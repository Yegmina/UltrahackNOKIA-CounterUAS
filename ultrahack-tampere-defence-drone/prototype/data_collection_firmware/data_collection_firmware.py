"""Laptop multi-sensor data collection runner.

Records selected cameras and microphones into separate synchronized files with
per-stream timing metadata for later RGB/audio/IR alignment.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import queue
import re
import subprocess
import sys
import threading
import time
import uuid
import wave
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np

try:
    cv2.setLogLevel(0)
except Exception:
    pass

try:
    import pyaudio
except ImportError:  # pragma: no cover - covered by runtime diagnostics.
    pyaudio = None


PROGRESS_PREFIX = "PROGRESS "
DEFAULT_OUTPUT_ROOT = Path(__file__).with_name("outputs")


@dataclass(frozen=True)
class CaptureDevice:
    kind: str
    name: str
    slug: str
    index: int | None = None
    backend: str = "unknown"
    alternative_name: str | None = None
    usable: bool = True

    def to_json_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StreamSummary:
    kind: str
    name: str
    slug: str
    index: int | None
    path: str
    timing_path: str
    started_utc_ns: int | None = None
    started_monotonic_ns: int | None = None
    stopped_utc_ns: int | None = None
    stopped_monotonic_ns: int | None = None
    frames: int = 0
    samples: int = 0
    chunks: int = 0
    errors: list[str] = field(default_factory=list)

    def to_json_dict(self) -> dict[str, Any]:
        return asdict(self)


def utc_now_ns() -> int:
    return time.time_ns()


def monotonic_now_ns() -> int:
    return time.monotonic_ns()


def utc_iso_from_ns(value_ns: int) -> str:
    dt = datetime.fromtimestamp(value_ns / 1_000_000_000, tz=timezone.utc)
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def safe_slug(name: str, fallback: str = "device") -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", name.strip().lower()).strip("_")
    slug = re.sub(r"_+", "_", slug)
    return slug or fallback


def timestamp_visible(elapsed_s: float, interval_s: float = 10.0, visible_s: float = 1.5) -> bool:
    if interval_s <= 0:
        return True
    if elapsed_s < 0:
        return False
    return math.fmod(elapsed_s, interval_s) < max(0.0, visible_s)


def timestamp_text(utc_ns: int) -> str:
    dt = datetime.fromtimestamp(utc_ns / 1_000_000_000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S.") + f"{int((utc_ns % 1_000_000_000) / 1_000_000):03d} UTC"


def draw_timestamp_overlay(
    frame: np.ndarray,
    utc_ns: int,
    elapsed_s: float,
    interval_s: float = 10.0,
    visible_s: float = 1.5,
) -> np.ndarray:
    if not timestamp_visible(elapsed_s, interval_s, visible_s):
        return frame
    output = frame.copy()
    text = timestamp_text(utc_ns)
    cv2.putText(
        output,
        text,
        (9, 19),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.42,
        (0, 0, 0),
        1,
        cv2.LINE_AA,
    )
    cv2.putText(
        output,
        text,
        (8, 18),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.42,
        (255, 255, 255),
        1,
        cv2.LINE_AA,
    )
    return output


def parse_dshow_devices(text: str) -> list[CaptureDevice]:
    devices: list[CaptureDevice] = []
    pending: CaptureDevice | None = None
    pattern = re.compile(r'\[dshow @ [^\]]+\] "(.+)" \((video|audio|none)\)')
    alt_pattern = re.compile(r'\[dshow @ [^\]]+\]\s+Alternative name "(.+)"')
    for line in text.splitlines():
        match = pattern.search(line)
        if match:
            if pending is not None:
                devices.append(pending)
            name, kind = match.groups()
            pending = CaptureDevice(
                kind=kind,
                name=name,
                slug=safe_slug(name),
                backend="dshow",
                usable=kind in {"video", "audio"},
            )
            continue
        alt_match = alt_pattern.search(line)
        if alt_match and pending is not None:
            pending = CaptureDevice(
                kind=pending.kind,
                name=pending.name,
                slug=pending.slug,
                index=pending.index,
                backend=pending.backend,
                alternative_name=alt_match.group(1),
                usable=pending.usable,
            )
    if pending is not None:
        devices.append(pending)
    return devices


def list_dshow_devices() -> list[CaptureDevice]:
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return parse_dshow_devices(result.stdout or "")


def list_opencv_cameras(max_index: int = 8) -> list[CaptureDevice]:
    cameras: list[CaptureDevice] = []
    dshow_videos = [device for device in list_dshow_devices() if device.kind == "video" and device.usable]
    for index in range(max_index):
        capture = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        ok = bool(capture.isOpened())
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        capture.release()
        if not ok:
            continue
        fallback_name = f"Camera {index}"
        dshow_device = dshow_videos[index] if index < len(dshow_videos) else None
        name = dshow_device.name if dshow_device else fallback_name
        details = f"{name} ({width}x{height})" if width and height else name
        cameras.append(
            CaptureDevice(
                kind="video",
                name=details,
                slug=safe_slug(name),
                index=index,
                backend="opencv_dshow",
                alternative_name=dshow_device.alternative_name if dshow_device else None,
                usable=True,
            )
        )
    return cameras


def list_pyaudio_inputs() -> list[CaptureDevice]:
    if pyaudio is None:
        return []
    audio = pyaudio.PyAudio()
    devices: list[CaptureDevice] = []
    seen: set[str] = set()
    try:
        preferred_host_api = 0
        for host_index in range(audio.get_host_api_count()):
            host_info = audio.get_host_api_info_by_index(host_index)
            if str(host_info.get("name", "")).lower() == "mme":
                preferred_host_api = host_index
                break
        for index in range(audio.get_device_count()):
            info = audio.get_device_info_by_index(index)
            if int(info.get("hostApi", -1)) != preferred_host_api:
                continue
            if int(info.get("maxInputChannels", 0)) <= 0:
                continue
            name = str(info.get("name") or f"Audio input {index}")
            lower_name = name.lower()
            if "sound mapper" in lower_name or "primary sound capture driver" in lower_name:
                continue
            if not any(term in lower_name for term in ("microphone", "mic", "array", "input")):
                continue
            canonical = re.sub(r"[^a-z0-9]+", "", lower_name)
            canonical = canonical.replace("wave", "")
            canonical = canonical[:48]
            if canonical in seen:
                continue
            seen.add(canonical)
            devices.append(
                CaptureDevice(
                    kind="audio",
                    name=name,
                    slug=safe_slug(name),
                    index=index,
                    backend="pyaudio_mme",
                    usable=True,
                )
            )
    finally:
        audio.terminate()
    return devices


def discover_devices() -> dict[str, Any]:
    dshow_devices = list_dshow_devices()
    return {
        "dshow": [device.to_json_dict() for device in dshow_devices],
        "cameras": [device.to_json_dict() for device in list_opencv_cameras()],
        "audio_inputs": [device.to_json_dict() for device in list_pyaudio_inputs()],
    }


def make_session_id(name: str | None = None) -> str:
    prefix = safe_slug(name or "session")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"{prefix}_{stamp}_{uuid.uuid4().hex[:8]}"


def make_manifest(
    session_id: str,
    settings: dict[str, Any],
    devices: dict[str, list[CaptureDevice]],
    streams: list[StreamSummary],
    markers: list[dict[str, Any]],
    session_start_utc_ns: int,
    session_start_monotonic_ns: int,
    session_stop_utc_ns: int | None = None,
    session_stop_monotonic_ns: int | None = None,
) -> dict[str, Any]:
    start_offsets = [
        abs(stream.started_monotonic_ns - session_start_monotonic_ns)
        for stream in streams
        if stream.started_monotonic_ns is not None
    ]
    max_start_jitter_ms = max(start_offsets) / 1_000_000 if start_offsets else None
    return {
        "schema_version": 1,
        "session_id": session_id,
        "session_start_utc_ns": session_start_utc_ns,
        "session_start_utc": utc_iso_from_ns(session_start_utc_ns),
        "session_start_monotonic_ns": session_start_monotonic_ns,
        "session_stop_utc_ns": session_stop_utc_ns,
        "session_stop_utc": utc_iso_from_ns(session_stop_utc_ns) if session_stop_utc_ns else None,
        "session_stop_monotonic_ns": session_stop_monotonic_ns,
        "max_start_jitter_ms": max_start_jitter_ms,
        "settings": settings,
        "clock": {
            "timezone": "UTC",
            "time_source": "system_clock",
            "utc_minus_monotonic_ns_at_start": session_start_utc_ns - session_start_monotonic_ns,
        },
        "devices": {
            "cameras": [device.to_json_dict() for device in devices.get("cameras", [])],
            "audio_inputs": [device.to_json_dict() for device in devices.get("audio_inputs", [])],
        },
        "streams": [stream.to_json_dict() for stream in streams],
        "sync_markers": markers,
    }


def emit_progress(args: argparse.Namespace, payload: dict[str, Any]) -> None:
    if getattr(args, "progress_json", False):
        print(PROGRESS_PREFIX + json.dumps(payload, ensure_ascii=False), flush=True)


def stop_requested(path: str | None) -> bool:
    return bool(path and Path(path).exists())


def play_beep(enabled: bool = True) -> None:
    if not enabled:
        return
    try:
        import winsound

        winsound.Beep(880, 180)
        winsound.Beep(1320, 120)
    except Exception:
        pass


class CameraWorker(threading.Thread):
    def __init__(
        self,
        device: CaptureDevice,
        out_dir: Path,
        start_event: threading.Event,
        stop_event: threading.Event,
        utc_offset_ns: int,
        settings: dict[str, Any],
        error_queue: "queue.Queue[str]",
    ):
        super().__init__(daemon=True)
        self.device = device
        self.out_dir = out_dir
        self.start_event = start_event
        self.stop_event = stop_event
        self.utc_offset_ns = utc_offset_ns
        self.settings = settings
        self.error_queue = error_queue
        self.capture: cv2.VideoCapture | None = None
        self.summary = StreamSummary(
            kind="video",
            name=device.name,
            slug=device.slug,
            index=device.index,
            path=str(out_dir / f"camera_{device.slug}.mp4"),
            timing_path=str(out_dir / f"video_frames_{device.slug}.jsonl"),
        )

    def open_device(self) -> None:
        if self.device.index is None:
            raise RuntimeError(f"Camera has no OpenCV index: {self.device.name}")
        capture = cv2.VideoCapture(int(self.device.index), cv2.CAP_DSHOW)
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, int(self.settings["video_width"]))
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, int(self.settings["video_height"]))
        capture.set(cv2.CAP_PROP_FPS, float(self.settings["fps"]))
        if not capture.isOpened():
            raise RuntimeError(f"Could not open camera {self.device.name}")
        self.capture = capture

    def run(self) -> None:
        writer = None
        timing = None
        try:
            assert self.capture is not None
            while not self.start_event.is_set() and not self.stop_event.is_set():
                self.capture.grab()
                time.sleep(0.002)
            self.start_event.wait()
            timing = Path(self.summary.timing_path).open("w", encoding="utf-8")
            fps = max(1.0, float(self.capture.get(cv2.CAP_PROP_FPS) or self.settings["fps"]))
            width = int(self.capture.get(cv2.CAP_PROP_FRAME_WIDTH) or self.settings["video_width"])
            height = int(self.capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or self.settings["video_height"])
            writer = cv2.VideoWriter(
                self.summary.path,
                cv2.VideoWriter_fourcc(*"mp4v"),
                fps,
                (width, height),
            )
            if not writer.isOpened():
                raise RuntimeError(f"Could not open video writer: {self.summary.path}")
            frame_index = 0
            last_mono_ns: int | None = None
            expected_frame_ns = int(1_000_000_000 / fps)
            while not self.stop_event.is_set():
                ok, frame = self.capture.read()
                now_mono = monotonic_now_ns()
                now_utc = now_mono + self.utc_offset_ns
                if not ok:
                    time.sleep(0.01)
                    continue
                if frame.shape[1] != width or frame.shape[0] != height:
                    frame = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
                if self.summary.started_monotonic_ns is None:
                    self.summary.started_monotonic_ns = now_mono
                    self.summary.started_utc_ns = now_utc
                elapsed_s = (now_mono - int(self.settings["session_start_monotonic_ns"])) / 1_000_000_000
                output = draw_timestamp_overlay(
                    frame,
                    now_utc,
                    elapsed_s,
                    float(self.settings["timestamp_interval_s"]),
                    float(self.settings["timestamp_visible_s"]),
                )
                writer.write(output)
                dropped = False
                estimated_dropped = 0
                if last_mono_ns is not None and expected_frame_ns > 0:
                    gap = now_mono - last_mono_ns
                    if gap > expected_frame_ns * 1.8:
                        dropped = True
                        estimated_dropped = max(0, int(round(gap / expected_frame_ns)) - 1)
                timing.write(
                    json.dumps(
                        {
                            "frame_index": frame_index,
                            "utc_ns": now_utc,
                            "utc_ms": now_utc // 1_000_000,
                            "utc_iso": utc_iso_from_ns(now_utc),
                            "monotonic_ns": now_mono,
                            "elapsed_s": round(elapsed_s, 6),
                            "dropped_frame_gap": dropped,
                            "estimated_dropped_frames": estimated_dropped,
                        }
                    )
                    + "\n"
                )
                frame_index += 1
                self.summary.frames = frame_index
                last_mono_ns = now_mono
            self.summary.stopped_monotonic_ns = monotonic_now_ns()
            self.summary.stopped_utc_ns = self.summary.stopped_monotonic_ns + self.utc_offset_ns
        except Exception as exc:
            message = f"camera {self.device.name}: {type(exc).__name__}: {exc}"
            self.summary.errors.append(message)
            self.error_queue.put(message)
        finally:
            if timing is not None:
                timing.close()
            if writer is not None:
                writer.release()
            if self.capture is not None:
                self.capture.release()


class AudioWorker(threading.Thread):
    def __init__(
        self,
        device: CaptureDevice,
        out_dir: Path,
        start_event: threading.Event,
        stop_event: threading.Event,
        utc_offset_ns: int,
        settings: dict[str, Any],
        error_queue: "queue.Queue[str]",
    ):
        super().__init__(daemon=True)
        self.device = device
        self.out_dir = out_dir
        self.start_event = start_event
        self.stop_event = stop_event
        self.utc_offset_ns = utc_offset_ns
        self.settings = settings
        self.error_queue = error_queue
        self.audio = None
        self.stream = None
        self.summary = StreamSummary(
            kind="audio",
            name=device.name,
            slug=device.slug,
            index=device.index,
            path=str(out_dir / f"audio_{device.slug}.wav"),
            timing_path=str(out_dir / f"audio_chunks_{device.slug}.jsonl"),
        )

    def open_device(self) -> None:
        if pyaudio is None:
            raise RuntimeError("PyAudio is not installed.")
        if self.device.index is None:
            raise RuntimeError(f"Audio device has no PyAudio index: {self.device.name}")
        self.audio = pyaudio.PyAudio()
        self.stream = self.audio.open(
            format=pyaudio.paInt16,
            channels=int(self.settings["audio_channels"]),
            rate=int(self.settings["audio_rate"]),
            input=True,
            input_device_index=int(self.device.index),
            frames_per_buffer=int(self.settings["audio_chunk_frames"]),
        )

    def run(self) -> None:
        timing = None
        wav_file = None
        try:
            assert self.stream is not None
            assert self.audio is not None
            chunk_frames = int(self.settings["audio_chunk_frames"])
            while not self.start_event.is_set() and not self.stop_event.is_set():
                self.stream.read(chunk_frames, exception_on_overflow=False)
            self.start_event.wait()
            timing = Path(self.summary.timing_path).open("w", encoding="utf-8")
            wav_file = wave.open(self.summary.path, "wb")
            wav_file.setnchannels(int(self.settings["audio_channels"]))
            wav_file.setsampwidth(self.audio.get_sample_size(pyaudio.paInt16))
            wav_file.setframerate(int(self.settings["audio_rate"]))
            chunk_index = 0
            sample_start = 0
            while not self.stop_event.is_set():
                before_mono = monotonic_now_ns()
                data = self.stream.read(chunk_frames, exception_on_overflow=False)
                after_mono = monotonic_now_ns()
                chunk_utc_ns = before_mono + self.utc_offset_ns
                if self.summary.started_monotonic_ns is None:
                    self.summary.started_monotonic_ns = before_mono
                    self.summary.started_utc_ns = chunk_utc_ns
                wav_file.writeframes(data)
                sample_end = sample_start + chunk_frames
                elapsed_s = (before_mono - int(self.settings["session_start_monotonic_ns"])) / 1_000_000_000
                timing.write(
                    json.dumps(
                        {
                            "chunk_index": chunk_index,
                            "sample_start": sample_start,
                            "sample_end": sample_end,
                            "utc_ns": chunk_utc_ns,
                            "utc_ms": chunk_utc_ns // 1_000_000,
                            "utc_iso": utc_iso_from_ns(chunk_utc_ns),
                            "monotonic_ns": before_mono,
                            "read_end_monotonic_ns": after_mono,
                            "elapsed_s": round(elapsed_s, 6),
                            "frames": chunk_frames,
                        }
                    )
                    + "\n"
                )
                sample_start = sample_end
                chunk_index += 1
                self.summary.chunks = chunk_index
                self.summary.samples = sample_start
            self.summary.stopped_monotonic_ns = monotonic_now_ns()
            self.summary.stopped_utc_ns = self.summary.stopped_monotonic_ns + self.utc_offset_ns
        except Exception as exc:
            message = f"audio {self.device.name}: {type(exc).__name__}: {exc}"
            self.summary.errors.append(message)
            self.error_queue.put(message)
        finally:
            if timing is not None:
                timing.close()
            if wav_file is not None:
                wav_file.close()
            if self.stream is not None:
                try:
                    self.stream.stop_stream()
                    self.stream.close()
                except Exception:
                    pass
            if self.audio is not None:
                self.audio.terminate()


def select_devices(args: argparse.Namespace) -> tuple[list[CaptureDevice], list[CaptureDevice]]:
    all_cameras = list_opencv_cameras()
    all_audio = list_pyaudio_inputs()
    if args.all_detected:
        return all_cameras, all_audio
    camera_indices = {int(value) for value in args.camera_index}
    audio_indices = {int(value) for value in args.audio_index}
    cameras = [device for device in all_cameras if device.index in camera_indices]
    audio_inputs = [device for device in all_audio if device.index in audio_indices]
    return cameras, audio_inputs


def marker_watcher(
    marker_file: Path,
    markers: list[dict[str, Any]],
    stop_event: threading.Event,
    utc_offset_ns: int,
    beep: bool,
) -> None:
    seen = 0
    while not stop_event.is_set():
        if marker_file.exists():
            lines = marker_file.read_text(encoding="utf-8", errors="ignore").splitlines()
            for line in lines[seen:]:
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    payload = {"label": line.strip() or "manual_marker"}
                now_mono = monotonic_now_ns()
                markers.append(
                    {
                        "label": payload.get("label", "manual_marker"),
                        "source": payload.get("source", "ui"),
                        "utc_ns": now_mono + utc_offset_ns,
                        "utc_iso": utc_iso_from_ns(now_mono + utc_offset_ns),
                        "monotonic_ns": now_mono,
                    }
                )
                play_beep(beep)
            seen = len(lines)
        time.sleep(0.1)


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    csv_path = path.with_name("stream_summary.csv")
    with csv_path.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=["kind", "name", "slug", "index", "path", "frames", "samples", "chunks", "errors"],
        )
        writer.writeheader()
        for stream in manifest.get("streams", []):
            writer.writerow(
                {
                    "kind": stream.get("kind"),
                    "name": stream.get("name"),
                    "slug": stream.get("slug"),
                    "index": stream.get("index"),
                    "path": stream.get("path"),
                    "frames": stream.get("frames"),
                    "samples": stream.get("samples"),
                    "chunks": stream.get("chunks"),
                    "errors": "; ".join(stream.get("errors", [])),
                }
            )


def record_session(args: argparse.Namespace) -> dict[str, Any]:
    session_id = make_session_id(args.session_name)
    out_dir = Path(args.out_dir or DEFAULT_OUTPUT_ROOT) / session_id
    out_dir.mkdir(parents=True, exist_ok=True)
    cameras, audio_inputs = select_devices(args)
    if not cameras and not audio_inputs:
        raise RuntimeError("No camera or audio devices selected.")

    now_mono = monotonic_now_ns()
    utc_offset_ns = utc_now_ns() - now_mono
    session_start_monotonic_ns = now_mono + int(float(args.schedule_delay_s) * 1_000_000_000)
    session_start_utc_ns = session_start_monotonic_ns + utc_offset_ns
    settings: dict[str, Any] = {
        "session_name": args.session_name,
        "location": args.location,
        "notes": args.notes,
        "video_width": int(args.video_width),
        "video_height": int(args.video_height),
        "fps": float(args.fps),
        "audio_rate": int(args.audio_rate),
        "audio_channels": int(args.audio_channels),
        "audio_chunk_frames": int(args.audio_chunk_frames),
        "timestamp_interval_s": float(args.timestamp_interval_s),
        "timestamp_visible_s": float(args.timestamp_visible_s),
        "schedule_delay_s": float(args.schedule_delay_s),
        "session_start_monotonic_ns": session_start_monotonic_ns,
    }

    start_event = threading.Event()
    stop_event = threading.Event()
    error_queue: "queue.Queue[str]" = queue.Queue()
    workers: list[CameraWorker | AudioWorker] = []
    for camera in cameras:
        workers.append(CameraWorker(camera, out_dir, start_event, stop_event, utc_offset_ns, settings, error_queue))
    for audio_device in audio_inputs:
        workers.append(AudioWorker(audio_device, out_dir, start_event, stop_event, utc_offset_ns, settings, error_queue))

    opened_workers: list[CameraWorker | AudioWorker] = []
    failed_summaries: list[StreamSummary] = []
    for worker in workers:
        try:
            worker.open_device()
        except Exception as exc:
            message = f"{worker.summary.kind} {worker.summary.name}: {type(exc).__name__}: {exc}"
            worker.summary.errors.append(message)
            failed_summaries.append(worker.summary)
        else:
            opened_workers.append(worker)
    workers = opened_workers
    if not workers:
        raise RuntimeError("Selected devices were found, but none could be opened for recording.")

    markers: list[dict[str, Any]] = []
    marker_file = Path(args.marker_file) if args.marker_file else out_dir / "sync_markers.jsonl"
    marker_thread = threading.Thread(
        target=marker_watcher,
        args=(marker_file, markers, stop_event, utc_offset_ns, bool(args.sync_beep)),
        daemon=True,
    )
    marker_thread.start()

    for worker in workers:
        worker.start()

    emit_progress(
        args,
        {
            "stage": "armed",
            "session_id": session_id,
            "session_start_utc_ns": session_start_utc_ns,
            "session_start_utc": utc_iso_from_ns(session_start_utc_ns),
        "cameras": sum(1 for worker in workers if worker.summary.kind == "video"),
        "audio_inputs": sum(1 for worker in workers if worker.summary.kind == "audio"),
        "failed_streams": len(failed_summaries),
        "out_dir": str(out_dir),
        },
    )

    while monotonic_now_ns() < session_start_monotonic_ns:
        if stop_requested(args.stop_file):
            stop_event.set()
            break
        time.sleep(0.005)

    markers.append(
        {
            "label": "session_start",
            "source": "collector",
            "utc_ns": session_start_utc_ns,
            "utc_iso": utc_iso_from_ns(session_start_utc_ns),
            "monotonic_ns": session_start_monotonic_ns,
        }
    )
    play_beep(bool(args.sync_beep))
    start_event.set()

    started_at = monotonic_now_ns()
    try:
        while not stop_event.is_set():
            elapsed = (monotonic_now_ns() - session_start_monotonic_ns) / 1_000_000_000
            if args.duration_s and elapsed >= float(args.duration_s):
                stop_event.set()
                break
            if stop_requested(args.stop_file):
                stop_event.set()
                break
            errors = []
            while True:
                try:
                    errors.append(error_queue.get_nowait())
                except queue.Empty:
                    break
            emit_progress(
                args,
                {
                    "stage": "recording",
                    "session_id": session_id,
                    "elapsed_s": max(0.0, elapsed),
                    "video_frames": sum(getattr(worker.summary, "frames", 0) for worker in workers),
                    "audio_samples": sum(getattr(worker.summary, "samples", 0) for worker in workers),
                    "markers": len(markers),
                    "errors": errors,
                },
            )
            time.sleep(max(0.1, float(args.progress_interval)))
    finally:
        stop_event.set()
        for worker in workers:
            worker.join(timeout=5)

    stop_mono = monotonic_now_ns()
    stop_utc = stop_mono + utc_offset_ns
    stream_summaries = [worker.summary for worker in workers] + failed_summaries
    manifest = make_manifest(
        session_id=session_id,
        settings=settings,
        devices={"cameras": cameras, "audio_inputs": audio_inputs},
        streams=stream_summaries,
        markers=markers,
        session_start_utc_ns=session_start_utc_ns,
        session_start_monotonic_ns=session_start_monotonic_ns,
        session_stop_utc_ns=stop_utc,
        session_stop_monotonic_ns=stop_mono,
    )
    manifest["processing_seconds"] = round((stop_mono - started_at) / 1_000_000_000, 3)
    manifest["out_dir"] = str(out_dir)
    manifest["manifest_path"] = str(out_dir / "session_manifest.json")
    write_manifest(out_dir / "session_manifest.json", manifest)
    emit_progress(args, {"stage": "complete", "session_id": session_id, "manifest_path": manifest["manifest_path"]})
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Laptop multi-sensor data collection prototype.")
    parser.add_argument("--json", action="store_true", help="Print final JSON summary.")
    parser.add_argument("--progress-json", action="store_true", help="Print progress JSON lines.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list-devices", help="List available cameras and microphone inputs.")

    record = subparsers.add_parser("record", help="Record synchronized camera/audio streams.")
    record.add_argument("--all-detected", action="store_true", help="Record all detected usable devices.")
    record.add_argument("--camera-index", action="append", default=[], help="OpenCV camera index to record.")
    record.add_argument("--audio-index", action="append", default=[], help="PyAudio input index to record.")
    record.add_argument("--session-name", default="data_collection")
    record.add_argument("--location", default="")
    record.add_argument("--notes", default="")
    record.add_argument("--out-dir", default=str(DEFAULT_OUTPUT_ROOT))
    record.add_argument("--duration-s", type=float, default=0.0, help="Stop automatically after N seconds; 0 means manual stop.")
    record.add_argument("--stop-file", default="")
    record.add_argument("--marker-file", default="")
    record.add_argument("--progress-interval", type=float, default=1.0)
    record.add_argument("--schedule-delay-s", type=float, default=2.0)
    record.add_argument("--video-width", type=int, default=1280)
    record.add_argument("--video-height", type=int, default=720)
    record.add_argument("--fps", type=float, default=30.0)
    record.add_argument("--audio-rate", type=int, default=48000)
    record.add_argument("--audio-channels", type=int, default=1)
    record.add_argument("--audio-chunk-frames", type=int, default=1024)
    record.add_argument("--timestamp-interval-s", type=float, default=10.0)
    record.add_argument("--timestamp-visible-s", type=float, default=1.5)
    record.add_argument("--sync-beep", action="store_true", help="Play beep at start and marker events.")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.command == "list-devices":
            payload = discover_devices()
        elif args.command == "record":
            payload = record_session(args)
        else:  # pragma: no cover
            parser.error(f"Unsupported command: {args.command}")
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        raise
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
