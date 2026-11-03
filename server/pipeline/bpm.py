"""IR(PPG) 신호로 심박수(BPM)를 추정한다. 화면 표시용이며 판정(RuleJudge)에는 쓰지 않는다.

방법 (collector/calculate_bpm.py의 대역통과 + 피크 간격 방식을 실시간용으로 바꿈):
1. 최근 WINDOW_SEC(10초) + SETTLE_SEC(2초)의 IR 샘플을 모은다 (기기 시각 기준).
2. 40~200 BPM(0.67~3.33Hz) 2차 버터워스 대역통과를 sosfilt로 건다 (인과적 필터, filtfilt는 쓰지 않음).
   필터가 자리 잡는 앞의 SETTLE_SEC는 버리고 마지막 10초만 본다.
3. find_peaks로 맥박 피크를 찾고, 간격이 고르면(변동계수 검사 통과) 평균 간격으로 BPM = 60 / 간격.
   (간격 하나하나는 20ms 샘플 단위로 잘리므로 중앙값보다 평균이 해상도가 좋다)
4. 아래 경우에는 값을 내지 않는다 (bpm=None, 화면에는 "—"):
     NOT_ENOUGH_DATA  10초가 아직 안 모였거나, 창 안에 0.5초 넘는 끊김이 있음
     NO_CONTACT       창 안에 손 접촉이 없는 샘플이 있음 (contact=0)
     LOW_SIGNAL       IR 원신호가 너무 낮음 (센서에 손가락이 없음)
     WEAK_PULSE       필터 후 맥박 성분이 너무 약함
     TOO_FEW_BEATS    피크가 너무 적음
     IRREGULAR        피크 간격이 너무 불규칙함 (변동계수가 큼)
     OUT_OF_RANGE     추정값이 40~200 BPM 밖
- 아래 기준값은 실측 로그(run_20261103_161545)의 신호 크기를 보고 넉넉하게 정한 임시값이다.
  정답 심박(심전도 등)과 비교해 검증하지 않았다. 추정값이며 정확도를 보장하지 않는다.
"""

import math
from collections import deque
from dataclasses import dataclass
from typing import Optional

import numpy as np
from scipy.signal import butter, find_peaks, sosfilt

WINDOW_SEC = 10.0
SETTLE_SEC = 2.0
BPM_MIN, BPM_MAX = 40.0, 200.0
FILTER_ORDER = 2
MAX_GAP_MS = 500            # 창 안에 이보다 긴 끊김이 있으면 계산하지 않는다
MIN_FILL = 0.8              # 기대 샘플 수 대비 최소 비율
MIN_IR_DC = 50000.0         # 임시값. 실측 IR 원신호 1% 분위 약 92,000, 손을 떼면 약 2,000
MIN_AC_STD = 20.0           # 임시값. 실측 10초 창의 필터 후 표준편차 5% 분위 약 137
MIN_BEATS = 5               # 피크 간격이 최소 5개 (피크 6개)
# 피크 간격 변동계수 상한. 0.25에서는 둘째 봉우리를 일부 두 번 센 후보(변동계수 0.22~0.24)가 통과해
# 합성 신호 60개 중 10개가 ±2 BPM을 벗어났고, 맥박 없는 신호 20개 중 2개에서 값을 냈다. 0.15에서는 둘 다 0개.
# 실측 정답 심박과 비교한 값은 아니다 (2026-11-04 합성 신호 시험).
MAX_IBI_CV = 0.15
PEAK_PROMINENCE_SD = (0.5, 1.0)   # 피크 돌출도 기준 (필터 후 표준편차의 배수) 후보

REASON_TEXT = {
    "OK": "",
    "NOT_ENOUGH_DATA": "신호를 모으는 중",
    "NO_CONTACT": "손 접촉 없음",
    "LOW_SIGNAL": "센서 신호가 약함",
    "WEAK_PULSE": "맥박 신호가 약함",
    "TOO_FEW_BEATS": "맥박을 충분히 찾지 못함",
    "IRREGULAR": "맥박 간격이 불규칙함",
    "OUT_OF_RANGE": "범위를 벗어난 값",
    "PAUSED": "측정 구간이 아님",
}


@dataclass
class BpmEstimate:
    bpm: Optional[float]
    reason: str
    n_beats: int = 0
    ibi_cv: Optional[float] = None
    fs: Optional[float] = None

    @property
    def reason_text(self) -> str:
        return REASON_TEXT.get(self.reason, self.reason)


