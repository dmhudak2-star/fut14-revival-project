#!/usr/bin/env python3
"""Build a cards0 pair whose fcc_login1 never creates its loading popup.

``fcc_login1::BeginLogin`` builds a zero-button standard popup whose message is
the literal ``Loading``.  Retail deletes it only in ``ContinueToCreateClub``,
so any route that leaves ``futLogIn1`` another way carries the popup with it --
and a screen with a modal up ignores navigation transitions, which is why
dispatching ``iceBreaker`` into the live flow was accepted and moved nothing.

The fix is the one the PC revival project settled on.  Its reviewed retail
sequence is::

    isPopupUpById(...); push true; EQUALS2; NOT; NOT; BRANCHIFTRUE skip_popup

Turning the one-byte ``EQUALS2`` (0x49) into ``OR`` (0x11) makes the condition
true, because one operand is the literal true, so the existing branch skips
popup construction.  Opcode width, function size, branch encoding and stack
depth are all unchanged.  ``ShowLoadingIcon`` is also rewritten to
``HideLoadingIcon`` -- same length -- so no login-stage indicator is left
running in its place.

Their offsets were recovered from the PC build; this console's APT turned out
to be byte-identical at the same offsets, so the same two edits apply here.

The patched resource is re-compressed and written back into the slot it came
from.  Relocating it to the end of the archive instead -- which is what this
tool did before there was an encoder -- boots the title to a black screen even
with the archive's declared length corrected, so the loader wants the resource
where its directory record has always pointed.
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
from lzx_encode import encode_container


SCREEN_ENTRY = "data/ui/external/ion_fut/screens/fcc_login1.big"

# The APT inside the screen's inner BIGF, and the reviewed compare within it.
APT_ENTRY_OFFSET = 0x40
POPUP_COMPARE_APT_OFFSET = 0xCA
RETAIL_OPCODE = 0x49  # EQUALS2
PATCHED_OPCODE = 0x11  # OR

# Bytes either side of the opcode, so a build whose APT differs is refused
# rather than silently patched at the wrong place.
CONTEXT_PREFIX = bytes.fromhex("b901af07af085ab901af04af09a20a5273")
CONTEXT_SUFFIX = bytes.fromhex("12129d")

SHOW = b"ShowLoadingIcon"
HIDE = b"HideLoadingIcon"

BH_BASE, BH_STRIDE = 20, 20


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_directory(big: Path) -> list[tuple[int, int, int, int, str]]:
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
        entries.append(
            (index, table_offset, offset, size, head[cursor:end].decode("ascii", "replace"))
        )
        cursor = end + 1
    return entries


def uncompressed_container(payload: bytes) -> bytes:
    """Wrap a decoded resource the way this archive's entries are wrapped."""
    return (
        bytes(4)
        + b"chunkunc"
        + struct.pack(
            ">10I", 2, len(payload), 0x40000, 1, 0x10, 0, 0, 0, len(payload), 4
        )
        + payload
    )


def patch_screen(decoded: bytes) -> tuple[bytes, dict]:
    """Skip the popup, and stop the loading indicator it was paired with."""
    opcode_offset = APT_ENTRY_OFFSET + POPUP_COMPARE_APT_OFFSET
    anchor = opcode_offset - len(CONTEXT_PREFIX)
    if decoded[anchor:opcode_offset] != CONTEXT_PREFIX:
        raise RuntimeError("Reviewed popup compare is not where this build has it")
    after = decoded[opcode_offset + 1 : opcode_offset + 1 + len(CONTEXT_SUFFIX)]
    if after != CONTEXT_SUFFIX:
        raise RuntimeError("Reviewed popup branch does not follow the compare")
    if decoded[opcode_offset] == PATCHED_OPCODE:
        raise RuntimeError("This screen is already patched")
    if decoded[opcode_offset] != RETAIL_OPCODE:
        raise RuntimeError(
            f"Unexpected opcode {decoded[opcode_offset]:#04x} at the popup compare"
        )

    output = bytearray(decoded)
    output[opcode_offset] = PATCHED_OPCODE
    show = output.find(SHOW)
    if show < 0:
        raise RuntimeError("ShowLoadingIcon is not in this screen")
    if output.find(SHOW, show + 1) >= 0:
        raise RuntimeError("More than one ShowLoadingIcon; refusing to guess")
    output[show : show + len(SHOW)] = HIDE

    changed = [i for i, (a, b) in enumerate(zip(decoded, output)) if a != b]
    expected = {opcode_offset} | {
        show + i for i, (a, b) in enumerate(zip(SHOW, HIDE)) if a != b
    }
    if set(changed) != expected:
        raise RuntimeError(f"Unexpected byte changes: {changed}")
    return bytes(output), {
        "opcode_offset": opcode_offset,
        "show_offset": show,
        "changed": changed,
    }


def slot_capacity(entries: list, offset: int) -> int:
    """Bytes available where this entry sits, up to the next one."""
    following = sorted(entry[2] for entry in entries if entry[2] > offset)
    if not following:
        raise RuntimeError("This entry is last; its capacity is unbounded here")
    return following[0] - offset


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
    args = parser.parse_args()

    entries = read_directory(args.source_big)
    index, table_offset, offset, size, _name = next(
        e for e in entries if e[4].lower() == SCREEN_ENTRY
    )
    with args.source_big.open("rb") as stream:
        stream.seek(offset)
        decoded = decode_container(stream.read(size))
    patched, info = patch_screen(decoded)
    payload = encode_container(patched)
    if decode_container(payload) != patched:
        raise RuntimeError("Re-encoded screen does not decode back to itself")

    slot = slot_capacity(entries, offset)
    if len(payload) > slot:
        raise RuntimeError(
            f"Patched screen is {len(payload):#x} bytes and its slot is {slot:#x}"
        )

    shutil.copyfile(args.source_big, args.output_big)
    shutil.copyfile(args.source_bh, args.output_bh)

    # Written in place: same offset, same slot, only the stored length changes.
    with args.output_big.open("r+b") as stream:
        stream.seek(offset)
        stream.write(payload)
        stream.write(bytes(slot - len(payload)))
        stream.seek(table_offset)
        stream.write(struct.pack(">II", offset, len(payload)))

    record = BH_BASE + index * BH_STRIDE
    with args.output_bh.open("r+b") as stream:
        stream.seek(record)
        if struct.unpack(">II", stream.read(8)) != (offset, size):
            raise RuntimeError(f"Unexpected BH record for index {index}")
        stream.seek(record)
        stream.write(struct.pack(">II", offset, len(payload)))

    print(f"{SCREEN_ENTRY}: index={index}")
    print(f"  popup compare  {RETAIL_OPCODE:#04x} -> {PATCHED_OPCODE:#04x} "
          f"at decoded {info['opcode_offset']:#x}")
    print(f"  ShowLoadingIcon -> HideLoadingIcon at {info['show_offset']:#x}")
    print(f"  offset={offset:#x} unchanged; size {size:#x} -> {len(payload):#x} "
          f"in a {slot:#x} slot")
    print(f"BIG SHA-256: {sha256(args.output_big)}")
    print(f"BH  SHA-256: {sha256(args.output_bh)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
