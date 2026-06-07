#!/usr/bin/env python3
"""
Smoke tests for the phone-as-sensor pipeline.

Current target:
- IP Webcam RGB frames over ADB port-forward
- IP Webcam MJPEG stream endpoint
- IP Webcam audio endpoints
- Basic status metadata
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import time
import urllib.error
import urllib.request


def common_adb() -> str | None:
    found = shutil.which("adb")
    if found:
        return found
    local = Path.home() / "AppData" / "Local" / "Android" / "Sdk" / "platform-tools" / "adb.exe"
    if local.exists():
        return str(local)
    return None


def run(cmd: list[str], timeout: float = 15) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def adb_forward(adb: str, serial: str | None, host_port: int, device_port: int) -> None:
    cmd = [adb]
    if serial:
        cmd += ["-s", serial]
    cmd += ["forward", f"tcp:{host_port}", f"tcp:{device_port}"]
    result = run(cmd)
    if result.returncode != 0:
        raise SystemExit(result.stderr.strip() or result.stdout.strip() or "adb forward failed")


def fetch(url: str, timeout: float = 5, max_bytes: int | None = None) -> tuple[int, str | None, bytes]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        if max_bytes is None:
            body = response.read()
        else:
            body = response.read(max_bytes)
        return response.status, response.headers.get("Content-Type"), body


def test_status(base_url: str) -> dict:
    status, content_type, body = fetch(base_url + "/status.json", timeout=5)
    data = json.loads(body.decode("utf-8", "replace"))
    print(f"status.json: HTTP {status}, content-type={content_type}")
    print(
        "device: "
        f"battery={data.get('deviceInfo', {}).get('batteryPercent')}%, "
        f"charging={data.get('deviceInfo', {}).get('batteryCharging')}, "
        f"video_size={data.get('curvals', {}).get('video_size')}, "
        f"orientation={data.get('curvals', {}).get('orientation')}"
    )
    return data


def benchmark_shot(base_url: str, frames: int, save_dir: Path) -> None:
    save_dir.mkdir(parents=True, exist_ok=True)
    url = base_url + "/shot.jpg"
    sizes: list[int] = []
    last = b""
    started = time.perf_counter()
    for _ in range(frames):
        status, content_type, body = fetch(url, timeout=5)
        if status != 200 or not body.startswith(b"\xff\xd8"):
            raise RuntimeError(f"bad shot.jpg response: status={status}, content_type={content_type}")
        sizes.append(len(body))
        last = body
    elapsed = time.perf_counter() - started
    (save_dir / "ipwebcam_last_shot.jpg").write_bytes(last)
    print(f"shot.jpg: {frames} frames in {elapsed:.2f}s = {frames / elapsed:.2f} FPS")
    print(f"shot.jpg bytes min/avg/max: {min(sizes)}/{sum(sizes)//len(sizes)}/{max(sizes)}")


def test_mjpeg(base_url: str) -> None:
    status, content_type, body = fetch(base_url + "/video", timeout=8, max_bytes=8192)
    has_jpeg = b"\xff\xd8" in body
    ok = status == 200 and content_type and "multipart/x-mixed-replace" in content_type and has_jpeg
    print(f"video: HTTP {status}, content-type={content_type}, jpeg_in_first_8k={has_jpeg}")
    if not ok:
        raise RuntimeError("MJPEG stream endpoint did not look valid")


def test_audio(base_url: str) -> None:
    for path in ("/audio.wav", "/audio.aac", "/audio.opus"):
        try:
            status, content_type, body = fetch(base_url + path, timeout=8, max_bytes=4096)
            print(f"{path}: HTTP {status}, content-type={content_type}, bytes={len(body)}, head={body[:8]!r}")
        except urllib.error.HTTPError as exc:
            print(f"{path}: HTTP {exc.code}")
        except Exception as exc:
            print(f"{path}: ERROR {type(exc).__name__}: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--adb", default=common_adb(), help="Path to adb.exe")
    parser.add_argument("--serial", help="ADB serial")
    parser.add_argument("--host-port", type=int, default=8080)
    parser.add_argument("--device-port", type=int, default=8080)
    parser.add_argument("--base-url", help="Override base URL instead of using ADB port-forward")
    parser.add_argument("--frames", type=int, default=60)
    parser.add_argument("--save-dir", default="prototype/logs/smoke")
    args = parser.parse_args()

    if args.base_url:
        base_url = args.base_url.rstrip("/")
    else:
        if not args.adb:
            raise SystemExit("adb not found; pass --adb or --base-url")
        adb_forward(args.adb, args.serial, args.host_port, args.device_port)
        base_url = f"http://127.0.0.1:{args.host_port}"
        print(f"ADB port-forward active: {base_url} -> device tcp:{args.device_port}")

    save_dir = Path(args.save_dir)
    test_status(base_url)
    benchmark_shot(base_url, args.frames, save_dir)
    test_mjpeg(base_url)
    test_audio(base_url)
    print("Smoke test complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
