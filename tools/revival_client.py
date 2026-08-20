#!/usr/bin/env python3
"""Patch a console against a server that is somewhere else.

`tools/fut.sh` does two unrelated jobs in one script: it runs the Blaze server
*and* it writes bytes into the console over XBDM. That is right for this
machine, where both happen to live, and it is wrong for everybody else --
because it makes the server's machine and the console's machine the same
machine, and that machine is a Mac with a checkout and a virtualenv.

Split in two, only the second job has to be near the console. The server can be
a VPS (`deploy/DEPLOY.md`), shared by everyone. This file is the second job
alone: it launches the title, applies the three stages of patches, and keeps
the third applied -- against a server whose address it is simply told.

    python3 tools/revival_client.py --console 192.168.1.25 --server 203.0.113.10

That matters more than it sounds. Everything on this path is **pure standard
library** -- no capstone, no pip, no virtualenv; `capstone` in
`requirements.txt` belongs to the disassembly tools and nothing here imports
them. So this runs anywhere a Python 3.10 runs, and in particular it runs under
Termux on an Android phone on the same Wi-Fi as the console. Somebody with a
modded 360, a phone and no PC at all can play. That is not the end state --
`docs/PLUGIN.md` removes this program too -- but it is the state that exists.

Two deliberate differences from `fut.sh`:

* **No zsh, and no background jobs.** The patch watcher runs in the foreground
  of this process. Termux has no reliable `nohup`+`pkill` story, and a watcher
  that a phone silently killed would look exactly like a watcher with nothing
  to do -- the same failure `fut.sh` had to learn to log its way out of.
* **The account reset is a request, not a file.** `fut.sh` clears
  `runtime/local-account.json` and restarts the server; neither is available
  from across the network. `POST /revival/reset` is the same thing said
  remotely. A server too old to know that route answers 404, which this
  reports and carries on from -- it costs a stale FirstTimeFlag, not a run.
"""

from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
REPO = TOOLS.parent
sys.path.insert(0, str(TOOLS))

import revival_config  # noqa: E402

XBDM_PORT = 730

# Same knobs `fut.sh` documents, same reasons. The hinted window is tried
# first because it costs one small read; the full heap sweep is what once
# froze this console hard enough to need the power button, so it stays out of
# the polling loop and only runs after the title is long past the splash.
HINT_GRACE = 150
WATCH_INTERVAL = 5
WATCH_MISSES = 4
WATCH_GIVE_UP = 20


def step(message: str) -> None:
    print(f"\n== {message}", flush=True)


def detail(message: str) -> None:
    for line in str(message).splitlines():
        print(f"   {line}", flush=True)


def run(script: str, *arguments: str, timeout: int = 900) -> str:
    """Run one of the patchers with the interpreter that is running us.

    `sys.executable` rather than a hardcoded `python3`: under Termux, in a
    virtualenv, or on a machine whose `python3` is 3.8, the interpreter that
    got this far is by construction one that works.
    """
    finished = subprocess.run(
        [sys.executable, str(TOOLS / script), *arguments],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=REPO,
    )
    return (finished.stdout + finished.stderr).strip()


def running_title(console: str) -> str:
    """What the console is running, or "" if it cannot be asked.

    Spoken to directly rather than through `Xbdm`: this is the one question
    asked before anything is known to work, and it should fail as an empty
    answer rather than as a traceback about an import.
    """
    try:
        with socket.create_connection((console, XBDM_PORT), timeout=8) as link:
            link.recv(256)
            link.sendall(b"xbeinfo running\r\n")
            time.sleep(0.5)
            return link.recv(4096).decode("latin-1")
    except OSError:
        return ""


