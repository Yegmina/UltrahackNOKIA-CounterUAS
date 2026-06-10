from __future__ import annotations

import numpy as np
import cv2

from motion_diff_detector import (
    MotionConfig,
    MotionDetection,
    MotionTracker,
    RoiMask,
    RoiZone,
    SemanticConfig,
    SemanticDetection,
    analyze_gray_pair,
    filter_candidates_by_semantics,
    prepare_gray,
    render_motion_only,
    roi_zone_points_pixels,
)


def make_roi_mask(zone_type: str, points: list[list[float]], penalty: float = 0.5) -> RoiMask:
    return RoiMask(
        version=1,
        mode="fixed",
        zones=(RoiZone(name=f"{zone_type}_zone", type=zone_type, points=tuple(map(tuple, points)), penalty=penalty),),
    )


def make_detection(center_x: float, center_y: float, area: float = 100.0) -> MotionDetection:
    half = 5.0
    return MotionDetection(
        x1=center_x - half,
        y1=center_y - half,
        x2=center_x + half,
        y2=center_y + half,
        center_x=center_x,
        center_y=center_y,
        area=area,
    )


def dummy_contour() -> np.ndarray:
    return np.array([[[0, 0]], [[1, 0]], [[1, 1]], [[0, 1]]], dtype=np.int32)


