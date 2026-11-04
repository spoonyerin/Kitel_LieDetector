"""평가 리포트 (S14): 로지스틱 회귀(train_logreg)와 규칙 판정기 임계값 검증(evaluate_rule)을 한 장의 마크다운으로.

사용법 (한 줄):
    .venv\\Scripts\\python.exe -m training.report                       (data/real/experiments → training/out/report.md)
    .venv\\Scripts\\python.exe -m training.report --data training/out/dataset.csv --out training/out/report.md

- 평가만 한다. model.json과 rule_config.json은 쓰지 않는다 (만들려면 각 스크립트를 따로 실행).
- 내용: 데이터 요약(참가자 수, 시도 수, 제외 사유), 참가자별 시도 수, 모델별 지표, 혼동행렬,
  내보내기 조건 충족 여부, 한계와 "정확도를 보장하지 않음" 안내.
- 확정 데이터가 0건이면 "데이터 없음" 리포트를 쓰고 정상 종료한다.
- 참가자는 수집 때 쓴 별칭(participant_id)으로만 적는다. 출력은 data/real, collector 안에 쓰지 않는다.
"""

import argparse
import datetime as dt
import math
import os
import sys
from collections import Counter

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from training import evaluate_rule as E  # noqa: E402
from training import train_logreg as T  # noqa: E402
from training.build_dataset import (DEFAULT_OUT_DIR, DEFAULT_ROOT, EXCLUDE_REASONS, REASON_KR,  # noqa: E402
                                    _inside_protected, build_rows, read_dataset)

DEFAULT_OUT = os.path.join(DEFAULT_OUT_DIR, "report.md")
DISCLAIMER = ("오락용 게임의 평가 결과입니다. 이 숫자는 정확도를 보장하지 않으며, 판정과 이유는 "
              "통계적 설명일 뿐 거짓의 증명이 아닙니다.")
LOGREG_REASON = {
    "no_data": "데이터 없음", "dummy_rows": "더미 행이 섞여 거부",
    "cannot_evaluate": "평가할 수 없음 (진실·거짓이 모두 있고 참가자가 2명 이상이어야 함)",
    "not_better_than_baseline": "기준 모델보다 낫지 않음 → model.json을 만들지 않음 (규칙 판정기로 시연)",
    "too_few_participants": f"참가자 {T.MIN_PARTICIPANTS}명 미만 → 탐색용 (model.json을 만들지 않음)",
}
RULE_REASON = {
    "no_data": "데이터 없음", "dummy_rows": "더미 행이 섞여 거부",
    "cannot_evaluate": "평가할 수 없음 (진실·거짓이 모두 있고 참가자가 2명 이상이어야 함)",
    "not_better_than_baseline": "항상 다수 기준 모델보다 낫지 않음 → 설정 파일을 제안하지 않음 (기본값 3.0σ 유지)",
    "too_few_participants": f"참가자 {T.MIN_PARTICIPANTS}명 미만 → 탐색용 (설정 파일을 제안하지 않음)",
}


def _f(v):
    return "—" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v:.3f}"


def _metric_rows(items):
    out = ["| 모델 | 평가 시도 | 균형 정확도 | 거짓 검출률 | 진실→거짓 오판율 |", "|---|---:|---:|---:|---:|"]
    for name, n, m in items:
        out.append(f"| {name} | {n} | {_f(m['balanced_accuracy'])} | {_f(m['lie_detection_rate'])} | "
                   f"{_f(m['truth_as_lie_rate'])} |")
    return out


def _confusion(name, m):
    c = m["confusion"]
    return [f"**{name}**", "", "| 실제 \\ 판정 | 진실 | 거짓 |", "|---|---:|---:|",
            f"| 진실 | {c['truth_pred_truth']} | {c['truth_pred_lie']} |",
            f"| 거짓 | {c['lie_pred_truth']} | {c['lie_pred_lie']} |", ""]


