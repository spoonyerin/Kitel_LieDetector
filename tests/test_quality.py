"""최소 품질 검사(server/pipeline/quality.py) 테스트."""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server.pipeline import quality as Q  # noqa: E402
from server.pipeline.quality import assess, contact_events_in_window  # noqa: E402
from server.pipeline.tracker import TrialSegment  # noqa: E402
from server.source.lines import Event, LineParser  # noqa: E402

SUMMARY_OK = ("#SUMMARY,1,2,2,1,NONE,{warn},741,2205.74,62.98,2251.78,12.08,101961.6,"
              "216,4324,999,2007.84,161.97,2286.73,8.84,104661.9,-3.142,2.892")
SUMMARY_BAD = ("#SUMMARY,1,1,1,0,HAND_OFF_LONG,0,750,641.97,128.95,2233.39,22.64,105593.9,"
               "0,0,0,0.00,0.00,0.00,0.00,0.0,nan,nan")


def _ev(t, name, detail="START"):
    return Event(esp_time_ms=t, name=name, trial=2, detail=detail, session=1, baseline_id=2)


def _trial(summary_line=None, extra_events=()):
    tr = TrialSegment(session_index=1, esp_session=1, trial=2, baseline_id=2)
    if summary_line:
        tr.summary = LineParser().feed(summary_line)
    tr.events = [_ev(1000, "READY"), _ev(2000, "QUESTION"), _ev(5000, "QUESTION", "END"),
                 _ev(5000, "RESPONSE"), _ev(25000, "RESPONSE", "END"), _ev(25800, "RESULT"),
                 *extra_events]
    tr.events.sort(key=lambda e: e.esp_time_ms)
    return tr


def test_default_policy_is_hold_on_contact():
    assert Q.HOLD_ON_CONTACT_EVENTS is True


def test_ok_trial():
    q = assess(_trial(SUMMARY_OK.format(warn=0)), [])
    assert q.ok and q.hold_code is None and q.flags == [] and q.warnings == []


def test_no_summary_and_device_invalid():
    assert assess(_trial(None), ["NO_SUMMARY"]).hold_code == "NO_SUMMARY"
    assert assess(_trial(None), ["NO_SUMMARY"]).flags == ["NO_SUMMARY"]
    q = assess(_trial(SUMMARY_BAD), ["Q_TOO_SHORT:0"])
    assert q.hold_code == "DEVICE_INVALID:HAND_OFF_LONG" and "Q_TOO_SHORT:0" in q.flags


def test_feature_problem_holds():
    q = assess(_trial(SUMMARY_OK.format(warn=0)), ["Q_TOO_SHORT:12"])
    assert not q.ok and q.hold_code == "Q_TOO_SHORT:12"


def test_contact_event_inside_window_holds():
    tr = _trial(SUMMARY_OK.format(warn=0), [_ev(12000, "CONTACT_SHIFT", "RESPONSE:722")])
    assert contact_events_in_window(tr.events) == ["CONTACT_SHIFT"]
    assert assess(tr, []).hold_code == "CONTACT_EVENT:CONTACT_SHIFT"
    # 정책을 끄면 경고로만 남는다
    q = assess(tr, [], hold_on_contact=False)
    assert q.ok and q.warnings == ["CONTACT_EVENTS=CONTACT_SHIFT"]


def test_contact_event_outside_window_ignored():
    """RESULT 단계나 READY 단계의 접촉 변화는 질문·답변 비교에 영향이 없으므로 보류하지 않는다."""
    tr = _trial(SUMMARY_OK.format(warn=0), [_ev(1500, "CONTACT_DIP", "READY"), _ev(27000, "CONTACT_DIP", "RESULT"),
                                            _ev(28000, "BASELINE_LOST", "HAND_OFF_LONG")])
    assert contact_events_in_window(tr.events) == []
    assert assess(tr, []).ok


def test_warn_bits_are_warnings_not_holds():
    q = assess(_trial(SUMMARY_OK.format(warn=64 + 16)), [])
    assert q.ok
    assert "UNKNOWN_WARN_BITS=64" in q.warnings
    assert any(w.startswith("DEVICE_WARN=") and "CONTACT_DIP" in w for w in q.warnings)