def reboot_to_dashboard(console: str) -> bool:
    """Bare `magicboot`. Never `magicboot cold`.

    `magicboot` from the dashboard starts FIFA; `magicboot` with FIFA already
    running reboots to the dashboard instead. So relaunching on top of a hung
    FUT frontend -- exactly when you reach for it -- costs a reboot and lands
    nowhere, and the reboot drops XBDM mid-command, which reads as a dead
    console when it is a healthy one twelve seconds from the dashboard.

    `magicboot cold` took this console off the network entirely on 12 August
    and it needed the power button.
    """
    try:
        with socket.create_connection((console, XBDM_PORT), timeout=8) as link:
            link.recv(256)
            link.sendall(b"magicboot\r\n")
    except OSError:
        pass
    for _ in range(40):
        time.sleep(5)
        if "dash.xex" in running_title(console):
            return True
    detail("le dashboard n'est pas revenu")
    return False


def reset_account(base: str) -> None:
    """Ask the server for a clean first-login state.

    The title rewrites this state from its in-memory session within seconds,
    so re-entering FUT without a relaunch cannot work -- which is why this is
    done here, immediately before the launch, and not at any other time.
    """
    step("session serveur")
    request = urllib.request.Request(f"{base}/revival/reset", method="POST", data=b"")
    try:
        with urllib.request.urlopen(request, timeout=10) as answer:
            detail(answer.read().decode("utf-8", "replace").strip() or "réinitialisée")
    except urllib.error.HTTPError as error:
        if error.code == 404:
            detail("serveur trop ancien pour /revival/reset -- on continue")
        else:
            detail(f"réinitialisation refusée ({error.code}) -- on continue")
    except OSError as error:
        # Not fatal, but worth saying loudly: if the server is unreachable
        # from here it is probably unreachable from the console too, and the
        # game will only ever say "connectez-vous aux serveurs EA".
        detail(f"serveur injoignable ({error}) -- la console ne l'atteindra pas non plus")


def launch(console: str, server: str, title: str,
           core_port: int, identity_port: int) -> bool:
    step("lancement du titre")
    if "fifa" in running_title(console).lower():
        detail("FIFA tourne déjà -- reboot, puis lancement immédiat")
        if not reboot_to_dashboard(console):
            return False
    output = run(
        "fifa14_early_local_server.py", console,
        "--local-ip", server,
        "--identity-port", str(identity_port),
        "--timeout", "900",
        "--launch-title", title,
        "--redirector-transport", "plaintext",
        "--redirect-fut-resource",
    )
    detail("\n".join(output.splitlines()[-2:]))
    # Without these the console never reaches the server at all, and the game
    # says only "connectez-vous à Xbox Live et aux serveurs EA" -- which names
    # neither the patch nor the step that failed.
    if "hostnames preserved" not in output:
        return False

    # The EAS FC session is a second Blaze connection, from powdllzf, to
    # endpoints the launch patch does not touch. powdllzf is not mapped yet at
    # this point, so the patcher polls for it.
    step("endpoints EAS FC")
    detail(run(
        "fifa14_easfc_endpoint_patch.py", console,
        "--local-ip", server,
        "--core-port", str(core_port),
        "--identity-port", str(identity_port),
        "--timeout", "90",
    ))
    return True


def patch_once(console: str, *, hinted: bool) -> str:
    if hinted:
        return run(
            "fifa14_tu3_helperfunctions_runtime_patch.py", console,
            "--hint-only", "--timeout", "20", "--interval", "3",
            "--chunk-size", "0x100000", timeout=120,
        ).splitlines()[-1]
    return run(
        "fifa14_tu3_helperfunctions_runtime_patch.py", console,
        "--timeout", "540", "--chunk-size", "0x800000", timeout=900,
    ).splitlines()[-1]


def await_patch(console: str) -> bool:
    """Wait for the APT itself, rather than for somebody to say "menu".

    The patch has to be in place before Ultimate Team is entered; applied from
    inside the FUT loader it does nothing. The main menu was only ever a proxy
    for that, and a person was the only thing watching for it.
    """
    step("patch helperFunctions (automatique)")
    deadline = time.monotonic() + HINT_GRACE
    while time.monotonic() < deadline:
        line = patch_once(console, hinted=True)
        if line.startswith("Verified:"):
            detail(line)
            return True
        time.sleep(2)
    detail("la fenêtre hintée n'a rien donné, balayage complet")
    line = patch_once(console, hinted=False)
    detail(line)
    return line.startswith("Verified:")


