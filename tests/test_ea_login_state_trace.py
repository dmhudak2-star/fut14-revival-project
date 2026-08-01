from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import fifa14_ea_login_state_trace as trace


class EaLoginStateTraceCodegenTests(unittest.TestCase):
    def test_reserved_regions_do_not_overlap(self) -> None:
        journal_end = trace.JOURNAL + len(trace.PROBES) * trace.RECORD_SIZE
        entries_end = (
            trace.ENTRY_STUB_BASE
            + len(trace.PROBES) * trace.ENTRY_STUB_STRIDE
        )
        returns_end = (
            trace.RETURN_STUB_BASE
            + len(trace.PROBES) * trace.RETURN_STUB_STRIDE
        )
        self.assertLessEqual(journal_end, trace.ENTRY_STUB_BASE)
        self.assertLessEqual(entries_end, trace.RETURN_STUB_BASE)
        self.assertLessEqual(returns_end, 0x83C8B000)

    def test_every_stub_fits_its_slot(self) -> None:
        for index, probe in enumerate(trace.PROBES):
            self.assertEqual(
                len(trace.build_entry_stub(index, probe)),
                trace.ENTRY_STUB_STRIDE,
            )
            if probe.return_site is not None:
                self.assertEqual(
                    len(trace.build_return_stub(index)),
                    trace.RETURN_STUB_STRIDE,
                )

    def test_entry_trampoline_returns_after_displaced_instruction(self) -> None:
        for index, probe in enumerate(trace.PROBES):
            image = trace.build_entry_stub(index, probe)
            meaningful = image.rstrip(b"\0")
            self.assertGreaterEqual(len(meaningful), 8)
            branch_word = int.from_bytes(meaningful[-4:], "big")
            branch_site = (
                trace.entry_stub_address(index) + len(meaningful) - 4
            )
            displacement = branch_word & 0x03FFFFFC
            if displacement & 0x02000000:
                displacement -= 0x04000000
            self.assertEqual(branch_site + displacement, probe.entry + 4)


if __name__ == "__main__":
    unittest.main()
