"""질문 순서(server/questions.py) 테스트.

- 질문 은행에서 no/question_id/question_text만 읽고 라벨 열은 읽지 않는다.
- 연습 1 + 본 질문 5 + 추가 질문, 시드로 재현.
- 시도 배정 규칙: 질문 구간 전에 무효 → 같은 질문 다시, 이후 → 다음 질문. 세션마다 새 순서.
- 판정 코드는 질문 모듈을 쓰지 않는다.
"""

import inspect
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server.judges.base import LABEL_KEYS, check_no_labels  # noqa: E402
from server.pipeline.tracker import (PhaseChanged, SessionEnded, SessionStarted, Tracker,  # noqa: E402
                                     TrialDone, TrialSegment)
from server.questions import (DEFAULT_BANK, EXHAUSTED_TEXT, N_MAIN, ORDER_NOTES, QUESTION_KEYS,  # noqa: E402
                              QuestionPlanner, build_order, load_questions)
from server.source.dummy_source import DummyConfig, dummy_items  # noqa: E402
from server.source.lines import iter_raw_log  # noqa: E402

REAL_LOG = os.path.join(ROOT, "data", "real", "experiments", "run_20261103_161545", "raw_serial_log.txt")


# ---------- 은행 읽기 ----------
def test_load_bank_reads_only_question_columns():
    qs = load_questions(DEFAULT_BANK)
    assert len(qs) == 15 and all(set(q) == set(QUESTION_KEYS) for q in qs)
    assert qs[0] == {"no": 1, "question_id": "Q01", "question_text": "형제나 자매가 있습니까?"}


def test_label_columns_in_bank_are_ignored(tmp_path):
    """은행 파일에 라벨 비슷한 열이 있어도 읽지 않는다 (tmp_path에만 쓴다)."""
    f = tmp_path / "bank.csv"
    rows = ["no,item_id,question_text,condition,marked_lie,ground_truth"]
    rows += [f"{i},Q{i:02d},질문 {i}입니까?,lie,1,1" for i in range(1, 8)]
    f.write_text("\n".join(rows) + "\n", encoding="utf-8")
    qs = load_questions(str(f))
    assert all(set(q) == set(QUESTION_KEYS) for q in qs)
    planner = QuestionPlanner(qs, seed=1)
    planner.on_update(SessionStarted(1, 1))
    msg = planner.on_update(PhaseChanged("READY", 1, 1, 1, 0))[0]
    check_no_labels(msg)
    assert not set(msg) & set(LABEL_KEYS) and "lie" not in str(msg.values())


# ---------- 순서 ----------
def test_order_practice_main_extra():
    import random
    order = build_order(load_questions(), random.Random("x"))
    assert [q.role for q in order] == ["practice"] + ["main"] * N_MAIN + ["extra"] * 9
    assert len({q.no for q in order}) == 15
    assert [q.label for q in order[:3]] == ["연습 질문", "본 질문 1/5", "본 질문 2/5"]
    assert order[6].label == "추가 질문 1"


def test_seed_reproducible_and_sessions_differ():
    a, b = QuestionPlanner(load_questions(), seed=7), QuestionPlanner(load_questions(), seed=7)
    ids = lambda p, s: [q.question_id for q in p.order_for_session(s)]
    assert ids(a, 1) == ids(b, 1) and ids(a, 2) == ids(b, 2)
    assert ids(a, 1) != ids(a, 2)
    assert ids(a, 1) != ids(QuestionPlanner(load_questions(), seed=8), 1)
    assert isinstance(QuestionPlanner(load_questions()).seed, int)   # 시드가 없으면 무작위로 정하고 기록


# ---------- 배정 규칙 (직접 만든 알림) ----------
def _seg(trial, status, session_index=1):
    return TrialSegment(session_index=session_index, esp_session=1, trial=trial, baseline_id=1, status=status)


def _ready(p, trial):
    return p.on_update(PhaseChanged("READY", trial, 1, 1, trial * 1000))


