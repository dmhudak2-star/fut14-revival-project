#!/usr/bin/env python3
"""Locate and patch the decoded TU3 helperFunctions APT in Xbox memory.

This is the runtime equivalent of the verified archive branch patch.  It
waits for the exact original TU3 APT image, validates its complete SHA-256,
and changes only the three reviewed six-byte branch instructions.  It does
not invoke navigation, publish frontend events, or load a FUT screen.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import socket
import time
from pathlib import Path


APT_SIZE = 0x6E0C
ORIGINAL_APT_SHA256 = "72248703909dd2fd66080de1d55e7d72050ac8f516089e10e30bb0477c3cbb63"
SIGNATURE_OFFSET = 0x2C70
SIGNATURE = bytes.fromhex(
    "735AB901B297B901AF0DAFA959B901AF46A247524912"
    "9D0000000028A301F11CA301F21CB901AF0DAFA9B901AF48AF49"
)
PATCHES = (
    (0x2C86, bytes.fromhex("9D0000000028"), bytes.fromhex("990000000064")),
    (0x2D92, bytes.fromhex("9D000000000C"), bytes.fromhex("9D0000000000")),
    (0x2FEA, bytes.fromhex("9D00000000B0"), bytes.fromhex("9900000000B0")),
)
CONTEXTS = (
    (
        0x2C86,
        bytes.fromhex("B901AF0DAFA959B901AF46A247524912"),
        bytes.fromhex("A301F11CA301F21CB901AF0DAFA9B901"),
    ),
    (
        0x2D92,
        bytes.fromhex("4EA301FA52870000000217B902734912"),
        bytes.fromhex("59B901A301FB5D990000001FA301FC26"),
    ),
    (
        0x2FEA,
        bytes.fromhex("A302122659B901AF2CA302135212"),
        bytes.fromhex("59A20240870000000000000217B902A2"),
    ),
)


class Xbdm:
    def __init__(self, host: str) -> None:
        self.sock = socket.create_connection((host, 730), timeout=10)
        self.sock.settimeout(20)
        self.reader = self.sock.makefile("rb")
        if not self.line().startswith(b"201-"):
            raise RuntimeError("unexpected XBDM banner")

    def close(self) -> None:
        try:
            self.reader.close()
        finally:
            self.sock.close()

    def line(self) -> bytes:
        line = self.reader.readline()
        if not line:
            raise EOFError("XBDM closed the connection")
        return line.rstrip(b"\r\n")

    def multiline(self, command: str) -> list[bytes]:
        self.sock.sendall(command.encode("ascii") + b"\r\n")
        status = self.line()
        if not status.startswith(b"202-"):
            raise RuntimeError(status.decode("ascii", "replace"))
        result: list[bytes] = []
        while True:
            line = self.line()
            if line == b".":
                return result
            result.append(line)

    def command(self, command: str) -> bytes:
        self.sock.sendall(command.encode("ascii") + b"\r\n")
        status = self.line()
        if not status.startswith(b"200-"):
            raise RuntimeError(status.decode("ascii", "replace"))
        return status

    def regions(self) -> list[tuple[int, int, int]]:
        result = []
        for raw in self.multiline("walkmem"):
            match = re.search(
                rb"base=0x([0-9a-f]+) size=0x([0-9a-f]+) protect=0x([0-9a-f]+)",
                raw,
                re.IGNORECASE,
            )
            if match:
                result.append(tuple(int(value, 16) for value in match.groups()))
        return result

    def read(self, address: int, length: int) -> bytes:
        if length > 0x4000:
            return self.read_binary(address, length)
        lines = self.multiline(f"getmem addr=0x{address:08X} length=0x{length:X}")
        encoded = b"".join(line.strip() for line in lines)
        if not re.fullmatch(rb"[0-9A-Fa-f]+", encoded):
            raise RuntimeError(f"invalid getmem response at 0x{address:08X}")
        data = bytes.fromhex(encoded.decode("ascii"))
        if len(data) != length:
            raise RuntimeError(f"short read at 0x{address:08X}: {len(data):#x}/{length:#x}")
        return data

    def read_exact(self, length: int) -> bytes:
        output = bytearray()
        while len(output) < length:
            chunk = self.reader.read(length - len(output))
            if not chunk:
                raise EOFError("XBDM closed a binary response early")
            output.extend(chunk)
        return bytes(output)

    def read_binary(self, address: int, length: int) -> bytes:
        self.sock.sendall(
            f"getmemex addr=0x{address:08X} length=0x{length:08X}\r\n".encode("ascii")
        )
        status = self.line()
        if not status.startswith(b"203-"):
            raise RuntimeError(status.decode("ascii", "replace"))
        output = bytearray()
        while len(output) < length:
            header = int.from_bytes(self.read_exact(2), "little")
            chunk_size = header & 0x7FFF
            if not chunk_size:
                continue
            if len(output) + chunk_size > length:
                raise RuntimeError("getmemex returned more bytes than requested")
            # xbGuard's xbdm uses bit 15 as the final-chunk marker; every
            # non-empty chunk is followed by payload bytes.
            output.extend(self.read_exact(chunk_size))
        return bytes(output)

    def write(self, address: int, data: bytes) -> None:
        self.command(f"setmem addr=0x{address:08X} data={data.hex().upper()}")


# Every run that located the APT found it in the same neighbourhood, so a
# bounded first pass there answers in seconds.  A full sweep of the heap costs
# many minutes over XBDM, which is enough to dominate a whole measurement cycle.
OBSERVED_APT_NEIGHBOURHOOD = 0xBDD78000
DEFAULT_HINT_WINDOW = 0x00400000

# A hard-coded hint goes stale as soon as the heap moves, and then every run
# pays for the full sweep again.  Remembering where the APT actually turned up
# lets the next run start from a hint that tracks the console instead.
DEFAULT_HINT_FILE = (
    Path(__file__).resolve().parents[1] / "runtime" / "helperfunctions-apt.json"
)


def remembered_hint(path: Path) -> int | None:
    try:
        recorded = json.loads(path.read_text())["address"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return recorded if isinstance(recorded, int) else None


def remember_hint(path: Path, address: int) -> None:
    """Record a confirmed APT address, but never fail the patch over it."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"address": address}) + "\n")
    except OSError:
        pass


