#!/usr/bin/env python3
"""Live-ish ThermoVue IJPEG pull bridge over ADB.

This keeps ThermoVue Pro in the foreground, triggers its photo button through
ADB, pulls the newest `/sdcard/Pictures/thermo_tc2c/*.jpg`, extracts the real
256x192 uint16 thermal plane from the IJPEG APP3 payload, and visualizes or
forwards it.

It is a practical bridge while native/in-process live callbacks remain blocked
by platform privileges. It is slower than a true stream, but the extracted plane
is raw thermal data saved by ThermoVue, not a screen capture.
"""

from __future__ import annotations

import argparse
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Iterable

import numpy as np

from thermovue_ijpeg_extract import iter_jpeg_segments, parse_ijpeg_descriptor


DEFAULT_ACTIVITY = "com.energy.tc2c/com.energy.usbCamera.ui.splash.SplashActivity"
DEFAULT_PHONE_DIR = "/sdcard/Pictures/thermo_tc2c"
MAGIC = b"YEGMINA_THERMAL_RAW_V1 "


def common_adb_paths() -> list[Path]:
    local_app_data = Path.home() / "AppData" / "Local"
    return [
        local_app_data / "Android" / "Sdk" / "platform-tools" / "adb.exe",
        Path("C:/Android/platform-tools/adb.exe"),
    ]


def find_adb(explicit: str | None) -> str:
    if explicit:
        path = Path(explicit).expanduser()
        if not path.exists():
            raise SystemExit(f"ADB path does not exist: {path}")
        return str(path)
    found = shutil.which("adb")
    if found:
        return found
    for candidate in common_adb_paths():
        if candidate.exists():
            return str(candidate)
    raise SystemExit("Could not find adb. Pass --adb C:\\path\\to\\adb.exe")


