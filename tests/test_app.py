"""서버(server/app.py, server/config.py) 테스트.

httpx 없이 테스트한다: 임시 uvicorn을 127.0.0.1의 빈 포트에 띄우고,
websockets 동기 클라이언트로 /ws에, urllib로 정적 화면에 접속한다.
재생 테스트는 data/real을 읽기만 한다 (실측 로그가 없으면 건너뛴다).
"""

import json
import os
import socket
import sys
import threading
import time
import urllib.request
from contextlib import contextmanager

import pytest
import uvicorn
from websockets.sync.client import connect

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server.app import create_app  # noqa: E402
from server.config import DEFAULT_REPLAY_RUN, Settings, load_settings  # noqa: E402

REAL_LOG = os.path.join(DEFAULT_REPLAY_RUN, "raw_serial_log.txt")


# ---------- 설정 ----------
def test_settings_defaults_and_env():
    s = load_settings({})
    assert (s.source, s.judge, s.replay_run) == ("dummy", "rule", DEFAULT_REPLAY_RUN)
    s = load_settings({"SOURCE": "REPLAY", "REPLAY_SPEED": "4", "DUMMY_PATTERNS": "calm, poor",
                       "REPLAY_RUN": "data/real/experiments/run_x"})
    assert s.source == "replay" and s.replay_speed == 4.0 and s.dummy_patterns == ["calm", "poor"]
    assert os.path.isabs(s.replay_run) and s.replay_run.endswith("run_x")


def test_settings_reject_bad_values():
    with pytest.raises(ValueError, match="S5b"):
        load_settings({"SOURCE": "serial"})
    with pytest.raises(ValueError):
        load_settings({"SOURCE": "camera"})
    with pytest.raises(ValueError):
        load_settings({"JUDGE": "logreg"})
    with pytest.raises(ValueError):
        load_settings({"REPLAY_SPEED": "fast"})


# ---------- 임시 서버 ----------
def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def running_server(settings: Settings):
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(settings), host="127.0.0.1", port=port,
                                           log_level="warning", lifespan="on"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started:
        if time.time() > deadline:
            raise RuntimeError("테스트 서버가 시작되지 않았습니다")
        time.sleep(0.02)
    try:
        yield port
    finally:
        server.should_exit = True
        thread.join(10)


def _collect(port, until, timeout=20.0):
    """/ws에서 메시지를 모은다. until(messages)이 참이 되면 멈춘다."""
    msgs = []
    deadline = time.time() + timeout
    with connect(f"ws://127.0.0.1:{port}/ws", open_timeout=5) as ws:
        while not until(msgs):
            left = deadline - time.time()
            if left <= 0:
                raise AssertionError(f"시간 초과. 받은 메시지 종류: {[m['type'] for m in msgs]}")
            msgs.append(json.loads(ws.recv(timeout=left)))
    return msgs


def _finished(msgs):
    return any(m["type"] == "device_status" and m["finished"] for m in msgs)


def _results(msgs):
    return [m for m in msgs if m["type"] == "trial_result"]


def _dummy(speed, trials=3, patterns=("calm", "reactive", "poor")):
    return Settings(source="dummy", dummy_trials=trials, dummy_patterns=list(patterns), dummy_speed=speed)


# ---------- 정적 화면 ----------
def test_serves_web_dist_index():
    with running_server(_dummy(speed=0)) as port:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as r:
            body = r.read().decode("utf-8")
            assert r.status == 200 and "TruthPulse" in body


# ---------- 실시간 방송 ----------
def test_update_to_message_mapping():
    """추적기 알림 → 웹 메시지 변환 (서버 없이)."""
    from server.app import update_to_message
    from server.pipeline.tracker import (DeviceRebooted, Notice, PhaseChanged, SessionEnded,
                                         SessionStarted)
    from server.source.lines import Event
    m = update_to_message(PhaseChanged("RESPONSE", 2, 1, 1, 5000), "dummy", None)
    assert m == {"type": "phase_update", "phase": "RESPONSE", "trial": 2, "esp_session": 1,
                 "baseline_id": 1, "esp_time_ms": 5000, "event": None}
    m = update_to_message(Notice(Event(6000, "CONTACT_SHIFT", 2, "RESPONSE:722", 1, 1)), "dummy", None)
    assert m["phase"] is None and m["event"]["name"] == "CONTACT_SHIFT" and m["event"]["detail"] == "RESPONSE:722"
    assert update_to_message(SessionStarted(1, 3), "dummy", None)["event"]["name"] == "SESSION_START"
    m = update_to_message(SessionEnded(1, 3, "LONG_PRESS"), "dummy", None)
    assert (m["event"]["name"], m["event"]["detail"]) == ("SESSION_END", "LONG_PRESS")
    assert update_to_message(DeviceRebooted("#BOOT,KITEL_MAIN_V5"), "dummy", None)["event"]["name"] == "DEVICE_REBOOT"


