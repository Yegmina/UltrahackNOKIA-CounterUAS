from __future__ import annotations

import numpy as np
import cv2

from motion_diff_detector import (
    MotionConfig,
    analyze_gray_pair,
    prepare_gray,
    render_motion_only,
)


def test_static_frames_produce_no_motion() -> None:
    config = MotionConfig(diff_threshold=18, min_area=20)
    previous = np.zeros((100, 100), dtype=np.uint8)
    current = previous.copy()

    mask, detections, motion_ratio, rejected = analyze_gray_pair(previous, current, config, 100, 100)

    assert not rejected
    assert motion_ratio == 0.0
    assert detections == []
    assert np.count_nonzero(mask) == 0


def test_moving_blob_produces_box() -> None:
    config = MotionConfig(diff_threshold=18, min_area=20, morph_kernel=1)
    previous = np.zeros((100, 100), dtype=np.uint8)
    current = previous.copy()
    cv2.rectangle(current, (20, 30), (34, 44), 255, thickness=cv2.FILLED)

    mask, detections, _, rejected = analyze_gray_pair(previous, current, config, 100, 100)

    assert not rejected
    assert len(detections) == 1
    detection = detections[0]
    assert detection.x1 <= 20
    assert detection.y1 <= 30
    assert detection.x2 >= 35
    assert detection.y2 >= 45
    assert np.count_nonzero(mask) > 0


def test_threshold_filters_weak_noise() -> None:
    config = MotionConfig(diff_threshold=18, min_area=1, morph_kernel=1)
    previous = np.zeros((40, 40), dtype=np.uint8)
    current = np.full((40, 40), 10, dtype=np.uint8)

    mask, detections, _, rejected = analyze_gray_pair(previous, current, config, 40, 40)

    assert not rejected
    assert detections == []
    assert np.count_nonzero(mask) == 0


def test_global_frame_change_is_rejected() -> None:
    config = MotionConfig(diff_threshold=18, min_area=1, morph_kernel=1, max_motion_ratio=0.10)
    previous = np.zeros((50, 50), dtype=np.uint8)
    current = np.full((50, 50), 255, dtype=np.uint8)

    mask, detections, motion_ratio, rejected = analyze_gray_pair(previous, current, config, 50, 50)

    assert rejected
    assert motion_ratio > 0.99
    assert detections == []
    assert np.count_nonzero(mask) == 0


def test_motion_only_renderer_keeps_pixels_inside_mask_only() -> None:
    frame = np.zeros((20, 20, 3), dtype=np.uint8)
    frame[:, :] = (10, 100, 200)
    mask = np.zeros((20, 20), dtype=np.uint8)
    mask[5:10, 6:11] = 255

    rendered = render_motion_only(frame, mask)

    assert rendered[7, 8].tolist() == [10, 100, 200]
    assert rendered[1, 1].tolist() == [0, 0, 0]
    assert np.count_nonzero(rendered) > 0


def test_prepare_gray_downscales_and_blurs() -> None:
    frame = np.zeros((100, 80, 3), dtype=np.uint8)
    config = MotionConfig(analysis_scale=0.5, blur_kernel=5)

    gray = prepare_gray(frame, config)

    assert gray.shape == (50, 40)
