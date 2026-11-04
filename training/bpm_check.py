"""심박 추정값 비교 (S14, I14): 사용자가 잰 기준 심박(CSV)과 서버의 심박 추정값을 비교한다.

사용법 (한 줄):
    .venv\\Scripts\\python.exe -m training.bpm_check ref_bpm.csv
    .venv\\Scripts\\python.exe -m training.bpm_check ref_bpm.csv --root data\\real\\experiments --window response --out C:\\temp\\bpm.csv

기준 CSV (UTF-8, 첫 줄 헤더; 다른 열은 무시):
    run,session,trial,ref_bpm
    run_20261103_161545,S01,2,74
    - run: run 폴더 이름(--root 아래) 또는 run 폴더/raw_serial_log.txt 경로
    - session: 서버 화면의 세션 순번 (S01 또는 1). 기기 세션 번호가 아니라 SESSION START 순서
    - trial: 기기 시도 번호 (화면·명령줄의 T번호)
    - ref_bpm: 맥박 직접 세기나 다른 측정기 값 (BPM)

서버 추정값: 진행 추적기 → server.result.judge_trial의 특징 (서버·학습과 같은 코드)
    --window response: hr_resp (답변 구간 끝 10초, 기본) / baseline: hr_bl (기준 측정 끝 10초)
    → 기준 값도 같은 구간에서 재야 비교가 맞다.
출력: 시도별 표, 평균 절대 오차(MAE), 편향(추정 − 기준의 평균), 값이 없는 비율, 찾지 못한 시도.
- raw_serial_log.txt만 읽는다 (라벨이 든 protocol.csv, trials_summary.csv는 읽지 않는다). data/real은 읽기만 한다.
"""

import argparse
import csv
import math
import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from server.cli import _inside_protected  # noqa: E402
from server.pipeline.tracker import Tracker, TrialDone, baseline_for  # noqa: E402
from server.result import judge_trial  # noqa: E402
from server.source.lines import iter_raw_log  # noqa: E402
from training.build_dataset import DEFAULT_ROOT, _read_csv  # noqa: E402

WINDOWS = {"response": "hr_resp", "baseline": "hr_bl"}
REQUIRED = ("run", "session", "trial", "ref_bpm")
REF_RANGE = (20.0, 250.0)     # 이 밖의 기준 값은 입력 실수로 보고 뺀다


def _session_no(v: str) -> int:
    m = re.fullmatch(r"[sS]?0*(\d+)", (v or "").strip())
    if not m:
        raise ValueError(f"세션 값이 올바르지 않습니다: {v!r} (예: S01 또는 1)")
    return int(m.group(1))


def read_reference(path: str):
    """(기준 행 목록, 잘못된 줄 목록[(줄 번호, 이유)])."""
    rows = _read_csv(path)
    if rows and not all(k in rows[0] for k in REQUIRED):
        raise ValueError(f"기준 CSV에 필요한 열이 없습니다: {', '.join(k for k in REQUIRED if k not in rows[0])}")
    refs, bad = [], []
    for i, r in enumerate(rows, 2):
        try:
            ref = float(r["ref_bpm"])
            if not (REF_RANGE[0] <= ref <= REF_RANGE[1]):
                raise ValueError(f"ref_bpm 범위 밖 ({ref})")
            run = (r["run"] or "").strip()
            if not run:
                raise ValueError("run이 비어 있음")
            refs.append({"line": i, "run": run, "session": _session_no(r["session"]),
                         "trial": int(str(r["trial"]).strip()), "ref_bpm": ref})
        except (ValueError, TypeError) as e:
            bad.append((i, str(e)))
    return refs, bad


def _log_path(run: str, root: str):
    for cand in (run, os.path.join(root, run)):
        if os.path.isfile(cand) and cand.lower().endswith(".txt"):
            return cand
        p = os.path.join(cand, "raw_serial_log.txt")
        if os.path.isfile(p):
            return p
    return None


def server_estimates(log_path: str) -> dict:
    """(세션 순번, 기기 시도 번호) → 특징 dict (hr_resp, hr_bl 포함). 라벨 파일은 읽지 않는다."""
    tracker, out = Tracker("hardware"), {}

    def handle(updates):
        for u in updates:
            if isinstance(u, TrialDone):
                t = u.segment
                res = judge_trial(t, "hardware", baseline_segment=baseline_for(tracker, t))
                out[(t.session_index, t.trial)] = res["features"]

    for _pc, line in iter_raw_log(log_path):
        handle(tracker.feed_line(line))
    handle(tracker.close())
    return out


