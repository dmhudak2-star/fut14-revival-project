#!/usr/bin/env python3
"""Install the local Blaze revival chain while default.xex is stopped at modload."""

from __future__ import annotations

import argparse
import datetime as dt
import ipaddress
import select
import subprocess
import sys
import time
from pathlib import Path

from fifa14_early_gate_patch import Connection
from fifa14_revive_session import (
    DEFAULT_XBOX,
    FUT_GATE_ADDRESS,
    FUT_GATE_READY_STATES,
    HERE,
    SessionLog,
    apply_chain,
    build_responses,
    collect_diagnostics,
    detect_local_ip,
    start_tcp_sink,
    stop_tcp_sink,
    xbdm_ready,
)


def wait_for_default_xex(
    log: SessionLog,
    notify: Connection,
    control: Connection,
    timeout: float,
) -> None:
    log.line(
        "\nEARLY_REVIVAL_ARMED: launch FIFA 14 from XeXMenu/dashboard now."
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        readable, _, _ = select.select([notify.sock], [], [], 1)
        if not readable:
            continue
        event = notify.line()
        lowered = event.lower()
        if "modload" not in lowered or 'name="default.xex"' not in lowered:
            continue
        log.line(f"Module event: {event}")
        control.command("stop")
        try:
            gate = control.read(FUT_GATE_ADDRESS, 4)
        except Exception as error:
            gate = b""
            log.line(f"  candidate read failed: {error}")
        if gate not in FUT_GATE_READY_STATES:
            log.line(
                "  skipped: this default.xex is not FIFA 14 "
                f"(gate={gate.hex().upper() or 'unreadable'})."
            )
            control.command("go")
            continue
        log.line(
            f"  FIFA 14 confirmed at gate 0x{FUT_GATE_ADDRESS:08X}."
        )
        return
    raise TimeoutError("default.xex modload event was not observed")


def start_router(
    log: SessionLog,
    xbox: str,
    redirector: Path,
    session_dir: Path,
    seconds: int,
) -> subprocess.Popen[str]:
    command = [
        sys.executable,
        str(HERE / "fifa14_postauth_router_watch.py"),
        xbox,
        "--seconds",
        str(seconds),
        "--from-start",
        "--followups-in-xbox",
        "--redirector-delay",
        "0.05",
        "--qos-delay",
        "0.25",
        "--redirector-response",
        str(redirector),
        "--output",
        str(session_dir / "frames"),
    ]
    process = subprocess.Popen(
        command,
        cwd=HERE.parent,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
    )
    if process.stdout is None:
        raise RuntimeError("Router stdout is unavailable")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        # The TextIOWrapper may read both startup lines from the pipe at
        # once.  A second select() would then report the fd idle even though
        # POSTAUTH_ROUTER_READY is already in Python's buffer, so consume
        # lines directly after the child has emitted its first status line.
        line = process.stdout.readline()
        if not line:
            if process.poll() is not None:
                raise RuntimeError("Router exited before becoming ready")
            continue
        line = line.rstrip()
        log.line(line)
        if line == "POSTAUTH_ROUTER_READY":
            return process
    process.terminate()
    raise TimeoutError("Router did not become ready")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--xbox", default=DEFAULT_XBOX)
    parser.add_argument("--local-ip")
    parser.add_argument("--load-timeout", type=float, default=300)
    parser.add_argument("--monitor-seconds", type=int, default=900)
    args = parser.parse_args()

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    session_dir = HERE / f"fifa14_early_revive_session_{stamp}"
    session_dir.mkdir(parents=True)
    log = SessionLog(session_dir / "session.log")
    notify: Connection | None = None
    control: Connection | None = None
    router: subprocess.Popen[str] | None = None
    sink: subprocess.Popen[str] | None = None
    stopped = False
    try:
        local_ip = args.local_ip or detect_local_ip(args.xbox)
        local_ip = str(ipaddress.IPv4Address(local_ip))
        log.line(f"Session directory: {session_dir}")
        log.line(f"Xbox: {args.xbox}")
        log.line(f"Mac local IP: {local_ip}")
        if not xbdm_ready(args.xbox):
            raise RuntimeError(f"XBDM is not reachable at {args.xbox}:730")

        redirector, preauth = build_responses(log, local_ip, session_dir)
        notify = Connection(args.xbox)
        notify.command(
            'debugger connect override name="FIFAEarlyRevive" user="CodexMac"'
        )
        notify.command("notify reconnectport=1", expected=205)
        control = Connection(args.xbox)
        wait_for_default_xex(
            log, notify, control, args.load_timeout
        )
        stopped = True

        log.line("\n[Installing complete chain while title is stopped]")
        apply_chain(
            log,
            args.xbox,
            local_ip,
            redirector,
            preauth,
            diagnostics=False,
        )
        sink = start_tcp_sink(
            log,
            local_ip,
            session_dir,
            args.monitor_seconds,
        )
        router = start_router(
            log,
            args.xbox,
            redirector,
            session_dir,
            args.monitor_seconds,
        )
        if sink.poll() is not None:
            raise RuntimeError("Local TCP sink exited before title resume")
        control.command("go")
        stopped = False
        log.line(
            "\nEARLY_REVIVAL_READY: execution resumed with routing active. "
            "Continue to the FIFA menu; do not open FUT until instructed."
        )

        assert router.stdout is not None
        try:
            for line in router.stdout:
                log.line(line.rstrip())
            result = router.wait()
        except KeyboardInterrupt:
            log.line("\nMonitor interrupted; collecting diagnostics.")
            router.terminate()
            try:
                router.wait(timeout=3)
            except subprocess.TimeoutExpired:
                router.kill()
                router.wait()
            result = 130
        collect_diagnostics(log, args.xbox)
        log.line(f"\nSession complete: router exit code {result}")
        log.line(f"Full log: {log.path}")
        return 0 if result in (0, 130) else result
    except Exception as error:
        log.line(f"\nERROR: {error}")
        return 1
    finally:
        if router is not None and router.poll() is None:
            router.terminate()
        if control is not None:
            if stopped:
                try:
                    control.command("go")
                    log.line("Execution resumed during cleanup.")
                except Exception:
                    pass
            control.close()
        if sink is not None:
            stop_tcp_sink(log, sink)
        if notify is not None:
            notify.close()
        log.close()


if __name__ == "__main__":
    raise SystemExit(main())
