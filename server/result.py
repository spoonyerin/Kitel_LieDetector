"""시도 하나를 판정하고 PRD 9절 형식의 trial_result를 만든다.

흐름: TrialSegment → 국소 dz 특징 → 품질 검사 → 판정기 → 이유 문장
정답 라벨은 어디에도 들어가지 않는다. 학습되지 않은 판정기는 score=None이다.
"""

from typing import Optional

from server.judges.rule import RuleJudge
from server.pipeline.features import json_features, local_features
from server.pipeline.quality import assess
from server.reasons import make_reasons

DISCLAIMER = "오락용 게임입니다. 이유는 통계적 설명이며 거짓의 증명이 아닙니다."


def judge_trial(trial, source: str, judge: RuleJudge = None,
                session_id: Optional[str] = None, participant_id: Optional[str] = None,
                question_id: Optional[str] = None) -> dict:
    """trial: server.pipeline.tracker.TrialSegment."""
    if source not in ("dummy", "hardware"):
        raise ValueError("source는 dummy 또는 hardware여야 한다")
    judge = judge or RuleJudge()
    summary_values = trial.summary.values if trial.summary else None
    feat = local_features(trial.samples, summary_values)
    quality = assess(trial, feat.problems)
    baseline = {}
    if summary_values:
        baseline = {k: summary_values.get(k) for k in ("bl_n", "bl_fsr_mean", "bl_fsr_sd",
                                                      "bl_gsr_mean", "bl_gsr_sd", "bl_ir_mean")}
    verdict = judge.judge(feat.features, baseline, quality.as_dict())
    reasons = make_reasons(verdict, judge)
    return {
        "type": "trial_result",
        "source": source,
        "session_id": session_id or f"S{trial.session_index:02d}",
        "participant_id": participant_id,
        "trial_id": trial.trial,
        "baseline_id": trial.baseline_id,
        "question_id": question_id,
        "device_status": trial.status,
        "decision": verdict.decision,
        "score": verdict.score,
        "judge": {"name": verdict.judge_name, "version": verdict.judge_version, "trained": verdict.trained},
        "judge_note": judge.note,
        "quality": {"ok": quality.ok, "flags": quality.flags, "warnings": quality.warnings},
        "features": json_features(feat.features),
        "contributions": [
            {"sensor": c.sensor, "feature": c.feature, "value": c.value, "contribution": c.contribution,
             "direction": "up" if c.value > 0 else "down",
             "over_threshold": abs(c.value) >= judge.thresholds[c.sensor]}
            for c in verdict.contributions
        ],
        "reasons": reasons,
        "hold_reason": reasons[0] if verdict.decision == "inconclusive" else None,
        "hold_code": verdict.hold_reason,
        "disclaimer": DISCLAIMER,
        "device_verdict": None,
    }