def make_semantic_detection(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    label: str = "person",
    confidence: float = 0.8,
) -> SemanticDetection:
    return SemanticDetection(
        label=label,
        raw_label=label,
        confidence=confidence,
        x1=x1,
        y1=y1,
        x2=x2,
        y2=y2,
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
    assert detection.roi_action == "keep"
    assert analysis.raw_detection_count == 1
    assert analysis.roi_rejected_count == 0
    assert analysis.roi_penalized_count == 0
    assert np.count_nonzero(analysis.accepted_mask) > 0


def test_hysteresis_rejects_low_motion_without_high_seed() -> None:
    config = MotionConfig(
        diff_threshold=5,
        min_area=1,
        morph_kernel=1,
        shake_protection=False,
        hysteresis=True,
        hysteresis_high_threshold=20,
    )
    previous = np.zeros((40, 40), dtype=np.uint8)
    current = previous.copy()
    cv2.rectangle(current, (10, 10), (15, 15), 10, thickness=cv2.FILLED)

    analysis = analyze_gray_pair(previous, current, config, 40, 40)

    assert analysis.raw_detection_count == 0
    assert analysis.detections == []
    assert np.count_nonzero(analysis.accepted_mask) == 0


def test_hysteresis_keeps_low_region_connected_to_high_seed() -> None:
    config = MotionConfig(
        diff_threshold=5,
        min_area=1,
        morph_kernel=1,
        shake_protection=False,
        hysteresis=True,
        hysteresis_high_threshold=20,
    )
    previous = np.zeros((40, 40), dtype=np.uint8)
    current = previous.copy()
    cv2.rectangle(current, (10, 10), (15, 15), 10, thickness=cv2.FILLED)
    current[12, 12] = 40

    analysis = analyze_gray_pair(previous, current, config, 40, 40)

    assert analysis.raw_detection_count == 1
    assert len(analysis.detections) == 1
    assert np.count_nonzero(analysis.accepted_mask) > 0


def test_temporal_filter_rejects_first_hit_and_keeps_persistent_track() -> None:
    tracker = MotionTracker(
        MotionConfig(
            temporal_filter=True,
            temporal_window_frames=3,
            temporal_min_hits=2,
            track_match_distance=20,
        )
    )
    contour = dummy_contour()

    first = tracker.filter_candidates([(make_detection(10, 10), contour)], frame_index=1)
    second = tracker.filter_candidates([(make_detection(14, 10), contour)], frame_index=2)

    assert first.candidates == []
    assert first.temporal_rejected_count == 1
    assert len(second.candidates) == 1
    assert second.candidates[0][0].track_id == 1
    assert second.candidates[0][0].track_hits == 2


def test_track_confirmation_hides_tentative_tracks_until_confirmed() -> None:
    tracker = MotionTracker(
        MotionConfig(
            track_confirmation=True,
            track_confirm_hits=2,
            track_match_distance=20,
        )
    )
    contour = dummy_contour()

    first = tracker.filter_candidates([(make_detection(10, 10), contour)], frame_index=1)
    second = tracker.filter_candidates([(make_detection(14, 10), contour)], frame_index=2)

    assert first.candidates == []
    assert first.unconfirmed_rejected_count == 1
    assert len(second.candidates) == 1
    assert second.candidates[0][0].track_confirmed


def test_direction_consistency_rejects_jittering_track() -> None:
    tracker = MotionTracker(
        MotionConfig(
            direction_consistency=True,
            direction_min_hits=3,
            direction_min_displacement=1.0,
            direction_cosine=0.5,
            track_match_distance=40,
        )
    )
    contour = dummy_contour()

    first = tracker.filter_candidates([(make_detection(10, 10), contour)], frame_index=1)
    second = tracker.filter_candidates([(make_detection(20, 10), contour)], frame_index=2)
    third = tracker.filter_candidates([(make_detection(10, 10), contour)], frame_index=3)

    assert len(first.candidates) == 1
    assert len(second.candidates) == 1
    assert third.candidates == []
    assert third.direction_rejected_count == 1


def test_roi_normalized_points_scale_to_video_size() -> None:
    zone = RoiZone(
        name="top",
        type="ignore",
        points=((0.0, 0.0), (1.0, 0.0), (0.5, 0.5)),
    )

    points = roi_zone_points_pixels(zone, image_width=200, image_height=100)

    assert points.tolist() == [[0.0, 0.0], [200.0, 0.0], [100.0, 50.0]]


def test_roi_ignore_zone_rejects_detection() -> None:
    config = MotionConfig(
        diff_threshold=18,
        min_area=20,
        morph_kernel=1,
        shake_protection=False,
    )
    previous = np.zeros((100, 100), dtype=np.uint8)
    current = previous.copy()
    cv2.rectangle(current, (20, 30), (34, 44), 255, thickness=cv2.FILLED)
    roi_mask = make_roi_mask("ignore", [[0.1, 0.2], [0.5, 0.2], [0.5, 0.6], [0.1, 0.6]])

    analysis = analyze_gray_pair(previous, current, config, 100, 100, roi_mask=roi_mask)

    assert analysis.raw_detection_count == 1
    assert analysis.roi_rejected_count == 1
    assert analysis.detections == []
    assert np.count_nonzero(analysis.accepted_mask) == 0


def test_roi_flight_zone_rejects_detection_outside_flight_space() -> None:
    config = MotionConfig(
        diff_threshold=18,
        min_area=20,
        morph_kernel=1,
        shake_protection=False,
    )
    previous = np.zeros((100, 100), dtype=np.uint8)
    current = previous.copy()
    cv2.rectangle(current, (70, 30), (84, 44), 255, thickness=cv2.FILLED)
    roi_mask = make_roi_mask("flight", [[0.0, 0.0], [0.4, 0.0], [0.4, 1.0], [0.0, 1.0]])

    analysis = analyze_gray_pair(previous, current, config, 100, 100, roi_mask=roi_mask)

    assert analysis.raw_detection_count == 1
    assert analysis.roi_rejected_count == 1
    assert analysis.detections == []
    assert np.count_nonzero(analysis.accepted_mask) == 0


def test_roi_penalty_zone_keeps_and_tags_detection() -> None:
    config = MotionConfig(
        diff_threshold=18,
        min_area=20,
        morph_kernel=1,
        shake_protection=False,
    )
    previous = np.zeros((100, 100), dtype=np.uint8)
    current = previous.copy()
    cv2.rectangle(current, (20, 30), (34, 44), 255, thickness=cv2.FILLED)
    roi_mask = make_roi_mask(
        "penalty",
        [[0.1, 0.2], [0.5, 0.2], [0.5, 0.6], [0.1, 0.6]],
        penalty=0.35,
    )

    analysis = analyze_gray_pair(previous, current, config, 100, 100, roi_mask=roi_mask)

    assert analysis.raw_detection_count == 1
    assert analysis.roi_rejected_count == 0
    assert analysis.roi_penalized_count == 1
    assert len(analysis.detections) == 1
    detection = analysis.detections[0]
    assert detection.roi_action == "penalize"
    assert detection.zone_type == "penalty"
    assert detection.zone_name == "penalty_zone"
    assert detection.roi_penalty == 0.35
    assert np.count_nonzero(analysis.accepted_mask) > 0


def test_semantic_person_filter_rejects_overlapping_motion() -> None:
    result = filter_candidates_by_semantics(
        [(make_detection(20, 20), dummy_contour())],
        [make_semantic_detection(10, 10, 30, 30)],
        SemanticConfig(enabled=True, labels=("person",), action="reject", overlap_threshold=0.15),
    )

    assert result.candidates == []
    assert result.rejected_count == 1
    assert result.penalized_count == 0


def test_semantic_person_filter_keeps_non_overlapping_motion() -> None:
    result = filter_candidates_by_semantics(
        [(make_detection(80, 80), dummy_contour())],
        [make_semantic_detection(10, 10, 30, 30)],
        SemanticConfig(enabled=True, labels=("person",), action="reject", overlap_threshold=0.15),
    )

    assert len(result.candidates) == 1
    assert result.rejected_count == 0
    assert result.candidates[0][0].semantic_action == "keep"


def test_semantic_person_filter_can_penalize_overlapping_motion() -> None:
    result = filter_candidates_by_semantics(
        [(make_detection(20, 20), dummy_contour())],
        [make_semantic_detection(10, 10, 30, 30, confidence=0.6)],
        SemanticConfig(enabled=True, labels=("person",), action="penalize", overlap_threshold=0.15),
    )

    assert result.rejected_count == 0
    assert result.penalized_count == 1
    assert len(result.candidates) == 1
    detection = result.candidates[0][0]
    assert detection.semantic_action == "penalize"
    assert detection.semantic_label == "person"
    assert detection.semantic_confidence == 0.6
    assert detection.semantic_overlap == 1.0


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
