"""학습 파이프라인(training/) 테스트.

가짜 collector 기록과 가짜 라벨은 모두 pytest 임시 폴더(tmp_path)에만 만든다. 실제 학습이 읽는 경로
(data/real/experiments)와 분리되어 있다. data/real은 읽기만 한다.
"""

import csv
import io
import json
import math
import os
import sys
from contextlib import redirect_stdout

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server.pipeline.tracker import Tracker, TrialDone, baseline_for  # noqa: E402
from server.result import judge_trial  # noqa: E402
from server.source.dummy_source import DummyConfig, dummy_items  # noqa: E402
from training import build_dataset as B  # noqa: E402
from training import train_logreg as T  # noqa: E402

import record_session  # noqa: E402  (collector가 쓰는 열 이름을 그대로 쓴다)

PROTO_FIELDS = ["no", "item_id", "question_text", "marked_lie", "asked_trials", "role",
                "planned_condition", "final_condition", "status"]


# ----------------------------------------------------------
# 가짜 collector 기록 (tmp_path에만)
# ----------------------------------------------------------
def spec(pattern, role, planned, condition, confirmed, final=None):
    return {"pattern": pattern, "role": role, "planned": planned, "condition": condition,
            "confirmed": confirmed, "final": condition if final is None else final}


def make_run(root, run_name, pid, specs, old_format=False, with_events=True, source=None):
    run_dir = os.path.join(root, run_name)
    os.makedirs(run_dir)
    lines = [l for _, l in dummy_items(DummyConfig(trials=len(specs), patterns=[s["pattern"] for s in specs]))]
    with open(os.path.join(run_dir, "raw_serial_log.txt"), "w", encoding="utf-8") as f:
        for l in lines:
            f.write(f"2099-01-01 00:00:00.000\t{l}\n")
    sid = f"20990101_S01_{pid}"
    if with_events:
        with open(os.path.join(run_dir, "run_events.csv"), "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["pc_time", "esp_time_ms", "event", "trial", "detail", "esp_session", "baseline_id", "session_id"])
            w.writerow(["2099-01-01 00:00:00.000", 12500, "SESSION", 0, "START", 1, 0, sid])
    if source:
        with open(os.path.join(run_dir, "SOURCE"), "w", encoding="utf-8") as f:
            f.write(source)
    sdir = os.path.join(run_dir, sid)
    os.makedirs(sdir)
    fields = [c for c in record_session.TRIAL_SUMMARY_FIELDS
              if not (old_format and c in ("planned_condition", "confirmed", "role"))]
    with open(os.path.join(sdir, "trials_summary.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for i, s in enumerate(specs, 1):
            w.writerow({"session_id": sid, "participant_id": pid, "trial_id": i, "baseline_id": 1,
                        "question_id": f"Q{i:02d}", "condition": s["condition"], "planned_condition": s["planned"],
                        "confirmed": s["confirmed"], "role": s["role"], "status": "COMPLETE"})
    if not old_format:
        with open(os.path.join(sdir, "protocol.csv"), "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=PROTO_FIELDS)
            w.writeheader()
            for i, s in enumerate(specs, 1):
                w.writerow({"no": i, "item_id": f"Q{i:02d}", "question_text": f"질문 {i}", "marked_lie": 0,
                            "asked_trials": str(i), "role": s["role"], "planned_condition": s["planned"],
                            "final_condition": s["final"], "status": "COMPLETE"})
    return run_dir


MIXED = [
    spec("calm", "practice", "test", "test", ""),                 # 연습
    spec("reactive", "main", "lie", "lie", "as_planned"),         # 포함 (거짓)
    spec("calm", "main", "truth", "truth", "as_planned"),         # 포함 (진실)
    spec("calm", "main", "truth", "truth", ""),                   # 미확정
    spec("reactive", "main", "lie", "test", "corrected"),         # 참가자가 헷갈림(x) → test
    spec("calm", "main", "truth", "truth", "corrected", final="lie"),   # protocol.csv와 불일치
    spec("poor", "main", "lie", "lie", "as_planned"),             # 보류 (손 뗌)
]


# ----------------------------------------------------------
# build_dataset
# ----------------------------------------------------------
def test_labels_and_exclusion_reasons(tmp_path):
    make_run(str(tmp_path), "run_20990101_000001", "P01", MIXED)
    rows, excluded, n_runs = B.build_rows(str(tmp_path))
    assert n_runs == 1
    assert [(r["trial_id"], r["condition"], r["label"]) for r in rows] == [(2, "lie", 1), (3, "truth", 0)]
    assert dict(excluded) == {"practice": 1, "not_confirmed": 1, "excluded_test": 1, "label_mismatch": 1,
                              "quality_hold": 1}
    r = rows[0]
    assert (r["participant_id"], r["session_id"], r["source"], r["rule_decision"]) == \
        ("P01", "20990101_S01_P01", "hardware", "lie")
    assert set(B.MODEL_FEATURES) <= set(r) and "fsr_dz" not in r     # 기기 dz는 학습 특징에서 뺀다


def test_features_match_server_code(tmp_path):
    """학습/서빙 불일치 없음: 데이터셋 특징 = 서버 judge_trial의 특징 (기준 측정 구간 포함)."""
    run_dir = make_run(str(tmp_path), "run_20990101_000001", "P01", MIXED)
    rows, _, _ = B.build_rows(str(tmp_path))
    tr, served = Tracker("hardware"), {}
    with open(os.path.join(run_dir, "raw_serial_log.txt"), encoding="utf-8") as f:
        for raw in f:
            for u in tr.feed_line(raw.rstrip("\n").split("\t", 1)[1]):
                if isinstance(u, TrialDone):
                    served[u.segment.trial] = judge_trial(u.segment, "hardware",
                                                          baseline_segment=baseline_for(tr, u.segment))["features"]
    for r in rows:
        for k in B.MODEL_FEATURES:
            v = served[r["trial_id"]][k]
            assert (math.isnan(r[k]) and v is None) or r[k] == v, k


def test_label_values_do_not_change_features(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    make_run(str(a), "run_20990101_000001", "P01", MIXED[:3])
    flipped = [MIXED[0], spec("reactive", "main", "lie", "truth", "corrected"),
               spec("calm", "main", "truth", "lie", "corrected")]
    make_run(str(b), "run_20990101_000001", "P01", flipped)
    ra, _, _ = B.build_rows(str(a))
    rb, _, _ = B.build_rows(str(b))
    assert [r["label"] for r in ra] == [1, 0] and [r["label"] for r in rb] == [0, 1]
    for x, y in zip(ra, rb):
        assert all((math.isnan(x[k]) and math.isnan(y[k])) or x[k] == y[k] for k in B.MODEL_FEATURES)


@pytest.mark.parametrize("how", ["folder", "marker"])
def test_dummy_runs_are_rejected(tmp_path, how):
    if how == "folder":
        root = tmp_path / "dummy" / "experiments"
        make_run(str(root), "run_20990101_000001", "P01", MIXED[:3])
    else:
        root = tmp_path / "experiments"
        make_run(str(root), "run_20990101_000001", "P01", MIXED[:3], source="dummy")
    rows, excluded, _ = B.build_rows(str(root))
    assert rows == [] and dict(excluded) == {"dummy": 3}


def test_old_format_and_session_mapping_fallback(tmp_path):
    make_run(str(tmp_path / "old"), "run_20990101_000001", "P01", MIXED[:3], old_format=True)
    rows, excluded, _ = B.build_rows(str(tmp_path / "old"))
    assert rows == [] and dict(excluded) == {"label_columns_missing": 3}
    make_run(str(tmp_path / "noev"), "run_20990101_000001", "P01", MIXED[:3], with_events=False)
    rows, _, _ = B.build_rows(str(tmp_path / "noev"))        # run_events.csv 없으면 폴더 이름 _S01_로 찾음
    assert len(rows) == 2


def test_zero_confirmed_ends_normally(tmp_path, capsys):
    make_run(str(tmp_path), "run_20990101_000001", "P01", [MIXED[0], MIXED[3]])
    out = tmp_path / "out" / "dataset.csv"
    assert B.main(["--root", str(tmp_path), "--out", str(out)]) == 0
    text = capsys.readouterr().out
    assert "학습할 데이터가 없습니다" in text and "연습 질문" in text and "라벨 미확정" in text
    assert not out.exists()


def test_real_data_has_no_trainable_rows():
    """실측(읽기 전용): 지금은 연습 질문과 예전 형식뿐이라 0건 (2026-11-04)."""
    if not os.path.isdir(B.DEFAULT_ROOT):
        pytest.skip("실측 데이터 없음")
    rows, excluded, _ = B.build_rows()
    assert rows == [] and set(excluded) <= {"practice", "label_columns_missing"}


def test_protected_output_paths():
    for bad in (os.path.join(ROOT, "data", "real", "x.csv"), os.path.join(ROOT, "collector", "x.csv")):
        assert B.main(["--out", bad]) == 2 and not os.path.exists(bad)
        assert T.main(["--out", bad.replace(".csv", ".json")]) == 2


# ----------------------------------------------------------
# train_logreg (합성 특징 표, 가짜 라벨)
# ----------------------------------------------------------
def table(n_part=6, n_per=20, strength=3.0, rule="noise", seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for p in range(n_part):
        for i in range(n_per):
            y = i % 2
            row = {k: float(rng.normal()) for k in B.MODEL_FEATURES}
            row["gsr_mean_delta_q_z"] = strength * y + float(rng.normal())
            row.update(label=y, participant_id=f"P{p:02d}", source="hardware",
                       rule_decision=(("lie" if y else "truth") if rule == "perfect"
                                      else ("lie" if rng.random() < 0.5 else "truth")))
            rows.append(row)
    return rows


def _run(rows, tmp_path, **kw):
    buf = io.StringIO()
    out = tmp_path / "model.json"
    with redirect_stdout(buf):
        wrote, res = T.train(rows, str(out), **kw)
    return wrote, res, buf.getvalue(), out


def test_participant_split_is_disjoint(tmp_path):
    rows = table()
    X = np.array([[r[k] for k in B.MODEL_FEATURES] for r in rows])
    y = np.array([r["label"] for r in rows]); g = np.array([r["participant_id"] for r in rows])
    _, folds, scheme = T.outer_cv(X, y, g, B.MODEL_FEATURES, np.zeros(len(y), dtype=int))
    assert scheme == "leave-one-participant-out" and len(folds) == 6
    assert sorted(p for f in folds for p in f["test_groups"]) == sorted(set(g))
    assert all(not set(f["train_groups"]) & set(f["test_groups"]) for f in folds)


def test_preprocessing_never_sees_test_fold():
    """라벨 누수 방지: 테스트 참가자의 값(아주 큰 값)이 그 폴드의 표준화·결측 대체에 들어가지 않는다."""
    rows = table()
    for r in rows:
        if r["participant_id"] == "P00":
            r["fsr_local_dz"] = 1e6
    X = np.array([[r[k] for k in B.MODEL_FEATURES] for r in rows])
    y = np.array([r["label"] for r in rows]); g = np.array([r["participant_id"] for r in rows])
    _, folds, _ = T.outer_cv(X, y, g, B.MODEL_FEATURES, np.zeros(len(y), dtype=int))
    for f in folds:
        if f["test_groups"] == ["P00"]:
            assert abs(f["prep_mean"]["fsr_local_dz"]) < 10 and abs(f["prep_median"]["fsr_local_dz"]) < 10
        else:
            assert f["prep_mean"]["fsr_local_dz"] > 1e4      # 학습 폴드에 있을 때만 영향


def test_fold_preprocessor_fit_uses_only_given_rows(monkeypatch):
    seen = []
    orig = T.FoldPreprocessor.fit
    monkeypatch.setattr(T.FoldPreprocessor, "fit", lambda self, X: (seen.append(len(X)), orig(self, X))[1])
    rows = table(n_part=4, n_per=10)
    X = np.array([[r[k] for k in B.MODEL_FEATURES] for r in rows])
    y = np.array([r["label"] for r in rows]); g = np.array([r["participant_id"] for r in rows])
    T.outer_cv(X, y, g, B.MODEL_FEATURES, np.zeros(len(y), dtype=int))
    assert seen and max(seen) <= 30      # 한 명(10개)을 뺀 학습 폴드보다 큰 데이터로 fit하지 않는다


def test_export_when_better_than_baselines(tmp_path):
    rows = table()
    wrote, res, text, out = _run(rows, tmp_path)
    assert wrote and out.exists()
    m = json.loads(out.read_text(encoding="utf-8"))
    for k in ("feature_order", "impute_median", "scaler_mean", "scaler_std", "clip_range", "coef", "intercept",
              "threshold", "filters", "version", "training"):
        assert k in m
    assert m["exploratory"] is False and m["training"]["n_participants"] == 6 and m["training"]["n_trials"] == 120
    assert len(m["coef"]) == len(m["feature_order"]) == len(m["scaler_mean"])
    raw = out.read_text(encoding="utf-8")
    assert "P00" not in raw and "participant_id" not in raw and "run_" not in raw   # 참가자 정보 없음
    lr = res["metrics"]["logreg"]["balanced_accuracy"]
    assert lr > res["metrics"]["majority"]["balanced_accuracy"] and lr > res["metrics"]["rule"]["balanced_accuracy"]
    # model.json만으로 계산한 거짓 점수 = 학습한 모델 (서빙 일치)
    prep = T.FoldPreprocessor(B.MODEL_FEATURES).fit(np.array([[r[k] for k in B.MODEL_FEATURES] for r in rows]))
    model = T._logreg(m["C"]).fit(prep.transform(np.array([[r[k] for k in B.MODEL_FEATURES] for r in rows])),
                                  [r["label"] for r in rows])
    expect = model.predict_proba(prep.transform(np.array([[r[k] for k in B.MODEL_FEATURES] for r in rows[:10]])))[:, 1]
    assert np.allclose(T.predict_from_model(m, rows[:10]), expect, atol=1e-9)
    assert "균형정확도" in text and "참가자 P00" in text


def test_no_export_when_not_better_than_rule(tmp_path):
    wrote, res, text, out = _run(table(rule="perfect"), tmp_path)
    assert not wrote and not out.exists() and res["reason"] == "not_better_than_baseline"
    assert "기준 모델보다 높지 않습니다" in text


def test_small_participants_warning_and_allow_small(tmp_path):
    rows = table(n_part=4)
    wrote, res, text, out = _run(rows, tmp_path)
    assert not wrote and res["reason"] == "too_few_participants" and T.SMALL_BANNER in text
    assert "!!!!" in text
    wrote, res, text, out = _run(rows, tmp_path, allow_small=True)
    assert wrote and json.loads(out.read_text(encoding="utf-8"))["exploratory"] is True
    assert T.SMALL_BANNER in text


def test_zero_rows_one_class_and_dummy(tmp_path):
    wrote, res, text, _ = _run([], tmp_path)
    assert not wrote and "학습할 데이터가 없습니다" in text
    one = [dict(r, label=0) for r in table()]
    wrote, res, text, _ = _run(one, tmp_path)
    assert not wrote and res["reason"] == "cannot_evaluate"
    dummy = table()
    dummy[3]["source"] = "dummy"
    wrote, res, text, _ = _run(dummy, tmp_path)
    assert not wrote and res["reason"] == "dummy_rows" and "더미" in text


def test_training_is_deterministic(tmp_path):
    _, a, _, _ = _run(table(), tmp_path)
    _, b, _, _ = _run(table(), tmp_path)
    assert a["metrics"] == b["metrics"]


def test_dataset_csv_roundtrip_and_cli(tmp_path):
    """가짜 collector 5명 → build_dataset CSV → train_logreg (끝까지 동작, RuleJudge가 완벽해서 내보내지 않음)."""
    root = tmp_path / "experiments"
    specs = [MIXED[0], spec("reactive", "main", "lie", "lie", "as_planned"), spec("calm", "main", "truth", "truth", "as_planned"),
             spec("reactive", "main", "lie", "lie", "as_planned"), spec("calm", "main", "truth", "truth", "as_planned")]
    for i in range(5):
        make_run(str(root), f"run_20990101_00000{i}", f"P{i:02d}", specs)
    data = tmp_path / "out" / "dataset.csv"
    assert B.main(["--root", str(root), "--out", str(data)]) == 0
    rows = B.read_dataset(str(data))
    assert len(rows) == 20 and {r["participant_id"] for r in rows} == {f"P{i:02d}" for i in range(5)}
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert T.main(["--data", str(data), "--out", str(tmp_path / "out" / "model.json")]) == 0
    assert "균형정확도" in buf.getvalue()
