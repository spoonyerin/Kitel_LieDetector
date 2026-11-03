"""입력원의 줄을 시간 간격에 맞춰 내보내는 도우미.

입력원은 (시각 초, 줄) 쌍을 만든다. paced()는 각 줄의 목표 시각까지 기다린 뒤 줄을 내보낸다.
- speed: 1이면 실제 속도, 4면 4배 빠르게, 0 이하면 기다리지 않는다.
- max_wait: 한 번에 기다리는 최대 초. 세션 사이의 긴 공백(수 분)을 줄인다.
- 시각이 거꾸로 가면(기기 재부팅 등) 기다리지 않고 그 지점을 새 기준으로 삼는다.
- 목표 시각 = 기준 시계 + (줄 시각 - 기준 줄 시각) / speed 로 계산한다.
  Windows에서는 짧은 대기가 약 15ms 단위로 늘어나므로, 줄마다 간격만큼 기다리면 고배속 재생이
  크게 느려진다. 목표 시각 방식은 MIN_SLEEP보다 짧은 대기를 건너뛰어도 오차가 쌓이지 않는다.
"""

import time
from typing import Callable, Iterable, Iterator, Optional, Tuple

Item = Tuple[Optional[float], str]
MIN_SLEEP = 0.005   # 이보다 짧은 대기는 건너뛴다 (다음 줄에서 따라잡는다)


def paced(items: Iterable[Item], speed: float = 1.0, max_wait: float = 2.0,
          sleep: Callable[[float], None] = time.sleep,
          clock: Callable[[], float] = time.monotonic) -> Iterator[str]:
    base_t = base_clock = prev = None
    for t, line in items:
        if speed > 0 and t is not None:
            if prev is not None:
                gap = t - prev
                if gap < 0:                       # 재부팅 등: 기다리지 않고 새 기준
                    base_t = None
                elif gap / speed > max_wait:      # 긴 공백: max_wait만 기다리고 새 기준
                    sleep(max_wait)
                    base_t = None
            if base_t is None:
                base_t, base_clock = t, clock()
            wait = base_clock + (t - base_t) / speed - clock()
            if wait >= MIN_SLEEP:
                sleep(wait)
            prev = t
        yield line
