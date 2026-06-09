"""Jetson/laptop Counter-UAS fusion prototype.

This is intentionally modest: it gives the hackathon system one runnable place
to combine RGB, thermal UDP frames, and later audio/model outputs. The detector
logic is heuristic until a real drone model is dropped in.
"""

from __future__ import annotations

import argparse
import json
import queue
import socket
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from pan_tilt_controller import PixelTrackerController, SerialMountClient, UdpMountClient


MAGIC = b"YEGMINA_THERMAL_RAW_V1 "


@dataclass
class ThermalFrame:
    frame_id: str
    timestamp: float
    image: np.ndarray


@dataclass
class AudioScore:
    timestamp: float
    score: float
    rms: float
    peak_hz: float
    band_ratio: float


@dataclass
class DetectionState:
    rgb_score: float = 0.0
    thermal_score: float = 0.0
    audio_score: float = 0.0
    fused_score: float = 0.0
    target_xy: tuple[int, int] | None = None
    target_radius: int = 0
    status: str = "idle"


@dataclass
class RgbMotionAnalysis:
    score: float
    target_xy: tuple[int, int] | None
    target_radius: int
    previous_gray: np.ndarray | None
    candidate_area: float = 0.0
    global_motion: bool = False
    global_dx: float = 0.0
    global_dy: float = 0.0
    global_consensus: float = 0.0
    tracked_vectors: int = 0


