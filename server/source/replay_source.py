"""재생 입력원: 수집기가 저장한 raw_serial_log.txt를 다시 내보낸다.

- 파일은 읽기 전용으로만 연다 (server.source.lines.iter_raw_log). data/real에 쓰지 않는다.
- 시간 간격은 로그의 PC 수신 시각으로 재현한다. 기기 시각은 재부팅하면 0으로 돌아가므로 쓰지 않는다.
- 실측 기기 데이터이므로 source는 "hardware"다.
"""

import datetime as dt
import os
import time
from typing import Iterator, Optional

from server.source.lines import iter_raw_log
from server.source.pacing import Item, paced

PC_TIME_FORMAT = "%Y-%m-%d %H:%M:%S.%f"


def resolve_log(path: str) -> str:
    """run 폴더를 받으면 그 안의 raw_serial_log.txt 경로를 돌려준다."""
    if os.path.isdir(path):
        path = os.path.join(path, "raw_serial_log.txt")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"원시 로그가 없습니다: {path}")
    return path


def _pc_seconds(pc: Optional[str]) -> Optional[float]:
    if not pc:
        return None
    try:
        return dt.datetime.strptime(pc, PC_TIME_FORMAT).timestamp()
    except ValueError:
        return None


def replay_items(path: str) -> Iterator[Item]:
    """(PC 수신 시각 초, 기기 줄)을 차례로 돌려준다."""
    for pc, line in iter_raw_log(resolve_log(path)):
        yield _pc_seconds(pc), line


class ReplaySource:
    """저장된 로그 재생. lines()는 기기 줄을 시간 간격에 맞춰 내보낸다."""
    source = "hardware"
    name = "replay"

    def __init__(self, path: str, speed: float = 1.0, max_wait: float = 2.0, sleep=time.sleep):
        self.path = resolve_log(path)
        self.speed = speed
        self.max_wait = max_wait
        self.sleep = sleep

    def lines(self) -> Iterator[str]:
        return paced(replay_items(self.path), self.speed, self.max_wait, self.sleep)
