#!/usr/bin/env python3
"""Probe ThermoVue MP4 recordings for raw thermal side data.

ThermoVue still images are IJPEG files with raw 256x192 thermal planes. This
script answers a different question: did the video recorder mux any equivalent
raw/private payload into `.mp4` recordings, or is the file only encoded visual
video/audio?
"""

from __future__ import annotations

import argparse
import json
import struct
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable


CONTAINER_BOXES = {
    "moov",
    "trak",
    "mdia",
    "minf",
    "stbl",
    "edts",
    "dinf",
    "udta",
    "meta",
    "ilst",
}

RAW_MARKERS = [
    b"IJPEG",
    b"APP3",
    b"APP5",
    b"temp_u16",
    b"rawTemp",
    b"remapTemp",
    b"thermal",
    b"Thermal",
    b"infisense",
    b"uuid",
]


@dataclass(frozen=True)
class BoxInfo:
    path: str
    offset: int
    size: int
    type: str


@dataclass(frozen=True)
class SampleEntry:
    path: str
    offset: int
    type: str


def parse_boxes(data: bytes, start: int, end: int, prefix: str = "") -> list[BoxInfo]:
    boxes: list[BoxInfo] = []
    offset = start
    while offset + 8 <= end:
        size32, box_type_raw = struct.unpack_from(">I4s", data, offset)
        box_type = box_type_raw.decode("latin-1", errors="replace")
        header = 8
        if size32 == 1:
            if offset + 16 > end:
                break
            size = struct.unpack_from(">Q", data, offset + 8)[0]
            header = 16
        elif size32 == 0:
            size = end - offset
        else:
            size = size32
        if size < header or offset + size > end:
            break

        path = f"{prefix}/{box_type}" if prefix else box_type
        box = BoxInfo(path=path, offset=offset, size=size, type=box_type)
        boxes.append(box)

        child_start = offset + header
        if box_type == "meta":
            child_start += 4
        if box_type in CONTAINER_BOXES and child_start < offset + size:
            boxes.extend(parse_boxes(data, child_start, offset + size, path))
        offset += size
    return boxes


def parse_sample_entries(data: bytes, boxes: Iterable[BoxInfo]) -> list[SampleEntry]:
    entries: list[SampleEntry] = []
    for box in boxes:
        if box.type != "stsd":
            continue
        payload = box.offset + 8
        if payload + 8 > box.offset + box.size:
            continue
        entry_count = struct.unpack_from(">I", data, payload + 4)[0]
        cursor = payload + 8
        for _idx in range(entry_count):
            if cursor + 8 > box.offset + box.size:
                break
            entry_size, entry_type_raw = struct.unpack_from(">I4s", data, cursor)
            entry_type = entry_type_raw.decode("latin-1", errors="replace")
            if entry_size < 8 or cursor + entry_size > box.offset + box.size:
                break
            entries.append(SampleEntry(path=box.path, offset=cursor, type=entry_type))
            cursor += entry_size
    return entries


def find_tokens(data: bytes, markers: Iterable[bytes]) -> dict[str, list[int]]:
    result: dict[str, list[int]] = {}
    for marker in markers:
        positions: list[int] = []
        start = 0
        while True:
            found = data.find(marker, start)
            if found < 0:
                break
            positions.append(found)
            if len(positions) >= 20:
                break
            start = found + 1
        result[marker.decode("ascii", errors="replace")] = positions
    return result


def summarize(path: Path) -> dict[str, object]:
    data = path.read_bytes()
    boxes = parse_boxes(data, 0, len(data))
    sample_entries = parse_sample_entries(data, boxes)
    token_hits = find_tokens(data, RAW_MARKERS)
    top_level = [asdict(box) for box in boxes if "/" not in box.path]
    suspicious_hits = {
        token: positions
        for token, positions in token_hits.items()
        if positions and token not in {"uuid"}
    }
    has_private_uuid = bool(token_hits.get("uuid"))
    has_raw_markers = bool(suspicious_hits)
    return {
        "file": str(path),
        "bytes": len(data),
        "top_level_boxes": top_level,
        "sample_entries": [asdict(entry) for entry in sample_entries],
        "token_hits": token_hits,
        "has_private_uuid_box": has_private_uuid,
        "has_raw_thermal_markers": has_raw_markers,
        "conclusion": conclusion(sample_entries, has_private_uuid, has_raw_markers),
    }


def conclusion(
    sample_entries: list[SampleEntry],
    has_private_uuid: bool,
    has_raw_markers: bool,
) -> str:
    entry_types = {entry.type for entry in sample_entries}
    if has_raw_markers or has_private_uuid:
        return "MP4 contains private/raw-looking markers; inspect manually."
    if entry_types <= {"avc1", "mp4a"} and entry_types:
        return "No raw thermal markers found; file appears to be normal H.264/AAC media only."
    return "No raw thermal markers found; sample entries are not the expected ThermoVue H.264/AAC pair."


def render_markdown(summary: dict[str, object]) -> str:
    lines = [
        "# ThermoVue MP4 Probe",
        "",
        f"File: `{summary['file']}`",
        f"Bytes: `{summary['bytes']}`",
        "",
        f"Conclusion: **{summary['conclusion']}**",
        "",
        "## Sample Entries",
        "",
    ]
    entries = summary["sample_entries"]
    if entries:
        for entry in entries:  # type: ignore[assignment]
            lines.append(f"- `{entry['type']}` at offset `{entry['offset']}` path `{entry['path']}`")
    else:
        lines.append("- none")

    lines.extend(["", "## Top-Level Boxes", ""])
    for box in summary["top_level_boxes"]:  # type: ignore[assignment]
        lines.append(f"- `{box['type']}` offset `{box['offset']}` size `{box['size']}`")

    lines.extend(["", "## Raw Marker Scan", ""])
    for token, positions in summary["token_hits"].items():  # type: ignore[union-attr]
        if positions:
            rendered = ", ".join(str(pos) for pos in positions[:10])
        else:
            rendered = "none"
        lines.append(f"- `{token}`: {rendered}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mp4", type=Path)
    parser.add_argument("--out", type=Path, help="Write Markdown report")
    parser.add_argument("--json", type=Path, help="Write JSON summary")
    args = parser.parse_args()

    summary = summarize(args.mp4)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    markdown = render_markdown(summary)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(markdown, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
