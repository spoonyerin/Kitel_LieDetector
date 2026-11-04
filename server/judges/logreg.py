"""학습된 로지스틱 회귀 판정기 (LogregJudge, S13). training/train_logreg.py가 만든 model.json을 읽는다.

계산 (model.json 그대로):
    x = 특징값 (feature_order 순서), 없으면 impute_median
    z = clip((x - scaler_mean) / scaler_std, clip_range)
    기여도_i = coef_i × z_i,  점수 = 1 / (1 + exp(-(Σ기여도 + intercept)))
    점수 >= threshold 이면 lie, 아니면 truth
- 점수는 0~1의 "거짓 점수"다. 보정(calibration)을 평가하기 전이므로 다른 이름으로 부르지 않는다.
- 품질 보류는 판정기보다 먼저 적용한다 (quality.ok가 거짓이면 inconclusive, score=None).
- 입력에 라벨 키가 있으면 ValueError (check_no_labels).
- 특징의 절반 넘게 없으면 결측 대체만으로 판정하지 않고 보류한다 (MODEL_FEATURES_MISSING).

선택 (select_judge): JUDGE=logreg인데 model.json이 없거나 형식·버전이 맞지 않으면 서버를 멈추지 않고
RuleJudge로 대신하고 그 이유를 돌려준다. exploratory=true 모델은 ALLOW_EXPLORATORY_MODEL을 켰을 때만 쓴다.
"""

import json
import math
import os
from typing import Optional, Tuple

from server.judges.base import Contribution, Verdict, check_no_labels

MODEL_FORMAT = "truthpulse-logreg"
SUPPORTED_VERSIONS = ("0.1",)          # model.json version의 "주.부" 앞부분
REQUIRED_KEYS = ("format", "version", "feature_order", "impute_median", "scaler_mean", "scaler_std",
                 "clip_range", "coef", "intercept", "threshold", "training")
MAX_IMPUTED_FRAC = 0.5                 # 임시값: 이보다 많은 특징이 비어 있으면 보류
REASON_MIN_CONTRIBUTION = 0.25         # 임시값: 이유로 말할 최소 기여도 (로그 오즈 단위)
MAX_REASONS = 3


class ModelLoadError(Exception):
    """model.json을 쓸 수 없는 이유 (화면·콘솔에 그대로 보여 준다)."""


def load_model(path: str, allow_exploratory: bool = False) -> dict:
    if not path or not os.path.isfile(path):
        raise ModelLoadError(f"model.json이 없습니다 ({path})")
    try:
        with open(path, encoding="utf-8") as f:
            m = json.load(f)
    except (OSError, ValueError) as e:
        raise ModelLoadError(f"model.json을 읽을 수 없습니다: {e}") from e
    if not isinstance(m, dict):
        raise ModelLoadError("model.json 형식이 아닙니다")
    missing = [k for k in REQUIRED_KEYS if k not in m]
    if missing:
        raise ModelLoadError(f"model.json에 필요한 항목이 없습니다: {', '.join(missing)}")
    if m["format"] != MODEL_FORMAT:
        raise ModelLoadError(f"model.json 형식이 다릅니다: {m['format']} (필요: {MODEL_FORMAT})")
    if not any(str(m["version"]).startswith(v + ".") or str(m["version"]) == v for v in SUPPORTED_VERSIONS):
        raise ModelLoadError(f"지원하지 않는 모델 버전입니다: {m['version']} (지원: {', '.join(SUPPORTED_VERSIONS)}.x)")
    n = len(m["feature_order"])
    if n == 0 or any(len(m[k]) != n for k in ("impute_median", "scaler_mean", "scaler_std", "coef")):
        raise ModelLoadError("model.json의 특징 수와 계수·정규화 값의 길이가 맞지 않습니다")
    if any((not isinstance(s, (int, float))) or s <= 0 for s in m["scaler_std"]):
        raise ModelLoadError("model.json의 표준편차 값이 올바르지 않습니다")
    if not (0 < float(m["threshold"]) < 1) or len(m["clip_range"]) != 2:
        raise ModelLoadError("model.json의 임계값 또는 클리핑 범위가 올바르지 않습니다")
    if m.get("exploratory") and not allow_exploratory:
        raise ModelLoadError("탐색용 모델(exploratory)입니다. 쓰려면 ALLOW_EXPLORATORY_MODEL=1로 허용하세요")
    return m


