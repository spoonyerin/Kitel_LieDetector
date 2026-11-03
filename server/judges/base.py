"""판정기 인터페이스 (docs/development.md 6절).

모든 판정기는 같은 입력(features, baseline, quality)과 같은 출력(Verdict)을 가진다.
정답 라벨이 입력에 섞이면 오류를 낸다 (작업 규칙 5).
"""

from dataclasses import dataclass, field
from typing import Optional, Protocol

# 판정 입력에 들어오면 안 되는 키 (정답 라벨과 질문 계획)
LABEL_KEYS = ("ground_truth", "condition", "planned_condition", "marked_lie", "label", "confirmed")


@dataclass
class Contribution:
    sensor: str          # "ppg" | "gsr" | "fsr"
    feature: str
    value: float         # 표준화된 특징값 (σ)
    contribution: float  # 판정에 기여한 정도 (부호 있음, 양수 = 거짓 쪽)


@dataclass
class Verdict:
    decision: str                    # "truth" | "lie" | "inconclusive"
    score: Optional[float]           # 0~1 또는 None
    trained: bool
    judge_name: str
    judge_version: str
    contributions: list = field(default_factory=list)
    hold_reason: Optional[str] = None   # 보류 사유 코드 (문장은 server/reasons.py가 만든다)


class Judge(Protocol):
    name: str
    version: str
    trained: bool

    def judge(self, features: dict, baseline: dict, quality: dict) -> Verdict: ...


def check_no_labels(*inputs: dict) -> None:
    """입력 사전에 라벨 키가 있으면 ValueError를 낸다 (중첩 사전도 확인)."""
    for d in inputs:
        if not isinstance(d, dict):
            continue
        for k, v in d.items():
            if k in LABEL_KEYS:
                raise ValueError(f"판정 입력에 정답 라벨 키가 있습니다: {k}")
            if isinstance(v, dict):
                check_no_labels(v)
