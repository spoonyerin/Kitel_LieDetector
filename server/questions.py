"""질문 순서 (게임 모드). 기기는 질문 문장을 보내지 않으므로 서버가 시도마다 질문을 정한다.

- 질문 은행은 collector/record_session.py의 load_bank로 읽는다 (no, item_id, question_text만 읽음).
  은행 파일에 다른 열이 있어도 읽지 않고, 여기서도 세 값만 남긴다. 정답 라벨(거짓으로 답할 질문)은
  서버가 알지 못하며 다루지 않는다. 판정 코드는 이 모듈을 쓰지 않는다.
- 세션마다 순서를 새로 정한다: 연습 1(무작위) + 본 질문 5(섞기) + 나머지는 추가 질문(섞기).
  난수는 QUESTION_SEED와 세션 순번으로 정하므로 같은 시드면 같은 순서가 나온다.
- 시도가 열릴 때(READY START) 대기열에서 하나를 꺼내 그 시도에 배정한다. 기기의 시도 번호로 계산하지 않는다.
  (기기는 무효가 된 시도 뒤에도 번호를 계속 올리므로 번호로 계산하면 어긋난다)
- 시도가 완료되지 못했을 때(ABORTED, INCOMPLETE, INTERRUPTED):
    질문 구간(QUESTION) 전에 끝났으면 질문을 아직 읽지 않았으므로 같은 질문을 대기열 맨 앞에 다시 넣는다.
    질문 구간 이후에 끝났으면 이미 들은 질문이므로 다음 질문으로 넘어간다.
- 세션 시작 신호(SESSION START)를 못 본 세션(서버를 세션 도중에 켠 경우)에는 질문을 배정하지 않는다.
"""

import os
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from server.pipeline.tracker import PhaseChanged, SessionEnded, SessionStarted, TrialDone
from server.source.lines import COLLECTOR_DIR

import record_session as _rs  # noqa: E402  (server.source.lines가 collector 경로를 추가한다)

DEFAULT_BANK = os.path.join(COLLECTOR_DIR, "question_bank.csv")
N_MAIN = _rs.N_MAIN_QUESTIONS          # 본 질문 수 (collector와 같은 5)
QUESTION_KEYS = ("no", "question_id", "question_text")   # 은행에서 남기는 값 (라벨 없음)

ORDER_NOTES = {
    "replay": "재생: 기록 당시 실제로 물은 질문과 다를 수 있습니다. 서버가 정한 예시 순서입니다.",
    "dummy": "질문 순서는 서버가 정합니다. 진행자는 화면의 질문을 읽어 주세요.",
    "serial": "질문 순서는 서버가 정합니다. 진행자는 화면의 질문을 읽어 주세요.",
}
EXHAUSTED_TEXT = "준비된 질문을 모두 사용했습니다."


def load_questions(path: str = DEFAULT_BANK) -> List[dict]:
    """질문 은행을 읽기 전용으로 읽는다. no, question_id, question_text만 돌려준다."""
    bank = _rs.load_bank(path)
    return [{k: q[k] for k in QUESTION_KEYS} for _, q in sorted(bank.items())]


@dataclass
class Question:
    no: int
    question_id: str
    question_text: str
    role: str              # practice | main | extra
    index: int             # 역할 안에서 몇 번째인지 (1부터)

    @property
    def label(self) -> str:
        if self.role == "practice":
            return "연습 질문"
        if self.role == "main":
            return f"본 질문 {self.index}/{N_MAIN}"
        return f"추가 질문 {self.index}"


def build_order(questions: List[dict], rng: random.Random) -> List[Question]:
    """연습 1 + 본 질문 N_MAIN + 추가 질문(나머지)을 섞어 만든다. 라벨은 쓰지 않는다."""
    pool = list(questions)
    if len(pool) < 1 + N_MAIN:
        raise ValueError(f"질문 은행에 최소 {1 + N_MAIN}문항이 필요합니다 (현재 {len(pool)}개)")
    practice = rng.choice(pool)
    rest = [q for q in pool if q is not practice]
    rng.shuffle(rest)
    main, extra = rest[:N_MAIN], rest[N_MAIN:]
    order = [Question(practice["no"], practice["question_id"], practice["question_text"], "practice", 1)]
    order += [Question(q["no"], q["question_id"], q["question_text"], "main", i) for i, q in enumerate(main, 1)]
    order += [Question(q["no"], q["question_id"], q["question_text"], "extra", i) for i, q in enumerate(extra, 1)]
    return order