def watch(console: str) -> None:
    """Keep the patch applied for as long as the APT is in memory.

    The title loads helperFunctions more than once: a patch that verifies
    seconds after launch reads back `original` a minute later, because the copy
    that was patched has been replaced by the one the frontend loads next, and
    only the last one counts. So the patch is watched rather than applied.

    Every pass is printed, hit or miss. Logging only the hits made a watcher
    that had been failing for ten minutes look exactly like a watcher with
    nothing to do.
    """
    print("\n" + "=" * 42)
    print(" PRÊT. Entre dans Ultimate Team quand tu veux.")
    print(" Laisse cette fenêtre ouverte : elle garde le patch en place.")
    print("=" * 42, flush=True)
    misses = 0
    dry = 0
    while dry < WATCH_GIVE_UP:
        hinted = misses < WATCH_MISSES
        line = patch_once(console, hinted=hinted)
        if not hinted:
            misses = 0
        print(f"{time.strftime('%H:%M:%S')} {line}", flush=True)
        if line.startswith("Verified:"):
            misses = 0
            dry = 0
        else:
            misses += 1
            dry += 1
        time.sleep(WATCH_INTERVAL)
    # Once Ultimate Team is entered the APT is gone from memory for good, and
    # the watcher would sweep the heap for nothing.
    print("APT introuvable, surveillance arrêtée (normal une fois dans FUT).")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Lancer et patcher FIFA 14 contre un serveur distant.",
    )
    parser.add_argument("--console", default=None,
                        help="IP de la Xbox (défaut : console.address)")
    parser.add_argument("--server", default=None,
                        help="IP ou nom du serveur (défaut : server.host)")
    parser.add_argument("--core-port", type=int, default=None)
    parser.add_argument("--identity-port", type=int, default=None)
    parser.add_argument("--title", default=None)
    parser.add_argument("--no-reset", action="store_true",
                        help="ne pas demander une session neuve au serveur")
    parser.add_argument("--patch-only", action="store_true",
                        help="titre déjà lancé : appliquer et surveiller l'étage 3")
    args = parser.parse_args(argv)

    console = args.console or revival_config.value("console.address")
    server = args.server or revival_config.server_host()
    core_port = args.core_port or revival_config.port("server.core_port")
    identity_port = args.identity_port or revival_config.port("server.identity_port")
    title = args.title or revival_config.value("console.title")
    if not console or not server:
        print("configuration illisible -- voir fifa14revival.example.ini",
              file=sys.stderr)
        return 2

    # The server address is resolved to an IP here, not left as a name. The
    # patchers compile it into the title's memory, and the EAS FC strings are
    # rewritten in place with no room for a hostname (docs/RELEASE.md).
    try:
        server_ip = socket.gethostbyname(server)
    except OSError:
        print(f"impossible de résoudre {server}", file=sys.stderr)
        return 2

    print(f"console {console}   serveur {server_ip}:{core_port}/{identity_port}")
    if args.patch_only:
        if not await_patch(console):
            print("\n!! le patch n'a pas pris -- n'entre pas dans FUT", file=sys.stderr)
            return 1
        watch(console)
        return 0

    if not args.no_reset:
        reset_account(f"http://{server_ip}:{identity_port}")
    if not launch(console, server_ip, title, core_port, identity_port):
        print("\n!! les correctifs de lancement n'ont pas pris -- "
              "la console n'atteindra pas le serveur", file=sys.stderr)
        return 1
    run("xbox360_virtual_input.py", console, "restore", timeout=30)
    if not await_patch(console):
        print("\n!! le patch n'a pas pris -- n'entre pas dans FUT, relance",
              file=sys.stderr)
        return 1
    watch(console)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
