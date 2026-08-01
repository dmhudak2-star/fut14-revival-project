from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import fifa14_xnet_startup_patch as xnet


class XNetStartupPatchTests(unittest.TestCase):
    def test_supported_branch_and_nop_are_exact(self) -> None:
        self.assertEqual(xnet.NOSECURE_MODE_BRANCH, 0x82D6DBFC)
        self.assertEqual(xnet.NOSECURE_MODE_ORIGINAL, bytes.fromhex("41820014"))
        self.assertEqual(xnet.NOSECURE_MODE_PATCHED, bytes.fromhex("60000000"))
        self.assertEqual(xnet.XNET_BYPASS_BRANCH, 0x82D6DD00)
        self.assertEqual(xnet.XNET_BYPASS_ORIGINAL, bytes.fromhex("409A0008"))
        self.assertEqual(xnet.XNET_BYPASS_PATCHED, bytes.fromhex("60000000"))


if __name__ == "__main__":
    unittest.main()
