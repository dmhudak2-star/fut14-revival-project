#!/usr/bin/env python3
"""Capture and decode an Xbox 360 framebuffer through XBDM."""

from __future__ import annotations

import argparse
import binascii
import re
import socket
import struct
import zlib
from pathlib import Path


def read_exact(file, length: int) -> bytes:
    data = bytearray()
    while len(data) < length:
        chunk = file.read(length - len(data))
        if not chunk:
            raise EOFError(
                f"Screenshot ended at {len(data):#x}/{length:#x}"
            )
        data.extend(chunk)
    return bytes(data)


def parse_header(line: str) -> dict[str, int]:
    fields = {
        key.lower(): int(value, 0)
        for key, value in re.findall(
            r"([A-Za-z_]+)=(0x[0-9A-Fa-f]+|\d+)", line
        )
    }
    aliases = {
        "framebuffersize": "size",
        "buffer_size": "size",
        "offsetx": "offset_x",
        "offsety": "offset_y",
    }
    for source, target in aliases.items():
        if source in fields:
            fields[target] = fields[source]
    for required in ("pitch", "width", "height"):
        if required not in fields:
            raise RuntimeError(f"Missing {required} in screenshot header: {line}")
    fields.setdefault("offset_x", 0)
    fields.setdefault("offset_y", 0)
    fields.setdefault("size", fields["pitch"] * fields["height"])
    return fields


def tiled_combine(
    outer_inner_bytes: int, bank: int, pipe: int, y_lsb: int
) -> int:
    return (
        (y_lsb << 4)
        | (pipe << 6)
        | (bank << 11)
        | (outer_inner_bytes & 0b1111)
        | (((outer_inner_bytes >> 4) & 1) << 5)
        | (((outer_inner_bytes >> 5) & 0b111) << 8)
        | ((outer_inner_bytes >> 8) << 12)
    )


def tiled_xenia(
    x: int,
    y: int,
    width: int,
    log_bpp: int,
    bank_xor: int = 0,
    pipe_xor: int = 0,
) -> int:
    pitch_aligned = (width + 31) & ~31
    outer_blocks = (
        ((y >> 5) * (pitch_aligned >> 5) + (x >> 5)) << 6
    )
    inner_blocks = (((y >> 1) & 0b111) << 3) | (x & 0b111)
    outer_inner_bytes = (outer_blocks | inner_blocks) << log_bpp
    bank = ((y >> 4) & 1) ^ (bank_xor & 1)
    pipe = (
        ((x >> 3) & 0b11) ^ (((y >> 3) & 1) << 1) ^ (pipe_xor & 3)
    )
    return tiled_combine(outer_inner_bytes, bank, pipe, y & 1)


def decode_bgrx_tiled(
    raw: bytes,
    *,
    pitch: int,
    width: int,
    height: int,
    offset_x: int,
    offset_y: int,
    crop_right: int,
) -> tuple[int, int, bytes]:
    bytes_per_pixel = 4
    framebuffer_width = pitch // bytes_per_pixel
    out_width = max(1, width - crop_right)
    rgb = bytearray(out_width * height * 3)
    destination = 0
    for y in range(height):
        for x in range(out_width):
            source = tiled_xenia(
                offset_x + x,
                offset_y + y,
                framebuffer_width,
                2,
            )
            if source + 4 <= len(raw):
                blue, green, red, _ = raw[source : source + 4]
            else:
                red = green = blue = 0
            rgb[destination : destination + 3] = bytes(
                (red, green, blue)
            )
            destination += 3
    return out_width, height, bytes(rgb)


def png_chunk(name: bytes, data: bytes) -> bytes:
    body = name + data
    return (
        struct.pack(">I", len(data))
        + body
        + struct.pack(">I", binascii.crc32(body) & 0xFFFFFFFF)
    )


def write_png(path: Path, width: int, height: int, rgb: bytes) -> None:
    stride = width * 3
    scanlines = b"".join(
        b"\0" + rgb[y * stride : (y + 1) * stride]
        for y in range(height)
    )
    png = (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(
            b"IHDR",
            struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0),
        )
        + png_chunk(b"IDAT", zlib.compress(scanlines, 6))
        + png_chunk(b"IEND", b"")
    )
    path.write_bytes(png)


def capture(host: str) -> tuple[dict[str, int], bytes]:
    with socket.create_connection((host, 730), timeout=8) as sock:
        sock.settimeout(20)
        file = sock.makefile("rwb", buffering=0)
        greeting = file.readline().decode("ascii", "replace").strip()
        if not greeting.startswith("201"):
            raise RuntimeError(f"Unexpected XBDM greeting: {greeting}")
        file.write(b"screenshot\r\n")
        status = file.readline().decode("ascii", "replace").strip()
        if not status.startswith("203"):
            raise RuntimeError(f"Screenshot request failed: {status}")
        header_line = file.readline().decode("ascii", "replace").strip()
        header = parse_header(header_line)
        return header, read_exact(file, header["size"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--no-auto-crop", action="store_true",
        help="Keep the known corrupted right-edge columns.",
    )
    args = parser.parse_args()

    header, raw = capture(args.host)
    if header["pitch"] // header["width"] != 4:
        raise RuntimeError("Only 32-bit XBDM framebuffers are supported")
    crop_right = (
        0
        if args.no_auto_crop
        else round(header["width"] * 0.02)
    )
    width, height, rgb = decode_bgrx_tiled(
        raw,
        pitch=header["pitch"],
        width=header["width"],
        height=header["height"],
        offset_x=header["offset_x"],
        offset_y=header["offset_y"],
        crop_right=crop_right,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_png(args.output, width, height, rgb)
    print(f"Screenshot saved: {args.output.resolve()}")
    print(
        f"{width}x{height}, pitch={header['pitch']}, "
        f"format=0x{header.get('format', 0):08X}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
