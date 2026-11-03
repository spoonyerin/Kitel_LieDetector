"""기기(ESP32 KITEL_MAIN_V5) 출력 줄 해석기.

collector/record_session.py의 줄 해석 규칙을 그대로 따른다.
- 상수와 함수는 collector에서 import해서 쓰고 새로 정의하지 않는다.
- record_session.Run은 줄을 해석하면서 폴더와 CSV를 쓰기 때문에 서버에서 직접 부르지 않는다.
  대신 아래 규칙을 같은 순서와 같은 필드 위치로 옮겨 왔다.
    줄 판별 순서   : Run.handle_line          (record_session.py 542-581행)
    이벤트 필드    : Run.on_event             (584-594행)
    요약 헤더 폴백 : Run.on_summary / on_baseline_summary (649-686행)
    샘플 필드      : Run.on_sample            (712-723행)
- 세션 활성 여부처럼 상태에 따른 판단(on_sample의 세션 번호 비교 등)은 해석기가 아니라
  진행 추적기(S2)에서 한다.
- 이 모듈은 파일을 쓰지 않는다. 원시 로그는 읽기 전용으로만 연다.
"""

import os
import sys
from dataclasses import dataclass, field
from typing import Iterator, Optional, Union

# collector 폴더를 import 경로에 추가한다 (collector는 패키지가 아니다).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
COLLECTOR_DIR = os.path.join(_REPO_ROOT, "collector")
if COLLECTOR_DIR not in sys.path:
    sys.path.insert(0, COLLECTOR_DIR)

import record_session as _rs  # noqa: E402  (collector 코드 재사용, 수정 금지)

# collector 상수와 함수를 그대로 다시 내보낸다 (복사하지 않고 같은 객체를 쓴다).
FW_SAMPLE_HEADER = _rs.FW_SAMPLE_HEADER
N_SAMPLE_FIELDS = _rs.N_SAMPLE_FIELDS
SUMMARY_HEADER_V5 = _rs.SUMMARY_HEADER_V5
BASELINE_HEADER_V5 = _rs.BASELINE_HEADER_V5
WARN_BITS = _rs.WARN_BITS
TRIAL_PHASES = _rs.TRIAL_PHASES
BASELINE_PHASES = _rs.BASELINE_PHASES
warn_names = _rs.warn_names
check_segment = _rs.check_segment


def to_number(value: str):
    """요약 값 하나를 숫자로 바꾼다. 정수 → 실수(nan 포함) → 문자열 순서로 시도한다."""
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)  # "nan"은 float('nan')이 된다
    except ValueError:
        return value


# ----------------------------------------------------------
# 해석 결과 형식
# ----------------------------------------------------------
@dataclass
class Sample:
    """50Hz 샘플 한 줄. 필드 위치는 Run.on_sample과 같다."""
    time_ms: int
    fsr: int
    gsr: int
    ir: int
    red: int
    phase: str
    trial: int
    session: int
    baseline_id: int
    seq: int
    contact: int

    def as_collector_tuple(self):
        """record_session.check_segment가 받는 샘플 형식으로 바꾼다."""
        return (self.time_ms, self.fsr, self.gsr, self.ir, self.red,
                self.phase, self.seq, self.contact)


@dataclass
class Event:
    """#EVENT,<esp_time_ms>,<이름>,<trial>,<detail>,<session>,<baseline_id>"""
    esp_time_ms: int
    name: str
    trial: int
    detail: str
    session: int
    baseline_id: int


@dataclass
class _Summary:
    """요약 줄 공통 부분. raw는 기기가 보낸 문자열 그대로, values는 숫자로 바꾼 값."""
    raw: dict
    values: dict
    header_source: str  # "received" (기기가 보낸 헤더) 또는 "fallback_v5"

    @property
    def session(self) -> int:
        return int(self.raw["session"])

    @property
    def baseline_id(self) -> int:
        return int(self.raw["baseline_id"])

    @property
    def valid(self) -> bool:
        # collector와 같은 기준: 문자열 "1"일 때만 유효
        return self.raw.get("valid") == "1"

    @property
    def reason(self) -> str:
        return self.raw.get("reason", "")

    @property
    def warn_flags(self) -> int:
        try:
            return int(self.raw.get("warn_flags", "0"))
        except ValueError:
            return 0

    @property
    def warn_names(self) -> str:
        return warn_names(self.raw.get("warn_flags"))


@dataclass
class TrialSummary(_Summary):
    """#SUMMARY 줄 (시도가 끝날 때 기기가 보내는 요약)."""

    @property
    def trial(self) -> int:
        return int(self.raw["trial"])


@dataclass
class BaselineSummary(_Summary):
    """#BASELINE 줄 (기준 측정이 끝날 때 기기가 보내는 요약)."""


@dataclass
class Header:
    """헤더 줄. kind는 "sample", "summary", "baseline" 중 하나."""
    kind: str
    fields: list
    ok: bool = True  # sample 헤더가 FW_SAMPLE_HEADER와 같은지


@dataclass
class Boot:
    """#BOOT 줄 (ESP32 재부팅)."""
    text: str


@dataclass
class Message:
    """그 밖의 # 줄 (기기 안내 메시지 등)."""
    text: str


@dataclass
class Bad:
    """해석할 수 없는 줄. kind는 어느 종류로 보고 해석하다 실패했는지."""
    kind: str
    line: str
    why: str


