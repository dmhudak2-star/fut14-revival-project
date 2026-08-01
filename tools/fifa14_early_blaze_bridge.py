#!/usr/bin/env python3
"""Install the local plaintext Blaze bridge before FIFA 14 starts."""

from __future__ import annotations

import argparse
import select
import subprocess
import sys
import time
from pathlib import Path

from fifa14_early_redirector_patch import Connection
from fifa14_plain_send_hook import Xbdm, verify_module


REPOSITORY = Path(__file__).resolve().parents[1]
TOOLS = REPOSITORY / "tools"
BRIDGE = REPOSITORY / "server" / "fifa14_xbdm_blaze_bridge.py"
FIFA_PDATA = 'pdata=0x82329200'
FIFA_PSIZE = 'psize=0x0009e1e0'


def run_step(label: str, *arguments: str) -> None:
    result = subprocess.run(
        [sys.executable, *arguments],
        cwd=REPOSITORY,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    output = result.stdout.rstrip()
    print(f"[{label}]", flush=True)
    if output:
        print(output, flush=True)
    if result.returncode:
        raise RuntimeError(f"{label} failed with exit code {result.returncode}")


def install_chain(xbox: str) -> None:
    steps = (
        (
            "Redirector insecure profile",
            str(TOOLS / "fifa14_redirector_profile_patch.py"),
            xbox,
            "apply-insecure",
        ),
        (
            "Local virtual connect",
            str(TOOLS / "fifa14_connect_bypass.py"),
            xbox,
            "apply",
        ),
        (
            "Socket writable state",
            str(TOOLS / "fifa14_connect_ready_patch.py"),
            xbox,
            "apply",
        ),
        (
            "Socket connected state",
            str(TOOLS / "fifa14_connect_status_patch.py"),
            xbox,
            "apply",
        ),
        (
            "Plaintext send bridge",
            str(TOOLS / "fifa14_plain_send_hook.py"),
            xbox,
            "apply",
        ),
        (
            "Plaintext receive bridge",
            str(TOOLS / "fifa14_plain_recv_hook.py"),
            xbox,
            "apply",
        ),
        (
            "Queued receive readiness",
            str(TOOLS / "fifa14_pending_recv_ready_patch.py"),
            xbox,
            "apply",
        ),
        (
            "Registered-response ProtoSSL pump",
            str(TOOLS / "fifa14_pending_response_pump_patch.py"),
            xbox,
            "apply",
            "--registered-only",
        ),
    )
    for label, *arguments in steps:
        run_step(label, *arguments)


def start_bridge(
    xbox: str, local_ip: str, seconds: int
) -> subprocess.Popen[str]:
    process = subprocess.Popen(
        [
            sys.executable,
            str(BRIDGE),
            xbox,
            "--advertise",
            local_ip,
            "--seconds",
            str(seconds),
            "--from-start",
        ],
        cwd=REPOSITORY,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
    )
    if process.stdout is None:
        process.terminate()
        raise RuntimeError("bridge stdout is unavailable")
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        line = process.stdout.readline()
        if not line:
            if process.poll() is not None:
                raise RuntimeError("bridge exited before becoming ready")
            continue
        print(line.rstrip(), flush=True)
        if line.rstrip() == "XBDM_BLAZE_BRIDGE_READY":
            return process
    process.terminate()
    raise TimeoutError("bridge did not become ready")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host", help="Xbox IP address")
    parser.add_argument("--local-ip", required=True)
    parser.add_argument("--load-timeout", type=float, default=300)
    parser.add_argument("--monitor-seconds", type=int, default=900)
    parser.add_argument(
        "--attach",
        action="store_true",
        help="install into the supported FIFA build that is already loaded",
    )
    args = parser.parse_args()

    notify = Connection(args.host)
    control: Connection | None = None
    bridge: subprocess.Popen[str] | None = None
    stopped = False
    try:
        notify.command(
            'debugger connect override name="FIFABlazeBridge" user="CodexMac"'
        )
        notify.command("notify reconnectport=1", expected=205)
        control = Connection(args.host)
        if args.attach:
            verifier = Xbdm(args.host)
            try:
                verify_module(verifier)
            finally:
                verifier.close()
            control.command("stop")
            stopped = True
            print("FIFA 14 suspended for bridge installation.", flush=True)
            install_chain(args.host)
            bridge = start_bridge(
                args.host, args.local_ip, args.monitor_seconds
            )
            control.command("go")
            stopped = False
            print(
                "BLAZE_BRIDGE_ACTIVE - continue through the FIFA title screen.",
                flush=True,
            )
            assert bridge.stdout is not None
            for line in bridge.stdout:
                print(line.rstrip(), flush=True)
            return bridge.wait()
        print(
            "BLAZE_BRIDGE_ARMED - return to XeXMenu and launch FIFA 14.",
            flush=True,
        )
        deadline = time.monotonic() + args.load_timeout
        while time.monotonic() < deadline:
            readable, _, _ = select.select([notify.sock], [], [], 1)
            if not readable:
                continue
            event = notify.line()
            lowered = event.lower()
            if (
                "modload" not in lowered
                or 'name="default.xex"' not in lowered
                or FIFA_PDATA not in lowered
                or FIFA_PSIZE not in lowered
            ):
                continue
            print(f"Module event: {event}", flush=True)
            control.command("stop")
            stopped = True
            install_chain(args.host)
            bridge = start_bridge(
                args.host, args.local_ip, args.monitor_seconds
            )
            control.command("go")
            stopped = False
            print(
                "BLAZE_BRIDGE_ACTIVE - continue through the FIFA title screen.",
                flush=True,
            )
            assert bridge.stdout is not None
            for line in bridge.stdout:
                print(line.rstrip(), flush=True)
            return bridge.wait()
        raise TimeoutError("FIFA 14 modload event was not observed")
    finally:
        if control is not None:
            if stopped:
                try:
                    control.command("go")
                    print("Execution resumed during cleanup.", flush=True)
                except Exception:
                    pass
            control.close()
        notify.close()
        if bridge is not None and bridge.poll() is None:
            bridge.terminate()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted; cleanup attempted.", flush=True)
        raise SystemExit(130)
    except Exception as error:
        print(f"Error: {error}", flush=True)
        raise SystemExit(1)
