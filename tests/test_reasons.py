"""이유 문장(server/reasons.py)과 trial_result(server/result.py) 테스트."""

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server.judges.rule import RuleJudge  # noqa: E402
from server.pipeline.tracker import Tracker, TrialDone  # noqa: E402
from server.reasons import hold_text, make_reasons  # noqa: E402
from server.result import DISCLAIMER, judge_trial  # noqa: E402
from server.source.dummy_source import DummyConfig, dummy_items  # noqa: E402
from server.source.lines import iter_raw_log  # noqa: E402

OK = {"ok": True, "hold_code": None}
REAL_LOG = os.path.join(ROOT, "data", "real", "experiments", "run_20261103_161545", "raw_serial_log.txt")


def _reasons(fsr, gsr, quality=OK, judge=None):
    j = judge or RuleJudge()
    v = j.judge({"fsr_local_dz": fsr, "gsr_local_dz": gsr}, {}, quality)
    return make_reasons(v, j)


# ---------- 이유 문장 ----------
def test_only_exceeding_sensor_is_mentioned():
    r = _reasons(-3.7, -2.8)
    assert r == ["답변 중 손가락 압력(FSR)이 질문 구간보다 3.7σ 낮았습니다 (임계값 3.0σ)."]
    assert not any("GSR" in s for s in r)


def test_two_sensors_ordered_by_size():
    r = _reasons(-6.0, 10.5)
    assert r[0].startswith("답변 중 피부전도(GSR)가 질문 구간보다 10.5σ 높았습니다") and "가장 큰 판정 요인" in r[0]
    assert r[1].startswith("답변 중 손가락 압력(FSR)이 질문 구간보다 6.0σ 낮았습니다")


def test_no_sensor_exceeded():
    assert _reasons(1.0, -2.9) == ["임계값(FSR 3.0σ, GSR 3.0σ)을 넘은 센서가 없습니다."]


@pytest.mark.parametrize("code,needle", [
    ("DEVICE_INVALID:HAND_OFF_LONG", "손이 센서에서 오래 떨어져"),
    ("DEVICE_INVALID:HAND_LIFTED", "답변 중 손이 센서에서 떨어져"),
    ("DEVICE_INVALID:WEIRD", "사유: WEIRD"),
    ("NO_SUMMARY", "기기 요약을 받지 못해"),
    ("Q_TOO_SHORT:12", "12샘플, 최소 50샘플"),
    ("CONTACT_EVENT:CONTACT_SHIFT", "손 접촉이 바뀌어(CONTACT_SHIFT)"),
    ("FEATURE_NAN", "특징값을 계산하지 못해"),
    ("THRESHOLD_UNSET", "임계값이 정해지지 않아"),
    ("SOMETHING", "SOMETHING"),
])
def test_hold_texts(code, needle):
    assert needle in hold_text(code)


def test_hold_reason_sentence_from_verdict():
    r = _reasons(9, 9, quality={"ok": False, "hold_code": "CONTACT_EVENT:HAND_LOST"})
    assert r == [hold_text("CONTACT_EVENT:HAND_LOST")]


# ---------- trial_result ----------
def _done(lines, source):
    tr = Tracker(source)
    out = []
    for line in lines:
        out += [u.segment for u in tr.feed_line(line) if isinstance(u, TrialDone)]
    return out


def test_trial_result_format_dummy():
    lines = [line for _, line in dummy_items(DummyConfig(trials=3, patterns=["calm", "reactive", "poor"]))]
    results = [judge_trial(t, "dummy") for t in _done(lines, "dummy")]
    assert [r["decision"] for r in results] == ["truth", "lie", "inconclusive"]
    for r in results:
        json.dumps(r, allow_nan=False)  # nan 없이 JSON으로 보낼 수 있어야 한다
        assert r["type"] == "trial_result" and r["source"] == "dummy"
        assert r["score"] is None and r["judge"] == {"name": "rule", "version": "0.0.1", "trained": False}
        assert "라벨 데이터로 맞추거나 검증하지 않았습니다" in r["judge_note"]
        assert r["disclaimer"] == DISCLAIMER and r["device_verdict"] is None
        assert set(r["features"]) == {"fsr_local_dz", "gsr_local_dz", "fsr_dz", "gsr_dz"}
    assert results[2]["hold_code"] == "DEVICE_INVALID:HAND_OFF_LONG" and results[2]["hold_reason"]
    assert results[1]["hold_reason"] is None
    assert all(c["over_threshold"] for c in results[1]["contributions"])


def test_trial_result_rejects_unknown_source():
    lines = [line for _, line in dummy_items(DummyConfig(trials=1, patterns=["calm"]))]
    with pytest.raises(ValueError):
        judge_trial(_done(lines, "dummy")[0], "replay")


def test_real_161545_decisions():
    """기본 상수(3.0σ, 1개, 접촉 보류)로 실측 161545: 진실 5, 거짓 2, 보류 4."""
    if not os.path.isfile(REAL_LOG):
        pytest.skip("실측 로그 없음")
    lines = [line for _, line in iter_raw_log(REAL_LOG)]
    results = [judge_trial(t, "hardware") for t in _done(lines, "hardware")]
    got = {(r["session_id"], r["trial_id"]): (r["decision"], r["hold_code"]) for r in results}
    assert got == {
        ("S01", 1): ("inconclusive", "DEVICE_INVALID:HAND_OFF_LONG"),
        ("S01", 2): ("truth", None), ("S01", 3): ("truth", None), ("S01", 4): ("truth", None),
        ("S01", 5): ("inconclusive", "CONTACT_EVENT:CONTACT_SHIFT"),
        ("S01", 6): ("inconclusive", "CONTACT_EVENT:CONTACT_SHIFT"),
        ("S02", 1): ("lie", None), ("S02", 2): ("lie", None),
        ("S02", 3): ("truth", None), ("S02", 4): ("truth", None),
        ("S02", 5): ("inconclusive", "CONTACT_EVENT:CONTACT_SHIFT"),
    }
    for r in results:
        json.dumps(r, allow_nan=False)
        assert r["source"] == "hardware" and r["score"] is None
