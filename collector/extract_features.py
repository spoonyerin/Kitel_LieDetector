
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.signal import medfilt

folder = Path(__file__).parent
files = sorted(folder.glob("sensor_*.csv"))

if not files:
    raise FileNotFoundError("센서 CSV 파일이 없습니다.")

df = pd.read_csv(files[-1])

time_s = (
    df["time_ms"] - df["time_ms"].iloc[0]
) / 1000

# 초기 실험용 구간
# 0~10초: 안정 상태
# 10~25초: 반응 구간
baseline_mask = (time_s >= 0) & (time_s < 10)
response_mask = (time_s >= 10) & (time_s < 25)

results = []

for sensor in ["fsr", "gsr", "ir", "red"]:
    raw = df[sensor].to_numpy(dtype=float)

    # 순간적인 이상값을 줄이는 간단한 필터
    cleaned = medfilt(raw, kernel_size=5)

    baseline = cleaned[baseline_mask]
    response = cleaned[response_mask]

    if len(baseline) < 5 or len(response) < 5:
        raise ValueError("측정 구간의 데이터가 부족합니다.")

    base_mean = np.mean(baseline)
    base_median = np.median(baseline)
    base_std = np.std(baseline)

    base_range = (
        np.percentile(baseline, 95)
        - np.percentile(baseline, 5)
    )

    delta_mean = np.mean(response) - base_mean
    max_change = np.max(np.abs(response - base_median))
    response_std = np.std(response)

    results.append({
        "sensor": sensor,
        "baseline_mean": base_mean,
        "baseline_median": base_median,
        "baseline_std": base_std,
        "baseline_range": base_range,
        "delta_mean": delta_mean,
        "max_change": max_change,
        "response_std": response_std
    })

features = pd.DataFrame(results)

print("\nKITEL FEATURE EXTRACTION")
print("File:", files[-1].name)
print(features.round(2).to_string(index=False))

output = folder / "features_test.csv"
features.to_csv(output, index=False)

print("\nSaved:", output)
print("주의: 기존 센서 테스트 데이터 기반의 기능 검증 결과")