def test_assignment_rules():
    p = QuestionPlanner(load_questions(), seed=3, input_kind="serial", source="hardware")
    order = p.order_for_session(1)
    assert _ready(p, 1) == []                       # 세션 시작 전: 배정하지 않음 (서버를 도중에 켠 경우)
    p.on_update(SessionStarted(1, 1))
    m1 = _ready(p, 1)[0]
    assert m1["question_id"] == order[0].question_id and m1["question_label"] == "연습 질문"
    assert m1["order_note"] == ORDER_NOTES["serial"] and m1["source"] == "hardware"
    # 1) 질문 구간 전에 무효 → 같은 질문을 다시
    p.on_update(TrialDone(_seg(1, "ABORTED")))
    m2 = _ready(p, 2)[0]
    assert m2["question_id"] == order[0].question_id and m2["trial"] == 2
    # 2) 질문 구간 이후에 무효 → 다음 질문
    p.on_update(PhaseChanged("QUESTION", 2, 1, 1, 0))
    p.on_update(TrialDone(_seg(2, "ABORTED")))
    assert _ready(p, 3)[0]["question_id"] == order[1].question_id
    # 3) 완료 → 다음 질문, 같은 시도 READY가 또 와도 다시 배정하지 않음
    p.on_update(PhaseChanged("QUESTION", 3, 1, 1, 0))
    p.on_update(TrialDone(_seg(3, "COMPLETE")))
    assert _ready(p, 3) == []
    assert _ready(p, 4)[0]["question_id"] == order[2].question_id
    # 4) INCOMPLETE도 질문 구간 전이면 같은 질문 다시
    p.on_update(TrialDone(_seg(4, "INCOMPLETE")))
    assert _ready(p, 5)[0]["question_id"] == order[2].question_id
    assert p.question_for(1, 2).question_id == order[0].question_id
    assert p.question_for(1, 99) is None and p.question_for(9, 1) is None
    # 세션 종료 → 현재 질문 비움, 이후 READY는 무시
    cleared = p.on_update(SessionEnded(1, 1, "LONG_PRESS"))[0]
    assert cleared["question_text"] is None and cleared["trial"] is None
    assert _ready(p, 6) == []
    # 새 세션 → 새 순서
    p.on_update(SessionStarted(2, 2))
    assert _ready(p, 1)[0]["question_id"] == p.order_for_session(2)[0].question_id


def test_exhausted_after_all_questions():
    p = QuestionPlanner(load_questions(), seed=1)
    p.on_update(SessionStarted(1, 1))
    for t in range(1, 16):
        p.on_update(PhaseChanged("QUESTION", t - 1, 1, 1, 0))
        m = _ready(p, t)[0]
        assert m["exhausted"] is False
        p.on_update(PhaseChanged("QUESTION", t, 1, 1, 0))
        p.on_update(TrialDone(_seg(t, "COMPLETE")))
    m = _ready(p, 16)[0]
    assert m["exhausted"] is True and m["question_id"] is None and m["question_text"] == EXHAUSTED_TEXT
    assert p.question_for(1, 16) is None


# ---------- 추적기와 함께 (더미, 실측) ----------
def _run(lines, source, input_kind, seed=5):
    tr = Tracker(source)
    p = QuestionPlanner(load_questions(), seed=seed, input_kind=input_kind, source=source)
    msgs, done = [], []
    for line in lines:
        for u in tr.feed_line(line):
            if isinstance(u, TrialDone):
                done.append(u.segment)
            msgs += p.on_update(u)
    for u in tr.close():
        msgs += p.on_update(u)
    return p, msgs, done


def test_dummy_abort_after_question_moves_on():
    lines = [l for _, l in dummy_items(DummyConfig(trials=4, patterns=["calm", "poor", "calm", "calm"]))]
    p, msgs, done = _run(lines, "dummy", "dummy")
    order = p.order_for_session(1)
    got = [m["question_id"] for m in msgs if m["trial"]]
    # 2번 시도는 답변 중 손 뗌(질문 구간 이후 무효) → 3번은 다음 질문
    assert got == [order[0].question_id, order[1].question_id, order[2].question_id, order[3].question_id]
    assert [t.status for t in done] == ["COMPLETE", "ABORTED", "COMPLETE", "COMPLETE"]
    for m in msgs:
        check_no_labels(m)


def test_real_log_abort_in_ready_reuses_question():
    """실측 161545: S01 T01은 READY 중 손 뗌(HAND_OFF_LONG, 질문 전) → T02가 같은 연습 질문을 받는다."""
    if not os.path.isfile(REAL_LOG):
        pytest.skip("실측 로그 없음")
    lines = [l for _, l in iter_raw_log(REAL_LOG)]
    p, msgs, _ = _run(lines, "hardware", "replay")
    s1 = p.order_for_session(1)
    assert p.question_for(1, 1).question_id == p.question_for(1, 2).question_id == s1[0].question_id
    assert p.question_for(1, 3).question_id == s1[1].question_id
    s2 = p.order_for_session(2)
    assert p.question_for(2, 1).question_id == s2[0].question_id      # 세션이 바뀌면 새 순서
    assert all(m["order_note"] == ORDER_NOTES["replay"] for m in msgs)
    assert "기록 당시" in ORDER_NOTES["replay"]
    assert sum(1 for m in msgs if m["trial"] is None) == 2               # 세션 2개 종료 → 비움 2번


def test_reboot_ends_session_and_clears_question():
    lines = [l for _, l in dummy_items(DummyConfig(trials=2, patterns=["calm", "calm"]))]
    cut = next(i for i, l in enumerate(lines) if ",QUESTION,2,START," in l) + 50
    p, msgs, done = _run(lines[:cut] + ["#BOOT,KITEL_MAIN_V5"], "hardware", "serial")
    assert done[-1].status == "INTERRUPTED"
    assert msgs[-1]["question_text"] is None


# ---------- 판정과 분리 ----------
def test_judgement_code_does_not_use_questions():
    from server import result
    from server.judges import rule
    from server.pipeline import features, quality
    for mod in (rule, result, features, quality):
        src = inspect.getsource(mod)
        assert "questions" not in src and "question_bank" not in src, mod.__name__
