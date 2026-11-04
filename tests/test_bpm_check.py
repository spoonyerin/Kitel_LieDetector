"""심박 추정값 비교(training/bpm_check.py, S14) 테스트. 가짜 기록과 기준 CSV는 tmp_path에만 만든다."""

import builtins
import csv
import math
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server.source.dummy_source import DummyConfig, dummy_items  # noqa: E402
from training import bpm_check as BC  # noqa: E402


def make_log(root, name="run_20990101_000001", patterns=("calm", "reactive", "poor")):
    d = os.path.join(root, name)
    os.makedirs(d)
    with open(os.path.join(d, "raw_serial_log.txt"), "w", encoding="utf-8") as f:
        for _, l in dummy_items(DummyConfig(trials=len(patterns), patterns=list(patterns))):
            f.write(f"2099-01-01 00:00:00.000\t{l}\n")
    # 라벨 파일이 있어도 읽지 않아야 한다
    for fn in ("protocol.csv", "trials_summary.csv"):
        with open(os.path.join(d, fn), "w", encoding="utf-8") as f:
            f.write("condition\nlie\n")
    return d


def write_ref(path, rows, header=("run", "session", "trial", "ref_bpm", "note")):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    return str(path)


@pytest.fixture(scope="module")
def run_root(tmp_path_factory):
    root = str(tmp_path_factory.mktemp("exp"))
    d = make_log(root)
    est = BC.server_estimates(os.path.join(d, "raw_serial_log.txt"))
    return root, est


def test_estimates_from_server_code(run_root):
    _, est = run_root
    assert set(est) == {(1, 1), (1, 2), (1, 3)}
    assert est[(1, 1)]["hr_resp"] == pytest.approx(72, abs=3)          # 더미 calm ≈ 72
    assert est[(1, 2)]["hr_resp"] > est[(1, 1)]["hr_resp"]             # reactive는 심박이 오른다
    assert est[(1, 3)]["hr_resp"] is None                              # 손 뗌 → 값 없음


def test_compare_metrics(run_root, tmp_path):
    root, est = run_root
    e1, e2 = est[(1, 1)]["hr_resp"], est[(1, 2)]["hr_resp"]
    ref = write_ref(tmp_path / "ref.csv", [
        ["run_20990101_000001", "S01", 1, e1 - 4, "손목 15초×4"],
        ["run_20990101_000001", "1", 2, e2 - 2, ""],
        ["run_20990101_000001", "s01", 3, 70, ""],                        # 추정값 없음
        ["run_20990101_000001", "S01", 9, 70, ""],                        # 시도 없음
        ["run_없음", "S01", 1, 70, ""],                                   # run 없음
        ["run_20990101_000001", "S01", 1, 500, ""],                       # 범위 밖 → 건너뜀
        ["run_20990101_000001", "X", 1, 70, ""],                          # 세션 형식 오류
        ["run_20990101_000001", "S01", 1, "", ""],                        # 값 없음
    ])
    refs, bad = BC.read_reference(ref)
    assert len(refs) == 5 and [b[0] for b in bad] == [7, 8, 9]
    res = BC.compare(refs, root)
    assert [r["status"] for r in res["rows"]] == ["ok", "ok", "no_estimate", "trial_not_found", "run_not_found"]
    assert (res["n_pairs"], res["n_no_estimate"], res["n_not_found"]) == (2, 1, 2)
    assert res["mae"] == pytest.approx(3.0) and res["bias"] == pytest.approx(3.0)
    assert res["max_abs_error"] == pytest.approx(4.0) and res["missing_ratio"] == pytest.approx(1 / 3)
    base = BC.compare(refs[:1], root, window="baseline")
    assert base["feature"] == "hr_bl" and base["rows"][0]["est_bpm"] == pytest.approx(est[(1, 1)]["hr_bl"])


def test_run_path_forms(run_root, tmp_path):
    root, _ = run_root
    run_dir = os.path.join(root, "run_20990101_000001")
    for run in (run_dir, os.path.join(run_dir, "raw_serial_log.txt")):
        res = BC.compare([{"line": 2, "run": run, "session": 1, "trial": 1, "ref_bpm": 72.0}], str(tmp_path))
        assert res["rows"][0]["status"] == "ok"


def test_missing_columns_and_empty(tmp_path):
    p = write_ref(tmp_path / "bad.csv", [["r", 1, 1]], header=("run", "session", "trial"))
    with pytest.raises(ValueError, match="ref_bpm"):
        BC.read_reference(p)
    res = BC.compare([], str(tmp_path))
    assert res["n_ref"] == 0 and math.isnan(res["mae"]) and math.isnan(res["missing_ratio"])


def test_does_not_read_label_files(run_root, tmp_path, monkeypatch):
    root, est = run_root
    opened = []
    real_open = builtins.open
    monkeypatch.setattr(builtins, "open", lambda f, *a, **k: (opened.append(str(f)), real_open(f, *a, **k))[1])
    ref = write_ref(tmp_path / "ref.csv", [["run_20990101_000001", "S01", 1, 72, ""]])
    BC.compare(BC.read_reference(ref)[0], root)
    names = {os.path.basename(p) for p in opened}
    assert "raw_serial_log.txt" in names and not names & {"protocol.csv", "trials_summary.csv"}


def test_main_output(run_root, tmp_path, capsys):
    root, _ = run_root
    ref = write_ref(tmp_path / "ref.csv", [["run_20990101_000001", "S01", 1, 70, ""]])
    out = tmp_path / "o" / "cmp.csv"
    assert BC.main([ref, "--root", root, "--out", str(out)]) == 0
    text = capsys.readouterr().out
    assert "평균 절대 오차" in text and "편향(추정−기준)" in text and "값이 없는 비율 0%" in text
    assert "비교 개수가 적어" in text
    with open(out, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["status"] == "ok" and float(rows[0]["ref_bpm"]) == 70
    assert BC.main([ref, "--out", os.path.join(ROOT, "data", "real", "x.csv")]) == 2
    assert BC.main([str(tmp_path / "없음.csv")]) == 2
