"""심박 추정(server/pipeline/bpm.py) 테스트.

합성 신호로 정확도를 확인하고, 신호가 없거나 약하거나 불규칙하면 값을 내지 않는지 본다.
실측 로그(data/real, 읽기 전용)는 값의 범위만 확인한다. 정답 심박이 없으므로 정확도는 주장하지 않는다.
"""

import inspect
import json
import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server.pipeline import bpm as B  # noqa: E402
from server.pipeline.bpm import BpmEstimate, BpmTracker, bpm_message, estimate_bpm  # noqa: E402
from server.source.lines import Sample, iter_raw_log  # noqa: E402

REAL_LOG = os.path.join(ROOT, "data", "real", "experiments", "run_20261103_161545", "raw_serial_log.txt")


def synth(bpm=72.0, seconds=12.5, amp=500.0, noise=40.0, dc=100000.0, seed=0, beat_times=None, contact=1,
          resp=300.0):
    """맥파 비슷한 합성 IR. 잡음, 호흡성 흔들림, 가끔 36ms 간격(실측처럼)을 넣는다."""
    rng = np.random.default_rng(seed)
    steps = np.full(int(seconds * 50), 20.0)
    steps[rng.choice(len(steps), size=len(steps) // 50, replace=False)] = 36.0
    t = 1000.0 + np.cumsum(steps)
    ts = (t - t[0]) / 1000.0
    if beat_times is None:
        beat_times = np.arange(0.1, seconds + 1, 60.0 / bpm)
    x = np.full_like(ts, dc)
    for b in beat_times:   # 박동마다 빠르게 오르고 천천히 내려가는 모양 (-로 흡수)
        d = ts - b
        x -= amp * np.where(d >= 0, np.exp(-((d - 0.12) / 0.08) ** 2) + 0.3 * np.exp(-((d - 0.4) / 0.12) ** 2), 0)
    x += resp * np.sin(2 * np.pi * 0.25 * ts) + rng.normal(0, noise, len(ts))
    c = np.full(len(ts), contact)
    return t, x, c


# ---------- 합성 신호 ----------
@pytest.mark.parametrize("seed", range(5))
def test_72_bpm_with_noise(seed):
    est = estimate_bpm(*synth(72, seed=seed))
    assert est.reason == "OK" and est.bpm == pytest.approx(72, abs=2), est


@pytest.mark.parametrize("bpm", [50, 60, 95, 120, 150])
def test_other_rates(bpm):
    est = estimate_bpm(*synth(bpm, seed=1))
    assert est.reason == "OK" and est.bpm == pytest.approx(bpm, abs=2), est


def test_not_enough_data_and_gap():
    assert estimate_bpm(*synth(72, seconds=6)).reason == "NOT_ENOUGH_DATA"
    t, x, c = synth(72, seconds=14)
    t2 = t.copy()
    t2[400:] += 2000   # 8초쯤에 2초 끊김 → 끊김 뒤 6초만 남는다
    assert estimate_bpm(t2, x, c).reason == "NOT_ENOUGH_DATA"
    assert estimate_bpm([], [], []).bpm is None


def test_no_contact_is_none():
    t, x, c = synth(72)
    c[-30] = 0
    est = estimate_bpm(t, x, c)
    assert est.bpm is None and est.reason == "NO_CONTACT"


def test_no_finger_low_signal_is_none():
    est = estimate_bpm(*synth(72, dc=2000, amp=20))
    assert est.bpm is None and est.reason == "LOW_SIGNAL"


@pytest.mark.parametrize("seed", range(5))
@pytest.mark.parametrize("amp", [0.0, 5.0])
def test_no_pulse_never_gives_a_value(amp, seed):
    """맥박이 없거나 잡음보다 약하면 (호흡성 흔들림과 잡음만 있어도) 값을 내지 않는다."""
    est = estimate_bpm(*synth(72, amp=amp, seed=seed))
    assert est.bpm is None and est.reason in ("WEAK_PULSE", "IRREGULAR", "TOO_FEW_BEATS"), est


def test_weak_pulse_reason():
    est = estimate_bpm(*synth(72, amp=3.0, noise=5.0, resp=0.0))
    assert est.bpm is None and est.reason == "WEAK_PULSE"


def test_double_counting_and_half_rate_are_avoided():
    """작은 둘째 봉우리가 있는 맥파: 두 번 세지도(144) 건너뛰지도(36) 않는다."""
    for seed in range(10):
        est = estimate_bpm(*synth(72, seed=seed))
        assert est.bpm == pytest.approx(72, abs=2), (seed, est)


def test_irregular_is_none():
    rng = np.random.default_rng(3)
    beats = np.cumsum(rng.uniform(0.35, 1.4, 40))   # 43~171 BPM 사이로 마구 바뀜
    est = estimate_bpm(*synth(beat_times=beats))
    assert est.bpm is None and est.reason == "IRREGULAR" and est.ibi_cv > B.MAX_IBI_CV


def test_constants():
    assert (B.WINDOW_SEC, B.BPM_MIN, B.BPM_MAX) == (10.0, 40.0, 200.0)


# ---------- 추적기와 메시지 ----------
def _samples(t, x, c):
    return [Sample(time_ms=int(a), fsr=0, gsr=0, ir=int(b), red=0, phase="RESPONSE", trial=1, session=1,
                   baseline_id=1, seq=i, contact=int(k)) for i, (a, b, k) in enumerate(zip(t, x, c))]


def test_tracker_keeps_recent_window_and_resets_on_time_jump():
    tr = BpmTracker()
    for s in _samples(*synth(72, seconds=30)):
        tr.add(s)
    span = tr.buf[-1][0] - tr.buf[0][0]
    assert span <= (B.WINDOW_SEC + B.SETTLE_SEC) * 1000 + 200
    assert tr.estimate().bpm == pytest.approx(72, abs=2)
    tr.add(Sample(500, 0, 0, 100000, 0, "BASELINE", 0, 1, 1, 0, 1))   # 재부팅 등으로 시각이 거꾸로
    assert len(tr.buf) == 1 and tr.estimate().reason == "NOT_ENOUGH_DATA"
    tr.reset()
    assert tr.estimate().reason == "NOT_ENOUGH_DATA"


def test_bpm_message_format():
    m = bpm_message(BpmEstimate(72.345, "OK", n_beats=11, ibi_cv=0.04), "dummy", 12345)
    assert m == {"type": "bpm_update", "source": "dummy", "bpm": 72.3, "estimate": True, "reason": "OK",
                 "reason_text": "", "n_beats": 11, "ibi_cv": 0.04, "window_sec": 10.0, "esp_time_ms": 12345}
    m = bpm_message(BpmEstimate(None, "IRREGULAR", ibi_cv=float("nan")), "hardware")
    assert m["bpm"] is None and m["reason_text"] == "맥박 간격이 불규칙함" and m["ibi_cv"] is None
    json.dumps(m, allow_nan=False)


# ---------- 판정과 분리 ----------
def test_bpm_is_not_used_by_judgement():
    """BPM은 화면 표시용: 판정 관련 코드는 bpm 모듈을 쓰지 않고, 판정 특징에도 없다."""
    from server import result
    from server.judges import rule
    from server.pipeline import features, quality
    for mod in (rule, result, features, quality):
        assert "bpm" not in inspect.getsource(mod).lower(), mod.__name__
    from server.pipeline.tracker import Tracker, TrialDone
    from server.source.dummy_source import DummyConfig, dummy_items
    tr = Tracker("dummy")
    done = []
    for _, line in dummy_items(DummyConfig(trials=1, patterns=["calm"])):
        done += [u.segment for u in tr.feed_line(line) if isinstance(u, TrialDone)]
    res = result.judge_trial(done[0], "dummy")
    assert set(res["features"]) == {"fsr_local_dz", "gsr_local_dz", "fsr_dz", "gsr_dz"}


# ---------- 더미와 실측 ----------
def test_dummy_calm_trial_is_about_72():
    from server.cli import bpm_series
    from server.source.dummy_source import DummyConfig, dummy_items
    series = bpm_series(line for _, line in dummy_items(DummyConfig(trials=1, patterns=["calm"])))
    vals = [e.bpm for _s, trial, phase, e in series if e.bpm is not None and trial == 1]
    assert vals and all(abs(v - 72) <= 3 for v in vals)   # 더미 심박 72 ± 1.5


def test_real_log_values_stay_in_range():
    """실측 161545: 값이 나온 창은 모두 40~200 범위. 정답이 없으므로 정확도는 확인하지 않는다."""
    if not os.path.isfile(REAL_LOG):
        pytest.skip("실측 로그 없음")
    from server.cli import bpm_series
    series = bpm_series(line for _, line in iter_raw_log(REAL_LOG))
    vals = [e.bpm for *_, e in series if e.bpm is not None]
    assert vals, "값을 낸 창이 하나도 없다"
    assert all(40 <= v <= 200 for v in vals)
    assert all(e.reason in B.REASON_TEXT for *_, e in series)
