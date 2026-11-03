"""서버 명령줄 도구.

사용법:
    .venv\\Scripts\\python.exe -m server.cli parse data\\real\\experiments\\run_20261103_161545
    .venv\\Scripts\\python.exe -m server.cli parse <raw_serial_log.txt 경로>

parse: 원시 로그를 줄 해석기로 읽고 종류별 개수, 요약의 valid/reason 분포,
       해석 불가 줄의 예시를 출력한다. 파일을 쓰지 않는다.
"""

import argparse
import os
import sys
from collections import Counter

from server.source.lines import (Bad, BaselineSummary, Event, LineParser, Sample,
                                 TrialSummary, iter_raw_log)


def resolve_log(path: str) -> str:
    """run 폴더를 받으면 그 안의 raw_serial_log.txt 경로를 돌려준다."""
    if os.path.isdir(path):
        path = os.path.join(path, "raw_serial_log.txt")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"원시 로그가 없습니다: {path}")
    return path


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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="server.cli", description="TruthPulse 서버 명령줄 도구")
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("parse", help="원시 로그를 해석해 요약을 출력한다 (읽기 전용)")
    p.add_argument("path", help="run 폴더 또는 raw_serial_log.txt 경로")
    p.add_argument("--examples", type=int, default=5, help="해석 불가 줄 예시 개수 (기본 5)")
    p.set_defaults(func=cmd_parse)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
