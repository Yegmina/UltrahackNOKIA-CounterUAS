from __future__ import annotations

import numpy as np
from PIL import Image

from vision_drone_detector import (
    Detection,
    draw_boxes,
    filter_detections,
    serialize_image_result,
)


def test_filter_detections_applies_confidence_threshold() -> None:
    detections = [
        Detection(0, 0, 10, 10, 0.24, 0, "uav"),
        Detection(5, 5, 12, 12, 0.25, 0, "uav"),
        Detection(8, 8, 16, 16, 0.9, 0, "uav"),
    ]
    kept = filter_detections(detections, 0.25)
    assert [detection.confidence for detection in kept] == [0.25, 0.9]


def test_serialize_image_result_has_stable_fields() -> None:
    detections = [Detection(1.1234567, 2, 30, 40, 0.8765432, 0, "uav")]
    record = serialize_image_result("frame.jpg", "best.pt", 0.25, 640, 360, detections)
    assert record == {
        "source": "frame.jpg",
        "model": "best.pt",
        "confidence_threshold": 0.25,
        "image_width": 640,
        "image_height": 360,
        "detections": [
            {
                "x1": 1.123457,
                "y1": 2.0,
                "x2": 30.0,
                "y2": 40.0,
                "confidence": 0.876543,
                "class_id": 0,
                "class_name": "uav",
            }
        ],
    }


def test_draw_boxes_changes_image_pixels() -> None:
    image = Image.new("RGB", (80, 60), "white")
    annotated = draw_boxes(image, [Detection(10, 10, 50, 40, 0.8, 0, "uav")])
    assert not np.array_equal(np.array(image), np.array(annotated))
