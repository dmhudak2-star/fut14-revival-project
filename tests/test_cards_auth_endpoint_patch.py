from __future__ import annotations

import sys
from pathlib import Path


TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import fifa14_cards_auth_endpoint_patch as patch


def test_endpoint_images_fit_exact_native_slots() -> None:
    retail = patch.image(patch.RETAIL_URL)
    local = patch.image(b"http://192.168.1.36:18080")
    assert len(retail) == patch.URL_SLOT_SIZE
    assert len(local) == patch.URL_SLOT_SIZE
    assert retail.rstrip(b"\0") == patch.RETAIL_URL
    assert local.rstrip(b"\0") == b"http://192.168.1.36:18080"


def test_entry_hook_fits_before_credentials_patch_and_preserves_mflr() -> None:
    local_url = b"http://192.168.1.36:18080"
    stub = patch.build_stub(local_url)
    assert len(stub) == patch.STUB_SIZE
    assert stub.startswith(patch.ORIGINAL)
    assert patch.URL_DATA + patch.URL_DATA_SIZE <= 0x897BF200
    assert patch.url_data_image(local_url).rstrip(b"\0") == local_url


def test_entry_hook_writes_each_url_word_to_both_native_slots() -> None:
    local_url = b"http://192.168.1.36:18080"
    stub = patch.build_stub(local_url)
    expected_writes = bytearray()
    for offset in range(0, patch.URL_DATA_SIZE, 4):
        expected_writes.extend(patch.insn(patch.lwz(10, 11, offset)))
        expected_writes.extend(
            patch.insn(patch.stw(10, 3, patch.URL_OFFSET + offset))
        )
        expected_writes.extend(
            patch.insn(
                patch.stw(
                    10,
                    3,
                    patch.URL_OFFSET + patch.URL_SLOT_SIZE + offset,
                )
            )
        )
    assert bytes(expected_writes) in stub


def test_endpoint_hook_and_legacy_trace_use_distinct_branches() -> None:
    assert patch.site_patch() != patch.insn(
        patch.branch(patch.SITE, patch.legacy_trace.STUB, False)
    )
