from __future__ import annotations

import numpy as np

from audio_drone_detector import analyze_audio, iter_windows, median_smooth


def test_iter_windows_pads_short_audio() -> None:
    audio = np.zeros(4_000, dtype=np.float32)
    windows = list(iter_windows(audio, sample_rate=16_000))
    assert len(windows) == 1
    assert windows[0][2].shape == (16_000,)


def test_median_smooth_reduces_single_spike() -> None:
    assert median_smooth([0.1, 0.9, 0.1], 3) == [0.5, 0.1, 0.5]


def test_analyze_audio_requires_consecutive_hits() -> None:
    calls = iter([0.8, 0.2, 0.85, 0.9])

    def predictor(_window: np.ndarray) -> float:
        return next(calls)

    audio = np.zeros(40_000, dtype=np.float32)
    event = analyze_audio(
        audio,
        16_000,
        "synthetic",
        predictor=predictor,
        threshold=0.7,
        consecutive_required=2,
        median_kernel=1,
    )
    assert event.detected is True
    assert event.consecutive_hits == 2
    assert event.window_count == 4
