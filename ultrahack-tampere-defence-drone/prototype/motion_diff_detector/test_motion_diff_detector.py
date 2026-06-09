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

    analysis = analyze_gray_pair(previous, current, config, 100, 100)

    assert not analysis.global_motion_rejected
    assert analysis.motion_ratio == 0.0
    assert analysis.detections == []
    assert np.count_nonzero(analysis.accepted_mask) == 0


def test_moving_blob_produces_box() -> None:
    config = MotionConfig(diff_threshold=18, min_area=20, morph_kernel=1)
    previous = np.zeros((100, 100), dtype=np.uint8)
    current = previous.copy()
    cv2.rectangle(current, (20, 30), (34, 44), 255, thickness=cv2.FILLED)

    analysis = analyze_gray_pair(previous, current, config, 100, 100)

    assert not analysis.global_motion_rejected
    assert len(analysis.detections) == 1
    detection = analysis.detections[0]
    assert detection.x1 <= 20
    assert detection.y1 <= 30
    assert detection.x2 >= 35
    assert detection.y2 >= 45
    assert np.count_nonzero(analysis.accepted_mask) > 0


def test_threshold_filters_weak_noise() -> None:
    config = MotionConfig(diff_threshold=18, min_area=1, morph_kernel=1)
    previous = np.zeros((40, 40), dtype=np.uint8)
    current = np.full((40, 40), 10, dtype=np.uint8)

    analysis = analyze_gray_pair(previous, current, config, 40, 40)

    assert not analysis.global_motion_rejected
    assert analysis.detections == []
    assert np.count_nonzero(analysis.accepted_mask) == 0


def test_global_frame_change_is_rejected() -> None:
    config = MotionConfig(diff_threshold=18, min_area=1, morph_kernel=1, max_motion_ratio=0.10)
    previous = np.zeros((50, 50), dtype=np.uint8)
    current = np.full((50, 50), 255, dtype=np.uint8)

    analysis = analyze_gray_pair(previous, current, config, 50, 50)

    assert analysis.global_motion_rejected
    assert analysis.motion_ratio > 0.99
    assert analysis.detections == []
    assert np.count_nonzero(analysis.accepted_mask) == 0


def make_textured_frame(width: int = 160, height: int = 120) -> np.ndarray:
    frame = np.zeros((height, width), dtype=np.uint8)
    for x in range(0, width, 20):
        cv2.line(frame, (x, 0), (x, height - 1), 90, 1)
    for y in range(0, height, 20):
        cv2.line(frame, (0, y), (width - 1, y), 90, 1)
    cv2.putText(frame, "arena", (35, 65), cv2.FONT_HERSHEY_SIMPLEX, 0.6, 180, 2)
    return cv2.GaussianBlur(frame, (5, 5), 0)


def test_coherent_shift_is_compensated() -> None:
    config = MotionConfig(
        diff_threshold=18,
        min_area=5,
        morph_kernel=1,
        max_motion_ratio=0.10,
        shake_protection=True,
        shake_min_shift=1.0,
        shake_consensus=0.70,
    )
    previous = make_textured_frame()
    transform = np.array([[1, 0, 4], [0, 1, 3]], dtype=np.float32)
    current = cv2.warpAffine(previous, transform, (previous.shape[1], previous.shape[0]))

    analysis = analyze_gray_pair(previous, current, config, previous.shape[1], previous.shape[0])

    assert analysis.global_motion_detected
    assert not analysis.global_motion_rejected
    assert analysis.motion_ratio < 0.10


def test_local_blob_survives_shake_compensation() -> None:
    config = MotionConfig(
        diff_threshold=18,
        min_area=20,
        morph_kernel=1,
        max_motion_ratio=0.10,
        shake_protection=True,
        shake_min_shift=1.0,
        shake_consensus=0.70,
    )
    previous = make_textured_frame()
    transform = np.array([[1, 0, 4], [0, 1, 3]], dtype=np.float32)
    current = cv2.warpAffine(previous, transform, (previous.shape[1], previous.shape[0]))
    cv2.circle(current, (105, 45), 6, 255, thickness=cv2.FILLED)

    analysis = analyze_gray_pair(previous, current, config, previous.shape[1], previous.shape[0])

    assert analysis.global_motion_detected
    assert not analysis.global_motion_rejected
    assert analysis.detections


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