# ----------------------------------------------------------
# 특징 이름 → 센서, 쉬운 문장
# ----------------------------------------------------------
SENSOR_KR = {"fsr": "손가락 압력", "gsr": "피부전도", "hr": "심박", "ppg": "맥파"}
SENSOR_SUBJECT = {"fsr": "손가락 압력이", "gsr": "피부전도가", "hr": "심박이", "ppg": "맥파가"}   # 주격 조사
REF_KR = {"bl": "기준 측정과 비교", "q": "질문 구간과 비교"}


def sensor_of(name: str) -> str:
    if name.startswith("fsr"):
        return "fsr"
    if name.startswith("gsr"):
        return "gsr"
    return "ppg"   # hr_*, ppg_* 는 모두 PPG(IR)에서 나온다


def _ref(name):
    for ref in ("bl", "q"):
        if f"_{ref}_" in name + "_" or name.endswith(f"_{ref}"):
            return REF_KR[ref]
    return ""


def feature_sentence(name: str, z: float) -> str:
    """특징 이름과 표준화 값(학습 데이터의 평소 대비 위/아래)으로 쉬운 문장을 만든다. 숫자·영문 코드 없음."""
    up = z > 0
    big, long_, high = ("컸", "길었", "높았") if up else ("작았", "짧았", "낮았")
    s = SENSOR_KR.get(name.split("_")[0], "신호")
    ref = _ref(name)
    tail = f" ({ref})" if ref else ""
    if "local_dz" in name:
        return f"답변 중 {s} 변화가 질문 구간과 비교해 평소보다 {big}습니다."
    if "mean_delta" in name:
        return f"답변 중 {s}의 평균 변화가 평소보다 {big}습니다{tail}."
    if "max_change" in name:
        return f"답변 중 {s}의 가장 큰 변화가 평소보다 {big}습니다{tail}."
    if "sd_ratio" in name:
        return f"답변 중 {s}의 변동이 평소보다 {big}습니다{tail}."
    if "duration" in name:
        subj = SENSOR_SUBJECT.get(name.split("_")[0], "신호가")
        return f"답변 중 {subj} 크게 벗어나 있던 시간이 평소보다 {long_}습니다{tail}."
    if name.startswith("hr_delta"):
        return f"답변 중 심박 변화가 평소보다 {big}습니다{tail}."
    if name == "hr_resp":
        return f"답변 중 심박이 평소보다 {high}습니다."
    if name in ("hr_bl", "hr_q"):
        return f"{'기준 측정' if name == 'hr_bl' else '질문'} 중 심박이 평소보다 {high}습니다."
    if name.startswith("ppg_amp"):
        return f"답변 중 맥파 세기 변화가 평소보다 {big}습니다{tail}."
    return f"답변 중 신호 특징 하나가 평소보다 {big}습니다."


