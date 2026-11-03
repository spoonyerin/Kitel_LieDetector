"""판정기(server/judges) 테스트: 형식, 임계값 경계, 보류, 라벨 독립성."""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server.judges import rule as R  # noqa: E402
from server.judges.base import Contribution, Verdict, check_no_labels  # noqa: E402
from server.judges.rule import RuleJudge  # noqa: E402

OK = {"ok": True, "hold_code": None, "flags": [], "warnings": []}


def _f(fsr, gsr):
    return {"fsr_local_dz": fsr, "gsr_local_dz": gsr, "fsr_dz": -20.0, "gsr_dz": 20.0}


def test_constants_as_decided():
    assert (R.FSR_LOCAL_DZ_THRESHOLD, R.GSR_LOCAL_DZ_THRESHOLD, R.MIN_SENSORS_OVER) == (3.0, 3.0, 1)


def test_identity_and_output_format():
    j = RuleJudge()
    assert (j.name, j.version, j.trained) == ("rule", "0.0.1", False)
    v = j.judge(_f(1.0, -0.5), {}, OK)
    assert isinstance(v, Verdict) and v.decision == "truth" and v.score is None and v.trained is False
    assert (v.judge_name, v.judge_version) == ("rule", "0.0.1")
    assert all(isinstance(c, Contribution) for c in v.contributions)
    assert [(c.sensor, c.feature) for c in v.contributions] == [("fsr", "fsr_local_dz"), ("gsr", "gsr_local_dz")]
    assert "라벨 데이터로 맞추거나 검증하지 않았습니다" in j.note


def test_threshold_boundary():
    j = RuleJudge()
    assert j.judge(_f(2.999, 0), {}, OK).decision == "truth"
    assert j.judge(_f(-3.0, 0), {}, OK).decision == "lie"     # 같을 때 포함, 방향 무관
    assert j.judge(_f(0, 3.0), {}, OK).decision == "lie"


def test_min_sensors_two():
    j = RuleJudge(min_sensors=2)
    assert j.judge(_f(5.0, 0.1), {}, OK).decision == "truth"
    assert j.judge(_f(5.0, -3.5), {}, OK).decision == "lie"


def test_uses_local_dz_not_device_dz():
    """기기 dz가 아무리 커도 국소 dz가 작으면 truth다."""
    v = RuleJudge().judge({"fsr_local_dz": 0.2, "gsr_local_dz": 0.1, "fsr_dz": -32.0, "gsr_dz": 20.0}, {}, OK)
    assert v.decision == "truth"


def test_contribution_values():
    v = RuleJudge().judge(_f(-6.0, 1.5), {}, OK)
    fsr, gsr = v.contributions
    assert fsr.value == -6.0 and fsr.contribution == pytest.approx(2.0)
    assert gsr.contribution == pytest.approx(0.5)
    assert [c.sensor for c in RuleJudge().exceeded(v)] == ["fsr"]


def test_holds():
    j = RuleJudge()
    v = j.judge(_f(9, 9), {}, {"ok": False, "hold_code": "CONTACT_EVENT:CONTACT_SHIFT"})
    assert (v.decision, v.hold_reason, v.score, v.contributions) == \
        ("inconclusive", "CONTACT_EVENT:CONTACT_SHIFT", None, [])
    assert j.judge(_f(float("nan"), 1), {}, OK).hold_reason == "FEATURE_NAN"
    unset = RuleJudge(fsr_threshold=None, gsr_threshold=3.0, min_sensors=1, use_defaults=False)
    assert not unset.configured
    assert unset.judge(_f(9, 9), {}, OK).hold_reason == "THRESHOLD_UNSET"
    assert "정해지지 않아" in unset.note


def test_label_keys_rejected():
    j = RuleJudge()
    for bad in ({**_f(1, 1), "condition": "lie"}, {**_f(1, 1), "ground_truth": 1}):
        with pytest.raises(ValueError):
            j.judge(bad, {}, OK)
    with pytest.raises(ValueError):
        j.judge(_f(1, 1), {"meta": {"planned_condition": "truth"}}, OK)
    with pytest.raises(ValueError):
        check_no_labels({"marked_lie": 1})


def test_label_independence():
    """같은 신호면 (나중에 붙는) 라벨이 무엇이든 판정은 같다: 판정 입력에는 라벨이 들어갈 자리가 없다."""
    j = RuleJudge()
    feats = _f(-3.7, -2.8)
    results = {label: j.judge(dict(feats), {}, dict(OK)) for label in ("truth", "lie", None)}
    decisions = {(v.decision, tuple((c.value, c.contribution) for c in v.contributions)) for v in results.values()}
    assert len(decisions) == 1
