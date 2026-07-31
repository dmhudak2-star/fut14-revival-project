#!/usr/bin/env python3
"""Transactionally activate or roll back the FIFA 14 CreateClub archives.

The script never deletes or overwrites a remote file.  It only renames these
three names for each archive extension::

    data1.big                  active archive
    data1_createclub.big       staged patched archive
    data1_codex_original.big   recoverable original archive

The same layout is used for ``.bh``.  Every mutating command first verifies
that FIFA 14 itself is not mapped.  If a command reply is lost or another
error interrupts an apply, the script reconnects, infers the completed steps
from the filenames, and restores the original pair.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import socket
import struct
import sys
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple


ROOT = "USB0:\\"

# These hashes were calculated from the exact local files downloaded/built for
# this console.  Sizes happen to be identical, so the digest check is what
# distinguishes the original and CreateClub contents.
EXPECTED = {
    "big": {
        "size": 336_771_570,
        "original_sha256": "f996043c3c1274280d9f99882687e54ab7836fd106e511fbaa67bc8717550304",
        "patched_sha256": "2da77c50f90d547b2d88f9b333d1794ee8abe34aebd2ab7d7d4eff4f5fbaa2ee",
    },
    "bh": {
        "size": 348_996,
        "original_sha256": "9d1bd50c1de67e1a16ed141e88ccda789ae2a0566ec3ab3956e1473737e96e1c",
        "patched_sha256": "5a716f2ffc285124d99b94171121896cbdf3fb73775648ccee3ee1e41648766f",
    },
}


def remote_path(name: str) -> str:
    return ROOT + name


def names_for(extension: str) -> Tuple[str, str, str]:
    return (
        f"data1.{extension}",
        f"data1_createclub.{extension}",
        f"data1_codex_original.{extension}",
    )


def parse_number(value: str) -> int:
    value = value.strip()
    if value.lower().startswith("0x"):
        return int(value[2:], 16)
    if value.lower().startswith("0q"):
        return int(value[2:], 16)
    return int(value, 10)


def get_parameter(line: str, key: str) -> Optional[str]:
    pattern = rf"(?:^|\s){re.escape(key)}=(?:\"([^\"]*)\"|([^\s]+))"
    match = re.search(pattern, line, flags=re.IGNORECASE)
    if not match:
        return None
    return match.group(1) if match.group(1) is not None else match.group(2)


@dataclass(frozen=True)
class RemoteEntry:
    name: str
    size: int
    raw: str


class XbdmError(RuntimeError):
    pass


class XbdmClient:
    def __init__(self, host: str, port: int, timeout: float):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sock: Optional[socket.socket] = None
        self.reader = None

    def __enter__(self) -> "XbdmClient":
        self.sock = socket.create_connection(
            (self.host, self.port), timeout=self.timeout
        )
        self.sock.settimeout(self.timeout)
        self.reader = self.sock.makefile("rb")
        greeting = self._read_line()
        if not greeting.startswith("201"):
            self.close()
            raise XbdmError(f"Unexpected XBDM greeting: {greeting}")
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def close(self) -> None:
        if self.reader is not None:
            try:
                self.reader.close()
            except OSError:
                pass
            self.reader = None
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    def _read_line(self) -> str:
        if self.reader is None:
            raise XbdmError("XBDM connection is not open")
        data = self.reader.readline()
        if not data:
            raise XbdmError("XBDM closed the connection")
        return data.decode("ascii", "replace").rstrip("\r\n")

    def _send(self, command: str) -> None:
        if self.sock is None:
            raise XbdmError("XBDM connection is not open")
        if "\r" in command or "\n" in command:
            raise ValueError("XBDM command cannot contain line breaks")
        self.sock.sendall(command.encode("ascii") + b"\r\n")

    def multiline(self, command: str) -> List[str]:
        self._send(command)
        response = self._read_line()
        if not response.startswith("202"):
            raise XbdmError(f"{command}: {response}")
        lines: List[str] = []
        while True:
            line = self._read_line()
            if line == ".":
                return lines
            lines.append(line)

    def ok(self, command: str) -> str:
        self._send(command)
        response = self._read_line()
        if not response.startswith("200"):
            raise XbdmError(f"{command}: {response}")
        return response

    def directory(self) -> Dict[str, RemoteEntry]:
        entries: Dict[str, RemoteEntry] = {}
        for line in self.multiline(f'dirlist name="{ROOT}"'):
            value = get_parameter(line, "name")
            if value is None:
                continue
            leaf = value.replace("/", "\\").rsplit("\\", 1)[-1]
            size_lo = get_parameter(line, "sizelo")
            size_hi = get_parameter(line, "sizehi")
            size = 0
            if size_lo is not None:
                size |= parse_number(size_lo)
            if size_hi is not None:
                size |= parse_number(size_hi) << 32
            entries[leaf.casefold()] = RemoteEntry(leaf, size, line)
        return entries

    def modules(self) -> List[str]:
        return self.multiline("modules")

    def rename(self, source: str, destination: str) -> None:
        self.ok(
            f'rename name="{remote_path(source)}" '
            f'newname="{remote_path(destination)}"'
        )

    def sha256(self, filename: str, expected_size: int) -> str:
        if self.sock is None or self.reader is None:
            raise XbdmError("XBDM connection is not open")
        command = f'getfile name="{remote_path(filename)}"'
        self._send(command)
        response = self._read_line()
        if not response.startswith("203"):
            raise XbdmError(f"{command}: {response}")
        raw_length = self.reader.read(4)
        if len(raw_length) != 4:
            raise XbdmError(f"{command}: missing binary length")
        length = struct.unpack("<I", raw_length)[0]
        if length != expected_size:
            raise XbdmError(
                f"{filename}: binary length {length} != expected {expected_size}"
            )

        digest = hashlib.sha256()
        remaining = length
        next_report = 32 * 1024 * 1024
        while remaining:
            block = self.reader.read(min(1024 * 1024, remaining))
            if not block:
                raise XbdmError(
                    f"{filename}: XBDM closed with {remaining} bytes remaining"
                )
            digest.update(block)
            remaining -= len(block)
            completed = length - remaining
            if completed >= next_report or remaining == 0:
                print(
                    f"\r  hashing {filename}: {completed}/{length} "
                    f"({completed * 100.0 / length:5.1f}%)",
                    end="",
                    flush=True,
                )
                next_report += 32 * 1024 * 1024
        print()
        return digest.hexdigest()


def entry(entries: Dict[str, RemoteEntry], filename: str) -> Optional[RemoteEntry]:
    return entries.get(filename.casefold())


def extension_state(entries: Dict[str, RemoteEntry], extension: str) -> str:
    active, staged, backup = names_for(extension)
    present = tuple(entry(entries, name) is not None for name in (active, staged, backup))
    if present == (True, True, False):
        return "original"
    if present == (False, True, True):
        return "after-backup"
    if present == (True, False, True):
        return "applied"
    return "invalid"


def overall_state(entries: Dict[str, RemoteEntry]) -> str:
    states = [extension_state(entries, extension) for extension in EXPECTED]
    if states == ["original", "original"]:
        return "ORIGINAL"
    if states == ["applied", "applied"]:
        return "APPLIED"
    if all(state in {"original", "after-backup", "applied"} for state in states):
        return "TRANSITIONAL"
    return "INVALID"


def validate_sizes(entries: Dict[str, RemoteEntry]) -> None:
    errors: List[str] = []
    for extension, expected in EXPECTED.items():
        for filename in names_for(extension):
            remote = entry(entries, filename)
            if remote is not None and remote.size != expected["size"]:
                errors.append(
                    f"{filename}: {remote.size} bytes; expected {expected['size']}"
                )
    if errors:
        raise XbdmError("Unexpected archive size(s):\n  " + "\n  ".join(errors))


def print_status(entries: Dict[str, RemoteEntry]) -> None:
    print(f"Archive state: {overall_state(entries)}")
    for extension in EXPECTED:
        print(f"  .{extension}: {extension_state(entries, extension)}")
        for filename in names_for(extension):
            remote = entry(entries, filename)
            detail = "absent" if remote is None else f"present, {remote.size} bytes"
            print(f"    {filename:<29} {detail}")


def fifa14_is_loaded(module_lines: Iterable[str]) -> Optional[str]:
    for line in module_lines:
        name = get_parameter(line, "name")
        if name is None or name.casefold() != "default.xex":
            continue
        normalized = line.casefold()
        fifa_signature = (
            "timestamp=0x534c8977" in normalized
            or "osize=0x023ec400" in normalized
            or (
                "size=0x01f20000" in normalized
                and "pdata=0x82329200" in normalized
            )
        )
        if fifa_signature:
            return line
    return None


def ensure_title_stopped(client: XbdmClient) -> None:
    loaded = fifa14_is_loaded(client.modules())
    if loaded is not None:
        raise XbdmError(
            "FIFA 14 is still loaded. Return to the Xbox dashboard or XeXMenu "
            "before renaming its archives.\n  " + loaded
        )


def verify_identity(
    client: XbdmClient,
    filename: str,
    extension: str,
    identity: str,
) -> None:
    expected = EXPECTED[extension]
    expected_hash = expected[f"{identity}_sha256"]
    actual_hash = client.sha256(filename, expected["size"])
    if actual_hash != expected_hash:
        raise XbdmError(
            f"{filename}: SHA-256 {actual_hash} does not match known "
            f"{identity} {expected_hash}"
        )
    print(f"  verified {filename}: {identity} ({actual_hash})")


def verify_known_state(client: XbdmClient, entries: Dict[str, RemoteEntry]) -> None:
    state = overall_state(entries)
    if state == "ORIGINAL":
        for extension in EXPECTED:
            active, staged, _ = names_for(extension)
            verify_identity(client, active, extension, "original")
            verify_identity(client, staged, extension, "patched")
        return
    if state == "APPLIED":
        for extension in EXPECTED:
            active, _, backup = names_for(extension)
            verify_identity(client, active, extension, "patched")
            verify_identity(client, backup, extension, "original")
        return
    raise XbdmError(
        f"Full identity verification requires ORIGINAL or APPLIED state, got {state}"
    )


def checked_rename(
    client: XbdmClient,
    entries: Dict[str, RemoteEntry],
    source: str,
    destination: str,
) -> Dict[str, RemoteEntry]:
    if entry(entries, source) is None:
        raise XbdmError(f"Rename source is absent: {source}")
    if entry(entries, destination) is not None:
        raise XbdmError(f"Rename destination already exists: {destination}")
    print(f"  rename {source} -> {destination}")
    client.rename(source, destination)
    updated = client.directory()
    if entry(updated, source) is not None or entry(updated, destination) is None:
        raise XbdmError(
            f"Rename verification failed: {source} -> {destination}"
        )
    validate_sizes(updated)
    return updated


def connect(args: argparse.Namespace) -> XbdmClient:
    return XbdmClient(args.host, args.port, args.timeout)


def read_status(args: argparse.Namespace) -> Dict[str, RemoteEntry]:
    with connect(args) as client:
        entries = client.directory()
    validate_sizes(entries)
    return entries


def recover_original(args: argparse.Namespace, attempts: int = 4) -> None:
    """Converge any valid transaction prefix back to the original layout."""
    last_error: Optional[BaseException] = None
    for attempt in range(1, attempts + 1):
        try:
            with connect(args) as client:
                ensure_title_stopped(client)
                entries = client.directory()
                validate_sizes(entries)
                state = overall_state(entries)
                if state == "ORIGINAL":
                    print("Original archive layout verified.")
                    return
                if state == "INVALID":
                    raise XbdmError(
                        "Cannot auto-recover an invalid filename layout; no files were changed."
                    )

                # First put any active patched member back under its staged name.
                for extension in EXPECTED:
                    if extension_state(entries, extension) == "applied":
                        active, staged, _ = names_for(extension)
                        entries = checked_rename(client, entries, active, staged)

                # Then restore every original member from its backup name.
                for extension in EXPECTED:
                    if extension_state(entries, extension) == "after-backup":
                        active, _, backup = names_for(extension)
                        entries = checked_rename(client, entries, backup, active)

                if overall_state(entries) != "ORIGINAL":
                    raise XbdmError(
                        f"Recovery ended in unexpected state {overall_state(entries)}"
                    )
                print("Original archive layout restored and verified.")
                return
        except (KeyboardInterrupt, OSError, XbdmError) as exc:
            last_error = exc
            print(f"Recovery attempt {attempt}/{attempts} failed: {exc}", file=sys.stderr)

    raise XbdmError(
        "Automatic recovery could not restore the original layout after "
        f"{attempts} attempts. Last error: {last_error}"
    )


def apply_patch(args: argparse.Namespace) -> None:
    mutation_started = False
    try:
        with connect(args) as client:
            ensure_title_stopped(client)
            entries = client.directory()
            validate_sizes(entries)
            state = overall_state(entries)
            print_status(entries)
            if state == "APPLIED":
                print("CreateClub archives are already active; nothing changed.")
                return
            if state != "ORIGINAL":
                raise XbdmError(
                    f"Apply requires ORIGINAL state, got {state}. Run rollback first."
                )
            if not args.quick:
                print("Verifying original and staged contents before mutation...")
                verify_known_state(client, entries)
            else:
                print("WARNING: --quick skips SHA-256 identity checks; sizes only.")

            # Preserve both originals before activating either patched member.
            mutation_started = True
            for extension in EXPECTED:
                active, _, backup = names_for(extension)
                entries = checked_rename(client, entries, active, backup)
            for extension in EXPECTED:
                active, staged, _ = names_for(extension)
                entries = checked_rename(client, entries, staged, active)

            if overall_state(entries) != "APPLIED":
                raise XbdmError(
                    f"Apply ended in unexpected state {overall_state(entries)}"
                )
            print("CreateClub archive pair activated; original pair remains as backup.")
    except BaseException as exc:
        if not mutation_started:
            raise
        if isinstance(exc, KeyboardInterrupt):
            print("\nApply interrupted; restoring original layout...", file=sys.stderr)
        else:
            print(f"Apply failed: {exc}", file=sys.stderr)
            print("Restoring original layout...", file=sys.stderr)
        try:
            recover_original(args)
        except BaseException as recovery_error:
            raise XbdmError(
                f"Apply error: {exc}; recovery error: {recovery_error}"
            ) from recovery_error
        raise XbdmError(f"Apply was rolled back: {exc}") from exc


def rollback(args: argparse.Namespace) -> None:
    # Verify identities before a normal rollback.  A transitional state is
    # still recoverable by filenames after an interrupted prior transaction.
    with connect(args) as client:
        ensure_title_stopped(client)
        entries = client.directory()
        validate_sizes(entries)
        state = overall_state(entries)
        print_status(entries)
        if state == "ORIGINAL":
            print("Original archives are already active; nothing changed.")
            return
        if state == "INVALID":
            raise XbdmError("Rollback refused: invalid filename layout.")
        if state == "APPLIED" and not args.quick:
            print("Verifying active patched pair and original backups...")
            verify_known_state(client, entries)
        elif args.quick:
            print("WARNING: --quick skips SHA-256 identity checks; sizes only.")
    recover_original(args)


def verify(args: argparse.Namespace) -> None:
    with connect(args) as client:
        entries = client.directory()
        validate_sizes(entries)
        print_status(entries)
        verify_known_state(client, entries)
    print("All archive identities verified.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Safely activate/rollback FIFA 14 CreateClub data1 archives"
    )
    parser.add_argument("host", help="Xbox 360 IP address")
    parser.add_argument(
        "action", choices=("status", "verify", "apply", "rollback")
    )
    parser.add_argument("--port", type=int, default=730)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument(
        "--quick",
        action="store_true",
        help="skip content hashes during apply/rollback (not recommended)",
    )
    args = parser.parse_args()

    try:
        if args.action == "status":
            print_status(read_status(args))
        elif args.action == "verify":
            verify(args)
        elif args.action == "apply":
            apply_patch(args)
        elif args.action == "rollback":
            rollback(args)
        return 0
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except (OSError, XbdmError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