@dataclass
class _SessionPlan:
    index: int
    queue: List[Question]
    assigned: Dict[int, Question] = field(default_factory=dict)
    reached: set = field(default_factory=set)      # 질문 구간(QUESTION)에 들어간 시도
    active: bool = True


class QuestionPlanner:
    """진행 추적기의 알림(update)을 받아 시도마다 질문을 배정하고 question_update 메시지를 만든다."""

    def __init__(self, questions: List[dict], seed: Optional[int] = None, input_kind: str = "dummy",
                 source: str = "dummy"):
        self.questions = list(questions)
        self.seed = seed if seed is not None else random.SystemRandom().randrange(1_000_000)
        self.input_kind = input_kind
        self.source = source
        self.sessions: Dict[int, _SessionPlan] = {}
        self.current: Optional[_SessionPlan] = None

    def order_for_session(self, session_index: int) -> List[Question]:
        # 문자열 시드는 실행마다 같은 난수열을 준다 (random 모듈의 버전 2 시드 방식)
        return build_order(self.questions, random.Random(f"{self.seed}-{session_index}"))

    def question_for(self, session_index: int, trial: int) -> Optional[Question]:
        plan = self.sessions.get(session_index)
        return plan.assigned.get(trial) if plan else None

    # ---------- 알림 처리 ----------
    def on_update(self, u) -> list:
        if isinstance(u, SessionStarted):
            plan = _SessionPlan(u.session_index, self.order_for_session(u.session_index))
            self.sessions[u.session_index] = plan
            self.current = plan
            return []
        plan = self.current
        if plan is None or not plan.active:
            return []   # 세션 시작을 못 본 경우 등: 배정하지 않는다
        if isinstance(u, PhaseChanged):
            if u.phase == "READY" and u.trial > 0 and u.trial not in plan.assigned:
                q = plan.queue.pop(0) if plan.queue else None
                if q is not None:
                    plan.assigned[u.trial] = q
                return [self.message(plan.index, u.trial, q)]
            if u.phase == "QUESTION" and u.trial in plan.assigned:
                plan.reached.add(u.trial)
            return []
        if isinstance(u, TrialDone):
            t = u.segment
            q = plan.assigned.get(t.trial)
            if t.session_index == plan.index and q is not None and t.status != "COMPLETE" \
                    and t.trial not in plan.reached and q not in plan.queue:
                plan.queue.insert(0, q)   # 아직 읽지 않은 질문: 다음 시도에 다시 쓴다
            return []
        if isinstance(u, SessionEnded):
            plan.active = False
            return [self.cleared(plan.index)]
        return []

    # ---------- 메시지 ----------
    def message(self, session_index: int, trial: int, q: Optional[Question]) -> dict:
        return {
            "type": "question_update",
            "source": self.source,
            "session_index": session_index,
            "trial": trial,
            "question_id": q.question_id if q else None,
            "question_no": q.no if q else None,
            "question_text": q.question_text if q else EXHAUSTED_TEXT,
            "exhausted": q is None,
            "role": q.role if q else None,
            # 화면용 이름("연습 질문" 등). 정답 라벨 키(label)와 헷갈리지 않게 question_label로 둔다.
            "question_label": q.label if q else "질문 없음",
            "order_note": ORDER_NOTES.get(self.input_kind, ORDER_NOTES["dummy"]),
            "seed": self.seed,
        }

    def cleared(self, session_index: int) -> dict:
        return {"type": "question_update", "source": self.source, "session_index": session_index,
                "trial": None, "question_id": None, "question_no": None, "question_text": None,
                "exhausted": False, "role": None, "question_label": None,
                "order_note": ORDER_NOTES.get(self.input_kind, ORDER_NOTES["dummy"]), "seed": self.seed}
