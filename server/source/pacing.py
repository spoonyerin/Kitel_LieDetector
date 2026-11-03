"""입력원의 줄을 시간 간격에 맞춰 내보내는 도우미.

입력원은 (시각 초, 줄) 쌍을 만든다. paced()는 이웃한 시각의 차이만큼 기다린 뒤 줄을 내보낸다.
- speed: 1이면 실제 속도, 4면 4배 빠르게, 0 이하면 기다리지 않는다.
- max_wait: 한 번에 기다리는 최대 초. 세션 사이의 긴 공백(수 분)을 줄인다.
- 시각이 거꾸로 가면(기기 재부팅 등) 기다리지 않는다.
"""

import time
from typing import Callable, Iterable, Iterator, Optional, Tuple

Item = Tuple[Optional[float], str]


def paced(items: Iterable[Item], speed: float = 1.0, max_wait: float = 2.0,
          sleep: Callable[[float], None] = time.sleep) -> Iterator[str]:
    prev = None
    for t, line in items:
        if speed > 0 and t is not None and prev is not None:
            wait = (t - prev) / speed
            if wait > 0:
                sleep(min(wait, max_wait))
        if t is not None:
            prev = t
        yield line
