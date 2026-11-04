"""서버 기록 저장(server/storage.py, SAVE=1, S16) 테스트.

기록은 모두 pytest 임시 폴더(tmp_path)에만 쓴다. data/server/에는 쓰지 않는다.
보호 폴더(data/real, data/dummy, collector) 검사는 폴더를 만들기 전에 거부되는지만 확인한다 (아무것도 만들지 않음).
"""

import json
import os
import sys
import threading

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server.app import run_pipeline  # noqa: E402
from server.config import DEFAULT_SAVE_ROOT, Settings, load_settings  # noqa: E402
from server.source.dummy_source import DummyConfig, dummy_items  # noqa: E402
from server.source.lines import iter_raw_log  # noqa: E402
from server import storage as ST  # noqa: E402
from server.storage import ServerRecorder, StorageError, check_save_root  # noqa: E402


def _run(settings, source=None):
    msgs = []
    run_pipeline(settings, msgs.append, threading.Event(), source=source)
    return msgs


def _dummy(tmp_path, **kw):
    base = dict(source="dummy", dummy_trials=2, dummy_patterns=["calm", "poor"], dummy_speed=0,
                question_seed=7, save=True, save_root=str(tmp_path / "server"))
    base.update(kw)
    return Settings(**base)


def _one_dir(root):
    dirs = os.listdir(root)
    assert len(dirs) == 1, dirs
    return os.path.join(root, dirs[0])


def _jsonl(path):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    assert "NaN" not in text and "Infinity" not in text
    return [json.loads(line) for line in text.splitlines()]


# ---------- 기본 꺼짐 ----------
def test_save_is_off_by_default(tmp_path):
    s = load_settings({})
    assert s.save is False and s.save_root == DEFAULT_SAVE_ROOT
    assert os.path.normpath(DEFAULT_SAVE_ROOT).endswith(os.path.join("data", "server"))
    assert load_settings({"SAVE": "1"}).save is True and load_settings({"SAVE": "0"}).save is False
    with pytest.raises(ValueError):
        load_settings({"SAVE": "maybe"})
    root = tmp_path / "server"
    msgs = _run(_dummy(tmp_path, save=False))
    assert not root.exists()                              # 아무것도 만들지 않는다
    st = [m for m in msgs if m["type"] == "device_status"]
    assert st and all(m["saving"] is False and m["save_dir"] is None and m["save_error"] is None for m in st)


def test_gitignore_has_data_server():
    with open(os.path.join(ROOT, ".gitignore"), encoding="utf-8") as f:
        lines = [x.strip() for x in f]
    assert "data/server/" in lines and "data/" in lines


# ---------- 저장 위치 보호 ----------
@pytest.mark.parametrize("parts", [
    ("data", "real"), ("data", "real", "__저장시험_없음__"), ("data", "real", "experiments", "__x__"),
    ("data", "dummy"), ("data", "dummy", "__저장시험_없음__"), ("collector", "__저장시험_없음__"),
])
def test_protected_roots_rejected_before_creating(parts):
    target = os.path.join(ROOT, *parts)
    existed = os.path.exists(target)
    with pytest.raises(StorageError, match="쓰지 않습니다"):
        check_save_root(target)
    with pytest.raises(StorageError):
        ServerRecorder(target, "serial", "hardware")
    assert os.path.exists(target) == existed               # 폴더를 만들지 않았다
    # 상대 경로·대소문자·.. 우회도 같은 결과
    rel = os.path.relpath(target, os.getcwd())
    with pytest.raises(StorageError):
        check_save_root(rel)
    with pytest.raises(StorageError):
        check_save_root(os.path.join(ROOT, "data", "server", "..", *parts[1:]) if parts[0] == "data" else target)


def test_protected_reason_allows_server_folder(tmp_path):
    assert ST.protected_reason(os.path.join(ROOT, "data", "server")) is None
    assert ST.protected_reason(os.path.join(ROOT, "data", "realistic")) is None      # 이름만 비슷한 폴더
    assert ST.protected_reason(str(tmp_path)) is None
    assert ST.protected_reason(os.path.join(ROOT, "data", "real")) == "data/real"
    assert ST.protected_reason(os.path.join(ROOT, "data", "dummy", "x")) == "data/dummy"


