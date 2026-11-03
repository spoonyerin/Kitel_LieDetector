"""더미 입력원: 기기(KITEL_MAIN_V5)와 같은 형식의 줄을 합성한다.

- 하드웨어 없이 화면과 흐름을 개발하기 위한 것이다. source는 항상 "dummy"이며 학습에 쓰지 않는다.
- 줄 형식과 순서는 실제 로그(run_20261103_161545)에서 확인한 흐름을 따른다.
    PLACE_HAND → 버튼 → SESSION START → BASELINE(15초) → BASELINE END → READY START → #BASELINE
    → 버튼 → QUESTION → 버튼 → RESPONSE(20초) → ANALYZING → #SUMMARY → RESULT → 버튼(짧게/길게)
  보류(poor) 흐름: INVALID → BASELINE_LOST → #SUMMARY(valid=0, nan) → ABORTED → 기준 재측정
- 요약의 dz는 실제 로그로 확인한 식과 같다: (답변 구간 평균 - 기준 평균) / 기준 표준편차.
- 실제 로그에서 확인하지 못한 부분 (더미 전용 가정):
    * 답변 중에 손을 떼면 기기가 어떤 순서로 보내는지 (실측은 READY 중에 손을 뗀 경우만 있다)
    * 보류 직후 세션을 끝낼 때의 버튼 detail (ABORTED:LONG:END_SESSION으로 둔다)
    * 표준편차가 모집단 식인지 표본 식인지 (모집단 식으로 둔다)
- 패턴: calm(작은 변화), reactive(GSR 상승, FSR 감소, 심박 상승), poor(답변 중 손 뗌 → 보류)
"""

import math
import random
import statistics
import time
from dataclasses import dataclass, field
from typing import Iterator, List

from server.source.lines import BASELINE_HEADER_V5, FW_SAMPLE_HEADER, SUMMARY_HEADER_V5
from server.source.pacing import Item, paced

PATTERNS = ("calm", "reactive", "poor")
SAMPLE_MS = 20


@dataclass
class DummyConfig:
    trials: int = 3
    patterns: List[str] = field(default_factory=lambda: ["calm", "reactive", "poor"])
    seed: int = 0
    session: int = 1           # 기기 세션 번호
    start_ms: int = 10000      # 기기 시각 시작값
    baseline_ms: int = 15000   # record_session.EXPECTED_MS와 같다
    response_ms: int = 20000
    ready_ms: int = 2000
    question_ms: int = 3000
    result_ms: int = 3000
    hand_off_after_ms: int = 3000   # poor 패턴에서 답변 시작 후 손을 떼는 시점
    invalid_after_ms: int = 5000    # 손을 뗀 뒤 보류가 확정되기까지


