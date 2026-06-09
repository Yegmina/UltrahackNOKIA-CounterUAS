#!/usr/bin/env python3
"""Analyze raw USB endpoint captures from Thermal Live Debug.

The Android APK saves files under:

    Android/data/com.yegmina.thermallivedebug/files/thermal_live_debug_*/usb_probe/*.bin

This script scans those captures for 256x192 little-endian uint16 windows that
look like real thermal/IR planes and can write normalized PGM previews.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import statistics


WIDTH = 256
HEIGHT = 192
U16_BYTES = WIDTH * HEIGHT * 2
INFO_BYTES = WIDTH * 2 * 2
THERMOVUE_TEMP_OFFSET = U16_BYTES + INFO_BYTES


@dataclass(frozen=True)
class Candidate:
    path: Path
    offset: int
    min_value: int
    max_value: int
    mean_value: float
    sampled_unique: int
    score: float


def iter_input_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(sorted(path.rglob("*.bin")))
        elif path.is_file():
            files.append(path)
    return files


def read_u16_le(data: bytes, offset: int, index: int) -> int:
    byte_index = offset + index * 2
    return data[byte_index] | (data[byte_index + 1] << 8)


def analyze_window(data: bytes, path: Path, offset: int) -> Candidate | None:
    if offset < 0 or offset + U16_BYTES > len(data):
        return None

    sample_step = 67
    samples: list[int] = []
    for index in range(0, WIDTH * HEIGHT, sample_step):
        samples.append(read_u16_le(data, offset, index))

    min_value = min(samples)
    max_value = max(samples)
    sampled_unique = len(set(samples))
    value_range = max_value - min_value
    if value_range < 8 or sampled_unique < 4:
        return None

    mean_value = statistics.fmean(samples)
    score = value_range * 0.75 + sampled_unique * 8.0
    return Candidate(
        path=path,
        offset=offset,
        min_value=min_value,
        max_value=max_value,
        mean_value=mean_value,
        sampled_unique=sampled_unique,
        score=score,
    )


def candidate_offsets(length: int) -> list[int]:
    offsets = [0, THERMOVUE_TEMP_OFFSET, max(0, length - U16_BYTES)]
    max_offset = length - U16_BYTES
    if max_offset <= 0:
        return sorted(set(offsets))
    step = max(512, U16_BYTES // 16)
    offsets.extend(range(0, max_offset + 1, step))
    return sorted({offset for offset in offsets if 0 <= offset <= max_offset})


def find_candidates(path: Path, max_candidates: int) -> list[Candidate]:
    data = path.read_bytes()
    candidates: list[Candidate] = []
    for offset in candidate_offsets(len(data)):
        candidate = analyze_window(data, path, offset)
        if candidate is not None:
            candidates.append(candidate)
    candidates.sort(key=lambda item: item.score, reverse=True)
    return candidates[:max_candidates]


def write_pgm(candidate: Candidate, output_dir: Path) -> Path:
    data = candidate.path.read_bytes()
    values = [
        read_u16_le(data, candidate.offset, index)
        for index in range(WIDTH * HEIGHT)
    ]
    min_value = min(values)
    max_value = max(values)
    value_range = max(1, max_value - min_value)
    pixels = bytes(
        max(0, min(255, (value - min_value) * 255 // value_range))
        for value in values
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    stem = candidate.path.stem + f"_offset_{candidate.offset}"
    output = output_dir / f"{stem}.pgm"
    with output.open("wb") as handle:
        handle.write(f"P5\n{WIDTH} {HEIGHT}\n255\n".encode("ascii"))
        handle.write(pixels)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path, help="*.bin file or directory to scan")
    parser.add_argument("--max-candidates", type=int, default=5)
    parser.add_argument("--write-pgm", type=Path, help="Directory for normalized PGM previews")
    args = parser.parse_args()

    files = iter_input_files(args.paths)
    if not files:
        print("No .bin files found.")
        return 1

    found_any = False
    for path in files:
        candidates = find_candidates(path, args.max_candidates)
        print(f"\n{path} bytes={path.stat().st_size} candidates={len(candidates)}")
        for candidate in candidates:
            found_any = True
            print(
                "  "
                f"offset={candidate.offset} "
                f"min={candidate.min_value} "
                f"max={candidate.max_value} "
                f"mean={candidate.mean_value:.1f} "
                f"unique={candidate.sampled_unique} "
                f"score={candidate.score:.1f}"
            )
            if args.write_pgm:
                output = write_pgm(candidate, args.write_pgm)
                print(f"    wrote {output}")

    return 0 if found_any else 2


if __name__ == "__main__":
    raise SystemExit(main())
