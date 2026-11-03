"""시리얼 입력원(server/source/serial_source.py) 테스트.

실제 COM 포트를 열지 않는다: 가짜 opener/가짜 시리얼 객체만 쓰고, open_serial 테스트에서는
pyserial의 Serial 클래스를 가짜로 바꿔 끼운다. 기기로 쓰기(write)가 0번인지 확인한다.
"""

import io
import os
import re
import sys
import threading
import time
from contextlib import redirect_stdout

import pytest
import serial

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server.app import run_pipeline  # noqa: E402
from server.config import Settings, load_settings  # noqa: E402
from server.source import serial_source as S  # noqa: E402
from server.source.dummy_source import DummyConfig, dummy_items  # noqa: E402
from server.source.serial_source import SerialSource, SourceStatus, open_serial  # noqa: E402


PAUSE = object()   # FakeSerial chunks 안에서 0.3초 쉼


class FakeSerial:
    """chunks를 차례로 돌려준다. 예외 객체가 있으면 그 자리에서 던진다 (USB 뽑힘 흉내)."""

    def __init__(self, chunks, on_empty=None):
        self.chunks = list(chunks)
        self.on_empty = on_empty
        self.writes = []
        self.closed = False

    @property
    def in_waiting(self):
        c = self.chunks[0] if self.chunks else b""
        return len(c) if isinstance(c, bytes) else 0

    def read(self, n):
        if not self.chunks:
            if self.on_empty:
                self.on_empty()
            return b""
        c = self.chunks.pop(0)
        if isinstance(c, Exception):
            raise c
        if c is PAUSE:            # 기기가 잠시 아무것도 보내지 않는 구간
            time.sleep(0.3)
            return b""
        return c

    def write(self, data):  # 불리면 안 된다
        self.writes.append(data)
        raise AssertionError("기기로 쓰기 시도")

    def close(self):
        self.closed = True


def make_opener(results):
    """results: FakeSerial 또는 예외의 목록. 차례로 반환하거나 던진다. 실제 포트는 열지 않는다."""
    calls = []

    def opener(port, baud, timeout):
        calls.append((port, baud, timeout))
        r = results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r
    opener.calls = calls
    return opener


def run_source(src, limit=10000):
    out = []
    for item in src.lines():
        out.append(item)
        if len(out) >= limit:
            break
    return out


# ---------- 기본값과 설정 ----------
def test_default_baud_comes_from_collector():
    """collector/record_session.py의 --baud 기본값과 같아야 한다 (읽기만 함)."""
    with open(os.path.join(ROOT, "collector", "record_session.py"), encoding="utf-8") as f:
        m = re.search(r'add_argument\("--baud", type=int, default=(\d+)\)', f.read())
    assert m and int(m.group(1)) == S.DEFAULT_BAUD == 115200
    assert load_settings({"SOURCE": "serial", "SERIAL_PORT": "COM6"}).serial_baud == 115200


def test_settings_serial():
    s = load_settings({"SOURCE": "serial", "SERIAL_PORT": "COM6", "SERIAL_BAUD": "9600", "SERIAL_RETRY_SEC": "2"})
    assert (s.source, s.serial_port, s.serial_baud, s.serial_retry_sec, s.serial_idle_sec) == \
        ("serial", "COM6", 9600, 2.0, 5.0)
    with pytest.raises(ValueError, match="SERIAL_PORT"):
        load_settings({"SOURCE": "serial"})
    with pytest.raises(ValueError):
        load_settings({"SOURCE": "serial", "SERIAL_PORT": "COM6", "SERIAL_RETRY_SEC": "5"})


def test_source_rejects_bad_params():
    with pytest.raises(ValueError):
        SerialSource("")
    with pytest.raises(ValueError):
        SerialSource("COM6", retry_sec=0.5)


# ---------- 포트 열기: DTR/RTS를 끄고 연다, 쓰지 않는다 ----------
def test_open_serial_turns_off_dtr_rts_before_open(monkeypatch):
    log = []

    class FakePySerial:
        def __init__(self):
            object.__setattr__(self, "_log", log)

        def __setattr__(self, name, value):
            log.append(("set", name, value))
            object.__setattr__(self, name, value)

        def open(self):
            log.append(("open",))

        def write(self, data):
            log.append(("write", data))

        def setDTR(self, v=True):
            log.append(("setDTR", v))

        def setRTS(self, v=True):
            log.append(("setRTS", v))

    monkeypatch.setattr(serial, "Serial", FakePySerial)   # 실제 포트를 열지 않는다
    ser = open_serial("COM_FAKE", 115200)
    assert isinstance(ser, FakePySerial)
    sets = {e[1]: e[2] for e in log if e[0] == "set"}
    assert sets == {"port": "COM_FAKE", "baudrate": 115200, "timeout": 0.1, "dtr": False, "rts": False}
    i_open = log.index(("open",))
    assert log.index(("set", "dtr", False)) < i_open and log.index(("set", "rts", False)) < i_open
    assert not [e for e in log if e[0] in ("write", "setDTR", "setRTS")]