class ThermalUdpThread(threading.Thread):
    def __init__(
        self,
        host: str,
        port: int,
        width: int,
        height: int,
        out_queue: "queue.Queue[ThermalFrame]",
        stop_event: threading.Event,
        max_packet: int = 2048,
        stale_seconds: float = 3.0,
    ) -> None:
        super().__init__(daemon=True)
        self.host = host
        self.port = port
        self.width = width
        self.height = height
        self.out_queue = out_queue
        self.stop_event = stop_event
        self.max_packet = max_packet
        self.stale_seconds = stale_seconds
        self.partials: dict[str, dict[str, Any]] = {}

    def run(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((self.host, self.port))
        sock.settimeout(0.5)
        expected_total = self.width * self.height * 2
        while not self.stop_event.is_set():
            try:
                packet, _sender = sock.recvfrom(self.max_packet)
            except socket.timeout:
                self._drop_stale()
                continue

            parsed = parse_thermal_packet(packet)
            if parsed is None:
                continue
            frame_id, chunk, chunks, offset, total, payload = parsed
            if total != expected_total:
                continue
            if chunk < 0 or chunk >= chunks or offset < 0 or offset + len(payload) > total:
                continue

            partial = self.partials.get(frame_id)
            if partial is None:
                partial = {
                    "data": bytearray(total),
                    "seen": set(),
                    "chunks": chunks,
                    "updated": time.time(),
                }
                self.partials[frame_id] = partial

            partial["data"][offset : offset + len(payload)] = payload
            partial["seen"].add(chunk)
            partial["updated"] = time.time()
            if len(partial["seen"]) != partial["chunks"]:
                continue

            raw = bytes(partial["data"])
            del self.partials[frame_id]
            image = np.frombuffer(raw, dtype="<u2").reshape(self.height, self.width)
            put_latest(self.out_queue, ThermalFrame(frame_id, time.time(), image))

    def _drop_stale(self) -> None:
        now = time.time()
        stale = [
            frame_id
            for frame_id, partial in self.partials.items()
            if now - partial["updated"] > self.stale_seconds
        ]
        for frame_id in stale:
            del self.partials[frame_id]


class AudioWavThread(threading.Thread):
    def __init__(
        self,
        url: str,
        out_queue: "queue.Queue[AudioScore]",
        stop_event: threading.Event,
        chunk_bytes: int = 8192,
        reconnect_delay: float = 1.0,
    ) -> None:
        super().__init__(daemon=True)
        self.url = url
        self.out_queue = out_queue
        self.stop_event = stop_event
        self.chunk_bytes = chunk_bytes
        self.reconnect_delay = reconnect_delay

    def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                with urllib.request.urlopen(self.url, timeout=5) as response:
                    stream_info, pending = read_wav_stream_header(response)
                    if stream_info is None:
                        time.sleep(self.reconnect_delay)
                        continue
                    sample_rate, channels, bits_per_sample = stream_info
                    if bits_per_sample != 16:
                        print(f"audio: unsupported wav bits_per_sample={bits_per_sample}")
                        return
                    buffer = bytearray(pending)
                    while not self.stop_event.is_set():
                        if len(buffer) < self.chunk_bytes:
                            chunk = response.read(self.chunk_bytes - len(buffer))
                            if not chunk:
                                break
                            buffer.extend(chunk)
                            continue
                        payload = bytes(buffer[: self.chunk_bytes])
                        del buffer[: self.chunk_bytes]
                        score = score_pcm16_audio(payload, sample_rate, channels)
                        put_latest(self.out_queue, score)
            except Exception as exc:
                print(f"audio: reconnect after {type(exc).__name__}: {exc}")
                time.sleep(self.reconnect_delay)


class DemoAudioThread(threading.Thread):
    def __init__(
        self,
        out_queue: "queue.Queue[AudioScore]",
        stop_event: threading.Event,
        sample_rate: int = 16000,
        chunk_samples: int = 2048,
    ) -> None:
        super().__init__(daemon=True)
        self.out_queue = out_queue
        self.stop_event = stop_event
        self.sample_rate = sample_rate
        self.chunk_samples = chunk_samples
        self.index = 0

    def run(self) -> None:
        while not self.stop_event.is_set():
            t = (np.arange(self.chunk_samples) + self.index) / self.sample_rate
            envelope = 0.35 + 0.15 * np.sin(self.index / self.sample_rate)
            signal = (
                envelope * np.sin(2 * np.pi * 220.0 * t)
                + 0.18 * np.sin(2 * np.pi * 440.0 * t)
                + 0.08 * np.sin(2 * np.pi * 660.0 * t)
            )
            pcm = np.clip(signal * 32767.0, -32768, 32767).astype("<i2")
            put_latest(
                self.out_queue,
                score_pcm16_audio(pcm.tobytes(), self.sample_rate, channels=1),
            )
            self.index += self.chunk_samples
            time.sleep(self.chunk_samples / self.sample_rate)


def parse_thermal_packet(packet: bytes) -> tuple[str, int, int, int, int, bytes] | None:
    if not packet.startswith(MAGIC):
        return None
    try:
        header, payload = packet.split(b"\n", 1)
    except ValueError:
        return None

    fields: dict[str, str] = {}
    for part in header[len(MAGIC) :].decode("ascii", errors="replace").split():
        if "=" in part:
            key, value = part.split("=", 1)
            fields[key] = value
    required = ("frame", "chunk", "chunks", "offset", "total")
    if any(key not in fields for key in required):
        return None
    return (
        fields["frame"],
        int(fields["chunk"]),
        int(fields["chunks"]),
        int(fields["offset"]),
        int(fields["total"]),
        payload,
    )


def put_latest(out_queue: "queue.Queue[Any]", value: Any) -> None:
    while True:
        try:
            out_queue.get_nowait()
        except queue.Empty:
            break
    out_queue.put_nowait(value)


def read_latest(in_queue: "queue.Queue[Any]", fallback: Any) -> Any:
    value = fallback
    while True:
        try:
            value = in_queue.get_nowait()
        except queue.Empty:
            return value


def read_wav_stream_header(response: Any) -> tuple[tuple[int, int, int] | None, bytes]:
    header = bytearray()
    while len(header) < 16384:
        chunk = response.read(1024)
        if not chunk:
            return None, b""
        header.extend(chunk)
        fmt_idx = header.find(b"fmt ")
        data_idx = header.find(b"data")
        if fmt_idx < 0 or data_idx < 0 or len(header) < data_idx + 8:
            continue
        if len(header) < fmt_idx + 24:
            continue
        fmt_size = int.from_bytes(header[fmt_idx + 4 : fmt_idx + 8], "little")
        fmt_start = fmt_idx + 8
        fmt_end = fmt_start + fmt_size
        if len(header) < fmt_end:
            continue
        fmt_data = header[fmt_start:fmt_end]
        if len(fmt_data) < 16:
            return None, b""
        audio_format = int.from_bytes(fmt_data[0:2], "little")
        channels = int.from_bytes(fmt_data[2:4], "little")
        sample_rate = int.from_bytes(fmt_data[4:8], "little")
        bits_per_sample = int.from_bytes(fmt_data[14:16], "little")
        if audio_format != 1 or channels <= 0 or sample_rate <= 0:
            return None, b""
        payload_start = data_idx + 8
        return (sample_rate, channels, bits_per_sample), bytes(header[payload_start:])
    return None, b""


def score_pcm16_audio(payload: bytes, sample_rate: int, channels: int) -> AudioScore:
    usable = len(payload) - (len(payload) % (2 * channels))
    if usable <= 0:
        return AudioScore(time.time(), 0.0, 0.0, 0.0, 0.0)
    samples = np.frombuffer(payload[:usable], dtype="<i2").astype(np.float32)
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    if samples.size < 128:
        rms = float(np.sqrt(np.mean(samples * samples)) / 32768.0)
        return AudioScore(time.time(), min(1.0, rms * 4.0), rms, 0.0, 0.0)

    samples = samples - float(np.mean(samples))
    rms = float(np.sqrt(np.mean(samples * samples)) / 32768.0)
    windowed = samples * np.hanning(samples.size)
    spectrum = np.abs(np.fft.rfft(windowed))
    freqs = np.fft.rfftfreq(samples.size, d=1.0 / sample_rate)
    total_energy = float(np.sum(spectrum) + 1e-9)
    band_mask = (freqs >= 80.0) & (freqs <= 1200.0)
    band_ratio = float(np.sum(spectrum[band_mask]) / total_energy)
    peak_hz = float(freqs[int(np.argmax(spectrum))])
    peak_bonus = 0.15 if 80.0 <= peak_hz <= 1200.0 else 0.0
    score = float(np.clip((rms * 3.5) + (band_ratio * 0.65) + peak_bonus, 0.0, 1.0))
    return AudioScore(time.time(), score, rms, peak_hz, band_ratio)


def normalize_u16(image: np.ndarray) -> np.ndarray:
    lo, hi = np.percentile(image, [2, 98])
    if hi <= lo:
        hi = lo + 1
    scaled = np.clip((image.astype(np.float32) - lo) * 255.0 / (hi - lo), 0, 255)
    return scaled.astype(np.uint8)


def detect_thermal_candidate(frame: ThermalFrame | None) -> tuple[float, tuple[int, int] | None, int]:
    if frame is None:
        return 0.0, None, 0
    image = normalize_u16(frame.image)
    threshold = np.percentile(image, 99.2)
    mask = (image >= threshold).astype(np.uint8)
    ys, xs = np.nonzero(mask)
    if len(xs) < 4:
        return 0.05, None, 0
    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())
    area = len(xs)
    box_area = max(1, (x1 - x0 + 1) * (y1 - y0 + 1))
    compactness = min(1.0, area / box_area)
    size_score = 1.0 - min(1.0, box_area / (image.shape[0] * image.shape[1] * 0.08))
    score = float(np.clip(0.2 + 0.5 * compactness + 0.3 * size_score, 0.0, 1.0))
    center = ((x0 + x1) // 2, (y0 + y1) // 2)
    radius = max(4, int(max(x1 - x0, y1 - y0) / 2))
    return score, center, radius


def estimate_global_shift(
    cv2: Any,
    previous_gray: np.ndarray,
    gray: np.ndarray,
    min_vectors: int = 12,
    consensus_px: float = 2.0,
) -> tuple[float, float, float, int]:
    scale = min(1.0, 640.0 / max(previous_gray.shape))
    if scale < 1.0:
        size = (int(previous_gray.shape[1] * scale), int(previous_gray.shape[0] * scale))
        previous_work = cv2.resize(previous_gray, size)
        gray_work = cv2.resize(gray, size)
    else:
        previous_work = previous_gray
        gray_work = gray

    points = cv2.goodFeaturesToTrack(
        previous_work,
        maxCorners=240,
        qualityLevel=0.01,
        minDistance=12,
        blockSize=7,
    )
    if points is None or len(points) < min_vectors:
        return 0.0, 0.0, 0.0, 0

    next_points, status, _err = cv2.calcOpticalFlowPyrLK(
        previous_work,
        gray_work,
        points,
        None,
        winSize=(21, 21),
        maxLevel=3,
        criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 20, 0.03),
    )
    if next_points is None or status is None:
        return 0.0, 0.0, 0.0, 0

    valid = status.reshape(-1) == 1
    if int(valid.sum()) < min_vectors:
        return 0.0, 0.0, 0.0, int(valid.sum())

    vectors = next_points.reshape(-1, 2)[valid] - points.reshape(-1, 2)[valid]
    median = np.median(vectors, axis=0)
    residuals = np.linalg.norm(vectors - median, axis=1)
    consensus = float(np.mean(residuals <= consensus_px))
    dx = float(median[0] / scale)
    dy = float(median[1] / scale)
    return dx, dy, consensus, int(vectors.shape[0])


