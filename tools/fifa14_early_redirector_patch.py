#!/usr/bin/env python3
"""Redirect FIFA 14's Blaze hostnames before the title starts executing."""

from __future__ import annotations

import argparse
import re
import select
import socket
import sys
import time


TARGET = b"192.0.2.35\0"
HOSTS = (
    (0x8210B238, b"gosredirector.ea.com\0"),
    (0x8210B250, b"gosredirector.scert.ea.com\0"),
    (0x8210B26C, b"gosredirector.stest.ea.com\0"),
    (0x8210B288, b"gosredirector.online.ea.com\0"),
)


class Connection:
    def __init__(self, host: str, timeout: float = 8):
        self.sock = socket.create_connection((host, 730), timeout=timeout)
        self.reader = self.sock.makefile("rb")
        if not self.line().startswith("201-"):
            raise RuntimeError("Unexpected XBDM banner")

    def line(self) -> str:
        raw = self.reader.readline()
        if not raw:
            raise EOFError("XBDM closed the connection")
        return raw.decode("ascii", "replace").rstrip("\r\n")

    def command(self, command: str, expected: int = 200) -> str:
        self.sock.sendall((command + "\r\n").encode("ascii"))
        while True:
            response = self.line()
            match = re.match(r"^(\d{3})[- ]", response)
            if match:
                if int(match.group(1)) != expected:
                    raise RuntimeError(f"{command}: {response}")
                return response

    def multiline(self, command: str) -> list[str]:
        self.sock.sendall((command + "\r\n").encode("ascii"))
        status = self.line()
        if not status.startswith("202-"):
            raise RuntimeError(f"{command}: {status}")
        result = []
        while True:
            line = self.line()
            if line == ".":
                return result
            result.append(line)

    def read(self, address: int, length: int) -> bytes:
        text = "".join(
            self.multiline(f"getmem addr=0x{address:08X} length=0x{length:X}")
        )
        if not re.fullmatch(r"[0-9A-Fa-f]+", text) or len(text) != length * 2:
            raise RuntimeError(f"Invalid memory response at 0x{address:08X}")
        return bytes.fromhex(text)

    # Same limit, same reason as `Xbdm.write` in fifa14_plain_send_hook: XBDM
    # parses a command into a fixed buffer, setmem spends two hex characters
    # per byte, and a long enough line is refused with a bare `446-` that says
    # nothing about length. This is the connection the *launcher* uses, so it
    # is the one that failed when the connect stub reached 252 bytes.
    CHUNK = 0x80

    def write(self, address: int, data: bytes) -> None:
        for offset in range(0, max(len(data), 1), self.CHUNK):
            piece = data[offset : offset + self.CHUNK]
            self.command(
                f"setmem addr=0x{address + offset:08X} data={piece.hex().upper()}"
            )

    def close(self) -> None:
        self.reader.close()
        self.sock.close()


def padded_target(size: int) -> bytes:
    if len(TARGET) > size:
        raise RuntimeError("Target address does not fit redirector string slot")
    return TARGET + b"\0" * (size - len(TARGET))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()

    notify = Connection(args.host)
    control: Connection | None = None
    stopped = False
    try:
        notify.command(
            'debugger connect override name="FIFARedirectEarly" user="CodexMac"'
        )
        notify.command("notify reconnectport=1", expected=205)
        control = Connection(args.host)

        print("Waiting for default.xex to load. Launch FIFA 14 now.", flush=True)
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            readable, _, _ = select.select([notify.sock], [], [], 1)
            if not readable:
                continue
            event = notify.line()
            lowered = event.lower()
            if "modload" not in lowered or 'name="default.xex"' not in lowered:
                continue

            print(f"Module event: {event}", flush=True)
            control.command("stop")
            stopped = True

            for address, original in HOSTS:
                replacement = padded_target(len(original))
                before = control.read(address, len(original))
                print(f"0x{address:08X} before: {before!r}")
                if before == original:
                    control.write(address, replacement)
                elif before != replacement:
                    raise RuntimeError(
                        f"Unexpected bytes at 0x{address:08X}: {before!r}"
                    )
                if control.read(address, len(original)) != replacement:
                    raise RuntimeError(
                        f"Verification failed at 0x{address:08X}"
                    )

            print("Verified: redirector patched before title execution.")
            control.command("go")
            stopped = False
            print("Execution resumed. Wait at the FIFA main menu.")
            return 0

        raise TimeoutError("default.xex modload event was not observed")
    finally:
        if control is not None:
            if stopped:
                try:
                    control.command("go")
                    print("Execution resumed during cleanup.")
                except Exception:
                    pass
            control.close()
        notify.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted; cleanup attempted.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as error:
        print(f"\nError: {error}", file=sys.stderr)
        raise SystemExit(1)
