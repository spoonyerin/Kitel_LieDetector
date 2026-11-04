"""서버 명령줄 도구. 모든 명령은 파일을 쓰지 않는다.

사용법:
    .venv\\Scripts\\python.exe -m server.cli parse  data\\real\\experiments\\run_20261103_161545
    .venv\\Scripts\\python.exe -m server.cli replay data\\real\\experiments\\run_20261103_161545 --speed 0
    .venv\\Scripts\\python.exe -m server.cli dummy  --trials 3 --speed 0

parse : 원시 로그를 줄 해석기로 읽고 종류별 개수, 요약의 valid/reason 분포, 해석 불가 줄 예시를 출력한다.
replay: 원시 로그를 재생해 진행 추적기에 넣고 단계 변화와 시도별 결과를 출력한다 (source=hardware).
dummy : 합성 신호를 진행 추적기에 넣고 같은 형식으로 출력한다 (source=dummy).
ports : 연결된 COM 포트 목록을 보여 준다 (포트를 열지 않는다).
questions: 시드별 질문 순서를 미리 본다 (연습 1 + 본 질문 5 + 추가 질문). 라벨은 없다.
bpm   : 원시 로그의 IR로 심박수를 1초(기기 시각)마다 추정해 분포와 계산 못 한 사유를 보여 준다.
        추정값이며 정답 심박과 비교하지 않았다.
replay/dummy에 --judge를 주면 시도마다 RuleJudge로 판정하고 이유를 출력한다 (--json: trial_result JSON).
--fsr-th, --gsr-th, --min-sensors는 임계값 후보를 비교하는 탐색용이며 rule.py의 상수를 바꾸지 않는다.
"""

import argparse
import json
import math
import os
import sys
from collections import Counter
from typing import Optional

from server.judges.rule import RuleJudge
from server.result import judge_trial
from server.pipeline.tracker import (BaselineDone, baseline_for, DeviceRebooted, Notice, PhaseChanged,
                                     SessionEnded, SessionStarted, Tracker, TrialDone)
from server.source.dummy_source import PATTERNS, DummyConfig, DummySource
from server.source.lines import (Bad, BaselineSummary, Event, LineParser, Sample,
                                 TrialSummary, iter_raw_log)
from server.source.replay_source import ReplaySource, resolve_log


# ----------------------------------------------------------
# parse
# ----------------------------------------------------------
def cmd_parse(args) -> int:
    log_path = resolve_log(args.path)
    parser = LineParser()
    summaries, baselines, bad = [], [], []
    events, phases = Counter(), Counter()
    for _pc, line in iter_raw_log(log_path):
        item = parser.feed(line)
        if isinstance(item, Sample):
            phases[item.phase] += 1
        elif isinstance(item, Event):
            events[item.name] += 1
        elif isinstance(item, TrialSummary):
            summaries.append(item)
        elif isinstance(item, BaselineSummary):
            baselines.append(item)
        elif isinstance(item, Bad):
            bad.append(item)

    print(f"파일: {log_path}")
    print("줄 종류별 개수:")
    for name, n in sorted(parser.counts.items()):
        print(f"  {name:<16} {n}")
    print(f"샘플 단계별 개수: {dict(sorted(phases.items()))}")
    print(f"이벤트 이름별 개수: {dict(sorted(events.items()))}")
    print(f"요약 헤더: SUMMARY={parser.summary_header_source or '없음'}, "
          f"BASELINE={parser.baseline_header_source or '없음'}, 샘플 헤더 일치={parser.header_ok}")

    print(f"\nSUMMARY {len(summaries)}개")
    dist = Counter((s.raw.get("valid"), s.reason) for s in summaries)
    for (valid, reason), n in sorted(dist.items()):
        print(f"  valid={valid} reason={reason}: {n}")
    for s in summaries:
        print(f"  session={s.session} trial={s.trial} baseline={s.baseline_id} valid={s.raw.get('valid')} "
              f"reason={s.reason} warn={s.warn_names or '-'} "
              f"fsr_dz={s.raw.get('fsr_dz')} gsr_dz={s.raw.get('gsr_dz')}")

    print(f"\nBASELINE {len(baselines)}개")
    dist = Counter((b.raw.get("valid"), b.reason) for b in baselines)
    for (valid, reason), n in sorted(dist.items()):
        print(f"  valid={valid} reason={reason}: {n}")

    print(f"\n해석 불가 줄 {len(bad)}개")
    by_kind = Counter((b.kind, b.why) for b in bad)
    for (kind, why), n in sorted(by_kind.items()):
        print(f"  [{kind}] {why}: {n}")
    for b in bad[:args.examples]:
        print(f"  예시 [{b.kind}] {b.line[:100]}")
    return 0


