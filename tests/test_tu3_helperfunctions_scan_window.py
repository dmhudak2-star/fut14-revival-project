from __future__ import annotations

import sys
from pathlib import Path


TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import fifa14_tu3_helperfunctions_runtime_patch as patch


def test_window_keeps_only_the_overlapping_span() -> None:
    far = (0xB0000000, 0x1000000, 0)
    near = (0xBDC00000, 0x1000000, 0)
    clipped = patch.clip_to_window([far, near], 0xBDD78000, 0x400000)
    # The far region is dropped; the near one is cut at the window's top edge.
    assert clipped == [(0xBDC00000, 0xBDF78000 - 0xBDC00000, 0)]


def test_window_clips_both_ends_inside_one_region() -> None:
    regions = [(0xB0000000, 0x20000000, 7)]
    clipped = patch.clip_to_window(regions, 0xBDD78000, 0x400000)
    assert clipped == [(0xBDB78000, 0x400000, 7)]


def test_window_drops_regions_that_do_not_overlap() -> None:
    regions = [(0xA0000000, 0x1000, 0), (0xDF000000, 0x1000, 0)]
    assert patch.clip_to_window(regions, 0xBDD78000, 0x400000) == []


def test_window_is_centred_on_the_observed_neighbourhood() -> None:
    # Every run that located the APT found it here, so the default first pass
    # has to contain those addresses.
    low = patch.OBSERVED_APT_NEIGHBOURHOOD - patch.DEFAULT_HINT_WINDOW // 2
    high = patch.OBSERVED_APT_NEIGHBOURHOOD + patch.DEFAULT_HINT_WINDOW // 2
    for observed in (0xBDD78B00, 0xBDD77B00):
        assert low <= observed < high


def test_window_stays_far_smaller_than_a_full_sweep() -> None:
    assert patch.DEFAULT_HINT_WINDOW <= 0x1000000


class RegionListing:
    def __init__(self, regions):
        self._regions = regions

    def regions(self):
        return self._regions


def test_small_regions_are_searched_before_the_big_heap() -> None:
    listing = RegionListing(
        [
            (0xB3430000, 0x0C820000, 4),
            (0xBFE00000, 0x00200000, 4),
            (0xA6110000, 0x006A0000, 4),
        ]
    )
    ordered = patch.candidates(listing)
    above, below = ordered[:2], ordered[2:]
    assert [size for _, size, _ in above] == sorted(size for _, size, _ in above)
    assert above[0][0] == 0xBFE00000
    assert below[0][0] == 0xA6110000
