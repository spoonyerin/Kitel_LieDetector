"""참가자 별칭(웹 → 서버 set_participant, S17) 테스트.

웹이 서버로 보내는 메시지는 별칭 하나뿐이고, 그 밖의 메시지는 무시한다. 기기로는 아무것도 보내지 않는다.
기록 저장 확인은 pytest 임시 폴더(tmp_path)에만 쓴다.
"""

import json
import os
import sys
import threading
import time

import pytest
from websockets.sync.client import connect

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server.app import (ALIAS_RULE, ParticipantState, parse_client_message, run_pipeline,  # noqa: E402
                        validate_alias)
from server.config import Settings  # noqa: E402


# ---------- 별칭 검증 ----------
@pytest.mark.parametrize("raw,expect", [
    ("P01", "P01"), ("김철수", "김철수"), ("team A_1-2", "team A_1-2"), (" P02 ", "P02"), ("x" * 20, "x" * 20),
    ("가", "가"), ("0", "0"),
])
def test_alias_accepted(raw, expect):
    assert validate_alias(raw) == (expect, None)


@pytest.mark.parametrize("raw", [
    "", "   ", "x" * 21, "<script>", "P01;", "a.b", "a/b", "P01\n2", "ㄱㄴ", "é", "😀", "P'01", "=1+1",
    None, 123, ["P01"], {"a": 1},
])
def test_alias_rejected(raw):
    alias, why = validate_alias(raw)
    assert alias is None and why == ALIAS_RULE


def test_client_messages_other_than_alias_are_ignored():
    assert parse_client_message('{"type": "set_participant", "alias": "P03"}') == ("set_participant", "P03")
    assert parse_client_message('{"type": "set_participant", "alias": "P03!"}') == ("rejected", ALIAS_RULE)
    assert parse_client_message('{"type": "set_participant"}') == ("rejected", ALIAS_RULE)
    for text in ('{"type": "set_mode", "mode": "x"}', '{"type": "new_session"}', '{"type": "command", "send": "R"}',
                 "not json", "[1, 2]", '"P01"', "", json.dumps({"type": "set_participant", "alias": "P" * 2000})):
        assert parse_client_message(text) is None, text


# ---------- 시도 결과의 participant_id ----------
def _settings(tmp_path=None, **kw):
    base = dict(source="dummy", dummy_trials=2, dummy_patterns=["calm", "reactive"], dummy_speed=0)
    if tmp_path is not None:
        base.update(save=True, save_root=str(tmp_path / "srv"))
    base.update(kw)
    return Settings(**base)


def test_results_carry_alias_and_are_saved(tmp_path):
    p = ParticipantState()
    p.set("P07")
    msgs = []
    run_pipeline(_settings(tmp_path), msgs.append, threading.Event(), participant=p)
    res = [m for m in msgs if m["type"] == "trial_result"]
    assert len(res) == 2 and all(r["participant_id"] == "P07" for r in res)
    d = os.path.join(tmp_path / "srv", os.listdir(tmp_path / "srv")[0])
    with open(os.path.join(d, "trial_results.jsonl"), encoding="utf-8") as f:
        rows = [json.loads(x) for x in f]
    assert [r["participant_id"] for r in rows] == ["P07", "P07"]


def test_results_without_alias_are_null():
    msgs = []
    run_pipeline(_settings(), msgs.append, threading.Event())
    assert all(m["participant_id"] is None for m in msgs if m["type"] == "trial_result")
    msgs = []
    run_pipeline(_settings(), msgs.append, threading.Event(), participant=ParticipantState())
    assert all(m["participant_id"] is None for m in msgs if m["type"] == "trial_result")


# ---------- 서버 (임시 uvicorn) ----------
def _recv_until(ws, pred, timeout=10.0):
    got, deadline = [], time.time() + timeout
    while True:
        left = deadline - time.time()
        if left <= 0:
            raise AssertionError(f"시간 초과: {[m['type'] for m in got]}")
        m = json.loads(ws.recv(timeout=left))
        got.append(m)
        if pred(m):
            return m, got


def test_server_alias_broadcast_reject_and_resend():
    from test_app import running_server
    s = _settings(dummy_trials=2, dummy_patterns=["calm"], dummy_speed=60)   # 첫 결과까지 1초 남짓
    with running_server(s) as port:
        url = f"ws://127.0.0.1:{port}/ws"
        with connect(url, open_timeout=5) as a, connect(url, open_timeout=5) as b:
            _recv_until(a, lambda m: m["type"] == "device_status")
            # 무시되는 메시지 (응답 없음, 서버는 계속)
            a.send(json.dumps({"type": "set_mode", "mode": "serial"}))
            a.send("not json")
            # 거부: 보낸 쪽에만
            a.send(json.dumps({"type": "set_participant", "alias": "<홍길동>"}))
            m, _ = _recv_until(a, lambda m: m["type"] == "participant_update")
            assert m["rejected"] is True and m["alias"] is None and m["message"] == ALIAS_RULE
            # 허용: 모두에게
            a.send(json.dumps({"type": "set_participant", "alias": " P05 "}))
            m, _ = _recv_until(a, lambda m: m["type"] == "participant_update")
            assert m == {"type": "participant_update", "alias": "P05", "rejected": False}
            m, before = _recv_until(b, lambda m: m["type"] == "participant_update")
            assert m["alias"] == "P05" and m["rejected"] is False
            assert not any(x["type"] == "participant_update" and x.get("rejected") for x in before)   # b는 거부를 못 받음
            # 이후 판정 결과에 별칭이 들어간다
            r, _ = _recv_until(a, lambda m: m["type"] == "trial_result" and m["participant_id"] == "P05", timeout=20)
            assert r["source"] == "dummy"
        # 새 접속자에게도 현재 별칭을 다시 보낸다
        with connect(url, open_timeout=5) as c:
            m, _ = _recv_until(c, lambda m: m["type"] == "participant_update")
            assert m["alias"] == "P05"
