"""규칙 판정기 (RuleJudge): 국소 dz가 임계값을 넘은 센서 수로 판정한다.

- 입력 특징은 국소 dz(fsr_local_dz, gsr_local_dz)만 쓴다. 기기 dz(fsr_dz, gsr_dz)는 쓰지 않는다.
- |국소 dz| >= 임계값인 센서가 MIN_SENSORS_OVER개 이상이면 "lie", 아니면 "truth".
- 품질 검사에서 보류된 시도, 특징이 nan인 시도, 임계값이 정해지지 않은 경우는 "inconclusive".
- 학습된 모델이 아니다: trained=False, score=None (거짓 점수나 확률을 만들지 않는다).

설정 파일 (선택, S14): RULE_CONFIG 환경 변수가 가리키는 JSON에서 임계값을 읽을 수 있다.
    {"format": "truthpulse-rule-config", "version": "0.1", "fsr_th": 3.0, "gsr_th": 3.0, "min_sensors": 1,
     "validated": null 또는 {"n_participants", "n_trials", "balanced_accuracy", "baseline_balanced_accuracy", ...}}
    - 파일이 없거나 잘못되면 아래 코드 상수로 대신하고 이유를 돌려준다 (make_rule_judge).
    - 판정기 안내(note)에 "검증됨"을 쓰는 것은 validated에 검증 정보(참가자 수, 시도 수, 균형 정확도,
      기준 모델 균형 정확도)가 모두 있고, 참가자가 MIN_VALIDATED_PARTICIPANTS명 이상이며, 기준 모델보다
      나을 때뿐이다. 설정 파일이 있다는 것만으로 검증됨으로 표시하지 않는다.
"""

import json
import math
import os
from typing import Optional, Tuple

from server.judges.base import Contribution, Verdict, check_no_labels

# ----------------------------------------------------------
# 판정 임계값 (2026-11-04 사용자 결정)
# 통계에서 흔히 쓰는 관례(평소보다 3σ 이상 벗어남)로 정한 값이다.
# 라벨 데이터로 맞추거나 검증하지 않았다. 판정 성능을 보장하지 않는다.
# None으로 두면 모든 시도를 "임계값 미설정"으로 보류한다.
# ----------------------------------------------------------
FSR_LOCAL_DZ_THRESHOLD: Optional[float] = 3.0   # 관례값, 라벨로 튜닝·검증하지 않음
GSR_LOCAL_DZ_THRESHOLD: Optional[float] = 3.0   # 관례값, 라벨로 튜닝·검증하지 않음
MIN_SENSORS_OVER: Optional[int] = 1             # 임계값을 넘은 센서가 이 개수 이상이면 lie

FEATURE_OF = {"fsr": "fsr_local_dz", "gsr": "gsr_local_dz"}

CONFIG_FORMAT = "truthpulse-rule-config"
CONFIG_VERSIONS = ("0.1",)
MIN_VALIDATED_PARTICIPANTS = 5     # training/train_logreg.py MIN_PARTICIPANTS와 같은 값 (임시값)
VALIDATION_KEYS = ("n_participants", "n_trials", "balanced_accuracy", "baseline_balanced_accuracy")


class RuleConfigError(Exception):
    """규칙 설정 파일을 쓸 수 없는 이유 (콘솔·화면에 그대로 보여 준다)."""


def _number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def validation_ok(v) -> bool:
    """검증 정보가 모두 있고, 참가자 수가 충분하고, 기준 모델보다 나은지."""
    if not isinstance(v, dict) or not all(_number(v.get(k)) for k in VALIDATION_KEYS):
        return False
    if v.get("exploratory"):
        return False
    return (v["n_participants"] >= MIN_VALIDATED_PARTICIPANTS and v["n_trials"] > 0
            and v["balanced_accuracy"] > v["baseline_balanced_accuracy"])


def load_rule_config(path: str) -> dict:
    if not path or not os.path.isfile(path):
        raise RuleConfigError(f"규칙 설정 파일이 없습니다 ({path})")
    try:
        with open(path, encoding="utf-8") as f:
            c = json.load(f)
    except (OSError, ValueError) as e:
        raise RuleConfigError(f"규칙 설정 파일을 읽을 수 없습니다: {e}") from e
    if not isinstance(c, dict):
        raise RuleConfigError("규칙 설정 파일 형식이 아닙니다")
    if "format" in c and c["format"] != CONFIG_FORMAT:
        raise RuleConfigError(f"규칙 설정 파일 형식이 다릅니다: {c['format']} (필요: {CONFIG_FORMAT})")
    if "version" in c and not any(str(c["version"]) == v or str(c["version"]).startswith(v + ".")
                                  for v in CONFIG_VERSIONS):
        raise RuleConfigError(f"지원하지 않는 규칙 설정 버전입니다: {c['version']}")
    missing = [k for k in ("fsr_th", "gsr_th", "min_sensors") if k not in c]
    if missing:
        raise RuleConfigError(f"규칙 설정 파일에 필요한 항목이 없습니다: {', '.join(missing)}")
    for k in ("fsr_th", "gsr_th"):
        if not _number(c[k]) or c[k] <= 0:
            raise RuleConfigError(f"규칙 설정 파일의 {k} 값이 올바르지 않습니다: {c[k]}")
    if not isinstance(c["min_sensors"], int) or isinstance(c["min_sensors"], bool) or             c["min_sensors"] not in (1, len(FEATURE_OF)):
        raise RuleConfigError(f"규칙 설정 파일의 min_sensors 값이 올바르지 않습니다: {c['min_sensors']}")
    return c