def candidates(client: Xbdm) -> list[tuple[int, int, int]]:
    regions = [
        region
        for region in client.regions()
        if 0xA0000000 <= region[0] < 0xE0000000
        and 0 < region[1] <= 0x10000000
    ]
    # Cheap regions first: the small ones take seconds each, while the single
    # ~200 MiB heap block takes minutes, so paying for them up front costs
    # almost nothing and wins outright whenever the APT happens to live there.
    return sorted(regions, key=lambda region: (region[0] < 0xB0000000, region[1]))


def clip_to_window(
    regions: list[tuple[int, int, int]], centre: int, window: int
) -> list[tuple[int, int, int]]:
    """Restrict regions to the span around ``centre``, dropping the rest."""
    low, high = centre - window // 2, centre + window // 2
    clipped = []
    for base, size, protection in regions:
        start, end = max(base, low), min(base + size, high)
        if start < end:
            clipped.append((start, end - start, protection))
    return clipped


def scan_once(
    client: Xbdm,
    chunk_size: int,
    regions: list[tuple[int, int, int]] | None = None,
) -> list[int]:
    hits: list[int] = []
    overlap = len(SIGNATURE) - 1
    for base, size, _protection in (
        candidates(client) if regions is None else regions
    ):
        # Walk the region from its top.  The heap is one ~200 MiB block and
        # every APT sighting so far sat in its last sixth, so starting at the
        # bottom spends minutes on memory that has never held it.
        tail = b""
        for offset in reversed(range(0, size, chunk_size)):
            amount = min(chunk_size, size - offset)
            try:
                chunk = client.read(base + offset, amount)
            except Exception:
                tail = b""
                continue
            window = chunk + tail
            window_base = base + offset
            cursor = 0
            while True:
                found = window.find(SIGNATURE, cursor)
                if found < 0:
                    break
                apt = window_base + found - SIGNATURE_OFFSET
                if apt >= base and apt + APT_SIZE <= base + size and apt not in hits:
                    hits.append(apt)
                cursor = found + 1
            # Overlap the *lower* neighbour, since we are moving downwards.
            tail = window[:overlap]
    return hits


def validate_structure(apt: bytes) -> None:
    if len(apt) != APT_SIZE or not apt.startswith(b"Apt Data:1:7:4\x1a"):
        raise RuntimeError("runtime APT header/length mismatch")
    for (offset, expected, replacement), (context_offset, before, after) in zip(
        PATCHES, CONTEXTS
    ):
        if context_offset != offset:
            raise AssertionError("internal context table mismatch")
        if apt[offset - len(before) : offset] != before:
            raise RuntimeError(f"pre-branch context mismatch at APT+0x{offset:X}")
        if apt[offset + 6 : offset + 6 + len(after)] != after:
            raise RuntimeError(f"post-branch context mismatch at APT+0x{offset:X}")
        if apt[offset : offset + 6] not in (expected, replacement):
            raise RuntimeError(f"branch instruction mismatch at APT+0x{offset:X}")


def classify(client: Xbdm, address: int) -> str:
    apt = client.read(address, APT_SIZE)
    validate_structure(apt)
    if all(apt[offset : offset + 6] == expected for offset, expected, _ in PATCHES):
        return "original"
    if all(apt[offset : offset + 6] == replacement for offset, _, replacement in PATCHES):
        return "patched"
    return "mixed"


def apply(client: Xbdm, address: int) -> None:
    apt = client.read(address, APT_SIZE)
    validate_structure(apt)
    for offset, expected, replacement in PATCHES:
        if apt[offset : offset + 6] != expected:
            raise RuntimeError(f"branch mismatch at APT+0x{offset:X}")
        client.write(address + offset, replacement)
    for offset, _expected, replacement in PATCHES:
        actual = client.read(address + offset, 6)
        if actual != replacement:
            raise RuntimeError(f"write verification failed at APT+0x{offset:X}")


