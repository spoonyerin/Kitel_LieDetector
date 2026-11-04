"""규칙 판정기 임계값 검증 (S14, M5).

사용법 (한 줄):
    .venv\\Scripts\\python.exe -m training.evaluate_rule                 (data/real/experiments에서 데이터셋을 만들어 평가)
    .venv\\Scripts\\python.exe -m training.evaluate_rule --data training/out/dataset.csv --out training/out/rule_config.json
    --allow-small : 참가자가 5명 미만이어도 탐색용 설정 파일을 만든다 (validated=null, 검증됨으로 표시되지 않음)

방법 (낙관적 편향 방지를 위한 중첩 검증):
    - 바깥: 참가자 단위 분리 (10명 이하면 한 명씩 빼기, 많으면 GroupKFold(5), train_logreg와 같은 규칙).
    - 안쪽: 학습 폴드의 참가자 시도만으로 격자(FSR·GSR 임계값 1.5~4.5, 0.5 간격 × 최소 센서 수 1·2)를 훑어
      균형 정확도가 가장 높은 조합을 고른다. 빼 둔 참가자는 고르는 데 쓰지 않고, 고른 임계값으로 평가만 한다.
    - 같은 점수면: 진실→거짓 오판율이 낮은 쪽, 현재 기본값(3.0/3.0/1)에 가까운 쪽.
    - 기준 모델: 항상 다수 클래스 (학습 폴드의 다수). 참고로 현재 기본값(3.0σ, 고르지 않음)도 같이 보고한다.
    - 특징(국소 dz)이 없는 시도는 규칙 판정기가 보류하므로 평가에서 빼고 개수를 알린다.
지표: 균형 정확도, 거짓 검출률, 진실→거짓 오판율, 혼동행렬.

안전장치:
    - 참가자 5명 미만: "탐색용 결과이며 채택하지 않습니다"를 크게 출력하고 설정 파일을 만들지 않는다
      (--allow-small이면 validated=null인 탐색용 파일만 만든다).
    - 중첩 검증의 균형 정확도가 항상 다수 기준 모델보다 높지 않으면 설정 파일을 제안하지 않고 이유를 출력한다.
    - 제안 설정은 training/out/rule_config.json에만 쓴다. 서버에 자동 적용하지 않는다 (RULE_CONFIG로 직접 지정).
    - 출력은 data/real, collector 안에 쓰지 않는다. 정답 라벨은 평가에만 쓰고 판정 입력에는 넣지 않는다.
"""

import argparse
import datetime as dt
import json
import math
import os
import sys
from collections import Counter

import numpy as np

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from server.judges import rule as R  # noqa: E402
from training.build_dataset import (DEFAULT_OUT_DIR, DEFAULT_ROOT, _inside_protected, build_rows,  # noqa: E402
                                    print_report, read_dataset)
from training.train_logreg import MIN_PARTICIPANTS, SMALL_BANNER, _splits, metrics  # noqa: E402

CONFIG_VERSION = "0.1.0"
GRID_TH = tuple(round(1.5 + 0.5 * i, 1) for i in range(7))      # 1.5, 2.0, ..., 4.5
GRID_MIN = (1, 2)
DEFAULT_PARAMS = (R.FSR_LOCAL_DZ_THRESHOLD, R.GSR_LOCAL_DZ_THRESHOLD, R.MIN_SENSORS_OVER)
DEFAULT_OUT = os.path.join(DEFAULT_OUT_DIR, "rule_config.json")


def usable(rows):
    """(국소 dz가 둘 다 있는 행, 뺀 행 수)."""
    ok = [r for r in rows if all(isinstance(r.get(k), (int, float)) and math.isfinite(r[k])
                                 for k in ("fsr_local_dz", "gsr_local_dz"))]
    return ok, len(rows) - len(ok)


def rule_predict(F, G, params):
    """RuleJudge와 같은 규칙: |dz| >= 임계값인 센서가 min_sensors개 이상이면 거짓(1)."""
    fsr_th, gsr_th, min_sensors = params
    n_over = (np.abs(F) >= fsr_th).astype(int) + (np.abs(G) >= gsr_th).astype(int)
    return (n_over >= min_sensors).astype(int)


def _tie_key(params, m):
    f, g, k = params
    tar = m["truth_as_lie_rate"]
    dist = (f - DEFAULT_PARAMS[0]) ** 2 + (g - DEFAULT_PARAMS[1]) ** 2 + abs(k - DEFAULT_PARAMS[2])
    return (-m["balanced_accuracy"], tar if not math.isnan(tar) else 1.0, dist, f, g, k)


