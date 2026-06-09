"""Receive raw thermal frames from the privileged ThermoVue bridge.

The Android bridge sends one thermal frame as several UDP datagrams:

    YEGMINA_THERMAL_RAW_V1 frame=<id> chunk=<i> chunks=<n> offset=<b> total=<t>\n
    <raw uint16 bytes>

The in-process ThermoVue bridge can also send optional V2 payloads:

    YEGMINA_THERMAL_FRAME_V2 frame=<id> kind=<kind> ... format=<format>\n
    <payload bytes>

This receiver reassembles the chunks and visualizes the 256x192 thermal plane.
It is intentionally tolerant because the first privileged build will be used as
an integration probe during the hackathon.
"""

from __future__ import annotations

import argparse
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np


MAGIC = b"YEGMINA_THERMAL_RAW_V1 "
MAGIC_V2 = b"YEGMINA_THERMAL_FRAME_V2 "


@dataclass
class PartialFrame:
    total: int
    chunks: int
    data: bytearray
    protocol: str = "v1"
    kind: str = "temp_u16le"
    width: int = 0
    height: int = 0
    fmt: str = ""
    seen: set[int] = field(default_factory=set)
    updated_at: float = field(default_factory=time.time)


@dataclass
class ParsedPacket:
    protocol: str
    frame_id: str
    kind: str
    chunk: int
    chunks: int
    offset: int
    total: int
    payload: bytes
    width: int = 0
    height: int = 0
    fmt: str = ""


def parse_packet(packet: bytes) -> ParsedPacket | None:
    if packet.startswith(MAGIC):
        magic = MAGIC
        protocol = "v1"
        default_kind = "temp_u16le"
    elif packet.startswith(MAGIC_V2):
        magic = MAGIC_V2
        protocol = "v2"
        default_kind = "unknown"
    else:
        return None

    try:
        header, payload = packet.split(b"\n", 1)
    except ValueError:
        return None

    fields: dict[str, str] = {}
    for part in header[len(magic) :].decode("ascii", errors="replace").split():
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        fields[key] = value

    required = ("frame", "chunk", "chunks", "offset", "total")
    if any(key not in fields for key in required):
        return None

    return ParsedPacket(
        protocol=protocol,
        frame_id=fields["frame"],
        kind=fields.get("kind", default_kind),
        chunk=int(fields["chunk"]),
        chunks=int(fields["chunks"]),
        offset=int(fields["offset"]),
        total=int(fields["total"]),
        payload=payload,
        width=int(fields.get("width", "0")),
        height=int(fields.get("height", "0")),
        fmt=fields.get("format", ""),
    )


def thermal_to_preview(frame: np.ndarray) -> np.ndarray:
    lo, hi = np.percentile(frame, [2, 98])
    if hi <= lo:
        hi = lo + 1
    preview = np.clip((frame.astype(np.float32) - lo) * 255.0 / (hi - lo), 0, 255)
    return preview.astype(np.uint8)


def show_frame(frame: np.ndarray, scale: int, title: str) -> None:
    try:
        import cv2
    except ImportError:
        print(
            f"{title}: min={int(frame.min())} max={int(frame.max())} "
            f"mean={float(frame.mean()):.1f}"
        )
        return

    preview = thermal_to_preview(frame)
    preview = cv2.applyColorMap(preview, cv2.COLORMAP_INFERNO)
    if scale > 1:
        preview = cv2.resize(
            preview,
            (frame.shape[1] * scale, frame.shape[0] * scale),
            interpolation=cv2.INTER_NEAREST,
        )
    cv2.imshow(title, preview)
    cv2.waitKey(1)


def save_frame(save_dir: Path | None, frame_id: str, frame: np.ndarray) -> None:
    if save_dir is None:
        return
    save_dir.mkdir(parents=True, exist_ok=True)
    safe_id = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in frame_id)
    np.save(save_dir / f"thermal_{safe_id}.npy", frame)


def save_payload(save_dir: Path | None, frame_id: str, kind: str, payload: bytes) -> None:
    if save_dir is None:
        return
    save_dir.mkdir(parents=True, exist_ok=True)
    safe_id = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in frame_id)
    safe_kind = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in kind)
    (save_dir / f"thermal_{safe_id}_{safe_kind}.bin").write_bytes(payload)


