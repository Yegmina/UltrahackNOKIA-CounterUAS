"""Jetson/laptop Counter-UAS fusion prototype.

This is intentionally modest: it gives the hackathon system one runnable place
to combine RGB, thermal UDP frames, and later audio/model outputs. The detector
logic is heuristic until a real drone model is dropped in.
"""

from __future__ import annotations

import argparse
import queue
import socket
import threading
import time
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
class DetectionState:
    rgb_score: float = 0.0
    thermal_score: float = 0.0
    audio_score: float = 0.0
    fused_score: float = 0.0
    target_xy: tuple[int, int] | None = None
    target_radius: int = 0
    status: str = "idle"


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


def detect_rgb_motion(
    frame: np.ndarray | None, previous_gray: np.ndarray | None
) -> tuple[float, tuple[int, int] | None, int, np.ndarray | None]:
    if frame is None:
        return 0.0, None, 0, previous_gray
    try:
        import cv2
    except ImportError:
        return 0.0, None, 0, previous_gray

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (7, 7), 0)
    if previous_gray is None:
        return 0.0, None, 0, gray

    delta = cv2.absdiff(previous_gray, gray)
    _, mask = cv2.threshold(delta, 22, 255, cv2.THRESH_BINARY)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best: tuple[float, tuple[int, int], int] | None = None
    frame_area = frame.shape[0] * frame.shape[1]
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < 6 or area > frame_area * 0.08:
            continue
        x, y, w, h = cv2.boundingRect(contour)
        aspect = max(w, h) / max(1, min(w, h))
        if aspect > 6:
            continue
        size_score = 1.0 - min(1.0, area / (frame_area * 0.03))
        score = float(np.clip(0.25 + 0.75 * size_score, 0.0, 1.0))
        candidate = (score, (x + w // 2, y + h // 2), max(5, max(w, h) // 2))
        if best is None or candidate[0] > best[0]:
            best = candidate
    if best is None:
        return 0.05, None, 0, gray
    return best[0], best[1], best[2], gray


def fuse(rgb_score: float, thermal_score: float, audio_score: float) -> float:
    return float(
        np.clip(
            0.60 * rgb_score + 0.25 * thermal_score + 0.15 * audio_score,
            0.0,
            1.0,
        )
    )


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

    capture = open_rgb_source(args.rgb_source) if args.rgb_source else None
    mount_controller = PixelTrackerController(deadband_px=args.mount_deadband_px)
    mount_client = None
    if args.mount_udp_host:
        mount_client = UdpMountClient(args.mount_udp_host, args.mount_udp_port)
    elif args.mount_serial_port:
        mount_client = SerialMountClient(args.mount_serial_port, args.mount_serial_baud)
    previous_gray: np.ndarray | None = None
    latest_thermal: ThermalFrame | None = None
    frame_index = 0
    save_dir = Path(args.save_dir) if args.save_dir else None
    if save_dir:
        save_dir.mkdir(parents=True, exist_ok=True)

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
            thermal_score, thermal_xy, thermal_radius = detect_thermal_candidate(latest_thermal)
            rgb_score, rgb_xy, rgb_radius, previous_gray = detect_rgb_motion(rgb, previous_gray)
            audio_score = 0.0
            fused_score = fuse(rgb_score, thermal_score, audio_score)
            target_xy = rgb_xy
            target_radius = rgb_radius
            if target_xy is None and thermal_xy is not None:
                target_xy = thermal_xy
                target_radius = thermal_radius
            status = "confirmed" if fused_score >= args.alert_threshold else "watching"
            state = DetectionState(
                rgb_score=rgb_score,
                thermal_score=thermal_score,
                audio_score=audio_score,
                fused_score=fused_score,
                target_xy=target_xy,
                target_radius=target_radius,
                status=status,
            )
            if mount_client is not None:
                frame_h, frame_w = (rgb.shape[:2] if rgb is not None else (360, 640))
                mount_command = mount_controller.update(frame_w, frame_h, rgb_xy)
                mount_client.send(mount_command)
                if frame_index % 30 == 0:
                    print("mount", mount_command.as_line(), end="")
            dashboard = make_dashboard(rgb, latest_thermal, state, args.no_window)
            if frame_index % 30 == 0:
                thermal_age = None if latest_thermal is None else time.time() - latest_thermal.timestamp
                print(
                    f"frame={frame_index} fused={fused_score:.2f} "
                    f"rgb={rgb_score:.2f} thermal={thermal_score:.2f} "
                    f"thermal_age={thermal_age}"
                )
            if save_dir and dashboard is not None and frame_index % args.save_every == 0:
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rgb-source", help="Camera index, video file, RTSP, HTTP MJPEG, etc.")
    parser.add_argument("--thermal-host", default="0.0.0.0")
    parser.add_argument("--thermal-port", type=int, default=25000)
    parser.add_argument("--thermal-width", type=int, default=256)
    parser.add_argument("--thermal-height", type=int, default=192)
    parser.add_argument("--alert-threshold", type=float, default=0.55)
    parser.add_argument("--demo", action="store_true", help="Use synthetic RGB when no source is provided.")
    parser.add_argument("--demo-fps", type=float, default=15.0)
    parser.add_argument("--no-window", action="store_true")
    parser.add_argument("--save-dir")
    parser.add_argument("--save-every", type=int, default=60)
    parser.add_argument("--max-frames", type=int, default=0)
    parser.add_argument("--mount-udp-host")
    parser.add_argument("--mount-udp-port", type=int, default=26000)
    parser.add_argument("--mount-serial-port")
    parser.add_argument("--mount-serial-baud", type=int, default=115200)
    parser.add_argument("--mount-deadband-px", type=int, default=32)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