def select_params(F, G, y):
    """주어진 시도만으로 격자에서 균형 정확도 최대 조합을 고른다. 한 클래스뿐이면 기본값."""
    if len(set(y.tolist())) < 2:
        return DEFAULT_PARAMS
    best = None
    for f in GRID_TH:
        for g in GRID_TH:
            for k in GRID_MIN:
                key = _tie_key((f, g, k), metrics(y, rule_predict(F, G, (f, g, k))))
                if best is None or key < best[0]:
                    best = (key, (f, g, k))
    return best[1]


def nested_eval(F, G, y, groups):
    """바깥 참가자 분리 × 안쪽(학습 폴드만) 임계값 선택. 빼 둔 참가자는 평가만 한다."""
    splitter, scheme = _splits(groups)
    pred = {"rule_selected": np.zeros(len(y), dtype=int), "majority": np.zeros(len(y), dtype=int),
            "rule_default": rule_predict(F, G, DEFAULT_PARAMS)}
    folds = []
    for tr, te in splitter.split(F, y, groups):
        params = select_params(F[tr], G[tr], y[tr])
        pred["rule_selected"][te] = rule_predict(F[te], G[te], params)
        pred["majority"][te] = int(sorted(Counter(y[tr].tolist()).items(), key=lambda kv: (-kv[1], kv[0]))[0][0])
        folds.append({"train_groups": sorted(set(groups[tr].tolist())), "test_groups": sorted(set(groups[te].tolist())),
                      "params": list(params)})
    return pred, folds, scheme


LABEL_KR = {"rule_selected": "규칙 판정기 (임계값 중첩 선택)", "rule_default": "규칙 판정기 현재 기본값 (참고)",
            "majority": "기준: 항상 다수 클래스"}


def _fmt(v):
    return "—" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v:.3f}"