class _Gen:
    """기기 시각, 순번, 신호를 들고 줄을 만든다."""

    def __init__(self, cfg: DummyConfig):
        self.cfg = cfg
        self.rng = random.Random(cfg.seed)
        self.t = cfg.start_ms
        self.seq = 0
        self.out: List[Item] = []
        self.bid = 0
        self.base_lv = {"fsr": 2200.0, "gsr": 2250.0, "ir": 103000.0, "red": 88000.0}

    # ---------- 줄 쓰기 ----------
    def emit(self, line):
        self.out.append((self.t / 1000.0, line))

    def event(self, name, trial, detail, session=None, bid=None):
        s = self.cfg.session if session is None else session
        b = self.bid if bid is None else bid
        self.emit(f"#EVENT,{self.t},{name},{trial},{detail},{s},{b}")

    def wait(self, ms):
        self.t += ms

    # ---------- 신호 ----------
    def sample_values(self, phase, pattern, k, hand_on):
        """k: 답변 구간 반응 정도(0~1). hand_on=False면 손을 뗀 상태."""
        g = self.rng.gauss
        ts = self.t / 1000.0
        hr = 72 + 1.5 * math.sin(ts * 0.3)
        fsr = self.base_lv["fsr"] + g(0, 40)
        gsr = self.base_lv["gsr"] + 4 * math.sin(ts * 0.2) + g(0, 8)
        if phase == "RESPONSE":
            if pattern == "reactive":
                gsr += 90 * k
                fsr -= 260 * k
                hr += 12 * k
            elif pattern == "calm":
                gsr += 4 * k
                fsr -= 15 * k
        beat = (ts * hr / 60.0) % 1.0
        pulse = math.exp(-((beat - 0.2) / 0.08) ** 2)
        ir = self.base_lv["ir"] + 600 * pulse + g(0, 30)
        red = self.base_lv["red"] + 450 * pulse + g(0, 25)
        contact = 1
        if not hand_on:
            fsr, contact = 30 + g(0, 5), 0
            ir, red = 2000 + g(0, 50), 1500 + g(0, 50)
        clip = lambda v: int(min(4095, max(0, round(v))))
        return clip(fsr), clip(gsr), int(round(ir)), int(round(red)), contact

    def samples(self, phase, trial, ms, pattern="calm", react_from=None, hand_off_at=None):
        """ms 동안 20ms 간격 샘플을 만든다. 돌려주는 값은 만든 샘플 목록."""
        made = []
        start = self.t
        for _ in range(ms // SAMPLE_MS):
            self.t += SAMPLE_MS
            self.seq += 1
            k = 0.0
            if react_from is not None:
                k = min(1.0, max(0.0, (self.t - react_from) / 3000.0))
            hand_on = hand_off_at is None or self.t < hand_off_at
            fsr, gsr, ir, red, contact = self.sample_values(phase, pattern, k, hand_on)
            self.emit(f"{self.t},{fsr},{gsr},{ir},{red},{phase},{trial},"
                      f"{self.cfg.session},{self.bid},{self.seq},{contact}")
            made.append((fsr, gsr, ir, contact))
        assert self.t - start == ms
        return made


def _stats(rows, idx):
    vals = [r[idx] for r in rows]
    return statistics.fmean(vals), statistics.pstdev(vals)


def dummy_items(cfg: DummyConfig) -> Iterator[Item]:
    """(기기 시각 초, 줄)을 차례로 돌려준다. 정답 라벨은 만들지 않는다."""
    for p in cfg.patterns:
        if p not in PATTERNS:
            raise ValueError(f"알 수 없는 패턴: {p} (가능: {', '.join(PATTERNS)})")
    gen = _Gen(cfg)
    s = cfg.session
    gen.emit("#BOOT,KITEL_MAIN_V5")
    gen.emit(FW_SAMPLE_HEADER)
    gen.emit("#BASELINE_HEADER," + ",".join(BASELINE_HEADER_V5))
    gen.emit("#SUMMARY_HEADER," + ",".join(SUMMARY_HEADER_V5))

    # 세션 시작
    gen.event("PLACE_HAND", 0, "START", session=0, bid=0)
    gen.wait(1000)
    gen.event("HAND_OK", 0, "FSR", session=0, bid=0)
    gen.wait(1500)
    gen.event("BUTTON", 0, "PLACE_HAND:PRESS:START_SESSION:IRQ", session=0, bid=0)
    gen.event("PLACE_HAND", 0, "END", session=0, bid=0)
    gen.event("SESSION", 0, "START", session=s, bid=0)

    baseline = None

    def run_baseline(next_trial):
        nonlocal baseline
        gen.bid += 1
        gen.event("BASELINE", 0, "START")
        rows = gen.samples("BASELINE", 0, cfg.baseline_ms)
        gen.event("BASELINE", 0, "END")
        gen.event("READY", next_trial, "START")
        fm, fs = _stats(rows, 0)
        gm, gs = _stats(rows, 1)
        im, _ = _stats(rows, 2)
        baseline = {"n": len(rows), "fm": fm, "fs": fs, "gm": gm, "gs": gs, "im": im}
        gen.emit(f"#BASELINE,{s},{gen.bid},1,NONE,0,{len(rows)},{fm:.2f},{fs:.2f},{gm:.2f},{gs:.2f},{im:.1f}")

    run_baseline(1)
    ready_started = True  # run_baseline이 다음 시도의 READY START를 이미 보냈다

    for trial in range(1, cfg.trials + 1):
        pattern = cfg.patterns[(trial - 1) % len(cfg.patterns)]
        last = trial == cfg.trials
        if not ready_started:
            gen.event("READY", trial, "START")
        ready_started = False
        gen.samples("READY", trial, cfg.ready_ms)
        gen.event("BUTTON", trial, "READY:PRESS:QUESTION:IRQ")
        gen.event("READY", trial, "END")
        gen.event("QUESTION", trial, "START")
        q_rows = gen.samples("QUESTION", trial, cfg.question_ms)
        gen.event("BUTTON", trial, "QUESTION:PRESS:ANSWER:IRQ")
        gen.event("QUESTION", trial, "END")
        gen.event("RESPONSE", trial, "START")
        rs_start = gen.t
        b = baseline
        bl = f"{b['n']},{b['fm']:.2f},{b['fs']:.2f},{b['gm']:.2f},{b['gs']:.2f},{b['im']:.1f}"
        head = f"#SUMMARY,{s},{gen.bid},{trial}"

        if pattern == "poor":
            off_at = rs_start + cfg.hand_off_after_ms
            gen.samples("RESPONSE", trial, cfg.hand_off_after_ms)
            gen.event("HAND_LOST", trial, "RESPONSE")
            gen.samples("RESPONSE", trial, cfg.invalid_after_ms, hand_off_at=off_at)
            gen.event("INVALID", trial, "HAND_OFF_LONG")
            gen.event("BASELINE_LOST", trial, "HAND_OFF_LONG")
            gen.emit(f"{head},0,HAND_OFF_LONG,0,{bl},0,0,0,0.00,0.00,0.00,0.00,0.0,nan,nan")
            gen.event("RESPONSE", trial, "END")
            gen.event("ABORTED", trial, "START")
            gen.wait(cfg.result_ms)
            if last:
                gen.event("BUTTON", trial, "ABORTED:LONG:END_SESSION:IRQ")
                gen.event("SESSION", trial, "END:LONG_PRESS")
                gen.event("ABORTED", trial, "END")
                break
            gen.event("BUTTON", trial, "ABORTED:SHORT:NEED_REBASELINE:IRQ")
            gen.event("ABORTED", trial, "END")
            gen.event("PLACE_HAND", 0, "START")
            gen.wait(1500)
            gen.event("HAND_OK", 0, "FSR")
            gen.wait(1000)
            gen.event("BUTTON", 0, "PLACE_HAND:SHORT:REBASELINE:IRQ")
            gen.event("PLACE_HAND", 0, "END")
            run_baseline(trial + 1)
            ready_started = True
            continue

        rs_rows = gen.samples("RESPONSE", trial, cfg.response_ms, pattern=pattern, react_from=rs_start)
        gen.event("RESPONSE", trial, "END")
        gen.event("ANALYZING", trial, "START")
        rfm, rfs = _stats(rs_rows, 0)
        rgm, rgs = _stats(rs_rows, 1)
        rim, _ = _stats(rs_rows, 2)
        fsr_dz = (rfm - b["fm"]) / b["fs"] if b["fs"] > 0 else float("nan")
        gsr_dz = (rgm - b["gm"]) / b["gs"] if b["gs"] > 0 else float("nan")
        gen.emit(f"{head},1,NONE,0,{bl},{len(q_rows)},{cfg.question_ms},{len(rs_rows)},"
                 f"{rfm:.2f},{rfs:.2f},{rgm:.2f},{rgs:.2f},{rim:.1f},{fsr_dz:.3f},{gsr_dz:.3f}")
        gen.wait(800)
        gen.event("ANALYZING", trial, "END")
        gen.event("RESULT", trial, "START")
        gen.wait(cfg.result_ms)
        if last:
            gen.event("BUTTON", trial, "RESULT:LONG:END_SESSION:IRQ")
            gen.event("SESSION", trial, "END:LONG_PRESS")
            gen.event("RESULT", trial, "END")
        else:
            gen.event("BUTTON", trial, "RESULT:SHORT:NEXT_QUESTION:IRQ")
            gen.event("RESULT", trial, "END")

    gen.event("SESSION_END", cfg.trials, "START")
    gen.wait(2000)
    gen.event("SESSION_END", cfg.trials, "END")
    gen.event("PLACE_HAND", 0, "START", session=0, bid=0)
    yield from gen.out


class DummySource:
    """합성 신호 입력원. lines()는 기기 형식의 줄을 시간 간격에 맞춰 내보낸다."""
    source = "dummy"
    name = "dummy"

    def __init__(self, cfg: DummyConfig = None, speed: float = 1.0, max_wait: float = 2.0, sleep=time.sleep):
        self.cfg = cfg or DummyConfig()
        self.speed = speed
        self.max_wait = max_wait
        self.sleep = sleep

    def lines(self) -> Iterator[str]:
        return paced(dummy_items(self.cfg), self.speed, self.max_wait, self.sleep)
