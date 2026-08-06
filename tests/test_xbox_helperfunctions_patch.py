from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import fifa14_xbox_helperfunctions_patch as patcher


class XboxHelperFunctionsPatchTests(unittest.TestCase):
    def test_console_branches_are_big_endian_and_land_on_reviewed_blocks(self) -> None:
        expected = {
            0x2C62: 0x2CCC,
            0x2D6E: 0x2D74,
            0x2FC6: 0x307C,
        }
        self.assertEqual({item.offset: item.target for item in patcher.PATCHES}, expected)
        for item in patcher.PATCHES:
            self.assertEqual(
                patcher.branch_target(item.offset, item.replacement),
                item.target,
            )

    def test_patch_preserves_instruction_width(self) -> None:
        for item in patcher.PATCHES:
            self.assertEqual(len(item.expected), 6)
            self.assertEqual(len(item.replacement), 6)

    def test_record_fits_verified_archive_slot(self) -> None:
        self.assertLessEqual(patcher.RECORD_SIZE, patcher.SLOT_CAPACITY)
        self.assertEqual(patcher.RECORD_PATH_HASH, 0x56CC043AC27ECC11)


if __name__ == "__main__":
    unittest.main()
