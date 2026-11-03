"""학습용 데이터셋 만들기: collector 기록 → 시도별 특징 + 확정 라벨.

사용법:
    .venv\\Scripts\\python.exe -m training.build_dataset                      (기본: data/real/experiments → training/out/dataset.csv)
    .venv\\Scripts\\python.exe -m training.build_dataset --root <experiments 폴더> --out <csv>

특징은 서버와 같은 코드로 만든다 (학습/서빙 불일치 방지):
    raw_serial_log.txt → server.pipeline.tracker.Tracker → server.result.judge_trial (기준 측정 구간 포함)
    → trial_result.features (국소 dz + S11 전체 특징). RuleJudge 판정도 기준 모델 비교용으로 같이 담는다.

라벨 규칙 (2026-11-04 사용자 확인):
    - 세션 폴더의 trials_summary.csv에서 confirmed가 as_planned 또는 corrected이고
      condition이 truth 또는 lie인 시도만 쓴다 (truth=0, lie=1).
    - planned_condition만 있고 확인되지 않은 시도, condition=test(헷갈림·중단)는 쓰지 않는다.
    - protocol.csv의 asked_trials/final_condition과 trials_summary.csv의 condition이 다르면 label_mismatch로 제외.
    - 라벨은 특징 계산에 들어가지 않는다 (특징을 먼저 만들고 라벨은 나중에 붙인다).

조인 키: (run 폴더, 세션 순번 k, 기기 시도 번호)
    - 세션: run_events.csv의 k번째 "SESSION ... START" 줄의 session_id = 세션 폴더 이름.
      (진행 추적기와 collector는 SESSION START마다 세션을 하나씩 센다.) 없으면 폴더 이름의 _S{k:02d}_.
    - 시도: trials_summary.csv의 trial_id (기기 시도 번호), 참가자: 같은 줄의 participant_id.

제외 사유 (먼저 걸린 하나로 센다): dummy, no_session_files, label_columns_missing, no_label_row, practice,
    extra, quality_hold, not_confirmed, excluded_test, not_truth_or_lie, label_mismatch

더미 판별: 경로에 dummy 폴더가 있거나, run 폴더의 SOURCE 파일 내용이 dummy이면 그 run의 시도를 모두 제외한다.
data/real은 읽기만 한다. 출력은 data/real, collector 안에 쓰지 않는다.
"""

import argparse
import csv
import math
import os
import re
import sys
from collections import Counter

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from server.cli import _inside_protected  # noqa: E402  (data/real, collector 쓰기 방지)
from server.pipeline.features import FEATURE_NAMES  # noqa: E402
from server.pipeline.tracker import Tracker, TrialDone, baseline_for  # noqa: E402
from server.result import judge_trial  # noqa: E402
from server.source.lines import iter_raw_log  # noqa: E402

DEFAULT_ROOT = os.path.join(REPO_ROOT, "data", "real", "experiments")
DEFAULT_OUT_DIR = os.path.join(REPO_ROOT, "training", "out")
DEFAULT_DATASET = os.path.join(DEFAULT_OUT_DIR, "dataset.csv")

# 학습에 쓰는 특징: 판정용 국소 dz + S11 전체 특징. 기기 dz(fsr_dz, gsr_dz)는 세션 추세가 커서 넣지 않는다.
MODEL_FEATURES = ["fsr_local_dz", "gsr_local_dz", *FEATURE_NAMES]
LABELS = {"truth": 0, "lie": 1}
CONFIRMED_VALUES = ("as_planned", "corrected")
LABEL_COLUMNS = ("trial_id", "participant_id", "condition", "planned_condition", "confirmed", "role")
META_COLUMNS = ["run", "session_id", "participant_id", "trial_id", "question_id", "label", "condition",
                "confirmed", "rule_decision", "source"]
EXCLUDE_REASONS = ("dummy", "no_session_files", "label_columns_missing", "no_label_row", "practice", "extra",
                   "quality_hold", "not_confirmed", "excluded_test", "not_truth_or_lie", "label_mismatch")
