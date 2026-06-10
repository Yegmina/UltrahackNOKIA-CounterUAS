from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

from fusion_evidence_lab import (
    MotionConfig,
    detect_motion,
    estimate_extra_video_sync,
    fuse_records,
    import_detector_records,
    perspective_matrix_for_source,
    slugify,
)


class FusionEvidenceLabTests(unittest.TestCase):
    def test_slugify_is_stable(self) -> None:
        self.assertEqual(slugify("HP Wide Vision HD Camera (640x480)"), "hp_wide_vision_hd_camera_640x480")

    def test_perspective_uses_normalized_points(self) -> None:
        matrix = perspective_matrix_for_source(
            {
                "demo1": {
                    "src": [[0, 0], [1, 0], [1, 1], [0, 1]],
                    "dst": [[0, 0], [1, 0], [1, 1], [0, 1]],
                }
            },
            "demo1",
            100,
            50,
        )
        self.assertIsNotNone(matrix)
        point = np.array([[[99.0, 49.0]]], dtype=np.float32)
        transformed = cv2.perspectiveTransform(point, matrix)
        self.assertAlmostEqual(float(transformed[0, 0, 0]), 99.0, delta=0.01)
        self.assertAlmostEqual(float(transformed[0, 0, 1]), 49.0, delta=0.01)

    def test_static_frames_produce_no_motion(self) -> None:
        config = MotionConfig(min_area=5).normalized()
        frame = np.zeros((80, 100, 3), dtype=np.uint8)
        history: list[np.ndarray] = []
        prev, _mask, detections, _ratio, rejected = detect_motion(None, frame, config, history)
        self.assertFalse(detections)
        _prev, _mask, detections, _ratio, rejected = detect_motion(prev, frame.copy(), config, history)
        self.assertFalse(rejected)
        self.assertFalse(detections)

    def test_moving_blob_produces_box(self) -> None:
        config = MotionConfig(diff_threshold=10, min_area=5, analysis_scale=1.0).normalized()
        first = np.zeros((80, 100, 3), dtype=np.uint8)
        second = first.copy()
        cv2.circle(second, (45, 35), 5, (255, 255, 255), -1)
        history: list[np.ndarray] = []
        prev, _mask, _detections, _ratio, _rejected = detect_motion(None, first, config, history)
        _prev, _mask, detections, _ratio, rejected = detect_motion(prev, second, config, history)
        self.assertFalse(rejected)
        self.assertTrue(detections)
        self.assertGreaterEqual(detections[0]["x1"], 35)
        self.assertLessEqual(detections[0]["x2"], 55)

    def test_sync_estimation_prefers_matching_offset(self) -> None:
        archive = [
            {"session_s": 10.0, "motion_score": 1.0},
            {"session_s": 10.5, "motion_score": 0.8},
        ]
        extra = {
            "phone": [
                {"local_s": 2.0, "motion_score": 1.0},
                {"local_s": 2.5, "motion_score": 0.8},
            ]
        }
        report = estimate_extra_video_sync(archive, extra, {"phone": 8.0}, 0.5)
        self.assertAlmostEqual(report["phone"]["suggested_offset_s"], 8.0, delta=0.5)
        self.assertGreater(report["phone"]["correlation"], 0.5)

    def test_fusion_creates_event_from_motion_and_audio(self) -> None:
        timeline, events, evidence = fuse_records(
            motion_records=[
                {
                    "source_id": "cam",
                    "session_s": 5.0,
                    "motion_score": 0.8,
                    "evidence": {"annotated_path": "box.png"},
                }
            ],
            audio_records=[
                {
                    "source_id": "mic",
                    "session_s": 5.1,
                    "audio_score": 0.8,
                    "evidence": {"audio_path": "audio.png"},
                }
            ],
            imported_records=[],
            session_start_utc_ns=1_781_084_754_000_000_000,
            threshold=0.55,
            bin_s=0.5,
        )
        self.assertTrue(timeline)
        self.assertEqual(len(events), 1)
        self.assertGreater(events[0]["peak_score"], 0.55)
        self.assertEqual(len(evidence), 2)

    def test_import_detector_records_reads_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "detections.json"
            path.write_text(
                json.dumps(
                    {
                        "source": "camera_demo",
                        "timestamp_s": 3.0,
                        "model": "test",
                        "detections": [{"confidence": 0.7, "x1": 1, "y1": 2, "x2": 3, "y2": 4}],
                    }
                ),
                encoding="utf-8",
            )
            records = import_detector_records([path], None)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["imported_score"], 0.7)
        self.assertEqual(records[0]["session_s"], 3.0)


if __name__ == "__main__":
    unittest.main()