def make_rule_judge(config_path: Optional[str] = None) -> Tuple["RuleJudge", Optional[str]]:
    """(판정기, 대체 이유). 설정 파일을 쓸 수 없으면 코드 상수의 RuleJudge와 이유를 돌려준다."""
    if not config_path:
        return RuleJudge(), None
    try:
        c = load_rule_config(config_path)
    except RuleConfigError as e:
        return RuleJudge(), (f"규칙 설정 파일을 쓸 수 없어 코드 기본값(FSR {FSR_LOCAL_DZ_THRESHOLD}σ, "
                             f"GSR {GSR_LOCAL_DZ_THRESHOLD}σ, {MIN_SENSORS_OVER}개 센서)으로 판정합니다: {e}")
    j = RuleJudge(float(c["fsr_th"]), float(c["gsr_th"]), int(c["min_sensors"]))
    j.config_path = config_path
    j.validation = c.get("validated") if isinstance(c.get("validated"), dict) else None
    return j, None


class RuleJudge:
    name = "rule"
    version = "0.0.1"
    trained = False

    def __init__(self, fsr_threshold: Optional[float] = None, gsr_threshold: Optional[float] = None,
                 min_sensors: Optional[int] = None, use_defaults: bool = True):
        """인자를 주지 않으면 위의 상수를 쓴다. use_defaults=False면 인자만 쓴다 (None 허용)."""
        if use_defaults:
            fsr_threshold = FSR_LOCAL_DZ_THRESHOLD if fsr_threshold is None else fsr_threshold
            gsr_threshold = GSR_LOCAL_DZ_THRESHOLD if gsr_threshold is None else gsr_threshold
            min_sensors = MIN_SENSORS_OVER if min_sensors is None else min_sensors
        self.thresholds = {"fsr": fsr_threshold, "gsr": gsr_threshold}
        self.min_sensors = min_sensors
        self.config_path: Optional[str] = None    # 설정 파일에서 읽었으면 그 경로
        self.validation: Optional[dict] = None    # 설정 파일의 validated (있어도 validation_ok일 때만 검증됨)

    @property
    def validated(self) -> bool:
        return self.config_path is not None and validation_ok(self.validation)

    @property
    def configured(self) -> bool:
        return all(v is not None for v in self.thresholds.values()) and self.min_sensors is not None

    @property
    def note(self) -> str:
        """화면에 띄울 판정기 안내."""
        if not self.configured:
            return "판정 임계값이 정해지지 않아 판정을 보류합니다."
        th = (f"규칙 판정 임계값(FSR {self.thresholds['fsr']:.1f}σ, GSR {self.thresholds['gsr']:.1f}σ, "
              f"{self.min_sensors}개 센서 이상)")
        if self.validated:
            v = self.validation
            return (f"{th}은 라벨 데이터로 검증됨: 참가자 {int(v['n_participants'])}명, 시도 {int(v['n_trials'])}개, "
                    f"참가자 분리 검증 균형 정확도 {v['balanced_accuracy']:.2f} "
                    f"(항상 다수 클래스 기준 {v['baseline_balanced_accuracy']:.2f}). 정확도를 보장하지 않습니다.")
        defaults = (FSR_LOCAL_DZ_THRESHOLD, GSR_LOCAL_DZ_THRESHOLD, MIN_SENSORS_OVER)
        if self.config_path and (self.thresholds["fsr"], self.thresholds["gsr"], self.min_sensors) != defaults:
            return f"{th}은 설정 파일에서 정한 값이며, 라벨 데이터로 검증되지 않았습니다."
        return (f"{th}은 통계에서 흔히 쓰는 관례로 정한 값이며, "
                "라벨 데이터로 맞추거나 검증하지 않았습니다.")

    def _verdict(self, decision, contributions=None, hold=None) -> Verdict:
        return Verdict(decision=decision, score=None, trained=self.trained, judge_name=self.name,
                       judge_version=self.version, contributions=contributions or [], hold_reason=hold)

    def judge(self, features: dict, baseline: dict, quality: dict) -> Verdict:
        check_no_labels(features, baseline, quality)
        if not quality.get("ok", False):
            return self._verdict("inconclusive", hold=quality.get("hold_code") or "QUALITY")
        if not self.configured:
            return self._verdict("inconclusive", hold="THRESHOLD_UNSET")

        contributions = []
        for sensor, feat in FEATURE_OF.items():
            v = features.get(feat)
            if not isinstance(v, (int, float)) or math.isnan(v):
                return self._verdict("inconclusive", hold="FEATURE_NAN")
            # 거짓 쪽 기여도 = |값| / 임계값 (1 이상이면 임계값을 넘음). 방향은 value의 부호로 본다.
            contributions.append(Contribution(sensor=sensor, feature=feat, value=float(v),
                                              contribution=abs(v) / self.thresholds[sensor]))
        n_over = sum(1 for c in contributions if abs(c.value) >= self.thresholds[c.sensor])
        decision = "lie" if n_over >= self.min_sensors else "truth"
        return self._verdict(decision, contributions)

    def exceeded(self, verdict: Verdict) -> list:
        """임계값을 넘은 기여도만 큰 순서로 돌려준다 (이유 문장용)."""
        over = [c for c in verdict.contributions if abs(c.value) >= self.thresholds[c.sensor]]
        return sorted(over, key=lambda c: abs(c.value), reverse=True)