def test_pipeline_with_protected_root_keeps_running():
    target = os.path.join(ROOT, "data", "real", "__저장시험_없음__")
    msgs = _run(Settings(source="dummy", dummy_trials=1, dummy_patterns=["calm"], dummy_speed=0,
                         save=True, save_root=target))
    assert not os.path.exists(target)
    errs = [m for m in msgs if m["type"] == "error"]
    assert errs and "data/real/ 안입니다" in errs[0]["message"]
    st = [m for m in msgs if m["type"] == "device_status"]
    assert all(m["saving"] is False and "data/real" in m["save_error"] for m in st)
    assert len([m for m in msgs if m["type"] == "trial_result"]) == 1     # 판정은 계속된다


# ---------- 더미 ----------
def test_dummy_record_contents(tmp_path):
    msgs = _run(_dummy(tmp_path))
    d = _one_dir(tmp_path / "server")
    assert os.path.basename(d).endswith("_dummy")                          # 폴더 이름에 입력원
    assert sorted(os.listdir(d)) == ["run_info.json", "trial_results.jsonl"]   # 더미는 원시 줄 없음
    st = [m for m in msgs if m["type"] == "device_status"]
    assert all(m["saving"] is True and m["save_dir"] == d for m in st)        # tmp_path는 저장소 밖 → 절대 경로
    with open(os.path.join(d, "run_info.json"), encoding="utf-8") as f:
        info = json.load(f)
    assert (info["format"], info["input"], info["source"]) == ("truthpulse-server-record", "dummy", "dummy")
    assert info["training_excluded"] is True and "학습에 쓰지 않습니다" in info["training_excluded_reason"]
    assert info["question_seed"] == 7 and info["judge"]["name"] == "rule" and info["judge"]["version"] == "0.0.1"
    assert info["labels_saved"] is False and info["n_results"] == 2 and info["ended_at"]
    assert info["dummy"] == {"trials": 2, "patterns": ["calm", "poor"], "seed": 0}
    rows = _jsonl(os.path.join(d, "trial_results.jsonl"))
    web = [m for m in msgs if m["type"] == "trial_result"]
    assert len(rows) == 2 == len(web)
    for r, w in zip(rows, web):
        assert r["type"] == "trial_result" and (r["source"], r["input"]) == ("dummy", "dummy")
        assert r["training_excluded"] is True and r["saved_at"]
        assert "server_only" in r and "server_only" not in w            # 서버 전용 항목은 기록에만
        assert r["decision"] == w["decision"] and r["question_id"] == w["question_id"]
        for k in ("ground_truth", "condition", "planned_condition", "marked_lie", "confirmed", "label"):
            assert k not in r


def test_label_keys_are_never_written(tmp_path):
    rec = ServerRecorder(str(tmp_path), "dummy", "dummy")
    assert rec.write_result({"type": "trial_result", "decision": "lie", "condition": "lie"}) is False
    assert not rec.ok and "trial_results.jsonl" in rec.error
    assert not os.path.exists(os.path.join(rec.dir, "trial_results.jsonl"))


def test_nan_written_as_null_and_names_unique(tmp_path):
    import datetime as dt
    fixed = lambda: dt.datetime(2099, 1, 1, 0, 0, 1)   # noqa: E731
    a = ServerRecorder(str(tmp_path), "serial", "hardware", now=fixed)
    b = ServerRecorder(str(tmp_path), "serial", "hardware", now=fixed)
    assert os.path.basename(a.dir) == "20990101_000001_serial" and os.path.basename(b.dir) == "20990101_000001_serial_2"
    assert a.write_result({"type": "trial_result", "features": {"x": float("nan")}, "score": float("inf")})
    row = _jsonl(os.path.join(a.dir, "trial_results.jsonl"))[0]
    assert row["features"]["x"] is None and row["score"] is None and row["training_excluded"] is False
    with pytest.raises(StorageError):
        ServerRecorder(str(tmp_path), "camera", "hardware")
    with pytest.raises(StorageError):
        ServerRecorder(str(tmp_path), "dummy", "fake")