def build_report(rows, excluded=None, n_runs=None, source_desc="", today=None):
    """(마크다운 문자열, {"logreg": 결과, "rule": 결과}). 파일은 쓰지 않는다."""
    today = today or dt.date.today().isoformat()
    L = [f"# TruthPulse 평가 리포트", "", f"- 만든 날: {today}", f"- 입력: {source_desc}", "", f"> {DISCLAIMER}", ""]

    # 1. 데이터 요약
    n_ex = sum(excluded.values()) if excluded is not None else None
    L += ["## 1. 데이터 요약", ""]
    if n_runs is not None:
        L.append(f"- run {n_runs}개, 시도 {len(rows) + (n_ex or 0)}개 중 평가에 쓴 시도 {len(rows)}개")
    else:
        L.append(f"- 데이터셋 파일의 시도 {len(rows)}개 (제외 사유는 데이터셋을 만들 때의 출력에 있음)")
    parts = Counter(str(r["participant_id"]) for r in rows)
    labels = Counter(int(r["label"]) for r in rows)
    L.append(f"- 참가자 {len(parts)}명, 진실 {labels.get(0, 0)}개, 거짓 {labels.get(1, 0)}개")
    if excluded:
        L += ["", "| 제외 사유 | 개수 |", "|---|---:|"]
        L += [f"| {REASON_KR[r]} (`{r}`) | {excluded[r]} |" for r in EXCLUDE_REASONS if excluded.get(r)]
    L.append("")

    if not rows:
        L += ["## 데이터 없음", "",
              "라벨이 확정된 본 질문 시도가 0건이라 평가하지 않았습니다. 수집기로 본 질문을 모으고 세션 후 확인 단계에서 "
              "라벨을 확정한 뒤 다시 실행하세요.", ""]
        L += _limits()
        return "\n".join(L), {"logreg": {"reason": "no_data"}, "rule": {"reason": "no_data"}}

    # 2. 참가자별 시도 수
    L += ["## 2. 참가자별 시도 수", "", "| 참가자 | 시도 | 진실 | 거짓 |", "|---|---:|---:|---:|"]
    for p in sorted(parts):
        ys = [int(r["label"]) for r in rows if str(r["participant_id"]) == p]
        L.append(f"| {p} | {len(ys)} | {ys.count(0)} | {ys.count(1)} |")
    L.append("")

    # 평가 (파일을 쓰지 않음)
    quiet = []
    ok_lr, lr = T.train(rows, None, log=quiet.append)
    ok_rule, ru = E.evaluate(rows, None, log=quiet.append)

    # 3. 모델별 지표
    L += ["## 3. 모델별 지표 (참가자 분리 검증)", ""]
    items = []
    if "metrics" in lr:
        n = len(rows)
        items += [("로지스틱 회귀", n, lr["metrics"]["logreg"]),
                  ("기준: 항상 다수 클래스", n, lr["metrics"]["majority"]),
                  ("기준: 규칙 판정기 3.0σ (보류는 진실로 셈)", n, lr["metrics"]["rule"])]
    if "metrics" in ru:
        n = ru["n_used"]
        items += [("규칙 판정기, 임계값 중첩 선택", n, ru["metrics"]["rule_selected"]),
                  ("규칙 판정기 현재 기본값 3.0σ", n, ru["metrics"]["rule_default"]),
                  ("기준: 항상 다수 클래스 (규칙 평가 시도)", n, ru["metrics"]["majority"])]
    if items:
        L += _metric_rows(items)
        L.append("")
        scheme = lr.get("scheme") or ru.get("scheme")
        L.append(f"- 검증 방식: {scheme} (참가자 단위). 로지스틱 회귀의 전처리·C 선택과 규칙 판정기의 임계값 선택은 "
                 "학습 폴드 참가자로만 했습니다.")
        if ru.get("n_no_feature"):
            L.append(f"- 국소 dz가 없는 시도 {ru['n_no_feature']}개는 규칙 판정기 평가에서 뺐습니다.")
        L.append("")
    else:
        L += ["평가할 수 없습니다: " + LOGREG_REASON.get(lr.get("reason"), str(lr.get("reason"))), ""]

    # 4. 혼동행렬
    if items:
        L += ["## 4. 혼동행렬", ""]
        for name, _n, m in items:
            L += _confusion(name, m)

    # 5. 결과물
    L += ["## 5. 내보내기 조건", ""]
    L.append("- 로지스틱 회귀 model.json: " + ("조건 충족" + (" (탐색용)" if lr.get("small") else "")
                                           if lr.get("exportable") else LOGREG_REASON.get(lr.get("reason"), "—")))
    if ru.get("exportable"):
        f, g, k = ru["params"]
        L.append(f"- 규칙 판정기 설정 제안: 조건 충족 — FSR {f}σ, GSR {g}σ, {k}개 센서 이상 "
                 "(`python -m training.evaluate_rule`로 파일을 만들고 RULE_CONFIG로 직접 적용)")
    else:
        L.append("- 규칙 판정기 설정 제안: " + RULE_REASON.get(ru.get("reason"), "—"))
    if ru.get("folds"):
        chosen = Counter(tuple(fd["params"]) for fd in ru["folds"])
        L.append("- 폴드별로 고른 임계값 (FSR, GSR, 최소 센서): " +
                 ", ".join(f"{p} ×{n}" for p, n in sorted(chosen.items(), key=lambda kv: -kv[1])))
    L.append("")
    L += _limits(small=lr.get("small") or ru.get("small"))
    return "\n".join(L), {"logreg": lr, "rule": ru}


def _limits(small=False):
    out = ["## 한계와 안내", ""]
    if small:
        out.append(f"- **{T.SMALL_BANNER}** (참가자 {T.MIN_PARTICIPANTS}명 미만).")
    out += [f"- {DISCLAIMER}",
            "- 참가자 수와 시도 수가 적으면 지표가 크게 흔들립니다. 신뢰 구간은 계산하지 않았습니다.",
            "- 거짓 점수의 보정(점수가 실제 비율과 맞는지)은 평가하지 않았습니다.",
            "- 질문 은행·실험 환경·참가자 구성이 바뀌면 결과가 달라질 수 있습니다.",
            "- 특징 상수(클리핑, 하한, 구간 길이)는 임시값이며 이 리포트에서 다시 정하지 않았습니다.", ""]
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="training.report", description="평가 리포트 (마크다운)")
    ap.add_argument("--data", default=None, help="build_dataset이 만든 CSV (없으면 --root에서 바로 만든다)")
    ap.add_argument("--root", default=DEFAULT_ROOT, help="experiments 폴더 (읽기 전용)")
    ap.add_argument("--out", default=DEFAULT_OUT, help="리포트 경로 (data/real, collector 안은 거부)")
    args = ap.parse_args(argv)
    bad = _inside_protected(args.out)
    if bad:
        print(f"[거부] 출력 경로가 {bad}/ 안입니다: {args.out}")
        return 2
    if args.data:
        rows, excluded, n_runs, desc = read_dataset(args.data), None, None, f"데이터셋 {os.path.basename(args.data)}"
    else:
        rows, excluded, n_runs = build_rows(args.root)
        desc = f"collector 기록 ({os.path.basename(os.path.normpath(args.root))})"
    text, _ = build_report(rows, excluded, n_runs, desc)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(text)
    print(("데이터 없음: 라벨이 확정된 시도가 0건입니다. " if not rows else "") + f"리포트 → {os.path.abspath(args.out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