def test_live_dummy_broadcast():
    """더미 3시도를 20배속(약 5초)으로 돌리며 단계 변화와 시도 결과가 실시간으로 오는지 본다.

    맨 앞의 SESSION_START 등은 접속 전에 지나갈 수 있으므로, 접속 뒤에 확실히 일어나는
    단계(첫 질문 이후)와 이벤트만 확인한다.
    """
    with running_server(_dummy(speed=20)) as port:
        msgs = _collect(port, lambda m: _finished(m) and len(_results(m)) == 3)
    first = msgs[0]
    assert first["type"] == "device_status" and first["input"] == "dummy"
    assert first["input_label"] == "합성 신호 (더미)" and first["live"] is False and first["source"] == "dummy"
    phases = [m["phase"] for m in msgs if m["type"] == "phase_update" and m["phase"]]
    for p in ("QUESTION", "RESPONSE", "ANALYZING", "RESULT", "ABORTED", "SESSION_END"):
        assert p in phases
    events = [m["event"]["name"] for m in msgs if m["type"] == "phase_update" and m["event"]]
    for e in ("HAND_LOST", "INVALID", "SESSION_END"):
        assert e in events
    # 시도 결과는 실시간으로 왔다 (기록 재전송이 아니라 단계 메시지 사이에 섞여 온다)
    first_result = next(i for i, m in enumerate(msgs) if m["type"] == "trial_result")
    assert any(m["type"] == "phase_update" for m in msgs[first_result:])
    results = _results(msgs)
    assert [r["decision"] for r in results] == ["truth", "lie", "inconclusive"]
    for r in results:
        assert r["source"] == "dummy" and r["score"] is None and r["judge"]["trained"] is False
        assert "라벨 데이터로 맞추거나 검증하지 않았습니다" in r["judge_note"]
        assert r["disclaimer"].startswith("오락용 게임입니다")


def test_new_client_gets_history():
    """입력원이 끝난 뒤 접속해도(새로고침) 상태, 마지막 단계, 지금까지의 시도 결과를 받는다."""
    with running_server(_dummy(speed=0)) as port:
        live = _collect(port, lambda m: _finished(m) and len(_results(m)) == 3)
        late = _collect(port, lambda m: len(_results(m)) == 3, timeout=5)
    assert late[0]["type"] == "device_status" and late[0]["finished"] is True
    assert late[1]["type"] == "phase_update" and late[1]["phase"]
    assert _results(late) == _results(live)


def test_clients_send_messages_are_ignored():
    """보기 전용: 웹에서 보낸 메시지는 무시되고 연결은 유지된다."""
    with running_server(_dummy(speed=0)) as port:
        with connect(f"ws://127.0.0.1:{port}/ws", open_timeout=5) as ws:
            ws.send(json.dumps({"type": "set_mode", "mode": "serial"}))
            got = [json.loads(ws.recv(timeout=5)) for _ in range(2)]
            assert got[0]["type"] == "device_status"


# ---------- 재생 ----------
def test_replay_real_log():
    if not os.path.isfile(REAL_LOG):
        pytest.skip("실측 로그 없음")
    settings = Settings(source="replay", replay_speed=0)
    with running_server(settings) as port:
        msgs = _collect(port, lambda m: _finished(m) and len(_results(m)) == 11)
    status = [m for m in msgs if m["type"] == "device_status"]
    assert all(s["input"] == "replay" and s["input_label"] == "저장된 로그 재생" and s["source"] == "hardware"
               for s in status)
    results = _results(msgs)
    counts = {d: sum(1 for r in results if r["decision"] == d) for d in ("truth", "lie", "inconclusive")}
    assert counts == {"truth": 5, "lie": 2, "inconclusive": 4}   # S3 CLI 결과와 같아야 한다
    assert all(r["source"] == "hardware" for r in results)


def test_bad_replay_path_reports_error(tmp_path):
    settings = Settings(source="replay", replay_run=str(tmp_path / "없는_폴더"), replay_speed=0)
    with running_server(settings) as port:
        msgs = _collect(port, lambda m: _finished(m) and any(x["type"] == "error" for x in m), timeout=5)
    errors = [m for m in msgs if m["type"] == "error"]
    assert errors and "원시 로그가 없습니다" in errors[0]["message"]
