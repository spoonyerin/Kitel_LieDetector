"""TruthPulse 서버: FastAPI + WebSocket(/ws), web/dist 정적 서빙.

실행:
    .venv\\Scripts\\python.exe -m uvicorn server.app:app --port 8000
    (입력원은 환경 변수 SOURCE=dummy|replay, 자세한 설정은 server/config.py)

흐름: 입력원(별도 스레드) → Tracker → judge_trial → 메시지 → /ws 접속자 전원에게 방송
- 서버와 웹은 보기 전용이다. 웹에서 오는 메시지로 기기나 입력원을 제어하지 않는다.
- 재생 입력원은 data/real을 읽기만 한다. 서버는 파일을 쓰지 않는다.
- 새 접속자에게는 현재 입력원 상태, 마지막 오류, 기준 측정, 마지막 단계, 최근 샘플,
  지금까지의 시도 결과를 먼저 보낸다
  (새로고침해도 화면이 비지 않게).

서버 → 웹 메시지 (docs/development.md 5.3절):
    device_status  입력원 이름과 종류(재생/합성/실시간), 연결 여부, 끝났는지
    phase_update   기기 단계 변화 또는 단계 안의 사건(손 접촉, 세션 시작/종료, 재부팅)
    sample_batch   그래프용 원시 샘플 묶음 (server/stream.py: 솎기, 0.2~0.5초 간격, 최대 개수 제한)
    baseline_result 기준 측정 결과 (평균, 표준편차, 상태, 하한 적용 여부)
    trial_result   server.result.judge_trial의 결과 그대로 (judge_note, disclaimer 포함)
    error          입력원 오류 (retryable, message)
"""

import asyncio
import json
import logging
import mimetypes
import threading
from collections import deque
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from server.config import WEB_DIST, Settings, load_settings
from server.judges.rule import RuleJudge
from server.pipeline.tracker import (BaselineDone, DeviceRebooted, Notice, PhaseChanged, SessionEnded,
                                     SessionStarted, Tracker, TrialDone)
from server.result import for_web, judge_trial
from server.source.dummy_source import DummyConfig, DummySource
from server.source.lines import Sample
from server.source.replay_source import ReplaySource
from server.source.serial_source import SerialSource, SourceStatus
from server.stream import SampleBatcher, baseline_message

# Windows 레지스트리에 따라 .mjs가 JavaScript로 인식되지 않을 수 있어 직접 지정한다 (모듈 스크립트 필수).
mimetypes.add_type("text/javascript", ".mjs")

SAMPLE_HISTORY_BATCHES = 80   # 새 접속자에게 다시 보낼 최근 샘플 묶음 수 (0.25초 간격이면 약 20초)
logger = logging.getLogger("truthpulse")

# 입력원 종류별 화면 표시용 정보
INPUT_INFO = {
    "replay": {"label": "저장된 로그 재생", "live": False},
    "dummy": {"label": "합성 신호 (더미)", "live": False},
    # serial의 live는 기기 줄을 실제로 받는 동안만 true (SourceStatus receiving/idle/disconnected로 바뀜)
    "serial": {"label": "실시간 기기", "live": False},
}


# ----------------------------------------------------------
# 입력원과 메시지 변환
# ----------------------------------------------------------
def make_source(settings: Settings, sleep, stop: threading.Event = None):
    if settings.source == "replay":
        return ReplaySource(settings.replay_run, speed=settings.replay_speed,
                            max_wait=settings.replay_max_wait, sleep=sleep)
    if settings.source == "serial":
        return SerialSource(settings.serial_port, settings.serial_baud, retry_sec=settings.serial_retry_sec,
                            idle_sec=settings.serial_idle_sec, sleep=sleep, stop=stop)
    cfg = DummyConfig(trials=settings.dummy_trials, patterns=list(settings.dummy_patterns),
                      seed=settings.dummy_seed)
    return DummySource(cfg, speed=settings.dummy_speed, max_wait=2.0, sleep=sleep)


def device_status(settings: Settings, source_label: str, connected: bool, finished: bool = False,
                  live: Optional[bool] = None, message: str = "") -> dict:
    info = INPUT_INFO[settings.source]
    return {"type": "device_status", "input": settings.source, "input_label": info["label"],
            "live": info["live"] if live is None else live, "source": source_label,
            "connected": connected, "finished": finished, "message": message,
            "replay_run": settings.replay_run if settings.source == "replay" else None,
            "port": settings.serial_port if settings.source == "serial" else None}


