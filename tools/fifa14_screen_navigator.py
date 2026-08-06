#!/usr/bin/env python3
"""Drive FIFA 14 from boot to a named screen without a physical controller.

Reaching the FUT entry point takes several minutes of intro videos, a storage
selector and an autosave notice, and a blind sequence of button presses fails
often enough to dominate the cost of every live experiment.

This navigator closes the loop instead: it captures the framebuffer, reduces it
to a small colour grid, matches that against signatures recovered from real
captures, and presses the button that screen expects.  It only sends the retail
button the screen already prompts for; it never writes game state, publishes an
event, or forces a navigation action.
"""

from __future__ import annotations

import argparse
import time

from xbdm_screenshot import capture, decode_bgrx_tiled
import xbox360_virtual_input as virtual_input


GRID_X, GRID_Y = 8, 6
SIGNATURE_LENGTH = GRID_X * GRID_Y * 3

# Recovered from captures of this build.  Repeated captures of one screen drift
# by at most 37, while the closest distinct pair sits at 88, so a threshold
# below half that separation cannot confuse two screens even at full drift.
# Anything further away is reported as "unknown", which is what the attract
# videos between screens look like.
MATCH_THRESHOLD = 43

# A dimmed dialog sits close to a plain dark frame in absolute terms, so
# distance alone would call a black loading screen a dialog.  Requiring similar
# contrast separates them: the dialog keeps a bright band over a dark backdrop,
# a blank frame has none.
CONTRAST_TOLERANCE = 15


def contrast(image: bytes) -> int:
    return max(image) - min(image)