def run_adb(
    adb: str,
    args: Iterable[str],
    *,
    serial: str | None = None,
    timeout: float = 20.0,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    cmd = [adb]
    if serial:
        cmd += ["-s", serial]
    cmd += list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    if check and result.returncode != 0:
        raise RuntimeError(
            f"adb command failed ({result.returncode}): {' '.join(cmd)}\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )
    return result


def start_thermovue(adb: str, serial: str | None, activity: str) -> None:
    run_adb(adb, ["shell", "input", "keyevent", "WAKEUP"], serial=serial, timeout=10)
    result = run_adb(adb, ["shell", "am", "start", "-n", activity], serial=serial, timeout=20)
    if result.returncode != 0:
        print(result.stdout.strip())
        print(result.stderr.strip())
        raise SystemExit("failed to launch ThermoVue")


def latest_phone_jpeg(adb: str, serial: str | None, phone_dir: str) -> str | None:
    script = f"ls -t {phone_dir}/*.jpg 2>/dev/null | head -1"
    result = run_adb(adb, ["shell", script], serial=serial, timeout=10)
    path = result.stdout.strip().splitlines()
    return path[0].strip() if path else None


def phone_file_size(adb: str, serial: str | None, phone_path: str) -> int | None:
    result = run_adb(
        adb,
        ["shell", f"stat -c %s {phone_path} 2>/dev/null || wc -c < {phone_path}"],
        serial=serial,
        timeout=10,
    )
    text = result.stdout.strip().splitlines()
    if not text:
        return None
    try:
        return int(text[-1].strip())
    except ValueError:
        return None


def wait_for_new_capture(
    adb: str,
    serial: str | None,
    phone_dir: str,
    previous: str | None,
    timeout_s: float,
) -> str:
    deadline = time.time() + timeout_s
    candidate: str | None = None
    last_size: int | None = None
    stable_since = 0.0
    while time.time() < deadline:
        latest = latest_phone_jpeg(adb, serial, phone_dir)
        if latest and latest != previous:
            size = phone_file_size(adb, serial, latest)
            if latest != candidate or size != last_size:
                candidate = latest
                last_size = size
                stable_since = time.time()
            elif size and time.time() - stable_since >= 0.4:
                return latest
        time.sleep(0.2)
    raise TimeoutError("no new ThermoVue JPEG appeared after capture tap")


def pull_file(adb: str, serial: str | None, phone_path: str, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    local = out_dir / Path(phone_path).name
    run_adb(adb, ["pull", phone_path, str(local)], serial=serial, timeout=30, check=True)
    return local


def extract_plane(path: Path, plane_name: str) -> np.ndarray:
    data = path.read_bytes()
    app2 = None
    app3_chunks: list[bytes] = []
    for marker, _marker_pos, _length, payload in iter_jpeg_segments(data):
        if marker == 0xDA:
            break
        if marker == 0xE2 and payload[4:12].rstrip(b"\0") == b"IJPEG":
            app2 = payload
        elif marker == 0xE3:
            app3_chunks.append(payload)
    if app2 is None:
        raise ValueError(f"{path} does not contain an IJPEG APP2 descriptor")

    descriptor = parse_ijpeg_descriptor(app2)
    payload = b"".join(app3_chunks)
    cursor = 0
    for plane in descriptor.planes:
        raw = payload[cursor : cursor + plane.size]
        cursor += plane.size
        if plane.name != plane_name:
            continue
        if plane.bit_num != 16:
            raise ValueError(f"{plane_name} is not a uint16 plane")
        return np.frombuffer(raw, dtype="<u2").reshape(plane.height, plane.width).copy()
    raise ValueError(f"plane not found: {plane_name}")


def normalize_u16(frame: np.ndarray) -> np.ndarray:
    lo, hi = np.percentile(frame, [2, 98])
    if hi <= lo:
        hi = lo + 1
    preview = np.clip((frame.astype(np.float32) - lo) * 255.0 / (hi - lo), 0, 255)
    return preview.astype(np.uint8)


def show_frame(frame: np.ndarray, scale: int, title: str) -> bool:
    import cv2

    preview = cv2.applyColorMap(normalize_u16(frame), cv2.COLORMAP_INFERNO)
    if scale > 1:
        preview = cv2.resize(
            preview,
            (frame.shape[1] * scale, frame.shape[0] * scale),
            interpolation=cv2.INTER_NEAREST,
        )
    cv2.imshow(title, preview)
    return cv2.waitKey(1) & 0xFF != ord("q")


def send_udp_frame(sock: socket.socket, target: tuple[str, int], frame_id: str, raw: bytes) -> None:
    max_payload = 1300
    chunks = (len(raw) + max_payload - 1) // max_payload
    for chunk in range(chunks):
        offset = chunk * max_payload
        piece = raw[offset : offset + max_payload]
        header = (
            MAGIC
            + f"frame={frame_id} chunk={chunk} chunks={chunks} offset={offset} total={len(raw)}\n".encode(
                "ascii"
            )
        )
        sock.sendto(header + piece, target)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb", help="Path to adb.exe")
    parser.add_argument("--serial", help="ADB serial if more than one device is connected")
    parser.add_argument("--activity", default=DEFAULT_ACTIVITY)
    parser.add_argument("--phone-dir", default=DEFAULT_PHONE_DIR)
    parser.add_argument("--out-dir", type=Path, default=Path("prototype/logs/ijpeg_live_pull"))
    parser.add_argument("--tap-x", type=int, default=540)
    parser.add_argument("--tap-y", type=int, default=1950)
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--startup-wait", type=float, default=6.0)
    parser.add_argument("--capture-timeout", type=float, default=10.0)
    parser.add_argument("--max-frames", type=int, default=0, help="0 means run until stopped")
    parser.add_argument("--plane", choices=["temp_u16le", "ir_u16le"], default="temp_u16le")
    parser.add_argument("--no-launch", action="store_true", help="Do not bring ThermoVue to foreground")
    parser.add_argument("--no-tap", action="store_true", help="Only watch/pull new ThermoVue JPEGs")
    parser.add_argument("--no-window", action="store_true")
    parser.add_argument("--scale", type=int, default=3)
    parser.add_argument("--udp-host", help="Forward extracted raw uint16 frames to this host")
    parser.add_argument("--udp-port", type=int, default=25000)
    args = parser.parse_args()

    adb = find_adb(args.adb)
    if not args.no_launch:
        start_thermovue(adb, args.serial, args.activity)
        time.sleep(args.startup_wait)

    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM) if args.udp_host else None
    udp_target = (args.udp_host, args.udp_port) if args.udp_host else None

    previous = latest_phone_jpeg(adb, args.serial, args.phone_dir)
    print(f"Watching {args.phone_dir}; previous={previous}")

    frame_count = 0
    try:
        while args.max_frames <= 0 or frame_count < args.max_frames:
            started = time.time()
            if not args.no_tap:
                run_adb(
                    adb,
                    ["shell", "input", "tap", str(args.tap_x), str(args.tap_y)],
                    serial=args.serial,
                    timeout=10,
                    check=True,
                )
            phone_path = wait_for_new_capture(
                adb, args.serial, args.phone_dir, previous, args.capture_timeout
            )
            previous = phone_path
            local_path = pull_file(adb, args.serial, phone_path, args.out_dir)
            frame = extract_plane(local_path, args.plane)
            frame_count += 1
            latency = time.time() - started
            print(
                f"frame={frame_count} file={Path(phone_path).name} "
                f"plane={args.plane} min={int(frame.min())} max={int(frame.max())} "
                f"mean={float(frame.mean()):.1f} latency={latency:.2f}s"
            )

            if udp_sock and udp_target:
                send_udp_frame(udp_sock, udp_target, str(frame_count), frame.astype("<u2").tobytes())
            if not args.no_window and not show_frame(frame, args.scale, "ThermoVue IJPEG thermal"):
                break

            sleep_for = args.interval - (time.time() - started)
            if sleep_for > 0:
                time.sleep(sleep_for)
    except KeyboardInterrupt:
        pass
    finally:
        if udp_sock:
            udp_sock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
