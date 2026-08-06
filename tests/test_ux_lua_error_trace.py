from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import fifa14_ux_lua_error_trace as trace


class UxLuaErrorTraceCodegenTests(unittest.TestCase):
    def test_reserved_regions_do_not_overlap(self) -> None:
        self.assertEqual(
            trace.STUB_BASE + len(trace.PROBES) * trace.STUB_STRIDE,
            trace.JOURNAL,
        )
        self.assertEqual(
            trace.JOURNAL + len(trace.PROBES) * trace.RECORD_SIZE,
            trace.CAVE_END,
        )
        self.assertLessEqual(trace.CAVE_END, trace.NEXT_KNOWN_CAVE)

    def test_every_stub_fits_its_owned_slot(self) -> None:
        for probe in trace.PROBES:
            self.assertEqual(len(trace.build_stub(probe)), trace.STUB_STRIDE)

    def test_trampolines_resume_after_displaced_instruction(self) -> None:
        for probe in trace.PROBES:
            image = trace.build_stub(probe).rstrip(b"\0")
            branch_word = int.from_bytes(image[-4:], "big")
            branch_site = probe.stub + len(image) - 4
            displacement = branch_word & 0x03FFFFFC
            if displacement & 0x02000000:
                displacement -= 0x04000000
            self.assertEqual(branch_site + displacement, probe.site + 4)

    def test_missing_name_registers_match_retail_branches(self) -> None:
        self.assertEqual(trace.PROBES[0].string_register, 30)
        self.assertFalse(trace.PROBES[0].original_first)
        self.assertEqual(trace.PROBES[1].string_register, 5)
        self.assertTrue(trace.PROBES[1].original_first)


if __name__ == "__main__":
    unittest.main()