def evaluate(rows, out_path=None, allow_small=False, log=print):
    """반환: (설정 파일을 썼는지, 결과 dict). out_path가 비어 있으면 평가만 한다 (리포트용)."""
    if not rows:
        log("평가할 데이터가 없습니다. (라벨이 확정된 본 질문 시도가 0건)")
        return False, {"reason": "no_data"}
    if any(str(r.get("source", "hardware")) != "hardware" for r in rows):
        log("평가를 거부합니다: 더미(source가 hardware가 아닌) 행이 섞여 있습니다.")
        return False, {"reason": "dummy_rows"}
    ok_rows, n_nan = usable(rows)
    if n_nan:
        log(f"국소 dz가 없는 시도 {n_nan}개는 규칙 판정기가 보류하므로 평가에서 뺍니다.")
    F = np.array([r["fsr_local_dz"] for r in ok_rows], dtype=float)
    G = np.array([r["gsr_local_dz"] for r in ok_rows], dtype=float)
    y = np.array([int(r["label"]) for r in ok_rows], dtype=int)
    groups = np.array([str(r["participant_id"]) for r in ok_rows])
    n_part = len(set(groups.tolist()))
    base = {"n_rows": len(rows), "n_used": len(ok_rows), "n_no_feature": n_nan, "n_participants": n_part,
            "n_truth": int(np.sum(y == 0)), "n_lie": int(np.sum(y == 1))}
    if len(set(y.tolist())) < 2 or n_part < 2:
        log("평가할 수 없습니다: 진실과 거짓이 모두 있고 참가자가 2명 이상이어야 합니다. 설정 파일을 만들지 않습니다.")
        return False, {**base, "reason": "cannot_evaluate"}
    small = n_part < MIN_PARTICIPANTS
    if small:
        bar = "!" * 60
        log(f"\n{bar}\n!!  {SMALL_BANNER} (참가자 {n_part}명 < {MIN_PARTICIPANTS}명)\n{bar}\n")

    pred, folds, scheme = nested_eval(F, G, y, groups)
    res = {k: metrics(y, p) for k, p in pred.items()}
    log(f"검증: {scheme}, 폴드 {len(folds)}개 (임계값은 학습 폴드 참가자로만 고르고, 빼 둔 참가자로 평가)")
    for k in ("rule_selected", "majority", "rule_default"):
        m, c = res[k], res[k]["confusion"]
        log(f"  {LABEL_KR[k]:<26} 균형정확도 {_fmt(m['balanced_accuracy'])}  거짓검출 {_fmt(m['lie_detection_rate'])}  "
            f"진실→거짓 {_fmt(m['truth_as_lie_rate'])}  혼동[진실→진실 {c['truth_pred_truth']}, 진실→거짓 "
            f"{c['truth_pred_lie']}, 거짓→진실 {c['lie_pred_truth']}, 거짓→거짓 {c['lie_pred_lie']}]")
    chosen = Counter(tuple(f["params"]) for f in folds)
    log("폴드별로 고른 임계값 (FSR, GSR, 최소 센서): " +
        ", ".join(f"{p} ×{n}" for p, n in sorted(chosen.items(), key=lambda kv: -kv[1])))
    final = select_params(F, G, y)
    result = {**base, "metrics": res, "scheme": scheme, "folds": folds, "small": small, "params": list(final)}

    ba, ba_base = res["rule_selected"]["balanced_accuracy"], res["majority"]["balanced_accuracy"]
    if not ba > ba_base:
        log(f"설정 파일을 제안하지 않습니다: 중첩 검증 균형 정확도({_fmt(ba)})가 항상 다수 기준 모델({_fmt(ba_base)})보다 "
            "높지 않습니다. 현재 기본값(3.0σ)을 유지하세요.")
        result["reason"] = "not_better_than_baseline"
        return False, result
    if small and not allow_small:
        log(f"설정 파일을 만들지 않습니다: 참가자 {n_part}명 < {MIN_PARTICIPANTS}명. "
            "탐색용으로만 보려면 --allow-small을 주세요 (검증됨으로 표시되지 않습니다).")
        result["reason"] = "too_few_participants"
        return False, result

    evaluation = {"n_participants": n_part, "n_trials": int(len(y)), "n_truth": base["n_truth"],
                  "n_lie": base["n_lie"], "n_no_feature": n_nan, "cv": scheme, "method": "nested-participant-split",
                  "balanced_accuracy": ba, "lie_detection_rate": res["rule_selected"]["lie_detection_rate"],
                  "truth_as_lie_rate": res["rule_selected"]["truth_as_lie_rate"],
                  "baseline": "majority", "baseline_balanced_accuracy": ba_base,
                  "default_rule_balanced_accuracy": res["rule_default"]["balanced_accuracy"],
                  "confusion": res["rule_selected"]["confusion"], "exploratory": bool(small)}
    payload = {
        "format": R.CONFIG_FORMAT, "version": CONFIG_VERSION, "created": dt.date.today().isoformat(),
        "fsr_th": final[0], "gsr_th": final[1], "min_sensors": final[2],
        "selection": "전체 확정 시도에서 격자 중 균형 정확도 최대 (평가 지표는 중첩 검증 값)",
        "grid": {"thresholds": list(GRID_TH), "min_sensors": list(GRID_MIN)},
        # 검증됨 표시는 참가자가 충분할 때만 (탐색용이면 null → 서버는 "검증되지 않았습니다"로 안내)
        "validated": None if small else evaluation,
        "evaluation": evaluation,
        "note": "자동으로 적용되지 않습니다. 쓰려면 RULE_CONFIG 환경 변수로 이 파일을 지정하세요.",
    }
    result["config"] = payload
    result["exportable"] = True
    if not out_path:
        log(f"설정 파일 제안 조건을 만족합니다: FSR {final[0]}σ, GSR {final[1]}σ, {final[2]}개 센서 (이번 실행은 파일을 쓰지 않음)")
        return False, result
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    log(f"제안 설정 → {os.path.abspath(out_path)} (FSR {final[0]}σ, GSR {final[1]}σ, {final[2]}개 센서"
        + (", 탐색용 validated=null" if small else "") + ")")
    log("서버에 자동 적용하지 않았습니다. 적용하려면: $env:RULE_CONFIG=\"" + os.path.abspath(out_path) + "\"")
    return True, result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="training.evaluate_rule", description="규칙 판정기 임계값 중첩 검증")
    ap.add_argument("--data", default=None, help="build_dataset이 만든 CSV (없으면 --root에서 바로 만든다)")
    ap.add_argument("--root", default=DEFAULT_ROOT, help="experiments 폴더 (읽기 전용)")
    ap.add_argument("--out", default=DEFAULT_OUT, help="제안 설정 JSON 경로 (data/real, collector 안은 거부)")
    ap.add_argument("--allow-small", action="store_true", help="참가자 5명 미만도 탐색용 설정 파일을 만든다")
    args = ap.parse_args(argv)
    bad = _inside_protected(args.out)
    if bad:
        print(f"[거부] 출력 경로가 {bad}/ 안입니다: {args.out}")
        return 2
    if args.data:
        rows = read_dataset(args.data)
        print(f"데이터셋 {args.data}: 시도 {len(rows)}개")
    else:
        rows, excluded, n_runs = build_rows(args.root)
        print_report(rows, excluded, n_runs)
    evaluate(rows, args.out, allow_small=args.allow_small)
    return 0


if __name__ == "__main__":
    sys.exit(main())