REASON_KR = {
    "dummy": "더미 데이터 (학습 금지)", "no_session_files": "세션 기록 폴더 없음",
    "label_columns_missing": "예전 형식 (확정 열 없음)", "no_label_row": "기록에 그 시도가 없음",
    "practice": "연습 질문", "extra": "계획 외 질문", "quality_hold": "보류 (품질 불량)",
    "not_confirmed": "라벨 미확정 (계획만 있음)", "excluded_test": "제외 표시 (test)",
    "not_truth_or_lie": "라벨 값이 진실/거짓이 아님", "label_mismatch": "protocol.csv와 라벨 불일치",
}


# ----------------------------------------------------------
# collector 기록 읽기 (읽기 전용)
# ----------------------------------------------------------
def _read_csv(path):
    if not os.path.isfile(path):
        return []
    for enc in ("utf-8-sig", "cp949"):
        try:
            with open(path, newline="", encoding=enc) as f:
                return list(csv.DictReader(f))
        except UnicodeDecodeError:
            continue
    return []


def is_dummy_run(run_dir: str) -> bool:
    parts = [p.lower() for p in os.path.normpath(os.path.abspath(run_dir)).split(os.sep)]
    if "dummy" in parts:
        return True
    marker = os.path.join(run_dir, "SOURCE")
    if os.path.isfile(marker):
        with open(marker, encoding="utf-8") as f:
            return f.read().strip().lower() == "dummy"
    return False


def session_dirs(run_dir: str) -> dict:
    """세션 순번 k → 세션 폴더 경로."""
    out = {}
    events = os.path.join(run_dir, "run_events.csv")
    if os.path.isfile(events):
        starts = [r.get("session_id", "") for r in _read_csv(events)
                  if r.get("event") == "SESSION" and r.get("detail") == "START"]
        for k, sid in enumerate(starts, 1):
            if sid and os.path.isdir(os.path.join(run_dir, sid)):
                out[k] = os.path.join(run_dir, sid)
    for name in sorted(os.listdir(run_dir)):
        m = re.search(r"_S(\d+)_", name)
        if m and os.path.isdir(os.path.join(run_dir, name)):
            out.setdefault(int(m.group(1)), os.path.join(run_dir, name))
    return out


def read_session_labels(session_dir: str):
    """(trial_id → trials_summary 줄, trial_id → protocol final_condition, 열이 다 있는지)."""
    rows = _read_csv(os.path.join(session_dir, "trials_summary.csv"))
    # 교차 확인이 필수이므로 protocol.csv가 없으면 확정 라벨을 쓸 수 없다 (예전 형식 run)
    ok = (bool(rows) and all(c in rows[0] for c in LABEL_COLUMNS)
          and os.path.isfile(os.path.join(session_dir, "protocol.csv")))
    by_trial = {}
    for r in rows:
        try:
            by_trial[int(r["trial_id"])] = r
        except (KeyError, ValueError):
            continue
    proto = {}
    for r in _read_csv(os.path.join(session_dir, "protocol.csv")):
        trials = (r.get("asked_trials") or "").split()
        finals = (r.get("final_condition") or "").split()
        for i, t in enumerate(trials):
            try:
                proto[int(t)] = finals[i] if i < len(finals) else ""
            except ValueError:
                continue
    return by_trial, proto, ok


# ----------------------------------------------------------
# 데이터셋
# ----------------------------------------------------------
def _label_or_reason(trial_no, session_dir, labels_cache):
    if session_dir is None:
        return None, "no_session_files"
    if session_dir not in labels_cache:
        labels_cache[session_dir] = read_session_labels(session_dir)
    by_trial, proto, ok = labels_cache[session_dir]
    if not ok:
        return None, "label_columns_missing"
    row = by_trial.get(trial_no)
    if row is None:
        return None, "no_label_row"
    return (row, proto), None


