#!/usr/bin/env python3
"""Patch the native FUT continuation branches in Xbox 360 TU3 patch.big.

The tool is fail-closed for the exact Title Update 3 archives extracted from
the active console.  It never edits its inputs and keeps the archive lengths
unchanged so the generated pair can be injected back into the STFS package.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import struct
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


BH_MAGIC = b"ViV4"
RECORD_INDEX = 2218
RECORD_OFFSET = 0x074E8500
RECORD_SIZE = 17_942
RECORD_PATH_HASH = 0x56CC043AC27ECC11
SLOT_CAPACITY = 0x4640

ORIGINAL_BIG_SIZE = 128_591_678
ORIGINAL_BIG_SHA256 = "d0486e06d03ecaef39167ca5208c3a8d3650f3628ba49e5a1029ea4ebf711877"
ORIGINAL_BH_SHA256 = "08eb5207bc124e82db738ba52cef27f8bf762e9e67ab5d014397b3a89f3f6c0e"
ORIGINAL_DECODED_SHA256 = "d6ffa69d851211a2bcb1e67933ab498666315b1d8ae38291c211cef97d3bee4f"

APT_OFFSET = 0x40
APT_SIZE = 0x6E0C


@dataclass(frozen=True)
class BranchPatch:
    offset: int
    expected: bytes
    replacement: bytes
    target: int
    description: str


PATCHES = (
    BranchPatch(
        0x2C86,
        bytes.fromhex("9D 00 00 00 00 28"),
        bytes.fromhex("99 00 00 00 00 64"),
        0x2CF0,
        "continue through the existing futSquadLoadSuccess call block",
    ),
    BranchPatch(
        0x2D92,
        bytes.fromhex("9D 00 00 00 00 0C"),
        bytes.fromhex("9D 00 00 00 00 00"),
        0x2D98,
        "keep the retail direct continuation when LiveDB is unavailable",
    ),
    BranchPatch(
        0x2FEA,
        bytes.fromhex("9D 00 00 00 00 B0"),
        bytes.fromhex("99 00 00 00 00 B0"),
        0x30A0,
        "continue through the existing enterFutCallback/EnterFUT2 block",
    ),
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run_checked(command: list[str]) -> None:
    result = subprocess.run(command, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(command)}\n"
            f"{result.stdout}{result.stderr}"
        )


def parse_record(bh: bytes) -> tuple[int, int, int]:
    if len(bh) < 16 or bh[:4] != BH_MAGIC:
        raise ValueError("patch.bh is not a ViV4 index")
    count = struct.unpack_from(">I", bh, 8)[0]
    if RECORD_INDEX >= count:
        raise ValueError(f"patch.bh has only {count} records")
    table_offset = 16 + RECORD_INDEX * 20
    offset, size, _reserved, hash_hi, hash_lo = struct.unpack_from(
        ">IIIII", bh, table_offset
    )
    path_hash = (hash_hi << 32) | hash_lo
    if (offset, size, path_hash) != (RECORD_OFFSET, RECORD_SIZE, RECORD_PATH_HASH):
        raise ValueError(
            "TU3 helperFunctions identity changed: "
            f"offset=0x{offset:X}, size={size}, hash=0x{path_hash:016X}"
        )
    return table_offset, offset, size


def parse_apt(decoded: bytes) -> bytes:
    if sha256(decoded) != ORIGINAL_DECODED_SHA256:
        raise ValueError("decoded helperFunctions is not the verified Xbox TU3 asset")
    if decoded[:4] != b"BIGF":
        raise ValueError("decoded helperFunctions package is not BIGF")
    offset, size = struct.unpack_from(">II", decoded, 16)
    if (offset, size) != (APT_OFFSET, APT_SIZE):
        raise ValueError(f"unexpected TU3 APT layout: offset=0x{offset:X}, size=0x{size:X}")
    apt = decoded[offset : offset + size]
    if len(apt) != size or not apt.startswith(b"Apt Data:"):
        raise ValueError("truncated or invalid TU3 APT entry")
    return apt


def branch_target(offset: int, instruction: bytes) -> int:
    displacement = struct.unpack_from(">i", instruction, 2)[0]
    return offset + 6 + displacement


def patch_decoded(decoded: bytes) -> tuple[bytes, list[dict[str, str]]]:
    apt = bytearray(parse_apt(decoded))
    changes: list[dict[str, str]] = []
    for item in PATCHES:
        actual = bytes(apt[item.offset : item.offset + 6])
        if actual != item.expected:
            raise ValueError(
                f"APT mismatch at 0x{item.offset:X}: expected "
                f"{item.expected.hex(' ')}, got {actual.hex(' ')}"
            )
        if branch_target(item.offset, item.replacement) != item.target:
            raise AssertionError(f"invalid branch target at 0x{item.offset:X}")
        apt[item.offset : item.offset + 6] = item.replacement
        changes.append(
            {
                "offset": f"0x{item.offset:X}",
                "before": item.expected.hex(" "),
                "after": item.replacement.hex(" "),
                "target": f"0x{item.target:X}",
                "description": item.description,
            }
        )
    result = bytearray(decoded)
    result[APT_OFFSET : APT_OFFSET + APT_SIZE] = apt
    return bytes(result), changes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--patch-big", type=Path, required=True)
    parser.add_argument("--patch-bh", type=Path, required=True)
    parser.add_argument("--decoder", type=Path, required=True)
    parser.add_argument("--encoder", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    original_big = args.patch_big.read_bytes()
    original_bh = args.patch_bh.read_bytes()
    if len(original_big) != ORIGINAL_BIG_SIZE or sha256(original_big) != ORIGINAL_BIG_SHA256:
        raise ValueError("patch.big is not the verified Xbox TU3 archive")
    if sha256(original_bh) != ORIGINAL_BH_SHA256:
        raise ValueError("patch.bh is not the verified Xbox TU3 index")
    table_offset, record_offset, record_size = parse_record(original_bh)

    args.output.mkdir(parents=True, exist_ok=True)
    original_dir = args.output / "original"
    patched_dir = args.output / "patched"
    original_dir.mkdir(exist_ok=True)
    patched_dir.mkdir(exist_ok=True)
    shutil.copy2(args.patch_big, original_dir / "patch.big")
    shutil.copy2(args.patch_bh, original_dir / "patch.bh")

    with tempfile.TemporaryDirectory(prefix="fifa14-tu3-helper-") as raw_temp:
        temp = Path(raw_temp)
        compressed = temp / "original.chunklzx"
        decoded_path = temp / "original.big"
        compressed.write_bytes(original_big[record_offset : record_offset + record_size])
        run_checked([str(args.decoder), str(compressed), str(decoded_path)])
        patched_decoded, changes = patch_decoded(decoded_path.read_bytes())
        patched_source = temp / "patched.big"
        patched_payload_path = temp / "patched.chunklzx"
        patched_source.write_bytes(patched_decoded)
        run_checked([str(args.encoder), str(patched_source), str(patched_payload_path)])
        patched_payload = patched_payload_path.read_bytes()
        if len(patched_payload) > SLOT_CAPACITY:
            raise ValueError(
                f"patched payload {len(patched_payload)} exceeds slot {SLOT_CAPACITY}"
            )
        verify_path = temp / "verify.big"
        run_checked([str(args.decoder), str(patched_payload_path), str(verify_path)])
        if verify_path.read_bytes() != patched_decoded:
            raise RuntimeError("patched TU3 LZX round-trip mismatch")

    patched_big = bytearray(original_big)
    patched_big[record_offset : record_offset + SLOT_CAPACITY] = bytes(SLOT_CAPACITY)
    patched_big[record_offset : record_offset + len(patched_payload)] = patched_payload
    patched_bh = bytearray(original_bh)
    struct.pack_into(">I", patched_bh, table_offset + 4, len(patched_payload))
    (patched_dir / "patch.big").write_bytes(patched_big)
    (patched_dir / "patch.bh").write_bytes(patched_bh)

    metadata = {
        "record_index": RECORD_INDEX,
        "record_offset": f"0x{RECORD_OFFSET:X}",
        "original_record_size": RECORD_SIZE,
        "patched_record_size": len(patched_payload),
        "slot_capacity": SLOT_CAPACITY,
        "original_big_sha256": sha256(original_big),
        "original_bh_sha256": sha256(original_bh),
        "patched_big_sha256": sha256(patched_big),
        "patched_bh_sha256": sha256(patched_bh),
        "patched_decoded_sha256": sha256(patched_decoded),
        "changes": changes,
    }
    (args.output / "metadata.json").write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(metadata, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