def status_messages(settings: Settings, st: SourceStatus) -> list:
    """시리얼 입력원 상태 → device_status (+ 오류면 error)."""
    if st.kind == "open":
        return [device_status(settings, "hardware", connected=True, live=False, message=st.message)]
    if st.kind == "receiving":
        return [device_status(settings, "hardware", connected=True, live=True, message=st.message)]
    if st.kind == "idle":
        return [device_status(settings, "hardware", connected=True, live=False, message=st.message)]
    # disconnected, open_failed: 화면에 오류를 보내고 다시 연결을 기다린다
    return [{"type": "error", "retryable": True, "message": st.message},
            device_status(settings, "hardware", connected=False, live=False, message=st.message)]


def _event_msg(name, detail="", trial=0, esp_session=None, esp_time_ms=None):
    return {"type": "phase_update", "phase": None,
            "event": {"name": name, "detail": detail, "trial": trial,
                      "esp_session": esp_session, "esp_time_ms": esp_time_ms}}


def update_to_message(u, source_label: str, judge) -> Optional[dict]:
    """추적기 알림 하나를 웹 메시지로 바꾼다. 보낼 것이 없으면 None."""
    if isinstance(u, PhaseChanged):
        return {"type": "phase_update", "phase": u.phase, "trial": u.trial, "esp_session": u.esp_session,
                "baseline_id": u.baseline_id, "esp_time_ms": u.esp_time_ms, "event": None}
    if isinstance(u, Notice):
        e = u.event
        return _event_msg(e.name, e.detail, e.trial, e.session, e.esp_time_ms)
    if isinstance(u, SessionStarted):
        return _event_msg("SESSION_START", "", 0, u.esp_session)
    if isinstance(u, SessionEnded):
        return _event_msg("SESSION_END", u.reason, 0, u.esp_session)
    if isinstance(u, DeviceRebooted):
        return _event_msg("DEVICE_REBOOT", u.text)
    if isinstance(u, TrialDone):
        res = judge_trial(u.segment, source_label, judge)
        hidden = res.get("server_only", {}).get("warnings")
        if hidden:  # 화면에는 보이지 않는 경고: 서버 로그에만 남긴다
            logger.warning("서버 전용 경고 %s T%02d: %s", res["session_id"], res["trial_id"], ", ".join(hidden))
        return for_web(res)
    if isinstance(u, BaselineDone):
        return baseline_message(u.segment, source_label)
    return None


def with_session(msg: dict, tracker: Tracker) -> dict:
    """단계 메시지에 추적기의 현재 세션 번호를 붙인다.

    기기는 세션 시작 전과 끝난 뒤의 PLACE_HAND 단계를 세션 번호 0으로 보낸다. 화면에는 기기 번호 대신
    추적기의 세션 순번(trial_result의 session_id S01, S02와 같은 번호)을 보여 준다.
    """
    if msg.get("type") == "phase_update":
        s = tracker.sess
        msg["session_index"] = s.index if s else None
        msg["session_active"] = bool(s and s.active)
    return msg


def run_pipeline(settings: Settings, emit, stop: threading.Event, source=None) -> None:
    """별도 스레드에서 돈다. emit(msg)로 메시지를 넘긴다. stop이 켜지면 바로 멈춘다.

    source를 주면 그 입력원을 쓴다 (테스트에서 가짜 시리얼 입력원을 넣을 때).
    """
    try:
        source = source or make_source(settings, sleep=stop.wait, stop=stop)
        judge = RuleJudge()
        tracker = Tracker(source.source)
        batcher = SampleBatcher(source.source, batch_sec=settings.sample_batch_sec,
                                decimate=settings.sample_decimate, max_samples=settings.sample_max_per_batch)
        # 시리얼은 포트를 열기 전이므로 연결 안 됨으로 시작한다
        emit(device_status(settings, source.source, connected=settings.source != "serial"))

        def handle(updates):
            for u in updates:
                msg = update_to_message(u, source.source, judge)
                if msg is not None:
                    emit(with_session(msg, tracker))

        for line in source.lines():
            if stop.is_set():
                return
            if isinstance(line, SourceStatus):   # 시리얼 입력원의 연결 상태
                if line.kind in ("disconnected", "open_failed"):
                    last = batcher.flush()
                    if last:
                        emit(last)
                for msg in status_messages(settings, line):
                    emit(msg)
                continue
            item = tracker.parser.feed(line)   # 추적기와 같은 해석기를 쓴다
            if item is None:
                continue
            if isinstance(item, Sample):
                batch = batcher.add(item)
                if batch:
                    emit(batch)
            handle(tracker.feed(item))
        if stop.is_set():   # 서버 종료로 멈춘 경우: 끝 처리 없이 나간다
            return
        last = batcher.flush()
        if last:
            emit(last)
        handle(tracker.close())
        emit(device_status(settings, source.source, connected=False, finished=True))
    except Exception as e:  # 입력원 오류는 화면에 알리고 스레드를 끝낸다
        emit({"type": "error", "retryable": False, "message": f"입력원 오류: {e}"})
        label = source.source if source else ("dummy" if settings.source == "dummy" else "hardware")
        emit(device_status(settings, label, connected=False, finished=True))


