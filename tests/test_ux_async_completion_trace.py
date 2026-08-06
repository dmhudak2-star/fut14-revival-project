from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import fifa14_ux_async_completion_trace as trace


class UxAsyncCompletionTraceCodegenTests(unittest.TestCase):
    def test_reserved_regions_do_not_overlap(self) -> None:
        self.assertLessEqual(trace.STUB + trace.STUB_SIZE, trace.JOURNAL)
        self.assertLessEqual(trace.JOURNAL + 4, trace.RING)
        self.assertEqual(
            trace.RING + trace.RING_COUNT * trace.RECORD_SIZE,
            trace.CAVE_END,
        )
        self.assertLessEqual(trace.CAVE_END, trace.NEXT_KNOWN_CAVE)

    def test_stub_fits_owned_slot(self) -> None:
        self.assertEqual(len(trace.STUB_BYTES), trace.STUB_SIZE)

    def test_ring_address_uses_valid_ppc_add(self) -> None:
        self.assertIn((0x7D284A14).to_bytes(4, "big"), trace.STUB_BYTES)
        self.assertNotIn((0x7D284214).to_bytes(4, "big"), trace.STUB_BYTES)

    def test_ring_slot_uses_atomic_sequence_register(self) -> None:
        self.assertIn((0x7149000F).to_bytes(4, "big"), trace.STUB_BYTES)
        self.assertNotIn((0x7129000F).to_bytes(4, "big"), trace.STUB_BYTES)

    def test_record_size_covers_handler_snapshot(self) -> None:
        self.assertEqual(trace.RECORD_SIZE, 0x60)

    def test_trampoline_resumes_after_displaced_mflr(self) -> None:
        image = trace.STUB_BYTES.rstrip(b"\0")
        branch_word = int.from_bytes(image[-4:], "big")
        branch_site = trace.STUB + len(image) - 4
        displacement = branch_word & 0x03FFFFFC
        if displacement & 0x02000000:
            displacement -= 0x04000000
        self.assertEqual(branch_site + displacement, trace.SITE + 4)


if __name__ == "__main__":
    unittest.main()