# ---------- 재생 ----------
def _fake_replay_run(tmp_path):
    run = tmp_path / "run_20990101_000001"
    run.mkdir()
    with open(run / "raw_serial_log.txt", "w", encoding="utf-8") as f:
        for _, line in dummy_items(DummyConfig(trials=2, patterns=["calm", "reactive"])):
            f.write(f"2099-01-01 00:00:00.000\t{line}\n")
    return str(run)


def test_replay_record_marked_not_for_training(tmp_path):
    run = _fake_replay_run(tmp_path)
    msgs = _run(Settings(source="replay", replay_run=run, replay_speed=0, save=True, save_root=str(tmp_path / "srv")))
    d = _one_dir(tmp_path / "srv")
    assert os.path.basename(d).endswith("_replay")
    assert "raw_serial_log.txt" not in os.listdir(d)                       # 재생 원본은 다시 쓰지 않는다
    rows = _jsonl(os.path.join(d, "trial_results.jsonl"))
    assert len(rows) == len([m for m in msgs if m["type"] == "trial_result"]) == 2
    assert all(r["source"] == "hardware" and r["input"] == "replay" and r["training_excluded"] is True for r in rows)
    with open(os.path.join(d, "run_info.json"), encoding="utf-8") as f:
        info = json.load(f)
    assert info["replay_run"] == run and "중복" in info["training_excluded_reason"]


# ---------- 시리얼 (가짜) ----------
def test_serial_raw_lines_same_format_as_collector(tmp_path):
    from test_serial_source import FakeSerial, make_opener
    from server.source.serial_source import SerialSource
    lines = [line for _, line in dummy_items(DummyConfig(trials=2, patterns=["calm", "reactive"]))]
    stop = threading.Event()
    fake = FakeSerial([(x + "\n").encode() for x in lines], on_empty=stop.set)
    src = SerialSource("COM_FAKE", stop=stop, sleep=lambda s: None, opener=make_opener([fake]))
    settings = Settings(source="serial", serial_port="COM_FAKE", save=True, save_root=str(tmp_path / "srv"))
    msgs = []
    run_pipeline(settings, msgs.append, stop, source=src)
    assert fake.writes == []                                               # 기기로는 아무것도 보내지 않는다
    d = _one_dir(tmp_path / "srv")
    assert os.path.basename(d).endswith("_serial")
    raw = os.path.join(d, "raw_serial_log.txt")
    saved = [line for _, line in iter_raw_log(raw)]                        # collector 형식 읽기 함수로 다시 읽힌다
    assert saved == [x for x in lines if x.strip()]
    with open(raw, encoding="utf-8") as f:
        first = f.readline()
    import re
    assert re.match(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3}\t", first)    # collector now_str과 같은 PC시각
    rows = _jsonl(os.path.join(d, "trial_results.jsonl"))
    assert [r["decision"] for r in rows] == ["truth", "lie"]
    assert all(r["source"] == "hardware" and r["input"] == "serial" and r["training_excluded"] is False for r in rows)
    with open(os.path.join(d, "run_info.json"), encoding="utf-8") as f:
        info = json.load(f)
    assert info["serial_port"] == "COM_FAKE" and info["n_raw_lines"] == len(saved) and info["labels_saved"] is False


# ---------- 쓰기 실패 ----------
def test_write_failure_does_not_stop_server(tmp_path, monkeypatch):
    def broken(self, name, text):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(ServerRecorder, "_append", broken)
    msgs = _run(_dummy(tmp_path, dummy_trials=3, dummy_patterns=["calm", "reactive", "calm"]))
    assert len([m for m in msgs if m["type"] == "trial_result"]) == 3      # 판정·방송은 계속된다
    errs = [m for m in msgs if m["type"] == "error"]
    assert len(errs) == 1 and "기록 저장에 실패해 저장을 멈췄습니다" in errs[0]["message"]   # 한 번만 알린다
    st = [m for m in msgs if m["type"] == "device_status"]
    assert st[0]["saving"] is True and st[-1]["saving"] is False and "No space" in st[-1]["save_error"]


def test_server_status_reports_saving(tmp_path):
    from test_app import _collect, _finished, _results, running_server
    with running_server(_dummy(tmp_path)) as port:
        msgs = _collect(port, lambda m: _finished(m) and len(_results(m)) == 2)
    st = [m for m in msgs if m["type"] == "device_status"][-1]
    assert st["saving"] is True and st["save_error"] is None and st["save_dir"]
