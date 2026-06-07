#!/usr/bin/env python3
"""
Laptop-side live viewer for ThermoVue raw sensor packets.

This is intentionally separate from thermal_stream_test.py, which screen-captures
the ThermoVue UI. This script expects true ThermoVue packet bytes from a future
Android bridge/hook and visualizes the IR and temperature planes on the laptop.

Packet layout based on ThermoVue reverse-engineering:
  IR plane          256 x 192 x uint16 =    98,304 bytes
  Info lines        256 x   2 x uint16 =     1,024 bytes
  Temperature plane 256 x 192 x uint16 =    98,304 bytes
  Visible frame    1440 x 1080 x RGB   = 4,665,600 bytes
  Total                                      4,863,232 bytes
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import queue
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
from typing import Iterable, Iterator

import numpy as np
from PIL import Image, ImageDraw, ImageTk
import tkinter as tk


IR_W = 256
IR_H = 192
INFO_LINES = 2
VL_W = 1440
VL_H = 1080

IR_BYTES = IR_W * IR_H * 2
INFO_BYTES = IR_W * INFO_LINES * 2
TEMP_BYTES = IR_W * IR_H * 2
VL_BYTES = VL_W * VL_H * 3
PACKET_BYTES = IR_BYTES + INFO_BYTES + TEMP_BYTES + VL_BYTES

DEFAULT_THERMOVUE_ACTIVITY = "com.energy.tc2c/com.energy.usbCamera.ui.splash.SplashActivity"


PALETTES: dict[str, np.ndarray] = {
    "gray": np.array(
        [
            [0, 0, 0],
            [255, 255, 255],
        ],
        dtype=np.uint8,
    ),
    "ironbow": np.array(
        [
            [0, 0, 0],
            [25, 0, 80],
            [90, 0, 130],
            [160, 35, 90],
            [215, 80, 35],
            [255, 170, 30],
            [255, 245, 170],
            [255, 255, 255],
        ],
        dtype=np.uint8,
    ),
    "inferno": np.array(
        [
            [0, 0, 4],
            [31, 12, 72],
            [85, 15, 109],
            [136, 34, 106],
            [186, 54, 85],
            [227, 89, 51],
            [249, 140, 10],
            [249, 201, 50],
            [252, 255, 164],
        ],
        dtype=np.uint8,
    ),
}


@dataclass(frozen=True)
class ThermoVuePacket:
    ir: np.ndarray
    info: bytes
    temp: np.ndarray
    visible: np.ndarray | None


@dataclass
class FrameStats:
    frame_index: int
    fps: float
    temp_min: float
    temp_max: float
    temp_center: float
    temp_mean: float


def common_adb_paths() -> list[Path]:
    paths: list[Path] = []
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        paths.append(Path(local_app_data) / "Android" / "Sdk" / "platform-tools" / "adb.exe")
    program_files = os.environ.get("ProgramFiles")
    if program_files:
        paths.append(Path(program_files) / "Android" / "android-sdk" / "platform-tools" / "adb.exe")
    return paths


def find_adb(explicit_path: str | None) -> str:
    if explicit_path:
        candidate = Path(explicit_path).expanduser()
        if candidate.exists():
            return str(candidate)
        raise SystemExit(f"ADB path does not exist: {candidate}")

    path_adb = shutil.which("adb")
    if path_adb:
        return path_adb

    for candidate in common_adb_paths():
        if candidate.exists():
            return str(candidate)

    raise SystemExit("Could not find adb. Pass --adb C:\\path\\to\\adb.exe")


def run_adb(
    adb: str,
    args: Iterable[str],
    *,
    serial: str | None = None,
    timeout: float = 20,
) -> subprocess.CompletedProcess[str]:
    cmd = [adb]
    if serial:
        cmd += ["-s", serial]
    cmd += list(args)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def select_adb_device(adb: str, serial: str | None) -> str | None:
    result = run_adb(adb, ["devices", "-l"], timeout=15)
    devices: list[tuple[str, str]] = []
    for line in result.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            devices.append((parts[0], parts[1]))

    if serial:
        for found_serial, state in devices:
            if found_serial == serial:
                if state != "device":
                    raise SystemExit(f"ADB device {serial} is listed as {state}, not device.")
                return found_serial
        raise SystemExit(f"ADB device {serial} was not listed.\n\n{result.stdout}")

    ready = [found_serial for found_serial, state in devices if state == "device"]
    if len(ready) == 1:
        return ready[0]
    if len(ready) > 1:
        raise SystemExit("More than one ADB device is connected. Pass --serial SERIAL.")
    return None


def adb_shell(adb: str, serial: str, command: str, timeout: float = 20) -> str:
    result = run_adb(adb, ["shell", command], serial=serial, timeout=timeout)
    return (result.stdout or "") + (result.stderr or "")


def launch_thermovue(adb: str, serial: str, activity: str = DEFAULT_THERMOVUE_ACTIVITY) -> None:
    result = run_adb(
        adb,
        ["shell", "am", "start", "-n", activity],
        serial=serial,
        timeout=20,
    )
    if result.returncode != 0:
        raise SystemExit(result.stderr.strip() or result.stdout.strip() or "Failed to launch ThermoVue.")
    print(result.stdout.strip())


def check_phone(adb_path: str | None, serial: str | None, launch: bool) -> int:
    adb = find_adb(adb_path)
    selected = select_adb_device(adb, serial)
    if not selected:
        print("No authorized ADB device found.")
        return 1

    print(f"ADB device: {selected}")
    print("Model:", adb_shell(adb, selected, "getprop ro.product.model").strip())
    print("Android SDK:", adb_shell(adb, selected, "getprop ro.build.version.sdk").strip())

    if launch:
        print("\nLaunching ThermoVue to power/mux the internal thermal module...")
        launch_thermovue(adb, selected)
        time.sleep(5)

    print("\nThermoVue package:")
    pkg = adb_shell(
        adb,
        selected,
        "dumpsys package com.energy.tc2c | grep -E 'codePath|versionName|versionCode|READ_PRIVILEGED|CAMERA|MANAGE_EXTERNAL_STORAGE' | head -80",
    ).strip()
    print(pkg or "Package not found or grep unavailable on phone.")

    print("\nUSB host manager thermal device hint:")
    usb = adb_shell(
        adb,
        selected,
        "dumpsys usb",
        timeout=30,
    )
    hints = thermal_usb_hints(usb)
    if hints:
        for line in hints:
            print(line)
    else:
        print("No ThermoVue internal USB camera visible. Launch ThermoVue and try again.")

    print("\nRaw stream status:")
    print(
        "This laptop script cannot pull raw packets directly over ADB. It needs a phone-side "
        "bridge/hook that forwards IIrFrameCallback packet bytes to TCP."
    )
    return 0


def thermal_usb_hints(usb_dump: str) -> list[str]:
    wanted = (
        "thermal cam",
        "vendor_id=13428",
        "product_id=17185",
        "manufacturer_name",
        "product_name=camera",
        "serial_number=202206223",
        "device_address=/dev/bus/usb",
        "name=/dev/bus/usb",
    )
    hints: list[str] = []
    seen: set[str] = set()
    for line in usb_dump.splitlines():
        compact = line.strip()
        lower = compact.lower()
        if not compact or "null" in lower:
            continue
        if not any(needle in lower for needle in wanted):
            continue
        if compact in seen:
            continue
        hints.append(line)
        seen.add(compact)
    return hints[:16]


def read_exact(stream, size: int) -> bytes:
    chunks = []
    remaining = size
    while remaining:
        chunk = stream.recv(remaining) if isinstance(stream, socket.socket) else stream.read(remaining)
        if not chunk:
            raise EOFError("stream ended")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def parse_packet(data: bytes, *, include_visible: bool = False) -> ThermoVuePacket:
    if len(data) != PACKET_BYTES:
        raise ValueError(f"Expected {PACKET_BYTES:,} bytes, got {len(data):,}.")

    offset = 0
    ir = np.frombuffer(data[offset : offset + IR_BYTES], dtype="<u2").reshape(IR_H, IR_W)
    offset += IR_BYTES

    info = data[offset : offset + INFO_BYTES]
    offset += INFO_BYTES

    temp = np.frombuffer(data[offset : offset + TEMP_BYTES], dtype="<u2").reshape(IR_H, IR_W)
    offset += TEMP_BYTES

    visible = None
    if include_visible:
        visible = np.frombuffer(data[offset : offset + VL_BYTES], dtype=np.uint8).reshape(VL_H, VL_W, 3)

    return ThermoVuePacket(ir=ir.copy(), info=info, temp=temp.copy(), visible=visible.copy() if visible is not None else None)


def fixed_packet_reader(sock: socket.socket) -> Iterator[bytes]:
    while True:
        yield read_exact(sock, PACKET_BYTES)


def u32le_packet_reader(sock: socket.socket) -> Iterator[bytes]:
    while True:
        raw_len = read_exact(sock, 4)
        (packet_len,) = struct.unpack("<I", raw_len)
        if packet_len != PACKET_BYTES:
            raise ValueError(f"Bridge sent packet length {packet_len:,}; expected {PACKET_BYTES:,}.")
        yield read_exact(sock, packet_len)


def tcp_listen_source(bind: str, port: int, protocol: str) -> Iterator[bytes]:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((bind, port))
        server.listen(1)
        print(f"Waiting for phone bridge on {bind}:{port} ({protocol}, {PACKET_BYTES:,} bytes/frame)...")
        conn, addr = server.accept()
        with conn:
            print(f"Connected: {addr[0]}:{addr[1]}")
            yield from packet_reader(conn, protocol)


def tcp_connect_source(host: str, port: int, protocol: str) -> Iterator[bytes]:
    with socket.create_connection((host, port), timeout=20) as conn:
        print(f"Connected to {host}:{port} ({protocol}, {PACKET_BYTES:,} bytes/frame).")
        yield from packet_reader(conn, protocol)


def packet_reader(sock: socket.socket, protocol: str) -> Iterator[bytes]:
    if protocol == "fixed":
        yield from fixed_packet_reader(sock)
    elif protocol == "u32le":
        yield from u32le_packet_reader(sock)
    else:
        raise ValueError(f"Unknown protocol: {protocol}")


def file_source(path: Path, loop: bool) -> Iterator[bytes]:
    while True:
        with path.open("rb") as stream:
            while True:
                chunk = stream.read(PACKET_BYTES)
                if not chunk:
                    break
                if len(chunk) != PACKET_BYTES:
                    raise EOFError(f"Trailing partial packet in {path}: {len(chunk):,} bytes.")
                yield chunk
        if not loop:
            return


def synthetic_packet_source(fps: float) -> Iterator[bytes]:
    frame = 0
    interval = 1.0 / max(fps, 0.1)
    x_grid, y_grid = np.meshgrid(np.arange(IR_W), np.arange(IR_H))
    visible = np.zeros((VL_H, VL_W, 3), dtype=np.uint8)
    visible[..., 0] = np.linspace(20, 100, VL_W, dtype=np.uint8)
    visible[..., 1] = np.linspace(30, 160, VL_H, dtype=np.uint8)[:, None]
    visible[..., 2] = 80

    while True:
        start = time.perf_counter()
        cx = IR_W / 2 + np.sin(frame / 18.0) * 70
        cy = IR_H / 2 + np.cos(frame / 23.0) * 45
        radius = 18 + 5 * np.sin(frame / 9.0)
        dist2 = (x_grid - cx) ** 2 + (y_grid - cy) ** 2

        background = 7000 + 400 * np.sin(x_grid / 27.0) + 250 * np.cos(y_grid / 19.0)
        hot = 9000 * np.exp(-dist2 / (2 * radius * radius))
        temp = np.clip(background + hot, 0, 65535).astype("<u2")
        ir = np.clip(3500 + hot * 0.75 + 150 * np.random.default_rng(frame).normal(size=(IR_H, IR_W)), 0, 65535).astype("<u2")
        info = np.zeros((INFO_LINES, IR_W), dtype="<u2")
        info[0, 0] = frame % 65535
        info[0, 1] = int(time.time()) % 65535

        packet = ir.tobytes() + info.tobytes() + temp.tobytes() + visible.tobytes()
        yield packet

        frame += 1
        elapsed = time.perf_counter() - start
        time.sleep(max(0.0, interval - elapsed))


def normalize_plane(plane: np.ndarray, low_percentile: float, high_percentile: float) -> np.ndarray:
    data = plane.astype(np.float32)
    lo = float(np.percentile(data, low_percentile))
    hi = float(np.percentile(data, high_percentile))
    if hi <= lo:
        lo = float(data.min())
        hi = float(data.max())
    if hi <= lo:
        return np.zeros(data.shape, dtype=np.float32)
    return np.clip((data - lo) / (hi - lo), 0.0, 1.0)


def apply_palette(values01: np.ndarray, palette_name: str) -> np.ndarray:
    palette = PALETTES[palette_name]
    scaled = values01 * (len(palette) - 1)
    low = np.floor(scaled).astype(np.int32)
    high = np.clip(low + 1, 0, len(palette) - 1)
    frac = (scaled - low)[..., None]
    rgb = palette[low] * (1.0 - frac) + palette[high] * frac
    return np.clip(rgb, 0, 255).astype(np.uint8)


def make_panel(
    plane: np.ndarray,
    *,
    title: str,
    palette: str,
    scale: int,
    low_percentile: float,
    high_percentile: float,
) -> Image.Image:
    rgb = apply_palette(normalize_plane(plane, low_percentile, high_percentile), palette)
    image = Image.fromarray(rgb, "RGB").resize((IR_W * scale, IR_H * scale), Image.Resampling.NEAREST)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, image.width, 20), fill=(0, 0, 0))
    draw.text((6, 4), title, fill=(255, 255, 255))
    return image


def visible_panel(visible: np.ndarray, *, max_width: int, max_height: int) -> Image.Image:
    image = Image.fromarray(visible, "RGB")
    image.thumbnail((max_width, max_height), Image.Resampling.BILINEAR)
    return image


def temp_value(raw: float, divisor: float, offset: float) -> float:
    return raw / divisor + offset


def stats_for(packet: ThermoVuePacket, frame_index: int, fps: float, divisor: float, offset: float) -> FrameStats:
    temp = packet.temp
    center = temp[temp.shape[0] // 2, temp.shape[1] // 2]
    return FrameStats(
        frame_index=frame_index,
        fps=fps,
        temp_min=temp_value(float(temp.min()), divisor, offset),
        temp_max=temp_value(float(temp.max()), divisor, offset),
        temp_center=temp_value(float(center), divisor, offset),
        temp_mean=temp_value(float(temp.mean()), divisor, offset),
    )


class LiveViewer:
    def __init__(self, args: argparse.Namespace, source: Iterator[bytes]) -> None:
        self.args = args
        self.source = source
        self.queue: queue.Queue[tuple[ThermoVuePacket, FrameStats] | Exception] = queue.Queue(maxsize=2)
        self.stop_event = threading.Event()
        self.frame_index = 0
        self.last_stat_time = time.perf_counter()
        self.last_frame_for_fps = 0

        self.root = tk.Tk()
        self.root.title("ThermoVue Sensor Live Viewer")
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind("<Escape>", lambda _event: self.close())
        self.root.bind("q", lambda _event: self.close())

        self.image_label = tk.Label(self.root, bg="black")
        self.image_label.pack(padx=8, pady=8)
        self.status = tk.StringVar(value="Waiting for frames...")
        tk.Label(self.root, textvariable=self.status, anchor="w", justify="left").pack(fill="x", padx=8, pady=(0, 8))
        self.photo: ImageTk.PhotoImage | None = None

        self.thread = threading.Thread(target=self.worker, daemon=True)
        self.thread.start()

    def worker(self) -> None:
        try:
            for data in self.source:
                if self.stop_event.is_set():
                    return
                packet = parse_packet(data, include_visible=self.args.show_visible)
                self.frame_index += 1
                now = time.perf_counter()
                elapsed = now - self.last_stat_time
                if elapsed >= 0.5:
                    fps = (self.frame_index - self.last_frame_for_fps) / elapsed
                    self.last_stat_time = now
                    self.last_frame_for_fps = self.frame_index
                else:
                    fps = 0.0
                stats = stats_for(packet, self.frame_index, fps, self.args.temp_divisor, self.args.temp_offset)
                self.put_latest((packet, stats))
        except Exception as exc:  # noqa: BLE001 - display failures in UI
            self.put_latest(exc)

    def put_latest(self, item: tuple[ThermoVuePacket, FrameStats] | Exception) -> None:
        while True:
            try:
                self.queue.put_nowait(item)
                return
            except queue.Full:
                try:
                    self.queue.get_nowait()
                except queue.Empty:
                    pass

    def update(self) -> None:
        try:
            item = self.queue.get_nowait()
        except queue.Empty:
            if not self.stop_event.is_set():
                self.root.after(20, self.update)
            return

        if isinstance(item, Exception):
            self.status.set(f"Stream stopped: {item}")
            return

        packet, stats = item
        scale = self.args.scale
        temp_image = make_panel(
            packet.temp,
            title="Temperature plane",
            palette=self.args.palette,
            scale=scale,
            low_percentile=self.args.low_percentile,
            high_percentile=self.args.high_percentile,
        )
        ir_image = make_panel(
            packet.ir,
            title="IR plane",
            palette=self.args.ir_palette,
            scale=scale,
            low_percentile=self.args.low_percentile,
            high_percentile=self.args.high_percentile,
        )

        panels = [temp_image, ir_image]
        if self.args.show_visible and packet.visible is not None:
            panels.append(visible_panel(packet.visible, max_width=temp_image.width * 2, max_height=temp_image.height))

        combined = Image.new("RGB", (sum(panel.width for panel in panels), max(panel.height for panel in panels)), "black")
        x = 0
        for panel in panels:
            combined.paste(panel, (x, 0))
            x += panel.width

        self.photo = ImageTk.PhotoImage(combined)
        self.image_label.configure(image=self.photo)
        self.status.set(
            f"frame={stats.frame_index}  fps={stats.fps:.1f}  "
            f"temp min/mean/center/max="
            f"{stats.temp_min:.1f}/{stats.temp_mean:.1f}/{stats.temp_center:.1f}/{stats.temp_max:.1f} "
            f"units  packet={PACKET_BYTES:,} bytes"
        )

        if not self.stop_event.is_set():
            self.root.after(10, self.update)

    def close(self) -> None:
        self.stop_event.set()
        self.root.destroy()

    def run(self) -> int:
        self.root.after(20, self.update)
        self.root.mainloop()
        return 0


def build_source(args: argparse.Namespace) -> Iterator[bytes]:
    if args.source == "demo":
        return synthetic_packet_source(args.demo_fps)
    if args.source == "file":
        if not args.packet_file:
            raise SystemExit("--packet-file is required with --source file")
        return file_source(Path(args.packet_file), args.loop)
    if args.source == "tcp-listen":
        return tcp_listen_source(args.bind, args.port, args.protocol)
    if args.source == "tcp-connect":
        return tcp_connect_source(args.host, args.port, args.protocol)
    raise SystemExit(f"Unknown source: {args.source}")


def run_headless(args: argparse.Namespace, source: Iterator[bytes]) -> int:
    started = time.perf_counter()
    for index in range(1, args.frames + 1):
        packet = parse_packet(next(source), include_visible=args.show_visible)
        elapsed = max(time.perf_counter() - started, 1e-6)
        stats = stats_for(packet, index, index / elapsed, args.temp_divisor, args.temp_offset)
        print(
            f"frame={stats.frame_index} fps={stats.fps:.1f} "
            f"temp_min={stats.temp_min:.1f} temp_mean={stats.temp_mean:.1f} "
            f"temp_center={stats.temp_center:.1f} temp_max={stats.temp_max:.1f} "
            f"ir_min={int(packet.ir.min())} ir_max={int(packet.ir.max())} info0={packet.info[:8].hex()}"
        )
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        choices=("tcp-listen", "tcp-connect", "file", "demo"),
        default="tcp-listen",
        help="Where raw ThermoVue packets come from.",
    )
    parser.add_argument("--bind", default="0.0.0.0", help="Bind address for --source tcp-listen.")
    parser.add_argument("--host", default="127.0.0.1", help="Host for --source tcp-connect.")
    parser.add_argument("--port", type=int, default=7777, help="TCP port for raw packet bridge.")
    parser.add_argument(
        "--protocol",
        choices=("fixed", "u32le"),
        default="fixed",
        help="fixed reads exactly one 4,863,232 byte packet; u32le expects a 4-byte little-endian length prefix.",
    )
    parser.add_argument("--packet-file", help="Raw packet file for --source file.")
    parser.add_argument("--loop", action="store_true", help="Loop packet file replay.")
    parser.add_argument("--demo-fps", type=float, default=25.0, help="Synthetic demo frame rate.")
    parser.add_argument("--headless", action="store_true", help="Parse frames and print stats without opening a GUI.")
    parser.add_argument("--frames", type=int, default=5, help="Frame count for --headless.")
    parser.add_argument("--scale", type=int, default=3, help="Display scale for 256x192 planes.")
    parser.add_argument("--palette", choices=tuple(PALETTES), default="ironbow", help="Temperature palette.")
    parser.add_argument("--ir-palette", choices=tuple(PALETTES), default="gray", help="IR palette.")
    parser.add_argument("--low-percentile", type=float, default=1.0, help="Low percentile for display normalization.")
    parser.add_argument("--high-percentile", type=float, default=99.5, help="High percentile for display normalization.")
    parser.add_argument("--show-visible", action="store_true", help="Also render the large visible RGB payload.")
    parser.add_argument(
        "--temp-divisor",
        type=float,
        default=1.0,
        help="Display conversion divisor for raw temp values. Use 1 until calibration is known.",
    )
    parser.add_argument(
        "--temp-offset",
        type=float,
        default=0.0,
        help="Display conversion offset for raw temp values. Use 0 until calibration is known.",
    )
    parser.add_argument("--check-phone", action="store_true", help="Check connected phone/ThermoVue USB state and exit.")
    parser.add_argument("--launch-thermovue", action="store_true", help="Launch ThermoVue before checking/running.")
    parser.add_argument("--adb", help="Path to adb.exe.")
    parser.add_argument("--serial", help="ADB serial if multiple devices are connected.")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    if args.check_phone:
        return check_phone(args.adb, args.serial, args.launch_thermovue)

    if args.launch_thermovue:
        adb = find_adb(args.adb)
        serial = select_adb_device(adb, args.serial)
        if not serial:
            raise SystemExit("No authorized ADB device found for --launch-thermovue.")
        launch_thermovue(adb, serial)
        time.sleep(5)

    source = build_source(args)
    if args.headless:
        return run_headless(args, source)

    return LiveViewer(args, source).run()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