# ----------------------------------------------------------
# 판정기
# ----------------------------------------------------------
class LogregJudge:
    name = "logreg"
    trained = True
    needs_extended_features = True     # result.py가 전체 특징(S11)까지 넘긴다

    def __init__(self, model: dict, path: Optional[str] = None):
        self.model = model
        self.path = path
        self.version = str(model["version"])
        self.exploratory = bool(model.get("exploratory", False))
        self.names = list(model["feature_order"])
        self.threshold = float(model["threshold"])

    @property
    def note(self) -> str:
        t = self.model.get("training", {})
        cv = t.get("cv_metrics", {})
        ba = lambda k: cv.get(k, {}).get("balanced_accuracy")
        fmt = lambda v: "-" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v:.2f}"
        head = "탐색용 모델(검증 부족)입니다. " if self.exploratory else ""
        return (f"{head}학습된 로지스틱 회귀 v{self.version}: 참가자 {t.get('n_participants', '?')}명, "
                f"시도 {t.get('n_trials', '?')}개(진실 {t.get('n_truth', '?')}, 거짓 {t.get('n_lie', '?')})로 학습. "
                f"참가자 분리 검증 균형 정확도 {fmt(ba('logreg'))} (규칙 판정기 {fmt(ba('rule'))}, "
                f"항상 다수 {fmt(ba('majority'))}). 거짓 점수는 0~1 값이며 보정을 평가하지 않은 값입니다.")

    def _verdict(self, decision, score=None, contributions=None, hold=None) -> Verdict:
        return Verdict(decision=decision, score=score, trained=True, judge_name=self.name,
                       judge_version=self.version, contributions=contributions or [], hold_reason=hold)

    def judge(self, features: dict, baseline: dict, quality: dict) -> Verdict:
        check_no_labels(features, baseline, quality)
        if not quality.get("ok", False):
            return self._verdict("inconclusive", hold=quality.get("hold_code") or "QUALITY")
        m = self.model
        lo, hi = m["clip_range"]
        logit, contributions, missing = float(m["intercept"]), [], 0
        for i, name in enumerate(self.names):
            v = features.get(name)
            if v is None or (isinstance(v, float) and math.isnan(v)):
                v = m["impute_median"][i]
                missing += 1
            z = min(hi, max(lo, (float(v) - m["scaler_mean"][i]) / m["scaler_std"][i]))
            c = m["coef"][i] * z
            logit += c
            contributions.append(Contribution(sensor=sensor_of(name), feature=name, value=z, contribution=c))
        if missing > MAX_IMPUTED_FRAC * len(self.names):
            return self._verdict("inconclusive", hold="MODEL_FEATURES_MISSING")
        score = 1.0 / (1.0 + math.exp(-logit))
        return self._verdict("lie" if score >= self.threshold else "truth", score, contributions)

    def top_contributions(self, verdict: Verdict) -> list:
        """판정 방향으로 실제로 기여한 특징 (기여도가 REASON_MIN_CONTRIBUTION 이상), 큰 순서로 최대 MAX_REASONS개."""
        sign = 1 if verdict.decision == "lie" else -1
        chosen = [c for c in verdict.contributions if sign * c.contribution >= REASON_MIN_CONTRIBUTION]
        return sorted(chosen, key=lambda c: -abs(c.contribution))[:MAX_REASONS]

    def is_highlighted(self, verdict: Verdict, c: Contribution) -> bool:
        return c in self.top_contributions(verdict)

    def reasons(self, verdict: Verdict) -> list:
        top = self.top_contributions(verdict)
        side = "높인" if verdict.decision == "lie" else "낮춘"
        if not top:
            return ["뚜렷하게 한쪽으로 기여한 특징이 없습니다. 여러 특징의 작은 차이가 모여 정해진 판정입니다."]
        out = [f"{feature_sentence(c.feature, c.value)} 거짓 점수를 {side} 요인입니다." for c in top]
        if len(top) > 1:
            out[0] += " 가장 큰 요인입니다."
        return out


# ----------------------------------------------------------
# 판정기 선택 (대체 포함)
# ----------------------------------------------------------
def select_judge(kind: str, model_path: Optional[str] = None,
                 allow_exploratory: bool = False) -> Tuple[object, Optional[str]]:
    """(판정기, 대체 이유). logreg를 쓸 수 없으면 RuleJudge와 이유를 돌려준다 (예외를 내지 않는다)."""
    from server.judges.rule import RuleJudge
    if kind != "logreg":
        return RuleJudge(), None
    try:
        return LogregJudge(load_model(model_path, allow_exploratory), model_path), None
    except ModelLoadError as e:
        return RuleJudge(), f"학습된 판정기를 쓸 수 없어 규칙 판정기로 대신합니다: {e}"