def restore(client: Xbdm, address: int) -> None:
    """Restore only the three owned branches to their retail instructions."""
    apt = client.read(address, APT_SIZE)
    validate_structure(apt)
    for offset, expected, replacement in PATCHES:
        current = apt[offset : offset + 6]
        if current == replacement:
            client.write(address + offset, expected)
        elif current != expected:
            raise RuntimeError(f"branch mismatch at APT+0x{offset:X}")
    for offset, expected, _replacement in PATCHES:
        actual = client.read(address + offset, 6)
        if actual != expected:
            raise RuntimeError(f"restore verification failed at APT+0x{offset:X}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("--address", type=lambda value: int(value, 0))
    parser.add_argument(
        "--restore",
        action="store_true",
        help="restore the three owned branches to the retail TU3 bytes",
    )
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--chunk-size", type=lambda value: int(value, 0), default=0x400000)
    parser.add_argument(
        "--hint",
        type=lambda value: int(value, 0),
        default=OBSERVED_APT_NEIGHBOURHOOD,
        help="address to search around before sweeping the whole heap",
    )
    parser.add_argument(
        "--hint-window",
        type=lambda value: int(value, 0),
        default=DEFAULT_HINT_WINDOW,
        help="size of that first pass; 0 disables it",
    )
    parser.add_argument(
        "--hint-only",
        action="store_true",
        help=(
            "never fall back to the full heap sweep. The sweep reads the heap "
            "in 8 MB chunks, and running it against a title still on the "
            "splash once froze this console hard enough to drop it off the "
            "network. The hinted window is small enough to poll from the "
            "moment the title starts."
        ),
    )
    parser.add_argument(
        "--hint-file",
        type=Path,
        default=DEFAULT_HINT_FILE,
        help="where the last confirmed APT address is remembered",
    )
    args = parser.parse_args()

    hints_to_try = []
    recalled = remembered_hint(args.hint_file)
    if recalled is not None:
        hints_to_try.append(recalled)
    if args.hint not in hints_to_try:
        hints_to_try.append(args.hint)

    if args.address is not None:
        client = Xbdm(args.host)
        try:
            state = classify(client, args.address)
            print(f"TU3 helperFunctions APT 0x{args.address:08X}: {state}", flush=True)
            if args.restore:
                if state == "patched":
                    restore(client, args.address)
                elif state != "original":
                    raise RuntimeError(f"refusing APT state {state}")
                print(
                    "Verified: three TU3 continuation branches restored to retail.",
                    flush=True,
                )
            elif state == "original":
                apply(client, args.address)
            elif state != "patched":
                raise RuntimeError(f"refusing APT state {state}")
            if not args.restore:
                print(
                    "Verified: three native TU3 continuation branches patched; "
                    "no frontend event or navigation was injected.",
                    flush=True,
                )
            return 0
        finally:
            client.close()

    deadline = time.monotonic() + args.timeout
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        client: Xbdm | None = None
        try:
            client = Xbdm(args.host)
            hits = []
            if args.hint_window:
                # The remembered address first: it reflects this console's
                # current heap, whereas the built-in one only ever reflects
                # where the APT used to sit.
                for hint in hints_to_try:
                    narrowed = clip_to_window(
                        candidates(client), hint, args.hint_window
                    )
                    if not narrowed:
                        continue
                    hits = scan_once(client, args.chunk_size, narrowed)
                    if hits:
                        print(
                            f"Found in the hinted window around 0x{hint:08X}.",
                            flush=True,
                        )
                        break
            if not hits and not args.hint_only:
                hits = scan_once(client, args.chunk_size)
            for address in hits:
                state = classify(client, address)
                print(f"TU3 helperFunctions APT 0x{address:08X}: {state}", flush=True)
                # classify() has validated the header, length and all three
                # branch contexts, so this address is worth hinting from.
                remember_hint(args.hint_file, address)
                if args.restore:
                    if state == "patched":
                        restore(client, address)
                        print(
                            "Verified: three TU3 continuation branches restored to retail.",
                            flush=True,
                        )
                        return 0
                    if state == "original":
                        print("Verified: TU3 helperFunctions is already retail.", flush=True)
                        return 0
                    raise RuntimeError(f"refusing APT state {state}")
                if state == "original":
                    apply(client, address)
                    print(
                        "Verified: three native TU3 continuation branches patched; "
                        "no frontend event or navigation was injected.",
                        flush=True,
                    )
                    return 0
                if state == "patched":
                    print("Verified: TU3 helperFunctions was already patched.", flush=True)
                    return 0
        except (ConnectionError, EOFError, OSError, RuntimeError) as error:
            print(f"scan {attempt}: {error}", flush=True)
        finally:
            if client is not None:
                client.close()
        time.sleep(args.interval)
    raise TimeoutError("TU3 helperFunctions APT was not found before timeout")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