def build_rows(root: str = DEFAULT_ROOT):
    """(포함된 행 목록, 제외 사유 Counter, run 수). 특징은 서버 코드로 계산하고 라벨은 그 뒤에 붙인다."""
    rows, excluded, n_runs = [], Counter(), 0
    if not os.path.isdir(root):
        return rows, excluded, n_runs
    for name in sorted(os.listdir(root)):
        run_dir = os.path.join(root, name)
        log = os.path.join(run_dir, "raw_serial_log.txt")
        if not (name.startswith("run_") and os.path.isfile(log)):
            continue
        n_runs += 1
        dummy = is_dummy_run(run_dir)
        sessions = {} if dummy else session_dirs(run_dir)
        labels_cache = {}
        tracker = Tracker("dummy" if dummy else "hardware")

        def handle(updates):
            for u in updates:
                if not isinstance(u, TrialDone):
                    continue
                t = u.segment
                if dummy:
                    excluded["dummy"] += 1
                    continue
                # 1) 특징 (라벨 없이, 서버와 같은 코드)
                res = judge_trial(t, "hardware", baseline_segment=baseline_for(tracker, t))
                # 2) 라벨
                found, reason = _label_or_reason(t.trial, sessions.get(t.session_index), labels_cache)
                if reason:
                    excluded[reason] += 1
                    continue
                lab, proto = found
                role = (lab.get("role") or "").strip()
                cond = (lab.get("condition") or "").strip().lower()
                confirmed = (lab.get("confirmed") or "").strip()
                if role == "practice":
                    reason = "practice"
                elif role == "extra":
                    reason = "extra"
                elif not res["quality"]["ok"]:
                    reason = "quality_hold"
                elif confirmed not in CONFIRMED_VALUES:
                    reason = "not_confirmed"
                elif cond == "test":
                    reason = "excluded_test"
                elif cond not in LABELS:
                    reason = "not_truth_or_lie"
                elif proto.get(t.trial) != cond:     # protocol.csv에 없거나 값이 다르면 쓰지 않는다
                    reason = "label_mismatch"
                if reason:
                    excluded[reason] += 1
                    continue
                row = {"run": name, "session_id": os.path.basename(sessions[t.session_index]),
                       "participant_id": (lab.get("participant_id") or "").strip(), "trial_id": t.trial,
                       "question_id": lab.get("question_id", ""), "label": LABELS[cond], "condition": cond,
                       "confirmed": confirmed, "rule_decision": res["decision"], "source": "hardware"}
                for k in MODEL_FEATURES:
                    v = res["features"].get(k)
                    row[k] = math.nan if v is None else float(v)
                rows.append(row)

        for _pc, line in iter_raw_log(log):
            handle(tracker.feed_line(line))
        handle(tracker.close())
    return rows, excluded, n_runs


def write_dataset(rows, path: str):
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=META_COLUMNS + MODEL_FEATURES)
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if isinstance(r[k], float) and math.isnan(r[k]) else r[k])
                        for k in META_COLUMNS + MODEL_FEATURES})


def read_dataset(path: str):
    out = []
    for r in _read_csv(path):
        row = dict(r)
        row["label"] = int(r["label"])
        row["trial_id"] = int(r["trial_id"])
        for k in MODEL_FEATURES:
            row[k] = math.nan if r.get(k, "") == "" else float(r[k])
        out.append(row)
    return out


def print_report(rows, excluded, n_runs):
    total = len(rows) + sum(excluded.values())
    print(f"run {n_runs}개, 시도 {total}개 중 학습에 쓸 시도 {len(rows)}개")
    if excluded:
        print("제외 (사유별):")
        for r in EXCLUDE_REASONS:
            if excluded.get(r):
                print(f"  {REASON_KR[r]:<24} {excluded[r]:>4}  ({r})")
    if rows:
        parts = Counter(r["participant_id"] for r in rows)
        labels = Counter(r["condition"] for r in rows)
        print(f"참가자 {len(parts)}명, 진실 {labels.get('truth', 0)}개, 거짓 {labels.get('lie', 0)}개")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="training.build_dataset", description="collector 기록 → 학습용 데이터셋")
    ap.add_argument("--root", default=DEFAULT_ROOT, help="experiments 폴더 (읽기 전용)")
    ap.add_argument("--out", default=DEFAULT_DATASET, help="출력 CSV (data/real, collector 안은 거부)")
    args = ap.parse_args(argv)
    bad = _inside_protected(args.out)
    if bad:
        print(f"[거부] 출력 경로가 {bad}/ 안입니다: {args.out}")
        return 2
    rows, excluded, n_runs = build_rows(args.root)
    print_report(rows, excluded, n_runs)
    if not rows:
        print("학습할 데이터가 없습니다. (라벨이 확정된 본 질문 시도가 0건)")
        return 0
    write_dataset(rows, args.out)
    print(f"데이터셋 → {os.path.abspath(args.out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
