from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops

from vla_drone_detector import (
    Detection,
    build_prompt,
    candidate_env_paths,
    detection_from_raw,
    detections_from_response,
    draw_boxes,
    filter_detections,
    normalized_box_to_pixels,
    safe_json_loads,
)


def test_safe_json_loads_accepts_clean_fenced_and_prose() -> None:
    clean = '{"detections": []}'
    fenced = '```json\n{"detections": [{"confidence": 0.8}]}\n```'
    prose = 'Result follows: {"detections": [{"confidence": 0.7}]} done.'

    assert safe_json_loads(clean)["detections"] == []
    assert safe_json_loads(fenced)["detections"][0]["confidence"] == 0.8
    assert safe_json_loads(prose)["detections"][0]["confidence"] == 0.7


def test_normalized_box_to_pixels_uses_ymin_xmin_ymax_xmax_order() -> None:
    assert normalized_box_to_pixels([100, 200, 500, 800], 1000, 500) == (
        200.0,
        50.0,
        800.0,
        250.0,
    )


def test_detection_from_raw_serializes_stable_fields() -> None:
    detection = detection_from_raw(
        {
            "box_2d": [100, 200, 500, 800],
            "confidence": 0.91,
            "category": "drone",
            "type": "quadrotor",
            "thermal_signature": "warm core",
            "rationale": "four points",
        },
        1000,
        500,
    )

    assert detection is not None
    assert detection.to_json_dict() == {
        "x1": 200.0,
        "y1": 50.0,
        "x2": 800.0,
        "y2": 250.0,
        "center_x": 500.0,
        "center_y": 150.0,
        "confidence": 0.91,
        "category": "drone",
        "type": "quadrotor",
        "thermal_signature": "warm core",
        "rationale": "four points",
    }


def test_confidence_threshold_filters_detections() -> None:
    detections = [
        Detection(0, 0, 10, 10, 5, 5, 0.2, "drone", "unknown_drone", "unknown", ""),
        Detection(0, 0, 10, 10, 5, 5, 0.8, "airplane", "jet_aircraft", "visible_rgb", ""),
    ]

    filtered = filter_detections(detections, 0.5)

    assert len(filtered) == 1
    assert filtered[0].type == "jet_aircraft"


def test_detections_from_response_filters_below_threshold() -> None:
    response = """
    {
      "detections": [
        {"box_2d": [10, 10, 100, 100], "confidence": 0.4, "category": "drone", "type": "fpv_drone"},
        {"box_2d": [200, 200, 300, 300], "confidence": 0.9, "category": "airplane", "type": "glider"}
      ]
    }
    """

    detections = detections_from_response(response, 1000, 1000, 0.5)

    assert len(detections) == 1
    assert detections[0].category == "airplane"


def test_draw_boxes_changes_pixels() -> None:
    image = Image.new("RGB", (100, 100), "black")
    detection = Detection(
        x1=10,
        y1=10,
        x2=50,
        y2=50,
        center_x=30,
        center_y=30,
        confidence=0.88,
        category="drone",
        type="quadrotor",
        thermal_signature="warm",
        rationale="shape",
    )

    annotated = draw_boxes(image, [detection])
    diff = ImageChops.difference(image, annotated)

    assert diff.getbbox() is not None


def test_build_prompts_include_required_terms_for_each_polarity_and_preset() -> None:
    for polarity in ("black_is_warm", "white_is_warm", "visible_rgb"):
        for prompt_type in (
            "thermal_counter_uas",
            "visible_daylight",
            "low_light_or_noisy",
            "custom",
        ):
            prompt = build_prompt(prompt_type, polarity, "camera is fixed")
            assert "custom edge computing VLA model" in prompt
            assert "drones and airplanes only" in prompt
            assert "quadrotor" in prompt
            assert "commercial_airliner" in prompt
            assert '"category": "drone"' in prompt
            assert "[ymin, xmin, ymax, xmax]" in prompt
            assert polarity.split("_")[0] in prompt or polarity == "visible_rgb"


def test_candidate_env_paths_include_repo_root_and_worktree_sibling() -> None:
    candidates = candidate_env_paths()
    repo_root = Path(__file__).resolve().parents[2]

    assert repo_root / ".env" in candidates
    if "-" in repo_root.parent.name:
        base_workspace = repo_root.parent.with_name(repo_root.parent.name.split("-", 1)[0])
        assert base_workspace / repo_root.name / ".env" in candidates