def frame_stats(label: str, frame: np.ndarray, completed: int) -> str:
    return (
        f"{label} complete #{completed}: "
        f"min={int(frame.min())} max={int(frame.max())} "
        f"mean={float(frame.mean()):.1f}"
    )


def receive(args: argparse.Namespace) -> None:
    bind = (args.host, args.port)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(bind)
    sock.settimeout(1.0)
    print(f"Listening on udp://{args.host}:{args.port}")

    partials: dict[str, PartialFrame] = {}
    completed = 0
    expected_total = args.width * args.height * 2

    while True:
        try:
            packet, sender = sock.recvfrom(args.max_packet)
        except socket.timeout:
            now = time.time()
            stale = [
                key for key, value in partials.items() if now - value.updated_at > args.stale_seconds
            ]
            for key in stale:
                del partials[key]
            continue

        parsed = parse_packet(packet)
        if parsed is None:
            print(f"Ignoring unknown packet from {sender}, bytes={len(packet)}")
            continue

        frame_id = parsed.frame_id
        chunk = parsed.chunk
        chunks = parsed.chunks
        offset = parsed.offset
        total = parsed.total
        payload = parsed.payload
        if total != expected_total:
            if parsed.kind not in {"thermovue_raw_packet", "raw_packet"}:
                print(f"Frame {frame_id}: unexpected total={total}, expected={expected_total}")
        if chunk < 0 or chunk >= chunks or offset < 0 or offset + len(payload) > total:
            print(f"Frame {frame_id}: invalid chunk={chunk} offset={offset} bytes={len(payload)}")
            continue

        key = f"{parsed.protocol}:{frame_id}:{parsed.kind}"
        partial = partials.get(key)
        if partial is None:
            partial = PartialFrame(
                total=total,
                chunks=chunks,
                data=bytearray(total),
                protocol=parsed.protocol,
                kind=parsed.kind,
                width=parsed.width,
                height=parsed.height,
                fmt=parsed.fmt,
            )
            partials[key] = partial
        partial.data[offset : offset + len(payload)] = payload
        partial.seen.add(chunk)
        partial.updated_at = time.time()

        if len(partial.seen) != partial.chunks:
            continue

        raw = bytes(partial.data)
        del partials[key]
        completed += 1

        width = partial.width or args.width
        height = partial.height or args.height
        plane_bytes = width * height * 2
        label = f"Frame {frame_id} kind={partial.kind}"

        if partial.kind in {"thermovue_raw_packet", "raw_packet"}:
            save_payload(args.save_dir, frame_id, partial.kind, raw)
            temp_offset = args.width * args.height * 2 + 1024
            temp_end = temp_offset + expected_total
            print(f"{label} complete #{completed}: raw_packet_bytes={len(raw)}")
            if len(raw) >= temp_end:
                frame = np.frombuffer(raw[temp_offset:temp_end], dtype="<u2").reshape(
                    args.height, args.width
                )
                print(frame_stats(f"{label} temp", frame, completed))
                save_frame(args.save_dir, f"{frame_id}_temp_from_packet", frame)
                if not args.no_window:
                    show_frame(frame, args.scale, "ThermoVue packet temp")
            continue

        if len(raw) == plane_bytes and (partial.fmt in {"", "u16le"} or partial.kind.endswith("u16le")):
            frame = np.frombuffer(raw, dtype="<u2").reshape(height, width)
            print(frame_stats(label, frame, completed))
            save_frame(args.save_dir, f"{frame_id}_{partial.kind}", frame)
            if not args.no_window:
                show_frame(frame, args.scale, f"ThermoVue {partial.kind}")
        else:
            print(
                f"{label} complete #{completed}: bytes={len(raw)} "
                f"format={partial.fmt or 'unknown'}"
            )
            save_payload(args.save_dir, frame_id, partial.kind, raw)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=25000)
    parser.add_argument("--width", type=int, default=256)
    parser.add_argument("--height", type=int, default=192)
    parser.add_argument("--scale", type=int, default=3)
    parser.add_argument("--max-packet", type=int, default=2048)
    parser.add_argument("--stale-seconds", type=float, default=3.0)
    parser.add_argument("--save-dir", type=Path)
    parser.add_argument("--no-window", action="store_true")
    receive(parser.parse_args())


if __name__ == "__main__":
    main()
