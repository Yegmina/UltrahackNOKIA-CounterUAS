#!/usr/bin/env python3
"""
ADB thermal stream smoke test for the Ulefone Armor 28 Ultra Thermal.

This does not require raw thermal-camera API access. For the first hackathon
test, open the phone's thermal camera app, then stream the phone screen over ADB.
If the app shows live thermal imagery, this script gives us a real-time thermal
feed for later detection/fusion experiments.
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import Iterable

import numpy as np
from PIL import Image, ImageDraw, ImageTk
import tkinter as tk


THERMAL_PACKAGE_KEYWORDS = (
    "thermal",
    "therm",
    "thermovue",
    "flir",
    "infrared",
    "ircamera",
    "ir.camera",
    "night",
    "ulefone",
)


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

    raise SystemExit(
        "Could not find adb. Install Android platform-tools or pass --adb "
        "C:\\path\\to\\adb.exe"
    )


def run_adb(
    adb: str,
    args: Iterable[str],
    *,
    serial: str | None = None,
    timeout: float = 15,
    text: bool = True,
) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
    cmd = [adb]
    if serial:
        cmd += ["-s", serial]
    cmd += list(args)
    return subprocess.run(
        cmd,
        capture_output=True,
        text=text,
        timeout=timeout,
        check=False,
    )


def parse_devices(output: str) -> list[dict[str, str]]:
    devices: list[dict[str, str]] = []
    for line in output.splitlines()[1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        serial = parts[0]
        state = parts[1] if len(parts) > 1 else "unknown"
        devices.append({"serial": serial, "state": state, "raw": line})
    return devices


def select_device(adb: str, serial: str | None) -> str:
    result = run_adb(adb, ["devices", "-l"], timeout=20)
    output = result.stdout if isinstance(result.stdout, str) else result.stdout.decode("utf-8", "replace")
    devices = parse_devices(output)

    if serial:
        for device in devices:
            if device["serial"] == serial:
                if device["state"] != "device":
                    explain_bad_state(device["state"])
                return serial
        raise SystemExit(f"ADB device {serial!r} was not listed.\n\n{output}")

    ready = [device for device in devices if device["state"] == "device"]
    if len(ready) == 1:
        return ready[0]["serial"]
    if len(ready) > 1:
        raise SystemExit(
            "More than one authorized ADB device is connected. Re-run with "
            f"--serial SERIAL.\n\n{output}"
        )

    if devices:
        explain_bad_state(devices[0]["state"])

    raise SystemExit(
        "No ADB device is listed.\n"
        "Check the USB cable, set USB mode to file transfer if needed, and enable "
        "Developer options -> USB debugging on the phone."
    )


def explain_bad_state(state: str) -> None:
    if state == "unauthorized":
        raise SystemExit(
            "Phone is connected but ADB is unauthorized.\n"
            "Unlock the phone and tap 'Allow USB debugging'. If no prompt appears, "
            "toggle USB debugging off/on or use 'Revoke USB debugging authorizations' "
            "in Developer options, then reconnect."
        )
    if state == "offline":
        raise SystemExit("ADB sees the phone but it is offline. Reconnect USB and try again.")
    raise SystemExit(f"ADB sees the phone but state is {state!r}.")


def adb_shell_text(adb: str, serial: str, command: str, timeout: float = 20) -> str:
    result = run_adb(adb, ["shell", command], serial=serial, timeout=timeout)
    stdout = result.stdout if isinstance(result.stdout, str) else result.stdout.decode("utf-8", "replace")
    stderr = result.stderr if isinstance(result.stderr, str) else result.stderr.decode("utf-8", "replace")
    if result.returncode != 0 and stderr.strip():
        return stdout + "\n[stderr]\n" + stderr
    return stdout


def list_packages(adb: str, serial: str) -> list[str]:
    output = adb_shell_text(adb, serial, "pm list packages", timeout=30)
    packages: list[str] = []
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("package:"):
            packages.append(line.removeprefix("package:"))
    return packages


def candidate_thermal_packages(packages: Iterable[str]) -> list[str]:
    candidates = []
    for package in packages:
        lower = package.lower()
        if any(keyword in lower for keyword in THERMAL_PACKAGE_KEYWORDS):
            candidates.append(package)
    return sorted(set(candidates))


def command_probe(args: argparse.Namespace) -> int:
    adb = find_adb(args.adb)
    serial = select_device(adb, args.serial)

    log_dir = Path(args.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"phone_probe_{stamp}.txt"

    commands = {
        "adb devices -l": lambda: run_adb(adb, ["devices", "-l"], timeout=20).stdout,
        "model props": lambda: adb_shell_text(
            adb,
            serial,
            "getprop | grep -E 'ro.product|ro.build.version|ro.hardware|ro.vendor|camera|thermal'",
            timeout=20,
        ),
        "current focus": lambda: adb_shell_text(
            adb,
            serial,
            "dumpsys window | grep -E 'mCurrentFocus|mFocusedApp|topResumedActivity'",
            timeout=20,
        ),
        "packages": lambda: adb_shell_text(adb, serial, "pm list packages", timeout=30),
        "camera dump": lambda: adb_shell_text(adb, serial, "dumpsys media.camera", timeout=45),
    }

    sections: list[str] = []
    packages: list[str] = []
    for title, getter in commands.items():
        try:
            value = getter()
            if isinstance(value, bytes):
                value = value.decode("utf-8", "replace")
        except subprocess.TimeoutExpired:
            value = "[timeout]"
        sections.append(f"\n===== {title} =====\n{value}")
        if title == "packages":
            packages = [
                line.removeprefix("package:")
                for line in str(value).splitlines()
                if line.startswith("package:")
            ]

    candidates = candidate_thermal_packages(packages)
    sections.insert(
        0,
        "===== thermal-looking package candidates =====\n"
        + ("\n".join(candidates) if candidates else "[none found by package-name keywords]"),
    )
    log_path.write_text("\n".join(sections), encoding="utf-8")

    print(f"ADB: {adb}")
    print(f"Device: {serial}")
    print(f"Probe log: {log_path}")
    if candidates:
        print("\nThermal-looking package candidates:")
        for package in candidates:
            print(f"  {package}")
        print("\nTry launching one with:")
        print(f"  {sys.executable} {Path(__file__)} launch --package {candidates[0]}")
    else:
        print("\nNo obvious thermal package names found. Open the thermal app manually on the phone.")
    return 0


def command_launch(args: argparse.Namespace) -> int:
    adb = find_adb(args.adb)
    serial = select_device(adb, args.serial)
    package = args.package
    result = run_adb(
        adb,
        ["shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"],
        serial=serial,
        timeout=20,
    )
    print(result.stdout.strip())
    if result.stderr.strip():
        print(result.stderr.strip(), file=sys.stderr)
    return result.returncode


def parse_crop(crop: str | None) -> tuple[int, int, int, int] | None:
    if not crop:
        return None
    match = re.fullmatch(r"\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*", crop)
    if not match:
        raise SystemExit("--crop must be x,y,width,height")
    x, y, w, h = [int(group) for group in match.groups()]
    if w <= 0 or h <= 0:
        raise SystemExit("--crop width and height must be positive")
    return x, y, w, h


def screencap_png(adb: str, serial: str, timeout: float = 5) -> bytes:
    result = run_adb(
        adb,
        ["exec-out", "screencap", "-p"],
        serial=serial,
        timeout=timeout,
        text=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", "replace") if isinstance(result.stderr, bytes) else str(result.stderr)
        raise RuntimeError(stderr.strip() or "adb screencap failed")
    data = result.stdout if isinstance(result.stdout, bytes) else result.stdout.encode()
    png_start = data.find(b"\x89PNG")
    if png_start > 0:
        # Some Android builds print a multi-display warning before the image bytes.
        data = data[png_start:]
    if not data.startswith(b"\x89PNG"):
        # Defensive fallback for environments that still CRLF-mangle screencap output.
        data = data.replace(b"\r\n", b"\n")
    return data


def decode_png(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data)).convert("RGB")


def crop_image(image: Image.Image, crop: tuple[int, int, int, int] | None) -> Image.Image:
    if not crop:
        return image
    x, y, w, h = crop
    return image.crop((x, y, x + w, y + h))


def draw_hotspot_overlay(
    image: Image.Image,
    *,
    percentile: float,
    min_pixels: int,
) -> tuple[Image.Image, str]:
    arr = np.asarray(image)
    gray = (0.299 * arr[:, :, 0] + 0.587 * arr[:, :, 1] + 0.114 * arr[:, :, 2]).astype(np.float32)
    threshold = float(np.percentile(gray, percentile))
    ys, xs = np.where(gray >= threshold)

    annotated = image.copy()
    draw = ImageDraw.Draw(annotated)
    label = f"hotspot p{percentile:g}: threshold={threshold:.1f}, pixels={len(xs)}"

    if len(xs) >= min_pixels:
        x0, x1 = int(xs.min()), int(xs.max())
        y0, y1 = int(ys.min()), int(ys.max())
        draw.rectangle((x0, y0, x1, y1), outline=(255, 40, 40), width=4)
        draw.text((x0 + 6, max(0, y0 - 18)), "bright thermal region", fill=(255, 40, 40))
    return annotated, label


class ScreenStreamApp:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.adb = find_adb(args.adb)
        self.serial = select_device(self.adb, args.serial)
        self.crop = parse_crop(args.crop)
        self.stop = threading.Event()
        self.frames: queue.Queue[tuple[Image.Image, float, str | None]] = queue.Queue(maxsize=1)
        self.error: str | None = None
        self.last_photo: ImageTk.PhotoImage | None = None
        self.save_dir = Path(args.save_dir) if args.save_dir else None
        self.frame_count = 0

        if self.save_dir:
            self.save_dir.mkdir(parents=True, exist_ok=True)

        if args.launch:
            launch_args = argparse.Namespace(adb=args.adb, serial=args.serial, package=args.launch)
            command_launch(launch_args)
            time.sleep(args.launch_delay)

        self.root = tk.Tk()
        self.root.title("Ulefone thermal screen stream over ADB")
        self.label = tk.Label(self.root)
        self.label.pack()
        self.status = tk.StringVar(value="starting...")
        tk.Label(self.root, textvariable=self.status, anchor="w", justify="left").pack(fill="x")
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def close(self) -> None:
        self.stop.set()
        self.root.after(100, self.root.destroy)

    def capture_loop(self) -> None:
        sample_times: list[float] = []
        while not self.stop.is_set():
            started = time.perf_counter()
            try:
                data = screencap_png(self.adb, self.serial, timeout=self.args.capture_timeout)
                image = decode_png(data)
                image = crop_image(image, self.crop)
                overlay = None
                if self.args.hotspots:
                    image, overlay = draw_hotspot_overlay(
                        image,
                        percentile=self.args.hotspot_percentile,
                        min_pixels=self.args.hotspot_min_pixels,
                    )

                self.frame_count += 1
                if self.save_dir and self.args.save_every and self.frame_count % self.args.save_every == 0:
                    image.save(self.save_dir / f"thermal_screen_{self.frame_count:06d}.png")

                sample_times.append(time.perf_counter())
                sample_times = sample_times[-20:]
                if len(sample_times) >= 2:
                    fps = (len(sample_times) - 1) / (sample_times[-1] - sample_times[0])
                else:
                    fps = 0.0

                status = (
                    f"device={self.serial} | frame={self.frame_count} | "
                    f"capture_fps={fps:.2f} | size={image.width}x{image.height}"
                )
                if overlay:
                    status += f" | {overlay}"

                while not self.frames.empty():
                    try:
                        self.frames.get_nowait()
                    except queue.Empty:
                        break
                self.frames.put_nowait((image, fps, status))

                elapsed = time.perf_counter() - started
                sleep_for = max(0.0, self.args.interval - elapsed)
                if sleep_for:
                    time.sleep(sleep_for)
            except Exception as exc:  # Keep the GUI alive long enough to show failure.
                self.error = str(exc)
                time.sleep(0.5)

    def refresh(self) -> None:
        if self.error:
            self.status.set(f"ERROR: {self.error}")
        try:
            image, _fps, status = self.frames.get_nowait()
        except queue.Empty:
            self.root.after(30, self.refresh)
            return

        if self.args.max_width and image.width > self.args.max_width:
            ratio = self.args.max_width / image.width
            image = image.resize((self.args.max_width, int(image.height * ratio)))

        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, image.width, 24), fill=(0, 0, 0))
        draw.text((8, 5), "ADB thermal screen stream", fill=(255, 255, 255))
        self.last_photo = ImageTk.PhotoImage(image)
        self.label.configure(image=self.last_photo)
        self.status.set(status)
        self.root.after(30, self.refresh)

    def run(self) -> int:
        thread = threading.Thread(target=self.capture_loop, daemon=True)
        thread.start()
        self.root.after(30, self.refresh)
        self.root.mainloop()
        self.stop.set()
        thread.join(timeout=2)
        return 0


def command_screen_stream(args: argparse.Namespace) -> int:
    return ScreenStreamApp(args).run()


def command_snapshot(args: argparse.Namespace) -> int:
    adb = find_adb(args.adb)
    serial = select_device(adb, args.serial)
    crop = parse_crop(args.crop)
    image = decode_png(screencap_png(adb, serial, timeout=args.capture_timeout))
    image = crop_image(image, crop)
    status = None
    if args.hotspots:
        image, status = draw_hotspot_overlay(
            image,
            percentile=args.hotspot_percentile,
            min_pixels=args.hotspot_min_pixels,
        )

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output)
    print(f"Saved snapshot: {output}")
    print(f"Device: {serial}")
    print(f"Size: {image.width}x{image.height}")
    if status:
        print(status)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Thermal phone stream tests over ADB")
    parser.add_argument("--adb", help="Path to adb.exe")
    parser.add_argument("--serial", help="ADB device serial when multiple devices are connected")

    subparsers = parser.add_subparsers(dest="command", required=True)

    probe = subparsers.add_parser("probe", help="Inspect phone packages and camera service info")
    probe.add_argument("--log-dir", default="prototype/logs", help="Directory for probe logs")
    probe.set_defaults(func=command_probe)

    launch = subparsers.add_parser("launch", help="Launch an Android package by package name")
    launch.add_argument("--package", required=True, help="Package to launch")
    launch.set_defaults(func=command_launch)

    stream = subparsers.add_parser("screen-stream", help="Stream phone screen frames over ADB")
    stream.add_argument("--launch", help="Optional package to launch before streaming")
    stream.add_argument("--launch-delay", type=float, default=2.0, help="Seconds to wait after launch")
    stream.add_argument("--interval", type=float, default=0.0, help="Minimum seconds between captures")
    stream.add_argument("--capture-timeout", type=float, default=5.0, help="Seconds before screencap timeout")
    stream.add_argument("--crop", help="Crop screen to x,y,width,height")
    stream.add_argument("--max-width", type=int, default=900, help="Resize display window to this width")
    stream.add_argument("--hotspots", action="store_true", help="Draw a simple bright-region hotspot box")
    stream.add_argument("--hotspot-percentile", type=float, default=99.6)
    stream.add_argument("--hotspot-min-pixels", type=int, default=30)
    stream.add_argument("--save-dir", help="Optional directory to save sampled frames")
    stream.add_argument("--save-every", type=int, default=0, help="Save every Nth frame when --save-dir is set")
    stream.set_defaults(func=command_screen_stream)

    snapshot = subparsers.add_parser("snapshot", help="Save one phone screen frame")
    snapshot.add_argument("--capture-timeout", type=float, default=5.0, help="Seconds before screencap timeout")
    snapshot.add_argument("--crop", help="Crop screen to x,y,width,height")
    snapshot.add_argument("--hotspots", action="store_true", help="Draw a simple bright-region hotspot box")
    snapshot.add_argument("--hotspot-percentile", type=float, default=99.6)
    snapshot.add_argument("--hotspot-min-pixels", type=int, default=30)
    snapshot.add_argument("--output", default="prototype/logs/thermal_snapshot.png")
    snapshot.set_defaults(func=command_snapshot)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
