"""시리얼 입력원: ESP32(KITEL_MAIN_V5)가 COM 포트로 보내는 줄을 읽는다 (읽기 전용).

- 기기로는 아무것도 쓰지 않는다. write()를 부르지 않는다.
- 포트를 열기 전에 DTR/RTS를 꺼 둔다. ESP32 개발 보드는 포트를 열 때 DTR/RTS 변화로 재부팅되는데,
  이를 막기 위해서다. (collector/record_session.py 1116-1120행은 반대로 DTR을 껐다 켜서 일부러 재부팅해
  #BOOT와 헤더 줄을 받는다. 서버는 재부팅을 피하므로 헤더가 오지 않을 수 있고, 이때는 줄 해석기의
  fallback_v5 헤더 규칙을 그대로 쓴다.)
  그래도 재부팅되면 기기가 #BOOT를 보내고, 진행 추적기가 진행 중인 시도를 INTERRUPTED로 닫는다.
- 줄 나누기는 record_session.py 1146-1151행과 같다: read(in_waiting or 1), b"\\n"으로 나누고
  utf-8(errors=replace)로 읽은 뒤 strip().
- 포트가 없거나 끊기면 상태(SourceStatus)를 알리고 retry_sec(1~2초) 뒤 다시 연다.
- 통신 속도 기본값 115200은 collector/record_session.py main()의 --baud 기본값(1201행)이다.
"""

import time
from dataclasses import dataclass
from typing import Callable, Iterator, Optional, Union

DEFAULT_BAUD = 115200        # collector/record_session.py 1201행: p.add_argument("--baud", type=int, default=115200)
READ_TIMEOUT = 0.1           # collector와 같은 읽기 시간 제한 (record_session.py 1116행)
RETRY_RANGE = (1.0, 2.0)


@dataclass
class SourceStatus:
    """입력원 상태 알림. 줄(str)과 섞여서 lines()에서 나온다.

    kind:
        open          포트를 열었다 (아직 기기 줄은 받지 않음)
        receiving     기기 줄을 받기 시작했다 (live)
        idle          idle_sec 동안 줄이 없다 (포트는 열려 있음)
        disconnected  열려 있던 포트가 끊겼다
        open_failed   포트를 열 수 없다 (없는 포트, 다른 프로그램이 사용 중 등)
    """
    kind: str
    port: str
    message: str = ""


def open_serial(port: str, baud: int, timeout: float = READ_TIMEOUT):
    """pyserial로 포트를 연다. 열기 전에 DTR/RTS를 꺼서 ESP32 재부팅을 막는다. 기기로 쓰지 않는다."""
    import serial  # 지연 임포트 (테스트는 가짜 opener를 쓴다)
    ser = serial.Serial()
    ser.port = port
    ser.baudrate = baud
    ser.timeout = timeout
    ser.dtr = False
    ser.rts = False
    ser.open()
    return ser


class SerialSource:
    source = "hardware"
    name = "serial"

    def __init__(self, port: str, baud: int = DEFAULT_BAUD, retry_sec: float = 1.5, idle_sec: float = 5.0,
                 sleep: Callable[[float], object] = time.sleep, stop=None,
                 opener: Callable = open_serial, clock: Callable[[], float] = time.monotonic):
        if not port:
            raise ValueError("SERIAL_PORT가 필요합니다 (예: COM6). 포트 목록: python -m server.cli ports")
        lo, hi = RETRY_RANGE
        if not lo <= retry_sec <= hi:
            raise ValueError(f"재연결 간격은 {lo}~{hi}초여야 합니다: {retry_sec}")
        self.port, self.baud, self.retry_sec, self.idle_sec = port, baud, retry_sec, idle_sec
        self.sleep, self.stop, self.opener, self.clock = sleep, stop, opener, clock

    def _stopped(self) -> bool:
        return self.stop is not None and self.stop.is_set()

    def lines(self) -> Iterator[Union[str, SourceStatus]]:
        """기기 줄(str)과 상태 알림(SourceStatus)을 차례로 내보낸다. stop이 켜질 때까지 계속한다."""
        last_fail: Optional[str] = None
        while not self._stopped():
            try:
                ser = self.opener(self.port, self.baud, READ_TIMEOUT)
            except Exception as e:  # SerialException, OSError, ValueError 등
                msg = f"포트 {self.port}를 열 수 없습니다: {e}"
                if msg != last_fail:   # 같은 실패를 재시도마다 반복해서 알리지 않는다
                    yield SourceStatus("open_failed", self.port, msg)
                    last_fail = msg
                self.sleep(self.retry_sec)
                continue
            last_fail = None
            yield SourceStatus("open", self.port, f"포트 {self.port}를 열었습니다 ({self.baud} bps).")
            yield from self._read_loop(ser)
            try:
                ser.close()
            except Exception:
                pass
            if not self._stopped():
                self.sleep(self.retry_sec)

    def _read_loop(self, ser):
        buf = b""
        receiving = False
        last_data = self.clock()
        while not self._stopped():
            try:
                chunk = ser.read(ser.in_waiting or 1)
            except Exception as e:
                yield SourceStatus("disconnected", self.port,
                                   f"기기 연결이 끊겼습니다 ({self.port}): {e}. {self.retry_sec:g}초 뒤 다시 연결합니다.")
                return
            now = self.clock()
            if chunk:
                last_data = now
                buf += chunk
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    if not receiving:
                        receiving = True
                        yield SourceStatus("receiving", self.port, "기기 신호를 받고 있습니다.")
                    yield line
            elif receiving and self.idle_sec > 0 and now - last_data >= self.idle_sec:
                receiving = False
                yield SourceStatus("idle", self.port, f"{self.idle_sec:g}초 동안 기기 신호가 없습니다.")


def list_ports():
    """연결된 COM 포트 목록 [(장치, 설명, 하드웨어 ID)]. 포트를 열지 않는다."""
    from serial.tools import list_ports as lp
    return [(p.device, p.description, p.hwid) for p in sorted(lp.comports(), key=lambda p: p.device)]
