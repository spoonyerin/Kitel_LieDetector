"""규칙 임계값 검증(training/evaluate_rule.py)과 평가 리포트(training/report.py) 테스트 (S14).

가짜 데이터(합성 특징 표, 가짜 collector 기록과 가짜 라벨)는 모두 tmp_path 또는 메모리에만 만든다.
"""

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

from server.judges.rule import RuleJudge, load_rule_config, make_rule_judge  # noqa: E402
from training import evaluate_rule as E  # noqa: E402
from training import report as RP  # noqa: E402
from training.build_dataset import MODEL_FEATURES, write_dataset  # noqa: E402


def rule_table(n_part=6, n_per=20, signal=True, seed=0):
    """거짓 시도는 FSR 국소 dz가 크다 (signal=False면 모든 dz가 0). 다른 특징은 잡음 + 약한 신호."""
    rng = np.random.default_rng(seed)
    rows = []
    for p in range(n_part):
        for i in range(n_per):
            y = i % 2
            row = {k: float(rng.normal()) for k in MODEL_FEATURES}
            row["gsr_mean_delta_q_z"] = 3.0 * y + float(rng.normal())
            if signal:
                row["fsr_local_dz"] = float(rng.choice([-1, 1]) * (3.3 + 0.4 * rng.random()) if y
                                            else rng.normal(0, 0.7))
                row["gsr_local_dz"] = float(rng.normal(0, 0.7))
            else:
                row["fsr_local_dz"] = row["gsr_local_dz"] = 0.0
            row.update(label=y, participant_id=f"P{p:02d}", source="hardware",
                       rule_decision="lie" if abs(row["fsr_local_dz"]) >= 3 else "truth")
            rows.append(row)
    return rows


def _eval(rows, out=None, **kw):
    buf = io.StringIO()
    with redirect_stdout(buf):
        wrote, res = E.evaluate(rows, out, **kw)
    return wrote, res, buf.getvalue()


# ---------- 규칙과 격자 ----------
def test_rule_predict_matches_rulejudge():
    rng = np.random.default_rng(1)
    F, G = rng.normal(0, 3, 200), rng.normal(0, 3, 200)
    F[:5] = [3.0, -3.0, 2.999, 4.5, -1.5]      # 경계값 (>=)
    for params in [(3.0, 3.0, 1), (1.5, 4.5, 2), (2.5, 2.0, 1)]:
        j = RuleJudge(*params)
        expect = [1 if j.judge({"fsr_local_dz": f, "gsr_local_dz": g}, {}, {"ok": True}).decision == "lie" else 0
                  for f, g in zip(F, G)]
        assert E.rule_predict(F, G, params).tolist() == expect
    assert E.GRID_TH == (1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5) and E.GRID_MIN == (1, 2)


def test_selection_uses_only_training_participants():
    """빼 둔 참가자의 값을 아무리 바꿔도 그 폴드에서 고른 임계값은 같다 (낙관적 편향 방지)."""
    rows = rule_table()
    ok, _ = E.usable(rows)
    F = np.array([r["fsr_local_dz"] for r in ok]); G = np.array([r["gsr_local_dz"] for r in ok])
    y = np.array([r["label"] for r in ok]); groups = np.array([r["participant_id"] for r in ok])
    _, folds, scheme = E.nested_eval(F, G, y, groups)
    assert scheme == "leave-one-participant-out" and len(folds) == 6
    F2, G2 = F.copy(), G.copy()
    held = groups == "P00"
    F2[held] = np.where(y[held] == 1, 1.6, 0.0)       # P00만 아주 다른 패턴 (1.5가 최적이 되도록)
    _, folds2, _ = E.nested_eval(F2, G2, y, groups)
    i = next(k for k, f in enumerate(folds) if f["test_groups"] == ["P00"])
    assert folds[i]["params"] == folds2[i]["params"]
    assert all("P00" not in f["train_groups"] for f in (folds[i], folds2[i]))


# ---------- 평가와 안전장치 ----------
def test_evaluate_writes_validated_config(tmp_path):
    out = tmp_path / "rule_config.json"
    wrote, res, log = _eval(rule_table(), str(out))
    assert wrote and out.exists()
    m = res["metrics"]
    assert m["rule_selected"]["balanced_accuracy"] > m["majority"]["balanced_accuracy"] == 0.5
    for k in ("balanced_accuracy", "lie_detection_rate", "truth_as_lie_rate", "confusion"):
        assert k in m["rule_selected"]
    c = load_rule_config(str(out))
    assert c["validated"]["n_participants"] == 6 and c["validated"]["n_trials"] == 120
    assert c["fsr_th"] in E.GRID_TH and c["min_sensors"] in E.GRID_MIN
    j, reason = make_rule_judge(str(out))
    assert reason is None and j.validated and "검증됨" in j.note
    assert "자동 적용하지 않았습니다" in log and "RULE_CONFIG" in log
    json.dumps(c, allow_nan=False)


def test_small_participants_exploratory(tmp_path):
    out = tmp_path / "rule_config.json"
    wrote, res, log = _eval(rule_table(n_part=3), str(out))
    assert not wrote and not out.exists() and res["reason"] == "too_few_participants"
    assert "탐색용 결과이며 채택하지 않습니다" in log
    wrote, res, log = _eval(rule_table(n_part=3), str(out), allow_small=True)
    assert wrote
    c = load_rule_config(str(out))
    assert c["validated"] is None and c["evaluation"]["exploratory"] is True
    j, _ = make_rule_judge(str(out))
    assert not j.validated and "검증됨" not in j.note          # 설정 파일이 있다고 검증됨이 아니다


