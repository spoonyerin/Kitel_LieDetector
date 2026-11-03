"""품질 검사 (S3 최소판). 판정하지 말아야 할 시도를 골라 보류 사유 코드를 붙인다.

보류 사유 코드 (앞에서부터 먼저 확인한다):
    NO_SUMMARY               기기 요약을 받지 못함
    DEVICE_INVALID:<사유>     기기가 valid=0으로 보냄 (예: HAND_OFF_LONG, HAND_LIFTED)
    Q_TOO_SHORT:<샘플 수>     QUESTION 구간이 너무 짧아 국소 dz의 기준을 만들 수 없음
    NO_RESPONSE / BL_SD_MISSING:<센서>   특징 계산에 필요한 값이 없음
    CONTACT_EVENT:<이벤트>    QUESTION~RESPONSE 구간에 손 접촉 변화 이벤트가 있음 (HOLD_ON_CONTACT_EVENTS)
경고(warnings)는 판정을 막지 않고 기록만 한다. 기기 경고 비트와 알 수 없는 비트가 여기에 들어간다.
시간 간격, 포화, 결측 등 원시 샘플 검사는 S6에서 추가한다.
"""

from dataclasses import dataclass, field
from typing import List, Optional

from server.source.lines import WARN_BITS

# 질문·답변 중 손 접촉 변화가 있으면 판정을 보류한다 (사용자 결정, 2026-11-04).
HOLD_ON_CONTACT_EVENTS = True
CONTACT_EVENTS = ("CONTACT_SHIFT", "CONTACT_DIP", "HAND_LOST", "BASELINE_LOST")
KNOWN_WARN_MASK = sum(WARN_BITS)


@dataclass
class Quality:
    ok: bool
    hold_code: Optional[str] = None
    flags: List[str] = field(default_factory=list)      # 보류 사유를 포함한 품질 문제 전체
    warnings: List[str] = field(default_factory=list)   # 판정을 막지 않는 경고

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


def assess(trial, feature_problems: List[str], hold_on_contact: bool = None) -> Quality:
    """trial: server.pipeline.tracker.TrialSegment, feature_problems: FeatureResult.problems."""
    if hold_on_contact is None:
        hold_on_contact = HOLD_ON_CONTACT_EVENTS
    flags, warnings = [], []
    sm = trial.summary

    if sm is None:
        flags.append("NO_SUMMARY")
    elif not sm.valid:
        flags.append(f"DEVICE_INVALID:{sm.reason or 'UNKNOWN'}")
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
    return Quality(ok=not flags, hold_code=flags[0] if flags else None, flags=flags, warnings=warnings)
