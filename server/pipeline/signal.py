"""인과적 전처리 (기획서·PRD 9절, collector/analyze_data.py의 시작값).

| 센서 | 처리 |
|------|------|
| FSR  | 5샘플 중앙값(과거 5개) + 5Hz 2차 버터워스 저역통과 |
| GSR  | 5샘플 중앙값(과거 5개) + 1Hz 2차 버터워스 저역통과 |
| PPG  | 0.5~5Hz 2차 버터워스 대역통과 |

- 모두 인과적이다: 지금 시점까지의 샘플만 쓴다. sosfilt만 쓰고 filtfilt(앞뒤를 모두 보는 필터)는 쓰지 않는다.
  중앙값도 가운데 정렬(scipy medfilt) 대신 과거 5개만 본다.
- 필터의 시작 상태는 첫 샘플 값에 맞춘다(sosfilt_zi × 첫 값). 시작할 때 0에서 튀어 오르는 과도 응답을 줄인다.
- 샘플링 속도는 기기 시각으로 추정한다: fs = (샘플 수 - 1) / 측정 시간(초).
- 같은 입력이면 항상 같은 출력이다 (난수 없음).
"""

import math

import numpy as np
from scipy.signal import butter, sosfilt, sosfilt_zi

MEDIAN_WINDOW = 5
FILTER_ORDER = 2
FSR_LOWPASS_HZ = 5.0
GSR_LOWPASS_HZ = 1.0
PPG_BAND_HZ = (0.5, 5.0)
DEFAULT_FS = 50.0           # 샘플이 너무 적어 추정할 수 없을 때


def estimate_fs(time_ms) -> float:
    t = np.asarray(time_ms, dtype=float)
    if len(t) < 2 or t[-1] <= t[0]:
        return DEFAULT_FS
    return (len(t) - 1) / ((t[-1] - t[0]) / 1000.0)


def causal_median(x, window: int = MEDIAN_WINDOW) -> np.ndarray:
    """과거 window개(자기 포함)의 중앙값. 처음 몇 개는 첫 값으로 채워 계산한다."""
    x = np.asarray(x, dtype=float)
    if len(x) == 0:
        return x
    padded = np.concatenate([np.full(window - 1, x[0]), x])
    return np.median(np.lib.stride_tricks.sliding_window_view(padded, window), axis=1)


def _sos_causal(x, sos) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    if len(x) == 0:
        return x
    zi = sosfilt_zi(sos) * x[0]
    y, _ = sosfilt(sos, x, zi=zi)
    return y


def lowpass(x, cutoff_hz: float, fs: float) -> np.ndarray:
    if not (0 < cutoff_hz < fs / 2):
        return np.asarray(x, dtype=float)
    return _sos_causal(x, butter(FILTER_ORDER, cutoff_hz, btype="lowpass", fs=fs, output="sos"))


def bandpass(x, low_hz: float, high_hz: float, fs: float) -> np.ndarray:
    high = min(high_hz, fs / 2 * 0.95)
    if not (0 < low_hz < high):
        return np.full(len(x), math.nan)
    return _sos_causal(x, butter(FILTER_ORDER, [low_hz, high], btype="bandpass", fs=fs, output="sos"))


def clean_fsr(x, fs: float) -> np.ndarray:
    return lowpass(causal_median(x), FSR_LOWPASS_HZ, fs)


def clean_gsr(x, fs: float) -> np.ndarray:
    return lowpass(causal_median(x), GSR_LOWPASS_HZ, fs)


def clean_ppg(x, fs: float) -> np.ndarray:
    return bandpass(x, PPG_BAND_HZ[0], PPG_BAND_HZ[1], fs)


def preprocess(samples) -> dict:
    """샘플 목록(server.source.lines.Sample) → 필터된 채널 배열과 시각, 단계, fs."""
    t = np.array([s.time_ms for s in samples], dtype=float)
    fs = estimate_fs(t)
    return {
        "time_ms": t,
        "phase": np.array([s.phase for s in samples], dtype=object),
        "contact": np.array([s.contact for s in samples], dtype=int),
        "fsr": clean_fsr([s.fsr for s in samples], fs),
        "gsr": clean_gsr([s.gsr for s in samples], fs),
        "ppg": clean_ppg([s.ir for s in samples], fs),
        "ir": np.array([s.ir for s in samples], dtype=float),
        "fs": fs,
    }
