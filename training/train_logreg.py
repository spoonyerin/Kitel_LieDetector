"""로지스틱 회귀 학습·평가·model.json 내보내기 (S12).

사용법 (한 줄):
    .venv\\Scripts\\python.exe -m training.train_logreg                 (data/real/experiments에서 데이터셋을 만들어 학습)
    .venv\\Scripts\\python.exe -m training.train_logreg --data training/out/dataset.csv --out training/out/model.json
    --allow-small : 참가자가 5명 미만이어도 탐색용 model.json을 만든다 (exploratory=true, 채택하지 않음)

검증: 참가자 단위 분리. 참가자가 10명 이하면 한 명씩 빼기(LeaveOneGroupOut), 많으면 GroupKFold(5).
    결측 처리(중앙값), 특징 선택(결측 비율·분산), 표준화, 클리핑, C 선택은 모두 학습 폴드 안에서만 한다
    (FoldPreprocessor.fit에는 학습 폴드 행만 들어간다. 테스트 폴드는 transform만 한다).
모델: L2 로지스틱 회귀, class_weight=balanced (클래스 불균형 보정), 임계값 0.5.
기준 모델: (1) 항상 다수 클래스 (학습 폴드의 다수), (2) RuleJudge(국소 dz 3.0σ) 판정 그대로.
지표: 균형 정확도, 거짓 검출률(거짓→거짓), 진실을 거짓으로 오판한 비율, 혼동행렬, 참가자별 시도 수.

안전장치:
    - 참가자 5명 미만: "탐색용 결과이며 채택하지 않습니다"를 크게 출력하고 model.json을 만들지 않는다
      (--allow-small이면 exploratory=true로만 만든다).
    - 로지스틱 회귀의 균형 정확도가 두 기준 모델보다 모두 높지 않으면 model.json을 만들지 않고 이유를 출력한다.
    - model.json에는 원시 데이터와 참가자 정보(ID, run 이름)를 넣지 않는다.
    - 출력은 data/real, collector 안에 쓰지 않는다. 기본 출력은 training/out/ (gitignore 대상).
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

from training.build_dataset import (DEFAULT_OUT_DIR, DEFAULT_ROOT, MODEL_FEATURES, _inside_protected,  # noqa: E402
                                    build_rows, print_report, read_dataset)

MODEL_VERSION = "0.1.0"
MIN_PARTICIPANTS = 5          # 이보다 적으면 탐색용 (채택하지 않음)
C_GRID = (0.01, 0.1, 1.0, 10.0)
DEFAULT_C = 1.0               # 안쪽 검증을 할 수 없을 때 (학습 폴드 참가자 3명 미만)
MAX_MISSING_FRAC = 0.5        # 학습 폴드에서 결측이 이보다 많은 특징은 뺀다 (임시값)
STD_CLIP = 5.0                # 표준화 뒤 클리핑 범위 ± (임시값)
THRESHOLD = 0.5
LOGO_MAX_GROUPS = 10
SMALL_BANNER = "탐색용 결과이며 채택하지 않습니다"


# ----------------------------------------------------------
# 폴드 안 전처리
# ----------------------------------------------------------
class FoldPreprocessor:
    """학습 폴드에서만 fit: 특징 선택(결측 비율, 분산) → 중앙값 대체 → 표준화 → 클리핑."""

    def __init__(self, names):
        self.names = list(names)

    def fit(self, X):
        X = np.asarray(X, dtype=float)
        keep = []
        for j in range(X.shape[1]):
            col = X[:, j]
            ok = ~np.isnan(col)
            if ok.sum() < 2 or 1 - ok.mean() > MAX_MISSING_FRAC or np.nanstd(col) < 1e-9:
                continue
            keep.append(j)
        self.keep = keep
        Xk = X[:, keep]
        self.median = np.nanmedian(Xk, axis=0) if keep else np.array([])
        filled = np.where(np.isnan(Xk), self.median, Xk)
        self.mean = filled.mean(axis=0) if keep else np.array([])
        self.std = filled.std(axis=0) if keep else np.array([])
        self.std = np.where(self.std < 1e-9, 1.0, self.std)
        return self

    def transform(self, X):
        Xk = np.asarray(X, dtype=float)[:, self.keep]
        filled = np.where(np.isnan(Xk), self.median, Xk)
        return np.clip((filled - self.mean) / self.std, -STD_CLIP, STD_CLIP)

    @property
    def kept_names(self):
        return [self.names[j] for j in self.keep]


def _logreg(C):
    from sklearn.linear_model import LogisticRegression
    return LogisticRegression(C=C, class_weight="balanced", max_iter=2000)


def _splits(groups):
    from sklearn.model_selection import GroupKFold, LeaveOneGroupOut
    n = len(set(groups))
    if n <= LOGO_MAX_GROUPS:
        return LeaveOneGroupOut(), "leave-one-participant-out"
    return GroupKFold(n_splits=5), "group-5-fold"


def _fit_predict(Xtr, ytr, Xte, C, names):
    prep = FoldPreprocessor(names).fit(Xtr)
    if not prep.keep:
        maj = int(Counter(ytr).most_common(1)[0][0])
        return np.full(len(Xte), maj), prep, None
    model = _logreg(C).fit(prep.transform(Xtr), ytr)
    proba = model.predict_proba(prep.transform(Xte))[:, 1]
    return (proba >= THRESHOLD).astype(int), prep, model


def balanced_accuracy(y, p):
    y, p = np.asarray(y), np.asarray(p)
    recalls = [np.mean(p[y == c] == c) for c in (0, 1) if np.any(y == c)]
    return float(np.mean(recalls)) if recalls else math.nan


def choose_C(X, y, groups, names):
    """학습 폴드 안의 안쪽 참가자 분리 검증으로 C를 고른다 (균형 정확도 최대, 같으면 작은 C)."""
    if len(set(groups)) < 3 or len(set(y)) < 2:
        return DEFAULT_C
    splitter, _ = _splits(groups)
    best_C, best = DEFAULT_C, -1.0
    for C in C_GRID:
        pred = np.zeros(len(y), dtype=int)
        for tr, te in splitter.split(X, y, groups):
            if len(set(y[tr])) < 2:
                pred[te] = Counter(y[tr]).most_common(1)[0][0]
                continue
            pred[te], _, _ = _fit_predict(X[tr], y[tr], X[te], C, names)
        score = balanced_accuracy(y, pred)
        if score > best + 1e-12:
            best_C, best = C, score
    return best_C


def outer_cv(X, y, groups, names, rule_pred):
    """참가자 분리 검증. 학습 폴드에서 전처리·C를 정하고 테스트 폴드는 예측만 한다."""
    X, y, groups = np.asarray(X, dtype=float), np.asarray(y, dtype=int), np.asarray(groups)
    splitter, scheme = _splits(groups)
    pred = {"logreg": np.zeros(len(y), dtype=int), "majority": np.zeros(len(y), dtype=int),
            "rule": np.asarray(rule_pred, dtype=int)}
    folds = []
    for tr, te in splitter.split(X, y, groups):
        maj = int(sorted(Counter(y[tr]).items(), key=lambda kv: (-kv[1], kv[0]))[0][0])
        pred["majority"][te] = maj
        if len(set(y[tr])) < 2:
            pred["logreg"][te] = maj
            folds.append({"train_groups": sorted(set(groups[tr])), "test_groups": sorted(set(groups[te])),
                          "C": None, "prep_mean": None, "prep_median": None})
            continue
        C = choose_C(X[tr], y[tr], groups[tr], names)
        pred["logreg"][te], prep, _ = _fit_predict(X[tr], y[tr], X[te], C, names)
        folds.append({"train_groups": sorted(set(groups[tr])), "test_groups": sorted(set(groups[te])), "C": C,
                      "prep_mean": dict(zip(prep.kept_names, prep.mean.tolist())),
                      "prep_median": dict(zip(prep.kept_names, prep.median.tolist()))})
    return pred, folds, scheme


def metrics(y, p):
    y, p = np.asarray(y), np.asarray(p)
    tn = int(np.sum((y == 0) & (p == 0))); fp = int(np.sum((y == 0) & (p == 1)))
    fn = int(np.sum((y == 1) & (p == 0))); tp = int(np.sum((y == 1) & (p == 1)))
    return {"balanced_accuracy": balanced_accuracy(y, p),
            "lie_detection_rate": tp / (tp + fn) if tp + fn else math.nan,
            "truth_as_lie_rate": fp / (fp + tn) if fp + tn else math.nan,
            "confusion": {"truth_pred_truth": tn, "truth_pred_lie": fp, "lie_pred_truth": fn, "lie_pred_lie": tp}}


def filter_settings():
    from server.pipeline import features as F
    from server.pipeline import signal as S
    return {"fsr": {"median": S.MEDIAN_WINDOW, "lowpass_hz": S.FSR_LOWPASS_HZ},
            "gsr": {"median": S.MEDIAN_WINDOW, "lowpass_hz": S.GSR_LOWPASS_HZ},
            "ppg": {"bandpass_hz": list(S.PPG_BAND_HZ)}, "order": S.FILTER_ORDER, "causal": True,
            "baseline_skip_sec": F.BASELINE_SKIP_SEC, "sd_floor": dict(F.SD_FLOORS), "flat_sd": F.FLAT_SD,
            "feature_z_clip": F.Z_CLIP, "feature_ratio_clip": F.RATIO_CLIP, "duration_z": F.DURATION_Z}


# ----------------------------------------------------------
# 학습 전체
# ----------------------------------------------------------
def train(rows, out_path, allow_small=False, log=print):
    """반환: (model.json을 썼는지, 결과 dict). rows는 build_dataset의 행."""
    if not rows:
        log("학습할 데이터가 없습니다. (라벨이 확정된 본 질문 시도가 0건)")
        return False, {"reason": "no_data"}
    if any(str(r.get("source", "hardware")) != "hardware" for r in rows):
        log("학습을 거부합니다: 더미(source가 hardware가 아닌) 행이 섞여 있습니다. 더미 데이터는 학습에 쓰지 않습니다.")
        return False, {"reason": "dummy_rows"}
    names = list(MODEL_FEATURES)
    X = np.array([[r[k] for k in names] for r in rows], dtype=float)
    y = np.array([int(r["label"]) for r in rows])
    groups = np.array([str(r["participant_id"]) for r in rows])
    rule = np.array([1 if r.get("rule_decision") == "lie" else 0 for r in rows])
    n_part = len(set(groups))

    log("참가자별 시도 수 (진실/거짓):")
    for g in sorted(set(groups)):
        m = groups == g
        log(f"  참가자 {g:<8} {int(m.sum()):>3}개 (진실 {int(np.sum(y[m] == 0))}, 거짓 {int(np.sum(y[m] == 1))})")
    if len(set(y)) < 2 or n_part < 2:
        log("평가할 수 없습니다: 진실과 거짓이 모두 있고 참가자가 2명 이상이어야 합니다. model.json을 만들지 않습니다.")
        return False, {"reason": "cannot_evaluate", "n_participants": n_part}
    small = n_part < MIN_PARTICIPANTS
    if small:
        bar = "!" * 60
        log(f"\n{bar}\n!!  {SMALL_BANNER} (참가자 {n_part}명 < {MIN_PARTICIPANTS}명)\n{bar}\n")

    pred, folds, scheme = outer_cv(X, y, groups, names, rule)
    res = {name: metrics(y, p) for name, p in pred.items()}
    log(f"검증: {scheme}, 폴드 {len(folds)}개 (전처리·C 선택은 학습 폴드 안에서만)")
    label = {"logreg": "로지스틱 회귀", "majority": "기준1 항상 다수 클래스", "rule": "기준2 RuleJudge 3.0σ"}
    for k in ("logreg", "majority", "rule"):
        m = res[k]
        c = m["confusion"]
        log(f"  {label[k]:<22} 균형정확도 {m['balanced_accuracy']:.3f}  거짓검출 {m['lie_detection_rate']:.3f}  "
            f"진실→거짓 {m['truth_as_lie_rate']:.3f}  혼동[진실→진실 {c['truth_pred_truth']}, 진실→거짓 "
            f"{c['truth_pred_lie']}, 거짓→진실 {c['lie_pred_truth']}, 거짓→거짓 {c['lie_pred_lie']}]")
    best_base = max(res["majority"]["balanced_accuracy"], res["rule"]["balanced_accuracy"])
    result = {"metrics": res, "scheme": scheme, "folds": folds, "n_participants": n_part, "small": small}

    if not res["logreg"]["balanced_accuracy"] > best_base:
        log(f"model.json을 만들지 않습니다: 로지스틱 회귀의 균형 정확도({res['logreg']['balanced_accuracy']:.3f})가 "
            f"기준 모델보다 높지 않습니다 (최고 기준 {best_base:.3f}). 규칙 판정기로 시연하세요.")
        result["reason"] = "not_better_than_baseline"
        return False, result
    if small and not allow_small:
        log(f"model.json을 만들지 않습니다: 참가자 {n_part}명 < {MIN_PARTICIPANTS}명. "
            "탐색용으로만 보려면 --allow-small을 주세요.")
        result["reason"] = "too_few_participants"
        return False, result

    # 최종 모델: 전체 데이터로 C를 고르고 전처리·학습 (평가 지표는 위의 분리 검증 값)
    C = choose_C(X, y, groups, names)
    prep = FoldPreprocessor(names).fit(X)
    model = _logreg(C).fit(prep.transform(X), y)
    payload = {
        "format": "truthpulse-logreg",
        "version": MODEL_VERSION,
        "created": dt.date.today().isoformat(),
        "exploratory": bool(small),
        "trained": True,
        "feature_order": prep.kept_names,
        "impute_median": prep.median.tolist(),
        "scaler_mean": prep.mean.tolist(),
        "scaler_std": prep.std.tolist(),
        "clip_range": [-STD_CLIP, STD_CLIP],
        "coef": model.coef_[0].tolist(),
        "intercept": float(model.intercept_[0]),
        "threshold": THRESHOLD,
        "C": C,
        "class_weight": "balanced",
        "label_map": {"truth": 0, "lie": 1},
        "filters": filter_settings(),
        "training": {"n_participants": n_part, "n_trials": int(len(y)), "n_truth": int(np.sum(y == 0)),
                     "n_lie": int(np.sum(y == 1)), "cv": scheme,
                     "cv_metrics": {k: {kk: vv for kk, vv in v.items()} for k, v in res.items()}},
    }
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    log(f"model.json → {os.path.abspath(out_path)}" + (" (탐색용, exploratory=true)" if small else ""))
    result["model"] = payload
    return True, result


def predict_from_model(model: dict, feature_rows):
    """model.json만으로 거짓 점수를 계산한다 (서빙과 같은 계산, 테스트용)."""
    names = model["feature_order"]
    X = np.array([[r.get(k, math.nan) for k in names] for r in feature_rows], dtype=float)
    X = np.where(np.isnan(X), np.array(model["impute_median"]), X)
    Z = np.clip((X - np.array(model["scaler_mean"])) / np.array(model["scaler_std"]), *model["clip_range"])
    return 1 / (1 + np.exp(-(Z @ np.array(model["coef"]) + model["intercept"])))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="training.train_logreg", description="로지스틱 회귀 학습·평가·내보내기")
    ap.add_argument("--data", default=None, help="build_dataset이 만든 CSV (없으면 --root에서 바로 만든다)")
    ap.add_argument("--root", default=DEFAULT_ROOT, help="experiments 폴더 (읽기 전용)")
    ap.add_argument("--out", default=os.path.join(DEFAULT_OUT_DIR, "model.json"),
                    help="model.json 경로 (data/real, collector 안은 거부)")
    ap.add_argument("--allow-small", action="store_true", help="참가자 5명 미만도 탐색용 model.json을 만든다")
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
    train(rows, args.out, allow_small=args.allow_small)
    return 0


if __name__ == "__main__":
    sys.exit(main())