# ---------- 줄 나누기 ----------
def test_lines_split_like_collector():
    stop = threading.Event()
    fake = FakeSerial([b"#EVENT,1,HAND_OK,0,FSR,0,0\r\n62441,773,22", b"33,105696,90223,BASELINE,0,1,1,3095,1\n",
                       b"\n\n#BOOT,\xff KITEL\n"], on_empty=stop.set)
    src = SerialSource("COMX", stop=stop, sleep=lambda s: None, opener=make_opener([fake]))
    out = run_source(src)
    lines = [x for x in out if isinstance(x, str)]
    assert lines == ["#EVENT,1,HAND_OK,0,FSR,0,0", "62441,773,2233,105696,90223,BASELINE,0,1,1,3095,1",
                     "#BOOT,� KITEL"]
    kinds = [x.kind for x in out if isinstance(x, SourceStatus)]
    assert kinds == ["open", "receiving"]
    assert fake.writes == [] and fake.closed


def test_open_failure_is_reported_once_then_retries():
    stop = threading.Event()
    waits = []
    good = FakeSerial([b"#BOOT,KITEL_MAIN_V5\n"], on_empty=stop.set)
    err = serial.SerialException("could not open port 'COM9': FileNotFoundError")
    opener = make_opener([err, err, good])
    src = SerialSource("COM9", retry_sec=1.5, stop=stop, sleep=waits.append, opener=opener)
    out = run_source(src)
    statuses = [x for x in out if isinstance(x, SourceStatus)]
    assert [s.kind for s in statuses] == ["open_failed", "open", "receiving"]   # 같은 실패는 한 번만
    assert "COM9" in statuses[0].message and waits[:2] == [1.5, 1.5]
    assert len(opener.calls) == 3 and opener.calls[0] == ("COM9", 115200, 0.1)


def test_disconnect_then_reconnect():
    stop = threading.Event()
    first = FakeSerial([b"#EVENT,1,HAND_OK,0,FSR,0,0\n", serial.SerialException("ClearCommError failed")])
    second = FakeSerial([b"#EVENT,2,HAND_LOST,0,BEFORE_START,0,0\n"], on_empty=stop.set)
    src = SerialSource("COM6", stop=stop, sleep=lambda s: None, opener=make_opener([first, second]))
    out = run_source(src)
    kinds = [x.kind if isinstance(x, SourceStatus) else "line" for x in out]
    assert kinds == ["open", "receiving", "line", "disconnected", "open", "receiving", "line"]
    assert first.closed and first.writes == [] and second.writes == []
    msg = next(x for x in out if isinstance(x, SourceStatus) and x.kind == "disconnected").message
    assert "끊겼습니다" in msg and "다시 연결" in msg


def test_idle_turns_live_off():
    stop = threading.Event()
    now = [0.0]

    def tick():  # 줄이 없을 때마다 1초씩 지난다
        now[0] += 1.0
        if now[0] > 8:
            stop.set()
    fake = FakeSerial([b"#EVENT,1,HAND_OK,0,FSR,0,0\n"], on_empty=tick)
    src = SerialSource("COM6", idle_sec=5, stop=stop, sleep=lambda s: None, opener=make_opener([fake]),
                       clock=lambda: now[0])
    kinds = [x.kind for x in run_source(src) if isinstance(x, SourceStatus)]
    assert kinds == ["open", "receiving", "idle"]


# ---------- 서버 파이프라인과 함께 (가짜 시리얼) ----------
def _pipeline(chunks_list, settings=None):
    """가짜 시리얼로 run_pipeline을 돌려 웹 메시지 목록을 돌려준다."""
    settings = settings or Settings(source="serial", serial_port="COM_FAKE")
    stop = threading.Event()
    msgs = []
    results = []
    for chunks in chunks_list:
        results.append(chunks if isinstance(chunks, Exception) else FakeSerial(chunks))
    last = results[-1]
    last.on_empty = stop.set
    src = SerialSource("COM_FAKE", stop=stop, sleep=lambda s: None, opener=make_opener(results))
    run_pipeline(settings, msgs.append, stop, source=src)
    fakes = [r for r in results if isinstance(r, FakeSerial)]
    assert all(f.writes == [] for f in fakes), "기기로 쓰기가 있었다"
    return msgs


def _dummy_bytes(trials, patterns):
    return [(line + "\n").encode() for _, line in dummy_items(DummyConfig(trials=trials, patterns=patterns))]


