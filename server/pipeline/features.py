"""특징 추출 (S3 최소판): 시도 안의 '국소 dz'.

국소 dz = (같은 시도의 RESPONSE 구간 평균 - QUESTION 구간 평균) / 기준 측정 표준편차
- 기준 측정 표준편차는 기기 요약(#SUMMARY)의 bl_fsr_sd, bl_gsr_sd를 쓰고 아래 하한을 적용한다.
- 기기가 주는 fsr_dz, gsr_dz는 (답변 평균 - 기준 평균) / 기준 표준편차라서 세션이 길어질수록
  한 방향으로 커지는 추세가 있었다. 국소 dz는 바로 앞의 QUESTION 구간을 기준으로 삼아 이 추세를
  줄이지만 없애지는 못한다 (국소 GSR은 시도 순서와의 상관 |r| 0.60~0.99로 방향성이 남음).
  (실측 4개 로그의 세션별 비교는 2026-11-04 분석에서 확인)
- 기기 dz는 비교와 화면 표시용으로만 함께 내보낸다. 판정에는 쓰지 않는다.
- 입력은 샘플과 기기 요약뿐이다. 정답 라벨은 받지 않는다.
"""

import math
import statistics
from dataclasses import dataclass, field
from typing import List, Optional

# 표준편차 하한 (ADC 단위). 기준이 지나치게 평평하면 작은 변화도 큰 dz가 되는 것을 막는다.
# 실측 4개 로그를 보고 정한 임시값이며 라벨 데이터로 맞추거나 검증하지 않았다.
FSR_SD_FLOOR = 20.0
GSR_SD_FLOOR = 5.0
# QUESTION 구간 최소 샘플 수 (50Hz 기준 1초). 이보다 짧으면 비교 기준으로 쓰지 않는다.
MIN_QUESTION_SAMPLES = 50

SD_FLOORS = {"fsr": FSR_SD_FLOOR, "gsr": GSR_SD_FLOOR}
SENSORS = ("fsr", "gsr")


@dataclass
class FeatureResult:
    features: dict                       # 판정기에 넘기는 값 (국소 dz + 참고용 기기 dz)
    problems: List[str] = field(default_factory=list)   # 계산할 수 없었던 이유 코드
    detail: dict = field(default_factory=dict)          # 화면/기록용 중간값


def _nan_to_none(v):
    return None if isinstance(v, float) and math.isnan(v) else v


def local_features(samples, summary_values: Optional[dict]) -> FeatureResult:
    """samples: server.source.lines.Sample 목록, summary_values: TrialSummary.values (없으면 None)."""
    nan = float("nan")
    feats = {"fsr_local_dz": nan, "gsr_local_dz": nan, "fsr_dz": nan, "gsr_dz": nan}
    problems, detail = [], {}
    q = [s for s in samples if s.phase == "QUESTION"]
    r = [s for s in samples if s.phase == "RESPONSE"]
    detail["n_question"], detail["n_response"] = len(q), len(r)

    if summary_values is None:
        problems.append("NO_SUMMARY")
        return FeatureResult(feats, problems, detail)
    for key in SENSORS:
        dev = summary_values.get(f"{key}_dz")
        feats[f"{key}_dz"] = dev if isinstance(dev, (int, float)) else nan

    if len(q) < MIN_QUESTION_SAMPLES:
        problems.append(f"Q_TOO_SHORT:{len(q)}")
    if not r:
        problems.append("NO_RESPONSE")
    if problems:
        return FeatureResult(feats, problems, detail)

    for key in SENSORS:
        sd = summary_values.get(f"bl_{key}_sd")
        if not isinstance(sd, (int, float)) or math.isnan(sd):
            problems.append(f"BL_SD_MISSING:{key}")
            continue
        sd_used = max(float(sd), SD_FLOORS[key])
        q_mean = statistics.fmean(getattr(s, key) for s in q)
        r_mean = statistics.fmean(getattr(s, key) for s in r)
        feats[f"{key}_local_dz"] = (r_mean - q_mean) / sd_used
        detail[key] = {"question_mean": q_mean, "response_mean": r_mean,
                       "baseline_sd": float(sd), "sd_used": sd_used, "floor_applied": sd < SD_FLOORS[key]}
    return FeatureResult(feats, problems, detail)


def json_features(features: dict) -> dict:
    """JSON으로 보낼 수 있게 nan을 None으로 바꾼다."""
    return {k: _nan_to_none(v) for k, v in features.items()}
