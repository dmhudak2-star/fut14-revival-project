#!/usr/bin/env python3
"""Build a data1 pair that routes futLogIn1's advance to the icebreaker.

This is the Xbox 360 equivalent of the FIFA-14-Ultimate-Team-Personal-Revival-
Project's NAV route patch, which redirects ``futLogIn1 --advance--> futLogIn2``
to ``iceBreaker`` so the flow reaches the captain selector instead of the step
that stalls.

Our build already declares an ``iceBreaker`` transition on ``futLogIn1``, so
the event exists -- but the screen only chooses it inside the callback that
never fires, and injecting the event by hand does nothing while a modal popup
is up.  Rerouting ``advance`` puts the icebreaker on the path the screen takes
by itself.

Two archive details make this awkward and are handled here rather than
assumed:

* the resource is LZX-compressed and there is no encoder in this repository,
  so the patched graph is stored in the uncompressed ``chunkunc`` container;
* that makes it larger than its slot, so the entry is appended to the end of
  the archive and its directory record repointed, rather than written in
  place.  ``data1.bh`` mirrors the same record and is updated to match.

Nothing else in either file is touched, and the original pair is left alone.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lzx_decode import decode_container


NAV_ENTRY = "data/ui/nav/fut/futloginflow.nav"

# futLogIn1's own transition block.  Matching the target together with the
# screen that precedes it keeps this from hitting createClub's identical
# `"targets":["futLogIn2"]`, which must keep pointing where it does.
ANCHOR = b'"name":"futLogIn1"'
ORIGINAL_TRANSITION = b'"event":"advance"\r\n\t\t\t\t\t,"targets":["futLogIn2"]'
PATCHED_TRANSITION = b'"event":"advance"\r\n\t\t\t\t\t,"targets":["iceBreaker"]'

# The BIG4 directory and the .bh index both carry (offset, size) big-endian.
BH_BASE, BH_STRIDE = 20, 20


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_directory(big: Path) -> tuple[int, list[tuple[int, int, int, str]]]:
    """Return the BIG4 base and every (index, table_offset, size, name)."""
    with big.open("rb") as stream:
        head = stream.read(1 << 20)
    base = head.find(b"BIG4")
    if base < 0:
        raise RuntimeError("Not a BIG4 archive")
    count = struct.unpack(">I", head[base + 8 : base + 12])[0]
    entries = []
    cursor = base + 16
    for index in range(count):
        table_offset = cursor
        offset, size = struct.unpack(">II", head[cursor : cursor + 8])
        cursor += 8
        end = head.find(b"\0", cursor)
        name = head[cursor:end].decode("ascii", "replace")
        cursor = end + 1
        entries.append((index, table_offset, offset, size, name))
    return base, entries


def uncompressed_container(payload: bytes) -> bytes:
    """Wrap a decoded resource the way the title's loader reads it.

    The four zero bytes ahead of the magic are part of every entry in this
    archive; a container written without them is rejected as unrecognised.
    """
    return (
        bytes(4)
        + b"chunkunc"
        + struct.pack(
            ">10I", 2, len(payload), 0x40000, 1, 0x10, 0, 0, 0, len(payload), 4
        )
        + payload
    )


def reroute(graph: bytes) -> bytes:
    """Point futLogIn1's advance at iceBreaker, and only that transition."""
    anchor = graph.find(ANCHOR)
    if anchor < 0:
        raise RuntimeError("futLogIn1 is not in this graph")
    position = graph.find(ORIGINAL_TRANSITION, anchor)
    if position < 0:
        raise RuntimeError("futLogIn1's advance transition does not match")
    following = graph.find(ORIGINAL_TRANSITION, position + 1)
    if following >= 0 and following < graph.find(b'"name":"createClub"'):
        raise RuntimeError("Ambiguous advance transition inside futLogIn1")
    return (
        graph[:position]
        + PATCHED_TRANSITION
        + graph[position + len(ORIGINAL_TRANSITION) :]
    )


def declare_new_length(stream) -> None:
    """Rewrite both length fields an appended archive must agree with.

    A BIG4 archive states its own byte length twice: in the four bytes ahead
    of the magic, and in the header word right after it, both little-endian.
    Appending a relocated resource without updating them leaves the entry past
    the declared end, where the loader will not read it -- which takes the
    title down at boot rather than failing visibly.
    """
    import struct as _struct

    stream.seek(0, 2)
    length = stream.tell()
    stream.seek(0)
    head = stream.read(64)
    base = head.find(b"BIG4")
    if base < 0:
        raise RuntimeError("Not a BIG4 archive")
    stream.seek(base - 4)
    stream.write(_struct.pack("<I", length))
    stream.seek(base + 4)
    stream.write(_struct.pack("<I", length))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_big", type=Path)
    parser.add_argument("source_bh", type=Path)
    parser.add_argument("output_big", type=Path)
    parser.add_argument("output_bh", type=Path)
    parser.add_argument(
        "--dump", type=Path, help="also write the patched graph as plain text"
    )
    args = parser.parse_args()

    _base, entries = read_directory(args.source_big)
    entry = next(e for e in entries if e[4].lower() == NAV_ENTRY)
    index, table_offset, offset, size, _name = entry

    with args.source_big.open("rb") as stream:
        stream.seek(offset)
        graph = decode_container(stream.read(size))
    patched = reroute(graph)
    if args.dump:
        args.dump.write_bytes(patched)
    payload = uncompressed_container(patched)

    shutil.copyfile(args.source_big, args.output_big)
    shutil.copyfile(args.source_bh, args.output_bh)

    with args.output_big.open("r+b") as stream:
        stream.seek(0, 2)
        # Keep the appended resource aligned the way every other entry is.
        new_offset = (stream.tell() + 0x7F) & ~0x7F
        stream.write(bytes(new_offset - stream.tell()))
        stream.write(payload)
        stream.seek(table_offset)
        stream.write(struct.pack(">II", new_offset, len(payload)))
        declare_new_length(stream)

    record = BH_BASE + index * BH_STRIDE
    with args.output_bh.open("r+b") as stream:
        stream.seek(record)
        if struct.unpack(">II", stream.read(8)) != (offset, size):
            raise RuntimeError(f"Unexpected BH record for index {index}")
        stream.seek(record)
        stream.write(struct.pack(">II", new_offset, len(payload)))

    print(f"{NAV_ENTRY}: index={index}")
    print(f"  was  offset={offset:#x} size={size:#x} (LZX)")
    print(f"  now  offset={new_offset:#x} size={len(payload):#x} (uncompressed)")
    print(f"BIG SHA-256: {sha256(args.output_big)}")
    print(f"BH  SHA-256: {sha256(args.output_bh)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
