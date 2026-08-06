#!/usr/bin/env python3
"""Wait for CardsDLL and arm the native offline Authentication patches.

This helper removes the remaining manual race from a clean XeXMenu launch. It
waits until the supported ``powdllzf.xex.dll`` image is present, then applies
both reversible patches in the required order:

1. local EASW session/token values for the native JSON builder;
2. the deterministic Authentication entry hook that redirects ``pow/auth``.

No Authentication call, return value, frontend event, or navigation action is
forced by this helper.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import sys
import time

from fifa14_plain_send_hook import Xbdm
import fifa14_cards_auth_credentials_patch as credentials
import fifa14_cards_auth_endpoint_patch as endpoint


MODULE_NAME = "powdllzf.xex.dll"
MODULE_BASE = 0x89700000
TOOLS = Path(__file__).resolve().parent


def module_state(lines: list[str]) -> str:
    module = next(
        (
            line
            for line in lines
            if re.search(r'name="powdllzf\.xex\.dll"', line, re.IGNORECASE)
        ),
        None,
    )
    if module is None:
        return "absent"
    base = re.search(r"\bbase=0x([0-9A-Fa-f]+)", module)
    if base is None or int(base.group(1), 16) != MODULE_BASE:
        raise RuntimeError(f"Unexpected {MODULE_NAME}: {module}")
    return "supported"


def patch_site_state(session: bytes, token: bytes, entry: bytes) -> str:
    """Classify whether CardsDLL code pages are ready for reversible patching."""
    session_ok = session in (credentials.SESSION_ORIGINAL, credentials.SESSION_PATCHED)
    token_ok = token in (credentials.TOKEN_ORIGINAL, credentials.TOKEN_PATCHED)
    entry_ok = entry in (
        endpoint.ORIGINAL,
        endpoint.site_patch(),
        endpoint.insn(endpoint.branch(endpoint.SITE, endpoint.legacy_trace.STUB, False)),
    )
    if session_ok and token_ok and entry_ok:
        return "ready"
    return (
        f"session={session.hex().upper()} "
        f"token={token.hex().upper()} entry={entry.hex().upper()}"
    )


def wait_for_patch_sites(
    host: str, timeout: float, interval: float, stable_reads: int = 3
) -> None:
    """Wait for relocated CardsDLL instructions to settle after modload.

    XBDM can report the module before its code pages expose the final TU3
    instructions.  Requiring several consecutive recognized snapshots avoids
    treating that short relocation window as an incompatible DLL.
    """
    deadline = time.monotonic() + timeout
    consecutive = 0
    last_state = "not read"
    while time.monotonic() < deadline:
        client: Xbdm | None = None
        try:
            client = Xbdm(host)
            session = client.read(credentials.SESSION_SITE, 8)
            token = client.read(credentials.TOKEN_SITE, 8)
            entry = client.read(endpoint.SITE, 4)
            current = patch_site_state(session, token, entry)
            last_state = current
            if current == "ready":
                consecutive += 1
                if consecutive >= stable_reads:
                    print(
                        "Verified: CardsDLL Authentication patch sites are stable.",
                        flush=True,
                    )
                    return
            else:
                consecutive = 0
        except (ConnectionError, EOFError, OSError) as error:
            consecutive = 0
            last_state = str(error)
        finally:
            if client is not None:
                client.close()
        time.sleep(interval)
    raise TimeoutError(
        "CardsDLL Authentication patch sites did not stabilize: " + last_state
    )


def wait_for_module(host: str, timeout: float, interval: float) -> None:
    deadline = time.monotonic() + timeout
    attempt = 0
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        attempt += 1
        client: Xbdm | None = None
        try:
            client = Xbdm(host)
            state = module_state(client.multiline("modules"))
            if state == "supported":
                print(
                    f"Detected supported {MODULE_NAME} at 0x{MODULE_BASE:08X}.",
                    flush=True,
                )
                return
            last_error = None
        except (ConnectionError, EOFError, OSError) as error:
            last_error = error
        finally:
            if client is not None:
                client.close()
        if attempt == 1 or attempt % 10 == 0:
            suffix = f" ({last_error})" if last_error is not None else ""
            print(f"Waiting for CardsDLL module{suffix}...", flush=True)
        time.sleep(interval)
    suffix = f": {last_error}" if last_error is not None else ""
    raise TimeoutError(f"{MODULE_NAME} was not observed before timeout{suffix}")


def run_patch(script: str, arguments: list[str]) -> None:
    command = [sys.executable, str(TOOLS / script), *arguments]
    subprocess.run(command, cwd=TOOLS.parent, check=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host", help="Xbox IP address")
    parser.add_argument("--local-ip", default="192.168.1.36")
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--interval", type=float, default=1.0)
    args = parser.parse_args()

    wait_for_module(args.host, args.timeout, args.interval)
    wait_for_patch_sites(args.host, min(args.timeout, 30.0), args.interval)
    run_patch(
        "fifa14_cards_auth_credentials_patch.py",
        [args.host, "apply"],
    )
    run_patch(
        "fifa14_cards_auth_endpoint_patch.py",
        [
            args.host,
            "apply",
            "--local-ip",
            args.local_ip,
            "--port",
            str(args.port),
        ],
    )
    print(
        "READY: native Cards Authentication credentials and endpoint hook are armed.",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        raise SystemExit(130)
    except subprocess.CalledProcessError as error:
        print(
            f"Error: patch command exited with status {error.returncode}",
            file=sys.stderr,
        )
        raise SystemExit(error.returncode or 1)
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1)
