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
    for name, reference in navigator.SIGNATURES.items():
        assert len(reference) == navigator.SIGNATURE_LENGTH, name


def test_embedded_signatures_stay_separable_by_a_wide_margin() -> None:
    # Real captures of one screen drift by under 40; keeping every distinct
    # pair above twice the match threshold means drift can never reach a
    # neighbouring class.
    for first, second in itertools.combinations(navigator.SIGNATURES, 2):
        measured = navigator.distance(
            navigator.SIGNATURES[first], navigator.SIGNATURES[second]
        )
        assert measured > 2 * navigator.MATCH_THRESHOLD, (first, second, measured)


def test_classify_returns_the_exact_screen_for_a_known_signature() -> None:
    for name, reference in navigator.SIGNATURES.items():
        assert navigator.classify(reference) == (name, 0)


def test_classify_tolerates_drift_up_to_the_threshold() -> None:
    reference = navigator.SIGNATURES["main_menu"]
    drift = navigator.MATCH_THRESHOLD
    nudged = bytes(min(255, value + drift) for value in reference)
    name, measured = navigator.classify(nudged)
    assert name == "main_menu"
    assert measured <= drift


def test_classify_reports_unknown_for_an_unrelated_frame() -> None:
    name, measured = navigator.classify(bytes(navigator.SIGNATURE_LENGTH))
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
