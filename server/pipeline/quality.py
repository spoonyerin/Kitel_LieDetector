"""품질 검사. 판정하지 말아야 할 시도를 골라 보류 사유 코드를 붙이고, 판정을 막지 않는 경고를 모은다.

보류 사유 코드 (앞에서부터 먼저 확인한다. hold_code는 첫 번째 코드):
    DEVICE_RESET             시도 중에 기기가 재부팅됨 (추적기 상태 INTERRUPTED)
    NO_SUMMARY               기기 요약을 받지 못함
    DEVICE_INVALID:<사유>     기기가 valid=0으로 보냄 (예: HAND_OFF_LONG, HAND_LIFTED)
    RAW_*                    원시 샘플을 믿을 수 없음 (아래 표, collector의 check_segment 결과 사용)
    Q_TOO_SHORT:<샘플 수>     QUESTION 구간이 너무 짧아 국소 dz의 기준을 만들 수 없음
    NO_RESPONSE / BL_SD_MISSING:<센서>   특징 계산에 필요한 값이 없음
    CONTACT_EVENT:<이벤트>    QUESTION~RESPONSE 구간에 손 접촉 변화 이벤트가 있음 (HOLD_ON_CONTACT_EVENTS)

원시 샘플 검사 (collector/record_session.py의 check_segment를 그대로 쓰고, 그 결과를 아래처럼 나눈다):

    check_segment 코드      뜻                                       처리
    NO_SAMPLES             시도에 샘플이 하나도 없음                  보류 RAW_NO_SAMPLES
    DUP_TS                 같은 기기 시각이 두 번 나옴                보류 RAW_TIME_ERROR
    BACKWARDS_TS           기기 시각이 거꾸로 감                      보류 RAW_TIME_ERROR
    SERIAL_LOST            순번(seq)이 건너뜀 = 전송 중 샘플이 빠짐     MAX_LOST_RATIO 초과면 보류 RAW_SAMPLES_LOST,
                                                                    이하면 경고 SAMPLES_LOST
    RESPONSE_FEW_SAMPLES   답변 구간 샘플이 기대의 90% 미만            보류 RAW_FEW_SAMPLES
    PHASE_ORDER            샘플의 단계 순서가 READY→QUESTION→RESPONSE가 아님  보류 RAW_PHASE_ORDER
    NO_CONTACT_SAMPLES     접촉(contact)=0 샘플이 있음                질문·답변 구간이면 보류 RAW_NO_CONTACT,
                                                                    준비(READY)에만 있으면 경고 READY_NO_CONTACT
    FSR_4095               FSR이 4095(ADC 상한)                       질문·답변 구간이면 보류 RAW_FSR_SAT,
                                                                    준비에만 있으면 경고 READY_FSR_SAT
    GSR_SAT                GSR이 0 또는 4095                          질문·답변 구간이면 보류 RAW_GSR_SAT,
                                                                    준비에만 있으면 경고 READY_GSR_SAT
    TIME_GAPS              샘플 간격이 30ms 초과 (실측 36~40ms)        경고 (모든 실측 시도에 나옴, I10)
    RATE                   샘플링 속도가 45~55Hz 밖                    경고
    RESPONSE_DURATION      답변 구간 이벤트 길이가 20초 ±50ms 밖        경고
    PHASE_MISMATCH         샘플의 단계 표시가 이벤트 시각과 어긋남       경고

    질문·답변 구간만 보류하는 이유: 판정 특징(국소 dz)은 QUESTION과 RESPONSE 샘플만 쓴다.

경고(warnings)는 판정을 막지 않고 기록만 한다. 기기 경고 비트와 알 수 없는 비트도 경고다.
"""

from dataclasses import dataclass, field
from typing import List, Optional

from server.source.lines import WARN_BITS

# 질문·답변 중 손 접촉 변화가 있으면 판정을 보류한다 (사용자 결정, 2026-11-04).
HOLD_ON_CONTACT_EVENTS = True
CONTACT_EVENTS = ("CONTACT_SHIFT", "CONTACT_DIP", "HAND_LOST", "BASELINE_LOST")
KNOWN_WARN_MASK = sum(WARN_BITS)

# 전송 중 빠진 샘플 비율이 이보다 크면 보류한다. 한두 개 빠진 것은 평균에 영향이 거의 없어 경고로 둔다.
# 임시값 (2026-11-04). 실측 4개 로그에는 빠진 샘플이 없었다.
MAX_LOST_RATIO = 0.02
QR_PHASES = ("QUESTION", "RESPONSE")


@dataclass
class Quality:
    ok: bool
    hold_code: Optional[str] = None
    flags: List[str] = field(default_factory=list)      # 보류 사유를 포함한 품질 문제 전체
    warnings: List[str] = field(default_factory=list)   # 판정을 막지 않는 경고
    raw: dict = field(default_factory=dict)             # 원시 샘플 검사 수치 (기록용)

    def as_dict(self) -> dict:
        return {"ok": self.ok, "hold_code": self.hold_code, "flags": list(self.flags),
                "warnings": list(self.warnings)}


def _question_response_window(events):
    """QUESTION START ~ RESPONSE END의 기기 시각. RESPONSE END가 없으면 끝을 열어 둔다."""
    start = next((e.esp_time_ms for e in events if e.name == "QUESTION" and e.detail == "START"), None)
    end = next((e.esp_time_ms for e in events if e.name == "RESPONSE" and e.detail == "END"), None)
    return start, end


