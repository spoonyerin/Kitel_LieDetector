"""전체 특징(server/pipeline/features.py의 extended_features) 테스트.

합성 신호로 값이 정의대로 나오는지, nan 처리, 결정성, 라벨 비참조, 판정 입력 불변을 확인한다.
실측 4개 로그(data/real, 읽기 전용)에서는 nan 비율만 확인한다 (정답 비교 아님).
"""

import math
import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server.pipeline import features as F  # noqa: E402
from server.pipeline.features import FEATURE_NAMES, extended_features  # noqa: E402
from server.source.lines import Sample  # noqa: E402

REAL_DIR = os.path.join(ROOT, "data", "real", "experiments")
REAL_RUNS = ["run_20261103_152007", "run_20261103_152345", "run_20261103_154858", "run_20261103_161545"]


class Gen:
    """20ms 간격으로 이어지는 합성 샘플. 맥박 위상은 누적한다."""

    def __init__(self):
        self.t, self.seq, self.beat = 1000, 0, 0.0

    def segment(self, phase, seconds, trial, fsr, gsr, bpm=70.0, ppg_amp=500.0, contact=1):
        out = []
        for _ in range(int(seconds * 50)):
            self.t += 20
            self.seq += 1
            ts = self.t / 1000.0
            self.beat = (self.beat + bpm / 60.0 * 0.02) % 1.0
            pulse = math.exp(-((self.beat - 0.2) / 0.08) ** 2)
            out.append(Sample(time_ms=self.t, fsr=int(round(fsr(ts))), gsr=int(round(gsr(ts))),
                              ir=int(round(100000 + ppg_amp * pulse)), red=90000, phase=phase, trial=trial,
                              session=1, baseline_id=1, seq=self.seq, contact=contact))
        return out


SINE_F = lambda ts: 50 * math.sin(2 * math.pi * 0.2 * ts)     # 0.2Hz, 표준편차 약 35.4
SINE_G = lambda ts: 20 * math.sin(2 * math.pi * 0.2 * ts)     # 표준편차 약 14.1


def make(resp_fsr=2300, resp_gsr=2260, resp_bpm=85.0, resp_amp=1000.0, flat_gsr=False, resp_sec=20):
    g = Gen()
    gsr_ref = (lambda ts: 2200) if flat_gsr else (lambda ts: 2200 + SINE_G(ts))
    base = g.segment("BASELINE", 15, 0, lambda ts: 2000 + SINE_F(ts), gsr_ref)
    trial = g.segment("READY", 2, 1, lambda ts: 2000 + SINE_F(ts), gsr_ref)
    trial += g.segment("QUESTION", 5, 1, lambda ts: 2000 + SINE_F(ts), gsr_ref)
    trial += g.segment("RESPONSE", resp_sec, 1, lambda ts: resp_fsr + SINE_F(ts),
                       (lambda ts: resp_gsr) if flat_gsr else (lambda ts: resp_gsr + SINE_G(ts)),
                       bpm=resp_bpm, ppg_amp=resp_amp)
    return trial, base


def _eq(a, b):
    return (math.isnan(a) and math.isnan(b)) or a == b


# ---------- 값 ----------
def test_feature_names_and_constants():
    assert len(FEATURE_NAMES) == 23 and len(set(FEATURE_NAMES)) == 23
    assert (F.BASELINE_SKIP_SEC, F.Z_CLIP, F.RATIO_CLIP, F.DURATION_Z, F.FLAT_SD) == (2.0, 10.0, 10.0, 2.0, 0.5)


