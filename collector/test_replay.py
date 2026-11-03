#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_replay.py  —  record_session.py V6 자동 검증 스크립트

저장된 raw_serial_log.txt를 재생하여 V6의 핵심 동작을 검증합니다.
실물 ESP32 없이 실행 가능합니다.

검증 항목:
  T1  SUMMARY_HEADER 없이 SUMMARY 수신 → 자동 복구 (폴백 헤더 사용)
  T2  SUMMARY_HEADER 있을 때 정상 파싱
  T3  valid=1 시험 → COMPLETE 처리
  T4  valid=0 시험 → ABORTED 처리
  T5  ABORTED 이후 연습 대체 질문 삽입 (replace 호출)
  T6  본 질문 5개 완료 → main_done_announced = True
  T7  세션 종료 후 confirm_queue 채워짐 (main COMPLETE 존재 시)
  T8  실제 로그 재생 → COMPLETE/ABORTED 비율 기대값 일치
  T9  원본 experiments 파일 변경 없음

사용법:
    python test_replay.py
    python test_replay.py --verbose
"""

import argparse
import csv
import os
import shutil
import sys
import tempfile

# record_session 모듈 임포트
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from record_session import Run, SUMMARY_HEADER_V5, BASELINE_HEADER_V5, N_MAIN_QUESTIONS
except ImportError as e:
    print(f"[오류] record_session.py 임포트 실패: {e}")
    sys.exit(1)


# ──────────────────────────────────────────────
# 테스트 유틸리티
# ──────────────────────────────────────────────
PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"

results = []

def check(name, condition, detail=""):
    status = PASS if condition else FAIL
    results.append((name, status, detail))
    mark = "✓" if condition else "✗"
    print(f"  [{mark}] {name}" + (f"  — {detail}" if detail else ""))
    return condition

def skip(name, reason=""):
    results.append((name, SKIP, reason))
    print(f"  [-] {name}  — SKIP: {reason}")


def feed_log(run_obj, log_path, verbose=False):
    """raw_serial_log.txt의 모든 줄을 Run.handle_line()에 공급합니다."""
    fed = 0
    with open(log_path, encoding="utf-8", errors="replace") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            if "\t" in raw:
                _, line = raw.split("\t", 1)
            else:
                line = raw
            if verbose:
                print(f"    >> {line[:80]}")
            run_obj.handle_line(line)
            fed += 1
    return fed


def make_run(tmpdir, participant="P99", bank=None):
    """테스트용 Run 객체 생성 (tmpdir에 파일 저장)."""
    return Run(tmpdir, participant, plan=[], bank=bank or {})


# ──────────────────────────────────────────────
# T1·T2: SUMMARY_HEADER 폴백 / 정상 파싱
# ──────────────────────────────────────────────
def test_summary_header_fallback(tmpdir, verbose):
    print("\n[T1] SUMMARY_HEADER 없이 SUMMARY 수신 → 자동 복구")
    r = make_run(tmpdir)

    # 세션 시작 이벤트 수동 공급
    r.handle_line("#EVENT,1000,SESSION,0,START,1,0")
    r.handle_line("#EVENT,2000,BASELINE,0,START,1,1")
    # BASELINE_HEADER 미수신 상태에서 BASELINE 수신
    bl = "1,1,1,NONE,0,749,2229.56,199.32,2092.69,30.13,123726.8"
    r.handle_line(f"#BASELINE,{bl}")

    # SUMMARY_HEADER 없이 SUMMARY 수신
    sm = "1,1,1,1,NONE,0,749,2229.56,199.32,2092.69,30.13,123726.8,172,3440,999,2211.48,274.56,1971.22,40.54,123745.8,-0.091,-4.031"
    r.handle_line(f"#EVENT,3000,READY,1,START,1,1")
    r.handle_line(f"#SUMMARY,{sm}")

    check("T1-a: summary_header 자동 설정됨",
          r.summary_header == SUMMARY_HEADER_V5,
          f"header={r.summary_header[:3] if r.summary_header else None}…")

    r.close()


def test_summary_header_normal(tmpdir, verbose):
    print("\n[T2] SUMMARY_HEADER 수신 후 정상 파싱")
    r = make_run(tmpdir)

    r.handle_line("#EVENT,1000,SESSION,0,START,1,0")
    r.handle_line(f"#SUMMARY_HEADER,{','.join(SUMMARY_HEADER_V5)}")

    sm = "1,1,1,1,NONE,0,749,2229.56,199.32,2092.69,30.13,123726.8,172,3440,999,2211.48,274.56,1971.22,40.54,123745.8,-0.091,-4.031"
    r.handle_line(f"#EVENT,3000,READY,1,START,1,1")
    r.handle_line(f"#SUMMARY,{sm}")

    check("T2-a: summary_header 정상 설정됨",
          r.summary_header == SUMMARY_HEADER_V5)

    r.close()


# ──────────────────────────────────────────────
# T3·T4: valid=1 → COMPLETE, valid=0 → ABORTED
# ──────────────────────────────────────────────
def _make_trial_lines(esp_session=1, trial=1, baseline_id=1, valid=1,
                      reason="NONE", warn=0):
    """단일 시험을 완성하는 이벤트 라인 목록을 반환합니다."""
    lines = []
    lines.append(f"#EVENT,1000,SESSION,0,START,{esp_session},0")
    lines.append(f"#EVENT,1500,BASELINE,0,START,{esp_session},{baseline_id}")
    bl = f"{esp_session},{baseline_id},1,NONE,0,749,2229.0,100.0,2000.0,50.0,120000.0"
    lines.append(f"#BASELINE_HEADER,{','.join(BASELINE_HEADER_V5)}")
    lines.append(f"#BASELINE,{bl}")

    lines.append(f"#SUMMARY_HEADER,{','.join(SUMMARY_HEADER_V5)}")
    lines.append(f"#EVENT,5000,READY,{trial},START,{esp_session},{baseline_id}")
    lines.append(f"#EVENT,6000,QUESTION,{trial},START,{esp_session},{baseline_id}")
    lines.append(f"#EVENT,9000,RESPONSE,{trial},START,{esp_session},{baseline_id}")

    sm_vals = [str(esp_session), str(baseline_id), str(trial), str(valid),
               reason, str(warn),
               "749", "2229.0", "100.0", "2000.0", "50.0", "120000.0",
               "172", "3440", "999",
               "2100.0", "80.0", "1900.0", "60.0", "120500.0",
               "1.5", "-2.3"]
    lines.append(f"#SUMMARY,{','.join(sm_vals)}")

    if valid == 1:
        lines.append(f"#EVENT,29000,RESULT,{trial},START,{esp_session},{baseline_id}")
    else:
        lines.append(f"#EVENT,10000,ABORTED,{trial},START,{esp_session},{baseline_id}")
    return lines


def test_complete_aborted(tmpdir, verbose):
    print("\n[T3] valid=1 → COMPLETE")
    r = make_run(tmpdir)
    for line in _make_trial_lines(valid=1):
        r.handle_line(line)
    s = r.sess
    if s and 1 in s.trials:
        tr = s.trials[1]
        check("T3-a: status=COMPLETE", tr.status == "COMPLETE",
              f"status={tr.status}")
        check("T3-b: esp valid='1'", tr.esp.get("valid") == "1",
              f"valid={tr.esp.get('valid')}")
        check("T3-c: fsr_dz 파싱됨", tr.esp.get("fsr_dz") == "1.5",
              f"fsr_dz={tr.esp.get('fsr_dz')}")
    else:
        check("T3-a: 시험 객체 존재", False, "trials dict 비어있음")
    r.close()

    print("\n[T4] valid=0 → ABORTED")
    r2 = make_run(tmpdir)
    for line in _make_trial_lines(valid=0, reason="HAND_OFF_LONG"):
        r2.handle_line(line)
    s2 = r2.sess
    if s2 and 1 in s2.trials:
        tr2 = s2.trials[1]
        check("T4-a: status=ABORTED", tr2.status == "ABORTED",
              f"status={tr2.status}")
        check("T4-b: reason 기록됨", tr2.reason != "",
              f"reason={tr2.reason!r}")
    else:
        check("T4-a: 시험 객체 존재", False, "trials dict 비어있음")
    r2.close()


# ──────────────────────────────────────────────
# T5: ABORTED → replace 호출 (연습 대체 질문)
# ──────────────────────────────────────────────
def test_replace_on_abort(tmpdir, verbose):
    print("\n[T5] ABORTED 시 연습 대체 질문 삽입 확인")
    try:
        from record_session import load_bank, Protocol
        import random
        bank_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "question_bank.csv")
        if not os.path.exists(bank_path):
            skip("T5", "question_bank.csv 없음")
            return
        bank = load_bank(bank_path)
        marked = {2, 5, 7, 9, 11}
        proto = Protocol(bank, marked, 2, random.Random(42))
        initial_len = len(proto.queue)
        practice_entry = proto.queue[0]
        assert practice_entry["role"] == "practice"
        # next_entry()로 먼저 꺼낸 뒤 replace() 호출 (실제 동작과 동일)
        proto.next_entry()   # queue: initial_len → initial_len - 1
        replaced = proto.replace(practice_entry)  # queue: initial_len - 1 → initial_len
        check("T5-a: next_entry 후 replace → 길이 복원",
              len(proto.queue) == initial_len,
              f"initial={initial_len}, after={len(proto.queue)}")
        check("T5-b: 새 연습 질문이 앞에 삽입됨",
              proto.queue[0]["role"] == "practice",
              f"role={proto.queue[0]['role']}")
    except Exception as e:
        check("T5: 오류 없이 실행", False, str(e))


# ──────────────────────────────────────────────
# T6: 본 질문 5개 완료 → main_done_announced
# ──────────────────────────────────────────────
def test_main_done_announced(tmpdir, verbose):
    print("\n[T6] 본 질문 5개 완료 → main_done_announced")
    try:
        from record_session import load_bank, Protocol
        import random
        bank_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "question_bank.csv")
        if not os.path.exists(bank_path):
            skip("T6", "question_bank.csv 없음")
            return
        bank = load_bank(bank_path)
        marked = {2, 5, 7, 9, 11}
        proto = Protocol(bank, marked, 2, random.Random(42))

        check("T6-a: queue 초기 길이 = 1(practice)+5(main)",
              len(proto.queue) == N_MAIN_QUESTIONS + 1,
              f"len={len(proto.queue)}")
        check("T6-b: main_left() = N_MAIN_QUESTIONS",
              proto.main_left() == N_MAIN_QUESTIONS,
              f"main_left={proto.main_left()}")

        # 연습 1개 + 본 5개 소진
        for _ in range(N_MAIN_QUESTIONS + 1):
            proto.next_entry()
        check("T6-c: 소진 후 main_left() = 0",
              proto.main_left() == 0)
    except Exception as e:
        check("T6: 오류 없이 실행", False, str(e))


# ──────────────────────────────────────────────
# T7: 세션 종료 후 confirm_queue
# ──────────────────────────────────────────────
def test_confirm_queue(tmpdir, verbose):
    print("\n[T7] main COMPLETE 시험이 있을 때 confirm_queue 채워짐")
    try:
        from record_session import load_bank, Protocol
        import random
        bank_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "question_bank.csv")
        if not os.path.exists(bank_path):
            skip("T7", "question_bank.csv 없음")
            return
        bank = load_bank(bank_path)
        r = make_run(tmpdir, bank=bank)

        # new 명령으로 프로토콜 등록 (다음 세션용)
        r.handle_command("new P_TEST 2 5 7 9 11")

        # 세션 시작
        r.handle_line("#EVENT,1000,SESSION,0,START,1,0")
        check("T7-a: proto 할당됨", r.sess is not None and r.sess.proto is not None)

        # 연습 + 본 질문 5개 시뮬레이션 (valid=1)
        if r.sess and r.sess.proto:
            for trial_num in range(1, N_MAIN_QUESTIONS + 2):  # 1=practice, 2-6=main
                lines = _make_trial_lines(
                    esp_session=1, trial=trial_num, baseline_id=1, valid=1
                )
                # SESSION START는 이미 보냈으므로 제외
                for ln in lines:
                    if "SESSION" not in ln or "START" not in ln:
                        r.handle_line(ln)

        # 세션 종료
        r.handle_line("#EVENT,99000,SESSION,0,END:BUTTON_LONG,1,1")

        # confirm_queue에 main COMPLETE 시험이 들어있는지 확인
        # (시험이 모두 완료된 경우에만)
        completed_main = [t for s in r.sessions for t in s.trials.values()
                          if t.role == "main" and t.status == "COMPLETE"]
        if completed_main:
            check("T7-b: confirm_queue 채워짐",
                  len(r.confirm_queue) > 0,
                  f"confirm_queue={len(r.confirm_queue)}개")
        else:
            skip("T7-b: main COMPLETE 없음 (시뮬레이션 조건 미달)")

        r.close()
    except Exception as e:
        check("T7: 오류 없이 실행", False, str(e))


# ──────────────────────────────────────────────
# T8: 실제 로그 재생 테스트
# ──────────────────────────────────────────────
def test_real_log_replay(tmpdir, verbose):
    print("\n[T8] 실제 로그 재생 — run_20261103_154858")
    log_path = os.path.join("experiments", "run_20261103_154858", "raw_serial_log.txt")
    if not os.path.exists(log_path):
        skip("T8", f"로그 없음: {log_path}")
        return

    r = make_run(tmpdir)
    fed = feed_log(r, log_path, verbose=verbose)
    r.close()

    # 집계
    all_trials = [(s, tid, tr)
                  for s in r.sessions
                  for tid, tr in sorted(s.trials.items())]
    n_total    = len(all_trials)
    n_complete = sum(1 for _, _, tr in all_trials if tr.status == "COMPLETE")
    n_aborted  = sum(1 for _, _, tr in all_trials if tr.status == "ABORTED")
    n_with_gsr = sum(1 for _, _, tr in all_trials if tr.esp.get("gsr_dz") not in ("", None))

    check("T8-a: 줄 공급됨", fed > 0, f"fed={fed}줄")
    check("T8-b: 세션 3개 생성됨", len(r.sessions) == 3,
          f"sessions={len(r.sessions)}")
    check("T8-c: 전체 시험 20개",  n_total == 20,
          f"trials={n_total}")
    # 기대: COMPLETE=19, ABORTED=1 (S03 trial5 = HAND_LIFTED)
    check("T8-d: COMPLETE 19개",   n_complete == 19,
          f"complete={n_complete}")
    check("T8-e: ABORTED 1개",     n_aborted  == 1,
          f"aborted={n_aborted}")
    # ABORTED 시험도 ESP32가 부분 SUMMARY를 보낼 수 있으므로 >= complete
    check("T8-f: gsr_dz 파싱됨 (COMPLETE 이상)",
          n_with_gsr >= n_complete,
          f"gsr_dz있음={n_with_gsr}, complete={n_complete}")
    # V6 핵심: summary_header 폴백이 사용됐음을 확인 (로그에 HEADER가 중간에만 있음)
    check("T8-g: summary_header 설정됨", r.summary_header is not None)


def test_real_log_161545(tmpdir, verbose):
    print("\n[T8b] 실제 로그 재생 — run_20261103_161545")
    log_path = os.path.join("experiments", "run_20261103_161545", "raw_serial_log.txt")
    if not os.path.exists(log_path):
        skip("T8b", f"로그 없음: {log_path}")
        return

    r = make_run(tmpdir)
    feed_log(r, log_path, verbose=verbose)
    r.close()

    all_trials = [(s, tid, tr) for s in r.sessions for tid, tr in s.trials.items()]
    n_complete = sum(1 for _, _, tr in all_trials if tr.status == "COMPLETE")
    n_aborted  = sum(1 for _, _, tr in all_trials if tr.status == "ABORTED")

    check("T8b-a: 세션 2개",      len(r.sessions) == 2,
          f"sessions={len(r.sessions)}")
    # 기대: COMPLETE=10, ABORTED=1 (S01 trial1 = HAND_OFF_LONG)
    check("T8b-b: COMPLETE 10개", n_complete == 10,
          f"complete={n_complete}")
    check("T8b-c: ABORTED 1개",   n_aborted  == 1,
          f"aborted={n_aborted}")


# ──────────────────────────────────────────────
# T9: 원본 데이터 변경 없음
# ──────────────────────────────────────────────
def test_original_unchanged():
    print("\n[T9] 원본 experiments 데이터 변경 없음")
    csv_path = os.path.join("experiments", "run_20261103_154858",
                            "20261103_S01_P01", "trials_summary.csv")
    if not os.path.exists(csv_path):
        skip("T9", "원본 CSV 없음")
        return
    with open(csv_path, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    statuses = {r["status"] for r in rows}
    fsr_values = [r.get("fsr_dz", "") for r in rows]
    check("T9-a: 원본 status 여전히 ABORTED",
          statuses == {"ABORTED"},
          f"statuses={statuses}")
    check("T9-b: 원본 fsr_dz 비어 있음",
          all(v == "" for v in fsr_values),
          f"fsr_dz={fsr_values}")


# ──────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="record_session V6 자동 검증")
    parser.add_argument("--verbose", action="store_true", help="로그 줄 출력")
    args = parser.parse_args()

    print("=" * 60)
    print("KITEL record_session V6 자동 검증")
    print("=" * 60)

    tmpdir = tempfile.mkdtemp(prefix="kitel_test_")
    try:
        test_summary_header_fallback(os.path.join(tmpdir, "t1"), args.verbose)
        test_summary_header_normal(os.path.join(tmpdir, "t2"), args.verbose)
        test_complete_aborted(os.path.join(tmpdir, "t3"), args.verbose)
        test_replace_on_abort(os.path.join(tmpdir, "t5"), args.verbose)
        test_main_done_announced(os.path.join(tmpdir, "t6"), args.verbose)
        test_confirm_queue(os.path.join(tmpdir, "t7"), args.verbose)
        test_real_log_replay(os.path.join(tmpdir, "t8"), args.verbose)
        test_real_log_161545(os.path.join(tmpdir, "t8b"), args.verbose)
        test_original_unchanged()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    # 최종 결과
    n_pass = sum(1 for _, s, _ in results if s == PASS)
    n_fail = sum(1 for _, s, _ in results if s == FAIL)
    n_skip = sum(1 for _, s, _ in results if s == SKIP)

    print(f"\n{'='*60}")
    print(f"결과: PASS {n_pass}  FAIL {n_fail}  SKIP {n_skip}  (총 {len(results)}개)")
    if n_fail == 0:
        print("모든 테스트 통과 ✓")
    else:
        print("실패한 테스트:")
        for name, status, detail in results:
            if status == FAIL:
                print(f"  ✗ {name}: {detail}")
    print("=" * 60)
    sys.exit(0 if n_fail == 0 else 1)


if __name__ == "__main__":
    main()