def best_motion_candidate(
    cv2: Any,
    mask: np.ndarray,
    frame_area: int,
    min_area: float = 6.0,
    max_area_ratio: float = 0.08,
) -> tuple[float, tuple[int, int] | None, int, float]:
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best: tuple[float, tuple[int, int], int, float] | None = None
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < min_area or area > frame_area * max_area_ratio:
            continue
        x, y, w, h = cv2.boundingRect(contour)
        aspect = max(w, h) / max(1, min(w, h))
        if aspect > 6:
            continue
        size_score = 1.0 - min(1.0, area / (frame_area * 0.03))
        score = float(np.clip(0.25 + 0.75 * size_score, 0.0, 1.0))
        candidate = (score, (x + w // 2, y + h // 2), max(5, max(w, h) // 2), area)
        if best is None or candidate[0] > best[0]:
            best = candidate
    if best is None:
        return 0.05, None, 0, 0.0
    return best


def detect_rgb_motion_guarded(
    frame: np.ndarray | None,
    previous_gray: np.ndarray | None,
    min_area: float = 6.0,
    max_area_ratio: float = 0.08,
    threshold: int = 22,
    shake_protection: bool = True,
    shake_min_shift: float = 1.5,
    shake_consensus: float = 0.72,
    shake_consensus_px: float = 2.0,
    shake_residual_min_score: float = 0.75,
    shake_max_residual_area_ratio: float = 0.008,
) -> RgbMotionAnalysis:
    if frame is None:
        return RgbMotionAnalysis(0.0, None, 0, previous_gray)
    try:
        import cv2
    except ImportError:
        return RgbMotionAnalysis(0.0, None, 0, previous_gray)

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (7, 7), 0)
    if previous_gray is None:
        return RgbMotionAnalysis(0.0, None, 0, gray)

    compare_gray = previous_gray
    global_dx = 0.0
    global_dy = 0.0
    global_consensus = 0.0
    tracked_vectors = 0
    global_motion = False
    if shake_protection:
        global_dx, global_dy, global_consensus, tracked_vectors = estimate_global_shift(
            cv2,
            previous_gray,
            gray,
            consensus_px=shake_consensus_px,
        )
        global_shift = float(np.hypot(global_dx, global_dy))
        global_motion = global_shift >= shake_min_shift and global_consensus >= shake_consensus
        if global_motion:
            transform = np.array([[1.0, 0.0, global_dx], [0.0, 1.0, global_dy]], dtype=np.float32)
            compare_gray = cv2.warpAffine(
                previous_gray,
                transform,
                (gray.shape[1], gray.shape[0]),
                flags=cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_REPLICATE,
            )

    delta = cv2.absdiff(compare_gray, gray)
    _, mask = cv2.threshold(delta, threshold, 255, cv2.THRESH_BINARY)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    frame_area = frame.shape[0] * frame.shape[1]
    score, target_xy, radius, candidate_area = best_motion_candidate(
        cv2,
        mask,
        frame_area,
        min_area=min_area,
        max_area_ratio=max_area_ratio,
    )
    if global_motion and target_xy is None:
        score = 0.0
    elif global_motion and target_xy is not None:
        candidate_area_ratio = candidate_area / max(1, frame_area)
        if score < shake_residual_min_score or candidate_area_ratio > shake_max_residual_area_ratio:
            score = 0.0
            target_xy = None
            radius = 0
    return RgbMotionAnalysis(
        score,
        target_xy,
        radius,
        gray,
        candidate_area=candidate_area,
        global_motion=global_motion,
        global_dx=global_dx,
        global_dy=global_dy,
        global_consensus=global_consensus,
        tracked_vectors=tracked_vectors,
    )


def detect_rgb_motion(
    frame: np.ndarray | None,
    previous_gray: np.ndarray | None,
    min_area: float = 6.0,
    max_area_ratio: float = 0.08,
    threshold: int = 22,
) -> tuple[float, tuple[int, int] | None, int, np.ndarray | None]:
    result = detect_rgb_motion_guarded(
        frame,
        previous_gray,
        min_area=min_area,
        max_area_ratio=max_area_ratio,
        threshold=threshold,
        shake_protection=False,
    )
    return result.score, result.target_xy, result.target_radius, result.previous_gray


def fuse(
    rgb_score: float,
    thermal_score: float,
    audio_score: float,
    rgb_weight: float = 0.60,
    thermal_weight: float = 0.25,
    audio_weight: float = 0.15,
) -> float:
    total_weight = max(1e-6, rgb_weight + thermal_weight + audio_weight)
    return float(
        np.clip(
            (
                rgb_weight * rgb_score
                + thermal_weight * thermal_score
                + audio_weight * audio_score
            )
            / total_weight,
            0.0,
            1.0,
        )
    )


def rounded(value: float | None, places: int = 3) -> float | None:
    if value is None:
        return None
    return round(float(value), places)


def write_telemetry(
    handle: Any,
    frame_index: int,
    timestamp: float,
    fps: float,
    state: DetectionState,
    thermal_age: float | None,
    thermal_fresh: bool,
    audio_age: float | None,
    audio_fresh: bool,
    latest_audio: AudioScore | None,
    mount_command: Any | None,
    rgb_motion: RgbMotionAnalysis | None = None,
) -> None:
    target = None
    if state.target_xy is not None:
        target = {
            "x": int(state.target_xy[0]),
            "y": int(state.target_xy[1]),
            "radius": int(state.target_radius),
        }
    mount = None
    if mount_command is not None:
        mount = {
            "pan_speed": rounded(mount_command.pan_speed),
            "tilt_speed": rounded(mount_command.tilt_speed),
            "reason": mount_command.reason,
        }
    record = {
        "timestamp": rounded(timestamp, 6),
        "frame": frame_index,
        "fps": rounded(fps, 2),
        "status": state.status,
        "scores": {
            "rgb": rounded(state.rgb_score),
            "thermal": rounded(state.thermal_score),
            "audio": rounded(state.audio_score),
            "fused": rounded(state.fused_score),
        },
        "target": target,
        "thermal": {
            "age_s": rounded(thermal_age),
            "fresh": thermal_fresh,
        },
        "audio": {
            "age_s": rounded(audio_age),
            "fresh": audio_fresh,
            "rms": None if latest_audio is None else rounded(latest_audio.rms),
            "peak_hz": None if latest_audio is None else rounded(latest_audio.peak_hz, 1),
            "band_ratio": None if latest_audio is None else rounded(latest_audio.band_ratio),
        },
        "rgb_motion": None
        if rgb_motion is None
        else {
            "global_motion": rgb_motion.global_motion,
            "global_dx": rounded(rgb_motion.global_dx),
            "global_dy": rounded(rgb_motion.global_dy),
            "global_consensus": rounded(rgb_motion.global_consensus),
            "tracked_vectors": rgb_motion.tracked_vectors,
            "candidate_area": rounded(rgb_motion.candidate_area),
        },
        "mount": mount,
    }
    handle.write(json.dumps(record, separators=(",", ":")) + "\n")


def open_rgb_source(source: str | None) -> Any:
    if not source:
        return None
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("OpenCV is required for RGB input") from exc
    if source.isdigit():
        source_value: int | str = int(source)
    else:
        source_value = source
    capture = cv2.VideoCapture(source_value)
    capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    if not capture.isOpened():
        raise RuntimeError(f"Could not open RGB source: {source}")
    return capture


def make_demo_rgb(frame_index: int, width: int = 640, height: int = 360) -> np.ndarray:
    image = np.zeros((height, width, 3), dtype=np.uint8)
    image[:, :] = (18, 28, 34)
    x = 80 + (frame_index * 6) % (width - 160)
    y = height // 3 + int(30 * np.sin(frame_index / 18.0))
    image[y - 3 : y + 3, x - 12 : x + 12] = (220, 220, 220)
    image[y - 8 : y + 8, x - 2 : x + 2] = (180, 180, 180)
    return image


def make_dashboard(
    rgb: np.ndarray | None,
    thermal: ThermalFrame | None,
    state: DetectionState,
    no_window: bool,
) -> np.ndarray | None:
    try:
        import cv2
    except ImportError:
        return None
    if rgb is None:
        rgb_panel = make_demo_rgb(0)
    else:
        rgb_panel = rgb.copy()
        if rgb_panel.shape[1] != 640:
            scale = 640 / rgb_panel.shape[1]
            rgb_panel = cv2.resize(rgb_panel, (640, int(rgb_panel.shape[0] * scale)))

    if state.target_xy is not None and state.target_radius > 0:
        cv2.circle(rgb_panel, state.target_xy, state.target_radius, (0, 220, 255), 2)

    if thermal is None:
        thermal_panel = np.zeros((rgb_panel.shape[0], 360, 3), dtype=np.uint8)
        thermal_panel[:, :] = (24, 24, 24)
    else:
        thermal_u8 = normalize_u16(thermal.image)
        thermal_panel = cv2.applyColorMap(thermal_u8, cv2.COLORMAP_INFERNO)
        thermal_panel = cv2.resize(thermal_panel, (360, rgb_panel.shape[0]))

    dashboard = np.hstack([rgb_panel, thermal_panel])
    lines = [
        f"fused {state.fused_score:.2f}",
        f"rgb {state.rgb_score:.2f}",
        f"thermal {state.thermal_score:.2f}",
        f"audio {state.audio_score:.2f}",
        state.status,
    ]
    for index, line in enumerate(lines):
        cv2.putText(
            dashboard,
            line,
            (14, 28 + index * 24),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
    if not no_window:
        cv2.imshow("Counter-UAS fusion node", dashboard)
        cv2.waitKey(1)
    return dashboard


def run(args: argparse.Namespace) -> None:
    thermal_queue: "queue.Queue[ThermalFrame]" = queue.Queue(maxsize=1)
    audio_queue: "queue.Queue[AudioScore]" = queue.Queue(maxsize=1)
    stop_event = threading.Event()
    thermal_thread = ThermalUdpThread(
        args.thermal_host,
        args.thermal_port,
        args.thermal_width,
        args.thermal_height,
        thermal_queue,
        stop_event,
    )
    thermal_thread.start()
    audio_thread: threading.Thread | None = None
    if args.audio_wav_url:
        audio_thread = AudioWavThread(
            args.audio_wav_url,
            audio_queue,
            stop_event,
            chunk_bytes=args.audio_chunk_bytes,
        )
    elif args.audio_demo:
        audio_thread = DemoAudioThread(audio_queue, stop_event)
    if audio_thread is not None:
        audio_thread.start()

    capture = open_rgb_source(args.rgb_source) if args.rgb_source else None
    mount_controller = PixelTrackerController(deadband_px=args.mount_deadband_px)
    mount_client = None
    if args.mount_udp_host:
        mount_client = UdpMountClient(args.mount_udp_host, args.mount_udp_port)
    elif args.mount_serial_port:
        mount_client = SerialMountClient(args.mount_serial_port, args.mount_serial_baud)
    previous_gray: np.ndarray | None = None
    latest_thermal: ThermalFrame | None = None
    latest_audio: AudioScore | None = None
    frame_index = 0
    started_at = time.time()
    save_dir = Path(args.save_dir) if args.save_dir else None
    if save_dir:
        save_dir.mkdir(parents=True, exist_ok=True)
    telemetry_file = None
    if args.telemetry_jsonl:
        telemetry_path = Path(args.telemetry_jsonl)
        telemetry_path.parent.mkdir(parents=True, exist_ok=True)
        telemetry_file = telemetry_path.open("a", encoding="utf-8", buffering=1)

    try:
        while True:
            if args.max_frames > 0 and frame_index >= args.max_frames:
                break
            if capture is None:
                rgb = make_demo_rgb(frame_index) if args.demo else None
                time.sleep(1.0 / max(1.0, args.demo_fps))
            else:
                ok, rgb = capture.read()
                if not ok:
                    rgb = None
                    time.sleep(0.05)

            latest_thermal = read_latest(thermal_queue, latest_thermal)
            latest_audio = read_latest(audio_queue, latest_audio)
            now = time.time()
            thermal_age = None if latest_thermal is None else now - latest_thermal.timestamp
            thermal_fresh = (
                latest_thermal is not None
                and thermal_age is not None
                and thermal_age <= args.thermal_stale_seconds
            )
            if thermal_fresh:
                thermal_score, thermal_xy, thermal_radius = detect_thermal_candidate(latest_thermal)
            else:
                thermal_score, thermal_xy, thermal_radius = 0.0, None, 0

            rgb_motion = detect_rgb_motion_guarded(
                rgb,
                previous_gray,
                min_area=args.rgb_motion_min_area,
                max_area_ratio=args.rgb_motion_max_area_ratio,
                threshold=args.rgb_motion_threshold,
                shake_protection=not args.disable_rgb_shake_protection,
                shake_min_shift=args.rgb_shake_min_shift,
                shake_consensus=args.rgb_shake_consensus,
                shake_consensus_px=args.rgb_shake_consensus_px,
                shake_residual_min_score=args.rgb_shake_residual_min_score,
                shake_max_residual_area_ratio=args.rgb_shake_max_residual_area_ratio,
            )
            rgb_score = rgb_motion.score
            rgb_xy = rgb_motion.target_xy
            rgb_radius = rgb_motion.target_radius
            previous_gray = rgb_motion.previous_gray
            audio_age = None if latest_audio is None else now - latest_audio.timestamp
            audio_fresh = (
                latest_audio is not None
                and audio_age is not None
                and audio_age <= args.audio_stale_seconds
            )
            audio_score = latest_audio.score if audio_fresh and latest_audio is not None else 0.0
            fused_score = fuse(
                rgb_score,
                thermal_score,
                audio_score,
                rgb_weight=args.fusion_rgb_weight,
                thermal_weight=args.fusion_thermal_weight,
                audio_weight=args.fusion_audio_weight,
            )
            target_xy = rgb_xy
            target_radius = rgb_radius
            if target_xy is None and thermal_xy is not None and rgb is None:
                target_xy = thermal_xy
                target_radius = thermal_radius
            base_status = "confirmed" if fused_score >= args.alert_threshold else "watching"
            live_sources = []
            if rgb is not None:
                live_sources.append("rgb")
            if thermal_fresh:
                live_sources.append("thermal")
            if audio_fresh:
                live_sources.append("audio")
            shake_note = " shake-filtered" if rgb_motion.global_motion else ""
            status = f"{base_status} {'/'.join(live_sources) if live_sources else 'no-source'}{shake_note}"
            state = DetectionState(
                rgb_score=rgb_score,
                thermal_score=thermal_score,
                audio_score=audio_score,
                fused_score=fused_score,
                target_xy=target_xy,
                target_radius=target_radius,
                status=status,
            )
            mount_command = None
            if mount_client is not None:
                if rgb is not None:
                    frame_h, frame_w = rgb.shape[:2]
                    mount_target = rgb_xy
                elif thermal_xy is not None:
                    frame_w, frame_h = args.thermal_width, args.thermal_height
                    mount_target = thermal_xy
                else:
                    frame_w, frame_h = 640, 360
                    mount_target = None
                mount_command = mount_controller.update(frame_w, frame_h, mount_target)
                mount_client.send(mount_command)
                if frame_index % 30 == 0:
                    print("mount", mount_command.as_line(), end="")
            dashboard = make_dashboard(
                rgb,
                latest_thermal if thermal_fresh else None,
                state,
                args.no_window,
            )
            fps = (frame_index + 1) / max(0.001, time.time() - started_at)
            if frame_index % 30 == 0:
                audio_text = (
                    "none"
                    if latest_audio is None
                    else (
                        f"score={latest_audio.score:.2f} rms={latest_audio.rms:.3f} "
                        f"peak={latest_audio.peak_hz:.0f}Hz age={audio_age:.2f}s"
                    )
                )
                print(
                    f"frame={frame_index} fps={fps:.1f} fused={fused_score:.2f} "
                    f"rgb={rgb_score:.2f} thermal={thermal_score:.2f} "
                    f"shake={rgb_motion.global_motion} "
                    f"shift=({rgb_motion.global_dx:.1f},{rgb_motion.global_dy:.1f}) "
                    f"consensus={rgb_motion.global_consensus:.2f} "
                    f"thermal_age={thermal_age} thermal_fresh={thermal_fresh} audio={audio_text}"
                )
            if (
                telemetry_file is not None
                and frame_index % max(1, args.telemetry_every) == 0
            ):
                write_telemetry(
                    telemetry_file,
                    frame_index,
                    now,
                    fps,
                    state,
                    thermal_age,
                    thermal_fresh,
                    audio_age,
                    audio_fresh,
                    latest_audio,
                    mount_command,
                    rgb_motion,
                )
            if save_dir and dashboard is not None and frame_index % max(1, args.save_every) == 0:
                try:
                    import cv2

                    cv2.imwrite(str(save_dir / f"dashboard_{frame_index:06d}.jpg"), dashboard)
                except ImportError:
                    pass
            frame_index += 1
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        if capture is not None:
            capture.release()
        if telemetry_file is not None:
            telemetry_file.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rgb-source", help="Camera index, video file, RTSP, HTTP MJPEG, etc.")
    parser.add_argument("--thermal-host", default="0.0.0.0")
    parser.add_argument("--thermal-port", type=int, default=25000)
    parser.add_argument("--thermal-width", type=int, default=256)
    parser.add_argument("--thermal-height", type=int, default=192)
    parser.add_argument("--thermal-stale-seconds", type=float, default=2.0)
    parser.add_argument("--audio-wav-url", help="Streaming WAV URL, for example IP Webcam /audio.wav")
    parser.add_argument("--audio-chunk-bytes", type=int, default=8192)
    parser.add_argument("--audio-stale-seconds", type=float, default=2.0)
    parser.add_argument("--audio-demo", action="store_true", help="Use synthetic drone-like audio score.")
    parser.add_argument("--rgb-motion-min-area", type=float, default=6.0)
    parser.add_argument("--rgb-motion-max-area-ratio", type=float, default=0.08)
    parser.add_argument("--rgb-motion-threshold", type=int, default=22)
    parser.add_argument("--disable-rgb-shake-protection", action="store_true")
    parser.add_argument("--rgb-shake-min-shift", type=float, default=1.5)
    parser.add_argument("--rgb-shake-consensus", type=float, default=0.72)
    parser.add_argument("--rgb-shake-consensus-px", type=float, default=2.0)
    parser.add_argument("--rgb-shake-residual-min-score", type=float, default=0.75)
    parser.add_argument("--rgb-shake-max-residual-area-ratio", type=float, default=0.008)
    parser.add_argument("--fusion-rgb-weight", type=float, default=0.60)
    parser.add_argument("--fusion-thermal-weight", type=float, default=0.25)
    parser.add_argument("--fusion-audio-weight", type=float, default=0.15)
    parser.add_argument("--alert-threshold", type=float, default=0.55)
    parser.add_argument("--demo", action="store_true", help="Use synthetic RGB when no source is provided.")
    parser.add_argument("--demo-fps", type=float, default=15.0)
    parser.add_argument("--no-window", action="store_true")
    parser.add_argument("--save-dir")
    parser.add_argument("--save-every", type=int, default=60)
    parser.add_argument("--telemetry-jsonl")
    parser.add_argument("--telemetry-every", type=int, default=1)
    parser.add_argument("--max-frames", type=int, default=0)
    parser.add_argument("--mount-udp-host")
    parser.add_argument("--mount-udp-port", type=int, default=26000)
    parser.add_argument("--mount-serial-port")
    parser.add_argument("--mount-serial-baud", type=int, default=115200)
    parser.add_argument("--mount-deadband-px", type=int, default=32)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