def contact_events_in_window(events) -> List[str]:
    start, end = _question_response_window(events)
    if start is None:
        return []
    return [e.name for e in events
            if e.name in CONTACT_EVENTS and e.esp_time_ms >= start and (end is None or e.esp_time_ms <= end)]


def raw_sample_check(trial):
    """collector의 check_segment 결과를 보류 코드와 경고로 나눈다. (holds, warnings, raw 수치)"""
    rep = trial.report()
    issues = [x for x in rep["issues"].split(";") if x]
    has = lambda prefix: any(x.startswith(prefix) for x in issues)
    qr = [s for s in trial.samples if s.phase in QR_PHASES]
    qr_contact0 = sum(1 for s in qr if s.contact == 0)
    qr_fsr_sat = sum(1 for s in qr if s.fsr >= 4095)
    qr_gsr_sat = sum(1 for s in qr if s.gsr <= 0 or s.gsr >= 4095)
    n, lost = rep["n_samples"], rep["seq_missing"]
    lost_ratio = lost / (n + lost) if n + lost else 0.0
    holds, warns = [], []

    if n == 0:
        holds.append("RAW_NO_SAMPLES")
    if rep["dup_ts"] or rep["backwards_ts"]:
        holds.append("RAW_TIME_ERROR")
    if lost:
        if lost_ratio > MAX_LOST_RATIO:
            holds.append("RAW_SAMPLES_LOST")
        else:
            warns.append(f"SAMPLES_LOST={lost}")
    if has("RESPONSE_FEW_SAMPLES"):
        holds.append("RAW_FEW_SAMPLES")
    if n > 0 and not rep["phase_order_ok"]:
        holds.append("RAW_PHASE_ORDER")
    if qr_contact0:
        holds.append("RAW_NO_CONTACT")
    elif rep["contact0"]:
        warns.append(f"READY_NO_CONTACT={rep['contact0']}")
    if qr_fsr_sat:
        holds.append("RAW_FSR_SAT")
    elif rep["fsr_sat"]:
        warns.append(f"READY_FSR_SAT={rep['fsr_sat']}")
    if qr_gsr_sat:
        holds.append("RAW_GSR_SAT")
    elif rep["gsr_sat"]:
        warns.append(f"READY_GSR_SAT={rep['gsr_sat']}")
    if rep["time_gaps"]:
        warns.append(f"TIME_GAPS={rep['time_gaps']}(max {rep['max_gap_ms']}ms)")
    if has("RATE="):
        warns.append(f"RATE={rep['rate_hz']}Hz")
    if has("RESPONSE_DURATION"):
        warns.append(f"RESPONSE_DURATION={rep.get('response_ms_event')}ms")
    if rep["phase_mismatch"]:
        warns.append(f"PHASE_MISMATCH={rep['phase_mismatch']}")

    raw = {"n_samples": n, "n_question": rep.get("n_question"), "n_response": rep.get("n_response"),
           "rate_hz": rep["rate_hz"], "time_gaps": rep["time_gaps"], "max_gap_ms": rep["max_gap_ms"],
           "seq_missing": lost, "dup_ts": rep["dup_ts"], "backwards_ts": rep["backwards_ts"],
           "contact0": rep["contact0"], "qr_contact0": qr_contact0,
           "fsr_sat": rep["fsr_sat"], "qr_fsr_sat": qr_fsr_sat, "gsr_sat": rep["gsr_sat"], "qr_gsr_sat": qr_gsr_sat,
           "integrity": rep["integrity"], "issues": rep["issues"]}
    return holds, warns, raw


def assess(trial, feature_problems: List[str], hold_on_contact: bool = None) -> Quality:
    """trial: server.pipeline.tracker.TrialSegment, feature_problems: FeatureResult.problems."""
    if hold_on_contact is None:
        hold_on_contact = HOLD_ON_CONTACT_EVENTS
    flags, warnings = [], []
    sm = trial.summary

    if getattr(trial, "status", "") == "INTERRUPTED":
        flags.append("DEVICE_RESET")
    if sm is None:
        flags.append("NO_SUMMARY")
    elif not sm.valid:
        flags.append(f"DEVICE_INVALID:{sm.reason or 'UNKNOWN'}")
    raw_holds, raw_warns, raw = raw_sample_check(trial)
    flags += raw_holds
    warnings += raw_warns
    flags += list(feature_problems)

    contacts = contact_events_in_window(trial.events)
    if contacts and hold_on_contact:
        flags.append(f"CONTACT_EVENT:{contacts[0]}")
    elif contacts:
        warnings.append("CONTACT_EVENTS=" + ",".join(contacts))

    if sm is not None:
        if sm.warn_names:
            warnings.append(f"DEVICE_WARN={sm.warn_names}")
        unknown = sm.warn_flags & ~KNOWN_WARN_MASK
        if unknown:
            warnings.append(f"UNKNOWN_WARN_BITS={unknown}")

    # 중복 제거 (순서 유지)
    flags = list(dict.fromkeys(flags))
    return Quality(ok=not flags, hold_code=flags[0] if flags else None, flags=flags, warnings=warnings, raw=raw)
