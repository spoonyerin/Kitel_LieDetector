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
    if kind == "DEVICE_RESET":
        return f"답변 중에 기기가 다시 시작돼서 {EXCLUDED}"
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
    if kind == "MODEL_FEATURES_MISSING":
        return f"학습된 판정기에 필요한 신호 값이 많이 비어 있어서 {NOT_JUDGED}"
    if kind == "THRESHOLD_UNSET":
        return f"판정 기준이 아직 정해지지 않아서 {NOT_JUDGED}"
    if kind in RAW_HOLD_KR:
        why, tail = RAW_HOLD_KR[kind]
        return f"{why} {tail}"
    return f"신호 상태가 좋지 않아서 {NOT_JUDGED}"


# 원시 샘플 검사의 보류 사유 (server/pipeline/quality.py의 RAW_* 코드)
RAW_HOLD_KR = {
    "RAW_NO_SAMPLES": ("신호가 들어오지 않아서", NOT_JUDGED),
    "RAW_TIME_ERROR": ("신호의 시간 순서가 뒤섞여서", NOT_JUDGED),
    "RAW_SAMPLES_LOST": ("신호 일부가 전송 중에 빠져서", NOT_JUDGED),
    "RAW_FEW_SAMPLES": ("답변하는 동안의 신호가 충분히 모이지 않아서", NOT_JUDGED),
    "RAW_PHASE_ORDER": ("측정 단계 순서가 맞지 않아서", NOT_JUDGED),
    "RAW_NO_CONTACT": ("질문이나 답변 중에 손가락이 센서에서 떨어져서", EXCLUDED),
    "RAW_FSR_SAT": ("손가락을 너무 세게 눌러 압력 센서가 한계에 닿아서", EXCLUDED),
    "RAW_GSR_SAT": ("피부전도 센서 값이 측정 범위를 벗어나서", EXCLUDED),
}

# 경고 (판정은 함). 화면에 작게 보여 줄 쉬운 문장. 서버 전용 경고(UNKNOWN_WARN_BITS)는 화면에 보내지 않는다.
WARNING_KR = {
    "TIME_GAPS": "신호 간격이 가끔 조금 늦었어요.",
    "SAMPLES_LOST": "신호가 조금 빠졌지만 판정에는 충분했어요.",
    "RATE": "신호 속도가 평소와 조금 달랐어요.",
    "RESPONSE_DURATION": "답변 구간 길이가 평소와 조금 달랐어요.",
    "PHASE_MISMATCH": "단계 표시와 신호 시각이 조금 어긋났어요.",
    "READY_NO_CONTACT": "질문 전 준비 단계에서 손가락이 잠시 떨어졌어요.",
    "READY_FSR_SAT": "질문 전 준비 단계에서 압력 센서가 한계에 닿았어요.",
    "READY_GSR_SAT": "질문 전 준비 단계에서 피부전도 값이 범위를 벗어났어요.",
    "CONTACT_EVENTS": "질문이나 답변 중에 손 접촉이 바뀌었어요.",
}
DEVICE_WARN_KR = {
    "LOW_SAMPLES": "기기가 받은 신호가 적었어요",
    "LOW_IR": "맥파 신호가 약했어요",
    "FSR_SAT": "압력 센서가 한계에 닿았어요",
    "GSR_SAT": "피부전도 값이 범위를 벗어났어요",
    "CONTACT_DIP": "접촉이 잠시 약해졌어요",
    "PPG_OVERFLOW": "맥파 센서 값이 넘쳤어요",
}


def warning_text(code: str) -> str:
    """경고 코드를 쉬운 문장으로 바꾼다 (영문 코드, 숫자 없이)."""
    kind, _, arg = (code or "").partition("=")
    if kind == "DEVICE_WARN":
        parts = [DEVICE_WARN_KR.get(n, "기기가 경고를 보냈어요") for n in arg.split("|") if n]
        return "기기 알림: " + ", ".join(dict.fromkeys(parts)) + "." if parts else "기기가 경고를 보냈어요."
    return WARNING_KR.get(kind, "신호에 작은 문제가 있었어요.")


def contribution_text(c, threshold: float) -> str:
    name, particle = SENSOR_KR.get(c.sensor, (c.sensor.upper(), "가"))
    direction = "높았습니다" if c.value > 0 else "낮았습니다"
    return f"답변 중 {name}{particle} 질문 구간보다 {abs(c.value):.1f}σ {direction} (임계값 {threshold:.1f}σ)."


def make_reasons(verdict, judge) -> list:
    """verdict: Verdict, judge: RuleJudge (thresholds, exceeded()를 쓴다)."""
    if verdict.decision == "inconclusive":
        return [hold_text(verdict.hold_reason)]
    if hasattr(judge, "reasons"):      # 학습된 판정기는 실제로 기여한 특징으로 자기 문장을 만든다
        return judge.reasons(verdict)
    over = judge.exceeded(verdict)
    if not over:
        th = judge.thresholds
        return [f"임계값(FSR {th['fsr']:.1f}σ, GSR {th['gsr']:.1f}σ)을 넘은 센서가 없습니다."]
    reasons = [contribution_text(c, judge.thresholds[c.sensor]) for c in over]
    if len(over) > 1:
        reasons[0] += " 가장 큰 판정 요인입니다."
    return reasons
