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


@pytest.mark.parametrize("code,expected", [
    ("CONTACT_EVENT:CONTACT_SHIFT", "손 위치가 바뀌어 이 답변은 측정에서 제외했어요."),
    ("CONTACT_EVENT:CONTACT_DIP", "손가락이 센서에서 살짝 떨어져 이 답변은 측정에서 제외했어요."),
    ("CONTACT_EVENT:HAND_LOST", "손이 센서에서 떨어져 이 답변은 측정에서 제외했어요."),
    ("CONTACT_EVENT:BASELINE_LOST", "기준 측정이 흐트러져 이 답변은 측정에서 제외했어요."),
    ("DEVICE_INVALID:HAND_OFF_LONG", "손이 센서에서 오래 떨어져 있어서 이 답변은 측정에서 제외했어요."),
    ("DEVICE_INVALID:HAND_LIFTED", "답변 중에 손을 떼서 이 답변은 측정에서 제외했어요."),
    ("DEVICE_INVALID:WEIRD", "기기가 이 답변을 무효로 처리해서 이 답변은 측정에서 제외했어요."),
    ("NO_SUMMARY", "기기에서 측정 결과를 받지 못해서 이 답변은 판정하지 않았어요."),
    ("Q_TOO_SHORT:12", "질문 시간이 1초보다 짧아 비교할 기준이 없어서 이 답변은 판정하지 않았어요."),
    ("FEATURE_NAN", "신호 값을 계산하지 못해서 이 답변은 판정하지 않았어요."),
    ("THRESHOLD_UNSET", "판정 기준이 아직 정해지지 않아서 이 답변은 판정하지 않았어요."),
    ("SOMETHING", "신호 상태가 좋지 않아서 이 답변은 판정하지 않았어요."),
    ("RAW_NO_SAMPLES", "신호가 들어오지 않아서 이 답변은 판정하지 않았어요."),
    ("RAW_TIME_ERROR", "신호의 시간 순서가 뒤섞여서 이 답변은 판정하지 않았어요."),
    ("RAW_SAMPLES_LOST", "신호 일부가 전송 중에 빠져서 이 답변은 판정하지 않았어요."),
    ("RAW_FEW_SAMPLES", "답변하는 동안의 신호가 충분히 모이지 않아서 이 답변은 판정하지 않았어요."),
    ("RAW_PHASE_ORDER", "측정 단계 순서가 맞지 않아서 이 답변은 판정하지 않았어요."),
    ("RAW_NO_CONTACT", "질문이나 답변 중에 손가락이 센서에서 떨어져서 이 답변은 측정에서 제외했어요."),
    ("RAW_FSR_SAT", "손가락을 너무 세게 눌러 압력 센서가 한계에 닿아서 이 답변은 측정에서 제외했어요."),
    ("RAW_GSR_SAT", "피부전도 센서 값이 측정 범위를 벗어나서 이 답변은 측정에서 제외했어요."),
])
def test_hold_texts_are_plain(code, expected):
    assert hold_text(code) == expected


def test_hold_texts_hide_codes():
    """쉬운 말: 화면 문장에 영문 코드나 샘플 수, σ가 들어가지 않는다 (코드는 hold_code에 따로 남는다)."""
    import re
    from server.reasons import RAW_HOLD_KR
    for code in ("CONTACT_EVENT:CONTACT_SHIFT", "DEVICE_INVALID:HAND_OFF_LONG", "DEVICE_INVALID:WEIRD",
                 "Q_TOO_SHORT:12", "NO_SUMMARY", "SOMETHING", *RAW_HOLD_KR):
        text = hold_text(code)
        assert not re.search(r"[A-Z]{3,}|σ|샘플", text), text


@pytest.mark.parametrize("code,expected", [
    ("TIME_GAPS=9(max 36ms)", "신호 간격이 가끔 조금 늦었어요."),
    ("SAMPLES_LOST=1", "신호가 조금 빠졌지만 판정에는 충분했어요."),
    ("RATE=55.6Hz", "신호 속도가 평소와 조금 달랐어요."),
    ("RESPONSE_DURATION=20100ms", "답변 구간 길이가 평소와 조금 달랐어요."),
    ("READY_NO_CONTACT=236", "질문 전 준비 단계에서 손가락이 잠시 떨어졌어요."),
    ("DEVICE_WARN=CONTACT_DIP|FSR_SAT", "기기 알림: 접촉이 잠시 약해졌어요, 압력 센서가 한계에 닿았어요."),
    ("WHATEVER=3", "신호에 작은 문제가 있었어요."),
])
def test_warning_texts_are_plain(code, expected):
    import re
    from server.reasons import warning_text
    text = warning_text(code)
    assert text == expected
    assert not re.search(r"[A-Z]{3,}|σ|샘플|\d", text), text


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
    # 뜻을 모르는 경고 비트(64)는 화면용 warnings에서 빠지고 server_only에만 남는다
    t5 = next(r for r in results if (r["session_id"], r["trial_id"]) == ("S01", 5))
    assert t5["server_only"]["warnings"] == ["UNKNOWN_WARN_BITS=64"]
    assert not any(w.startswith("UNKNOWN_WARN_BITS") for r in results for w in r["quality"]["warnings"])
    assert t5["hold_reason"] == "손 위치가 바뀌어 이 답변은 측정에서 제외했어요."
    # S10: 원시 샘플 검사 결과. 실측 시도는 모두 TIME_GAPS 경고가 있고 화면용 쉬운 문장이 붙는다
    for r in results:
        assert r["quality"]["raw_check"]["n_samples"] > 0
        assert "신호 간격이 가끔 조금 늦었어요." in r["quality"]["warning_texts"]
        assert not any("64" in t or "UNKNOWN" in t for t in r["quality"]["warning_texts"])
    t1 = next(r for r in results if (r["session_id"], r["trial_id"]) == ("S01", 1))
    assert "질문 전 준비 단계에서 손가락이 잠시 떨어졌어요." in t1["quality"]["warning_texts"]


def test_for_web_strips_server_only():
    from server.result import for_web, split_warnings
    assert split_warnings(["UNKNOWN_WARN_BITS=64", "DEVICE_WARN=CONTACT_DIP"]) == \
        (["DEVICE_WARN=CONTACT_DIP"], ["UNKNOWN_WARN_BITS=64"])
    assert for_web({"type": "trial_result", "server_only": {"warnings": ["x"]}}) == {"type": "trial_result"}
