"""진행 추적기(server/pipeline/tracker.py) 테스트.

- 더미 한 사이클의 단계 순서와 시도 상태
- 실제 로그(data/real, 읽기 전용)에서 시도 상태가 collector의 Run과 같은지
  (Run은 pytest 임시 폴더 tmp_path에만 파일을 쓴다)
- 재부팅, 다음 시도 시작 같은 경계 경우
"""

import contextlib
import io
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server.pipeline.tracker import (BaselineDone, PhaseChanged, SessionEnded,  # noqa: E402
                                     SessionStarted, Tracker, TrialDone)
from server.source.dummy_source import DummyConfig, dummy_items  # noqa: E402
from server.source.lines import iter_raw_log  # noqa: E402

import record_session  # noqa: E402  (server.source.lines가 collector 경로를 추가한다)

REAL_DIR = os.path.join(ROOT, "data", "real", "experiments")
REAL_RUNS = ["run_20261103_152007", "run_20261103_152345",
             "run_20261103_154858", "run_20261103_161545"]


def _run_tracker(lines, source="hardware"):
    tr = Tracker(source)
    updates = []
    for line in lines:
        updates += tr.feed_line(line)
    updates += tr.close()
    return tr, updates


def _real_lines(run):
    path = os.path.join(REAL_DIR, run, "raw_serial_log.txt")
    if not os.path.isfile(path):
        pytest.skip(f"실측 로그 없음: {run}")
    return [line for _, line in iter_raw_log(path)]


def _dummy_lines(**kw):
    return [line for _, line in dummy_items(DummyConfig(**kw))]


def _tracker_statuses(tr):
    trials = {(s.index, tid): (t.status, t.reason) for s in tr.sessions for tid, t in s.trials.items()}
    bases = {(s.index, bid): b.status for s in tr.sessions for bid, b in s.baselines.items()}
    return trials, bases


def _collector_statuses(lines, out_dir):
    """collector의 Run에 같은 줄을 넣는다. Run은 out_dir(pytest 임시 폴더)에만 쓴다."""
    with contextlib.redirect_stdout(io.StringIO()):
        r = record_session.Run(str(out_dir), "P01", [], bank=None, clear=False)
        for line in lines:
            r.handle_line(line)
        r.close()
    trials = {(i + 1, tid): (t.status, t.reason) for i, s in enumerate(r.sessions) for tid, t in s.trials.items()}
    bases = {(i + 1, bid): b.status for i, s in enumerate(r.sessions) for bid, b in s.baselines.items()}
    return trials, bases


# ---------- 기본 ----------
def test_tracker_requires_known_source():
    with pytest.raises(ValueError):
        Tracker("replay")


def test_dummy_one_cycle_phase_order():
    _tr, updates = _run_tracker(_dummy_lines(trials=1, patterns=["calm"]), source="dummy")
    phases = [u.phase for u in updates if isinstance(u, PhaseChanged)]
    assert phases == ["PLACE_HAND", "BASELINE", "READY", "QUESTION", "RESPONSE",
                      "ANALYZING", "RESULT", "SESSION_END", "PLACE_HAND"]
    kinds = [type(u).__name__ for u in updates if not isinstance(u, PhaseChanged)]
    assert kinds.index("SessionStarted") < kinds.index("BaselineDone") < kinds.index("TrialDone") \
        < kinds.index("SessionEnded")