def test_sensor_features_match_definitions():
    trial, base = make()
    v, info = extended_features(trial, base)
    sd_f, sd_g = 50 / math.sqrt(2), 20 / math.sqrt(2)
    # (a) 답변 평균 변화 z
    assert v["fsr_mean_delta_bl_z"] == pytest.approx(300 / sd_f, rel=0.06)
    assert v["fsr_mean_delta_q_z"] == pytest.approx(300 / sd_f, rel=0.06)
    assert v["gsr_mean_delta_bl_z"] == pytest.approx(60 / sd_g, rel=0.06)
    # (b) 최대 변화 z = (300 + 50) / 35.4
    assert v["fsr_max_change_bl_z"] == pytest.approx(350 / sd_f, rel=0.06)
    # (c) 변동 정도 비율: 흔들림은 같지만 답변 시작의 계단(2000→2300)이 필터를 거쳐 구간 안에 남아 약 15% 커진다
    #     (정의상 답변 구간은 반응의 시작을 포함한다). 변화가 없으면 1에 가깝다 (test_no_change_gives_near_zero)
    for k in ("fsr_sd_ratio_bl", "fsr_sd_ratio_q", "gsr_sd_ratio_bl", "gsr_sd_ratio_q"):
        assert 1.0 <= v[k] <= 1.25, (k, v[k])
    # (d) 반응 지속시간: 답변 20초 내내 |z| > 2
    assert v["fsr_duration_bl_s"] == pytest.approx(20.0, abs=0.3)
    assert v["gsr_duration_bl_s"] == pytest.approx(20.0, abs=0.6)
    assert info["baseline_found"] and info["flat"] == [] and info["n_response"] == 1000
    assert info["n_baseline"] == 650   # 앞 2초(100샘플) 버림


def test_ppg_features():
    trial, base = make(resp_bpm=85.0, resp_amp=1000.0)
    v, info = extended_features(trial, base)
    assert v["hr_bl"] == pytest.approx(70, abs=2) and v["hr_resp"] == pytest.approx(85, abs=2)
    assert v["hr_delta_bl"] == pytest.approx(15, abs=3)
    assert math.isnan(v["hr_q"]) and math.isnan(v["hr_delta_q"])          # 준비+질문 7초 < 12초
    assert info["hr_reasons"]["q"] == "NOT_ENOUGH_DATA"
    assert v["ppg_amp_ratio_bl"] == pytest.approx(2.0, rel=0.2)
    assert v["ppg_amp_ratio_q"] == pytest.approx(2.0, rel=0.2)


def test_no_change_gives_near_zero():
    trial, base = make(resp_fsr=2000, resp_gsr=2200, resp_bpm=70.0, resp_amp=500.0)
    v, _ = extended_features(trial, base)
    assert abs(v["fsr_mean_delta_bl_z"]) < 0.3 and abs(v["gsr_mean_delta_q_z"]) < 0.3
    assert v["fsr_duration_bl_s"] < 1.0
    for k in ("fsr_sd_ratio_bl", "fsr_sd_ratio_q", "gsr_sd_ratio_bl", "gsr_sd_ratio_q"):
        assert v[k] == pytest.approx(1.0, abs=0.05), k
    assert v["hr_delta_bl"] == pytest.approx(0, abs=2.5) and v["ppg_amp_ratio_bl"] == pytest.approx(1.0, rel=0.15)


def test_clipping():
    trial, base = make(resp_fsr=4000)      # 약 56σ
    v, _ = extended_features(trial, base)
    assert v["fsr_mean_delta_bl_z"] == F.Z_CLIP and v["fsr_max_change_bl_z"] == F.Z_CLIP


# ---------- nan 처리 ----------
def test_flat_reference_is_rejected():
    trial, base = make(flat_gsr=True)
    v, info = extended_features(trial, base)
    assert all(math.isnan(v[k]) for k in FEATURE_NAMES if k.startswith("gsr_"))
    assert not math.isnan(v["fsr_mean_delta_bl_z"])
    assert set(info["flat"]) == {"gsr_bl", "gsr_q"}


def test_missing_baseline_keeps_question_features():
    trial, _ = make()
    v, info = extended_features(trial, None)
    assert not info["baseline_found"]
    assert all(math.isnan(v[k]) for k in FEATURE_NAMES if "_bl" in k)
    assert not math.isnan(v["fsr_mean_delta_q_z"]) and not math.isnan(v["ppg_amp_ratio_q"])


@pytest.mark.parametrize("trial", [[], None, make(resp_sec=3)[0]])
def test_short_or_empty_is_all_nan(trial):
    v, _ = extended_features(trial, make()[1])
    assert all(math.isnan(v[k]) for k in FEATURE_NAMES)


# ---------- 결정성, 라벨, 판정 입력 ----------
def test_deterministic():
    trial, base = make()
    a, _ = extended_features(trial, base)
    b, _ = extended_features(list(trial), list(base))
    assert all(_eq(a[k], b[k]) for k in FEATURE_NAMES)