# ----------------------------------------------------------
# replay / dummy 공통: 입력원 → 추적기 → 출력
# ----------------------------------------------------------
def _fmt(v):
    if isinstance(v, float):
        return "nan" if math.isnan(v) else f"{v:.3f}"
    return "-" if v is None else str(v)


def _print_update(u, brief):
    if isinstance(u, TrialDone):
        t = u.segment
        rep = t.report()
        sm = t.summary
        dz = (f"fsr_dz={_fmt(sm.values.get('fsr_dz'))} gsr_dz={_fmt(sm.values.get('gsr_dz'))}"
              if sm else "요약 없음")
        print(f"  [시도 완료] S{t.session_index} T{t.trial:02d} B{t.baseline_id} {t.status:<11} "
              f"{('(' + t.reason + ')') if t.reason else ''} 샘플 R{rep['n_ready']}/Q{rep['n_question']}"
              f"/RS{rep['n_response']} valid={sm.raw.get('valid') if sm else '-'} {dz} "
              f"무결성={rep['integrity']}")
    elif isinstance(u, BaselineDone):
        b = u.segment
        print(f"  [기준 측정] S{b.session_index} B{b.baseline_id} {b.status} {b.reason} "
              f"샘플 {len(b.samples)}개 valid={b.summary.raw.get('valid') if b.summary else '-'}")
    elif isinstance(u, SessionStarted):
        print(f"[세션 시작] S{u.session_index} (기기 세션 {u.esp_session})")
    elif isinstance(u, SessionEnded):
        print(f"[세션 종료] S{u.session_index} ({u.reason})")
    elif isinstance(u, DeviceRebooted):
        print(f"[기기 재부팅] {u.text}")
    elif brief:
        return
    elif isinstance(u, PhaseChanged):
        tag = f"T{u.trial:02d}" if u.trial else "   "
        print(f"    {u.esp_time_ms / 1000:9.3f}s {tag} 단계 → {u.phase}")
    elif isinstance(u, Notice):
        e = u.event
        print(f"    {e.esp_time_ms / 1000:9.3f}s 알림 {e.name} {e.detail}")


DECISION_KR = {"truth": "진실", "lie": "거짓", "inconclusive": "보류"}


def _print_result(res, as_json):
    if as_json:
        print(json.dumps(res, ensure_ascii=False))
        return
    f = res["features"]
    loc = (f"국소 fsr={_fmt(f['fsr_local_dz'])} gsr={_fmt(f['gsr_local_dz'])} | "
           f"기기 fsr={_fmt(f['fsr_dz'])} gsr={_fmt(f['gsr_dz'])}")
    print(f"  [판정] {res['session_id']} T{res['trial_id']:02d} {DECISION_KR[res['decision']]:<2} "
          f"score={res['score']} trained={res['judge']['trained']} | {loc}")
    for r in res["reasons"]:
        print(f"         - {r}")
    if res["quality"]["warnings"]:
        print(f"         (경고: {', '.join(res['quality']['warnings'])})")
    hidden = res.get("server_only", {}).get("warnings")
    if hidden:
        print(f"         (서버 전용 경고, 화면에는 숨김: {', '.join(hidden)})")


