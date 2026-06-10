from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from data_collection_firmware import (
    CaptureDevice,
    StreamSummary,
    draw_timestamp_overlay,
    make_manifest,
    parse_dshow_devices,
    safe_slug,
    stop_requested,
    timestamp_visible,
    write_manifest,
)


def test_device_name_slugging_is_stable() -> None:
    assert safe_slug("Microphone (Realtek Audio USB)") == "microphone_realtek_audio_usb"
    assert safe_slug(" HP Wide Vision HD Camera ") == "hp_wide_vision_hd_camera"
    assert safe_slug("!!!", fallback="camera") == "camera"


def test_parse_dshow_devices_from_ffmpeg_output() -> None:
    text = """
[dshow @ 000001] "DEMO1 " (video)
[dshow @ 000001]   Alternative name "@device_pnp_video"
[dshow @ 000001] "Microphone (Realtek Audio USB)" (audio)
[dshow @ 000001]   Alternative name "@device_audio"
[dshow @ 000001] "Microphone (broken)" (none)
"""
    devices = parse_dshow_devices(text)

    assert [device.kind for device in devices] == ["video", "audio", "none"]
    assert devices[0].name == "DEMO1 "
    assert devices[0].alternative_name == "@device_pnp_video"
    assert devices[1].usable is True
    assert devices[2].usable is False


def test_timestamp_overlay_cadence() -> None:
    assert timestamp_visible(0.1, interval_s=10.0, visible_s=1.5)
    assert timestamp_visible(10.1, interval_s=10.0, visible_s=1.5)
    assert not timestamp_visible(2.0, interval_s=10.0, visible_s=1.5)
    assert not timestamp_visible(9.9, interval_s=10.0, visible_s=1.5)


def test_draw_timestamp_overlay_changes_pixels_only_when_visible() -> None:
    frame = np.zeros((80, 320, 3), dtype=np.uint8)

    visible = draw_timestamp_overlay(frame, 1_700_000_000_123_000_000, 0.1, 10.0, 1.5)
    hidden = draw_timestamp_overlay(frame, 1_700_000_000_123_000_000, 3.0, 10.0, 1.5)

    assert np.count_nonzero(visible) > 0
    assert np.array_equal(hidden, frame)


def test_manifest_schema_includes_streams_timing_and_markers() -> None:
    camera = CaptureDevice(kind="video", name="Camera 0", slug="camera_0", index=0)
    audio = CaptureDevice(kind="audio", name="Mic 1", slug="mic_1", index=1)
    streams = [
        StreamSummary(
            kind="video",
            name="Camera 0",
            slug="camera_0",
            index=0,
            path="camera_camera_0.mp4",
            timing_path="video_frames_camera_0.jsonl",
            started_monotonic_ns=1_010_000_000,
        ),
        StreamSummary(
            kind="audio",
            name="Mic 1",
            slug="mic_1",
            index=1,
            path="audio_mic_1.wav",
            timing_path="audio_chunks_mic_1.jsonl",
            started_monotonic_ns=1_012_000_000,
        ),
    ]

    manifest = make_manifest(
        session_id="test",
        settings={"session_name": "test"},
        devices={"cameras": [camera], "audio_inputs": [audio]},
        streams=streams,
        markers=[{"label": "session_start", "utc_ns": 2_000_000_000, "monotonic_ns": 1_000_000_000}],
        session_start_utc_ns=2_000_000_000,
        session_start_monotonic_ns=1_000_000_000,
        session_stop_utc_ns=3_000_000_000,
        session_stop_monotonic_ns=2_000_000_000,
    )

    assert manifest["session_id"] == "test"
    assert manifest["max_start_jitter_ms"] == 12.0
    assert manifest["streams"][0]["timing_path"] == "video_frames_camera_0.jsonl"
    assert manifest["sync_markers"][0]["label"] == "session_start"
    assert manifest["clock"]["time_source"] == "system_clock"


def test_write_manifest_also_writes_stream_summary_csv(tmp_path: Path) -> None:
    manifest = {
        "streams": [
            {
                "kind": "video",
                "name": "Camera",
                "slug": "camera",
                "index": 0,
                "path": "camera.mp4",
                "frames": 10,
                "samples": 0,
                "chunks": 0,
                "errors": [],
            }
        ]
    }
    manifest_path = tmp_path / "session_manifest.json"

    write_manifest(manifest_path, manifest)

    assert json.loads(manifest_path.read_text(encoding="utf-8"))["streams"][0]["frames"] == 10
    assert (tmp_path / "stream_summary.csv").exists()


def test_stop_file_detection(tmp_path: Path) -> None:
    stop_file = tmp_path / "stop_requested"

    assert not stop_requested(str(stop_file))
    stop_file.write_text("stop\n", encoding="utf-8")
    assert stop_requested(str(stop_file))
