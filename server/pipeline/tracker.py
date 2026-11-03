"""진행 추적기: 해석된 줄을 받아 세션, 기준 측정, 시도 단위로 모은다.

collector/record_session.py의 Run과 같은 규칙으로 묶고 상태를 정한다 (파일은 쓰지 않는다).
    세션 시작/종료          : on_event의 SESSION START / SESSION END:<사유>, #BOOT → ESP32_RESET
    샘플 묶기              : on_sample (활성 세션의 같은 세션 번호만, trial 0 = 기준 측정)
    이벤트 묶기            : on_event (시도 → 현재 기준 측정 순서)
    시도 열기              : READY START (trial > 0), 진행 중인 시도가 있으면 INCOMPLETE로 닫는다
    시도 끝               : RESULT START → valid=1이면 COMPLETE, 아니면 ABORTED / ABORTED START → ABORTED
    세션 종료 시 남은 것   : end_session (ESP32_RESET이면 INTERRUPTED)
    요약 붙이기            : on_summary / on_baseline_summary
정답 라벨은 다루지 않는다. 질문 계획(protocol)과 라벨은 이 모듈에 들어오지 않는다.
"""

from dataclasses import dataclass, field
from typing import List, Optional

from server.source.lines import (BASELINE_PHASES, TRIAL_PHASES, Bad, BaselineSummary, Boot, Event,
                                 LineParser, Sample, TrialSummary, check_segment)

# 웹 단계 표시에 쓰는 기기 단계 이벤트 (START일 때 단계가 바뀐다)
PHASE_EVENTS = ("PLACE_HAND", "BASELINE", "READY", "QUESTION", "RESPONSE",
                "ANALYZING", "RESULT", "ABORTED", "SESSION_END")
# 웹에 알릴 접촉/무효 관련 이벤트
NOTICE_EVENTS = ("HAND_OK", "HAND_LOST", "CONTACT_DIP", "CONTACT_SHIFT", "CONTACT_BACK",
                 "INVALID", "BASELINE_LOST", "PPG_STALL", "PPG_RECOVERED")


# ----------------------------------------------------------
# 구간
# ----------------------------------------------------------
def _events_for_check(events):
    """check_segment가 받는 이벤트 형식 (pc_time, esp_time_ms, name, detail)."""
    return [(None, e.esp_time_ms, e.name, e.detail) for e in events]


@dataclass
class BaselineSegment:
    session_index: int
    esp_session: int
    baseline_id: int
    samples: List[Sample] = field(default_factory=list)
    events: List[Event] = field(default_factory=list)
    summary: Optional[BaselineSummary] = None
    status: str = "RUNNING"
    reason: str = ""

    def report(self) -> dict:
        """collector의 무결성 검사(check_segment)를 그대로 쓴다."""
        return check_segment([s.as_collector_tuple() for s in self.samples],
                             _events_for_check(self.events), BASELINE_PHASES)


@dataclass
class TrialSegment:
    session_index: int
    esp_session: int
    trial: int
    baseline_id: int
    samples: List[Sample] = field(default_factory=list)
    events: List[Event] = field(default_factory=list)
    summary: Optional[TrialSummary] = None
    status: str = "RUNNING"
    reason: str = ""

    @property
    def valid(self) -> bool:
        return self.summary is not None and self.summary.valid

    def report(self) -> dict:
        return check_segment([s.as_collector_tuple() for s in self.samples],
                             _events_for_check(self.events), TRIAL_PHASES)


@dataclass
class SessionState:
    index: int
    esp_session: int
    active: bool = True
    end_reason: str = ""
    baselines: dict = field(default_factory=dict)
    trials: dict = field(default_factory=dict)
    cur_baseline: Optional[BaselineSegment] = None
    cur_trial: Optional[TrialSegment] = None


# ----------------------------------------------------------
# 추적기가 내보내는 알림
# ----------------------------------------------------------
@dataclass
class SessionStarted:
    session_index: int
    esp_session: int


@dataclass
class SessionEnded:
    session_index: int
    esp_session: int
    reason: str


@dataclass
class PhaseChanged:
    phase: str
    trial: int
    esp_session: int
    baseline_id: int
    esp_time_ms: int


@dataclass
class Notice:
    event: Event


@dataclass
class DeviceRebooted:
    text: str


@dataclass
class BaselineDone:
    segment: BaselineSegment


@dataclass
class TrialDone:
    segment: TrialSegment