def _run(source, brief, judge=None, as_json=False) -> int:
    tracker = Tracker(source.source)
    quiet = as_json  # JSON 출력일 때는 판정 결과만 한 줄씩 낸다
    if not quiet:
        print(f"입력원: {source.name} (source={source.source})")
        if judge:
            print(f"판정기: {judge.name} {judge.version} (trained={judge.trained}) — {judge.note}")
    done, results = [], []

    def handle(updates):
        for u in updates:
            if not quiet:
                _print_update(u, brief)
            if isinstance(u, TrialDone):
                done.append(u.segment)
                if judge:
                    res = judge_trial(u.segment, source.source, judge,
                                      baseline_segment=baseline_for(tracker, u.segment))
                    results.append(res)
                    _print_result(res, as_json)

    for line in source.lines():
        handle(tracker.feed_line(line))
    handle(tracker.close())

    if quiet:
        return 0
    print(f"\n시도 {len(done)}개: " + ", ".join(f"{k} {v}" for k, v in sorted(Counter(t.status for t in done).items())))
    if judge:
        print("판정: " + ", ".join(f"{DECISION_KR[k]} {v}" for k, v in
                                  sorted(Counter(r["decision"] for r in results).items())))
    print(f"세션 {len(tracker.sessions)}개, 세션 밖 샘플 {tracker.ignored_samples}개, 해석 불가 줄 {tracker.bad_lines}개")
    return 0


def _make_judge(args):
    if not args.judge:
        return None
    if args.model:   # 학습된 판정기 (쓸 수 없으면 규칙 판정기로 대신하고 이유를 출력)
        from server.judges.logreg import select_judge
        judge, fallback = select_judge("logreg", args.model, args.allow_exploratory)
        if fallback:
            print(f"[안내] {fallback}")
        return judge
    return RuleJudge(fsr_threshold=args.fsr_th, gsr_threshold=args.gsr_th, min_sensors=args.min_sensors)


def cmd_replay(args) -> int:
    return _run(ReplaySource(args.path, speed=args.speed, max_wait=args.max_wait), args.brief,
                _make_judge(args), args.json)


def cmd_dummy(args) -> int:
    patterns = [p.strip() for p in args.patterns.split(",") if p.strip()]
    cfg = DummyConfig(trials=args.trials, patterns=patterns, seed=args.seed)
    return _run(DummySource(cfg, speed=args.speed, max_wait=args.max_wait), args.brief,
                _make_judge(args), args.json)


def bpm_series(lines, step_ms: int = 1000):
    """기기 줄에서 step_ms(기기 시각)마다 BPM을 추정한다. [(세션 번호, 시도, 단계, 추정)]을 돌려준다."""
    from server.pipeline.bpm import BpmTracker
    parser, tracker, out, last = LineParser(), BpmTracker(), [], None
    for line in lines:
        item = parser.feed(line)
        if not isinstance(item, Sample):
            continue
        tracker.add(item)
        if last is None or item.time_ms - last >= step_ms or item.time_ms < last:
            last = item.time_ms
            out.append((item.session, item.trial, item.phase, tracker.estimate()))
    return out


def cmd_bpm(args) -> int:
    import numpy as np
    series = bpm_series(line for _pc, line in iter_raw_log(resolve_log(args.path)))
    print(f"파일: {resolve_log(args.path)}")
    print("심박 추정값 (IR, 10초 창, 40~200 BPM). 정답 심박과 비교하지 않았으므로 정확도는 모릅니다.")
    print(f"추정 {len(series)}회 (기기 시각 1초마다)")
    for reason, n in Counter(e.reason for *_, e in series).most_common():
        print(f"  {reason:<16} {n:>4}회  {e_text(reason)}")
    groups = {}
    for sess, _trial, phase, e in series:
        if e.bpm is not None:
            groups.setdefault(("전체", ""), []).append(e.bpm)
            groups.setdefault((f"기기 세션 {sess}", phase), []).append(e.bpm)
    if not groups:
        print("값을 낸 창이 없습니다.")
        return 0
    print("\n값이 나온 창의 분포 (BPM): 개수 / 최소 / 중앙값 / 최대")
    for (name, phase), vals in sorted(groups.items(), key=lambda kv: (kv[0][0] != "전체", kv[0])):
        v = np.array(vals)
        print(f"  {name:<10} {phase:<9} {len(v):>4} / {v.min():6.1f} / {np.median(v):6.1f} / {v.max():6.1f}")
    return 0


def e_text(reason):
    from server.pipeline.bpm import REASON_TEXT
    return REASON_TEXT.get(reason, "")


PROTECTED_DIRS = ("data/real", "collector")   # features 출력을 쓰면 안 되는 폴더 (저장소 기준)


