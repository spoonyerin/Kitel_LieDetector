"""서버 명령줄 도구. 모든 명령은 파일을 쓰지 않는다.

사용법:
    .venv\\Scripts\\python.exe -m server.cli parse  data\\real\\experiments\\run_20261103_161545
    .venv\\Scripts\\python.exe -m server.cli replay data\\real\\experiments\\run_20261103_161545 --speed 0
    .venv\\Scripts\\python.exe -m server.cli dummy  --trials 3 --speed 0

parse : 원시 로그를 줄 해석기로 읽고 종류별 개수, 요약의 valid/reason 분포, 해석 불가 줄 예시를 출력한다.
replay: 원시 로그를 재생해 진행 추적기에 넣고 단계 변화와 시도별 결과를 출력한다 (source=hardware).
dummy : 합성 신호를 진행 추적기에 넣고 같은 형식으로 출력한다 (source=dummy).
"""

import argparse
import math
import sys
from collections import Counter

from server.pipeline.tracker import (BaselineDone, DeviceRebooted, Notice, PhaseChanged,
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


def _run(source, brief) -> int:
    tracker = Tracker(source.source)
    print(f"입력원: {source.name} (source={source.source})")
    done = []
    for line in source.lines():
        for u in tracker.feed_line(line):
            _print_update(u, brief)
            if isinstance(u, TrialDone):
                done.append(u.segment)
    for u in tracker.close():
        _print_update(u, brief)
        if isinstance(u, TrialDone):
            done.append(u.segment)

    print(f"\n시도 {len(done)}개: " + ", ".join(f"{k} {v}" for k, v in sorted(Counter(t.status for t in done).items())))
    print(f"세션 {len(tracker.sessions)}개, 세션 밖 샘플 {tracker.ignored_samples}개, 해석 불가 줄 {tracker.bad_lines}개")
    return 0


def cmd_replay(args) -> int:
    return _run(ReplaySource(args.path, speed=args.speed, max_wait=args.max_wait), args.brief)


def cmd_dummy(args) -> int:
    patterns = [p.strip() for p in args.patterns.split(",") if p.strip()]
    cfg = DummyConfig(trials=args.trials, patterns=patterns, seed=args.seed)
    return _run(DummySource(cfg, speed=args.speed, max_wait=args.max_wait), args.brief)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="server.cli", description="TruthPulse 서버 명령줄 도구 (읽기 전용)")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("parse", help="원시 로그를 해석해 요약을 출력한다")
    p.add_argument("path", help="run 폴더 또는 raw_serial_log.txt 경로")
    p.add_argument("--examples", type=int, default=5, help="해석 불가 줄 예시 개수 (기본 5)")
    p.set_defaults(func=cmd_parse)

    def pacing_args(sp):
        sp.add_argument("--speed", type=float, default=1.0, help="재생 배속 (0이면 기다리지 않음, 기본 1)")
        sp.add_argument("--max-wait", type=float, default=2.0, help="한 번에 기다리는 최대 초 (기본 2)")
        sp.add_argument("--brief", action="store_true", help="단계 변화와 알림은 출력하지 않음")

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