# ----------------------------------------------------------
# 추적기
# ----------------------------------------------------------
class Tracker:
    """줄 또는 해석된 항목을 받아 알림 목록을 돌려준다."""

    def __init__(self, source: str):
        if source not in ("dummy", "hardware"):
            raise ValueError("source는 dummy 또는 hardware여야 한다")
        self.source = source
        self.parser = LineParser()
        self.sessions: List[SessionState] = []
        self.sess: Optional[SessionState] = None
        self.phase: Optional[str] = None
        self.ignored_samples = 0   # 활성 세션 밖의 샘플 (collector의 bad_lines 일부)
        self.bad_lines = 0         # 해석 불가 줄

    # ---------- 입력 ----------
    def feed_line(self, line: str) -> list:
        item = self.parser.feed(line)
        if item is None:
            return []
        return self.feed(item)

    def feed(self, item) -> list:
        out = []
        if isinstance(item, Sample):
            self._on_sample(item, out)
        elif isinstance(item, Event):
            self._on_event(item, out)
        elif isinstance(item, TrialSummary):
            self._on_summary(item)
        elif isinstance(item, BaselineSummary):
            self._on_baseline_summary(item, out)
        elif isinstance(item, Boot):
            if self.sess and self.sess.active:
                self._end_session("ESP32_RESET", out)
            out.append(DeviceRebooted(item.text))
        elif isinstance(item, Bad):
            self.bad_lines += 1
        return out

    def close(self) -> list:
        """입력이 끝났을 때 (collector의 Run.close와 같이 PROGRAM_CLOSED로 세션을 닫는다)."""
        out = []
        if self.sess and self.sess.active:
            self._end_session("PROGRAM_CLOSED", out)
        return out

    # ---------- 이벤트 ----------
    def _on_event(self, ev: Event, out):
        name, detail, trial = ev.name, ev.detail, ev.trial
        if name == "SESSION" and detail == "START":
            self._start_session(ev.session, out)

        s = self.sess
        if s and s.active and ev.session == s.esp_session:
            if name == "BASELINE" and detail == "START":
                b = BaselineSegment(s.index, s.esp_session, ev.baseline_id)
                s.baselines[ev.baseline_id] = b
                s.cur_baseline = b
            if name == "READY" and detail == "START" and trial > 0 and trial not in s.trials:
                self._open_trial(trial, ev.baseline_id, out)
            if trial > 0 and trial in s.trials:
                s.trials[trial].events.append(ev)
            elif s.cur_baseline is not None and s.cur_baseline.baseline_id == ev.baseline_id:
                s.cur_baseline.events.append(ev)

        if detail == "START" and name in PHASE_EVENTS:
            self.phase = name
            out.append(PhaseChanged(name, trial, ev.session, ev.baseline_id, ev.esp_time_ms))
        elif name in NOTICE_EVENTS:
            out.append(Notice(ev))

        # 시도 끝 (collector와 같이 세션 번호는 보지 않고 현재 시도 번호만 본다)
        if s and s.cur_trial and s.cur_trial.status == "RUNNING" and trial == s.cur_trial.trial:
            if name == "RESULT" and detail == "START":
                tr = s.cur_trial
                self._finish_trial(tr, "COMPLETE" if tr.valid else "ABORTED",
                                   "" if tr.valid else self._esp_reason(tr), out)
            elif name == "ABORTED" and detail == "START":
                self._finish_trial(s.cur_trial, "ABORTED", self._esp_reason(s.cur_trial), out)

        if name == "SESSION" and detail.startswith("END"):
            self._end_session(detail.split(":", 1)[-1], out)

    @staticmethod
    def _esp_reason(tr: TrialSegment) -> str:
        return tr.summary.reason if tr.summary else ""

    # ---------- 요약 ----------
    def _on_summary(self, sm: TrialSummary):
        s = self.sess
        if s and sm.trial in s.trials:
            s.trials[sm.trial].summary = sm

    def _on_baseline_summary(self, bs: BaselineSummary, out):
        s = self.sess
        if not s:
            return
        b = s.baselines.get(bs.baseline_id)
        if not b:
            return
        b.summary = bs
        b.status = "COMPLETE" if bs.valid else "ABORTED"
        b.reason = "" if bs.valid else bs.reason
        out.append(BaselineDone(b))

    # ---------- 샘플 ----------
    def _on_sample(self, smp: Sample, out):
        s = self.sess
        if not s or not s.active or smp.session != s.esp_session:
            self.ignored_samples += 1
            return
        if smp.trial == 0:
            b = s.baselines.get(smp.baseline_id)
            if b is None:
                b = BaselineSegment(s.index, s.esp_session, smp.baseline_id)
                s.baselines[smp.baseline_id] = b
                s.cur_baseline = b
            b.samples.append(smp)
        else:
            tr = s.trials.get(smp.trial) or self._open_trial(smp.trial, smp.baseline_id, out)
            tr.samples.append(smp)

    # ---------- 세션, 시도 ----------
    def _start_session(self, esp_session, out):
        if self.sess and self.sess.active:
            self._end_session("NEW_SESSION_STARTED", out)
        s = SessionState(index=len(self.sessions) + 1, esp_session=esp_session)
        self.sessions.append(s)
        self.sess = s
        out.append(SessionStarted(s.index, esp_session))

    def _open_trial(self, trial, bid, out) -> TrialSegment:
        s = self.sess
        if s.cur_trial and s.cur_trial.status == "RUNNING":
            self._finish_trial(s.cur_trial, "INCOMPLETE", "NEXT_TRIAL_STARTED", out)
        tr = TrialSegment(s.index, s.esp_session, trial, bid)
        s.trials[trial] = tr
        s.cur_trial = tr
        return tr

    @staticmethod
    def _finish_trial(tr: TrialSegment, status, reason, out):
        tr.status = status
        tr.reason = reason
        out.append(TrialDone(tr))

    def _end_session(self, reason, out):
        s = self.sess
        if not s or not s.active:
            return
        if s.cur_trial and s.cur_trial.status == "RUNNING":
            tr = s.cur_trial
            if tr.valid:
                self._finish_trial(tr, "COMPLETE", "", out)
            else:
                status = "INTERRUPTED" if reason == "ESP32_RESET" else "ABORTED"
                self._finish_trial(tr, status, self._esp_reason(tr) or reason, out)
        for b in s.baselines.values():
            if b.status == "RUNNING":
                b.status = "INTERRUPTED" if reason == "ESP32_RESET" else "ABORTED"
                b.reason = reason
                out.append(BaselineDone(b))
        s.active = False
        s.end_reason = reason
        out.append(SessionEnded(s.index, s.esp_session, reason))
