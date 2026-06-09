#!/usr/bin/env python3
"""Inspect ThermoVue APK/native-lib dumps pulled from Thermal Live Debug.

This is a lightweight no-ADB triage tool. It does not replace jadx/apktool, but
it quickly answers whether a pulled `vendor_dump/` contains APKs, DEX files,
native libraries, and useful ThermoVue/Tiny2C/UVC strings to chase next.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import re
import zipfile


KEYWORDS = [
    "Ircam",
    "Ircmd",
    "Tiny2C",
    "tiny2c",
    "Uvc",
    "UVC",
    "USBMonitor",
    "UsbControlBlock",
    "DualUvc",
    "FrameCallback",
    "IIrFrame",
    "rawTemp",
    "remapTemp",
    "getRawTempData",
    "getRemapTempData",
    "initHandleEngine",
    "handleStartPreview",
    "startPreview",
    "Surface",
    "SurfaceTexture",
    "GPIO",
    "yft_tiny2c",
    "thermal",
    "temperature",
]


@dataclass(frozen=True)
class Hit:
    file: str
    keyword: str
    value: str


def strings_from_bytes(data: bytes, min_len: int = 5) -> list[str]:
    pattern = rb"[\x20-\x7e]{" + str(min_len).encode("ascii") + rb",}"
    return [match.group(0).decode("ascii", errors="replace") for match in re.finditer(pattern, data)]


def find_apks(paths: list[Path]) -> list[Path]:
    apks: list[Path] = []
    for path in paths:
        if path.is_dir():
            apks.extend(sorted(path.rglob("*.apk")))
        elif path.suffix.lower() == ".apk" and path.is_file():
            apks.append(path)
    return sorted(set(apks))


def find_native_libs(paths: list[Path]) -> list[Path]:
    libs: list[Path] = []
    for path in paths:
        if path.is_dir():
            libs.extend(sorted(path.rglob("*.so")))
        elif path.suffix.lower() == ".so" and path.is_file():
            libs.append(path)
    return sorted(set(libs))


def keyword_hits(name: str, strings: list[str], limit: int) -> list[Hit]:
    hits: list[Hit] = []
    seen: set[tuple[str, str]] = set()
    for value in strings:
        for keyword in KEYWORDS:
            if keyword.lower() in value.lower():
                key = (keyword, value)
                if key in seen:
                    continue
                seen.add(key)
                hits.append(Hit(name, keyword, value))
                if len(hits) >= limit:
                    return hits
    return hits


def inspect_apk(path: Path, per_file_limit: int) -> tuple[list[str], list[Hit]]:
    lines: list[str] = []
    hits: list[Hit] = []
    lines.append(f"APK: {path} bytes={path.stat().st_size}")
    with zipfile.ZipFile(path) as apk:
        infos = apk.infolist()
        dex_infos = [info for info in infos if info.filename.endswith(".dex")]
        lib_infos = [info for info in infos if info.filename.startswith("lib/") and info.filename.endswith(".so")]
        lines.append(f"  entries={len(infos)} dex={len(dex_infos)} embeddedLibs={len(lib_infos)}")
        for info in dex_infos:
            lines.append(f"  dex {info.filename} bytes={info.file_size}")
            data = apk.read(info)
            hits.extend(keyword_hits(f"{path}!{info.filename}", strings_from_bytes(data), per_file_limit))
        for info in lib_infos[:80]:
            lines.append(f"  lib {info.filename} bytes={info.file_size}")
            data = apk.read(info)
            hits.extend(keyword_hits(f"{path}!{info.filename}", strings_from_bytes(data), per_file_limit))
        if len(lib_infos) > 80:
            lines.append(f"  lib truncated remaining={len(lib_infos) - 80}")
    return lines, hits


def inspect_native_lib(path: Path, per_file_limit: int) -> tuple[list[str], list[Hit]]:
    lines = [f"SO: {path} bytes={path.stat().st_size}"]
    data = path.read_bytes()
    return lines, keyword_hits(str(path), strings_from_bytes(data), per_file_limit)


def render_report(paths: list[Path], per_file_limit: int) -> str:
    lines: list[str] = ["# Vendor Dump Inspection", ""]
    apks = find_apks(paths)
    libs = find_native_libs(paths)
    lines.append(f"Inputs: {', '.join(str(path) for path in paths)}")
    lines.append(f"APKs: {len(apks)}")
    lines.append(f"Native libs outside APKs: {len(libs)}")
    lines.append("")

    all_hits: list[Hit] = []
    for apk in apks:
        apk_lines, hits = inspect_apk(apk, per_file_limit)
        lines.extend(apk_lines)
        lines.append("")
        all_hits.extend(hits)

    for lib in libs:
        lib_lines, hits = inspect_native_lib(lib, per_file_limit)
        lines.extend(lib_lines)
        lines.append("")
        all_hits.extend(hits)

    counter = Counter(hit.keyword for hit in all_hits)
    lines.append("## Keyword Summary")
    for keyword, count in counter.most_common():
        lines.append(f"- {keyword}: {count}")
    if not counter:
        lines.append("- no keyword hits")
    lines.append("")

    lines.append("## Hits")
    for hit in all_hits[:500]:
        safe_value = hit.value.replace("\n", " ").replace("\r", " ")
        lines.append(f"- `{hit.keyword}` in `{hit.file}`: `{safe_value}`")
    if len(all_hits) > 500:
        lines.append(f"- truncated remaining hits: {len(all_hits) - 500}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path, help="vendor_dump directory, APK, or .so")
    parser.add_argument("--per-file-limit", type=int, default=80)
    parser.add_argument("--out", type=Path, help="Optional markdown report path")
    args = parser.parse_args()

    report = render_report(args.paths, args.per_file_limit)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
