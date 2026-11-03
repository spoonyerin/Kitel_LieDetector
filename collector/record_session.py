#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KITEL record_session.py  V6  (ESP32 KITEL_MAIN_V5 펌웨어용 수집기)

[실험 모드]  question_bank.csv 의 15문항 중 참가자가 "거짓"으로 표시한 번호만
입력하면, 프로그램이 질문을 고르고(연습 1 + 본 5), 순서를 섞고, 진실/거짓 라벨을
자동으로 기록한다. 질문자 화면에는 질문만 나오고 라벨은 세션이 끝날 때까지 숨긴다.

실행 (명령 프롬프트, LieDetector 폴더에서):
    python record_session.py
    python record_session.py --participant P01 --port COM6
    python record_session.py --check experiments\\run_20261103_140000

실행 중 입력 명령 (입력 후 Enter):
    new P01 2 5 9 11 14   참가자 P01, 기록지에서 "거짓" 표시한 번호 (Baseline 전에 입력)
    fix 3 l               질문 3번 라벨을 거짓으로 수정 (t=진실, l=거짓, x=분석 제외)
    fix l                 방금 끝난 질문 라벨 수정
    t / l / x             (수동 모드) 현재 질문 조건 입력
    p P02                 다음 세션 참가자 ID만 지정 (수동 모드)
    s                     현재 상태
    h                     도움말
    quit                  저장하고 종료 (Ctrl+C도 가능)

  세션 종료 후 확인 단계:
    물었던 본 질문 5개를 하나씩 보여 준다. 참가자에게 실제로 어떻게 답했는지 묻고
    Enter = 계획대로 답함 / t = 실제로 진실 / l = 실제로 거짓 / x = 헷갈림(제외)

저장 구조:
    experiments\\run_<날짜시각>\\
        raw_serial_log.txt / run_events.csv
        all_trials_summary.csv / all_baselines.csv
        <session_id>\\
            protocol.csv          15문항, 거짓 표시, 실제로 물은 순서, 계획/최종 라벨
            participant_P01_baseline01_samples.csv
            participant_P01_trial01_samples.csv / _events.csv
            trials_summary.csv / baselines.csv

주의: Arduino IDE 시리얼 모니터를 닫은 뒤 실행 (COM 포트는 하나만 열 수 있음).
      포트를 열면 ESP32가 재부팅된다. 이는 정상이다.
