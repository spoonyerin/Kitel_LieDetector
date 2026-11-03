"""판정 이유 문장 생성.

- 임계값을 실제로 넘은 센서만 문장으로 만든다. 넘은 센서가 없으면 그렇다고 말한다.
- 국소 dz는 같은 시도의 질문 구간과 비교한 값이므로 문장에도 "질문 구간보다"라고 밝힌다.
- 보류이면 사유 코드를 문장으로 바꾼다.
- 이유는 통계적 설명이며 거짓의 증명이 아니다 (안내 문구는 result.py의 DISCLAIMER).
"""

from server.pipeline.features import MIN_QUESTION_SAMPLES

# 센서 이름과 붙는 조사
SENSOR_KR = {"gsr": ("피부전도(GSR)", "가"), "fsr": ("손가락 압력(FSR)", "이"), "ppg": ("맥파(PPG)", "가")}

# 보류 사유는 참가자가 바로 알아듣는 쉬운 말로 쓴다. 코드(hold_code)는 결과에 따로 남는다.
EXCLUDED = "이 답변은 측정에서 제외했어요."
NOT_JUDGED = "이 답변은 판정하지 않았어요."
CONTACT_KR = {
    "CONTACT_SHIFT": "손 위치가 바뀌어",
    "CONTACT_DIP": "손가락이 센서에서 살짝 떨어져",
    "HAND_LOST": "손이 센서에서 떨어져",
    "BASELINE_LOST": "기준 측정이 흐트러져",
}
DEVICE_REASON_KR = {
    "HAND_OFF_LONG": "손이 센서에서 오래 떨어져 있어서",
    "HAND_LIFTED": "답변 중에 손을 떼서",
}


def hold_text(code: str) -> str:
    """보류 사유 코드를 쉬운 한국어 문장으로 바꾼다."""
    kind, _, arg = (code or "").partition(":")
    if kind == "CONTACT_EVENT":
        return f"{CONTACT_KR.get(arg, '손 접촉이 바뀌어')} {EXCLUDED}"
    if kind == "DEVICE_INVALID":
        why = DEVICE_REASON_KR.get(arg)
        return f"{why} {EXCLUDED}" if why else f"기기가 이 답변을 무효로 처리해서 {EXCLUDED}"
    if kind == "NO_SUMMARY":
        return f"기기에서 측정 결과를 받지 못해서 {NOT_JUDGED}"
    if kind == "Q_TOO_SHORT":
        seconds = MIN_QUESTION_SAMPLES / 50
        return f"질문 시간이 {seconds:g}초보다 짧아 비교할 기준이 없어서 {NOT_JUDGED}"
    if kind == "NO_RESPONSE":
        return f"답변하는 동안의 신호가 없어서 {NOT_JUDGED}"
    if kind == "BL_SD_MISSING":
        return f"기준 측정 값이 없어서 {NOT_JUDGED}"
    if kind == "FEATURE_NAN":
        return f"신호 값을 계산하지 못해서 {NOT_JUDGED}"
    if kind == "THRESHOLD_UNSET":
        return f"판정 기준이 아직 정해지지 않아서 {NOT_JUDGED}"
    return f"신호 상태가 좋지 않아서 {NOT_JUDGED}"


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
