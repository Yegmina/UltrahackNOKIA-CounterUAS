"""Pan/tilt tracking controller scaffold.

The hardware is intentionally abstract because the final phone stand may expose
serial, Bluetooth-serial, Wi-Fi UDP, or an SDK. This file owns the control math
and small command protocol so the fusion node can be connected later.
"""

from __future__ import annotations

import argparse
import socket
import time
from dataclasses import dataclass


@dataclass
class MountCommand:
    pan_speed: float
    tilt_speed: float
    reason: str

    def as_line(self) -> str:
        return f"PT pan={self.pan_speed:.3f} tilt={self.tilt_speed:.3f} reason={self.reason}\n"


class PixelTrackerController:
    def __init__(
        self,
        deadband_px: int = 32,
        kp: float = 0.006,
        max_speed: float = 1.0,
        scan_speed: float = 0.18,
    ) -> None:
        self.deadband_px = deadband_px
        self.kp = kp
        self.max_speed = max_speed
        self.scan_speed = scan_speed
        self.scan_direction = 1.0
        self.last_seen_at = 0.0

    def update(
        self,
        frame_width: int,
        frame_height: int,
        target_xy: tuple[int, int] | None,
        now: float | None = None,
    ) -> MountCommand:
        now = time.time() if now is None else now
        if target_xy is None:
            if now - self.last_seen_at > 1.0:
                return MountCommand(
                    pan_speed=self.scan_direction * self.scan_speed,
                    tilt_speed=0.0,
                    reason="scan",
                )
            return MountCommand(0.0, 0.0, "hold")

        self.last_seen_at = now
        cx = frame_width / 2.0
        cy = frame_height / 2.0
        error_x = target_xy[0] - cx
        error_y = target_xy[1] - cy

        pan = 0.0 if abs(error_x) < self.deadband_px else self.kp * error_x
        tilt = 0.0 if abs(error_y) < self.deadband_px else -self.kp * error_y
        pan = clamp(pan, -self.max_speed, self.max_speed)
        tilt = clamp(tilt, -self.max_speed, self.max_speed)
        if pan == 0.0 and tilt == 0.0:
            return MountCommand(0.0, 0.0, "centered")
        return MountCommand(pan, tilt, "track")


class UdpMountClient:
    def __init__(self, host: str, port: int) -> None:
        self.address = (host, port)
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def send(self, command: MountCommand) -> None:
        self.socket.sendto(command.as_line().encode("ascii"), self.address)


class SerialMountClient:
    def __init__(self, port: str, baud: int = 115200) -> None:
        try:
            import serial
        except ImportError as exc:
            raise RuntimeError("Install pyserial to use --serial-port") from exc
        self.serial = serial.Serial(port, baudrate=baud, timeout=0.1)

    def send(self, command: MountCommand) -> None:
        self.serial.write(command.as_line().encode("ascii"))


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def parse_target(value: str) -> tuple[int, int] | None:
    if value.lower() in {"none", "lost", "scan"}:
        return None
    x_text, y_text = value.split(",", 1)
    return int(x_text), int(y_text)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frame-width", type=int, default=640)
    parser.add_argument("--frame-height", type=int, default=360)
    parser.add_argument("--target", default="none", help="x,y or none")
    parser.add_argument("--udp-host")
    parser.add_argument("--udp-port", type=int, default=26000)
    parser.add_argument("--serial-port")
    parser.add_argument("--serial-baud", type=int, default=115200)
    parser.add_argument("--deadband-px", type=int, default=32)
    parser.add_argument("--kp", type=float, default=0.006)
    parser.add_argument("--max-speed", type=float, default=1.0)
    args = parser.parse_args()

    controller = PixelTrackerController(
        deadband_px=args.deadband_px,
        kp=args.kp,
        max_speed=args.max_speed,
    )
    command = controller.update(
        args.frame_width,
        args.frame_height,
        parse_target(args.target),
    )
    print(command.as_line(), end="")

    client = None
    if args.udp_host:
        client = UdpMountClient(args.udp_host, args.udp_port)
    elif args.serial_port:
        client = SerialMountClient(args.serial_port, args.serial_baud)
    if client is not None:
        client.send(command)


if __name__ == "__main__":
    main()
