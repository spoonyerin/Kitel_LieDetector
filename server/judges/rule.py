"""규칙 판정기 (RuleJudge): 국소 dz가 임계값을 넘은 센서 수로 판정한다.

- 입력 특징은 국소 dz(fsr_local_dz, gsr_local_dz)만 쓴다. 기기 dz(fsr_dz, gsr_dz)는 쓰지 않는다.
- |국소 dz| >= 임계값인 센서가 MIN_SENSORS_OVER개 이상이면 "lie", 아니면 "truth".
- 품질 검사에서 보류된 시도, 특징이 nan인 시도, 임계값이 정해지지 않은 경우는 "inconclusive".
- 학습된 모델이 아니다: trained=False, score=None (거짓 점수나 확률을 만들지 않는다).
"""

import math
from typing import Optional

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

    @property
    def configured(self) -> bool:
        return all(v is not None for v in self.thresholds.values()) and self.min_sensors is not None

    @property
    def note(self) -> str:
        """화면에 띄울 판정기 안내."""
        if not self.configured:
            return "판정 임계값이 정해지지 않아 판정을 보류합니다."
        return (f"규칙 판정 임계값(FSR {self.thresholds['fsr']:.1f}σ, GSR {self.thresholds['gsr']:.1f}σ, "
                f"{self.min_sensors}개 센서 이상)은 통계에서 흔히 쓰는 관례로 정한 값이며, "
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
