#!/usr/bin/env python3
"""Extract ThermoVue/Infisense IJPEG thermal planes from a captured JPEG.

ThermoVue Pro saves a normal JPEG preview plus vendor APP segments. On the
Armor 28 Ultra Thermal sample inspected during the hackathon, APP2 contains an
IJPEG descriptor and APP3 contains, in order:

- 256x192 uint16 little-endian IR/raw plane
- 256x192 uint16 little-endian temperature/remap plane
- 1080x1440 RGBA display/visible plane

The script does not need Android libraries. It parses the JPEG marker stream,
splits APP3 by the descriptor sizes, and writes exact raw dumps plus optional
PNG previews for quick sanity checks.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


@dataclass
class PlaneDescriptor:
    name: str
    size: int
    data_format: int
    width: int
    height: int
    bit_num: int
    offset: int = 0


@dataclass
class IJpegDescriptor:
    sign: str
    version_raw: list[int]
    image_org_type: int
    image_disp_type: int
    pre_plane_header_hex: str
    planes: list[PlaneDescriptor]


def iter_jpeg_segments(data: bytes) -> Iterable[tuple[int, int, int, bytes]]:
    """Yield JPEG marker, marker offset, declared length, and payload bytes."""
    if not data.startswith(b"\xff\xd8"):
        raise ValueError("not a JPEG: missing SOI marker")

    pos = 2
    while pos < len(data):
        if data[pos] != 0xFF:
            raise ValueError(f"unexpected byte 0x{data[pos]:02x} at {pos}, expected marker")

        while pos < len(data) and data[pos] == 0xFF:
            pos += 1
        if pos >= len(data):
            break

        marker = data[pos]
        marker_pos = pos - 1
        pos += 1

        if marker == 0xDA:  # Start of Scan. The compressed preview follows.
            yield marker, marker_pos, 0, b""
            return
        if marker == 0xD9:  # End of Image.
            yield marker, marker_pos, 0, b""
            return
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:
            yield marker, marker_pos, 0, b""
            continue

        if pos + 2 > len(data):
            raise ValueError(f"truncated marker length at {marker_pos}")
        length = int.from_bytes(data[pos : pos + 2], "big")
        if length < 2:
            raise ValueError(f"invalid marker length {length} at {marker_pos}")

        payload_start = pos + 2
        payload_end = pos + length
        if payload_end > len(data):
            raise ValueError(f"marker 0x{marker:02x} at {marker_pos} extends past EOF")

        yield marker, marker_pos, length, data[payload_start:payload_end]
        pos = payload_end


def parse_plane(payload: bytes, base: int, name: str) -> PlaneDescriptor:
    # Native IJPEG stores image-size as uint64 little-endian followed by four
    # uint16 little-endian values: format, width, height, bit depth.
    size = int.from_bytes(payload[base : base + 8], "little")
    data_format, width, height, bit_num = struct.unpack_from("<HHHH", payload, base + 8)
    return PlaneDescriptor(
        name=name,
        size=size,
        data_format=data_format,
        width=width,
        height=height,
        bit_num=bit_num,
    )


def parse_ijpeg_descriptor(app2_payload: bytes) -> IJpegDescriptor:
    sign = app2_payload[4:12].rstrip(b"\0").decode("ascii", errors="replace")
    if sign != "IJPEG":
        raise ValueError("APP2 payload does not contain an IJPEG signature")

    planes = [
        parse_plane(app2_payload, 32, "ir_u16le"),
        parse_plane(app2_payload, 48, "temp_u16le"),
        parse_plane(app2_payload, 64, "rgba"),
    ]
    return IJpegDescriptor(
        sign=sign,
        version_raw=list(app2_payload[0:4]),
        image_org_type=app2_payload[12],
        image_disp_type=app2_payload[13],
        pre_plane_header_hex=app2_payload[:32].hex(" "),
        planes=planes,
    )


def uint16_stats(data: bytes) -> dict[str, float | int]:
    values = struct.unpack("<" + "H" * (len(data) // 2), data)
    return {
        "min": min(values),
        "max": max(values),
        "mean": round(sum(values) / len(values), 4),
        "first": list(values[:16]),
    }


def write_uint16_png(data: bytes, width: int, height: int, out_path: Path) -> None:
    from PIL import Image

    values = struct.unpack("<" + "H" * (len(data) // 2), data)
    lo = min(values)
    hi = max(values)
    span = max(1, hi - lo)
    pixels = bytes(max(0, min(255, int((v - lo) * 255 / span))) for v in values)
    Image.frombytes("L", (width, height), pixels).save(out_path)


def write_rgba_png(data: bytes, width: int, height: int, out_path: Path) -> None:
    from PIL import Image

    Image.frombytes("RGBA", (width, height), data).save(out_path)


def extract(input_path: Path, out_dir: Path, write_png: bool) -> dict:
    data = input_path.read_bytes()
    app2_ijpeg = None
    app3_chunks: list[bytes] = []
    marker_counts: dict[str, int] = {}
    sos_offset = None

    for marker, marker_pos, _length, payload in iter_jpeg_segments(data):
        marker_counts[f"0x{marker:02x}"] = marker_counts.get(f"0x{marker:02x}", 0) + 1
        if marker == 0xDA:
            sos_offset = marker_pos
            break
        if marker == 0xE2 and payload[4:12].rstrip(b"\0") == b"IJPEG":
            app2_ijpeg = payload
        elif marker == 0xE3:
            app3_chunks.append(payload)

    if app2_ijpeg is None:
        raise ValueError("no IJPEG APP2 descriptor found")
    descriptor = parse_ijpeg_descriptor(app2_ijpeg)
    app3_payload = b"".join(app3_chunks)

    expected_app3 = sum(plane.size for plane in descriptor.planes)
    if expected_app3 != len(app3_payload):
        raise ValueError(
            f"APP3 payload size mismatch: descriptor says {expected_app3}, "
            f"file contains {len(app3_payload)}"
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    stem = input_path.stem
    summary: dict = {
        "input": str(input_path),
        "input_size": len(data),
        "sos_offset": sos_offset,
        "marker_counts": marker_counts,
        "ijpeg": {},
        "outputs": {},
    }

    cursor = 0
    for plane in descriptor.planes:
        plane.offset = cursor
        raw = app3_payload[cursor : cursor + plane.size]
        cursor += plane.size

        if plane.bit_num == 16:
            raw_path = out_dir / f"{stem}.{plane.name}.{plane.width}x{plane.height}.u16le"
            raw_path.write_bytes(raw)
            plane_summary = {
                "raw": str(raw_path),
                "stats": uint16_stats(raw),
            }
            if write_png:
                png_path = out_dir / f"{stem}.{plane.name}.preview.png"
                write_uint16_png(raw, plane.width, plane.height, png_path)
                plane_summary["png"] = str(png_path)
        elif plane.bit_num == 32 and plane.size == plane.width * plane.height * 4:
            raw_path = out_dir / f"{stem}.{plane.name}.{plane.width}x{plane.height}.rgba"
            raw_path.write_bytes(raw)
            alpha_unique = sorted(set(raw[3::4]))
            plane_summary = {
                "raw": str(raw_path),
                "first_rgba": [list(raw[i : i + 4]) for i in range(0, min(32, len(raw)), 4)],
                "alpha_unique_sample": alpha_unique[:16],
                "alpha_unique_count": len(alpha_unique),
            }
            if write_png:
                png_path = out_dir / f"{stem}.{plane.name}.preview.png"
                write_rgba_png(raw, plane.width, plane.height, png_path)
                plane_summary["png"] = str(png_path)
        else:
            raw_path = out_dir / f"{stem}.{plane.name}.{plane.size}.bin"
            raw_path.write_bytes(raw)
            plane_summary = {"raw": str(raw_path)}

        summary["outputs"][plane.name] = plane_summary

    summary["ijpeg"] = asdict(descriptor)
    summary_path = out_dir / f"{stem}.ijpeg_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    summary["summary_json"] = str(summary_path)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="ThermoVue IJPEG/JPEG capture")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("prototype/logs/ijpeg_extract"),
        help="Directory for extracted raw planes and summaries",
    )
    parser.add_argument(
        "--no-png",
        action="store_true",
        help="Skip PNG preview rendering",
    )
    args = parser.parse_args()

    try:
        summary = extract(args.input, args.out_dir, write_png=not args.no_png)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
