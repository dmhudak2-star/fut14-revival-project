from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import fifa14_fut_launcher_transition_trace as trace


class FutLauncherTransitionTraceCodegenTests(unittest.TestCase):
    def test_reserved_regions_do_not_overlap(self) -> None:
        stubs_end = trace.STUB_BASE + len(trace.PROBES) * trace.STUB_STRIDE
        ring_end = trace.RING + trace.RING_COUNT * trace.RECORD_SIZE
        self.assertEqual(stubs_end, trace.JOURNAL)
        self.assertLessEqual(trace.JOURNAL + 4, trace.RING)
        self.assertEqual(ring_end, trace.CAVE_END)
        self.assertLessEqual(trace.CAVE_END, trace.NEXT_KNOWN_CAVE)

    def test_every_stub_fits_its_owned_slot(self) -> None:
        for probe in trace.PROBES:
            self.assertEqual(len(trace.build_stub(probe)), trace.STUB_STRIDE)

    def test_entry_trampoline_resumes_after_displaced_mflr(self) -> None:
        for probe in trace.PROBES:
            image = trace.build_stub(probe).rstrip(b"\0")
            branch_word = int.from_bytes(image[-4:], "big")
            branch_site = probe.stub + len(image) - 4
            displacement = branch_word & 0x03FFFFFC
            if displacement & 0x02000000:
                displacement -= 0x04000000
            self.assertEqual(branch_site + displacement, probe.site + 4)

    def test_callback_probe_is_narrowly_filtered_to_screen_event(self) -> None:
        callback = next(
            probe for probe in trace.PROBES
            if probe.name == "screen_notification_callback"
        )
        self.assertEqual(callback.event_filter, 0x276A)


if __name__ == "__main__":
    unittest.main()