Parsed = Union[Sample, Event, TrialSummary, BaselineSummary, Header, Boot, Message, Bad]


# ----------------------------------------------------------
# 해석기
# ----------------------------------------------------------
@dataclass
class LineParser:
    """기기 출력 줄을 하나씩 해석한다. 요약 헤더 상태를 기억한다 (Run과 같은 방식)."""
    summary_header: Optional[list] = None
    baseline_header: Optional[list] = None
    summary_header_source: str = ""
    baseline_header_source: str = ""
    header_ok: Optional[bool] = None
    counts: dict = field(default_factory=dict)

    def _count(self, item):
        key = type(item).__name__
        self.counts[key] = self.counts.get(key, 0) + 1
        return item

    def feed(self, line: str) -> Optional[Parsed]:
        """줄 하나를 해석한다. 빈 줄이면 None을 돌려준다.

        판별 순서는 Run.handle_line과 같다.
        """
        if not line:
            return None
        if line.startswith("#BOOT"):
            return self._count(Boot(line))
        if line.startswith("time_ms,"):
            self.header_ok = (line == FW_SAMPLE_HEADER)
            return self._count(Header("sample", line.split(","), self.header_ok))
        if line.startswith("#SUMMARY_HEADER,"):
            self.summary_header = line.split(",")[1:]
            self.summary_header_source = "received"
            return self._count(Header("summary", self.summary_header))
        if line.startswith("#BASELINE_HEADER,"):
            self.baseline_header = line.split(",")[1:]
            self.baseline_header_source = "received"
            return self._count(Header("baseline", self.baseline_header))
        if line.startswith("#SUMMARY,"):
            return self._count(self._summary(line, trial=True))
        if line.startswith("#BASELINE,"):
            return self._count(self._summary(line, trial=False))
        if line.startswith("#EVENT,"):
            return self._count(self._event(line))
        if line.startswith("#"):
            return self._count(Message(line))
        return self._count(self._sample(line))

    def _summary(self, line, trial):
        """#SUMMARY / #BASELINE. 헤더가 없으면 필드 수가 맞을 때만 V5 헤더로 폴백한다."""
        values = line.split(",")[1:]
        kind = "summary" if trial else "baseline"
        fallback = SUMMARY_HEADER_V5 if trial else BASELINE_HEADER_V5
        hdr = self.summary_header if trial else self.baseline_header
        if not hdr:
            if len(values) != len(fallback):
                return Bad(kind, line, f"헤더 미수신, 필드 수 {len(values)} (예상 {len(fallback)})")
            # Run과 같이 폴백 헤더를 이후에도 계속 쓴다
            hdr = fallback
            if trial:
                self.summary_header, self.summary_header_source = fallback, "fallback_v5"
            else:
                self.baseline_header, self.baseline_header_source = fallback, "fallback_v5"
        if len(values) != len(hdr):
            return Bad(kind, line, f"필드 수 불일치: 값 {len(values)}개, 헤더 {len(hdr)}개")
        raw = dict(zip(hdr, values))
        source = self.summary_header_source if trial else self.baseline_header_source
        cls = TrialSummary if trial else BaselineSummary
        item = cls(raw=raw, values={k: to_number(v) for k, v in raw.items()}, header_source=source)
        try:
            # 키 값이 정수가 아니면 collector도 시도/기준에 붙이지 못한다
            item.session, item.baseline_id
            if trial:
                item.trial
        except (KeyError, ValueError):
            return Bad(kind, line, "session/baseline_id/trial 값이 정수가 아님")
        return item

    @staticmethod
    def _event(line):
        p = line.split(",")
        if len(p) < 7:
            return Bad("event", line, f"필드 수 {len(p)} (최소 7)")
        try:
            t, trial, esp_s, bid = int(p[1]), int(p[3]), int(p[5]), int(p[6])
        except ValueError:
            return Bad("event", line, "정수 필드 해석 실패")
        return Event(esp_time_ms=t, name=p[2], trial=trial, detail=p[4], session=esp_s, baseline_id=bid)

    @staticmethod
    def _sample(line):
        p = line.split(",")
        if len(p) != N_SAMPLE_FIELDS:
            return Bad("sample", line, f"필드 수 {len(p)} (예상 {N_SAMPLE_FIELDS})")
        try:
            return Sample(time_ms=int(p[0]), fsr=int(p[1]), gsr=int(p[2]), ir=int(p[3]), red=int(p[4]),
                          phase=p[5], trial=int(p[6]), session=int(p[7]), baseline_id=int(p[8]),
                          seq=int(p[9]), contact=int(p[10]))
        except ValueError:
            return Bad("sample", line, "정수 필드 해석 실패")


# ----------------------------------------------------------
# 원시 로그 읽기 (읽기 전용)
# ----------------------------------------------------------
def iter_raw_log(path: str) -> Iterator[tuple]:
    """raw_serial_log.txt를 읽기 전용으로 열어 (PC 시각, 기기 줄)을 차례로 돌려준다.

    수집기는 `PC시각<TAB>기기 줄` 형식으로 저장한다. 기기 줄은 수집기와 같이 strip()한다.
    탭이 없는 줄은 PC 시각을 None으로 둔다. 빈 기기 줄은 건너뛴다.
    """
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            raw = raw.rstrip("\r\n")
            if "\t" in raw:
                pc, line = raw.split("\t", 1)
            else:
                pc, line = None, raw
            line = line.strip()
            if line:
                yield pc, line