def compare(refs, root: str = DEFAULT_ROOT, window: str = "response") -> dict:
    key = WINDOWS[window]
    cache, rows = {}, []
    for r in refs:
        path = _log_path(r["run"], root)
        row = {**r, "est_bpm": None, "diff": None, "status": "ok"}
        if path is None:
            row["status"] = "run_not_found"
        else:
            if path not in cache:
                cache[path] = server_estimates(path)
            feat = cache[path].get((r["session"], r["trial"]))
            if feat is None:
                row["status"] = "trial_not_found"
            else:
                v = feat.get(key)
                if v is None or (isinstance(v, float) and math.isnan(v)):
                    row["status"] = "no_estimate"
                else:
                    row["est_bpm"] = float(v)
                    row["diff"] = float(v) - r["ref_bpm"]
        rows.append(row)
    found = [x for x in rows if x["status"] in ("ok", "no_estimate")]
    pairs = [x for x in rows if x["status"] == "ok"]
    diffs = [x["diff"] for x in pairs]
    return {
        "window": window, "feature": key, "rows": rows, "n_ref": len(rows), "n_found": len(found),
        "n_pairs": len(pairs), "n_no_estimate": len(found) - len(pairs),
        "n_not_found": len(rows) - len(found),
        "missing_ratio": (len(found) - len(pairs)) / len(found) if found else math.nan,
        "mae": sum(abs(d) for d in diffs) / len(diffs) if diffs else math.nan,
        "bias": sum(diffs) / len(diffs) if diffs else math.nan,
        "max_abs_error": max(abs(d) for d in diffs) if diffs else math.nan,
    }


STATUS_KR = {"ok": "", "no_estimate": "추정값 없음", "trial_not_found": "시도를 찾지 못함",
             "run_not_found": "run을 찾지 못함"}


def _f(v, d=1):
    return "—" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v:.{d}f}"


def print_result(res, bad=(), log=print):
    log(f"비교 구간: {res['window']} ({res['feature']}). 기준 값도 같은 구간에서 재야 비교가 맞습니다.")
    log(f"{'run':<24} {'세션':>4} {'시도':>4} {'기준':>6} {'추정':>6} {'차이':>6}  비고")
    for r in res["rows"]:
        log(f"{os.path.basename(os.path.normpath(r['run'])):<24} S{r['session']:02d} {r['trial']:>4} "
            f"{_f(r['ref_bpm']):>6} {_f(r['est_bpm']):>6} {_f(r['diff']):>6}  {STATUS_KR[r['status']]}")
    for line, why in bad:
        log(f"  [건너뜀] 기준 CSV {line}번째 줄: {why}")
    log(f"기준 {res['n_ref']}개, 비교 {res['n_pairs']}개, 추정값 없음 {res['n_no_estimate']}개, "
        f"찾지 못함 {res['n_not_found']}개")
    log(f"평균 절대 오차 {_f(res['mae'])} BPM, 편향(추정−기준) {_f(res['bias'], 2)} BPM, "
        f"최대 오차 {_f(res['max_abs_error'])} BPM, 값이 없는 비율 {_f(res['missing_ratio'] * 100 if not math.isnan(res['missing_ratio']) else math.nan, 0)}%")
    if res["n_pairs"] < 10:
        log("참고: 비교 개수가 적어 오차 추정이 크게 흔들릴 수 있습니다.")


def write_rows(res, path: str):
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    cols = ["run", "session", "trial", "ref_bpm", "est_bpm", "diff", "status"]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in res["rows"]:
            w.writerow({k: ("" if r[k] is None else r[k]) for k in cols})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="training.bpm_check", description="서버 심박 추정값 ↔ 기준 심박 비교")
    ap.add_argument("ref_csv", help="기준 심박 CSV (run, session, trial, ref_bpm)")
    ap.add_argument("--root", default=DEFAULT_ROOT, help="run 폴더를 찾을 experiments 폴더 (읽기 전용)")
    ap.add_argument("--window", choices=sorted(WINDOWS), default="response",
                    help="response: 답변 끝 10초(hr_resp, 기본), baseline: 기준 측정 끝 10초(hr_bl)")
    ap.add_argument("--out", default=None, help="시도별 비교 CSV (선택, data/real·collector 안은 거부)")
    args = ap.parse_args(argv)
    if args.out:
        bad_out = _inside_protected(args.out)
        if bad_out:
            print(f"[거부] 출력 경로가 {bad_out}/ 안입니다: {args.out}")
            return 2
    if not os.path.isfile(args.ref_csv):
        print(f"기준 CSV가 없습니다: {args.ref_csv}")
        return 2
    try:
        refs, bad = read_reference(args.ref_csv)
    except ValueError as e:
        print(str(e))
        return 2
    if not refs:
        print("비교할 기준 값이 없습니다.")
        for line, why in bad:
            print(f"  [건너뜀] {line}번째 줄: {why}")
        return 0
    res = compare(refs, args.root, args.window)
    print_result(res, bad)
    if args.out:
        write_rows(res, args.out)
        print(f"시도별 비교 → {os.path.abspath(args.out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
