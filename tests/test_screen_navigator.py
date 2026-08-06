from __future__ import annotations

import itertools
import sys
from pathlib import Path

import pytest


TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import fifa14_screen_navigator as navigator
import xbox360_virtual_input as virtual_input


def solid(width: int, height: int, colour: tuple[int, int, int]) -> bytes:
    return bytes(colour) * (width * height)


def test_signature_has_one_average_colour_per_grid_cell() -> None:
    result = navigator.signature(64, 48, solid(64, 48, (10, 20, 30)))
    assert len(result) == navigator.SIGNATURE_LENGTH
    assert set(result[0::3]) == {10}
    assert set(result[1::3]) == {20}
    assert set(result[2::3]) == {30}


def test_signature_is_independent_of_resolution() -> None:
    small = navigator.signature(64, 48, solid(64, 48, (90, 100, 110)))
    large = navigator.signature(320, 240, solid(320, 240, (90, 100, 110)))
    assert small == large


def test_signature_distinguishes_a_changed_region() -> None:
    width, height = 64, 48
    frame = bytearray(solid(width, height, (0, 0, 0)))
    for y in range(height // 2):
        for x in range(width // 2):
            pixel = (y * width + x) * 3
            frame[pixel : pixel + 3] = b"\xff\xff\xff"
        # Only the top-left quadrant is white, so only its cells may change.
    result = navigator.signature(width, height, bytes(frame))
    assert max(result) == 255
    assert min(result) == 0


def test_every_embedded_signature_has_the_expected_length() -> None:
    for name, references in navigator.SIGNATURES.items():
        assert references, name
        for reference in references:
            assert len(reference) == navigator.SIGNATURE_LENGTH, name


def test_embedded_signatures_stay_separable_by_a_wide_margin() -> None:
    # Real captures of one screen drift by under 40; keeping every distinct
    # pair above twice the match threshold means drift can never reach a
    # neighbouring class.
    for first, second in itertools.combinations(navigator.SIGNATURES, 2):
        for left in navigator.SIGNATURES[first]:
            for right in navigator.SIGNATURES[second]:
                measured = navigator.distance(left, right)
                assert measured > 2 * navigator.MATCH_THRESHOLD, (
                    first,
                    second,
                    measured,
                )


def test_classify_returns_the_exact_screen_for_a_known_signature() -> None:
    for name, references in navigator.SIGNATURES.items():
        for reference in references:
            assert navigator.classify(reference) == (name, 0)


def test_a_screen_may_carry_several_references() -> None:
    # The dimmed dialog is far enough from the bright one that a single
    # reference cannot cover both.
    bright, dim = navigator.SIGNATURES["fut_error"]
    assert navigator.distance(bright, dim) > navigator.MATCH_THRESHOLD


def test_classify_tolerates_drift_up_to_the_threshold() -> None:
    # Real drift moves individual cells while the frame keeps its overall
    # light-to-dark range, so nudge a mid-range cell and leave the extremes.
    reference = navigator.SIGNATURES["main_menu"][0]
    low, high = min(reference), max(reference)
    index = next(
        position
        for position, value in enumerate(reference)
        if low < value - navigator.MATCH_THRESHOLD
        and value + navigator.MATCH_THRESHOLD < high
    )
    nudged = bytearray(reference)
    nudged[index] = reference[index] + navigator.MATCH_THRESHOLD
    name, measured = navigator.classify(bytes(nudged))
    assert name == "main_menu"
    assert measured == navigator.MATCH_THRESHOLD


def test_classify_reports_unknown_for_an_unrelated_frame() -> None:
    mid_grey = bytes([128]) * navigator.SIGNATURE_LENGTH
    name, measured = navigator.classify(mid_grey)
    assert name == "unknown"
    assert measured > navigator.MATCH_THRESHOLD


def test_every_known_screen_has_an_action_entry() -> None:
    assert set(navigator.ACTIONS) == set(navigator.SIGNATURES) | {"unknown"}
    for screen in navigator.PATIENT:
        assert navigator.ACTIONS[screen] is None
    for screen, button in navigator.ACTIONS.items():
        if button is not None:
            assert button in virtual_input.BUTTONS, screen


def test_pulse_rejects_unusable_requests() -> None:
    with pytest.raises(RuntimeError):
        virtual_input.pulse(None, "NOPE", 4)
    with pytest.raises(RuntimeError):
        virtual_input.pulse(None, "A", 0)
    with pytest.raises(RuntimeError):
        virtual_input.pulse(None, "A", 121)


def test_attract_frames_are_rechecked_faster_than_settled_screens() -> None:
    # A skip only reveals the title screen for a couple of seconds; polling at
    # the settled cadence would skip the next video instead and loop forever.
    assert navigator.SKIP_INTERVAL < 4
    assert navigator.ACTIONS["unknown"] == "START"


def test_a_blank_dark_frame_is_not_mistaken_for_the_dimmed_dialog() -> None:
    _, dim = navigator.SIGNATURES["fut_error"]
    # Close in absolute terms, but with none of the dialog's bright band.
    assert navigator.distance(bytes(navigator.SIGNATURE_LENGTH), dim) < (
        navigator.MATCH_THRESHOLD
    )
    assert navigator.classify(bytes(navigator.SIGNATURE_LENGTH))[0] == "unknown"


def test_contrast_measures_the_spread_of_a_signature() -> None:
    assert navigator.contrast(bytes(navigator.SIGNATURE_LENGTH)) == 0
    assert navigator.contrast(bytes([0, 255])) == 255
