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


# ======================================================================
# 전체 특징 (S11): 학습용. 판정(RuleJudge)에는 쓰지 않는다.
# ======================================================================
# 정의는 docs/development.md 7.4절에 있다. 모든 값은 인과적 필터(server/pipeline/signal.py)를 거친 신호로
# 계산하고, 계산할 수 없으면 nan이다. 입력은 시도 샘플과 기준 측정 샘플뿐이며 라벨은 받지 않는다.
# 아래 상수는 모두 임시값이다 (라벨 데이터로 맞추거나 검증하지 않았다, 2026-11-04).
BASELINE_SKIP_SEC = 2.0     # 임시값: 기준 측정 앞부분(불안정 구간)을 버리는 시간
MIN_BASELINE_SEC = 5.0      # 임시값: 남은 기준 구간이 이보다 짧으면 기준 측정 기반 특징은 nan
MIN_RESPONSE_SEC = 5.0      # 임시값: 답변 구간이 이보다 짧으면 모든 특징 nan
FLAT_SD = 0.5               # 임시값: 기준 표준편차가 이보다 작으면 평평한 신호로 보고 거부(nan)
Z_CLIP = 10.0               # 임시값: z 특징 클리핑 범위 (±)
RATIO_CLIP = 10.0           # 임시값: 비율 특징 상한
DURATION_Z = 2.0            # 임시값: 반응 지속시간을 셀 때의 기준 (|z| 초과)
PPG_AMP_FLOOR = 20.0        # 임시값: 맥파 진폭(필터 후 표준편차) 하한. bpm.MIN_AC_STD와 같은 값
REFS = ("bl", "q")          # 기준: 기준 측정(bl), 같은 시도의 질문 구간(q)

FEATURE_NAMES = (
    [f"{s}_{kind}_{ref}{suffix}" for s in SENSORS for ref in REFS
     for kind, suffix in (("mean_delta", "_z"), ("max_change", "_z"), ("sd_ratio", ""), ("duration", "_s"))]
    + ["hr_resp", "hr_bl", "hr_q", "hr_delta_bl", "hr_delta_q", "ppg_amp_ratio_bl", "ppg_amp_ratio_q"]
)


def _clip(v, lo, hi):
    return v if math.isnan(v) else min(hi, max(lo, v))


def _sensor_features(resp, ref, floor, fs):
    """한 센서, 한 기준에 대한 네 가지 특징. 계산할 수 없으면 nan, 평평하면 (nan..., True)."""
    import numpy as np
    nan = float("nan")
    if resp is None or ref is None or len(resp) == 0 or len(ref) == 0:
        return (nan, nan, nan, nan), False
    m, sd = float(np.mean(ref)), float(np.std(ref))
    if sd < FLAT_SD:
        return (nan, nan, nan, nan), True
    sdu = max(sd, floor)
    dev = (resp - m) / sdu
    mean_delta = _clip(float(np.mean(dev)), -Z_CLIP, Z_CLIP)
    max_change = _clip(float(np.max(np.abs(dev))), 0.0, Z_CLIP)
    sd_ratio = _clip(float(np.std(resp)) / sdu, 0.0, RATIO_CLIP)
    duration = float(np.sum(np.abs(dev) > DURATION_Z)) / fs
    return (mean_delta, max_change, sd_ratio, duration), False


def _hr(samples):
    """bpm.estimate_bpm 재사용 (마지막 10초 창). (값 또는 nan, 사유)."""
    from server.pipeline.bpm import estimate_bpm
    if not samples:
        return float("nan"), "NOT_ENOUGH_DATA"
    est = estimate_bpm([s.time_ms for s in samples], [s.ir for s in samples], [s.contact for s in samples])
    return (float("nan") if est.bpm is None else float(est.bpm)), est.reason


def extended_features(trial_samples, baseline_samples=None):
    """(특징 dict, 정보 dict). trial_samples: 시도 샘플, baseline_samples: 그 시도가 쓴 기준 측정 샘플."""
    import numpy as np
    from server.pipeline import signal as sig
    nan = float("nan")
    values = {k: nan for k in FEATURE_NAMES}
    info = {"baseline_found": bool(baseline_samples), "flat": [], "fs": None,
            "n_baseline": 0, "n_question": 0, "n_response": 0, "hr_reasons": {}}
    trial_samples = list(trial_samples or [])
    if not trial_samples:
        return values, info
    tr = sig.preprocess(trial_samples)
    fs = tr["fs"]
    info["fs"] = round(fs, 3)
    q_mask, r_mask = tr["phase"] == "QUESTION", tr["phase"] == "RESPONSE"
    info["n_question"], info["n_response"] = int(q_mask.sum()), int(r_mask.sum())
    if r_mask.sum() < MIN_RESPONSE_SEC * fs:
        return values, info

    bl = None
    if baseline_samples:
        bl = sig.preprocess(list(baseline_samples))
        bl_mask = bl["time_ms"] >= bl["time_ms"][0] + BASELINE_SKIP_SEC * 1000
        info["n_baseline"] = int(bl_mask.sum())
        if bl_mask.sum() < MIN_BASELINE_SEC * bl["fs"]:
            bl = None
    refs = {
        "bl": (lambda ch: bl[ch][bl_mask]) if bl is not None else None,
        "q": (lambda ch: tr[ch][q_mask]) if q_mask.sum() >= MIN_QUESTION_SAMPLES else None,
    }

    for s in SENSORS:
        resp = tr[s][r_mask]
        for ref in REFS:
            ref_x = refs[ref](s) if refs[ref] else None
            (md, mc, sr, du), flat = _sensor_features(resp, ref_x, SD_FLOORS[s], fs)
            if flat:
                info["flat"].append(f"{s}_{ref}")
            values[f"{s}_mean_delta_{ref}_z"], values[f"{s}_max_change_{ref}_z"] = md, mc
            values[f"{s}_sd_ratio_{ref}"], values[f"{s}_duration_{ref}_s"] = sr, du

    # 심박: 답변 끝 10초, 기준 측정 끝 10초, 질문이 끝날 때까지 이어진 12초 (bpm.py 창 규칙)
    if r_mask.sum() >= 10.0 * fs * 0.8:
        values["hr_resp"], info["hr_reasons"]["resp"] = _hr(trial_samples)
    else:
        info["hr_reasons"]["resp"] = "NOT_ENOUGH_DATA"
    if baseline_samples:
        values["hr_bl"], info["hr_reasons"]["bl"] = _hr(list(baseline_samples))
    upto_q = [x for x in trial_samples if x.phase in ("READY", "QUESTION")]
    values["hr_q"], info["hr_reasons"]["q"] = _hr(upto_q)
    values["hr_delta_bl"] = values["hr_resp"] - values["hr_bl"]
    values["hr_delta_q"] = values["hr_resp"] - values["hr_q"]

    # 맥파 진폭: 0.5~5Hz 대역통과 IR의 표준편차, 답변 / 기준
    amp_r = float(np.std(tr["ppg"][r_mask]))
    for ref in REFS:
        ref_x = refs[ref]("ppg") if refs[ref] else None
        if ref_x is None or len(ref_x) == 0:
            continue
        amp_ref = float(np.std(ref_x))
        if amp_ref >= PPG_AMP_FLOOR and not math.isnan(amp_r):
            values[f"ppg_amp_ratio_{ref}"] = _clip(amp_r / amp_ref, 0.0, RATIO_CLIP)
    return values, info
