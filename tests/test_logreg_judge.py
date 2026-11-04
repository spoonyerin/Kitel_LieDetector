"""학습된 판정기(server/judges/logreg.py) 테스트. 가짜 model.json은 tmp_path에만 만든다.

가짜 model.json은 training/train_logreg.py의 train()으로 합성 특징 표(가짜 라벨)에서 만든다.
"""

import copy
import io
import json
import math
import os
import re
import sys
from contextlib import redirect_stdout

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server.config import Settings, load_settings  # noqa: E402
from server.judges import logreg as L  # noqa: E402
from server.judges.logreg import LogregJudge, feature_sentence, load_model, select_judge  # noqa: E402
from server.judges.rule import RuleJudge  # noqa: E402
from training import train_logreg as T  # noqa: E402
from training.build_dataset import MODEL_FEATURES  # noqa: E402

OK = {"ok": True, "hold_code": None}


def table(n_part=6, n_per=20, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for p in range(n_part):
        for i in range(n_per):
            y = i % 2
            row = {k: float(rng.normal()) for k in MODEL_FEATURES}
            row["gsr_mean_delta_q_z"] = 3.0 * y + float(rng.normal())
            row["fsr_sd_ratio_bl"] = 1.0 + 0.8 * y + 0.3 * float(rng.normal())
            row.update(label=y, participant_id=f"P{p:02d}", source="hardware",
                       rule_decision="lie" if rng.random() < 0.5 else "truth")
            rows.append(row)
    return rows


def make_model(tmp_path, n_part=6, allow_small=False, name="model.json"):
    path = tmp_path / name
    with redirect_stdout(io.StringIO()):
        wrote, _ = T.train(table(n_part=n_part), str(path), allow_small=allow_small)
    assert wrote
    return str(path)


@pytest.fixture(scope="module")
def model_path(tmp_path_factory):
    return make_model(tmp_path_factory.mktemp("m"))


def feats(row):
    return {k: row[k] for k in MODEL_FEATURES}


# ---------- 계산이 model.json/학습 코드와 같다 ----------
def test_score_matches_training_model(model_path):
    m = load_model(model_path)
    j = LogregJudge(m, model_path)
    rows = table(seed=5)[:30]
    rows[0]["hr_q"] = math.nan            # 결측 대체도 같아야 한다
    rows[1]["gsr_local_dz"] = math.nan
    expect = T.predict_from_model(m, rows)
    for r, e in zip(rows, expect):
        v = j.judge(feats(r), {}, OK)
        assert v.score == pytest.approx(float(e), abs=1e-12)
        assert v.decision == ("lie" if e >= m["threshold"] else "truth")
        logit = sum(c.contribution for c in v.contributions) + m["intercept"]
        assert 1 / (1 + math.exp(-logit)) == pytest.approx(v.score)
    assert (j.name, j.trained, j.version, j.exploratory) == ("logreg", True, "0.1.0", False)
    assert [c.feature for c in v.contributions] == m["feature_order"]


def test_holds_and_label_keys(model_path):
    j = LogregJudge(load_model(model_path))
    row = feats(table()[0])
    v = j.judge(row, {}, {"ok": False, "hold_code": "CONTACT_EVENT:CONTACT_SHIFT"})
    assert (v.decision, v.score, v.contributions, v.hold_reason) == ("inconclusive", None, [], "CONTACT_EVENT:CONTACT_SHIFT")
    sparse = {k: (row[k] if i < 3 else None) for i, k in enumerate(MODEL_FEATURES)}
    v = j.judge(sparse, {}, OK)
    assert v.decision == "inconclusive" and v.hold_reason == "MODEL_FEATURES_MISSING"
    from server.reasons import hold_text
    assert "학습된 판정기에 필요한 신호 값이 많이 비어" in hold_text("MODEL_FEATURES_MISSING")
    for bad in ({**row, "condition": "lie"}, {**row, "ground_truth": 1}):
        with pytest.raises(ValueError):
            j.judge(bad, {}, OK)
    with pytest.raises(ValueError):
        j.judge(row, {"meta": {"planned_condition": "truth"}}, OK)


# ---------- 이유 문장 ----------
def test_reasons_only_actual_contributors_plain_korean(model_path):
    j = LogregJudge(load_model(model_path))
    for r in table(seed=9)[:20]:
        v = j.judge(feats(r), {}, OK)
        reasons = j.reasons(v)
        top = j.top_contributions(v)
        sign = 1 if v.decision == "lie" else -1
        assert all(sign * c.contribution >= L.REASON_MIN_CONTRIBUTION for c in top) and len(top) <= L.MAX_REASONS
        if top:
            assert len(reasons) == len(top)
            assert all(("높인" if v.decision == "lie" else "낮춘") in s for s in reasons)
        for s in reasons:
            assert not re.search(r"[A-Z]{3,}|σ|\d|확률", s), s


def test_no_strong_contributor_message(model_path):
    m = load_model(model_path)
    j = LogregJudge(m)
    at_mean = dict(zip(m["feature_order"], m["scaler_mean"]))   # 모든 z = 0
    v = j.judge(at_mean, {}, OK)
    assert j.reasons(v) == ["뚜렷하게 한쪽으로 기여한 특징이 없습니다. 여러 특징의 작은 차이가 모여 정해진 판정입니다."]


@pytest.mark.parametrize("name,z,expected", [
    ("fsr_sd_ratio_bl", 1.2, "답변 중 손가락 압력의 변동이 평소보다 컸습니다 (기준 측정과 비교)."),
    ("gsr_mean_delta_q_z", -0.8, "답변 중 피부전도의 평균 변화가 평소보다 작았습니다 (질문 구간과 비교)."),
    ("fsr_duration_bl_s", 1.0, "답변 중 손가락 압력이 크게 벗어나 있던 시간이 평소보다 길었습니다 (기준 측정과 비교)."),
    ("gsr_duration_q_s", -1.0, "답변 중 피부전도가 크게 벗어나 있던 시간이 평소보다 짧았습니다 (질문 구간과 비교)."),
    ("gsr_local_dz", 2.0, "답변 중 피부전도 변화가 질문 구간과 비교해 평소보다 컸습니다."),
    ("hr_resp", 1.0, "답변 중 심박이 평소보다 높았습니다."),
    ("hr_delta_bl", -1.0, "답변 중 심박 변화가 평소보다 작았습니다 (기준 측정과 비교)."),
    ("ppg_amp_ratio_q", 1.0, "답변 중 맥파 세기 변화가 평소보다 컸습니다 (질문 구간과 비교)."),
    ("fsr_max_change_q_z", 0.5, "답변 중 손가락 압력의 가장 큰 변화가 평소보다 컸습니다 (질문 구간과 비교)."),
])
def test_feature_sentences(name, z, expected):
    assert feature_sentence(name, z) == expected


def test_note_has_training_counts_and_metrics_without_probability_word(model_path):
    note = LogregJudge(load_model(model_path)).note
    assert "참가자 6명" in note and "시도 120개" in note and "균형 정확도" in note and "v0.1.0" in note
    assert "확률" not in note and "보정을 평가하지 않은" in note


# ---------- 로드와 대체 ----------
def _write(tmp_path, name, data):
    p = tmp_path / name
    p.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return str(p)


def test_fallback_to_rule_on_bad_model(tmp_path, model_path):
    good = json.loads(open(model_path, encoding="utf-8").read())
    cases = {
        "missing": str(tmp_path / "없음.json"),
        "broken": _write(tmp_path, "b.json", "{not json"),
        "format": _write(tmp_path, "f.json", {**good, "format": "other"}),
        "version": _write(tmp_path, "v.json", {**good, "version": "1.0.0"}),
        "length": _write(tmp_path, "l.json", {**good, "coef": good["coef"][:-1]}),
        "std": _write(tmp_path, "s.json", {**good, "scaler_std": [0.0] * len(good["scaler_std"])}),
        "keys": _write(tmp_path, "k.json", {k: v for k, v in good.items() if k != "intercept"}),
    }
    for name, path in cases.items():
        judge, reason = select_judge("logreg", path)
        assert isinstance(judge, RuleJudge) and reason and "규칙 판정기로 대신합니다" in reason, name
    judge, reason = select_judge("logreg", model_path)
    assert isinstance(judge, LogregJudge) and reason is None
    judge, reason = select_judge("rule", model_path)
    assert isinstance(judge, RuleJudge) and reason is None


def test_exploratory_model_needs_permission(tmp_path):
    path = make_model(tmp_path, n_part=4, allow_small=True, name="explore.json")
    judge, reason = select_judge("logreg", path)
    assert isinstance(judge, RuleJudge) and "탐색용 모델" in reason and "ALLOW_EXPLORATORY_MODEL" in reason
    judge, reason = select_judge("logreg", path, allow_exploratory=True)
    assert isinstance(judge, LogregJudge) and judge.exploratory and reason is None
    assert judge.note.startswith("탐색용 모델(검증 부족)입니다.")


# ---------- trial_result와 서버 ----------
def _dummy_trials():
    from server.pipeline.tracker import Tracker, TrialDone, baseline_for
    from server.source.dummy_source import DummyConfig, dummy_items
    tr, out = Tracker("dummy"), []
    for _, line in dummy_items(DummyConfig(trials=3, patterns=["calm", "reactive", "poor"])):
        out += [(u.segment, baseline_for(tr, u.segment)) for u in tr.feed_line(line) if isinstance(u, TrialDone)]
    return out


def test_trial_result_with_logreg(model_path, monkeypatch):
    from server.result import judge_trial
    j = LogregJudge(load_model(model_path))
    seen = []
    orig = LogregJudge.judge
    monkeypatch.setattr(LogregJudge, "judge", lambda self, f, b, q: (seen.append(set(f)), orig(self, f, b, q))[1])
    results = [judge_trial(t, "dummy", j, baseline_segment=b) for t, b in _dummy_trials()]
    assert set(MODEL_FEATURES) <= seen[0]                    # 학습된 판정기는 전체 특징을 받는다
    calm, reactive, poor = results
    for r in (calm, reactive):
        assert r["judge"] == {"name": "logreg", "version": "0.1.0", "trained": True, "exploratory": False}
        assert 0 <= r["score"] <= 1 and r["decision"] in ("truth", "lie")
        assert "학습된 로지스틱 회귀" in r["judge_note"]
        hi = [c["feature"] for c in r["contributions"] if c["over_threshold"]]
        assert len(hi) <= L.MAX_REASONS
    assert poor["decision"] == "inconclusive" and poor["score"] is None    # 품질 보류가 먼저
    json.dumps(results, allow_nan=False)


def test_rule_default_result_format_unchanged():
    from server.result import judge_trial
    t, b = _dummy_trials()[0]
    r = judge_trial(t, "dummy", baseline_segment=b)
    assert r["judge"] == {"name": "rule", "version": "0.0.1", "trained": False}


def test_settings_for_judge():
    s = load_settings({"JUDGE": "logreg", "MODEL_PATH": "x/model.json", "ALLOW_EXPLORATORY_MODEL": "1"})
    assert s.judge == "logreg" and s.allow_exploratory_model
    assert os.path.normpath(s.model_path).endswith(os.path.join("x", "model.json"))
    assert load_settings({}).judge == "rule" and load_settings({}).allow_exploratory_model is False
    with pytest.raises(ValueError):
        load_settings({"ALLOW_EXPLORATORY_MODEL": "maybe"})
    with pytest.raises(ValueError):
        load_settings({"JUDGE": "svm"})


def test_server_runs_with_logreg_and_falls_back(model_path, tmp_path):
    from test_app import _collect, _finished, _results, running_server
    s = Settings(source="dummy", dummy_trials=3, dummy_patterns=["calm", "reactive", "poor"], dummy_speed=0,
                 judge="logreg", model_path=model_path)
    with running_server(s) as port:
        msgs = _collect(port, lambda m: _finished(m) and len(_results(m)) == 3)
    st = [m for m in msgs if m["type"] == "device_status"][-1]
    assert st["judge"]["name"] == "logreg" and st["judge"]["fallback_reason"] is None and st["judge"]["trained"]
    assert all(r["judge"]["name"] == "logreg" for r in _results(msgs))
    s = Settings(source="dummy", dummy_trials=2, dummy_patterns=["calm", "reactive"], dummy_speed=0,
                 judge="logreg", model_path=str(tmp_path / "없음.json"))
    with running_server(s) as port:
        msgs = _collect(port, lambda m: _finished(m) and len(_results(m)) == 2)
    st = [m for m in msgs if m["type"] == "device_status"][-1]
    assert st["judge"]["name"] == "rule" and "규칙 판정기로 대신합니다" in st["judge"]["fallback_reason"]
    assert [r["judge"]["name"] for r in _results(msgs)] == ["rule", "rule"]


def test_cli_with_model(model_path, capsys):
    from server.cli import main
    assert main(["dummy", "--trials", "2", "--patterns", "calm,reactive", "--speed", "0", "--brief",
                 "--judge", "--model", model_path]) == 0
    out = capsys.readouterr().out
    assert "판정기: logreg 0.1.0 (trained=True)" in out and "score=" in out
