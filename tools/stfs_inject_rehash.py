#!/usr/bin/env python3
"""Replace same-size files in a Type-0 STFS package and rebuild its hashes.

This targets the single-table Type-0 layout used by Xbox 360 LIVE title
updates.  File allocation and chains are preserved; replacements must have
the exact original size.  SHA-1 data, L0, L1, L2, master and header hashes are
rebuilt.  The Microsoft RSA signature is deliberately preserved and therefore
requires an RGH/JTAG signature-check bypass after modification.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import struct
from pathlib import Path

from stfs_extract import BLOCK_SIZE, LEVEL0_BLOCKS, LEVEL1_BLOCKS, Type0Stfs


SPACE_L1 = 0xAB
SPACE_L2 = 0x718F


def sha1(data: bytes) -> bytes:
    return hashlib.sha1(data).digest()


def block_offset(base: int, physical: int) -> int:
    return base + physical * BLOCK_SIZE


def level0_table_offset(base: int, block: int) -> int:
    physical = (block // LEVEL0_BLOCKS) * SPACE_L1
    if block >= LEVEL0_BLOCKS:
        physical += block // LEVEL1_BLOCKS + 1
        if block >= LEVEL1_BLOCKS:
            physical += 1
    return block_offset(base, physical)


def level1_table_offset(base: int, block: int) -> int:
    if block < LEVEL1_BLOCKS:
        physical = SPACE_L1
    else:
        physical = SPACE_L2 * (block // LEVEL1_BLOCKS) + 1
    return block_offset(base, physical)


def level1_hash_offset(base: int, block: int) -> int:
    record = (block // LEVEL0_BLOCKS) % LEVEL0_BLOCKS
    return level1_table_offset(base, block) + 0x18 * record


def level2_table_offset(base: int) -> int:
    return block_offset(base, SPACE_L2)


def level2_hash_offset(base: int, block: int) -> int:
    record = (block // LEVEL1_BLOCKS) % LEVEL0_BLOCKS
    return level2_table_offset(base) + 0x18 * record


def verify_hash_tree(data: bytes, package: Type0Stfs) -> tuple[int, int]:
    checked = 0
    failed = 0
    for block in range(package.allocated_blocks):
        source = package.data_offset(block)
        expected = data[package.level0_hash_offset(block) : package.level0_hash_offset(block) + 20]
        if sha1(data[source : source + BLOCK_SIZE]) != expected:
            failed += 1
        checked += 1

    l0_count = (package.allocated_blocks + LEVEL0_BLOCKS - 1) // LEVEL0_BLOCKS
    for index in range(l0_count):
        block = index * LEVEL0_BLOCKS
        source = level0_table_offset(package.base, block)
        expected_at = level1_hash_offset(package.base, block)
        if sha1(data[source : source + BLOCK_SIZE]) != data[expected_at : expected_at + 20]:
            failed += 1
        checked += 1

    if package.allocated_blocks > LEVEL1_BLOCKS:
        l1_count = (package.allocated_blocks + LEVEL1_BLOCKS - 1) // LEVEL1_BLOCKS
        for index in range(l1_count):
            block = index * LEVEL1_BLOCKS
            source = level1_table_offset(package.base, block)
            expected_at = level2_hash_offset(package.base, block)
            if sha1(data[source : source + BLOCK_SIZE]) != data[expected_at : expected_at + 20]:
                failed += 1
            checked += 1
        top = level2_table_offset(package.base)
    elif package.allocated_blocks > LEVEL0_BLOCKS:
        top = level1_table_offset(package.base, 0)
    else:
        top = level0_table_offset(package.base, 0)

    if sha1(data[top : top + BLOCK_SIZE]) != data[0x381 : 0x381 + 20]:
        failed += 1
    checked += 1
    if sha1(data[0x344 : package.base]) != data[0x32C : 0x32C + 20]:
        failed += 1
    checked += 1
    return checked, failed


def rebuild_hash_tree(data: bytearray, package: Type0Stfs) -> None:
    for block in range(package.allocated_blocks):
        source = package.data_offset(block)
        destination = package.level0_hash_offset(block)
        data[destination : destination + 20] = sha1(data[source : source + BLOCK_SIZE])

    l0_count = (package.allocated_blocks + LEVEL0_BLOCKS - 1) // LEVEL0_BLOCKS
    for index in range(l0_count):
        block = index * LEVEL0_BLOCKS
        source = level0_table_offset(package.base, block)
        destination = level1_hash_offset(package.base, block)
        data[destination : destination + 20] = sha1(data[source : source + BLOCK_SIZE])

    if package.allocated_blocks > LEVEL1_BLOCKS:
        l1_count = (package.allocated_blocks + LEVEL1_BLOCKS - 1) // LEVEL1_BLOCKS
        for index in range(l1_count):
            block = index * LEVEL1_BLOCKS
            source = level1_table_offset(package.base, block)
            destination = level2_hash_offset(package.base, block)
            data[destination : destination + 20] = sha1(data[source : source + BLOCK_SIZE])
        top = level2_table_offset(package.base)
    elif package.allocated_blocks > LEVEL0_BLOCKS:
        top = level1_table_offset(package.base, 0)
    else:
        top = level0_table_offset(package.base, 0)

    data[0x381 : 0x381 + 20] = sha1(data[top : top + BLOCK_SIZE])
    data[0x32C : 0x32C + 20] = sha1(data[0x344 : package.base])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("package", type=Path)
    parser.add_argument("--replace", action="append", default=[], metavar="NAME=FILE")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()

    package = Type0Stfs(args.package)
    original = args.package.read_bytes()
    checked, failed = verify_hash_tree(original, package)
    print(f"Original hash tree: {checked - failed}/{checked} valid")
    if failed:
        raise ValueError(f"original STFS has {failed} invalid hash(es)")
    if args.verify_only:
        return 0

    replacements: dict[str, Path] = {}
    for expression in args.replace:
        if "=" not in expression:
            parser.error(f"invalid --replace value: {expression!r}")
        name, raw_path = expression.split("=", 1)
        replacements[name] = Path(raw_path)
    if not replacements:
        parser.error("at least one --replace NAME=FILE is required")

    entries = {entry.name: entry for entry in package.entries() if not entry.folder}
    output_data = bytearray(original)
    for name, source_path in replacements.items():
        entry = entries.get(name)
        if entry is None:
            raise ValueError(f"STFS file not found: {name}")
        replacement = source_path.read_bytes()
        if len(replacement) != entry.size:
            raise ValueError(
                f"{name}: replacement size {len(replacement)} != original {entry.size}"
            )
        cursor = 0
        for block in package.chain(entry.start_block, entry.block_count):
            count = min(BLOCK_SIZE, len(replacement) - cursor)
            if count <= 0:
                break
            destination = package.data_offset(block)
            output_data[destination : destination + count] = replacement[cursor : cursor + count]
            cursor += count
        if cursor != len(replacement):
            raise RuntimeError(f"{name}: injected only {cursor}/{len(replacement)} bytes")
        print(f"Injected {name}: {cursor} bytes across {entry.block_count} blocks")

    rebuild_hash_tree(output_data, package)
    checked, failed = verify_hash_tree(output_data, package)
    if failed:
        raise RuntimeError(f"rebuilt STFS still has {failed}/{checked} invalid hash(es)")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(output_data)
    shutil.copystat(args.package, args.output)
    print(f"Rebuilt hash tree: {checked}/{checked} valid")
    print(f"Output: {args.output}")
    print("Note: Microsoft RSA signature preserved; RGH/JTAG signature bypass required.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
