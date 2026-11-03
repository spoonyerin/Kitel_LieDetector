"""판정 이유 문장 생성.

- 임계값을 실제로 넘은 센서만 문장으로 만든다. 넘은 센서가 없으면 그렇다고 말한다.
- 국소 dz는 같은 시도의 질문 구간과 비교한 값이므로 문장에도 "질문 구간보다"라고 밝힌다.
- 보류이면 사유 코드를 문장으로 바꾼다.
- 이유는 통계적 설명이며 거짓의 증명이 아니다 (안내 문구는 result.py의 DISCLAIMER).
"""

from server.pipeline.features import MIN_QUESTION_SAMPLES

# 센서 이름과 붙는 조사
SENSOR_KR = {"gsr": ("피부전도(GSR)", "가"), "fsr": ("손가락 압력(FSR)", "이"), "ppg": ("맥파(PPG)", "가")}

DEVICE_REASON_KR = {
    "HAND_OFF_LONG": "손이 센서에서 오래 떨어져",
    "HAND_LIFTED": "답변 중 손이 센서에서 떨어져",
}


def hold_text(code: str) -> str:
    """보류 사유 코드를 한국어 문장으로 바꾼다."""
    kind, _, arg = (code or "").partition(":")
    if kind == "DEVICE_INVALID":
        why = DEVICE_REASON_KR.get(arg)
        if why:
            return f"{why} 기기가 이 시도를 무효로 처리했습니다. 판정을 보류합니다."
        return f"기기가 이 시도를 무효로 처리했습니다 (사유: {arg}). 판정을 보류합니다."
    if kind == "NO_SUMMARY":
        return "기기 요약을 받지 못해 판정을 보류합니다."
    if kind == "Q_TOO_SHORT":
        return (f"질문 구간이 너무 짧아({arg}샘플, 최소 {MIN_QUESTION_SAMPLES}샘플) "
                "비교 기준을 만들 수 없습니다. 판정을 보류합니다.")
    if kind == "NO_RESPONSE":
        return "답변 구간 신호가 없어 판정을 보류합니다."
    if kind == "BL_SD_MISSING":
        return "기준 측정 값을 받지 못해 판정을 보류합니다."
    if kind == "CONTACT_EVENT":
        return f"질문이나 답변 중에 손 접촉이 바뀌어({arg}) 판정을 보류합니다."
    if kind == "FEATURE_NAN":
        return "특징값을 계산하지 못해 판정을 보류합니다."
    if kind == "THRESHOLD_UNSET":
        return "판정 임계값이 정해지지 않아 판정을 보류합니다."
    return f"신호 품질 문제로 판정을 보류합니다 ({code})."


def contribution_text(c, threshold: float) -> str:
    name, particle = SENSOR_KR.get(c.sensor, (c.sensor.upper(), "가"))
    direction = "높았습니다" if c.value > 0 else "낮았습니다"
    return f"답변 중 {name}{particle} 질문 구간보다 {abs(c.value):.1f}σ {direction} (임계값 {threshold:.1f}σ)."


def make_reasons(verdict, judge) -> list:
    """verdict: Verdict, judge: RuleJudge (thresholds, exceeded()를 쓴다)."""
    if verdict.decision == "inconclusive":
        return [hold_text(verdict.hold_reason)]
    over = judge.exceeded(verdict)
    if not over:
        th = judge.thresholds
        return [f"임계값(FSR {th['fsr']:.1f}σ, GSR {th['gsr']:.1f}σ)을 넘은 센서가 없습니다."]
    reasons = [contribution_text(c, judge.thresholds[c.sensor]) for c in over]
    if len(over) > 1:
        reasons[0] += " 가장 큰 판정 요인입니다."
    return reasons