SIGNATURES = {
    "title": (bytes.fromhex(
        "80909C7B8993A2ACB3CBD3D7D7DEE2D2DCE0A9B5BCA2ABAE96A5B0BCC4C9DDE0"
        "E2E2DBDCDDD3D4CED7DAB8C2C6BDC6C6D5E4E9DDE2E4DDDEDFC0C4C5DAD1D4D8"
        "DFE5DFE6E8D8DFDFD9E7EAD7DFE1C1C4C6929495BC9598C1CCD5C0CBCFA1AAAD"
        "BDD0D8C2CED4CED6DAC6CED2ACB7BD8C9AA25E6B73515B5EA1B5C0ACBDC6BBC9"
        "CFB1BFC68B9AA465748048565E495257"
    ),),
    "storage": (bytes.fromhex(
        "1C1F22281F2C331F363B243D422B4338243B2D213024262721242668536C9562"
        "979A5A9C9C5B9E955B97714E732A2C2C2E3233979A9CD7DBDCDADEDFDBDFE0DB"
        "DFE0AEB2B32F31312F32339EA2A3D4D9DBD4D9DBD4D9DBD4D9DBA7ABAD232526"
        "292D2F989D9ECED3D5CED3D5CED3D5CED3D59DA2A31113142327293A3F404445"
        "47404445373B3D2F33352428290F1112"
    ),),
    # Informational dialogs whose only action is OK/Continuer: the autosave
    # notice and the "sign in to Xbox Live" notice after a console reboot.
    # They are grey panels over the same backdrop and sit only 57 apart, which
    # this grid cannot separate -- but they take the same button, so the
    # navigator does not need to tell them apart.
    "notice": (bytes.fromhex(
        "3A434B384047474E54595E635E64675C63674B5358484F52434B5251585C5E62"
        "656065675E6366585F624E555951575A61686C6165689A9EA1A3A6AAA0A4A7A9"
        "ADB05E64675B6264646A6E6166699B9FA0ACAFB2ACAFB2B2B5B8575E624C5256"
        "5D656A5E64675C61645A5F6253585D464D52394147363C40545D63575E635A61"
        "65575E634A51573A42492F373D30363B"
    ), bytes.fromhex(
        "3A434B384147484F54595F635F64675C63674B5358484F52444C5353595D6165"
        "6863676A6065685B616451585C545A5C5D666A61656886898C8A8E908A8E918E"
        "929562676A5F64675F676B5F64677478797C7F82797D80797D80555C60484E52"
        "545E64565D625B61645A60644F565B40484E2C343A272D314852594D565C535B"
        "6050585D40494F2F383F232C31242A2E"
    )),
    "main_menu": (bytes.fromhex(
        "AEB9C4D3D7E0E2E9F2E8EDF3EEF1F6D9DEE4D4DDE3C0C9D17880878F969A9CA6"
        "AB92A1A7CDD4D9D2DADEBCC5CB737B8632424A1929304F66688C7E48BFC5C9C2"
        "C7CCC1C6C99AA2AA5B727C1B282E425E61829075BBC1C5C5CACEB7BCC1848D95"
        "7C8687C0C6C8C1CBBB92AFA3BFC6C3BCC4BE8C95885B665E2F3B234858355364"
        "3D58694258684051633942512D28341C"
    ), bytes.fromhex(
        # The FUT tile cycles its artwork, which moves the whole left half.
        "AEB8C3D3D7E0E2E9F2E9EDF3EEF2F6D9DEE4D3DCE3C0C9D1777F868E969A9CA6"
        "AB92A0A7CCD3D9D2DADEBBC5CB737B8632424B1929304F66688C7E48BFC5C9C2"
        "C7CCC1C6C9969FA7586F7A1B282E425D61839075BBC1C5C4C9CDB7BCC0868E95"
        "7C8687C0C6C8C1CBBB92AFA3BAC1BEBEC9BBA6B89A7B8B722F3B234858355364"
        "3D58694258684050623745562D2A371E"
    )),
    "fut_error": (bytes.fromhex(
        "0E10130E10130E10130E10130E10130E10130E10130E10130E10130E10130E10"
        "130E10130E10130E10130E10130E10130E10130E10135D6063686B6F696C6F6E"
        "71740E10130E10130E10130E1013484B4C54575A585A5D595B5E0E10130E1013"
        "0E10130E10130E10130E10130E10130E10130E10130E10130E10130E10130E10"
        "130E10130E10130E10130E10130E1013"
    ), bytes.fromhex(
        # The same dialog while the screen behind it is dimmed.
        "0405060405060405060405060405060405060405060405060405060405060405"
        "060405060405060405060405060405060405060405061C1D1E1F20211F20211E"
        "1F20040506040506040506040506191A1A1E1F201F20211D1E1F040506040506"
        "0405060405060405060405060405060405060405060405060405060405060405"
        "06040506040506040506040506040506"
    )),
    # The dashboard profile chooser.  Pressing A here starts an Xbox Live
    # sign-in this setup cannot complete, so recognising it lets a run report
    # "the console needs signing in" instead of mashing buttons at it.  Only
    # the lit form is listed: while it fades it collapses to within 34 of the
    # dimmed FUT dialog, too close for this grid to call either way.
    "profile_chooser": (bytes.fromhex(
        "2415252515262211232211232111222818292F2030291B2A1D0F1E2413231F10"
        "202010211F0F201D0F1F2112221E111F270F263D2B35270F283C2E3F290E294F"
        "3D4E291029483648280A283620372C0B2D3C2943321032422941341234392138"
        "1F0821230925280A2B280B2A270A29220A24280A2A250B271D0E20241026220D"
        "26220D27210C251D0B211A0A1D1C0B1F"
    ),),
    "fut_loader": (bytes.fromhex(
        "AEBBC7D6DFE8E1E9F1E8ECF3EEF2F6E4EAF1D9E4EDC1CBD5818A94CED7DED6E0"
        "E8DAE3EBE6EDF3E4EDF3A3AFB9545D6B606E7A7C8A98A7B3BFC0CFD9CBD8E189"
        "97A55B69733C4C5D4E5E6D808E9BABB6BEB3BEC9AFBAC495A1AB6171812F3E4D"
        "45545267797098A79DA7B4ADA1AEA488978D5C6D66313F3C28331C42522D5061"
        "3A58694354663B4E603441522B263219"
    ),),
}

# The button each screen already prompts for.  "unknown" covers the attract
# videos, whose own prompt is "press START to skip".
ACTIONS = {
    "title": "START",
    "storage": "A",
    "notice": "A",
    "fut_error": "A",
    "profile_chooser": None,
    "main_menu": None,
    "fut_loader": None,
    "unknown": "START",
}

# An unrecognised frame is usually an attract video, which START skips, but it
# can also be a dialog this build shows only in some states -- those need A.
# Alternating covers both without having to enumerate every dialog in advance.
UNKNOWN_BUTTONS = ("START", "A")

# Screens the title reaches on its own; pressing at them only restarts a video.
PATIENT = {"main_menu", "fut_loader", "profile_chooser"}

# Skipping an attract video reveals the title screen for only a couple of
# seconds before the next one starts.  Polling at the settled cadence lands
# mid-video every time and loops forever, so unknown frames are re-checked
# quickly enough to catch that window.
SKIP_INTERVAL = 2.5


