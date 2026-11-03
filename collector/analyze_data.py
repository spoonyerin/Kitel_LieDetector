
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from pathlib import Path
from scipy.signal import medfilt, butter, sosfiltfilt

# 1. 가장 최근 CSV 파일 불러오기
folder = Path(__file__).parent
files = sorted(folder.glob("sensor_*.csv"))

if not files:
    raise FileNotFoundError("센서 CSV 파일이 없습니다.")

df = pd.read_csv(files[-1])

print("분석 파일:", files[-1].name)
print("데이터 수:", len(df))

# 2. 샘플링 주파수 추정
# FIFO를 읽은 시각이므로 실제 센서 샘플링 주파수와
# 차이가 있을 수 있음
time_ms = df["time_ms"].to_numpy(dtype=float)
duration = (time_ms[-1] - time_ms[0]) / 1000

if duration <= 0:
    raise ValueError("측정 시간이 올바르지 않습니다.")

fs = (len(df) - 1) / duration
print(f"추정 샘플링 속도: {fs:.2f} Hz")

if fs < 5:
    raise ValueError("샘플링 속도가 너무 낮습니다.")

time_s = (time_ms - time_ms[0]) / 1000

# 3. 원시 데이터
fsr = df["fsr"].to_numpy(dtype=float)
gsr = df["gsr"].to_numpy(dtype=float)
ir = df["ir"].to_numpy(dtype=float)
red = df["red"].to_numpy(dtype=float)

# 4. 필터 함수
def lowpass(data, cutoff_hz, fs):
    sos = butter(
        2,
        cutoff_hz,
        btype="lowpass",
        fs=fs,
        output="sos"
    )
    return sosfiltfilt(sos, data)

def bandpass(data, low_hz, high_hz, fs):
    sos = butter(
        2,
        [low_hz, high_hz],
        btype="bandpass",
        fs=fs,
        output="sos"
    )
    return sosfiltfilt(sos, data)

# 5. 센서별 필터링

# FSR: 5개 샘플 중앙값 + 저역통과
fsr_filtered = lowpass(
    medfilt(fsr, kernel_size=5),
    cutoff_hz=5,
    fs=fs
)

# GSR: 순간적인 튐 제거 + 저역통과
gsr_filtered = lowpass(
    medfilt(gsr, kernel_size=5),
    cutoff_hz=1,
    fs=fs
)

# PPG: 느린 추세 제거 + 빠른 잡음 감소
# 0.5~5Hz는 초기 실험용 범위
ir_filtered = bandpass(ir, 0.5, 5, fs)
red_filtered = bandpass(red, 0.5, 5, fs)

# 6. 원본과 필터링 결과 비교
signals = [
    ("FSR", fsr, fsr_filtered),
    ("GSR", gsr, gsr_filtered),
    ("PPG IR", ir, ir_filtered),
    ("PPG RED", red, red_filtered)
]

fig, axes = plt.subplots(
    4, 2,
    figsize=(14, 10),
    sharex=True
)

for i, (name, raw, filtered) in enumerate(signals):

    axes[i, 0].plot(
        time_s, raw,
        linewidth=0.8
    )
    axes[i, 0].set_title(f"{name} - RAW")

    axes[i, 1].plot(
        time_s, filtered,
        linewidth=0.8
    )
    axes[i, 1].set_title(f"{name} - FILTERED")

    for ax in axes[i]:
        ax.grid(True, alpha=0.3)
        ax.set_ylabel("ADC / signal")

axes[-1, 0].set_xlabel("Time (seconds)")
axes[-1, 1].set_xlabel("Time (seconds)")

plt.suptitle("KITEL - Noise Filtering Test")
plt.tight_layout()
plt.show()

# 7. 필터링한 데이터 저장
result = df.copy()

result["fsr_filtered"] = fsr_filtered
result["gsr_filtered"] = gsr_filtered
result["ir_filtered"] = ir_filtered
result["red_filtered"] = red_filtered

output = folder / "filtered_data.csv"
result.to_csv(output, index=False)

print("필터링 완료!")
print("저장 파일:", output)