def _inside_protected(path: str) -> Optional[str]:
    """path가 보호 폴더 안이면 그 폴더 이름을 돌려준다."""
    from server.config import REPO_ROOT
    target = os.path.realpath(path)
    for rel in PROTECTED_DIRS:
        base = os.path.realpath(os.path.join(REPO_ROOT, rel))
        try:
            if os.path.commonpath([target, base]) == base:
                return rel
        except ValueError:   # 다른 드라이브
            continue
    return None


def feature_rows(log_path: str, run_name: str):
    """원시 로그 하나의 시도별 특징 행 (판정 결과는 참고용, 라벨 없음)."""
    from server.judges.rule import RuleJudge
    from server.pipeline.features import FEATURE_NAMES
    tracker, judge, rows = Tracker("hardware"), RuleJudge(), []

    def add(updates):
        for u in updates:
            if not isinstance(u, TrialDone):
                continue
            t = u.segment
            res = judge_trial(t, "hardware", judge, baseline_segment=baseline_for(tracker, t))
            info = res["feature_info"]
            row = {"run": run_name, "session_id": res["session_id"], "trial_id": t.trial,
                   "baseline_id": t.baseline_id, "device_status": t.status, "decision": res["decision"],
                   "hold_code": res["hold_code"] or "", "quality_ok": res["quality"]["ok"],
                   "fs": info["fs"], "baseline_found": info["baseline_found"], "n_baseline": info["n_baseline"],
                   "n_question": info["n_question"], "n_response": info["n_response"],
                   "flat": "|".join(info["flat"])}
            for k in ("fsr_local_dz", "gsr_local_dz", "fsr_dz", "gsr_dz", *FEATURE_NAMES):
                v = res["features"].get(k)
                row[k] = "" if v is None else v
            rows.append(row)

    for _pc, line in iter_raw_log(log_path):
        add(tracker.feed_line(line))
    add(tracker.close())
    return rows


def cmd_features(args) -> int:
    import csv
    from server.pipeline.features import FEATURE_NAMES
    bad = _inside_protected(args.out)
    if bad:
        print(f"[거부] 출력 경로가 {bad}/ 안입니다. 이 폴더에는 쓰지 않습니다: {args.out}")
        return 2
    rows = []
    for path in args.paths:
        log = resolve_log(path)
        rows += feature_rows(log, os.path.basename(os.path.dirname(os.path.abspath(log))))
    if not rows:
        print("시도가 없습니다.")
        return 1
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"시도 {len(rows)}개 → {os.path.abspath(args.out)}")
    print("특징별 빈 값(nan) 비율 (전체 시도 / 판정된 시도). 정답과 비교한 값이 아니다:")
    judged = [r for r in rows if r["quality_ok"]]
    for k in ("fsr_local_dz", "gsr_local_dz", *FEATURE_NAMES):
        all_nan = sum(1 for r in rows if r[k] == "") / len(rows)
        ok_nan = (sum(1 for r in judged if r[k] == "") / len(judged)) if judged else float("nan")
        print(f"  {k:<22} {all_nan:6.0%} / {ok_nan:6.0%}")
    return 0


def cmd_questions(args) -> int:
    from server.config import DEFAULT_QUESTION_BANK
    from server.questions import QuestionPlanner, load_questions
    planner = QuestionPlanner(load_questions(args.bank or DEFAULT_QUESTION_BANK), args.seed)
    print(f"질문 순서 (시드 {planner.seed}). 서버를 같은 순서로 켜려면 QUESTION_SEED={planner.seed}")
    for s in range(1, args.sessions + 1):
        print(f"\n세션 S{s:02d}")
        for i, q in enumerate(planner.order_for_session(s), 1):
            if q.role == "extra" and not args.all:
                print("  (이후 추가 질문은 --all로 보기)")
                break
            print(f"  {i:>2}. [{q.label}] {q.question_id} {q.question_text}")
    return 0