"""

import argparse
import csv
import datetime as dt
import glob
import os
import queue
import random
import re
import sys
import threading
import time

try:
    import serial
except ImportError:
    print("pyserial이 필요합니다:  pip install pyserial")
    sys.exit(1)

FW_SAMPLE_HEADER = "time_ms,fsr,gsr,ir,red,phase,trial,session,baseline_id,seq,contact"
N_SAMPLE_FIELDS = len(FW_SAMPLE_HEADER.split(","))
SAMPLE_PERIOD_MS = 20
GAP_LIMIT_MS = 30
DURATION_TOL_MS = 50

TRIAL_PHASES = ["READY", "QUESTION", "RESPONSE"]
BASELINE_PHASES = ["BASELINE"]
EXPECTED_MS = {"BASELINE": 15000, "RESPONSE": 20000}

COND_KEYS = {"t": "truth", "l": "lie", "x": "test"}
COND_KR = {"truth": "진실로 답하게 할 것", "lie": "거짓으로 답하게 할 것",
           "test": "테스트(분석 제외)", "unlabeled": "미입력"}

WARN_BITS = {1: "LOW_SAMPLES", 2: "LOW_IR", 4: "FSR_SAT", 8: "GSR_SAT",
             16: "CONTACT_DIP", 32: "PPG_OVERFLOW"}

# 펌웨어 KITEL_MAIN_V5 고정 헤더 — SUMMARY_HEADER/BASELINE_HEADER 미수신 시 폴백
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

TRIAL_SAMPLE_FIELDS = ["session_id", "participant_id", "trial_id", "baseline_id",
                       "question_id", "condition",
                       "time_ms", "fsr", "gsr", "ir", "red", "phase", "seq", "contact"]
BASELINE_SAMPLE_FIELDS = ["session_id", "participant_id", "baseline_id",
                          "time_ms", "fsr", "gsr", "ir", "red", "phase", "seq", "contact"]
EVENT_FIELDS = ["session_id", "participant_id", "trial_id", "baseline_id",
                "question_id", "condition", "pc_time", "esp_time_ms", "event", "detail"]

N_MAIN_QUESTIONS = 5
WAIT_MIN_S, WAIT_MAX_S = 3, 10
DEFAULT_BANK = "question_bank.csv"

TRIAL_SUMMARY_FIELDS = [
    "session_id", "participant_id", "trial_id", "baseline_id", "question_id",
    "condition", "planned_condition", "confirmed", "role", "wait_s",
    "question_text", "status", "reason", "warn_flags", "warn_names",
    "integrity", "issues",
    "n_samples", "n_ready", "n_question", "n_response",
    "question_ms_event", "response_ms_event",
    "rate_hz", "dup_ts", "backwards_ts", "seq_missing", "time_gaps", "max_gap_ms",
    "phase_order_ok", "phase_mismatch", "contact0", "fsr_sat", "gsr_sat",
    "bl_n", "bl_fsr_mean", "bl_fsr_sd", "bl_gsr_mean", "bl_gsr_sd", "bl_ir_mean",
    "q_n", "q_ms", "rs_n", "rs_fsr_mean", "rs_fsr_sd", "rs_gsr_mean", "rs_gsr_sd",
    "rs_ir_mean", "fsr_dz", "gsr_dz",
]
BASELINE_SUMMARY_FIELDS = [
    "session_id", "participant_id", "baseline_id", "status", "reason",
    "warn_flags", "warn_names", "integrity", "issues",
    "n_samples", "baseline_ms_event", "rate_hz", "dup_ts", "seq_missing",
    "time_gaps", "max_gap_ms", "contact0", "fsr_sat", "gsr_sat",
    "n", "fsr_mean", "fsr_sd", "gsr_mean", "gsr_sd", "ir_mean",
]


def now_str():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def warn_names(flags):
    try:
        f = int(flags)
    except (TypeError, ValueError):
        return ""
    return "|".join(name for bit, name in WARN_BITS.items() if f & bit)


def next_pid(pid):
    m = re.match(r"^(.*?)(\d+)$", pid)
    if not m:
        return pid + "_2"
    head, num = m.groups()
    return f"{head}{int(num) + 1:0{len(num)}d}"


def write_csv(path, fields, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(fields)
        w.writerows(rows)


def write_dict_csv(path, fields, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


# ----------------------------------------------------------
# 선택: 질문 목록 파일 (개수 제한 아님. 기본값 제안용)
# ----------------------------------------------------------
def load_plan(path):
    rows = None
    for enc in ("utf-8-sig", "cp949"):
        try:
            with open(path, newline="", encoding=enc) as f:
                rows = list(csv.DictReader(f))
            break
        except UnicodeDecodeError:
            continue
    if rows is None:
        raise ValueError("목록 파일 인코딩을 읽을 수 없습니다 (UTF-8 또는 CP949로 저장).")
    plan = []
    for i, r in enumerate(rows, 1):
        cond = (r.get("condition") or "").strip().lower()
        if cond not in ("truth", "lie", "test", ""):
            raise ValueError(f"목록 {i}행 condition 오류: {cond!r}")
        plan.append({"question_id": (r.get("question_id") or "").strip(),
                     "condition": cond or "unlabeled",
                     "question_text": (r.get("question_text") or "").strip()})
    return plan


# ----------------------------------------------------------
# 실험 프로토콜: 질문 은행 + 참가자별 질문 선택
# ----------------------------------------------------------
def load_bank(path):
    rows = None
    for enc in ("utf-8-sig", "cp949"):
        try:
            with open(path, newline="", encoding=enc) as f:
                rows = list(csv.DictReader(f))
            break
        except UnicodeDecodeError:
            continue
    if not rows:
        raise ValueError(f"질문 은행을 읽을 수 없습니다: {path}")
    bank = {}
    for r in rows:
        n = int(r["no"])
        bank[n] = {"no": n, "question_id": r["item_id"].strip(), "question_text": r["question_text"].strip()}
    return bank


class Protocol:
    """참가자 1명분 질문 계획. 라벨은 세션이 끝날 때까지 화면에 표시하지 않는다."""

    def __init__(self, bank, marked, n_lies, rng):
        self.bank = bank
        self.marked = sorted(marked)
        self.n_lies = n_lies
        self.rng = rng
        unmarked = [n for n in bank if n not in marked]
        lies = rng.sample(self.marked, n_lies)
        truths = rng.sample(unmarked, N_MAIN_QUESTIONS - n_lies)
        rest = [n for n in unmarked if n not in truths]
        practice = rng.choice(rest)
        main = [(n, "lie") for n in lies] + [(n, "truth") for n in truths]
        rng.shuffle(main)
        self.used = set(lies) | set(truths) | {practice}
        self.queue = [self._entry(practice, "test", "practice")] + [self._entry(n, c, "main") for n, c in main]
        self.asked = []          # (trial_id, entry)

    def _entry(self, n, cond, role):
        q = self.bank[n]
        return {"no": n, "question_id": q["question_id"], "question_text": q["question_text"],
                "condition": cond, "planned": cond, "role": role,
                "wait_s": self.rng.randint(WAIT_MIN_S, WAIT_MAX_S)}

    def next_entry(self):
        return self.queue.pop(0) if self.queue else None

    def replace(self, entry):
        """무효가 된 질문 대신 같은 조건의 새 질문을 맨 앞에 넣는다 (이미 들은 질문 재사용 방지)."""
        pool = self.marked if entry["planned"] == "lie" else [n for n in self.bank if n not in self.marked]
        if entry["role"] == "practice":
            pool = [n for n in self.bank if n not in self.marked]
        free = [n for n in pool if n not in self.used]
        if free:
            n = self.rng.choice(free)
            self.used.add(n)
            new = self._entry(n, entry["planned"], entry["role"])
            replaced = True
        else:
            new = dict(entry, wait_s=self.rng.randint(WAIT_MIN_S, WAIT_MAX_S))
            replaced = False
        self.queue.insert(0, new)
        return replaced

    def main_left(self):
        return sum(1 for e in self.queue if e["role"] == "main")


def parse_new_command(rest, bank):
    """'P03 2 5 9 11 14' -> ('P03', {2,5,9,11,14})"""
    parts = rest.replace(",", " ").split()
    if len(parts) < 2:
        raise ValueError("사용법: new P03 2 5 9 11 14   (참가자 ID, 그 뒤에 거짓 표시 번호)")
    pid = parts[0]
    try:
        marked = {int(x) for x in parts[1:]}
    except ValueError:
        raise ValueError("번호는 숫자로 입력하세요. 예: new P03 2 5 9 11 14")
    bad = sorted(n for n in marked if n not in bank)
    if bad:
        raise ValueError(f"질문 은행에 없는 번호: {bad} (1~{max(bank)})")
    unmarked = len(bank) - len(marked)
    if len(marked) < 3:
        raise ValueError(f"거짓 표시는 최소 3개 필요합니다 (현재 {len(marked)}개). 권장 5개.")
    if unmarked < 4:
        raise ValueError(f"표시 안 한 문항이 최소 4개 필요합니다 (현재 {unmarked}개).")
    return pid, marked


def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


# ----------------------------------------------------------
# 무결성 검사
# samples: (time_ms, fsr, gsr, ir, red, phase, seq, contact)
# events : (pc_time, esp_time_ms, name, detail)
# ----------------------------------------------------------
def check_segment(samples, events, phases):
    r = {}
    issues = []
    n = len(samples)
    r["n_samples"] = n
    for ph in phases:
        r[f"n_{ph.lower()}"] = sum(1 for s in samples if s[5] == ph)

    times = [s[0] for s in samples]
    seqs = [s[6] for s in samples]
    diffs = [b - a for a, b in zip(times, times[1:])]
    r["dup_ts"] = sum(1 for d in diffs if d == 0)
    r["backwards_ts"] = sum(1 for d in diffs if d < 0)
    r["time_gaps"] = sum(1 for d in diffs if d > GAP_LIMIT_MS)
    r["max_gap_ms"] = max(diffs) if diffs else 0
    r["seq_missing"] = sum(b - a - 1 for a, b in zip(seqs, seqs[1:]) if b - a > 1)
    span = (times[-1] - times[0]) if n > 1 else 0
    r["rate_hz"] = round((n - 1) / (span / 1000.0), 2) if span > 0 else 0

    starts, ends = {}, {}
    for _, t, name, detail in events:
        if detail == "START" and name not in starts:
            starts[name] = t
        elif detail == "END":
            ends[name] = t
    for ph in phases:
        if ph == "READY":
            continue
        r[f"{ph.lower()}_ms_event"] = (ends[ph] - starts[ph]) if (ph in starts and ph in ends) else ""

    order = []
    for s in samples:
        if not order or order[-1] != s[5]:
            order.append(s[5])
    idx = [phases.index(p) if p in phases else -1 for p in order]
    r["phase_order_ok"] = int(-1 not in idx and all(b > a for a, b in zip(idx, idx[1:])))

    mismatch = 0
    for s in samples:
        ph = s[5]
        if ph in starts:
            en = ends.get(ph)
            if s[0] < starts[ph] or (en is not None and s[0] >= en + SAMPLE_PERIOD_MS):
                mismatch += 1
    r["phase_mismatch"] = mismatch
    r["contact0"] = sum(1 for s in samples if s[7] == 0)
    r["fsr_sat"] = sum(1 for s in samples if s[1] >= 4095)
    r["gsr_sat"] = sum(1 for s in samples if s[2] <= 0 or s[2] >= 4095)

    if n == 0:
        issues.append("NO_SAMPLES")
    if r["dup_ts"]:
        issues.append(f"DUP_TS={r['dup_ts']}")
    if r["backwards_ts"]:
        issues.append(f"BACKWARDS_TS={r['backwards_ts']}")
    if r["seq_missing"]:
        issues.append(f"SERIAL_LOST={r['seq_missing']}")
    if r["time_gaps"]:
        issues.append(f"TIME_GAPS={r['time_gaps']}(max {r['max_gap_ms']}ms)")
    if n > 1 and not (45 <= r["rate_hz"] <= 55):
        issues.append(f"RATE={r['rate_hz']}Hz")
    for ph, exp in EXPECTED_MS.items():
        if ph not in phases:
            continue
        v = r.get(f"{ph.lower()}_ms_event", "")
        if v != "" and abs(v - exp) > DURATION_TOL_MS:
            issues.append(f"{ph}_DURATION={v}ms")
        cnt = r[f"n_{ph.lower()}"]
        if v != "" and cnt < 0.9 * exp / SAMPLE_PERIOD_MS:
            issues.append(f"{ph}_FEW_SAMPLES={cnt}")
    if not r["phase_order_ok"]:
        issues.append("PHASE_ORDER")
    if mismatch:
        issues.append(f"PHASE_MISMATCH={mismatch}")
    if r["contact0"]:
        issues.append(f"NO_CONTACT_SAMPLES={r['contact0']}")
    if r["fsr_sat"]:
        issues.append(f"FSR_4095={r['fsr_sat']}")
    if r["gsr_sat"]:
        issues.append(f"GSR_SAT={r['gsr_sat']}")

    r["issues"] = ";".join(issues)
    r["integrity"] = "OK" if not issues else "CHECK"
    return r


# ----------------------------------------------------------
# Baseline / Trial / Session
# ----------------------------------------------------------
class Baseline:
    def __init__(self, sess, bid):
        self.sess = sess
        self.bid = bid
        self.samples = []
        self.events = []
        self.status = "RUNNING"
        self.reason = ""
        self.esp = {}
        self.report = None

    def path(self):
        return os.path.join(self.sess.dir,
                            f"participant_{self.sess.pid}_baseline{self.bid:02d}_samples.csv")

    def write(self):
        rows = [[self.sess.session_id, self.sess.pid, self.bid, *s] for s in self.samples]
        write_csv(self.path(), BASELINE_SAMPLE_FIELDS, rows)

    def summary_row(self):
        row = {"session_id": self.sess.session_id, "participant_id": self.sess.pid,
               "baseline_id": self.bid, "status": self.status, "reason": self.reason}
        if self.report:
            row.update(self.report)
        row.update({k: v for k, v in self.esp.items() if k not in ("baseline_id", "reason")})
        row["warn_names"] = warn_names(self.esp.get("warn_flags"))
        return row


class Trial:
    def __init__(self, sess, tid, bid, label):
        self.sess = sess
        self.tid = tid
        self.bid = bid
        self.qid = label.get("question_id") or f"Q{tid:02d}"
        self.cond = label.get("condition", "unlabeled")
        self.qtext = label.get("question_text", "")
        self.planned = label.get("planned", "")
        self.role = label.get("role", "")
        self.wait_s = label.get("wait_s", "")
        self.entry = label if self.role else None
        self.confirmed = ""
        self.samples = []
        self.events = []
        self.status = "RUNNING"
        self.reason = ""
        self.esp = {}
        self.report = None

    def base(self):
        return os.path.join(self.sess.dir, f"participant_{self.sess.pid}_trial{self.tid:02d}")

    def write(self):
        sid, pid = self.sess.session_id, self.sess.pid
        rows = [[sid, pid, self.tid, self.bid, self.qid, self.cond, *s] for s in self.samples]
        write_csv(self.base() + "_samples.csv", TRIAL_SAMPLE_FIELDS, rows)
        erows = [[sid, pid, self.tid, self.bid, self.qid, self.cond, *e] for e in self.events]
        write_csv(self.base() + "_events.csv", EVENT_FIELDS, erows)

    def summary_row(self):
        row = {"session_id": self.sess.session_id, "participant_id": self.sess.pid,
               "trial_id": self.tid, "baseline_id": self.bid, "question_id": self.qid,
               "condition": self.cond, "planned_condition": self.planned,
               "confirmed": self.confirmed, "role": self.role, "wait_s": self.wait_s,
               "question_text": self.qtext,
               "status": self.status, "reason": self.reason}
        if self.report:
            row.update(self.report)
        row.update({k: v for k, v in self.esp.items()
                    if k in TRIAL_SUMMARY_FIELDS and k not in ("baseline_id", "reason")})
        row["warn_names"] = warn_names(self.esp.get("warn_flags"))
        return row


class Session:
    def __init__(self, run, esp_num, pid, index):
        self.run = run
        self.esp_num = esp_num
        self.pid = pid
        self.session_id = f"{run.date}_S{index:02d}_{pid}"
        self.dir = os.path.join(run.dir, self.session_id)
        os.makedirs(self.dir, exist_ok=True)
        self.baselines = {}
        self.trials = {}
        self.cur_baseline = None
        self.cur_trial = None
        self.last_finished = None
        self.events = []
        self.active = True
        self.proto = None
        self.main_done_announced = False

    def write_protocol(self):
        if not self.proto:
            return
        asked = {}
        for tid, e in self.proto.asked:
            asked.setdefault(e["no"], []).append(tid)
        rows = []
        for n, q in sorted(self.proto.bank.items()):
            tids = asked.get(n, [])
            trs = [self.trials[t] for t in tids if t in self.trials]
            rows.append({
                "no": n, "item_id": q["question_id"], "question_text": q["question_text"],
                "marked_lie": int(n in self.proto.marked),
                "asked_trials": " ".join(str(t) for t in tids),
                "role": " ".join(t.role for t in trs),
                "planned_condition": " ".join(t.planned for t in trs),
                "final_condition": " ".join(t.cond for t in trs),
                "status": " ".join(t.status for t in trs),
            })
        write_dict_csv(os.path.join(self.dir, "protocol.csv"),
                       ["no", "item_id", "question_text", "marked_lie", "asked_trials", "role",
                        "planned_condition", "final_condition", "status"], rows)

    def write_summaries(self):
        self.write_protocol()
        write_dict_csv(os.path.join(self.dir, "trials_summary.csv"), TRIAL_SUMMARY_FIELDS,
                       [t.summary_row() for _, t in sorted(self.trials.items())])
        write_dict_csv(os.path.join(self.dir, "baselines.csv"), BASELINE_SUMMARY_FIELDS,
                       [b.summary_row() for _, b in sorted(self.baselines.items())])


class Run:
    def __init__(self, out_root, pid, plan, bank=None, clear=True):
        self.bank = bank or {}
        self.clear = clear
        self.pending_proto = None
        self.proto_count = 0
        self.rng = random.Random()
        self.ready_wait = None        # (시작 monotonic, 대기 초, trial)
        self.confirm_queue = []       # 세션 종료 후 확인할 Trial 목록
        self.date = dt.datetime.now().strftime("%Y%m%d")
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.dir = os.path.join(out_root, f"run_{stamp}")
        os.makedirs(self.dir, exist_ok=True)

        self.next_participant = pid
        self.plan = plan
        self.sessions = []
        self.sess = None
        self.pending_label = None
        self.summary_header = None
        self.baseline_header = None
        self.header_ok = None
        self.bad_lines = 0
        self.total_samples = 0

        self.raw = open(os.path.join(self.dir, "raw_serial_log.txt"), "w", encoding="utf-8")
        self.evf = open(os.path.join(self.dir, "run_events.csv"), "w", newline="", encoding="utf-8-sig")
        self.evw = csv.writer(self.evf)
        self.evw.writerow(["pc_time", "esp_time_ms", "event", "trial", "detail",
                           "esp_session", "baseline_id", "session_id"])

    # ---------- 공통 ----------
    def log_event(self, pc, t, name, trial, detail, esp_s, bid):
        sid = self.sess.session_id if self.sess else ""
        self.evw.writerow([pc, t, name, trial, detail, esp_s, bid, sid])
        self.evf.flush()

    def write_run_summaries(self):
        trows, brows = [], []
        for s in self.sessions:
            trows += [t.summary_row() for _, t in sorted(s.trials.items())]
            brows += [b.summary_row() for _, b in sorted(s.baselines.items())]
        write_dict_csv(os.path.join(self.dir, "all_trials_summary.csv"), TRIAL_SUMMARY_FIELDS, trows)
        write_dict_csv(os.path.join(self.dir, "all_baselines.csv"), BASELINE_SUMMARY_FIELDS, brows)

    # ---------- 한 줄 처리 ----------
    def handle_line(self, line):
        pc = now_str()
        self.raw.write(f"{pc}\t{line}\n")
        if not line:
            return
        if line.startswith("#BOOT"):
            print(f"\n[ESP32] 부팅됨: {line}")
            if self.sess and self.sess.active:
                self.end_session("ESP32_RESET")
            self.log_event(pc, "", "BOOT", "", line, "", "")
            return
        if line.startswith("time_ms,"):
            self.header_ok = (line == FW_SAMPLE_HEADER)
            if self.header_ok:
                print("[OK] ESP32 확인 (KITEL_MAIN_V5). (명령 도움말: h)")
                if self.bank and not self.pending_proto:
                    print(">>> 참가자 기록지를 받은 뒤 입력:  new P01 2 5 9 11 14")
            else:
                print(f"[경고] 예상과 다른 CSV 헤더: {line}\n       KITEL_MAIN_V5가 업로드되었는지 확인하세요.")
            return
        if line.startswith("#SUMMARY_HEADER,"):
            self.summary_header = line.split(",")[1:]
            return
        if line.startswith("#BASELINE_HEADER,"):
            self.baseline_header = line.split(",")[1:]
            return
        if line.startswith("#SUMMARY,"):
            self.on_summary(pc, line.split(",")[1:])
            return
        if line.startswith("#BASELINE,"):
            self.on_baseline_summary(pc, line.split(",")[1:])
            return
        if line.startswith("#EVENT,"):
            self.on_event(pc, line)
            return
        if line.startswith("#"):
            print(f"[ESP32] {line}")
            self.log_event(pc, "", "MSG", "", line, "", "")
            return
        self.on_sample(line)

    # ---------- 이벤트 ----------
    def on_event(self, pc, line):
        p = line.split(",")
        if len(p) < 7:
            self.bad_lines += 1
            return
        try:
            t, trial, esp_s, bid = int(p[1]), int(p[3]), int(p[5]), int(p[6])
        except ValueError:
            self.bad_lines += 1
            return
        name, detail = p[2], p[4]

        if name == "SESSION" and detail == "START":
            self.start_session(esp_s)

        self.log_event(pc, t, name, trial, detail, esp_s, bid)
        s = self.sess
        ev = (pc, t, name, detail)

        if s and s.active and esp_s == s.esp_num:
            s.events.append(ev)
            if name == "BASELINE" and detail == "START":
                b = Baseline(s, bid)
                s.baselines[bid] = b
                s.cur_baseline = b
                print(f"\n--- [{s.session_id}] Baseline #{bid} 측정 시작 (15초, 움직이지 않기) ---")
            if name == "READY" and detail == "START" and trial > 0 and trial not in s.trials:
                self.open_trial(trial, bid)

            if trial > 0 and trial in s.trials:
                s.trials[trial].events.append(ev)
            elif s.cur_baseline is not None and s.cur_baseline.bid == bid:
                s.cur_baseline.events.append(ev)

        if name == "QUESTION" and detail == "START":
            self.ready_wait = None

        # 콘솔 표시
        tag = f"T{trial:02d}" if trial else "   "
        if detail == "START" and name not in ("SESSION",):
            print(f"  [{t/1000:9.3f}s] {tag} {name}")
        elif name == "BUTTON":
            parts = detail.split(":")
            res = parts[2] if len(parts) > 2 else detail
            kind = parts[1] if len(parts) > 1 else ""
            mark = "   <- 무시됨" if res.startswith("IGNORED") else ""
            print(f"  [{t/1000:9.3f}s] {tag} 버튼 {kind} -> {res}{mark}")
        elif name in ("INVALID", "HAND_OK", "HAND_LOST", "CONTACT_DIP", "PPG_STALL", "PPG_RECOVERED"):
            print(f"  [{t/1000:9.3f}s] {tag} {name} {detail}")
        elif name == "BASELINE_LOST":
            print(f"  [{t/1000:9.3f}s] [!] Baseline 무효 ({detail}) -> 다음 질문 전에 Baseline 재측정 필요")

        # 질문 종료 처리
        if s and s.cur_trial and s.cur_trial.status == "RUNNING" and trial == s.cur_trial.tid:
            if name == "RESULT" and detail == "START":
                tr = s.cur_trial
                ok = tr.esp.get("valid") == "1"
                self.finish_trial(tr, "COMPLETE" if ok else "ABORTED",
                                  "" if ok else tr.esp.get("reason", ""))
            elif name == "ABORTED" and detail == "START":
                self.finish_trial(s.cur_trial, "ABORTED", s.cur_trial.esp.get("reason", ""))

        if name == "SESSION" and detail.startswith("END"):
            self.end_session(detail.split(":", 1)[-1])

    def on_summary(self, pc, values):
        hdr = self.summary_header
        if not hdr:
            if len(values) == len(SUMMARY_HEADER_V5):
                self.summary_header = SUMMARY_HEADER_V5
                hdr = SUMMARY_HEADER_V5
                print("[경고] SUMMARY_HEADER 미수신 — KITEL_MAIN_V5 기본 헤더로 자동 복구.")
                print("       다음 실험 전에 ESP32를 먼저 재부팅한 뒤 Python을 실행하세요.")
            else:
                print(f"[경고] SUMMARY 형식 불일치: 헤더 미수신, 필드 수={len(values)} (예상 {len(SUMMARY_HEADER_V5)})")
                return
        if len(values) != len(hdr):
            print(f"[경고] SUMMARY 형식 불일치: {len(values)}개 값, 헤더 {len(hdr)}개 필드")
            return
        d = dict(zip(hdr, values))
        self.log_event(pc, "", "SUMMARY", d.get("trial"), ",".join(values), d.get("session"), d.get("baseline_id"))
        s = self.sess
        try:
            tid = int(d["trial"])
        except (KeyError, ValueError):
            return
        if s and tid in s.trials:
            s.trials[tid].esp = d

    def on_baseline_summary(self, pc, values):
        hdr = self.baseline_header
        if not hdr:
            if len(values) == len(BASELINE_HEADER_V5):
                self.baseline_header = BASELINE_HEADER_V5
                hdr = BASELINE_HEADER_V5
                print("[경고] BASELINE_HEADER 미수신 — KITEL_MAIN_V5 기본 헤더로 자동 복구.")
            else:
                print(f"[경고] BASELINE 형식 불일치: 헤더 미수신, 필드 수={len(values)} (예상 {len(BASELINE_HEADER_V5)})")
                return
        if len(values) != len(hdr):
            print(f"[경고] BASELINE 형식 불일치: {len(values)}개 값, 헤더 {len(hdr)}개 필드")
            return
        d = dict(zip(hdr, values))
        self.log_event(pc, "", "BASELINE_SUMMARY", 0, ",".join(values), d.get("session"), d.get("baseline_id"))
        s = self.sess
        if not s:
            return
        try:
            bid = int(d["baseline_id"])
        except (KeyError, ValueError):
            return
        b = s.baselines.get(bid)
        if not b:
            return
        b.esp = d
        b.status = "COMPLETE" if d.get("valid") == "1" else "ABORTED"
        b.reason = "" if b.status == "COMPLETE" else d.get("reason", "")
        b.report = check_segment(b.samples, b.events, BASELINE_PHASES)
        b.write()
        s.write_summaries()
        self.write_run_summaries()
        mark = "[OK]" if b.report["integrity"] == "OK" else f"[확인 필요] {b.report['issues']}"
        print(f"  Baseline #{bid}: {b.status} {b.reason}  샘플 {b.report['n_samples']}개, "
              f"{b.report['rate_hz']}Hz  무결성 {mark}")
        if warn_names(d.get("warn_flags")):
            print(f"  Baseline 경고: {warn_names(d.get('warn_flags'))}")

    # ---------- 샘플 ----------
    def on_sample(self, line):
        p = line.split(",")
        if len(p) != N_SAMPLE_FIELDS:
            self.bad_lines += 1
            return
        try:
            smp = (int(p[0]), int(p[1]), int(p[2]), int(p[3]), int(p[4]),
                   p[5], int(p[9]), int(p[10]))
            trial, esp_s, bid = int(p[6]), int(p[7]), int(p[8])
        except ValueError:
            self.bad_lines += 1
            return
        s = self.sess
        if not s or not s.active or esp_s != s.esp_num:
            self.bad_lines += 1
            return
        if trial == 0:
            b = s.baselines.get(bid)
            if b is None:
                b = Baseline(s, bid)
                s.baselines[bid] = b
                s.cur_baseline = b
            b.samples.append(smp)
        else:
            tr = s.trials.get(trial) or self.open_trial(trial, bid)
            tr.samples.append(smp)
        self.total_samples += 1

    # ---------- 세션 ----------
    def start_session(self, esp_num):
        if self.sess and self.sess.active:
            self.end_session("NEW_SESSION_STARTED")
        pid = self.next_participant
        s = Session(self, esp_num, pid, len(self.sessions) + 1)
        self.sessions.append(s)
        self.sess = s
        self.pending_label = None
        if self.pending_proto:
            s.proto = self.pending_proto
            self.pending_proto = None
        print("\n" + "#" * 60)
        print(f" 세션 시작: {s.session_id}   (참가자 {pid})")
        if s.proto:
            print(f" 실험 모드: 연습 1 + 본 질문 {N_MAIN_QUESTIONS}개 (라벨 자동 기록, 화면에는 숨김)")
        elif self.bank:
            print(" [!] 질문 계획이 없습니다. Baseline 중에 'new P01 2 5 9 11 14' 를 입력하세요.")
        print("#" * 60)

    def end_session(self, reason):
        s = self.sess
        if not s or not s.active:
            return
        if s.cur_trial and s.cur_trial.status == "RUNNING":
            tr = s.cur_trial
            ok = tr.esp.get("valid") == "1"
            self.finish_trial(tr, "COMPLETE" if ok else ("INTERRUPTED" if reason == "ESP32_RESET" else "ABORTED"),
                              "" if ok else (tr.esp.get("reason") or reason))
        for b in s.baselines.values():
            if b.status == "RUNNING":
                b.status = "INTERRUPTED" if reason == "ESP32_RESET" else "ABORTED"
                b.reason = reason
                b.report = check_segment(b.samples, b.events, BASELINE_PHASES)
            b.write()
        for tr in s.trials.values():
            tr.write()
        s.active = False
        s.write_summaries()
        self.write_run_summaries()

        print("\n" + "#" * 60)
        print(f" 세션 종료: {s.session_id}  ({reason})")
        done = [t for t in s.trials.values() if t.status == "COMPLETE"]
        print(f" 질문 {len(s.trials)}개 (완료 {len(done)}개), Baseline 측정 {len(s.baselines)}회")
        for _, t in sorted(s.trials.items()):
            print(f"   T{t.tid:02d} B{t.bid} {t.qid:<6} {t.cond:<9} {t.status:<11} "
                  f"GSR z={t.esp.get('gsr_dz', '-'):>7}  FSR z={t.esp.get('fsr_dz', '-'):>7}")
        unl = [t.tid for t in s.trials.values() if t.cond == "unlabeled"]
        if unl:
            print(f" [!] 조건 미입력 질문: {unl}  (CSV의 condition=unlabeled)")
        self.next_participant = next_pid(s.pid)
        print(f" 다음 세션 참가자 ID: {self.next_participant}  (바꾸려면: p ID)")
        print("#" * 60)
        self.ready_wait = None
        if s.proto and reason != "PROGRAM_CLOSED":
            todo = [t for _, t in sorted(s.trials.items()) if t.role == "main" and t.status == "COMPLETE"]
            if todo:
                self.confirm_queue = todo
                print("\n" + "*" * 60)
                print(" 확인 단계: 참가자에게 각 질문에 실제로 어떻게 답했는지 물어보세요.")
                print("   Enter = 계획대로 답함   t = 실제로 진실   l = 실제로 거짓   x = 헷갈림(제외)")
                print("*" * 60)
                self.prompt_confirm()

    # ---------- 질문 ----------
    def open_trial(self, tid, bid):
        s = self.sess
        if s.cur_trial and s.cur_trial.status == "RUNNING":
            self.finish_trial(s.cur_trial, "INCOMPLETE", "NEXT_TRIAL_STARTED")

        entry = s.proto.next_entry() if s.proto else None
        if s.proto and entry is None:
            entry = {"question_id": f"EXTRA{tid:02d}", "question_text": "(계획 외 추가 질문)",
                     "condition": "test", "planned": "test", "role": "extra", "wait_s": ""}
        if entry is not None:
            label = entry
        elif self.pending_label:
            label = self.pending_label
            self.pending_label = None
        elif self.plan and tid - 1 < len(self.plan):
            label = dict(self.plan[tid - 1])
        else:
            label = {"condition": "unlabeled"}

        tr = Trial(s, tid, bid, label)
        s.trials[tid] = tr
        s.cur_trial = tr

        if entry is not None:
            if entry["role"] != "extra":
                s.proto.asked.append((tid, entry))
            self.show_question(tr)
            return tr

        print("\n" + "=" * 60)
        print(f" 질문 {tid} 시작 (Baseline #{bid} 사용)   [{s.session_id}]")
        print(f"  조건 : {tr.cond.upper()} -> {COND_KR.get(tr.cond, '')}")
        if tr.qtext:
            print(f"  질문 : {tr.qtext}")
        if tr.cond == "unlabeled":
            print("  >> 조건을 입력하세요:  t (진실) / l (거짓) / x (테스트)  [질문내용]")
        print("=" * 60)
        return tr

    def finish_trial(self, tr, status, reason):
        tr.status = status
        tr.reason = reason
        tr.report = check_segment(tr.samples, tr.events, TRIAL_PHASES)
        tr.write()
        s = tr.sess
        s.last_finished = tr
        s.write_summaries()
        self.write_run_summaries()

        rep = tr.report
        hidden = s.proto is not None and s.active
        if hidden and status != "COMPLETE" and tr.role in ("main", "practice") and tr.entry:
            new_q = s.proto.replace(tr.entry)
            tr.cond = "test"          # 중단된 질문은 분석에서 제외
            tr.write()
            s.write_summaries()
            self.write_run_summaries()
            print(f"  [!] 질문 {tr.tid} 무효 -> 다음 질문은 " +
                  ("새로운 대체 질문입니다." if new_q else "같은 질문을 다시 묻습니다 (남은 문항 없음)."))
        print("-" * 60)
        print(f" 질문 {tr.tid} 결과: {status}" + (f" ({reason})" if reason else "")
              + ("" if hidden else f"   조건={tr.cond}"))
        print(f"  샘플 {rep['n_samples']}개 | READY {rep['n_ready']}  QUESTION {rep['n_question']}"
              f"  RESPONSE {rep['n_response']}  | {rep['rate_hz']}Hz")
        print(f"  QUESTION {rep.get('question_ms_event')}ms  RESPONSE {rep.get('response_ms_event')}ms  "
              f"| 시리얼손실 {rep['seq_missing']}  간격이상 {rep['time_gaps']}")
        if tr.esp:
            print(f"  ESP32: GSR z={tr.esp.get('gsr_dz')}  FSR z={tr.esp.get('fsr_dz')}  "
                  f"경고={warn_names(tr.esp.get('warn_flags')) or '없음'}  (판정 아님)")
        print("  무결성: " + ("[OK]" if rep["integrity"] == "OK" else f"[확인 필요] {rep['issues']}"))
        if tr.cond == "unlabeled":
            print("  [!] 조건 미입력 -> 'fix t' 또는 'fix l' 로 입력하세요.")
        if hidden and s.proto.main_left() == 0 and status == "COMPLETE" and not s.main_done_announced:
            s.main_done_announced = True
            print("=" * 60)
            print("  ★ 본 질문 5개 완료!")
            print("  >> ESP32 버튼을 2초 길게 눌러 세션을 종료하세요. <<")
            print("=" * 60)
        elif hidden:
            print(f"  남은 본 질문: {s.proto.main_left()}개. 20–30초 쉰 뒤 짧게 눌러 다음 질문.")
        else:
            print("  ESP32: 짧게 누름 = 다음 질문 / 2초 누름 = 세션 종료")
        print("-" * 60)

    # ---------- 실험 모드 보조 ----------
    def show_question(self, tr):
        role_kr = {"practice": "연습 질문 (분석 제외)", "main": "본 질문", "extra": "계획 외 질문"}[tr.role]
        s = tr.sess
        n_done = sum(1 for _, e in s.proto.asked if e["role"] == "main")
        print("\n" + "=" * 60)
        if tr.role == "main":
            print(f" 질문 {tr.tid}  [{role_kr} {n_done}/{N_MAIN_QUESTIONS}]")
        else:
            print(f" 질문 {tr.tid}  [{role_kr}]")
        print()
        print(f"   \"{tr.qtext}\"")
        print()
        if tr.role == "extra":
            print("   본 질문은 모두 끝났습니다. 2초 길게 눌러 세션을 종료하세요.")
        else:
            print(f"   {tr.wait_s}초 기다렸다가 → 버튼 누르고 질문 읽기 → 답하는 순간 버튼")
            self.ready_wait = (time.monotonic(), tr.wait_s, tr.tid)
        print("=" * 60)

    def tick(self):
        """메인 루프에서 계속 호출: 무작위 대기 시간이 끝나면 질문 신호."""
        if self.ready_wait:
            t0, w, tid = self.ready_wait
            if time.monotonic() - t0 >= w:
                print(f"   >>> 지금 질문하세요! (질문 {tid}: 버튼 누르고 읽기)")
                self.ready_wait = None

    def prompt_confirm(self):
        if not self.confirm_queue:
            return
        tr = self.confirm_queue[0]
        kr = {"truth": "진실", "lie": "거짓"}.get(tr.planned, tr.planned)
        print(f"\n  질문 {tr.tid}: \"{tr.qtext}\"")
        print(f"     계획: {kr}  →  실제로는?  [Enter / t / l / x]")

    def handle_confirm(self, cmd):
        tr = self.confirm_queue[0]
        c = cmd.strip().lower()
        if c == "":
            tr.cond = tr.planned
            tr.confirmed = "as_planned"
        elif c in COND_KEYS:
            tr.cond = COND_KEYS[c]
            tr.confirmed = "as_planned" if tr.cond == tr.planned else "corrected"
        else:
            print("     Enter / t / l / x 중 하나를 입력하세요.")
            return
        tr.write()
        self.confirm_queue.pop(0)
        mark = "" if tr.confirmed == "as_planned" else "   <- 수정됨"
        print(f"     -> 질문 {tr.tid} 최종 라벨 = {tr.cond}{mark}")
        if self.confirm_queue:
            self.prompt_confirm()
        else:
            s = tr.sess
            s.write_summaries()
            self.write_run_summaries()
            main = [t for t in s.trials.values() if t.role == "main" and t.status == "COMPLETE"]
            n_l = sum(1 for t in main if t.cond == "lie")
            n_t = sum(1 for t in main if t.cond == "truth")
            print(f"\n  확인 완료: 진실 {n_t}개, 거짓 {n_l}개, 제외 {len(main) - n_l - n_t}개 저장됨.")
            print(f"  다음 참가자 기록지를 받으면:  new {self.next_participant} ...")

    def find_trial(self, tid):
        for ss in reversed(self.sessions):
            if tid in ss.trials:
                return ss.trials[tid]
        return None

    # ---------- 키보드 명령 ----------
    def handle_command(self, cmd):
        if self.confirm_queue and cmd.split()[:1] not in (["quit"], ["exit"], ["q"], ["h"], ["s"], ["fix"], ["new"], ["p"]):
            self.handle_confirm(cmd)
            return True
        if not cmd:
            return True
        parts = cmd.split(maxsplit=1)
        key = parts[0].lower()
        rest = parts[1].strip() if len(parts) > 1 else ""
        s = self.sess

        if key in ("quit", "exit", "q"):
            return False

        if key == "h":
            print(__doc__.split("실행 중 입력 명령")[1].split("저장 구조")[0])
            return True

        if key == "s":
            if self.confirm_queue:
                print(f"[상태] 확인 단계: 남은 질문 {len(self.confirm_queue)}개")
            if s and s.active and s.proto:
                print(f"[상태] 세션 {s.session_id}, 실험 모드, 남은 본 질문 {s.proto.main_left()}개")
            elif s and s.active:
                cur = s.cur_trial
                print(f"[상태] 세션 {s.session_id}, 질문 {len(s.trials)}개, "
                      f"현재 질문: {cur.tid if cur and cur.status == 'RUNNING' else '없음'}, "
                      f"다음 질문 예약 조건: {self.pending_label['condition'] if self.pending_label else '없음'}")
            else:
                print(f"[상태] 진행 중인 세션 없음. 다음 참가자 ID: {self.next_participant}")
            return True

        if key == "p":
            if not rest:
                print("사용법: p P02")
            else:
                self.next_participant = rest
                if s and s.active:
                    print(f"현재 세션({s.session_id})은 그대로 두고, 다음 세션 참가자 ID = {rest}")
                else:
                    print(f"다음 세션 참가자 ID = {rest}")
            return True

        if key in COND_KEYS:
            label = {"condition": COND_KEYS[key], "question_text": rest}
            cur = s.cur_trial if (s and s.active) else None
            if cur and cur.status == "RUNNING":
                cur.cond = label["condition"]
                if rest:
                    cur.qtext = rest
                print(f"질문 {cur.tid} 조건 = {cur.cond.upper()} ({COND_KR[cur.cond]})")
            else:
                self.pending_label = label
                print(f"다음 질문 조건 예약 = {label['condition'].upper()} ({COND_KR[label['condition']]})")
            return True

        if key == "new":
            if not self.bank:
                print("질문 은행이 없습니다. question_bank.csv 를 같은 폴더에 두세요.")
                return True
            try:
                pid, marked = parse_new_command(rest, self.bank)
            except ValueError as e:
                print(e)
                return True
            n_lies = 2 if self.proto_count % 2 == 0 else 3
            proto = Protocol(self.bank, marked, n_lies, self.rng)
            self.proto_count += 1
            if s and s.active and not s.trials and not s.proto:
                s.proto = proto
                if s.pid != pid:
                    print(f"[!] 이미 {s.pid}로 세션이 시작되어 있습니다. 파일 이름은 {s.pid}로 유지됩니다.")
                where = f"현재 세션({s.session_id})"
            else:
                self.pending_proto = proto
                self.next_participant = pid
                where = f"다음 세션({pid})"
            if self.clear:
                clear_screen()
            print("=" * 60)
            print(f" {where} 질문 계획 준비 완료: 연습 1 + 본 질문 {N_MAIN_QUESTIONS}개")
            print(" 거짓 표시 정보는 화면에서 지웠습니다. 질문자는 이제 화면을 봐도 됩니다.")
            print(" 손 올리기 → HAND OK → 버튼 → Baseline 15초")
            print("=" * 60)
            return True

        if key == "fix":
            fp = rest.split(maxsplit=1)
            target = None
            if fp and fp[0].isdigit():
                target = self.find_trial(int(fp[0]))
                if target is None:
                    print(f"질문 {fp[0]}번을 찾을 수 없습니다.")
                    return True
                fp = fp[1].split(maxsplit=1) if len(fp) > 1 else []
            if not fp or fp[0].lower() not in COND_KEYS:
                print("사용법: fix 3 l  (질문 3번을 거짓으로)  /  fix l  (방금 끝난 질문)")
                return True
            if target is None:
                for ss in reversed(self.sessions):
                    if ss.last_finished:
                        target = ss.last_finished
                        break
            if not target:
                print("수정할 완료 질문이 없습니다.")
                return True
            target.cond = COND_KEYS[fp[0].lower()]
            if target.planned:
                target.confirmed = "corrected" if target.cond != target.planned else "as_planned"
            if target in self.confirm_queue:
                self.confirm_queue.remove(target)
            if len(fp) > 1:
                target.qtext = fp[1]
            target.write()
            target.sess.write_summaries()
            self.write_run_summaries()
            print(f"[{target.sess.session_id}] 질문 {target.tid} 조건 수정 = {target.cond.upper()}")
            return True

        print(f"알 수 없는 명령: {cmd}  (도움말: h)")
        return True

    def close(self):
        if self.sess and self.sess.active:
            self.end_session("PROGRAM_CLOSED")
        self.write_run_summaries()
        self.raw.close()
        self.evf.close()
        print("\n" + "=" * 60)
        print(f" 저장 완료. 세션 {len(self.sessions)}개, 총 샘플 {self.total_samples}개, "
              f"해석 불가 줄 {self.bad_lines}개")
        if self.header_ok is None:
            print(" [경고] CSV 헤더를 받지 못했습니다.")
        print(f" 폴더: {os.path.abspath(self.dir)}")
        print("=" * 60)


# ----------------------------------------------------------
# 실행
# ----------------------------------------------------------
def keyboard_reader(q):
    while True:
        try:
            line = input()
        except (EOFError, KeyboardInterrupt):
            break
        q.put(line.strip())


def run(args):
    plan = load_plan(args.plan) if args.plan else []
    if plan:
        print(f"질문 목록 {len(plan)}개 불러옴 (기본값 제안용, 개수 제한 아님)")

    try:
        ser = serial.Serial(args.port, args.baud, timeout=0.1)
        # DTR 토글: ESP32 재부팅 유도 (BOOT/HEADER 라인을 확실히 수신하기 위함)
        ser.setDTR(False)
        time.sleep(0.05)
        ser.setDTR(True)
    except serial.SerialException as e:
        print(f"포트 {args.port} 열기 실패: {e}")
        print("Arduino IDE 시리얼 모니터가 열려 있으면 닫고 다시 실행하세요.")
        return

    bank = {}
    bank_path = args.bank
    if os.path.exists(bank_path):
        bank = load_bank(bank_path)
        print(f"질문 은행 {len(bank)}문항 불러옴: {bank_path}  (실험 모드)")
    else:
        print(f"[!] {bank_path} 가 없어 수동 모드로 실행합니다 (t/l/x 직접 입력).")

    r = Run(args.out, args.participant, plan, bank=bank, clear=not args.no_clear)
    print(f"{args.port} 연결됨. ESP32 재부팅 신호 전송 완료 — BOOT 메시지 수신 대기 중...")
    print(f"첫 세션 참가자 ID: {args.participant}")
    print(f"저장 폴더: {os.path.abspath(r.dir)}")
    print("[참고] BOOT 메시지가 나타나지 않으면 ESP32 리셋 버튼을 직접 눌러 주세요.")

    cmdq = queue.Queue()
    threading.Thread(target=keyboard_reader, args=(cmdq,), daemon=True).start()

    buf = b""
    try:
        while True:
            chunk = ser.read(ser.in_waiting or 1)
            if chunk:
                buf += chunk
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    r.handle_line(raw.decode("utf-8", errors="replace").strip())
            r.tick()
            keep = True
            while not cmdq.empty():
                if not r.handle_command(cmdq.get()):
                    keep = False
            if not keep:
                break
    except KeyboardInterrupt:
        print("\nCtrl+C: 저장하고 종료합니다.")
    except serial.SerialException as e:
        print(f"\n[오류] 시리얼 연결이 끊겼습니다: {e}")
    finally:
        try:
            ser.close()
        except Exception:
            pass
        r.close()


def check_dir(path):
    files = sorted(glob.glob(os.path.join(path, "**", "participant_*_samples.csv"), recursive=True))
    if not files:
        print("samples.csv 파일이 없습니다.")
        return
    for fpath in files:
        is_base = "_baseline" in os.path.basename(fpath)
        samples, events = [], []
        with open(fpath, newline="", encoding="utf-8-sig") as f:
            rd = csv.DictReader(f)
            expect = BASELINE_SAMPLE_FIELDS if is_base else TRIAL_SAMPLE_FIELDS
            if rd.fieldnames != expect:
                print(f"[경고] 헤더 불일치: {fpath}")
            for x in rd:
                samples.append((int(x["time_ms"]), int(x["fsr"]), int(x["gsr"]), int(x["ir"]),
                                int(x["red"]), x["phase"], int(x["seq"]), int(x["contact"])))
        epath = fpath.replace("_samples.csv", "_events.csv")
        if not is_base and os.path.exists(epath):
            with open(epath, newline="", encoding="utf-8-sig") as f:
                for x in csv.DictReader(f):
                    events.append((x["pc_time"], int(x["esp_time_ms"]), x["event"], x["detail"]))
        rep = check_segment(samples, events, BASELINE_PHASES if is_base else TRIAL_PHASES)
        mark = "[OK]" if rep["integrity"] == "OK" else f"[확인 필요] {rep['issues']}"
        print(f"{os.path.relpath(fpath, path)}: 샘플 {rep['n_samples']}개, {rep['rate_hz']}Hz  {mark}")


def main():
    p = argparse.ArgumentParser(description="KITEL ESP32 실험 데이터 수집기 V5")
    p.add_argument("--participant", default="P01", help="첫 세션 참가자 ID (기본 P01)")
    p.add_argument("--port", default="COM6")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--plan", help="선택: 질문 목록 CSV (question_id,condition,question_text)")
    p.add_argument("--out", default="experiments")
    p.add_argument("--check", help="저장된 run 폴더의 무결성만 다시 검사")
    p.add_argument("--bank", default=DEFAULT_BANK, help="질문 은행 CSV (기본 question_bank.csv)")
    p.add_argument("--no-clear", action="store_true", help="new 입력 후 화면을 지우지 않음")
    args = p.parse_args()

    if args.check:
        check_dir(args.check)
        return
    run(args)


if __name__ == "__main__":
    main()
