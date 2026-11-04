"""규칙 판정기 설정 파일(RULE_CONFIG, S14) 테스트.

설정 파일은 모두 pytest 임시 폴더(tmp_path)에만 만든다. 기본 동작(설정 파일 없음 = 코드 상수 3.0/3.0/1)과
기존 안내 문구가 그대로인지, 검증 정보가 충분할 때만 "검증됨"을 쓰는지 확인한다.
"""

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server.config import Settings, load_settings  # noqa: E402
from server.judges import rule as R  # noqa: E402
from server.judges.logreg import select_judge  # noqa: E402
from server.judges.rule import RuleJudge, load_rule_config, make_rule_judge  # noqa: E402

DEFAULT_NOTE = ("규칙 판정 임계값(FSR 3.0σ, GSR 3.0σ, 1개 센서 이상)은 통계에서 흔히 쓰는 관례로 정한 값이며, "
                "라벨 데이터로 맞추거나 검증하지 않았습니다.")
GOOD_VALIDATION = {"n_participants": 6, "n_trials": 120, "balanced_accuracy": 0.71,
                   "baseline_balanced_accuracy": 0.5, "exploratory": False}


def _write(tmp_path, name, data):
    p = tmp_path / name
    with open(p, "w", encoding="utf-8") as f:
        f.write(data if isinstance(data, str) else json.dumps(data, ensure_ascii=False))
    return str(p)


def cfg(**kw):
    base = {"format": R.CONFIG_FORMAT, "version": "0.1.0", "fsr_th": 2.5, "gsr_th": 3.5, "min_sensors": 1,
            "validated": None}
    base.update(kw)
    return base


# ---------- 기본 동작 불변 ----------
def test_default_unchanged():
    j = RuleJudge()
    assert (j.thresholds, j.min_sensors) == ({"fsr": 3.0, "gsr": 3.0}, 1)
    assert j.note == DEFAULT_NOTE and j.validated is False and j.config_path is None
    j2, reason = make_rule_judge(None)
    assert reason is None and j2.note == DEFAULT_NOTE
    j3, reason = select_judge("rule")
    assert isinstance(j3, RuleJudge) and reason is None and j3.note == DEFAULT_NOTE
    assert load_settings({}).rule_config is None


def test_settings_rule_config():
    s = load_settings({"RULE_CONFIG": "x/rule_config.json"})
    assert os.path.normpath(s.rule_config).endswith(os.path.join("x", "rule_config.json"))
    assert os.path.isabs(s.rule_config)


# ---------- 읽기와 대체 ----------
def test_config_thresholds_used(tmp_path):
    j, reason = make_rule_judge(_write(tmp_path, "c.json", cfg(min_sensors=2)))
    assert reason is None and j.thresholds == {"fsr": 2.5, "gsr": 3.5} and j.min_sensors == 2
    q = {"ok": True}
    assert j.judge({"fsr_local_dz": 2.6, "gsr_local_dz": 0.0}, {}, q).decision == "truth"   # 2개 필요
    assert j.judge({"fsr_local_dz": 2.6, "gsr_local_dz": -3.6}, {}, q).decision == "lie"
    # format/version 없이 숫자만 있어도 읽는다
    j, reason = make_rule_judge(_write(tmp_path, "m.json", {"fsr_th": 2.0, "gsr_th": 2.0, "min_sensors": 1}))
    assert reason is None and j.thresholds == {"fsr": 2.0, "gsr": 2.0}


@pytest.mark.parametrize("name,data", [
    ("broken", "{not json"),
    ("format", cfg(format="other")),
    ("version", cfg(version="9.0")),
    ("missing_key", {"fsr_th": 2.0, "min_sensors": 1}),
    ("negative", cfg(fsr_th=-1)),
    ("zero", cfg(gsr_th=0)),
    ("nan_text", cfg(fsr_th="2.0")),
    ("min3", cfg(min_sensors=3)),
    ("min_float", cfg(min_sensors=1.5)),
    ("list", "[1, 2]"),
])
def test_bad_config_falls_back_to_constants(tmp_path, name, data):
    path = _write(tmp_path, f"{name}.json", data)
    with pytest.raises(R.RuleConfigError):
        load_rule_config(path)
    j, reason = make_rule_judge(path)
    assert (j.thresholds, j.min_sensors, j.config_path) == ({"fsr": 3.0, "gsr": 3.0}, 1, None)
    assert "코드 기본값(FSR 3.0σ, GSR 3.0σ, 1개 센서)으로 판정합니다" in reason
    assert j.note == DEFAULT_NOTE


def test_missing_file_falls_back(tmp_path):
    j, reason = make_rule_judge(str(tmp_path / "없음.json"))
    assert j.config_path is None and "규칙 설정 파일이 없습니다" in reason


def test_logreg_and_rule_config_both_fail(tmp_path):
    j, reason = select_judge("logreg", str(tmp_path / "model.json"), rule_config=str(tmp_path / "rule.json"))
    assert isinstance(j, RuleJudge)
    assert "규칙 판정기로 대신합니다" in reason and "코드 기본값" in reason
    good = _write(tmp_path, "good.json", cfg())
    j, reason = select_judge("logreg", str(tmp_path / "model.json"), rule_config=good)
    assert j.thresholds["fsr"] == 2.5 and "코드 기본값" not in reason


