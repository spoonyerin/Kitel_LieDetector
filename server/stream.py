"""그래프용 샘플 묶음(sample_batch)과 기준 측정 결과(baseline_result) 메시지.

sample_batch는 과하게 보내지 않도록 세 가지로 제한한다.
- 솎기(decimate): N개 중 1개만 보낸다 (기본 2 → 50Hz 기기 샘플을 25Hz로).
- 묶음 간격(batch_sec, 0.2~0.5초): 실제 시계로 이 간격이 지나야 한 번 보낸다.
- 묶음 최대 개수(max_samples): 고배속 재생처럼 샘플이 몰리면 오래된 것부터 버리고 dropped에 센다.
메시지는 열 단위 배열로 보낸다 (키를 샘플마다 반복하지 않아 크기가 작다).
"""

import math
import time
from collections import deque
from typing import Callable, Optional

from server.pipeline.features import SD_FLOORS

BATCH_SEC_RANGE = (0.2, 0.5)
SAMPLE_COLUMNS = ("time_ms", "fsr", "gsr", "ir", "red", "phase", "trial", "seq", "contact")


class SampleBatcher:
    def __init__(self, source: str, batch_sec: float = 0.25, decimate: int = 2, max_samples: int = 100,
                 clock: Callable[[], float] = time.monotonic):
        lo, hi = BATCH_SEC_RANGE
        if not lo <= batch_sec <= hi:
            raise ValueError(f"묶음 간격은 {lo}~{hi}초여야 한다: {batch_sec}")
        if decimate < 1 or max_samples < 1:
            raise ValueError("decimate와 max_samples는 1 이상이어야 한다")
        self.source = source
        self.batch_sec = batch_sec
        self.decimate = decimate
        self.clock = clock
        self.buf = deque(maxlen=max_samples)
        self.dropped = 0
        self.count = 0
        self.last_flush = clock()

    def add(self, sample) -> Optional[dict]:
        """샘플 하나를 넣는다. 보낼 때가 되면 sample_batch 메시지를 돌려준다."""
        self.count += 1
        if (self.count - 1) % self.decimate == 0:
            if len(self.buf) == self.buf.maxlen:
                self.dropped += 1
            self.buf.append(sample)
        if self.clock() - self.last_flush >= self.batch_sec:
            return self.flush()
        return None

    def flush(self) -> Optional[dict]:
        self.last_flush = self.clock()
        if not self.buf:
            return None
        rows = list(self.buf)
        self.buf.clear()
        msg = {"type": "sample_batch", "source": self.source, "decimate": self.decimate,
               "dropped": self.dropped, "n": len(rows),
               "esp_session": rows[-1].session, "baseline_id": rows[-1].baseline_id}
        for col in SAMPLE_COLUMNS:
            msg[col] = [getattr(s, col) for s in rows]
        self.dropped = 0
        return msg


def _num(v):
    if isinstance(v, float) and math.isnan(v):
        return None
    return v if isinstance(v, (int, float)) else None


def baseline_message(segment, source: str) -> dict:
    """BaselineDone의 구간을 baseline_result 메시지로 바꾼다."""
    sm = segment.summary
    v = sm.values if sm else {}
    sds = {"fsr": _num(v.get("fsr_sd")), "gsr": _num(v.get("gsr_sd"))}
    return {
        "type": "baseline_result",
        "source": source,
        "session_id": f"S{segment.session_index:02d}",
        "esp_session": segment.esp_session,
        "baseline_id": segment.baseline_id,
        "status": segment.status,
        "reason": segment.reason,
        "valid": bool(sm and sm.valid),
        "n": _num(v.get("n")),
        "n_samples": len(segment.samples),
        "fsr": {"mean": _num(v.get("fsr_mean")), "sd": sds["fsr"]},
        "gsr": {"mean": _num(v.get("gsr_mean")), "sd": sds["gsr"]},
        "ir": {"mean": _num(v.get("ir_mean"))},
        "quality": {
            "ok": segment.status == "COMPLETE",
            "warnings": [w for w in [sm.warn_names if sm else ""] if w],
            # 국소 dz 계산 때 표준편차 하한이 적용되는 채널
            "sd_floor_applied": {k: (sds[k] is not None and sds[k] < SD_FLOORS[k]) for k in sds},
        },
    }
