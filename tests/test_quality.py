"""품질 검사(server/pipeline/quality.py) 테스트.

- 기기 요약, 특징 문제, 접촉 이벤트 정책 (S3)
- 원시 샘플 검사 (S10): collector check_segment 결과를 보류 코드와 경고로 나누는지
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server.pipeline import quality as Q  # noqa: E402
from server.pipeline.quality import assess, contact_events_in_window, raw_sample_check  # noqa: E402
from server.pipeline.tracker import TrialSegment  # noqa: E402
from server.source.lines import Event, LineParser, Sample  # noqa: E402

import record_session  # noqa: E402

SUMMARY_OK = ("#SUMMARY,1,2,2,1,NONE,{warn},741,2205.74,62.98,2251.78,12.08,101961.6,"
              "216,4324,999,2007.84,161.97,2286.73,8.84,104661.9,-3.142,2.892")
SUMMARY_BAD = ("#SUMMARY,1,1,1,0,HAND_OFF_LONG,0,750,641.97,128.95,2233.39,22.64,105593.9,"
               "0,0,0,0.00,0.00,0.00,0.00,0.0,nan,nan")
# 단계 구간 (기기 시각 ms): READY 1000~2000, QUESTION 2000~5000, RESPONSE 5000~25000 (20초)
PHASES = [("READY", 1000, 2000), ("QUESTION", 2000, 5000), ("RESPONSE", 5000, 25000)]


def _ev(t, name, detail="START"):
    return Event(esp_time_ms=t, name=name, trial=2, detail=detail, session=1, baseline_id=2)


def _samples(step=20, edit=None):
    """단계마다 20ms 간격 샘플. edit(phase, i, sample_dict)로 값을 바꿀 수 있다."""
    out, seq = [], 0
    for phase, start, end in PHASES:
        t = start + step
        while t <= end:
            seq += 1
            d = dict(time_ms=t, fsr=2000, gsr=2200, ir=100000, red=90000, phase=phase, trial=2, session=1,
                     baseline_id=2, seq=seq, contact=1)
            if edit:
                edit(phase, t, d)
            out.append(Sample(**d))
            t += step
    return out


def _trial(summary_line=SUMMARY_OK.format(warn=0), extra_events=(), samples=None, response_end=25000):
    tr = TrialSegment(session_index=1, esp_session=1, trial=2, baseline_id=2)
    if summary_line:
        tr.summary = LineParser().feed(summary_line)
    tr.events = [_ev(1000, "READY"), _ev(2000, "QUESTION"), _ev(5000, "QUESTION", "END"),
                 _ev(5000, "RESPONSE"), _ev(response_end, "RESPONSE", "END"), _ev(response_end + 800, "RESULT"),
                 *extra_events]
    tr.events.sort(key=lambda e: e.esp_time_ms)
    tr.samples = _samples() if samples is None else samples
    return tr


# ---------- 기존 정책 (S3) ----------
def test_default_policy_is_hold_on_contact():
    assert Q.HOLD_ON_CONTACT_EVENTS is True


def test_ok_trial():
    q = assess(_trial(), [])
    assert q.ok and q.hold_code is None and q.flags == [] and q.warnings == []
    assert q.raw["integrity"] == "OK" and q.raw["n_response"] == 1000


def test_no_summary_and_device_invalid():
    assert assess(_trial(None), ["NO_SUMMARY"]).hold_code == "NO_SUMMARY"
    assert assess(_trial(None), ["NO_SUMMARY"]).flags == ["NO_SUMMARY"]
    q = assess(_trial(SUMMARY_BAD), ["Q_TOO_SHORT:0"])
    assert q.hold_code == "DEVICE_INVALID:HAND_OFF_LONG" and "Q_TOO_SHORT:0" in q.flags


def test_feature_problem_holds():
    q = assess(_trial(), ["Q_TOO_SHORT:12"])
    assert not q.ok and q.hold_code == "Q_TOO_SHORT:12"


def test_contact_event_inside_window_holds():
    tr = _trial(extra_events=[_ev(12000, "CONTACT_SHIFT", "RESPONSE:722")])
    assert contact_events_in_window(tr.events) == ["CONTACT_SHIFT"]
    assert assess(tr, []).hold_code == "CONTACT_EVENT:CONTACT_SHIFT"
    q = assess(tr, [], hold_on_contact=False)   # 정책을 끄면 경고로만 남는다
    assert q.ok and q.warnings == ["CONTACT_EVENTS=CONTACT_SHIFT"]


def test_contact_event_outside_window_ignored():
    """RESULT 단계나 READY 단계의 접촉 변화는 질문·답변 비교에 영향이 없으므로 보류하지 않는다."""
    tr = _trial(extra_events=[_ev(1500, "CONTACT_DIP", "READY"), _ev(27000, "CONTACT_DIP", "RESULT"),
                              _ev(28000, "BASELINE_LOST", "HAND_OFF_LONG")])
    assert contact_events_in_window(tr.events) == []
    assert assess(tr, []).ok


def test_warn_bits_are_warnings_not_holds():
    q = assess(_trial(SUMMARY_OK.format(warn=64 + 16)), [])
    assert q.ok
    assert "UNKNOWN_WARN_BITS=64" in q.warnings
    assert any(w.startswith("DEVICE_WARN=") and "CONTACT_DIP" in w for w in q.warnings)


# ---------- 원시 샘플 검사 (S10) ----------
def test_raw_check_reuses_collector_check_segment():
    tr = _trial()
    _h, _w, raw = raw_sample_check(tr)
    rep = record_session.check_segment([s.as_collector_tuple() for s in tr.samples],
                                       [(None, e.esp_time_ms, e.name, e.detail) for e in tr.events],
                                       record_session.TRIAL_PHASES)
    assert raw["issues"] == rep["issues"] and raw["integrity"] == rep["integrity"]


def _assess_samples(edit=None, step=20, response_end=25000):
    return assess(_trial(samples=_samples(step=step, edit=edit), response_end=response_end), [])


def test_time_gaps_are_warnings_only():
    def slow(phase, t, d):   # 실측처럼 가끔 36ms 간격
        if t % 1000 == 0:
            d["time_ms"] = t + 16
    q = _assess_samples(slow)
    assert q.ok and q.flags == []
    assert any(w.startswith("TIME_GAPS=") for w in q.warnings)


def test_no_samples_holds():
    q = assess(_trial(samples=[]), [])
    assert q.hold_code == "RAW_NO_SAMPLES"


@pytest.mark.parametrize("kind", ["dup", "backwards"])
def test_time_order_errors_hold(kind):
    s = _samples()
    t = s[99].time_ms if kind == "dup" else s[99].time_ms - 5    # 같은 시각 / 거꾸로 간 시각
    s[100] = Sample(**{**s[100].__dict__, "time_ms": t})
    q = assess(_trial(samples=s), [])
    assert q.hold_code == "RAW_TIME_ERROR"


def test_lost_samples_small_is_warning_large_is_hold():
    s = _samples()
    few = [x for i, x in enumerate(s) if i != 500]                  # 1개 빠짐 (0.1%)
    q = assess(_trial(samples=few), [])
    assert q.ok and "SAMPLES_LOST=1" in q.warnings
    many = [x for i, x in enumerate(s) if not (400 <= i < 460)]     # 60개 빠짐 (5%) → 간격 끊김도 생김
    q = assess(_trial(samples=many), [])
    assert "RAW_SAMPLES_LOST" in q.flags and not q.ok


def test_few_response_samples_hold():
    s = [x for x in _samples() if not (x.phase == "RESPONSE" and x.time_ms > 15000)]   # 답변 절반만
    q = assess(_trial(samples=s), [])
    assert q.hold_code == "RAW_FEW_SAMPLES"


def test_phase_order_hold():
    s = _samples()
    i = next(k for k, x in enumerate(s) if x.phase == "RESPONSE") + 10
    s[i] = Sample(**{**s[i].__dict__, "phase": "READY"})     # 답변 중에 READY 샘플
    q = assess(_trial(samples=s), [])
    # (PHASE_MISMATCH는 collector 규칙상 그 단계의 END 이벤트가 있을 때만 세므로 여기서는 나오지 않는다)
    assert q.hold_code == "RAW_PHASE_ORDER"


def test_phase_mismatch_is_warning():
    """READY END가 1400ms인데 READY 샘플이 2000ms까지 있으면 collector는 어긋남으로 센다 → 경고만."""
    q = assess(_trial(extra_events=[_ev(1400, "READY", "END")]), [])
    assert q.ok and any(w.startswith("PHASE_MISMATCH=") for w in q.warnings)


@pytest.mark.parametrize("field,value,code", [("contact", 0, "RAW_NO_CONTACT"), ("fsr", 4095, "RAW_FSR_SAT"),
                                              ("gsr", 0, "RAW_GSR_SAT"), ("gsr", 4095, "RAW_GSR_SAT")])
def test_contact_and_saturation_in_question_or_response_hold(field, value, code):
    def bad(phase, t, d):
        if phase == "RESPONSE" and 10000 <= t < 10100:
            d[field] = value
    q = _assess_samples(bad)
    assert q.hold_code == code


@pytest.mark.parametrize("field,value,warn", [("contact", 0, "READY_NO_CONTACT"), ("fsr", 4095, "READY_FSR_SAT"),
                                              ("gsr", 4095, "READY_GSR_SAT")])
def test_contact_and_saturation_only_in_ready_are_warnings(field, value, warn):
    def bad(phase, t, d):
        if phase == "READY" and t < 1200:
            d[field] = value
    q = _assess_samples(bad)
    assert q.ok and any(w.startswith(warn + "=") for w in q.warnings)


def test_rate_and_duration_are_warnings():
    q = _assess_samples(step=18)    # 약 55.6Hz
    assert q.ok and any(w.startswith("RATE=") for w in q.warnings)
    q = _assess_samples(response_end=25100)   # 답변 구간 20.1초
    assert q.ok and "RESPONSE_DURATION=20100ms" in q.warnings


def test_device_invalid_stays_first_hold_code():
    """기기 무효 시도에 원시 문제가 같이 있어도 hold_code는 기기 사유 (기존 판정 결과 유지)."""
    def bad(phase, t, d):
        if phase == "RESPONSE":
            d["contact"] = 0
    q = assess(_trial(SUMMARY_BAD, samples=_samples(edit=bad)), [])
    assert q.hold_code == "DEVICE_INVALID:HAND_OFF_LONG" and "RAW_NO_CONTACT" in q.flags


def test_policy_constant():
    assert Q.MAX_LOST_RATIO == 0.02
