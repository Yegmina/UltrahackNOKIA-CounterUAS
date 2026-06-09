#!/usr/bin/env python3
"""Forward ThermoVue raw sensor packets from a Frida hook to the laptop viewer.

This is the phone-side bridge controller for the Ulefone Armor 28 Ultra
Thermal/ThermoVue prototype. It expects a Frida server/gadget capable of
instrumenting the ThermoVue process. The phone used during initial testing was
not rooted, so this script also performs clear prerequisite checks and exits
with an actionable message when Android blocks instrumentation.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
from typing import Any


PACKAGE = "com.energy.tc2c"
ACTIVITY = "com.energy.tc2c/com.energy.usbCamera.ui.splash.SplashActivity"
PACKET_BYTES = 4_863_232


def common_adb_paths() -> list[Path]:
    paths: list[Path] = []
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        paths.append(Path(local_app_data) / "Android" / "Sdk" / "platform-tools" / "adb.exe")
    android_home = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    if android_home:
        paths.append(Path(android_home) / "platform-tools" / "adb.exe")
    return paths


def find_adb(adb_arg: str | None) -> str:
    if adb_arg:
        return adb_arg
    for path in common_adb_paths():
        if path.exists():
            return str(path)
    found = shutil.which("adb")
    if found:
        return found
    raise SystemExit("adb not found. Install Android platform-tools or pass --adb.")


def run_adb(adb: str, serial: str | None, *args: str, timeout: int = 20) -> subprocess.CompletedProcess[str]:
    cmd = [adb]
    if serial:
        cmd.extend(["-s", serial])
    cmd.extend(args)
    return subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)


def adb_shell(adb: str, serial: str | None, command: str, timeout: int = 20) -> str:
    result = run_adb(adb, serial, "shell", command, timeout=timeout)
    return (result.stdout + result.stderr).strip()


def select_adb_device(adb: str, serial: str | None) -> str | None:
    if serial:
        return serial
    result = run_adb(adb, None, "devices", timeout=10)
    for line in result.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            return parts[0]
    return None


def launch_thermovue(adb: str, serial: str | None, delay: float) -> None:
    print("Launching ThermoVue...")
    result = run_adb(adb, serial, "shell", "am", "start", "-n", ACTIVITY, timeout=20)
    print((result.stdout + result.stderr).strip())
    time.sleep(delay)


def install_hint() -> str:
    return (
        "Install laptop packages with:\n"
        "  py -3 -m pip install frida frida-tools\n\n"
        "The phone also needs a matching frida-server running as root, or a "
        "Frida Gadget build embedded in a debuggable/copy of the target app."
    )


def import_frida():
    try:
        import frida  # type: ignore
    except ImportError as exc:
        raise SystemExit(f"Python package 'frida' is not installed.\n\n{install_hint()}") from exc
    return frida


def check_phone_lock_state(adb: str, serial: str | None) -> None:
    model = adb_shell(adb, serial, "getprop ro.product.model")
    sdk = adb_shell(adb, serial, "getprop ro.build.version.sdk")
    abi = adb_shell(adb, serial, "getprop ro.product.cpu.abi")
    debuggable = adb_shell(adb, serial, "getprop ro.debuggable")
    shell_id = adb_shell(adb, serial, "id")
    su_id = adb_shell(adb, serial, "su -c id")

    print(f"ADB device: {serial or '(default)'}")
    print(f"Model: {model or '?'}")
    print(f"Android SDK: {sdk or '?'}")
    print(f"ABI: {abi or '?'}")
    print(f"ro.debuggable: {debuggable or '?'}")
    print(f"shell id: {shell_id or '?'}")
    if "uid=0" in su_id:
        print(f"su: {su_id}")
    else:
        print("su: unavailable or not root")
        print(
            "Note: this phone state probably cannot attach Frida to ThermoVue. "
            "Continuing anyway so the exact failure is visible."
        )


class ViewerForwarder:
    def __init__(self, host: str, port: int, protocol: str, disabled: bool) -> None:
        self.host = host
        self.port = port
        self.protocol = protocol
        self.disabled = disabled
        self.sock: socket.socket | None = None
        self.lock = threading.Lock()

    def close(self) -> None:
        with self.lock:
            if self.sock is not None:
                self.sock.close()
                self.sock = None

    def _connect(self) -> socket.socket:
        if self.sock is None:
            print(f"Connecting to local viewer at {self.host}:{self.port}...")
            self.sock = socket.create_connection((self.host, self.port), timeout=10)
            print("Viewer TCP connection established.")
        return self.sock

    def forward(self, payload: bytes) -> None:
        if self.disabled:
            return
        if len(payload) != PACKET_BYTES:
            raise ValueError(f"Expected {PACKET_BYTES:,} bytes, got {len(payload):,}.")
        with self.lock:
            sock = self._connect()
            if self.protocol == "u32le":
                sock.sendall(struct.pack("<I", len(payload)))
            sock.sendall(payload)


def start_viewer(args: argparse.Namespace) -> subprocess.Popen[str] | None:
    if not args.start_viewer:
        return None
    viewer = Path(__file__).with_name("thermovue_sensor_live_viewer.py")
    cmd = [
        sys.executable,
        str(viewer),
        "--source",
        "tcp-listen",
        "--bind",
        args.viewer_host,
        "--port",
        str(args.viewer_port),
        "--protocol",
        args.viewer_protocol,
    ]
    if args.show_visible:
        cmd.append("--show-visible")
    print("Starting local viewer:")
    print("  " + " ".join(cmd))
    process = subprocess.Popen(cmd, text=True)
    time.sleep(1.5)
    return process


def find_process(device: Any, package: str) -> int | None:
    for process in device.enumerate_processes():
        name = process.name.lower()
        if process.name == package or process.name.startswith(package + ":"):
            return int(process.pid)
        if "thermovue" in name or "tc2c" in name or "energy" in name:
            print(f"Frida candidate process: pid={process.pid} name={process.name}")
            return int(process.pid)
    return None


def adb_pidof(adb: str, serial: str | None, package: str) -> int | None:
    output = adb_shell(adb, serial, f"pidof {package}")
    for token in output.split():
        if token.isdigit():
            return int(token)
    return None


def load_agent_source(args: argparse.Namespace) -> str:
    hook_path = Path(args.hook_js) if args.hook_js else Path(__file__).with_name("thermovue_frame_hook.js")
    source = hook_path.read_text(encoding="utf-8")
    config = {
        "expectedLength": PACKET_BYTES,
        "every": args.every,
        "maxFrames": args.frames,
    }
    return f"globalThis.BRIDGE_CONFIG = {json.dumps(config)};\n{source}"


def run_bridge(args: argparse.Namespace) -> int:
    adb = find_adb(args.adb)
    serial = select_adb_device(adb, args.serial)
    if not serial:
        print("No authorized ADB device found.")
        return 1

    check_phone_lock_state(adb, serial)
    if args.launch:
        launch_thermovue(adb, serial, args.launch_delay)

    frida = import_frida()
    viewer_process = start_viewer(args)
    forwarder = ViewerForwarder(args.viewer_host, args.viewer_port, args.viewer_protocol, args.no_tcp)
    done = threading.Event()
    counters = {"frames": 0, "bytes": 0}

    def on_message(message: dict[str, Any], data: bytes | None) -> None:
        if message.get("type") != "send":
            print(f"Frida message: {message}")
            return
        payload = message.get("payload") or {}
        kind = payload.get("type")
        if kind == "frame":
            if data is None:
                print("Frame metadata arrived without binary payload.")
                return
            counters["frames"] += 1
            counters["bytes"] += len(data)
            print(
                "frame "
                f"{counters['frames']} forwarded "
                f"len={len(data):,} "
                f"seen={payload.get('seen')} "
                f"class={payload.get('className')}"
            )
            if args.save_dir:
                save_frame(Path(args.save_dir), counters["frames"], data)
            try:
                forwarder.forward(data)
            except Exception as exc:  # noqa: BLE001 - report bridge failures cleanly
                print(f"Viewer forward failed: {exc}")
                done.set()
                return
            if args.frames and counters["frames"] >= args.frames:
                done.set()
        elif kind == "status":
            print(f"[agent] {payload.get('text')}")
            details = {k: v for k, v in payload.items() if k not in {"type", "text"}}
            if details:
                print(f"        {details}")
        elif kind == "error":
            print(f"[agent error] {payload.get('text')}")
            details = {k: v for k, v in payload.items() if k not in {"type", "text"}}
            if details:
                print(f"              {details}")
        else:
            print(f"[agent] {payload}")

    session = None
    try:
        print("Connecting to Frida USB device...")
        device = frida.get_usb_device(timeout=args.frida_timeout)
        pid = adb_pidof(adb, serial, args.package)
        if pid is not None:
            print(f"Using Android pidof target: {args.package} pid={pid}")
        else:
            pid = find_process(device, args.package)
        if pid is None:
            print(f"Process {args.package} was not found. Launching ThermoVue and retrying...")
            launch_thermovue(adb, serial, args.launch_delay)
            pid = adb_pidof(adb, serial, args.package)
            if pid is not None:
                print(f"Using Android pidof target: {args.package} pid={pid}")
            else:
                pid = find_process(device, args.package)
        if pid is None:
            print(f"Still cannot find process {args.package}.")
            return 1

        print(f"Attaching Frida to {args.package} pid={pid}...")
        session = device.attach(pid)
        script = session.create_script(load_agent_source(args))
        script.on("message", on_message)
        script.load()

        print("Bridge is running. Press Ctrl+C to stop.")
        while not done.wait(0.25):
            pass
        print(f"Bridge stopped after {counters['frames']} forwarded frame(s).")
        return 0
    except KeyboardInterrupt:
        print("Stopping bridge...")
        return 130
    except Exception as exc:  # noqa: BLE001 - Frida has several environment-specific errors
        print(f"Bridge could not attach/run: {exc}")
        print()
        print(
            "Most likely cause on this phone: Android is not rooted/debuggable, "
            "so Frida cannot instrument the privileged ThermoVue system app."
        )
        print()
        print(install_hint())
        return 2
    finally:
        forwarder.close()
        if session is not None:
            try:
                session.detach()
            except Exception:
                pass
        if viewer_process is not None and viewer_process.poll() is None:
            viewer_process.terminate()


def save_frame(save_dir: Path, index: int, data: bytes) -> None:
    save_dir.mkdir(parents=True, exist_ok=True)
    path = save_dir / f"thermovue_frame_{index:05d}.bin"
    path.write_bytes(data)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", default=PACKAGE, help="ThermoVue package/process name.")
    parser.add_argument("--adb", help="Path to adb.exe.")
    parser.add_argument("--serial", help="ADB serial if multiple devices are connected.")
    parser.add_argument("--launch", action="store_true", help="Launch ThermoVue before attaching.")
    parser.add_argument("--launch-delay", type=float, default=5.0, help="Seconds to wait after launching ThermoVue.")
    parser.add_argument("--frida-timeout", type=float, default=5.0, help="Seconds to wait for Frida USB device.")
    parser.add_argument("--hook-js", help="Override path to Frida hook JS.")
    parser.add_argument("--every", type=int, default=5, help="Forward every Nth sensor frame to reduce USB load.")
    parser.add_argument("--frames", type=int, default=0, help="Stop after N forwarded frames. 0 means run forever.")
    parser.add_argument("--save-dir", help="Optional directory for raw packet captures.")
    parser.add_argument("--no-tcp", action="store_true", help="Do not forward to the local TCP viewer.")
    parser.add_argument("--start-viewer", action="store_true", help="Start the local TCP viewer automatically.")
    parser.add_argument("--viewer-host", default="127.0.0.1", help="Viewer TCP host.")
    parser.add_argument("--viewer-port", type=int, default=7777, help="Viewer TCP port.")
    parser.add_argument("--viewer-protocol", choices=("fixed", "u32le"), default="fixed", help="Viewer packet protocol.")
    parser.add_argument("--show-visible", action="store_true", help="Ask viewer to render the visible RGB payload too.")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    return run_bridge(parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