def signature(width: int, height: int, rgb: bytes) -> bytes:
    """Reduce a frame to an average-colour grid, independent of resolution."""
    cells = bytearray()
    for grid_y in range(GRID_Y):
        top, bottom = grid_y * height // GRID_Y, (grid_y + 1) * height // GRID_Y
        for grid_x in range(GRID_X):
            left = grid_x * width // GRID_X
            right = (grid_x + 1) * width // GRID_X
            totals = [0, 0, 0]
            samples = 0
            for y in range(top, bottom, 4):
                row = y * width * 3
                for x in range(left, right, 4):
                    pixel = row + x * 3
                    totals[0] += rgb[pixel]
                    totals[1] += rgb[pixel + 1]
                    totals[2] += rgb[pixel + 2]
                    samples += 1
            cells.extend(value // max(samples, 1) for value in totals)
    return bytes(cells)


def distance(left: bytes, right: bytes) -> int:
    return max(abs(a - b) for a, b in zip(left, right))


def classify(current: bytes) -> tuple[str, int]:
    """Return the nearest known screen, or ``unknown`` when nothing is close.

    A screen can carry several references: the same dialog is rendered very
    differently when the view behind it is dimmed, and averaging that away
    would blur genuinely distinct screens together.
    """
    observed = contrast(current)
    best_name, best_distance = "unknown", None
    for name, references in SIGNATURES.items():
        for reference in references:
            measured = distance(current, reference)
            if best_distance is None or measured < best_distance:
                best_distance = measured
                comparable = (
                    abs(observed - contrast(reference)) <= CONTRAST_TOLERANCE
                )
                best_name = name if comparable else "unknown"
    if best_distance is None or best_distance > MATCH_THRESHOLD:
        return "unknown", best_distance if best_distance is not None else -1
    return best_name, best_distance


def observe(host: str) -> tuple[str, int]:
    header, raw = capture(host)
    if header["pitch"] // header["width"] != 4:
        raise RuntimeError("Only 32-bit XBDM framebuffers are supported")
    width, height, rgb = decode_bgrx_tiled(
        raw,
        pitch=header["pitch"],
        width=header["width"],
        height=header["height"],
        offset_x=header["offset_x"],
        offset_y=header["offset_y"],
        crop_right=round(header["width"] * 0.02),
    )
    return classify(signature(width, height, rgb))


def press(host: str, button: str, frames: int) -> None:
    client = virtual_input.Xbdm(host)
    try:
        virtual_input.pulse(client, button, frames)
    finally:
        client.close()


def navigate(
    host: str,
    target: str,
    timeout: float,
    interval: float,
    frames: int,
) -> int:
    deadline = time.monotonic() + timeout
    last_seen = None
    attempts = 0
    confirmed = None
    while time.monotonic() < deadline:
        screen, measured = observe(host)
        # Act only on a screen seen twice running.  A frame caught mid-fade
        # sits between two screens and can match the wrong one: a dimmed
        # profile chooser reads as the dimmed FUT dialog, and pressing that
        # screen's button starts a sign-in this setup cannot finish.  A settled
        # screen confirms on the next poll; a transition does not.
        if screen != confirmed:
            confirmed = screen
            time.sleep(SKIP_INTERVAL if screen == "unknown" else 1.0)
            continue
        if screen != last_seen:
            print(f"screen = {screen} (distance {measured})", flush=True)
            last_seen = screen
        if screen == target:
            print(f"Reached {target}.", flush=True)
            return 0
        if screen == "unknown":
            press(host, UNKNOWN_BUTTONS[attempts % len(UNKNOWN_BUTTONS)], frames)
            attempts += 1
            time.sleep(SKIP_INTERVAL)
            continue
        attempts = 0
        button = ACTIONS.get(screen)
        if button is not None:
            press(host, button, frames)
        time.sleep(interval)
    raise TimeoutError(f"{target} was not reached before timeout")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("identify", "goto"))
    parser.add_argument(
        "target", nargs="?", choices=sorted(SIGNATURES), default="main_menu"
    )
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--interval", type=float, default=6.0)
    parser.add_argument("--frames", type=int, default=10)
    args = parser.parse_args()

    if args.action == "identify":
        screen, measured = observe(args.host)
        print(f"screen = {screen} (distance {measured})")
        return 0
    return navigate(
        args.host, args.target, args.timeout, args.interval, args.frames
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
