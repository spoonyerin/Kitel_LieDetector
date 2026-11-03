#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
recover_data.py  —  KITEL 실험 데이터 복구 스크립트  (읽기 전용 모드)

experiments/ 폴더의 raw_serial_log.txt에서 SUMMARY·EVENT 데이터를 재파싱하여
유효성을 검증한 뒤 recovered_data/ 폴더에 복구 결과를 저장합니다.

[원본 데이터 보호]
  - experiments/ 폴더의 파일은 절대 수정하지 않습니다.
  - 복구 결과는 recovered_data/ 폴더에만 저장됩니다.

[복구 기준]
  - ESP32 SUMMARY valid=1  AND  RESULT 이벤트 존재  → recovered_status=COMPLETE
  - valid=0  OR  ABORTED 이벤트  OR  SUMMARY 없음   → recovered_status=ABORTED
  - role=practice → use_for_training=False (연습 질문은 학습 제외)
  - role=main + COMPLETE + condition∈{truth,lie}    → use_for_training=True

사용법:
    python recover_data.py                          # experiments/ 전체 처리
    python recover_data.py --run experiments/run_20261103_154858
    python recover_data.py --dry-run                # 저장 없이 분석만
"""

import argparse
import csv
import datetime as dt
import os
import sys

# ──────────────────────────────────────────────
# KITEL_MAIN_V5 고정 헤더 (SUMMARY_HEADER 미수신 시 폴백)
# ──────────────────────────────────────────────
SUMMARY_HEADER_V5 = [
    "session", "baseline_id", "trial", "valid", "reason", "warn_flags",
    "bl_n", "bl_fsr_mean", "bl_fsr_sd", "bl_gsr_mean", "bl_gsr_sd", "bl_ir_mean",
    "q_n", "q_ms", "rs_n", "rs_fsr_mean", "rs_fsr_sd", "rs_gsr_mean", "rs_gsr_sd",
    "rs_ir_mean", "fsr_dz", "gsr_dz",
]
BASELINE_HEADER_V5 = [
    "session", "baseline_id", "valid", "reason", "warn_flags",
    "n", "fsr_mean", "fsr_sd", "gsr_mean", "gsr_sd", "ir_mean",
]

# 복구 결과 CSV에 추가할 컬럼
RECOVERY_EXTRA_FIELDS = [
    "recovered_status",   # COMPLETE / ABORTED / ABORTED_NO_SUMMARY
    "valid_from_esp",     # ESP32 valid 값 (0/1/missing)
    "has_q_event",        # QUESTION 이벤트 존재 여부
    "has_rs_event",       # RESPONSE 이벤트 존재 여부
    "has_result_event",   # RESULT 이벤트 존재 여부
    "use_for_training",   # 학습 가능 여부 (True/False)
    "label_certainty",    # 라벨 신뢰도 (N/A / confirmed / plan_only)
    "exclude_reason",     # 학습 제외 사유
]


def read_csv_safe(path):
    """인코딩 자동 감지하여 CSV 읽기."""
    for enc in ("utf-8-sig", "utf-8", "cp949"):
        try:
            with open(path, newline="", encoding=enc) as f:
                return list(csv.DictReader(f))
        except (UnicodeDecodeError, FileNotFoundError):
            continue
    return []


def write_csv_utf8(path, fieldnames, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


# ──────────────────────────────────────────────
# raw_serial_log.txt 파싱
# ──────────────────────────────────────────────
def parse_raw_log(log_path):
    """
    raw_serial_log.txt를 파싱하여 다음을 반환합니다.
    summary_map : {(esp_session, trial_id): dict}
    event_map   : {(esp_session, trial_id): [events]}  event = (name, detail)
    baseline_map: {(esp_session, baseline_id): dict}
    """
    summary_header = None
    baseline_header = None
    summary_map = {}     # (esp_session, trial_id) → summary dict
    event_map = {}       # (esp_session, trial_id) → list of (name, detail)
    baseline_map = {}    # (esp_session, baseline_id) → baseline dict

    if not os.path.exists(log_path):
        return summary_map, event_map, baseline_map

    with open(log_path, encoding="utf-8", errors="replace") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            # 타임스탬프 제거: "2026-11-03 15:50:31.379\t#SUMMARY,..."
            if "\t" in raw:
                _, line = raw.split("\t", 1)
            else:
                line = raw

            # SUMMARY_HEADER
            if line.startswith("#SUMMARY_HEADER,"):
                summary_header = line.split(",")[1:]
                continue

            # BASELINE_HEADER
            if line.startswith("#BASELINE_HEADER,"):
                baseline_header = line.split(",")[1:]
                continue

            # SUMMARY
            if line.startswith("#SUMMARY,"):
                values = line.split(",")[1:]
                hdr = summary_header or (SUMMARY_HEADER_V5 if len(values) == len(SUMMARY_HEADER_V5) else None)
                if hdr and len(values) == len(hdr):
                    d = dict(zip(hdr, values))
                    try:
                        key = (int(d["session"]), int(d["trial"]))
                        summary_map[key] = d
                    except (KeyError, ValueError):
                        pass
                continue

            # BASELINE
            if line.startswith("#BASELINE,"):
                values = line.split(",")[1:]
                hdr = baseline_header or (BASELINE_HEADER_V5 if len(values) == len(BASELINE_HEADER_V5) else None)
                if hdr and len(values) == len(hdr):
                    d = dict(zip(hdr, values))
                    try:
                        key = (int(d["session"]), int(d["baseline_id"]))
                        baseline_map[key] = d
                    except (KeyError, ValueError):
                        pass
                continue

            # EVENT
            if line.startswith("#EVENT,"):
                parts = line.split(",")
                if len(parts) >= 7:
                    try:
                        trial_id = int(parts[3])
                        esp_session = int(parts[5])
                        name = parts[2]
                        detail = parts[4]
                        key = (esp_session, trial_id)
                        event_map.setdefault(key, []).append((name, detail))
                    except (ValueError, IndexError):
                        pass
                continue

    return summary_map, event_map, baseline_map


def build_session_mapping(run_dir):
    """
    run_events.csv에서 esp_session ↔ session_id 매핑을 구성합니다.
    반환: {esp_session_int: session_id_str}
    """
    mapping = {}
    evf = os.path.join(run_dir, "run_events.csv")
    rows = read_csv_safe(evf)
    for r in rows:
        if r.get("event") == "SESSION" and r.get("detail") == "START":
            try:
                esp_s = int(r["esp_session"])
                sid = r.get("session_id", "")
                if sid and esp_s not in mapping:
                    mapping[esp_s] = sid
            except (ValueError, KeyError):
                pass
    # 폴백: 세션 폴더 이름 순서로 매핑
    if not mapping:
        subdirs = sorted(
            d for d in os.listdir(run_dir)
            if os.path.isdir(os.path.join(run_dir, d)) and not d.startswith(".")
        )
        for i, d in enumerate(subdirs, 1):
            mapping[i] = d
    return mapping


def determine_validity(trial_id, esp_session, summary_map, event_map):
    """
    단일 시험의 복구 유효성을 판정합니다.
    반환: (recovered_status, valid_from_esp, has_q, has_rs, has_result, notes)
    """
    key = (esp_session, trial_id)
    summary = summary_map.get(key)
    events = event_map.get(key, [])

    # 이벤트 확인
    has_q      = any(n == "QUESTION" and d == "START" for n, d in events)
    has_rs     = any(n == "RESPONSE" and d == "START" for n, d in events)
    has_result = any(n == "RESULT"   and d == "START" for n, d in events)
    has_abort  = any(n == "ABORTED"  and d == "START" for n, d in events)

    if summary is None:
        return "ABORTED_NO_SUMMARY", "missing", has_q, has_rs, has_result

    valid = summary.get("valid", "0")
    reason = summary.get("reason", "")

    if valid == "1" and has_result and not has_abort:
        return "COMPLETE", valid, has_q, has_rs, has_result
    else:
        return "ABORTED", valid, has_q, has_rs, has_result


def recover_session(session_dir, esp_session, summary_map, event_map, dry_run, out_dir):
    """
    한 세션의 trials_summary.csv를 복구하여 out_dir에 저장합니다.
    반환: (n_complete, n_aborted, n_trainable) 통계
    """
    src_csv = os.path.join(session_dir, "trials_summary.csv")
    rows = read_csv_safe(src_csv)
    if not rows:
        return 0, 0, 0

    # 기존 필드 + 복구 추가 필드
    original_fields = list(rows[0].keys()) if rows else []
    # 중복 방지
    extra = [f for f in RECOVERY_EXTRA_FIELDS if f not in original_fields]
    out_fields = original_fields + extra

    recovered_rows = []
    n_complete = n_aborted = n_trainable = 0

    for row in rows:
        row = dict(row)  # 복사

        try:
            tid = int(row.get("trial_id", 0))
        except ValueError:
            tid = 0

        rec_status, valid_esp, has_q, has_rs, has_result = determine_validity(
            tid, esp_session, summary_map, event_map
        )

        # SUMMARY 통계 채우기 (빈 경우)
        key = (esp_session, tid)
        summary = summary_map.get(key)
        if summary:
            for col in ["bl_n", "bl_fsr_mean", "bl_fsr_sd", "bl_gsr_mean", "bl_gsr_sd",
                        "bl_ir_mean", "q_n", "q_ms", "rs_n", "rs_fsr_mean", "rs_fsr_sd",
                        "rs_gsr_mean", "rs_gsr_sd", "rs_ir_mean", "fsr_dz", "gsr_dz",
                        "warn_flags"]:
                if not row.get(col) and summary.get(col) is not None:
                    row[col] = summary[col]
            if not row.get("reason") and summary.get("reason"):
                row["reason"] = summary["reason"]

        # 복구 컬럼 설정
        role = row.get("role", "")
        cond = row.get("planned_condition", row.get("condition", ""))

        row["recovered_status"] = rec_status
        row["valid_from_esp"]   = valid_esp
        row["has_q_event"]      = "Y" if has_q else "N"
        row["has_rs_event"]     = "Y" if has_rs else "N"
        row["has_result_event"] = "Y" if has_result else "N"

        # 학습 가능 여부 판정
        if rec_status == "COMPLETE" and role == "main" and cond in ("truth", "lie"):
            row["use_for_training"] = "True"
            row["label_certainty"]  = "confirmed" if row.get("confirmed") == "as_planned" else "plan_only"
            row["exclude_reason"]   = ""
            n_trainable += 1
        else:
            row["use_for_training"] = "False"
            row["label_certainty"]  = "N/A" if role == "practice" else (
                "plan_only" if cond in ("truth", "lie") else "unknown"
            )
            if role == "practice":
                row["exclude_reason"] = "practice_question"
            elif rec_status != "COMPLETE":
                row["exclude_reason"] = f"invalid_measurement({rec_status})"
            elif cond not in ("truth", "lie"):
                row["exclude_reason"] = f"no_label(condition={cond})"
            else:
                row["exclude_reason"] = "unknown"

        if rec_status == "COMPLETE":
            n_complete += 1
        else:
            n_aborted += 1

        recovered_rows.append(row)

    if not dry_run and out_dir:
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, "trials_summary_recovered.csv")
        write_csv_utf8(out_path, out_fields, recovered_rows)

    return n_complete, n_aborted, n_trainable


def recover_run(run_dir, out_root, dry_run):
    """
    단일 run 폴더 처리. 복구 결과를 out_root/<run_name>/ 에 저장.
    반환: 통계 dict
    """
    run_name = os.path.basename(run_dir)
    out_run_dir = os.path.join(out_root, run_name)
    log_path = os.path.join(run_dir, "raw_serial_log.txt")

    print(f"\n{'='*60}")
    print(f"처리 중: {run_name}")

    summary_map, event_map, baseline_map = parse_raw_log(log_path)
    print(f"  SUMMARY 데이터: {len(summary_map)}개 파싱됨")

    esp_session_map = build_session_mapping(run_dir)
    print(f"  세션 매핑: {esp_session_map}")

    all_stats = []
    run_report_rows = []
    total_complete = total_aborted = total_trainable = 0

    # 세션 서브폴더 처리
    for esp_s, session_id in sorted(esp_session_map.items()):
        session_dir = os.path.join(run_dir, session_id)
        if not os.path.isdir(session_dir):
            print(f"  [경고] 세션 폴더 없음: {session_id}")
            continue

        out_sess_dir = os.path.join(out_run_dir, session_id) if not dry_run else None

        n_ok, n_ab, n_tr = recover_session(
            session_dir, esp_s, summary_map, event_map, dry_run, out_sess_dir
        )
        total_complete += n_ok
        total_aborted += n_ab
        total_trainable += n_tr

        print(f"  [{session_id}] COMPLETE={n_ok}  ABORTED={n_ab}  학습가능={n_tr}")

        run_report_rows.append({
            "run": run_name,
            "session_id": session_id,
            "esp_session": esp_s,
            "n_complete": n_ok,
            "n_aborted": n_ab,
            "n_trainable": n_tr,
        })

    # run 요약 CSV 저장
    if not dry_run and run_report_rows:
        os.makedirs(out_run_dir, exist_ok=True)
        write_csv_utf8(
            os.path.join(out_run_dir, "recovery_summary.csv"),
            ["run", "session_id", "esp_session", "n_complete", "n_aborted", "n_trainable"],
            run_report_rows,
        )

    return {
        "run": run_name,
        "n_sessions": len(esp_session_map),
        "n_complete": total_complete,
        "n_aborted": total_aborted,
        "n_trainable": total_trainable,
    }


def main():
    parser = argparse.ArgumentParser(description="KITEL 데이터 복구 스크립트")
    parser.add_argument("--run", help="특정 run 폴더 경로 (생략 시 experiments/ 전체)")
    parser.add_argument("--exp-dir", default="experiments", help="실험 폴더 경로 (기본: experiments)")
    parser.add_argument("--out-dir", default="recovered_data", help="복구 결과 폴더 (기본: recovered_data)")
    parser.add_argument("--dry-run", action="store_true", help="저장 없이 분석만 수행")
    args = parser.parse_args()

    if args.dry_run:
        print("[DRY RUN] 파일 저장 없이 분석만 수행합니다.")

    # 처리할 run 폴더 목록
    if args.run:
        run_dirs = [args.run]
    else:
        if not os.path.isdir(args.exp_dir):
            print(f"[오류] 실험 폴더 없음: {args.exp_dir}")
            sys.exit(1)
        run_dirs = sorted(
            os.path.join(args.exp_dir, d)
            for d in os.listdir(args.exp_dir)
            if d.startswith("run_") and os.path.isdir(os.path.join(args.exp_dir, d))
        )
        print(f"발견한 run 폴더: {len(run_dirs)}개")

    all_stats = []
    for rd in run_dirs:
        stats = recover_run(rd, args.out_dir, args.dry_run)
        all_stats.append(stats)

    # 전체 요약 출력
    print(f"\n{'='*60}")
    print("복구 결과 요약")
    print(f"{'='*60}")
    total_c = total_a = total_t = 0
    for s in all_stats:
        print(f"  {s['run']}: 세션 {s['n_sessions']}개 | "
              f"COMPLETE {s['n_complete']} / ABORTED {s['n_aborted']} | "
              f"학습가능 {s['n_trainable']}")
        total_c += s["n_complete"]
        total_a += s["n_aborted"]
        total_t += s["n_trainable"]

    print(f"\n  전체: COMPLETE {total_c}개 / ABORTED {total_a}개 / 학습가능 {total_t}개")

    if total_t == 0:
        print("\n  [참고] 학습 가능 데이터 없음 —")
        print("         오늘 세션의 모든 시험은 연습(practice) 질문이었습니다.")
        print("         SUMMARY 통계값(fsr_dz, gsr_dz 등)은 recovered CSV에 복구됩니다.")

    # 전체 summary CSV 저장
    if not args.dry_run and all_stats:
        os.makedirs(args.out_dir, exist_ok=True)
        ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(args.out_dir, f"recovery_report_{ts}.csv")
        write_csv_utf8(
            out_path,
            ["run", "n_sessions", "n_complete", "n_aborted", "n_trainable"],
            all_stats,
        )
        print(f"\n  전체 보고서: {os.path.abspath(out_path)}")
        print(f"  복구 폴더  : {os.path.abspath(args.out_dir)}")


if __name__ == "__main__":
    main()
