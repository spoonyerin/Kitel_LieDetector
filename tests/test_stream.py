"""그래프 샘플 묶음과 기준 측정 결과 메시지(server/stream.py) 테스트."""

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server.pipeline.tracker import BaselineDone, Tracker  # noqa: E402
from server.source.dummy_source import DummyConfig, dummy_items  # noqa: E402
from server.source.lines import Sample  # noqa: E402
from server.stream import SampleBatcher, baseline_message  # noqa: E402


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def _s(i):
    return Sample(time_ms=1000 + i * 20, fsr=2000 + i, gsr=2200, ir=100000, red=90000, phase="RESPONSE",
                  trial=1, session=1, baseline_id=1, seq=i, contact=1)


def test_rejects_out_of_range_settings():
    for bad in (0.1, 0.6):
        with pytest.raises(ValueError):
            SampleBatcher("dummy", batch_sec=bad)
    with pytest.raises(ValueError):
        SampleBatcher("dummy", decimate=0)


def test_decimate_and_interval():
    clock = FakeClock()
    b = SampleBatcher("hardware", batch_sec=0.25, decimate=2, max_samples=100, clock=clock)
    out = [b.add(_s(i)) for i in range(10)]
    assert all(m is None for m in out)          # 시간이 지나지 않으면 보내지 않는다
    clock.now = 0.25
    msg = b.add(_s(10))
    assert msg["type"] == "sample_batch" and msg["source"] == "hardware"
    assert msg["seq"] == [0, 2, 4, 6, 8, 10] and msg["n"] == 6 and msg["decimate"] == 2
    assert msg["time_ms"][1] - msg["time_ms"][0] == 40 and msg["dropped"] == 0
    assert set(msg) >= {"time_ms", "fsr", "gsr", "ir", "red", "phase", "trial", "seq", "contact"}
    json.dumps(msg, allow_nan=False)
    assert b.flush() is None                     # 비어 있으면 보낼 것이 없다


def test_cap_drops_oldest_and_counts():
    clock = FakeClock()
    b = SampleBatcher("dummy", batch_sec=0.25, decimate=1, max_samples=5, clock=clock)
    for i in range(12):
        b.add(_s(i))
    clock.now = 1.0
    msg = b.flush()
    assert msg["seq"] == [7, 8, 9, 10, 11] and msg["dropped"] == 7
    b.add(_s(12))
    clock.now = 2.0
    assert b.flush()["dropped"] == 0             # 다음 묶음은 다시 0부터


def test_baseline_message_from_dummy():
    tr = Tracker("dummy")
    done = []
    for _, line in dummy_items(DummyConfig(trials=1, patterns=["calm"])):
        done += [u.segment for u in tr.feed_line(line) if isinstance(u, BaselineDone)]
    msg = baseline_message(done[0], "dummy")
    json.dumps(msg, allow_nan=False)
    assert msg["type"] == "baseline_result" and msg["source"] == "dummy" and msg["status"] == "COMPLETE"
    assert msg["valid"] is True and msg["n"] == 750 and msg["n_samples"] == 750
    assert msg["fsr"]["sd"] > 20 and msg["gsr"]["sd"] > 5
    assert msg["quality"] == {"ok": True, "warnings": [], "sd_floor_applied": {"fsr": False, "gsr": False}}


def test_baseline_message_without_summary():
    from server.pipeline.tracker import BaselineSegment
    seg = BaselineSegment(session_index=1, esp_session=1, baseline_id=3, status="ABORTED", reason="LONG_PRESS")
    msg = baseline_message(seg, "hardware")
    assert msg["valid"] is False and msg["fsr"] == {"mean": None, "sd": None}
    assert msg["quality"]["ok"] is False