def estimate_bpm(time_ms, ir, contact=None) -> BpmEstimate:
    """샘플 배열로 BPM을 추정한다. 마지막 WINDOW_SEC만 판단에 쓴다 (앞의 SETTLE_SEC는 필터 안정용)."""
    t = np.asarray(time_ms, dtype=float)
    x = np.asarray(ir, dtype=float)
    if len(t) < 3:
        return BpmEstimate(None, "NOT_ENOUGH_DATA")
    # 마지막 큰 끊김 이후만 쓴다
    gaps = np.where(np.diff(t) > MAX_GAP_MS)[0]
    start = gaps[-1] + 1 if len(gaps) else 0
    t, x = t[start:], x[start:]
    c = None if contact is None else np.asarray(contact)[start:]
    if len(t) < 3 or t[-1] - t[0] < (WINDOW_SEC + SETTLE_SEC) * 1000 * 0.98:
        return BpmEstimate(None, "NOT_ENOUGH_DATA")
    fs = (len(t) - 1) / ((t[-1] - t[0]) / 1000.0)   # 기기 시각으로 샘플링 속도 추정
    win = t >= t[-1] - WINDOW_SEC * 1000
    if win.sum() < MIN_FILL * WINDOW_SEC * fs:
        return BpmEstimate(None, "NOT_ENOUGH_DATA", fs=fs)
    if c is not None and np.any(c[win] == 0):
        return BpmEstimate(None, "NO_CONTACT", fs=fs)
    if np.median(x[win]) < MIN_IR_DC:
        return BpmEstimate(None, "LOW_SIGNAL", fs=fs)
    sos = butter(FILTER_ORDER, [BPM_MIN / 60.0, BPM_MAX / 60.0], btype="bandpass", fs=fs, output="sos")
    f = sosfilt(sos, x - x[0])[win]
    tw = t[win]
    sd = float(np.std(f))
    if sd < MIN_AC_STD:
        return BpmEstimate(None, "WEAK_PULSE", fs=fs)
    # 센서와 회로에 따라 맥박이 위로도 아래로도 나타날 수 있다 (MAX30102 IR의 방향은 확인하지 않았다).
    # 두 방향 × 피크 돌출도 기준 두 가지로 후보를 만든다.
    # - 돌출도 기준이 낮으면 작은 둘째 봉우리(중복맥)까지 세어 간격이 0.3/0.5초처럼 번갈아 불규칙해진다.
    # - 돌출도 기준이 높으면 박동을 하나씩 건너뛰어 절반 속도인데도 간격은 고르게 보일 수 있다.
    # 그래서 간격이 고른 후보(변동계수 <= MAX_IBI_CV) 중 피크가 가장 많은 것을 고른다.
    candidates = []
    for sign in (1.0, -1.0):
        for prom in PEAK_PROMINENCE_SD:
            peaks, _ = find_peaks(sign * f, distance=max(1, int(fs * 60.0 / BPM_MAX)), prominence=prom * sd)
            if len(peaks) < MIN_BEATS + 1:
                continue
            ibi_s = np.diff(tw[peaks]) / 1000.0
            candidates.append((ibi_s, float(np.std(ibi_s) / np.mean(ibi_s))))
    if not candidates:
        return BpmEstimate(None, "TOO_FEW_BEATS", fs=fs)
    regular = [c for c in candidates if c[1] <= MAX_IBI_CV]
    ibi, cv = max(regular, key=lambda c: len(c[0])) if regular else min(candidates, key=lambda c: c[1])
    if cv > MAX_IBI_CV:
        return BpmEstimate(None, "IRREGULAR", n_beats=len(ibi), ibi_cv=cv, fs=fs)
    bpm = 60.0 / float(np.mean(ibi))
    if not BPM_MIN <= bpm <= BPM_MAX:
        return BpmEstimate(None, "OUT_OF_RANGE", n_beats=len(ibi), ibi_cv=cv, fs=fs)
    return BpmEstimate(bpm, "OK", n_beats=len(ibi), ibi_cv=cv, fs=fs)


class BpmTracker:
    """샘플을 받아 최근 12초를 들고 있다가 estimate()로 BPM을 계산한다."""

    def __init__(self):
        self.buf = deque()

    def reset(self):
        self.buf.clear()

    def add(self, sample):
        t = sample.time_ms
        if self.buf and t < self.buf[-1][0]:   # 기기 시각이 거꾸로 감 (재부팅 등)
            self.buf.clear()
        self.buf.append((t, sample.ir, sample.contact))
        keep_from = t - (WINDOW_SEC + SETTLE_SEC) * 1000 - 200
        while self.buf and self.buf[0][0] < keep_from:
            self.buf.popleft()

    def estimate(self) -> BpmEstimate:
        if not self.buf:
            return BpmEstimate(None, "NOT_ENOUGH_DATA")
        t, ir, c = zip(*self.buf)
        return estimate_bpm(t, ir, c)


def bpm_message(est: BpmEstimate, source: str, esp_time_ms=None) -> dict:
    """웹으로 보내는 bpm_update 메시지. 값은 추정값이며 판정에는 쓰지 않는다."""
    return {
        "type": "bpm_update",
        "source": source,
        "bpm": None if est.bpm is None else round(est.bpm, 1),
        "estimate": True,
        "reason": est.reason,
        "reason_text": est.reason_text,
        "n_beats": est.n_beats,
        "ibi_cv": None if est.ibi_cv is None or math.isnan(est.ibi_cv) else round(est.ibi_cv, 3),
        "window_sec": WINDOW_SEC,
        "esp_time_ms": esp_time_ms,
    }