def test_labels_do_not_change_features_or_judgement():
    """요약에 라벨 비슷한 값을 넣어도 trial_result의 특징과 판정이 같다 (특징 계산은 샘플만 본다)."""
    import copy
    from server.pipeline.tracker import Tracker, TrialDone, baseline_for
    from server.result import judge_trial
    from server.source.dummy_source import DummyConfig, dummy_items
    tr = Tracker("dummy")
    done = []
    for _, line in dummy_items(DummyConfig(trials=2, patterns=["calm", "reactive"])):
        done += [u.segment for u in tr.feed_line(line) if isinstance(u, TrialDone)]
    for t in done:
        base = baseline_for(tr, t)
        a = judge_trial(t, "dummy", baseline_segment=base)
        t2 = copy.deepcopy(t)
        t2.summary.raw.update({"condition": "lie", "ground_truth": "1"})
        b = judge_trial(t2, "dummy", baseline_segment=base)
        assert a["features"] == b["features"] and a["decision"] == b["decision"]


def test_rule_judge_receives_only_previous_features(monkeypatch):
    """전체 특징을 더해도 RuleJudge가 받는 특징은 기존 4개(국소 dz, 기기 dz)뿐이다."""
    from server.judges.rule import RuleJudge
    from server.pipeline.tracker import Tracker, TrialDone, baseline_for
    from server.result import judge_trial
    from server.source.dummy_source import DummyConfig, dummy_items
    seen = []
    orig = RuleJudge.judge
    monkeypatch.setattr(RuleJudge, "judge", lambda self, f, b, q: (seen.append(set(f)), orig(self, f, b, q))[1])
    tr = Tracker("dummy")
    for _, line in dummy_items(DummyConfig(trials=2, patterns=["calm", "reactive"])):
        for u in tr.feed_line(line):
            if isinstance(u, TrialDone):
                res = judge_trial(u.segment, "dummy", baseline_segment=baseline_for(tr, u.segment))
                assert set(FEATURE_NAMES) <= set(res["features"])
    assert seen and all(s == {"fsr_local_dz", "gsr_local_dz", "fsr_dz", "gsr_dz"} for s in seen)


# ---------- 실측 (읽기 전용, nan 비율 보고) ----------
def test_real_logs_nan_ratio_report():
    from server.cli import feature_rows
    rows = []
    for run in REAL_RUNS:
        log = os.path.join(REAL_DIR, run, "raw_serial_log.txt")
        if not os.path.isfile(log):
            pytest.skip(f"실측 로그 없음: {run}")
        rows += feature_rows(log, run)
    assert len(rows) == 38
    judged = [r for r in rows if r["quality_ok"]]
    ratios = {k: sum(1 for r in judged if r[k] == "") / len(judged) for k in FEATURE_NAMES}
    print("\n판정된 시도의 특징별 nan 비율 (정답 비교 아님):",
          {k: round(v, 2) for k, v in ratios.items() if v > 0})
    # 센서 특징과 맥파 진폭은 판정된 시도 모두에서 계산된다 (기준 측정 구간을 모두 찾음)
    for k in FEATURE_NAMES:
        if k.startswith(("fsr_", "gsr_", "ppg_")):
            assert ratios[k] == 0, k
    assert all(r["baseline_found"] for r in rows)


def test_features_cli_refuses_protected_output(tmp_path, capsys):
    from server.cli import main
    assert main(["features", os.path.join(REAL_DIR, "run_20261103_161545"),
                 "--out", os.path.join(ROOT, "data", "real", "x.csv")]) == 2
    assert not os.path.exists(os.path.join(ROOT, "data", "real", "x.csv"))
    assert main(["features", os.path.join(REAL_DIR, "run_20261103_161545"),
                 "--out", os.path.join(ROOT, "collector", "x.csv")]) == 2
    if os.path.isdir(os.path.join(REAL_DIR, "run_20261103_161545")):
        out = tmp_path / "f.csv"
        assert main(["features", os.path.join(REAL_DIR, "run_20261103_161545"), "--out", str(out)]) == 0
        head = out.read_text(encoding="utf-8-sig").splitlines()[0].split(",")
        assert "fsr_mean_delta_bl_z" in head and "condition" not in head and "ground_truth" not in head