# ---------- "검증됨"은 검증 정보가 충분할 때만 ----------
def test_config_without_validation_is_not_validated(tmp_path):
    j, _ = make_rule_judge(_write(tmp_path, "c.json", cfg()))
    assert not j.validated and "검증됨" not in j.note
    assert j.note == ("규칙 판정 임계값(FSR 2.5σ, GSR 3.5σ, 1개 센서 이상)은 설정 파일에서 정한 값이며, "
                      "라벨 데이터로 검증되지 않았습니다.")
    # 기본값과 같은 임계값의 설정 파일이면 기존 문구 그대로
    j, _ = make_rule_judge(_write(tmp_path, "d.json", cfg(fsr_th=3.0, gsr_th=3.0)))
    assert j.note == DEFAULT_NOTE


@pytest.mark.parametrize("validated", [
    {k: v for k, v in GOOD_VALIDATION.items() if k != "baseline_balanced_accuracy"},   # 기준 모델 대비 없음
    {k: v for k, v in GOOD_VALIDATION.items() if k != "n_trials"},                      # 시도 수 없음
    {**GOOD_VALIDATION, "n_participants": 3},                                           # 참가자 부족
    {**GOOD_VALIDATION, "balanced_accuracy": 0.5},                                      # 기준 모델보다 낫지 않음
    {**GOOD_VALIDATION, "exploratory": True},
    {**GOOD_VALIDATION, "balanced_accuracy": "0.9"},
    "yes",
    True,
])
def test_incomplete_validation_not_shown_as_validated(tmp_path, validated):
    j, reason = make_rule_judge(_write(tmp_path, "c.json", cfg(validated=validated)))
    assert reason is None and not j.validated and "검증됨" not in j.note and "검증되지 않았습니다" in j.note


def test_complete_validation_note(tmp_path):
    j, _ = make_rule_judge(_write(tmp_path, "c.json", cfg(validated=GOOD_VALIDATION)))
    assert j.validated
    assert j.note == ("규칙 판정 임계값(FSR 2.5σ, GSR 3.5σ, 1개 센서 이상)은 라벨 데이터로 검증됨: 참가자 6명, "
                      "시도 120개, 참가자 분리 검증 균형 정확도 0.71 (항상 다수 클래스 기준 0.50). "
                      "정확도를 보장하지 않습니다.")
    assert "확률" not in j.note


# ---------- trial_result, 서버, 명령줄 ----------
def test_trial_result_format_unchanged_with_config(tmp_path):
    from test_logreg_judge import _dummy_trials
    from server.result import judge_trial
    j, _ = make_rule_judge(_write(tmp_path, "c.json", cfg(validated=GOOD_VALIDATION)))
    t, b = _dummy_trials()[0]
    r = judge_trial(t, "dummy", j, baseline_segment=b)
    assert r["judge"] == {"name": "rule", "version": "0.0.1", "trained": False} and r["score"] is None
    assert "검증됨" in r["judge_note"]


def test_server_reports_rule_config(tmp_path):
    from test_app import _collect, _finished, _results, running_server
    path = _write(tmp_path, "c.json", cfg(validated=GOOD_VALIDATION))
    s = Settings(source="dummy", dummy_trials=2, dummy_patterns=["calm", "reactive"], dummy_speed=0, rule_config=path)
    with running_server(s) as port:
        msgs = _collect(port, lambda m: _finished(m) and len(_results(m)) == 2)
    st = [m for m in msgs if m["type"] == "device_status"][-1]["judge"]
    assert st["name"] == "rule" and st["rule_config"] == path and st["validated"] is True
    assert st["fallback_reason"] is None and st["trained"] is False
    assert all("검증됨" in r["judge_note"] for r in _results(msgs))

    s = Settings(source="dummy", dummy_trials=1, dummy_patterns=["calm"], dummy_speed=0,
                 rule_config=str(tmp_path / "없음.json"))
    with running_server(s) as port:
        msgs = _collect(port, lambda m: _finished(m) and len(_results(m)) == 1)
    st = [m for m in msgs if m["type"] == "device_status"][-1]["judge"]
    assert st["rule_config"] is None and st["validated"] is False and "코드 기본값" in st["fallback_reason"]
    assert _results(msgs)[0]["judge_note"] == DEFAULT_NOTE


def test_cli_rule_config(tmp_path, capsys):
    from server.cli import main
    path = _write(tmp_path, "c.json", cfg())
    assert main(["dummy", "--trials", "1", "--patterns", "calm", "--speed", "0", "--brief",
                 "--judge", "--rule-config", path]) == 0
    out = capsys.readouterr().out
    assert "FSR 2.5σ, GSR 3.5σ" in out and "설정 파일에서 정한 값" in out
    assert main(["dummy", "--trials", "1", "--patterns", "calm", "--speed", "0", "--brief",
                 "--judge", "--rule-config", str(tmp_path / "없음.json")]) == 0
    assert "[안내] 규칙 설정 파일을 쓸 수 없어" in capsys.readouterr().out
