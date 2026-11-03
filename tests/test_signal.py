"""인과적 전처리(server/pipeline/signal.py) 테스트."""

import inspect
import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server.pipeline import signal as sig  # noqa: E402

FS = 50.0
T = np.arange(0, 20, 1 / FS)


def test_estimate_fs_from_device_time():
    assert sig.estimate_fs(np.arange(0, 10000, 20)) == pytest.approx(50.0)
    assert sig.estimate_fs([5]) == sig.DEFAULT_FS and sig.estimate_fs([]) == sig.DEFAULT_FS


def test_causal_median_removes_spike_and_uses_past_only():
    x = np.array([10, 10, 10, 500, 10, 10, 10, 10.0])
    y = sig.causal_median(x)
    assert y.max() == 10 and len(y) == len(x)
    assert sig.causal_median([])[:].size == 0


@pytest.mark.parametrize("fn", [sig.clean_fsr, sig.clean_gsr, sig.clean_ppg])
def test_filters_are_causal(fn):
    """뒤쪽 샘플을 바꿔도 앞쪽 출력은 그대로여야 한다 (filtfilt가 아님)."""
    rng = np.random.default_rng(0)
    x = 2000 + 50 * np.sin(2 * np.pi * 1.2 * T) + rng.normal(0, 5, len(T))
    y1 = fn(x, FS)
    x2 = x.copy()
    x2[600:] += 1000
    y2 = fn(x2, FS)
    assert np.array_equal(y1[:600], y2[:600])
    assert not np.array_equal(y1[600:], y2[600:])


def test_constant_input_has_no_start_transient():
    x = np.full(500, 2200.0)
    assert np.allclose(sig.clean_gsr(x, FS), 2200.0)
    assert np.allclose(sig.clean_fsr(x, FS), 2200.0)
    assert np.allclose(sig.clean_ppg(x, FS), 0.0, atol=1e-6)   # 대역통과는 직류를 없앤다


def test_lowpass_band_behaviour():
    slow = 20 * np.sin(2 * np.pi * 0.1 * T)
    fast = 20 * np.sin(2 * np.pi * 10 * T)
    keep = sig.lowpass(slow, 1.0, FS)[200:]
    cut = sig.lowpass(fast, 1.0, FS)[200:]
    assert np.std(keep) > 0.9 * np.std(slow[200:]) and np.std(cut) < 0.05 * np.std(fast[200:])
    pulse = sig.bandpass(100 * np.sin(2 * np.pi * 1.2 * T), 0.5, 5.0, FS)[200:]
    drift = sig.bandpass(100 * np.sin(2 * np.pi * 0.05 * T), 0.5, 5.0, FS)[200:]
    assert np.std(pulse) > 0.8 * 70 and np.std(drift) < 0.15 * 70


def test_deterministic_and_no_filtfilt_call():
    x = 2000 + 30 * np.sin(T)
    assert np.array_equal(sig.clean_gsr(x, FS), sig.clean_gsr(x, FS))
    import ast
    from server.pipeline import features
    for mod in (sig, features):   # 설명 문장이 아니라 실제 호출과 import를 본다
        tree = ast.parse(inspect.getsource(mod))
        names = {n.attr if isinstance(n, ast.Attribute) else n.id
                 for n in ast.walk(tree) if isinstance(n, (ast.Name, ast.Attribute))}
        names |= {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
        assert not names & {"filtfilt", "sosfiltfilt"}, mod.__name__