def test_not_better_than_baseline_no_config(tmp_path):
    out = tmp_path / "rule_config.json"
    wrote, res, log = _eval(rule_table(signal=False), str(out), allow_small=True)
    assert not wrote and not out.exists() and res["reason"] == "not_better_than_baseline"
    assert "설정 파일을 제안하지 않습니다" in log


def test_no_data_dummy_nan_and_one_class(tmp_path):
    assert _eval([])[1]["reason"] == "no_data"
    rows = rule_table()
    rows[0]["source"] = "dummy"
    assert _eval(rows)[1]["reason"] == "dummy_rows"
    rows = rule_table()
    for r in rows[:7]:
        r["gsr_local_dz"] = math.nan
    _, res, log = _eval(rows)
    assert res["n_no_feature"] == 7 and res["n_used"] == 113 and "7개는" in log
    one = [r for r in rule_table() if r["label"] == 0]
    assert _eval(one)[1]["reason"] == "cannot_evaluate"


def test_main_protects_and_reads_dataset(tmp_path, capsys):
    assert E.main(["--data", "x.csv", "--out", os.path.join(ROOT, "data", "real", "rule_config.json")]) == 2
    assert E.main(["--data", "x.csv", "--out", os.path.join(ROOT, "collector", "rule_config.json")]) == 2
    data = tmp_path / "dataset.csv"
    rows = rule_table()
    for r in rows:
        r.update(run="run_x", session_id="s", trial_id=1, question_id="Q", condition="lie" if r["label"] else "truth",
                 confirmed="as_planned")
    write_dataset(rows, str(data))
    out = tmp_path / "o" / "rule_config.json"
    assert E.main(["--data", str(data), "--out", str(out)]) == 0
    assert out.exists()
    assert E.main(["--root", str(tmp_path / "빈폴더"), "--out", str(tmp_path / "r2.json")]) == 0
    assert "평가할 데이터가 없습니다" in capsys.readouterr().out and not (tmp_path / "r2.json").exists()


# ---------- 리포트 ----------
def test_report_no_data(tmp_path):
    out = tmp_path / "report.md"
    assert RP.main(["--root", str(tmp_path / "없음"), "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "## 데이터 없음" in text and "정확도를 보장하지 않으며" in text
    assert RP.main(["--root", str(tmp_path), "--out", os.path.join(ROOT, "data", "real", "r.md")]) == 2


def test_report_contents_and_no_side_files(tmp_path):
    out_dir = os.path.join(ROOT, "training", "out")
    before = {f: os.path.getmtime(os.path.join(out_dir, f)) for f in ("model.json", "rule_config.json")
              if os.path.exists(os.path.join(out_dir, f))}
    text, res = RP.build_report(rule_table(), excluded={"practice": 4, "quality_hold": 2}, n_runs=3,
                                source_desc="합성", today="2099-01-01")
    for s in ("## 1. 데이터 요약", "run 3개, 시도 126개 중 평가에 쓴 시도 120개", "참가자 6명, 진실 60개, 거짓 60개",
              "| 연습 질문 (`practice`) | 4 |", "## 2. 참가자별 시도 수", "| P00 | 20 | 10 | 10 |",
              "## 3. 모델별 지표", "| 로지스틱 회귀 |", "| 규칙 판정기, 임계값 중첩 선택 |", "기준: 항상 다수 클래스",
              "## 4. 혼동행렬", "| 실제 \\ 판정 | 진실 | 거짓 |", "## 5. 내보내기 조건", "## 한계와 안내",
              "정확도를 보장하지 않으며", "leave-one-participant-out"):
        assert s in text, s
    # 이 합성 표에서는 규칙 판정기가 거의 완벽해서 로지스틱 회귀가 기준(규칙)을 넘지 못한다 → 내보내지 않음
    assert res["logreg"]["reason"] == "not_better_than_baseline" and "기준 모델보다 낫지 않음" in text
    assert res["rule"].get("exportable") and "규칙 판정기 설정 제안: 조건 충족" in text
    assert "확률" not in text
    after = {f: os.path.getmtime(os.path.join(out_dir, f)) for f in ("model.json", "rule_config.json")
             if os.path.exists(os.path.join(out_dir, f))}
    assert before == after          # 리포트는 model.json, rule_config.json을 쓰지 않는다


def test_report_small_and_failures():
    text, res = RP.build_report(rule_table(n_part=3, signal=False), excluded={}, n_runs=1, source_desc="합성")
    assert "탐색용 결과이며 채택하지 않습니다" in text
    assert "설정 파일을 제안하지 않음" in text


def test_report_from_fake_collector(tmp_path):
    from test_training import MIXED, make_run
    make_run(str(tmp_path), "run_20990101_000001", "P01", MIXED)
    out = tmp_path / "report.md"
    assert RP.main(["--root", str(tmp_path), "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "평가에 쓴 시도 2개" in text and "| 연습 질문 (`practice`) | 1 |" in text
    assert "평가할 수 없음" in text        # 참가자 1명
