from __future__ import annotations

import sys
from pathlib import Path


TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import fifa14_cards_auth_credentials_patch as patch


def test_local_credentials_fit_owned_data_cave() -> None:
    image = patch.data_image()
    assert len(image) == patch.DATA_SIZE
    assert image.startswith(patch.SESSION_VALUE)
    assert image[0x40 :].startswith(patch.TOKEN_VALUE)


def test_gate_patches_load_r30_with_owned_strings() -> None:
    assert patch.SESSION_PATCHED == bytes.fromhex("3FC0897C 3BDEF200")
    assert patch.TOKEN_PATCHED == bytes.fromhex("3FC0897C 3BDEF240")
    assert patch.SESSION_ORIGINAL == bytes.fromhex("7C7E1B79 418200EC")
    assert patch.TOKEN_ORIGINAL == bytes.fromhex("7C7E1B79 41820098")
