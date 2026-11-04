"""서버 설정. 환경 변수에서 읽는다.

| 변수              | 값 (기본)                                       | 뜻 |
|-------------------|------------------------------------------------|----|
| SOURCE            | dummy | replay | serial  (dummy)               | 입력원 |
| SERIAL_PORT       | (없음, serial이면 필수)                         | 기기 COM 포트 (예: COM6). 목록: python -m server.cli ports |
| SERIAL_BAUD       | 115200                                         | 통신 속도. 기본값은 collector/record_session.py의 --baud 기본값 |
| SERIAL_RETRY_SEC  | 1.5                                            | 포트가 없거나 끊겼을 때 다시 여는 간격 (1~2초) |
| SERIAL_IDLE_SEC   | 5                                              | 이 시간 동안 기기 줄이 없으면 live=false (0이면 끔) |
| BPM_INTERVAL_SEC  | 1.0                                            | 심박 추정값(bpm_update)을 보내는 간격 (0.5~1초) |
| QUESTION_SEED     | (없음 → 서버 시작마다 무작위)                    | 질문 순서 시드. 같은 값이면 같은 순서 |
| QUESTION_BANK     | collector/question_bank.csv                    | 질문 은행 (읽기 전용, no/item_id/question_text만 읽음) |
| REPLAY_RUN        | data/real/experiments/run_20261103_161545      | 재생할 run 폴더 또는 raw_serial_log.txt (읽기 전용) |
| REPLAY_SPEED      | 1.0                                            | 재생 배속 (0이면 기다리지 않음) |
| REPLAY_MAX_WAIT   | 2.0                                            | 줄 사이 최대 대기 초 (세션 사이 긴 공백을 줄임) |
| DUMMY_TRIALS      | 10                                             | 더미 시도 수 |
| DUMMY_PATTERNS    | calm,reactive,calm,poor                        | 더미 패턴 (시도마다 차례로) |
| DUMMY_SEED        | 0                                              | 더미 난수 시드 |
| DUMMY_SPEED       | 1.0                                            | 더미 배속 |
| JUDGE             | rule                                           | 판정기 rule 또는 logreg (logreg를 쓸 수 없으면 rule로 대체) |
| MODEL_PATH        | training/out/model.json                        | logreg가 읽을 model.json |
| ALLOW_EXPLORATORY_MODEL | (없음 = 끔)                              | 1/true면 탐색용 모델(exploratory)도 로드 |
| RULE_CONFIG       | (없음 = rule.py 상수 3.0/3.0/1)                 | 규칙 판정기 임계값 JSON (training.evaluate_rule이 제안). 잘못되면 상수로 대체 |
| HISTORY_LIMIT     | 200                                            | 새 접속자에게 다시 보낼 시도 결과 최대 개수 |
| SAMPLE_BATCH_SEC  | 0.25                                           | 그래프 샘플 묶음 간격 (0.2~0.5초) |
| SAMPLE_DECIMATE   | 2                                              | 샘플 N개 중 1개만 보냄 (2 → 25Hz) |
| SAMPLE_MAX_PER_BATCH | 100                                         | 한 묶음 최대 샘플 수 (넘으면 오래된 것부터 버림) |
"""

import os
from dataclasses import dataclass, field
from typing import List, Optional

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIST = os.path.join(REPO_ROOT, "web", "dist")
DEFAULT_REPLAY_RUN = os.path.join(REPO_ROOT, "data", "real", "experiments", "run_20261103_161545")
DEFAULT_QUESTION_BANK = os.path.join(REPO_ROOT, "collector", "question_bank.csv")

SOURCES = ("dummy", "replay", "serial")
DEFAULT_BAUD = 115200   # server/source/serial_source.py와 같은 값 (collector/record_session.py 1201행)
JUDGES = ("rule", "logreg")
DEFAULT_MODEL_PATH = os.path.join(REPO_ROOT, "training", "out", "model.json")


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
    model_path: str = DEFAULT_MODEL_PATH
    allow_exploratory_model: bool = False
    rule_config: Optional[str] = None
    history_limit: int = 200
    sample_batch_sec: float = 0.25
    sample_decimate: int = 2
    sample_max_per_batch: int = 100
    serial_port: str = ""
    serial_baud: int = DEFAULT_BAUD
    serial_retry_sec: float = 1.5
    serial_idle_sec: float = 5.0
    bpm_interval_sec: float = 1.0
    question_seed: Optional[int] = None
    question_bank: str = DEFAULT_QUESTION_BANK

    def __post_init__(self):
        if not 0.5 <= self.bpm_interval_sec <= 1.0:
            raise ValueError(f"BPM_INTERVAL_SEC는 0.5~1초여야 합니다: {self.bpm_interval_sec}")
        if self.source == "serial" and not self.serial_port:
            raise ValueError("SOURCE=serial이면 SERIAL_PORT가 필요합니다 (예: COM6). 포트 목록: python -m server.cli ports")
        if not 1.0 <= self.serial_retry_sec <= 2.0:
            raise ValueError(f"SERIAL_RETRY_SEC는 1~2초여야 합니다: {self.serial_retry_sec}")
        if self.serial_baud <= 0 or self.serial_idle_sec < 0:
            raise ValueError("SERIAL_BAUD는 양수, SERIAL_IDLE_SEC는 0 이상이어야 합니다")
        if not 0.2 <= self.sample_batch_sec <= 0.5:
            raise ValueError(f"SAMPLE_BATCH_SEC는 0.2~0.5초여야 합니다: {self.sample_batch_sec}")
        if self.sample_decimate < 1 or self.sample_max_per_batch < 1:
            raise ValueError("SAMPLE_DECIMATE와 SAMPLE_MAX_PER_BATCH는 1 이상이어야 합니다")
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
        model_path=_abs(_get(env, "MODEL_PATH", DEFAULT_MODEL_PATH)),
        allow_exploratory_model=_get(env, "ALLOW_EXPLORATORY_MODEL", False, _truthy),
        rule_config=_abs(_get(env, "RULE_CONFIG", "")) if _get(env, "RULE_CONFIG", "") else None,
        history_limit=_get(env, "HISTORY_LIMIT", 200, int),
        sample_batch_sec=_get(env, "SAMPLE_BATCH_SEC", 0.25, float),
        sample_decimate=_get(env, "SAMPLE_DECIMATE", 2, int),
        sample_max_per_batch=_get(env, "SAMPLE_MAX_PER_BATCH", 100, int),
        serial_port=_get(env, "SERIAL_PORT", ""),
        serial_baud=_get(env, "SERIAL_BAUD", DEFAULT_BAUD, int),
        serial_retry_sec=_get(env, "SERIAL_RETRY_SEC", 1.5, float),
        serial_idle_sec=_get(env, "SERIAL_IDLE_SEC", 5.0, float),
        bpm_interval_sec=_get(env, "BPM_INTERVAL_SEC", 1.0, float),
        question_seed=_get(env, "QUESTION_SEED", None, int),
        question_bank=_abs(_get(env, "QUESTION_BANK", DEFAULT_QUESTION_BANK)),
    )


def _truthy(value: str) -> bool:
    v = value.strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    raise ValueError(value)


def _abs(path: str) -> str:
    return path if os.path.isabs(path) else os.path.join(REPO_ROOT, path)
