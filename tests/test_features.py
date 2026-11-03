"""국소 dz 특징(server/pipeline/features.py) 테스트."""

import math
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server.pipeline import features as F  # noqa: E402
from server.pipeline.features import local_features  # noqa: E402
from server.pipeline.tracker import Tracker, TrialDone  # noqa: E402
from server.source.lines import Sample, iter_raw_log  # noqa: E402

REAL_LOG = os.path.join(ROOT, "data", "real", "experiments", "run_20261103_161545", "raw_serial_log.txt")


def _samples(phase, n, fsr, gsr):
    return [Sample(time_ms=i * 20, fsr=fsr, gsr=gsr, ir=100000, red=90000, phase=phase,
                   trial=1, session=1, baseline_id=1, seq=i, contact=1) for i in range(n)]


def _summary(fsr_sd=40.0, gsr_sd=10.0):
    return {"bl_fsr_sd": fsr_sd, "bl_gsr_sd": gsr_sd, "fsr_dz": -9.9, "gsr_dz": 9.9}


def test_constants_as_decided():
    assert (F.FSR_SD_FLOOR, F.GSR_SD_FLOOR, F.MIN_QUESTION_SAMPLES) == (20.0, 5.0, 50)


def test_local_dz_value():
    s = _samples("READY", 10, 0, 0) + _samples("QUESTION", 100, 2000, 2200) + _samples("RESPONSE", 1000, 1880, 2250)
    res = local_features(s, _summary(fsr_sd=40.0, gsr_sd=10.0))
    assert res.problems == []
    assert res.features["fsr_local_dz"] == pytest.approx((1880 - 2000) / 40.0)
    assert res.features["gsr_local_dz"] == pytest.approx((2250 - 2200) / 10.0)
    # 기기 dz는 참고용으로 그대로 전달
    assert (res.features["fsr_dz"], res.features["gsr_dz"]) == (-9.9, 9.9)


def test_sd_floor_applied():
    s = _samples("QUESTION", 100, 2000, 2200) + _samples("RESPONSE", 100, 2100, 2210)
    res = local_features(s, _summary(fsr_sd=10.0, gsr_sd=1.0))
    assert res.features["fsr_local_dz"] == pytest.approx(100 / 20.0)
    assert res.features["gsr_local_dz"] == pytest.approx(10 / 5.0)
    assert res.detail["fsr"]["floor_applied"] and res.detail["gsr"]["floor_applied"]


def test_short_question_and_missing_parts():
    s = _samples("QUESTION", 49, 2000, 2200) + _samples("RESPONSE", 100, 2100, 2210)
    res = local_features(s, _summary())
    assert res.problems == ["Q_TOO_SHORT:49"] and math.isnan(res.features["fsr_local_dz"])
    assert local_features(s, None).problems == ["NO_SUMMARY"]
    no_rs = local_features(_samples("QUESTION", 60, 1, 1), _summary())
    assert no_rs.problems == ["NO_RESPONSE"]
    nan_sd = local_features(_samples("QUESTION", 60, 1, 1) + _samples("RESPONSE", 5, 1, 1),
                            {"bl_fsr_sd": float("nan"), "bl_gsr_sd": 3.0})
    assert "BL_SD_MISSING:fsr" in nan_sd.problems


def test_labels_do_not_change_features():
    """요약에 라벨 비슷한 키가 섞여 있어도 특징값은 같다 (특징 계산은 라벨을 읽지 않는다)."""
    s = _samples("QUESTION", 100, 2000, 2200) + _samples("RESPONSE", 100, 1900, 2300)
    a = local_features(s, _summary())
    b = local_features(s, {**_summary(), "condition": "lie", "ground_truth": 1})
    assert a.features == b.features


def test_real_161545_matches_analysis_table():
    """2026-11-04 분석 표의 값: S2 T01 국소 fsr -3.71 / gsr -2.77, S1 T02 -2.29 / 1.41."""
    if not os.path.isfile(REAL_LOG):
        pytest.skip("실측 로그 없음")
    tr = Tracker("hardware")
    done = {}
    for _, line in iter_raw_log(REAL_LOG):
        for u in tr.feed_line(line):
            if isinstance(u, TrialDone):
                done[(u.segment.session_index, u.segment.trial)] = u.segment
    for key, (fsr, gsr) in {(2, 1): (-3.71, -2.77), (1, 2): (-2.29, 1.41)}.items():
        t = done[key]
        res = local_features(t.samples, t.summary.values)
        assert res.features["fsr_local_dz"] == pytest.approx(fsr, abs=0.01)
        assert res.features["gsr_local_dz"] == pytest.approx(gsr, abs=0.01)
