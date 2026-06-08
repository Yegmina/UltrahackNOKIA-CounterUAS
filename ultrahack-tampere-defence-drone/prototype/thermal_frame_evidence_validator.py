"""Validate evidence that ThermoVue bridge output contains real thermal frames.

The bridge can produce several weak signals: non-null byte arrays, a frame count,
raw frame dumps, UDP receiver `.npy` files, and logcat/bridge logs. This tool
turns those into one explicit pass/fail report so we do not mistake allocated
buffers or stale data for true sensor access.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np


FRAME_COUNT_RE = re.compile(r"frameCount=([A-Za-z0-9_.:-]+)")
FIRST_FRAME_RE = re.compile(r"firstFrame=([A-Za-z0-9_.:-]+)")
RAW_RE = re.compile(r"rawTemp=len=(\d+)\s+checksum(?:256|1024)=0x([0-9a-fA-F]+)")
DUMP_RE = re.compile(r"frameDump\s+raw_temp\s+path=(\S+)\s+len=(\d+)")
UDP_RE = re.compile(r"udpThermalFrame sent .*rawBytes=(\d+)")


@dataclass
class Evidence:
    source: str
    passed: bool
    details: list[str]


def parse_int_like(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        pass
    match = re.search(r":(-?\d+)$", value)
    if match:
        return int(match.group(1))
    return None


def validate_log(path: Path, expected_bytes: int) -> Evidence:
    text = path.read_text(encoding="utf-8", errors="replace")
    frame_counts: list[int] = []
    first_flags: list[int] = []
    raw_lengths: list[int] = []
    raw_checksums: list[int] = []
    dump_lengths: list[int] = []
    udp_lengths: list[int] = []

    for line in text.splitlines():
        frame_match = FRAME_COUNT_RE.search(line)
        if frame_match:
            value = parse_int_like(frame_match.group(1))
            if value is not None:
                frame_counts.append(value)

        first_match = FIRST_FRAME_RE.search(line)
        if first_match:
            value = parse_int_like(first_match.group(1))
            if value is not None:
                first_flags.append(value)

        raw_match = RAW_RE.search(line)
        if raw_match:
            raw_lengths.append(int(raw_match.group(1)))
            raw_checksums.append(int(raw_match.group(2), 16))

        dump_match = DUMP_RE.search(line)
        if dump_match:
            dump_lengths.append(int(dump_match.group(2)))

        udp_match = UDP_RE.search(line)
        if udp_match:
            udp_lengths.append(int(udp_match.group(1)))

    max_frame = max(frame_counts) if frame_counts else 0
    max_first = max(first_flags) if first_flags else 0
    good_raw = any(length == expected_bytes and checksum != 0 for length, checksum in zip(raw_lengths, raw_checksums))
    good_dump = any(length == expected_bytes for length in dump_lengths)
    good_udp = any(length == expected_bytes for length in udp_lengths)
    passed = max_frame > 0 or max_first > 0 or good_raw or good_dump or good_udp

    details = [
        f"max_frame_count={max_frame}",
        f"max_first_frame_flag={max_first}",
        f"raw_entries={len(raw_lengths)} good_raw={good_raw}",
        f"raw_dumps={len(dump_lengths)} good_dump={good_dump}",
        f"udp_frames={len(udp_lengths)} good_udp={good_udp}",
    ]
    return Evidence(str(path), passed, details)


def validate_frame_array(
    source: str,
    frame: np.ndarray,
    min_unique: int,
    min_dynamic_range: int,
    min_nonzero_ratio: float,
) -> Evidence:
    flat = frame.reshape(-1)
    unique = int(np.unique(flat).size)
    frame_min = int(flat.min()) if flat.size else 0
    frame_max = int(flat.max()) if flat.size else 0
    dynamic_range = frame_max - frame_min
    nonzero_ratio = float(np.count_nonzero(flat) / max(1, flat.size))
    finite = bool(np.isfinite(frame.astype(np.float64)).all())
    passed = (
        finite
        and unique >= min_unique
        and dynamic_range >= min_dynamic_range
        and nonzero_ratio >= min_nonzero_ratio
    )
    details = [
        f"shape={tuple(frame.shape)}",
        f"dtype={frame.dtype}",
        f"min={frame_min}",
        f"max={frame_max}",
        f"dynamic_range={dynamic_range}",
        f"unique={unique}",
        f"nonzero_ratio={nonzero_ratio:.6f}",
    ]
    return Evidence(source, passed, details)


def load_raw_u16(path: Path, width: int, height: int) -> np.ndarray:
    raw = path.read_bytes()
    expected = width * height * 2
    if len(raw) != expected:
        raise ValueError(f"{path} has {len(raw)} bytes, expected {expected}")
    return np.frombuffer(raw, dtype="<u2").reshape(height, width)


def load_npy(path: Path, width: int, height: int) -> np.ndarray:
    frame = np.load(path)
    if frame.shape != (height, width):
        raise ValueError(f"{path} has shape {frame.shape}, expected {(height, width)}")
    return frame


def print_report(results: list[Evidence]) -> None:
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        print(f"{status} {result.source}")
        for detail in result.details:
            print(f"  {detail}")


def run_self_test(args: argparse.Namespace) -> int:
    y, x = np.mgrid[0 : args.height, 0 : args.width]
    frame = ((x * 13 + y * 7) % 2048 + 12000).astype("<u2")
    result = validate_frame_array(
        "self-test synthetic thermal frame",
        frame,
        args.min_unique,
        args.min_dynamic_range,
        args.min_nonzero_ratio,
    )
    print_report([result])
    return 0 if result.passed else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bridge-log", type=Path, action="append", default=[])
    parser.add_argument("--raw-bin", type=Path, action="append", default=[])
    parser.add_argument("--npy", type=Path, action="append", default=[])
    parser.add_argument("--width", type=int, default=256)
    parser.add_argument("--height", type=int, default=192)
    parser.add_argument("--min-unique", type=int, default=32)
    parser.add_argument("--min-dynamic-range", type=int, default=16)
    parser.add_argument("--min-nonzero-ratio", type=float, default=0.01)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return run_self_test(args)

    expected_bytes = args.width * args.height * 2
    results: list[Evidence] = []
    for path in args.bridge_log:
        results.append(validate_log(path, expected_bytes))
    for path in args.raw_bin:
        results.append(
            validate_frame_array(
                str(path),
                load_raw_u16(path, args.width, args.height),
                args.min_unique,
                args.min_dynamic_range,
                args.min_nonzero_ratio,
            )
        )
    for path in args.npy:
        results.append(
            validate_frame_array(
                str(path),
                load_npy(path, args.width, args.height),
                args.min_unique,
                args.min_dynamic_range,
                args.min_nonzero_ratio,
            )
        )

    if not results:
        parser.error("provide at least one --bridge-log, --raw-bin, --npy, or --self-test")

    print_report(results)
    return 0 if all(result.passed for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())
