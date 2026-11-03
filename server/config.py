"""서버 설정. 환경 변수에서 읽는다.

| 변수              | 값 (기본)                                       | 뜻 |
|-------------------|------------------------------------------------|----|
| SOURCE            | dummy | replay  (dummy)                        | 입력원. serial은 S5b에서 추가 |
| REPLAY_RUN        | data/real/experiments/run_20261103_161545      | 재생할 run 폴더 또는 raw_serial_log.txt (읽기 전용) |
| REPLAY_SPEED      | 1.0                                            | 재생 배속 (0이면 기다리지 않음) |
| REPLAY_MAX_WAIT   | 2.0                                            | 줄 사이 최대 대기 초 (세션 사이 긴 공백을 줄임) |
| DUMMY_TRIALS      | 10                                             | 더미 시도 수 |
| DUMMY_PATTERNS    | calm,reactive,calm,poor                        | 더미 패턴 (시도마다 차례로) |
| DUMMY_SEED        | 0                                              | 더미 난수 시드 |
| DUMMY_SPEED       | 1.0                                            | 더미 배속 |
| JUDGE             | rule                                           | 판정기 (현재 rule만) |
| HISTORY_LIMIT     | 200                                            | 새 접속자에게 다시 보낼 시도 결과 최대 개수 |
| SAMPLE_BATCH_SEC  | 0.25                                           | 그래프 샘플 묶음 간격 (0.2~0.5초) |
| SAMPLE_DECIMATE   | 2                                              | 샘플 N개 중 1개만 보냄 (2 → 25Hz) |
| SAMPLE_MAX_PER_BATCH | 100                                         | 한 묶음 최대 샘플 수 (넘으면 오래된 것부터 버림) |
"""

import os
from dataclasses import dataclass, field
from typing import List

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIST = os.path.join(REPO_ROOT, "web", "dist")
DEFAULT_REPLAY_RUN = os.path.join(REPO_ROOT, "data", "real", "experiments", "run_20261103_161545")

SOURCES = ("dummy", "replay")
JUDGES = ("rule",)


@dataclass
class Settings:
    source: str = "dummy"
    replay_run: str = DEFAULT_REPLAY_RUN
    replay_speed: float = 1.0
    replay_max_wait: float = 2.0
    dummy_trials: int = 10
    dummy_patterns: List[str] = field(default_factory=lambda: ["calm", "reactive", "calm", "poor"])
    dummy_seed: int = 0
    dummy_speed: float = 1.0
    judge: str = "rule"
    history_limit: int = 200
    sample_batch_sec: float = 0.25
    sample_decimate: int = 2
    sample_max_per_batch: int = 100

    def __post_init__(self):
        if not 0.2 <= self.sample_batch_sec <= 0.5:
            raise ValueError(f"SAMPLE_BATCH_SEC는 0.2~0.5초여야 합니다: {self.sample_batch_sec}")
        if self.sample_decimate < 1 or self.sample_max_per_batch < 1:
            raise ValueError("SAMPLE_DECIMATE와 SAMPLE_MAX_PER_BATCH는 1 이상이어야 합니다")
        if self.source == "serial":
            raise ValueError("SOURCE=serial은 아직 없습니다 (S5b에서 추가). dummy 또는 replay를 쓰세요.")
        if self.source not in SOURCES:
            raise ValueError(f"SOURCE는 {'|'.join(SOURCES)} 중 하나여야 합니다: {self.source}")
        if self.judge not in JUDGES:
            raise ValueError(f"JUDGE는 {'|'.join(JUDGES)} 중 하나여야 합니다: {self.judge}")
        if self.dummy_trials < 1 or self.history_limit < 1:
            raise ValueError("DUMMY_TRIALS와 HISTORY_LIMIT는 1 이상이어야 합니다")


def _get(env, key, default, cast=str):
    raw = env.get(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return cast(raw.strip())
    except ValueError as e:
        raise ValueError(f"환경 변수 {key} 값이 올바르지 않습니다: {raw}") from e


def load_settings(env=None) -> Settings:
    env = os.environ if env is None else env
    patterns = _get(env, "DUMMY_PATTERNS", None)
    run = _get(env, "REPLAY_RUN", DEFAULT_REPLAY_RUN)
    if not os.path.isabs(run):
        run = os.path.join(REPO_ROOT, run)
    return Settings(
        source=_get(env, "SOURCE", "dummy").lower(),
        replay_run=run,
        replay_speed=_get(env, "REPLAY_SPEED", 1.0, float),
        replay_max_wait=_get(env, "REPLAY_MAX_WAIT", 2.0, float),
        dummy_trials=_get(env, "DUMMY_TRIALS", 10, int),
        dummy_patterns=([p.strip() for p in patterns.split(",") if p.strip()] if patterns
                        else ["calm", "reactive", "calm", "poor"]),
        dummy_seed=_get(env, "DUMMY_SEED", 0, int),
        dummy_speed=_get(env, "DUMMY_SPEED", 1.0, float),
        judge=_get(env, "JUDGE", "rule").lower(),
        history_limit=_get(env, "HISTORY_LIMIT", 200, int),
        sample_batch_sec=_get(env, "SAMPLE_BATCH_SEC", 0.25, float),
        sample_decimate=_get(env, "SAMPLE_DECIMATE", 2, int),
        sample_max_per_batch=_get(env, "SAMPLE_MAX_PER_BATCH", 100, int),
    )