def test_dummy_trial_statuses_and_integrity():
    tr, updates = _run_tracker(_dummy_lines(trials=4, patterns=["calm", "reactive", "poor", "calm"]),
                               source="dummy")
    done = [u.segment for u in updates if isinstance(u, TrialDone)]
    assert [(t.trial, t.status, t.reason) for t in done] == [
        (1, "COMPLETE", ""), (2, "COMPLETE", ""), (3, "ABORTED", "HAND_OFF_LONG"), (4, "COMPLETE", "")]
    # 정상 시도는 collector 무결성 검사(check_segment)를 통과한다
    for t in done:
        if t.status == "COMPLETE":
            rep = t.report()
            assert rep["integrity"] == "OK", rep["issues"]
            assert rep["n_response"] == 1000
    # poor 뒤 기준 재측정: 기준 2개, 마지막 시도는 2번 기준을 쓴다
    bases = [u.segment for u in updates if isinstance(u, BaselineDone)]
    assert [b.baseline_id for b in bases] == [1, 2] and all(b.status == "COMPLETE" for b in bases)
    assert done[-1].baseline_id == 2
    assert tr.ignored_samples == 0 and tr.bad_lines == 0


def test_dummy_matches_collector(tmp_path):
    """더미 줄을 collector의 Run에 넣어도 같은 상태가 나온다 (더미 형식이 기기 형식과 맞다)."""
    lines = _dummy_lines(trials=4, patterns=["calm", "reactive", "poor", "calm"])
    tr, _ = _run_tracker(lines, source="dummy")
    assert _tracker_statuses(tr) == _collector_statuses(lines, tmp_path)


# ---------- 실제 로그 ----------
@pytest.mark.parametrize("run", REAL_RUNS)
def test_real_log_matches_collector(run, tmp_path):
    lines = _real_lines(run)
    tr, _ = _run_tracker(lines)
    assert _tracker_statuses(tr) == _collector_statuses(lines, tmp_path)


def test_real_log_161545_expected():
    """M1 재생 시험에서 확인한 값: 시도 11개 중 COMPLETE 10개, ABORTED 1개(HAND_OFF_LONG)."""
    _tr, updates = _run_tracker(_real_lines("run_20261103_161545"))
    done = [u.segment for u in updates if isinstance(u, TrialDone)]
    assert len(done) == 11
    assert sum(1 for t in done if t.status == "COMPLETE") == 10
    assert [(t.status, t.reason) for t in done if t.status != "COMPLETE"] == [("ABORTED", "HAND_OFF_LONG")]
    assert all(t.summary is not None for t in done)
    assert sum(1 for u in updates if isinstance(u, SessionStarted)) == 2
    assert [u.reason for u in updates if isinstance(u, SessionEnded)] == ["LONG_PRESS", "LONG_PRESS"]


# ---------- 경계 경우 ----------
SESSION_HEAD = [
    "#EVENT,1000,SESSION,0,START,1,0",
    "#EVENT,1000,BASELINE,0,START,1,1",
    "1020,2000,2200,100000,90000,BASELINE,0,1,1,1,1",
    "#EVENT,16000,BASELINE,0,END,1,1",
    "#EVENT,16000,READY,1,START,1,1",
    "16020,2000,2200,100000,90000,READY,1,1,1,2,1",
]


def test_boot_interrupts_running_trial():
    _tr, updates = _run_tracker(SESSION_HEAD + ["#BOOT,KITEL_MAIN_V5"])
    done = [u.segment for u in updates if isinstance(u, TrialDone)]
    assert [(t.status, t.reason) for t in done] == [("INTERRUPTED", "ESP32_RESET")]
    assert [u.reason for u in updates if isinstance(u, SessionEnded)] == ["ESP32_RESET"]


def test_next_ready_closes_running_trial_as_incomplete():
    lines = SESSION_HEAD + ["#EVENT,18000,READY,2,START,1,1"]
    _tr, updates = _run_tracker(lines)
    done = [u.segment for u in updates if isinstance(u, TrialDone)]
    assert (done[0].trial, done[0].status, done[0].reason) == (1, "INCOMPLETE", "NEXT_TRIAL_STARTED")


def test_samples_outside_session_are_ignored():
    tr, _ = _run_tracker(["1020,2000,2200,100000,90000,BASELINE,0,1,1,1,1"])
    assert tr.ignored_samples == 1 and tr.sessions == []
