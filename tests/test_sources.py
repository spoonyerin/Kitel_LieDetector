"""입력원(재생, 더미)과 시간 맞춤 도우미 테스트.

재생은 data/real을 읽기만 한다. 실측 로그가 없으면 해당 테스트를 건너뛴다.
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server.source.dummy_source import DummyConfig, DummySource, dummy_items  # noqa: E402
from server.source.lines import (Bad, BaselineSummary, Header, LineParser,  # noqa: E402
                                 Sample, TrialSummary)
from server.source.pacing import paced  # noqa: E402
from server.source.replay_source import ReplaySource, replay_items, resolve_log  # noqa: E402

REAL_RUN = os.path.join(ROOT, "data", "real", "experiments", "run_20261103_161545")


# ---------- 시간 맞춤 ----------
class FakeClock:
    """sleep한 만큼만 시간이 가는 가짜 시계."""

    def __init__(self):
        self.now = 0.0
        self.waits = []

    def sleep(self, s):
        self.waits.append(s)
        self.now += s

    def clock(self):
        return self.now


def test_paced_waits_by_time_difference():
    fc = FakeClock()
    items = [(0.0, "a"), (0.5, "b"), (0.5, "c"), (2.5, "d")]
    assert list(paced(items, speed=1.0, max_wait=10, sleep=fc.sleep, clock=fc.clock)) == ["a", "b", "c", "d"]
    assert fc.waits == [0.5, 2.0]


def test_paced_speed_cap_and_backwards():
    fc = FakeClock()
    items = [(0.0, "a"), (4.0, "b"), (100.0, "c"), (1.0, "d"), (None, "e"), (1.2, "f")]
    list(paced(items, speed=2.0, max_wait=3.0, sleep=fc.sleep, clock=fc.clock))
    # 4초/2배 = 2초, 96초/2배 = 48초 → 상한 3초, 시각이 거꾸로 가면 대기 없음, None은 건너뜀
    assert fc.waits == [2.0, 3.0, pytest.approx(0.1)]


def test_paced_speed_zero_never_sleeps():
    fc = FakeClock()
    list(paced([(0.0, "a"), (5.0, "b")], speed=0, sleep=fc.sleep, clock=fc.clock))
    assert fc.waits == []


def test_paced_small_gaps_do_not_accumulate_delay():
    """20ms 간격 1000줄을 100배속으로: 짧은 대기는 건너뛰고 몰아서 기다리므로 총 대기 ≈ 0.2초."""
    fc = FakeClock()
    items = [(i * 0.02, str(i)) for i in range(1001)]
    list(paced(items, speed=100, max_wait=2.0, sleep=fc.sleep, clock=fc.clock))
    assert sum(fc.waits) == pytest.approx(0.2, abs=0.01)
    assert all(w >= 0.005 for w in fc.waits) and len(fc.waits) < 1000


# ---------- 재생 ----------
def test_replay_missing_path_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        resolve_log(str(tmp_path / "없음"))


def test_replay_items_pc_time(tmp_path):
    f = tmp_path / "raw_serial_log.txt"
    f.write_text("2026-11-03 15:00:00.000\t#BOOT,KITEL_MAIN_V5\n"
                 "2026-11-03 15:00:01.250\t#EVENT,1,HAND_OK,0,FSR,0,0\n", encoding="utf-8")
    items = list(replay_items(str(tmp_path)))
    assert [line for _, line in items] == ["#BOOT,KITEL_MAIN_V5", "#EVENT,1,HAND_OK,0,FSR,0,0"]
    assert items[1][0] - items[0][0] == pytest.approx(1.25)


def test_replay_source_is_hardware():
    if not os.path.isdir(REAL_RUN):
        pytest.skip("실측 로그 없음")
    src = ReplaySource(REAL_RUN, speed=0)
    assert src.source == "hardware"
    first = next(iter(src.lines()))
    assert first.startswith("#EVENT,")


# ---------- 더미 ----------
def _parse_all(cfg):
    p = LineParser()
    return [p.feed(line) for _, line in dummy_items(cfg)]


def test_dummy_source_label():
    assert DummySource().source == "dummy"


def test_dummy_lines_parse_cleanly():
    items = _parse_all(DummyConfig(trials=4, patterns=["calm", "reactive", "poor", "calm"]))
    assert not [x for x in items if isinstance(x, Bad)]
    headers = [x for x in items if isinstance(x, Header)]
    assert {h.kind for h in headers} == {"sample", "summary", "baseline"}
    assert all(h.ok for h in headers)
    sums = [x for x in items if isinstance(x, TrialSummary)]
    assert len(sums) == 4 and all(s.header_source == "received" for s in sums)
    assert len([x for x in items if isinstance(x, BaselineSummary)]) == 2  # poor 뒤 재측정


def test_dummy_is_deterministic_with_seed():
    a = [line for _, line in dummy_items(DummyConfig(seed=7))]
    b = [line for _, line in dummy_items(DummyConfig(seed=7))]
    c = [line for _, line in dummy_items(DummyConfig(seed=8))]
    assert a == b and a != c


def test_dummy_device_time_and_seq_are_monotonic():
    samples = [x for x in _parse_all(DummyConfig()) if isinstance(x, Sample)]
    assert all(b.time_ms - a.time_ms >= 20 for a, b in zip(samples, samples[1:]))
    assert all(b.seq - a.seq == 1 for a, b in zip(samples, samples[1:]))


def test_dummy_summary_dz_matches_device_formula():
    """dz = (답변 평균 - 기준 평균) / 기준 표준편차 (실측 로그로 확인한 식).

    요약의 평균/표준편차는 소수 둘째 자리로 반올림되어 있어, 다시 계산한 값과는
    반올림 오차만큼 다를 수 있다 (상대 오차 0.1% 이내로 비교).
    """
    items = _parse_all(DummyConfig(trials=2, patterns=["calm", "reactive"]))
    for s in [x for x in items if isinstance(x, TrialSummary)]:
        v = s.values
        assert v["gsr_dz"] == pytest.approx((v["rs_gsr_mean"] - v["bl_gsr_mean"]) / v["bl_gsr_sd"],
                                            rel=1e-3, abs=5e-3)
        assert v["fsr_dz"] == pytest.approx((v["rs_fsr_mean"] - v["bl_fsr_mean"]) / v["bl_fsr_sd"],
                                            rel=1e-3, abs=5e-3)


def test_dummy_patterns_shape_summary():
    items = _parse_all(DummyConfig(trials=3, patterns=["calm", "reactive", "poor"]))
    calm, reactive, poor = [x for x in items if isinstance(x, TrialSummary)]
    assert calm.valid and abs(calm.values["gsr_dz"]) < 2 and abs(calm.values["fsr_dz"]) < 2
    assert reactive.valid and reactive.values["gsr_dz"] > 2 and reactive.values["fsr_dz"] < -2
    assert not poor.valid and poor.reason == "HAND_OFF_LONG"


def test_dummy_unknown_pattern_raises():
    with pytest.raises(ValueError):
        list(dummy_items(DummyConfig(patterns=["angry"])))