def cmd_ports(args, lister=None) -> int:
    """COM 포트 목록. 포트를 열지 않으므로 기기가 재부팅되지 않는다."""
    from server.source.serial_source import list_ports
    ports = (lister or list_ports)()
    if not ports:
        print("연결된 COM 포트가 없습니다. USB 케이블과 드라이버(CP210x/CH340 등)를 확인하세요.")
        return 0
    print(f"COM 포트 {len(ports)}개:")
    for device, desc, hwid in ports:
        print(f"  {device:<8} {desc}  [{hwid}]")
    print("\n서버 실행 예:  $env:SOURCE=\"serial\"; $env:SERIAL_PORT=\"" + ports[0][0] + "\"; "
          ".venv\\Scripts\\python.exe -m uvicorn server.app:app --port 8000")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="server.cli", description="TruthPulse 서버 명령줄 도구 (읽기 전용)")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ports", help="연결된 COM 포트 목록 (포트를 열지 않음)")
    p.set_defaults(func=cmd_ports)

    p = sub.add_parser("features", help="원시 로그의 시도별 전체 특징을 CSV로 쓴다 (data/real 안에는 쓰지 않음)")
    p.add_argument("paths", nargs="+", help="run 폴더 또는 raw_serial_log.txt (여러 개 가능, 읽기 전용)")
    p.add_argument("--out", required=True, help="출력 CSV 경로 (data/real, collector 안은 거부)")
    p.set_defaults(func=cmd_features)

    p = sub.add_parser("questions", help="시드별 질문 순서를 미리 본다 (라벨 없음)")
    p.add_argument("--seed", type=int, default=None, help="QUESTION_SEED와 같은 값 (없으면 무작위)")
    p.add_argument("--sessions", type=int, default=1, help="보여 줄 세션 수 (기본 1)")
    p.add_argument("--all", action="store_true", help="추가 질문까지 모두 보기")
    p.add_argument("--bank", default=None, help="질문 은행 경로 (기본 collector/question_bank.csv)")
    p.set_defaults(func=cmd_questions)

    p = sub.add_parser("bpm", help="원시 로그의 IR로 심박 추정값 분포를 본다 (읽기 전용)")
    p.add_argument("path", help="run 폴더 또는 raw_serial_log.txt 경로")
    p.set_defaults(func=cmd_bpm)

    p = sub.add_parser("parse", help="원시 로그를 해석해 요약을 출력한다")
    p.add_argument("path", help="run 폴더 또는 raw_serial_log.txt 경로")
    p.add_argument("--examples", type=int, default=5, help="해석 불가 줄 예시 개수 (기본 5)")
    p.set_defaults(func=cmd_parse)

    def pacing_args(sp):
        sp.add_argument("--speed", type=float, default=1.0, help="재생 배속 (0이면 기다리지 않음, 기본 1)")
        sp.add_argument("--max-wait", type=float, default=2.0, help="한 번에 기다리는 최대 초 (기본 2)")
        sp.add_argument("--brief", action="store_true", help="단계 변화와 알림은 출력하지 않음")
        sp.add_argument("--judge", action="store_true", help="시도마다 RuleJudge로 판정하고 이유를 출력")
        sp.add_argument("--json", action="store_true", help="--judge와 함께: trial_result JSON을 한 줄씩 출력")
        sp.add_argument("--fsr-th", type=float, default=None, help="탐색용 FSR 임계값 (기본: rule.py 상수)")
        sp.add_argument("--gsr-th", type=float, default=None, help="탐색용 GSR 임계값 (기본: rule.py 상수)")
        sp.add_argument("--min-sensors", type=int, default=None, help="탐색용 최소 센서 수 (기본: rule.py 상수)")
        sp.add_argument("--model", default=None, help="--judge와 함께: model.json으로 학습된 판정기 사용")
        sp.add_argument("--allow-exploratory", action="store_true", help="탐색용 model.json도 허용")

    p = sub.add_parser("replay", help="원시 로그를 재생해 시도별 결과를 출력한다")
    p.add_argument("path", help="run 폴더 또는 raw_serial_log.txt 경로")
    pacing_args(p)
    p.set_defaults(func=cmd_replay)

    p = sub.add_parser("dummy", help="합성 신호로 시도별 결과를 출력한다")
    p.add_argument("--trials", type=int, default=3)
    p.add_argument("--patterns", default="calm,reactive,poor",
                   help=f"쉼표로 구분, 시도마다 차례로 사용 (가능: {', '.join(PATTERNS)})")
    p.add_argument("--seed", type=int, default=0)
    pacing_args(p)
    p.set_defaults(func=cmd_dummy)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
