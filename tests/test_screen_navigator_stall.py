from __future__ import annotations

import sys
from pathlib import Path


TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import fifa14_screen_navigator as navigator


class Console:
    """A console whose screens respond only to the buttons they really take."""

    def __init__(self, script: list[tuple[str, str | None, str]]) -> None:
        # Each entry is (screen, the button that advances it, the next screen).
        self.script = {entry[0]: entry for entry in script}
        self.screen = script[0][0]
        self.presses: list[tuple[str, str]] = []

    def observe(self, _host: str) -> tuple[str, int]:
        return self.screen, 0

    def press(self, _host: str, button: str, _frames: int) -> None:
        self.presses.append((self.screen, button))
        _name, accepts, following = self.script[self.screen]
        if button == accepts:
            self.screen = following


def run(console: Console, target: str, monkeypatch, timeout: float = 60.0) -> int:
    monkeypatch.setattr(navigator, "observe", console.observe)
    monkeypatch.setattr(navigator, "press", console.press)
    monkeypatch.setattr(navigator.time, "sleep", lambda _seconds: None)
    return navigator.navigate(
        "console", target, timeout=timeout, interval=0.0, frames=1
    )


def test_a_misclassified_screen_still_gets_navigated(monkeypatch) -> None:
    # An attract video matched as "fut_error": its A does nothing, only START
    # skips it.  Without a stall-breaker the run hangs here until it times out.
    console = Console(
        [("fut_error", "START", "main_menu"), ("main_menu", None, "main_menu")]
    )
    assert run(console, "main_menu", monkeypatch) == 0
    assert ("fut_error", "START") in console.presses


def test_a_real_dialog_is_answered_with_its_own_button(monkeypatch) -> None:
    # The breaker must not cost a correctly classified screen anything: the
    # first press has to be the button that screen actually prompts for.
    console = Console(
        [("notice", "A", "main_menu"), ("main_menu", None, "main_menu")]
    )
    assert run(console, "main_menu", monkeypatch) == 0
    assert console.presses[0] == ("notice", "A")
    assert len(console.presses) == 1


def test_the_breaker_waits_out_the_stall_limit(monkeypatch) -> None:
    console = Console(
        [("fut_error", "START", "main_menu"), ("main_menu", None, "main_menu")]
    )
    run(console, "main_menu", monkeypatch)
    before = console.presses[: navigator.STALL_LIMIT]
    assert [button for _screen, button in before] == ["A"] * navigator.STALL_LIMIT


def test_a_screen_with_no_button_is_left_alone(monkeypatch) -> None:
    # profile_chooser needs a sign-in this setup cannot do; mashing buttons at
    # it would start one.  It must time out untouched instead.
    console = Console([("profile_chooser", None, "profile_chooser")])
    try:
        run(console, "main_menu", monkeypatch, timeout=0.2)
    except TimeoutError:
        pass
    assert console.presses == []


def test_start_is_tried_before_risking_the_sign_in_blade() -> None:
    # A at the title screen opens an Xbox Live sign-in this setup cannot
    # finish, so it must never be the first guess at an unknown screen.
    assert navigator.UNKNOWN_BUTTONS[0] == "START"
    assert navigator.UNKNOWN_BUTTONS.count("A") == 1
    assert navigator.UNKNOWN_BUTTONS[-1] == "A"


def test_an_unknown_dialog_needing_A_is_still_reached(monkeypatch) -> None:
    console = Console(
        [("unknown", "A", "main_menu"), ("main_menu", None, "main_menu")]
    )
    assert run(console, "main_menu", monkeypatch) == 0
    assert ("unknown", "A") in console.presses