def test_pipeline_live_flag_and_judgement():
    lines = _dummy_bytes(2, ["calm", "reactive"])
    mid = len(lines) // 2
    # 가짜 시리얼은 순식간에 보내므로, 중간에 0.3초 쉼을 넣어 그래프 묶음(0.25초 간격)이 나가게 한다
    msgs = _pipeline([lines[:mid] + [PAUSE] + lines[mid:]])
    status = [m for m in msgs if m["type"] == "device_status"]
    assert [(s["connected"], s["live"]) for s in status] == [(False, False), (True, False), (True, True)]
    assert all(s["input"] == "serial" and s["source"] == "hardware" and s["port"] == "COM_FAKE" for s in status)
    results = [m for m in msgs if m["type"] == "trial_result"]
    assert [r["decision"] for r in results] == ["truth", "lie"] and all(r["source"] == "hardware" for r in results)
    assert any(m["type"] == "sample_batch" for m in msgs) and any(m["type"] == "baseline_result" for m in msgs)


def test_pipeline_reboot_mid_trial_is_interrupted():
    """기기가 시도 중에 재부팅되면(#BOOT) 진행 중인 시도는 INTERRUPTED, 판정은 보류 (재부팅 사유)."""
    lines = _dummy_bytes(2, ["calm", "calm"])
    cut = next(i for i, b in enumerate(lines) if b.startswith(b"#EVENT") and b",RESPONSE,2,START," in b) + 200
    msgs = _pipeline([lines[:cut] + [b"#BOOT,KITEL_MAIN_V5\n"]])
    results = [m for m in msgs if m["type"] == "trial_result"]
    assert [(r["trial_id"], r["device_status"]) for r in results] == [(1, "COMPLETE"), (2, "INTERRUPTED")]
    held = results[1]
    assert held["decision"] == "inconclusive" and held["hold_code"] == "DEVICE_RESET"
    assert held["hold_reason"] == "답변 중에 기기가 다시 시작돼서 이 답변은 측정에서 제외했어요."
    events = [m["event"]["name"] for m in msgs if m["type"] == "phase_update" and m["event"]]
    assert "DEVICE_REBOOT" in events and "SESSION_END" in events


def test_pipeline_port_missing_then_connects():
    err = serial.SerialException("could not open port 'COM_FAKE': FileNotFoundError")
    msgs = _pipeline([err, [b"#BOOT,KITEL_MAIN_V5\n"]])
    errors = [m for m in msgs if m["type"] == "error"]
    assert len(errors) == 1 and errors[0]["retryable"] is True and "COM_FAKE" in errors[0]["message"]
    i_err = msgs.index(errors[0])
    after = [m for m in msgs[i_err:] if m["type"] == "device_status"]
    assert (after[0]["connected"], after[0]["live"]) == (False, False)
    assert (after[-1]["connected"], after[-1]["live"]) == (True, True)


def test_pipeline_disconnect_sends_error_and_live_false():
    first = [b"#BOOT,KITEL_MAIN_V5\n", serial.SerialException("device disconnected")]
    msgs = _pipeline([first, [b"#EVENT,1,HAND_OK,0,FSR,0,0\n"]])
    kinds = [(m["type"], m.get("connected"), m.get("live")) for m in msgs
             if m["type"] in ("device_status", "error")]
    assert kinds == [("device_status", False, False), ("device_status", True, False), ("device_status", True, True),
                     ("error", None, None), ("device_status", False, False),
                     ("device_status", True, False), ("device_status", True, True)]


# ---------- 포트 목록 명령 ----------
def test_cli_ports_with_fake_list():
    from server.cli import cmd_ports
    buf = io.StringIO()
    with redirect_stdout(buf):
        cmd_ports(None, lister=lambda: [("COM6", "Silicon Labs CP210x USB to UART Bridge (COM6)", "USB VID:PID=10C4:EA60")])
    out = buf.getvalue()
    assert "COM6" in out and "CP210x" in out and 'SERIAL_PORT="COM6"' in out
    buf = io.StringIO()
    with redirect_stdout(buf):
        cmd_ports(None, lister=lambda: [])
    assert "없습니다" in buf.getvalue()


def test_list_ports_does_not_open(monkeypatch):
    """list_ports는 comports()만 부르고 Serial을 만들지 않는다."""
    import serial.tools.list_ports as lp

    class P:
        device, description, hwid = "COM3", "가짜", "X"
    monkeypatch.setattr(lp, "comports", lambda: [P()])
    monkeypatch.setattr(serial, "Serial", lambda *a, **k: (_ for _ in ()).throw(AssertionError("포트를 열었다")))
    assert S.list_ports() == [("COM3", "가짜", "X")]
