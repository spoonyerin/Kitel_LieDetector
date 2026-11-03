
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path
from scipy.signal import butter, sosfiltfilt, find_peaks

folder = Path(__file__).parent
files = sorted(folder.glob("sensor_*.csv"))

if not files:
    raise FileNotFoundError("CSV 파일이 없습니다.")

df = pd.read_csv(files[-1])

time_ms = df["time_ms"].to_numpy(dtype=float)
time_s = (time_ms - time_ms[0]) / 1000

duration = time_s[-1]
fs = (len(df) - 1) / duration

ir = df["ir"].to_numpy(dtype=float)

# PPG 대역통과 필터
sos = butter(
    2, [0.5, 5],
    btype="bandpass",
    fs=fs,
    output="sos"
)

filtered = sosfiltfilt(sos, ir)

# 시작·종료 부분의 필터 경계 영향 제외
valid = (time_s >= 2) & (time_s <= duration - 2)

signal = filtered[valid]
times = time_s[valid]

# 최소 피크 간격: 약 0.4초
min_distance = int(fs * 0.4)

peaks, properties = find_peaks(
    signal,
    distance=min_distance,
    prominence=max(np.std(signal) * 0.5, 10)
)

peak_times = times[peaks]

if len(peak_times) >= 2:
    intervals = np.diff(peak_times)
    bpm = 60 / np.median(intervals)

    print(f"분석 파일: {files[-1].name}")
    print(f"피크 개수: {len(peaks)}")
    print(f"추정 BPM: {bpm:.1f}")
else:
    print("맥박 피크를 충분히 검출하지 못했습니다.")

plt.figure(figsize=(12, 5))

plt.plot(times, signal, label="Filtered PPG IR")
plt.plot(
    peak_times,
    signal[peaks],
    "ro",
    label="Detected peaks"
)

plt.title("KITEL - PPG Peak Detection")
plt.xlabel("Time (seconds)")
plt.ylabel("Filtered IR")
plt.grid(True, alpha=0.3)
plt.legend()
plt.tight_layout()
plt.show()
