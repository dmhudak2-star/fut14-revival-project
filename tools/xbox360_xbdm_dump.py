#!/usr/bin/env python3
"""Read mapped Xbox 360 module sections through XBDM's textual getmem command."""

from __future__ import annotations

import argparse
import socket
import sys
from pathlib import Path


FIFA14_SECTIONS = {
    "rdata": (0x82000400, 0x00328DB4),
    "pdata": (0x82329200, 0x0009E1E0),
    "text": (0x823D0000, 0x018B4838),
    "data": (0x83C90000, 0x001FAED8),
    "edata": (0x83E90000, 0x00006BC7),
    "idata": (0x83EA0000, 0x00000680),
    "xbld": (0x83EB0000, 0x00000170),
    "reloc": (0x83EB0200, 0x001DD69C),
}

POWDLLZF_SECTIONS = {
    "rdata": (0x89700400, 0x000107B4),
    "pdata": (0x89710C00, 0x00004AF8),
    "text": (0x89720000, 0x000931C0),
    "data": (0x897C0000, 0x00006FAC),
    "edata": (0x897D0000, 0x00000073),
    "idata": (0x897E0000, 0x000000F4),
    "xbld": (0x897F0000, 0x00000060),
    "reloc": (0x897F0200, 0x0000B264),
}

MODULES = {
    "default": FIFA14_SECTIONS,
    "powdllzf": POWDLLZF_SECTIONS,
}


class Xbdm:
    def __init__(self, host: str, port: int = 730, timeout: float = 10.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        self.reader = self.sock.makefile("rb")
        banner = self._line()
        if not banner.startswith(b"201-"):
            raise RuntimeError(f"Unexpected XBDM banner: {banner!r}")

    def _line(self) -> bytes:
        line = self.reader.readline()
        if not line:
            raise EOFError("XBDM closed the connection")
        return line.rstrip(b"\r\n")

    def read_memory(self, address: int, length: int) -> bytes:
        command = f"getmem addr=0x{address:08X} length=0x{length:X}\r\n"
        self.sock.sendall(command.encode("ascii"))
        status = self._line()
        if not status.startswith(b"202-"):
            raise RuntimeError(
                f"getmem failed at 0x{address:08X} ({length:#x} bytes): "
                f"{status.decode('ascii', 'replace')}"
            )

        encoded = bytearray()
        while True:
            line = self._line()
            if line == b".":
                break
            encoded.extend(line.strip())

        try:
            data = bytes.fromhex(encoded.decode("ascii"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise RuntimeError(f"Invalid hexadecimal response at 0x{address:08X}") from exc
        if len(data) != length:
            raise RuntimeError(
                f"Short read at 0x{address:08X}: expected {length:#x}, got {len(data):#x}"
            )
        return data

    def close(self) -> None:
        self.reader.close()
        self.sock.close()


def dump_section(
    xbdm: Xbdm,
    output: Path,
    name: str,
    address: int,
    size: int,
    chunk_size: int,
    prefix: str,
) -> None:
    target = output / f"{prefix}.{name}.bin"
    completed = target.stat().st_size if target.exists() else 0
    if completed > size:
        raise RuntimeError(f"{target} is larger than the expected section size")

    mode = "ab" if completed else "wb"
    with target.open(mode) as stream:
        offset = completed
        while offset < size:
            amount = min(chunk_size, size - offset)
            data = xbdm.read_memory(address + offset, amount)
            stream.write(data)
            stream.flush()
            offset += amount
            percent = offset * 100.0 / size
            print(
                f"\r{name:>6}: {offset:#010x}/{size:#010x} ({percent:6.2f}%)",
                end="",
                flush=True,
            )
    print()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host", help="Xbox 360 IP address")
    parser.add_argument(
        "--module",
        choices=tuple(MODULES),
        default="default",
        help="Mapped module whose sections should be dumped",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path.home() / "Desktop" / "fifa14_xbdm_dump",
    )
    parser.add_argument(
        "--sections",
        nargs="+",
        choices=tuple(FIFA14_SECTIONS),
        default=["rdata", "text", "data"],
    )
    parser.add_argument("--chunk-size", type=lambda value: int(value, 0), default=0x1000)
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    client = Xbdm(args.host)
    try:
        sections = MODULES[args.module]
        for section in args.sections:
            address, size = sections[section]
            dump_section(
                client,
                args.output,
                section,
                address,
                size,
                args.chunk_size,
                args.module,
            )
    finally:
        client.close()
    print(f"Dump complete: {args.output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted; rerun the same command to resume.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as error:
        print(f"\nError: {error}", file=sys.stderr)
        raise SystemExit(1)
