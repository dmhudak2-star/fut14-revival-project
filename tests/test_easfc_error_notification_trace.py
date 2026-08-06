from __future__ import annotations

import struct
import sys
from pathlib import Path


TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import fifa14_easfc_error_notification_trace as trace
import fifa14_cards_auth_credentials_patch as credentials
import fifa14_cards_auth_endpoint_patch as endpoint


def words(image: bytes) -> list[int]:
    return [
        struct.unpack_from(">I", image, offset)[0]
        for offset in range(0, len(image), 4)
    ]


def test_stub_starts_with_the_displaced_retail_instruction() -> None:
    assert words(trace.build_stub())[0] == int.from_bytes(trace.ORIGINAL, "big")


def test_stub_fits_its_cave_and_returns_after_the_patched_site() -> None:
    stub = trace.build_stub()
    assert len(stub) == trace.STUB_SIZE
    encoded = words(stub)
    tail = next(
        index for index, word in enumerate(encoded) if (word >> 26) == 18
    )
    displacement = encoded[tail] & 0x03FFFFFC
    if displacement & 0x02000000:
        displacement -= 0x04000000
    assert trace.STUB + tail * 4 + displacement == trace.SITE + 4
    assert encoded[tail] & 1 == 0  # never link: the retail LR must survive


def test_stub_preserves_the_argument_and_linkage_registers() -> None:
    # Only r10/r11 may be written: r3-r5 are the traced arguments and r12
    # carries the link register the next retail instruction stores.
    for word in words(trace.build_stub())[1:]:
        opcode = word >> 26
        if opcode in (14, 15):  # addi / addis
            assert (word >> 21) & 0x1F in (10, 11)
        elif opcode == 32:  # lwz
            assert (word >> 21) & 0x1F in (10, 11)
        elif opcode == 21:  # rlwinm writes rA
            assert (word >> 16) & 0x1F in (10, 11)
        elif opcode == 31 and ((word >> 1) & 0x3FF) == 266:  # add
            assert (word >> 21) & 0x1F in (10, 11)


def test_journal_and_stub_do_not_overlap_the_other_cardsdll_patches() -> None:
    regions = [
        (trace.STUB, trace.STUB_SIZE),
        (trace.JOURNAL, trace.JOURNAL_SIZE),
    ]
    occupied = [
        (endpoint.STUB, endpoint.STUB_SIZE),
        (endpoint.JOURNAL, endpoint.JOURNAL_SIZE),
        (endpoint.URL_DATA, endpoint.URL_DATA_SIZE),
        (credentials.DATA, credentials.DATA_SIZE),
    ]
    for start, size in regions:
        for other_start, other_size in occupied:
            assert start + size <= other_start or other_start + other_size <= start


def test_site_patch_branches_into_the_stub() -> None:
    word = struct.unpack(">I", trace.site_patch())[0]
    displacement = word & 0x03FFFFFC
    if displacement & 0x02000000:
        displacement -= 0x04000000
    assert trace.SITE + displacement == trace.STUB


def test_kind_and_caller_descriptions_match_the_recovered_table() -> None:
    assert trace.describe_kind(0) == "TXT_EASFC_SERVER_ERROR"
    assert trace.describe_kind(1) == "TXT_EASFC_PLEASE_SIGN_IN"
    assert trace.describe_kind(2) == "TXT_EASFC_RECONNECTING"
    assert "unmapped" in trace.describe_kind(9)
    assert "0x89790C58" in trace.describe_caller(0x89790CBC)
    assert "unknown" in trace.describe_caller(0)


def test_hook_targets_the_shared_failure_text_routine() -> None:
    # Every localization path for the popup funnels through this routine, so
    # the trace must sit on it rather than on one of its three callers.
    assert trace.SITE == 0x8978C920
