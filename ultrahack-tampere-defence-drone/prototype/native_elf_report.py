#!/usr/bin/env python3
"""Generate a lightweight native ELF/string report for ThermoVue libraries.

The hackathon laptop does not always have NDK tools such as `readelf`,
`llvm-objdump`, or `strings`. This script keeps the native reverse-engineering
path reproducible by parsing ELF section symbols directly and scanning printable
strings from APK-embedded or extracted `.so` files.
"""

from __future__ import annotations

import argparse
import re
import struct
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


DEFAULT_PATTERNS = [
    "Java_",
    "native_",
    "video",
    "stream",
    "frame",
    "callback",
    "iruvc",
    "uvc",
    "usb",
    "libusb",
    "device",
    "fd",
    "ctrl",
    "AC020",
    "Ircam",
    "Ircmd",
    "tiny2c",
    "TINY2",
    "yft",
    "/dev",
    "IJPEG",
]


@dataclass(frozen=True)
class InputBlob:
    name: str
    data: bytes


def printable_strings(data: bytes, min_len: int = 5) -> list[str]:
    pattern = rb"[\x20-\x7e]{" + str(min_len).encode("ascii") + rb",}"
    return [m.group(0).decode("ascii", errors="replace") for m in re.finditer(pattern, data)]


def matching(values: Iterable[str], patterns: list[str], limit: int) -> list[str]:
    lowered = [(p, p.lower()) for p in patterns]
    hits: list[str] = []
    seen: set[str] = set()
    for value in values:
        low = value.lower()
        if any(p_low in low for _p, p_low in lowered):
            if value in seen:
                continue
            seen.add(value)
            hits.append(value)
            if len(hits) >= limit:
                break
    return hits


def read_c_string(blob: bytes, offset: int) -> str:
    if offset < 0 or offset >= len(blob):
        return ""
    end = blob.find(b"\0", offset)
    if end < 0:
        end = len(blob)
    return blob[offset:end].decode("utf-8", errors="replace")


def parse_elf_symbols(data: bytes) -> dict[str, list[str]]:
    """Return symbols from .dynsym/.symtab sections in a 32/64-bit little ELF."""
    if not data.startswith(b"\x7fELF"):
        return {}
    elf_class = data[4]
    endian = "<" if data[5] == 1 else ">"
    if endian != "<":
        return {}

    if elf_class == 2:
        header = struct.unpack_from(endian + "16sHHIQQQIHHHHHH", data, 0)
        e_shoff = header[6]
        e_shentsize = header[11]
        e_shnum = header[12]
        e_shstrndx = header[13]
        sh_fmt = endian + "IIQQQQIIQQ"
        sym_fmt = endian + "IBBHQQ"
        sym_size = 24
    elif elf_class == 1:
        header = struct.unpack_from(endian + "16sHHIIIIIHHHHHH", data, 0)
        e_shoff = header[6]
        e_shentsize = header[11]
        e_shnum = header[12]
        e_shstrndx = header[13]
        sh_fmt = endian + "IIIIIIIIII"
        sym_fmt = endian + "IIIBBH"
        sym_size = 16
    else:
        return {}

    sections: list[tuple[int, int, int, int, int, int, int, int, int, int]] = []
    for i in range(e_shnum):
        off = e_shoff + i * e_shentsize
        if off + struct.calcsize(sh_fmt) > len(data):
            return {}
        sections.append(struct.unpack_from(sh_fmt, data, off))
    if e_shstrndx >= len(sections):
        return {}

    shstr = sections[e_shstrndx]
    shstr_blob = data[shstr[4] : shstr[4] + shstr[5]]

    names: list[str] = []
    for section in sections:
        names.append(read_c_string(shstr_blob, section[0]))

    out: dict[str, list[str]] = {}
    for idx, (name, section) in enumerate(zip(names, sections)):
        sh_type = section[1]
        if name not in (".dynsym", ".symtab") and sh_type not in (2, 11):
            continue
        link = section[6]
        if link >= len(sections):
            continue
        str_section = sections[link]
        str_blob = data[str_section[4] : str_section[4] + str_section[5]]
        sym_blob = data[section[4] : section[4] + section[5]]
        entry_size = section[9] or sym_size
        symbols: list[str] = []
        for off in range(0, len(sym_blob) - entry_size + 1, entry_size):
            st_name = struct.unpack_from(endian + "I", sym_blob, off)[0]
            sym_name = read_c_string(str_blob, st_name)
            if sym_name:
                symbols.append(sym_name)
        out[name or f"section_{idx}"] = sorted(set(symbols))
    return out


def collect_inputs(paths: list[Path], abi: str) -> list[InputBlob]:
    blobs: list[InputBlob] = []
    for path in paths:
        if path.is_dir():
            for so in sorted(path.rglob("*.so")):
                blobs.append(InputBlob(str(so), so.read_bytes()))
        elif path.suffix.lower() == ".apk":
            with zipfile.ZipFile(path) as apk:
                for info in apk.infolist():
                    if info.filename.startswith(f"lib/{abi}/") and info.filename.endswith(".so"):
                        blobs.append(InputBlob(f"{path}!{info.filename}", apk.read(info)))
        elif path.suffix.lower() == ".so":
            blobs.append(InputBlob(str(path), path.read_bytes()))
    return blobs


def render_report(blobs: list[InputBlob], patterns: list[str], limit: int) -> str:
    lines = ["# Native ELF Report", ""]
    lines.append(f"Inputs scanned: {len(blobs)}")
    lines.append("")
    for blob in blobs:
        lines.append(f"## {blob.name}")
        lines.append("")
        lines.append(f"- bytes: {len(blob.data)}")
        symbols = parse_elf_symbols(blob.data)
        dynsym = symbols.get(".dynsym", [])
        symtab = symbols.get(".symtab", [])
        lines.append(f"- dynsym symbols: {len(dynsym)}")
        lines.append(f"- symtab symbols: {len(symtab)}")

        symbol_hits = matching(dynsym + symtab, patterns, limit)
        string_hits = matching(printable_strings(blob.data), patterns, limit)

        lines.append("")
        lines.append("### Symbol Hits")
        if symbol_hits:
            for value in symbol_hits:
                lines.append(f"- `{value}`")
        else:
            lines.append("- none")

        lines.append("")
        lines.append("### String Hits")
        if string_hits:
            for value in string_hits:
                safe = value.replace("`", "'")
                lines.append(f"- `{safe}`")
        else:
            lines.append("- none")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path, help="APK, .so, or directory")
    parser.add_argument("--abi", default="arm64-v8a")
    parser.add_argument("--pattern", action="append", dest="patterns", help="Extra substring to match")
    parser.add_argument("--limit", type=int, default=120)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    patterns = DEFAULT_PATTERNS + (args.patterns or [])
    report = render_report(collect_inputs(args.paths, args.abi), patterns, args.limit)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
