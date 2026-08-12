#!/usr/bin/env python3
"""Drive FIFA 14's FUT frontend with nobody at the pad.

There is no picture to work from. XBDM screenshot capture is not dependable on
this console and the framebuffer navigator that depended on it was removed from
the launch path for freezing the title. What replaces it is already in the
repository and was being used as a log:

    **the server journal is a screen oracle.**

Every FUT screen has a request signature that belongs to it and to nothing
else. The hub asks `clientdata/totw` and then `clubUser`; the store asks
`storepackdescriptions`; a cup asks `tournament/teams` and then
`tournament/user/<id>`. Watching what arrives says where the title is, with no
picture involved -- and it says it in the client's own words, which is exactly
what any question about an unimplemented screen needs answering in.

Pair that with `xbox360_virtual_input.py` and the loop closes: press, wait for
the signature, decide what to press next.

    from fut_drive import Console
    with Console() as console:
        console.enter_fut()
        console.press("A")
        seen = console.wait_for("/tournament/teams", timeout=30)

The other half is recovery. Two freezes in one evening each cost a round trip
to a human. `Console.alive()` separates the two states that look identical from
here -- a hung frontend, where XBDM still answers, from a dead title, where it
does not -- and `recover()` reboots, waits for the dashboard and runs
`tools/fut.sh`, so a scenario can carry on rather than stopping for the night.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fifa14_plain_send_hook import Xbdm  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
XBOX = os.environ.get("XBOX", "192.168.1.25")
PYTHON = REPO / ".venv" / "bin" / "python"
RUNTIME = REPO / "runtime"

# What a screen asks for. Only paths that belong to one screen are listed: the
# point is to identify, not to describe, and a path several screens ask for
# identifies nothing.
SCREENS = {
    "dashboard": (),
    "fut-hub": ("/ut/game/fifa14/clientdata/totw", "/ut/game/fifa14/clubUser"),
    "store": ("/fut/packs/loc/storepackdescriptions",),
    "cups": ("/ut/game/fifa14/tournament/list",),
    "cup-entry": ("/ut/game/fifa14/tournament/teams",),
    "club": ("/ut/game/fifa14/club",),
    "market": ("/ut/game/fifa14/transfermarket",),
    "squad": ("/ut/game/fifa14/squad/active",),
}


class JournalTail:
    """The newest live journal, read forward from wherever we started.

    Reopened whenever a newer file appears, because relaunching the server
    starts a new one and a scenario that survives a relaunch has to follow it.
    """

    def __init__(self, directory: Path = RUNTIME) -> None:
        self.directory = directory
        self.path: Path | None = None
        self.offset = 0
        self.reopen(seek_to_end=True)

    def newest(self) -> Path | None:
        files = sorted(self.directory.glob("live-easw-*.jsonl"))
        return files[-1] if files else None

    def reopen(self, seek_to_end: bool = False) -> None:
        newest = self.newest()
        if newest is None or newest == self.path:
            return
        self.path = newest
        self.offset = newest.stat().st_size if seek_to_end else 0

    def read(self) -> list[dict]:
        """Everything written since the last call."""
        self.reopen()
        if self.path is None:
            return []
        events: list[dict] = []
        with self.path.open("rb") as handle:
            handle.seek(self.offset)
            for line in handle:
                if not line.endswith(b"\n"):
                    break            # a half-written line; leave it for later
                self.offset += len(line)
                try:
                    events.append(json.loads(line))
                except ValueError:
                    continue
        return events


class Console:
    """The console, its pad, and the journal that says what it is showing."""

    def __init__(self, host: str = XBOX, verbose: bool = True) -> None:
        self.host = host
        self.verbose = verbose
        self.journal = JournalTail()
        self.seen: list[dict] = []

    def __enter__(self) -> "Console":
        return self

    def __exit__(self, *_exc) -> None:
        self.release_pad()

    def say(self, message: str) -> None:
        if self.verbose:
            print(f"{time.strftime('%H:%M:%S')} {message}", flush=True)

    # -- the console itself --------------------------------------------------

    def running_title(self) -> str | None:
        """What the console is running, or None if XBDM does not answer.

        The distinction this draws is the one that matters after a freeze: a
        hung frontend still answers here, a dead title does not.
        """
        try:
            client = Xbdm(self.host)
        except Exception:
            return None
        try:
            # This wrapper hands back str; the patcher's own Xbdm hands back
            # bytes. Taking one for granted made every probe here look like a
            # dead console -- the AttributeError was swallowed as "no answer".
            line = client.multiline("xbeinfo running")[-1]
            return line.decode("ascii", "replace") if isinstance(line, bytes) else line
        except Exception:
            return None
        finally:
            try:
                client.close()
            except Exception:
                pass

    def alive(self) -> bool:
        return self.running_title() is not None

    def in_game(self) -> bool:
        title = self.running_title() or ""
        return "FIFA" in title or "fifa" in title

    def reboot(self, timeout: int = 240) -> bool:
        """Back to the dashboard.

        Bare `magicboot`. Two other forms were tried on 12 August and neither
        is usable:

        `magicboot title="...FIFA 14\\default.xex"` rebooted and came straight
        back up in FIFA, at the intro, with none of the launch patches applied
        -- a title that can never reach this server, and one `fut.sh` then
        refuses to relaunch over.

        `magicboot cold` **took the console off the network entirely**. Twenty
        minutes later port 730 was still refused and the ARP entry was
        incomplete; it needed the power button. Never send it from here: there
        is nothing on this side that can undo it.

        It drops the XBDM connection mid-command, so the exception on the way
        out is the expected outcome and not a failure.
        """
        self.say("reboot vers le dashboard")
        try:
            client = Xbdm(self.host)
            client.sock.sendall(b"magicboot\r\n")
            try:
                client.line()
            except Exception:
                pass
            client.close()
        except Exception:
            pass
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(8)
            title = self.running_title()
            if title and "dash.xex" in title:
                self.say("dashboard atteint")
                return True
        self.say("le dashboard n'est pas revenu")
        return False

    def relaunch(self, timeout: int = 240) -> bool:
        """`tools/fut.sh`, start to finish."""
        self.say("relance (tools/fut.sh)")
        log = RUNTIME / "last-launch.log"
        with log.open("wb") as handle:
            process = subprocess.Popen(
                ["zsh", str(REPO / "tools" / "fut.sh")],
                cwd=str(REPO), stdout=handle, stderr=subprocess.STDOUT,
            )
        deadline = time.time() + timeout
        while time.time() < deadline:
            if process.poll() is not None:
                break
            time.sleep(5)
        else:
            process.kill()
            self.say("la relance n'a pas terminé à temps")
            return False
        text = log.read_text(errors="replace")
        if "PRÊT" not in text:
            self.say("la relance a échoué -- voir runtime/last-launch.log")
            return False
        self.journal.reopen(seek_to_end=True)
        self.say("relancé et patché")
        return True

    def recover(self) -> bool:
        """Whatever state the console is in, get back to a patched title."""
        if not self.alive():
            self.say("XBDM muet -- le titre est mort, pas seulement gelé")
            # Nothing here can power-cycle the console. Wait a little in case
            # it is mid-reboot already; a real power cut needs a human.
            for _ in range(12):
                time.sleep(10)
                if self.alive():
                    break
            else:
                return False
        if self.in_game() and not self.reboot():
            return False
        if not self.in_game() and not self.relaunch():
            return False
        self.pad_ready = False
        return True

    # -- the pad -------------------------------------------------------------

    # The pad has to stay *connected* between presses, which the `press` action
    # of xbox360_virtual_input.py does not do: it arms the mailbox for a few
    # frames and then clears it, and a cleared mailbox falls through to the
    # real controller. With no controller switched on -- which is the state of
    # this console at four in the morning -- the title sees a pad appear for a
    # third of a second holding a button and vanish again, and ignores it.
    # Holding zero buttons between pulses is what made the first press land.

    def _input(self, *arguments: str) -> str:
        result = subprocess.run(
            [str(PYTHON), str(REPO / "tools" / "xbox360_virtual_input.py"),
             self.host, *arguments],
            capture_output=True, text=True,
        )
        return (result.stdout + result.stderr).strip()

    pad_ready = False
    IDLE_FRAMES = 900          # fifteen seconds of "connected, nothing held"

    def take_pad(self) -> bool:
        """Install the input hook. `fut.sh` restores it at every launch."""
        out = self._input("apply")
        self.pad_ready = "Verified" in out or "patched" in out
        if self.pad_ready:
            self._hold(0, self.IDLE_FRAMES)
        self.say(f"manette: {out.splitlines()[-1] if out else 'sans réponse'}")
        return self.pad_ready

    def release_pad(self) -> None:
        if self.pad_ready:
            self._input("restore")
            self.pad_ready = False

    def _hold(self, mask: int, frames: int) -> None:
        import struct
        from xbox360_virtual_input import MAILBOX
        client = Xbdm(self.host)
        try:
            client.write(
                MAILBOX,
                struct.pack(">IIHBBhhhhI8x", 1, 0, mask, 0, 0, 0, 0, 0, 0, frames),
            )
        finally:
            try:
                client.close()
            except Exception:
                pass

    def press(self, button: str, frames: int = 10, settle: float = 0.8) -> None:
        from xbox360_virtual_input import BUTTONS
        if not self.pad_ready:
            self.take_pad()
        self._hold(BUTTONS[button], frames)
        time.sleep(max(0.3, frames / 60.0))
        self._hold(0, self.IDLE_FRAMES)
        time.sleep(settle)

    def tap(self, *buttons: str, settle: float = 0.6) -> None:
        for button in buttons:
            self.press(button, settle=settle)

    # -- eyes ----------------------------------------------------------------
    #
    # `docs/AUTOMATIC_PATCH.md` records that XBDM screenshot capture "is not
    # dependable on this console", and the navigator that relied on it was
    # taken out of the launch path for that reason. It worked every single time
    # it was asked on 12 August 2026 -- 847x480, format 0x18280186 -- which is
    # what turned a blind driver into a sighted one. Whatever was wrong before
    # is not wrong now; the journal oracle stays as the fallback and as the
    # only thing that can answer "what did the client *ask for*".

    def shot(self, name: str = "screen") -> Path | None:
        """Capture the framebuffer. Returns the file, or None if it failed."""
        target = REPO / "work" / f"{name}.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [str(PYTHON), str(REPO / "tools" / "xbdm_screenshot.py"),
             self.host, str(target)],
            capture_output=True, text=True, timeout=120,
        )
        if not target.exists() or "saved" not in result.stdout:
            self.say(f"capture échouée: {(result.stdout + result.stderr).strip()[:120]}")
            return None
        return target

    def skip_intros(self, limit: int = 8) -> None:
        """START past the attract videos until the title stops taking it.

        Each video answers START by ending, and the next one starts; the main
        menu answers it with nothing. Pressing a fixed number of times either
        stops short of the menu or walks into it, so this presses until the
        picture stops changing.
        """
        previous = None
        for index in range(limit):
            self.press("START", frames=30, settle=4.0)
            shot = self.shot(f"intro-{index:02d}")
            current = shot.read_bytes() if shot else None
            if current is not None and current == previous:
                self.say(f"l'écran ne change plus après {index + 1} START")
                return
            previous = current
        self.say(f"toujours en intro après {limit} START")

    # -- the journal as an oracle -------------------------------------------

    def drain(self) -> list[dict]:
        events = self.journal.read()
        self.seen.extend(events)
        return events

    def paths_since(self, seconds: float = 10.0) -> list[str]:
        cutoff = time.time() - seconds
        out = []
        for event in self.seen:
            stamp = event.get("time") or ""
            if not stamp:
                continue
            out.append(event.get("path") or "")
        return [path for path in out if path]

    def wait_for(self, *fragments: str, timeout: float = 30.0,
                 quiet: float = 0.0) -> str | None:
        """Wait until a request path contains one of these fragments.

        Returns the path that matched, or None on timeout. `quiet` additionally
        requires that nothing has arrived for that long, which is how "the
        screen has finished loading" is spelled without a picture.
        """
        deadline = time.time() + timeout
        last_event = time.time()
        while time.time() < deadline:
            for event in self.drain():
                path = event.get("path") or ""
                if path:
                    last_event = time.time()
                for fragment in fragments:
                    if fragment in path:
                        self.say(f"vu: {path}")
                        return path
            if quiet and time.time() - last_event >= quiet:
                return None
            time.sleep(0.5)
        return None

    def settled(self, quiet: float = 4.0, timeout: float = 60.0) -> list[str]:
        """Everything requested until the traffic stops for `quiet` seconds."""
        deadline = time.time() + timeout
        collected: list[str] = []
        last = time.time()
        while time.time() < deadline:
            for event in self.drain():
                path = event.get("path") or ""
                if path:
                    collected.append(path)
                    last = time.time()
            if time.time() - last >= quiet:
                break
            time.sleep(0.5)
        return collected

    def where(self, paths: list[str] | None = None) -> str:
        """Name the screen those requests belong to, if any does."""
        paths = paths if paths is not None else self.paths_since()
        for screen, signature in SCREENS.items():
            if signature and all(
                any(fragment in path for path in paths) for fragment in signature
            ):
                return screen
        return "inconnu"

    def frozen(self, quiet: float = 45.0) -> bool:
        """No traffic for a long time while the title is still up."""
        self.drain()
        return self.in_game() and not self.settled(quiet=2.0, timeout=quiet)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("watch", "where", "press", "recover"))
    parser.add_argument("argument", nargs="?", default="")
    parser.add_argument("--seconds", type=float, default=30.0)
    args = parser.parse_args()

    console = Console()
    if args.action == "watch":
        paths = console.settled(quiet=args.seconds, timeout=args.seconds + 300)
        for path in paths:
            print(path)
        print(f"-- écran: {console.where(paths)}")
    elif args.action == "where":
        paths = console.settled(quiet=4.0, timeout=args.seconds)
        print(console.where(paths))
    elif args.action == "press":
        console.press(args.argument or "A")
        print(console.where(console.settled(quiet=3.0, timeout=20)))
    elif args.action == "recover":
        print("ok" if console.recover() else "échec")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