# ----------------------------------------------------------
# 방송
# ----------------------------------------------------------
def dumps(msg: dict) -> str:
    """nan이 섞이면 브라우저가 JSON을 읽지 못하므로 allow_nan=False로 직렬화한다."""
    return json.dumps(msg, ensure_ascii=False, allow_nan=False)


class Hub:
    """접속자 목록과 새 접속자에게 보낼 상태를 들고 있다. 이벤트 루프 안에서만 쓴다."""

    def __init__(self, history_limit: int):
        self.clients = set()
        self.history = []          # trial_result 목록
        self.status = None         # 마지막 device_status
        self.phase = None          # 마지막 단계 phase_update (event가 아닌 것)
        self.error = None          # 마지막 error (입력원이 실패한 뒤 접속해도 이유를 볼 수 있게)
        self.baseline = None       # 마지막 baseline_result
        self.samples = deque(maxlen=SAMPLE_HISTORY_BATCHES)   # 최근 sample_batch (그래프가 비지 않게)
        self.history_limit = history_limit
        self.lock = asyncio.Lock()

    def _remember(self, msg):
        t = msg.get("type")
        if t == "device_status":
            self.status = msg
            if msg.get("connected") and msg.get("input") == "serial":
                self.error = None   # 다시 연결되면 지난 연결 오류는 새 접속자에게 보내지 않는다
        elif t == "phase_update" and msg.get("phase"):
            self.phase = msg
        elif t == "trial_result":
            self.history.append(msg)
            del self.history[:-self.history_limit]
        elif t == "error":
            self.error = msg
        elif t == "baseline_result":
            self.baseline = msg
        elif t == "sample_batch":
            self.samples.append(msg)

    async def publish(self, msg: dict):
        async with self.lock:
            self._remember(msg)
            text = dumps(msg)
            for ws in list(self.clients):
                try:
                    await ws.send_text(text)
                except Exception:
                    self.clients.discard(ws)

    async def join(self, ws: WebSocket):
        """새 접속자에게 상태, 마지막 오류, 기준 측정, 마지막 단계, 최근 샘플, 지금까지의 시도 결과를
        보낸 뒤 방송 대상에 넣는다."""
        async with self.lock:
            for msg in [self.status, self.error, self.baseline, self.phase, *self.samples, *self.history]:
                if msg is not None:
                    await ws.send_text(dumps(msg))
            self.clients.add(ws)

    def leave(self, ws: WebSocket):
        self.clients.discard(ws)


# ----------------------------------------------------------
# 앱
# ----------------------------------------------------------
def create_app(settings: Settings = None) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        stop = threading.Event()
        hub = Hub(settings.history_limit)
        app.state.hub = hub
        app.state.settings = settings

        def emit(msg):  # 입력원 스레드 → 이벤트 루프
            loop.call_soon_threadsafe(queue.put_nowait, msg)

        async def pump():
            while True:
                msg = await queue.get()
                try:
                    await hub.publish(msg)
                except Exception as e:  # 메시지 하나가 잘못돼도 방송은 계속한다
                    await hub.publish({"type": "error", "retryable": True,
                                       "message": f"메시지 전송 실패 ({msg.get('type')}): {e}"})

        pump_task = asyncio.create_task(pump())
        worker = threading.Thread(target=run_pipeline, args=(settings, emit, stop),
                                  name="truthpulse-source", daemon=True)
        worker.start()
        try:
            yield
        finally:
            stop.set()
            await asyncio.to_thread(worker.join, 5)
            pump_task.cancel()

    app = FastAPI(title="TruthPulse", lifespan=lifespan)

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        await ws.accept()
        hub: Hub = app.state.hub
        await hub.join(ws)
        try:
            while True:
                await ws.receive_text()   # 보기 전용: 받은 메시지는 쓰지 않는다
        except WebSocketDisconnect:
            pass
        finally:
            hub.leave(ws)

    # 정적 화면은 마지막에 붙인다 (/ws가 먼저 잡히도록)
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
    return app


app = create_app()
