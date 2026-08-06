#!/usr/bin/env python3
"""Patch the verified Xbox 360 helperFunctions APT continuation branches.

The PC research snapshot by Loopizzle proved that three existing retail
branches are enough to continue from the pre-Cards loading loop into the
native ``enterFutCallback``/``EnterFUT2`` path.  FIFA 14's Xbox 360 package
contains the same functions with console-specific byte offsets and big-endian
branch displacements.

This tool is deliberately tied to the exact retail ``data0`` pair recovered
from the active console.  It writes new files to an output directory; it never
edits the input archives in place.  The caller remains responsible for keeping
the original pair and installing both generated files together.
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
RECORD_INDEX = 111
RECORD_OFFSET = 3_195_840
RECORD_SIZE = 18_378
RECORD_PATH_HASH = 0x56CC043AC27ECC11
SLOT_CAPACITY = 18_432

ORIGINAL_BIG_SIZE = 3_887_076
ORIGINAL_BIG_SHA256 = "90d671fd4f6d409ba988492762b92fc24dfcff7400fe5d395b45a733eef7d528"
ORIGINAL_BH_SHA256 = "f1847bd3e7dc739edd12c0d54d7483f75235cf5f1353ad9089f7d852a362c49b"
ORIGINAL_DECODED_SHA256 = "8a48c788dbbb65a89587a82752e39fb670af86740eada434efadbcfce3f7f480"
ORIGINAL_APT_SHA256 = "3d73e46362dda0ba97c5231f32108e895dd5c8ff9dd505a7851b4f0e6aaf4d8a"

APT_NAME = "0"
APT_OFFSET = 64
APT_SIZE = 28_120


@dataclass(frozen=True)
class BranchPatch:
    offset: int
    expected: bytes
    replacement: bytes
    target: int
    description: str


PATCHES = (
    BranchPatch(
        0x2C62,
        bytes.fromhex("9D 00 00 00 00 28"),
        bytes.fromhex("99 00 00 00 00 64"),
        0x2CCC,
        "continue through the existing futSquadLoadSuccess call block",
    ),
    BranchPatch(
        0x2D6E,
        bytes.fromhex("9D 00 00 00 00 0C"),
        bytes.fromhex("9D 00 00 00 00 00"),
        0x2D74,
        "keep the retail direct continuation when LiveDB is unavailable",
    ),
    BranchPatch(
        0x2FC6,
        bytes.fromhex("9D 00 00 00 00 B0"),
        bytes.fromhex("99 00 00 00 00 B0"),
        0x307C,
        "continue through the existing enterFutCallback/EnterFUT2 block",
    ),
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_record(bh: bytes) -> tuple[int, int, int, int]:
    if len(bh) < 16 or bh[:4] != BH_MAGIC:
        raise ValueError("data0.bh is not a ViV4 index")
    count = struct.unpack_from(">I", bh, 8)[0]
    if RECORD_INDEX >= count:
        raise ValueError(f"data0.bh has only {count} records")
    table_offset = 16 + RECORD_INDEX * 20
    offset, size, _reserved, hash_hi, hash_lo = struct.unpack_from(
        ">IIIII", bh, table_offset
    )
    path_hash = (hash_hi << 32) | hash_lo
    if (offset, path_hash) != (RECORD_OFFSET, RECORD_PATH_HASH):
        raise ValueError(
            "helperFunctions record identity changed: "
            f"offset=0x{offset:X}, hash=0x{path_hash:016X}"
        )
    return table_offset, offset, size, path_hash


def parse_apt_entry(decoded: bytes) -> tuple[int, int]:
    if len(decoded) < 16 or decoded[:4] not in (b"BIG4", b"BIGF"):
        raise ValueError("decoded helperFunctions package is not BIG4/BIGF")
    count, header_size = struct.unpack_from(">II", decoded, 8)
    if not 16 <= header_size <= len(decoded):
        raise ValueError("invalid helperFunctions BIG header")
    pos = 16
    matches: list[tuple[str, int, int]] = []
    for _index in range(count):
        offset, size = struct.unpack_from(">II", decoded, pos)
        pos += 8
        end = decoded.find(b"\0", pos, header_size)
        if end < 0:
            raise ValueError("unterminated helperFunctions BIG entry name")
        name = decoded[pos:end].decode("ascii")
        pos = end + 1
        if offset + size > len(decoded):
            raise ValueError(f"BIG entry {name!r} exceeds decoded package")
        if decoded[offset : offset + 9] == b"Apt Data:":
            matches.append((name, offset, size))
    if matches != [(APT_NAME, APT_OFFSET, APT_SIZE)]:
        raise ValueError(f"unexpected helperFunctions APT layout: {matches!r}")
    return APT_OFFSET, APT_SIZE


def branch_target(offset: int, instruction: bytes) -> int:
    if len(instruction) != 6 or instruction[0] not in (0x99, 0x9D):
        raise ValueError(f"not an aligned APT branch at 0x{offset:X}")
    displacement = struct.unpack_from(">i", instruction, 2)[0]
    return offset + 6 + displacement


def patch_decoded(decoded: bytes) -> tuple[bytes, list[dict[str, object]]]:
    if sha256(decoded) != ORIGINAL_DECODED_SHA256:
        raise ValueError("decoded helperFunctions package is not the verified retail original")
    apt_offset, apt_size = parse_apt_entry(decoded)
    apt = bytearray(decoded[apt_offset : apt_offset + apt_size])
    if sha256(apt) != ORIGINAL_APT_SHA256:
        raise ValueError("helperFunctions APT hash is not the verified retail original")

    changes: list[dict[str, object]] = []
    for item in PATCHES:
        actual = bytes(apt[item.offset : item.offset + 6])
        if actual != item.expected:
            raise ValueError(
                f"APT mismatch at 0x{item.offset:X}: "
                f"expected {item.expected.hex(' ')}, got {actual.hex(' ')}"
            )
        apt[item.offset : item.offset + 6] = item.replacement
        actual_target = branch_target(item.offset, item.replacement)
        if actual_target != item.target:
            raise AssertionError(
                f"branch target 0x{actual_target:X} != 0x{item.target:X}"
            )
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
    result[apt_offset : apt_offset + apt_size] = apt
    return bytes(result), changes


def run_checked(command: list[str]) -> None:
    result = subprocess.run(command, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(command)}\n"
            f"{result.stdout}{result.stderr}"
        )


def decode_record(decoder: Path, payload: bytes, temp: Path, stem: str) -> bytes:
    source = temp / f"{stem}.chunklzx"
    output = temp / f"{stem}.big"
    source.write_bytes(payload)
    run_checked([str(decoder), str(source), str(output)])
    return output.read_bytes()


def encode_record(encoder: Path, decoded: bytes, temp: Path) -> bytes:
    source = temp / "patched.big"
    output = temp / "patched.chunklzx"
    source.write_bytes(decoded)
    run_checked([str(encoder), str(source), str(output)])
    return output.read_bytes()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data0-big", type=Path, required=True)
    parser.add_argument("--data0-bh", type=Path, required=True)
    parser.add_argument("--decoder", type=Path, required=True)
    parser.add_argument("--encoder", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    original_big = args.data0_big.read_bytes()
    original_bh = args.data0_bh.read_bytes()
    if len(original_big) != ORIGINAL_BIG_SIZE:
        raise ValueError(f"data0.big size {len(original_big)} != {ORIGINAL_BIG_SIZE}")
    if sha256(original_big) != ORIGINAL_BIG_SHA256:
        raise ValueError("data0.big is not the verified active Xbox archive")
    if sha256(original_bh) != ORIGINAL_BH_SHA256:
        raise ValueError("data0.bh is not the verified active Xbox index")

    table_offset, record_offset, record_size, _path_hash = parse_record(original_bh)
    if record_size != RECORD_SIZE:
        raise ValueError(f"retail helperFunctions size {record_size} != {RECORD_SIZE}")
    payload = original_big[record_offset : record_offset + record_size]

    args.output.mkdir(parents=True, exist_ok=True)
    original_dir = args.output / "original"
    patched_dir = args.output / "patched"
    original_dir.mkdir(exist_ok=True)
    patched_dir.mkdir(exist_ok=True)
    shutil.copy2(args.data0_big, original_dir / "data0.big")
    shutil.copy2(args.data0_bh, original_dir / "data0.bh")

    with tempfile.TemporaryDirectory(prefix="fifa14-xbox-helper-") as raw_temp:
        temp = Path(raw_temp)
        decoded = decode_record(args.decoder, payload, temp, "retail")
        patched_decoded, changes = patch_decoded(decoded)
        patched_payload = encode_record(args.encoder, patched_decoded, temp)
        if len(patched_payload) > SLOT_CAPACITY:
            raise ValueError(
                f"patched helperFunctions payload {len(patched_payload)} exceeds "
                f"the verified {SLOT_CAPACITY}-byte slot"
            )
        decoded_check = decode_record(args.decoder, patched_payload, temp, "verify")
        if decoded_check != patched_decoded:
            raise RuntimeError("patched helperFunctions LZX round-trip mismatch")

    patched_big = bytearray(original_big)
    patched_big[record_offset : record_offset + SLOT_CAPACITY] = bytes(SLOT_CAPACITY)
    patched_big[record_offset : record_offset + len(patched_payload)] = patched_payload
    patched_bh = bytearray(original_bh)
    struct.pack_into(">I", patched_bh, table_offset + 4, len(patched_payload))

    (patched_dir / "data0.big").write_bytes(patched_big)
    (patched_dir / "data0.bh").write_bytes(patched_bh)
    metadata = {
        "record_index": RECORD_INDEX,
        "record_offset": RECORD_OFFSET,
        "retail_record_size": RECORD_SIZE,
        "patched_record_size": len(patched_payload),
        "slot_capacity": SLOT_CAPACITY,
        "retail_big_sha256": sha256(original_big),
        "retail_bh_sha256": sha256(original_bh),
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
