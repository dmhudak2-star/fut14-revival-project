from __future__ import annotations

import sys
from pathlib import Path


TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import fifa14_tu3_helperfunctions_runtime_patch as patch


class FakeXbdm:
    """Serves a synthetic region and records the order of the reads."""

    def __init__(self, base: int, image: bytes) -> None:
        self.base = base
        self.image = image
        self.reads: list[int] = []

    def read(self, address: int, length: int) -> bytes:
        self.reads.append(address)
        offset = address - self.base
        if offset < 0 or offset + length > len(self.image):
            raise RuntimeError("out of range")
        return self.image[offset : offset + length]


def build_image(size: int, apt_at: int) -> bytes:
    image = bytearray(b"\0" * size)
    signature_at = apt_at + patch.SIGNATURE_OFFSET
    image[signature_at : signature_at + len(patch.SIGNATURE)] = patch.SIGNATURE
    return bytes(image)


def test_scan_walks_a_region_downwards() -> None:
    base, size, chunk = 0xB0000000, 0x4000, 0x1000
    client = FakeXbdm(base, b"\0" * size)
    patch.scan_once(client, chunk, [(base, size, 0)])
    assert client.reads == sorted(client.reads, reverse=True)
    assert client.reads[0] == base + size - chunk


def test_scan_finds_a_signature_near_the_top_first() -> None:
    base, size, chunk = 0xB0000000, 0x20000, 0x1000
    # Leave room for the whole APT above the match, or the bounds check drops it.
    apt_at = 0x14000
    client = FakeXbdm(base, build_image(size, apt_at))
    hits = patch.scan_once(client, chunk, [(base, size, 0)])
    assert hits == [base + apt_at]


def test_scan_finds_a_signature_straddling_two_chunks() -> None:
    # The overlap must join a chunk to the one below it, not above.
    base, size, chunk = 0xB0000000, 0x20000, 0x1000
    # Place the signature so it spans a chunk boundary.
    apt_at = 0x8000 - patch.SIGNATURE_OFFSET - len(patch.SIGNATURE) // 2
    client = FakeXbdm(base, build_image(size, apt_at))
    hits = patch.scan_once(client, chunk, [(base, size, 0)])
    assert hits == [base + apt_at]


def test_scan_reports_nothing_for_an_empty_region() -> None:
    base, size, chunk = 0xB0000000, 0x2000, 0x1000
    client = FakeXbdm(base, b"\0" * size)
    assert patch.scan_once(client, chunk, [(base, size, 0)]) == []
