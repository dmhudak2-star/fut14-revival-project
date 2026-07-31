#!/usr/bin/env python3
"""Prepare and monitor one deterministic FIFA 14 FUT revival attempt."""

from __future__ import annotations

import argparse
import datetime as dt
import ipaddress
import select
import socket
import subprocess
import sys
import time
from pathlib import Path

from fifa14_plain_recv_hook import (
    PENDING_CURSOR,
    PENDING_LENGTH,
    PENDING_SOCKET,
)
from fifa14_plain_send_hook import Xbdm, verify_module


HERE = Path(__file__).resolve().parent
DEFAULT_XBOX = "192.0.2.25"
FUT_GATE_ADDRESS = 0x82835220
FUT_GATE_READY_STATES = {
    bytes.fromhex("2B160000"),  # original
    bytes.fromhex("48000020"),  # native-start patch
    bytes.fromhex("48000040"),  # legacy patch, migrated during apply
}


class SessionLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.file = path.open("w", encoding="utf-8")

    def line(self, value: str = "") -> None:
        print(value, flush=True)
        self.file.write(value + "\n")
        self.file.flush()

    def close(self) -> None:
        self.file.close()


def detect_local_ip(xbox: str) -> str:
    route = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        route.connect((xbox, 730))
        value = route.getsockname()[0]
    finally:
        route.close()
    address = ipaddress.IPv4Address(value)
    if not address.is_private:
        raise RuntimeError(f"Detected non-private Mac address: {address}")
    return str(address)


def xbdm_ready(xbox: str) -> bool:
    try:
        connection = socket.create_connection((xbox, 730), timeout=2)
    except OSError:
        return False
    connection.close()
    return True


def title_loaded(xbox: str) -> bool:
    try:
        client = Xbdm(xbox)
        try:
            verify_module(client)
            # XBDM can announce/default-list the module before its executable
            # pages are initialized.  Do not start patching until a known
            # instruction is actually present.
            return client.read(FUT_GATE_ADDRESS, 4) in FUT_GATE_READY_STATES
        finally:
            client.close()
    except Exception:
        return False


def run(
    log: SessionLog,
    label: str,
    arguments: list[str],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    log.line(f"\n[{label}]")
    result = subprocess.run(
        [sys.executable, *arguments],
        cwd=HERE.parent,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    output = result.stdout.rstrip()
    if output:
        for line in output.splitlines():
            log.line(line)
    if check and result.returncode:
        raise RuntimeError(f"{label} failed with exit code {result.returncode}")
    return result


def build_responses(
    log: SessionLog,
    local_ip: str,
    session_dir: Path,
) -> tuple[Path, Path]:
    redirector = session_dir / "redirector_standard_insecure.bin"
    preauth = session_dir / "preauth.bin"
    run(
        log,
        "Build Redirector response",
        [
            str(HERE / "fifa14_build_redirector_response.py"),
            "--host",
            local_ip,
            "--port",
            "10041",
            "--secure",
            "0",
            "--address-mode",
            "host",
            "--output",
            str(redirector),
        ],
    )
    run(
        log,
        "Build PreAuth response",
        [
            str(HERE / "fifa14_build_preauth_response.py"),
            "--host",
            local_ip,
            "--output",
            str(preauth),
        ],
    )
    return redirector, preauth


def apply_chain(
    log: SessionLog,
    xbox: str,
    local_ip: str,
    redirector: Path,
    preauth: Path,
    *,
    diagnostics: bool = False,
) -> None:
    functional_steps = (
        (
            "FUT entry gate",
            [str(HERE / "fifa14_fut_gate_patch.py"), xbox, "apply"],
        ),
        (
            "Redirector insecure profile",
            [
                str(HERE / "fifa14_redirector_profile_patch.py"),
                xbox,
                "apply-insecure",
            ],
        ),
        (
            "Blaze connect redirect",
            [
                str(HERE / "fifa14_connect_redirect.py"),
                xbox,
                "apply",
                "--local-ip",
                local_ip,
            ],
        ),
        (
            "Connect readiness",
            [str(HERE / "fifa14_connect_ready_patch.py"), xbox, "apply"],
        ),
        (
            "Connect status",
            [str(HERE / "fifa14_connect_status_patch.py"), xbox, "apply"],
        ),
        (
            "Plaintext send ring",
            [str(HERE / "fifa14_plain_send_hook.py"), xbox, "apply"],
        ),
        (
            "Plaintext receive queue",
            [str(HERE / "fifa14_plain_recv_hook.py"), xbox, "apply"],
        ),
        (
            "Queued-response socket readiness",
            [
                str(HERE / "fifa14_pending_recv_ready_patch.py"),
                xbox,
                "apply",
            ],
        ),
        (
            "PreAuth/Ping routing; Redirector/QoS deferred",
            [
                str(HERE / "fifa14_protossl_flow_patch.py"),
                xbox,
                "apply",
                "--response",
                str(preauth),
                "--redirector-response",
                str(redirector),
                "--defer-redirector",
                "--defer-qos",
            ],
        ),
        (
            "Registered-request PreAuth/Ping pump",
            [
                str(HERE / "fifa14_pending_response_pump_patch.py"),
                xbox,
                "apply",
                "--registered-only",
            ],
        ),
    )
    diagnostic_steps = (
        (
            "ProtoSSL receive trace",
            [str(HERE / "fifa14_protossl_recv_trace.py"), xbox, "apply"],
        ),
        (
            "Blaze frame-dispatch trace",
            [
                str(HERE / "fifa14_blaze_frame_dispatch_trace.py"),
                xbox,
                "apply",
            ],
        ),
        (
            "QoS send stack trace",
            [str(HERE / "fifa14_qos_send_stack_trace.py"), xbox, "apply"],
        ),
        (
            "QoS-complete signal trace",
            [
                str(HERE / "fifa14_qos_signal_trace_v2.py"),
                xbox,
                "apply",
            ],
        ),
        (
            "Connection-result trace",
            [
                str(HERE / "fifa14_connection_result_trace.py"),
                xbox,
                "apply",
            ],
        ),
    )
    steps = functional_steps + (diagnostic_steps if diagnostics else ())
    for label, arguments in steps:
        run(log, label, arguments)


def collect_diagnostics(log: SessionLog, xbox: str) -> None:
    commands = (
        (
            "Recent Blaze requests",
            [str(HERE / "fifa14_plain_send_recent.py"), xbox],
        ),
        (
            "Pending-response pump journal",
            [
                str(HERE / "fifa14_pending_response_pump_patch.py"),
                xbox,
                "read",
            ],
        ),
        (
            "Queued-response socket readiness journal",
            [
                str(HERE / "fifa14_pending_recv_ready_patch.py"),
                xbox,
                "status",
            ],
        ),
        (
            "ProtoSSL receive journal",
            [str(HERE / "fifa14_protossl_recv_trace.py"), xbox, "read"],
        ),
        (
            "Blaze frame-dispatch journal",
            [
                str(HERE / "fifa14_blaze_frame_dispatch_trace.py"),
                xbox,
                "read",
            ],
        ),
        (
            "Connection-result journal",
            [str(HERE / "fifa14_connection_result_trace.py"), xbox, "read"],
        ),
        (
            "QoS send stack journal",
            [str(HERE / "fifa14_qos_send_stack_trace.py"), xbox, "read"],
        ),
        (
            "QoS-complete signal journal",
            [
                str(HERE / "fifa14_qos_signal_trace_v2.py"),
                xbox,
                "read",
            ],
        ),
        (
            "Flow queue status",
            [str(HERE / "fifa14_protossl_flow_patch.py"), xbox, "status"],
        ),
    )
    for label, arguments in commands:
        run(log, label, arguments, check=False)


def start_tcp_sink(
    log: SessionLog,
    local_ip: str,
    session_dir: Path,
    seconds: int,
) -> subprocess.Popen[str]:
    command = [
        sys.executable,
        str(HERE / "fifa14_tcp_sink.py"),
        "--listen",
        local_ip,
        "--seconds",
        str(seconds + 60),
        "--output",
        str(session_dir / "tcp_sink"),
    ]
    log.line("\n[Persistent local TCP sink]")
    process = subprocess.Popen(
        command,
        cwd=HERE.parent,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
    )
    if process.stdout is None:
        process.terminate()
        raise RuntimeError("TCP sink stdout pipe is unavailable")
    readable, _, _ = select.select([process.stdout], [], [], 10)
    if not readable:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        raise TimeoutError("Local TCP sink did not report readiness")
    first_line = process.stdout.readline().rstrip()
    if first_line:
        log.line(first_line)
    if not first_line.startswith("TCP_SINK_READY "):
        try:
            remainder, _ = process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            process.terminate()
            remainder, _ = process.communicate(timeout=3)
        if remainder:
            for line in remainder.rstrip().splitlines():
                log.line(line)
        raise RuntimeError("Local TCP sink failed to become ready")
    return process


def clear_stale_pending_response(log: SessionLog, xbox: str) -> None:
    client = Xbdm(xbox)
    try:
        pending = int.from_bytes(client.read(PENDING_LENGTH, 4), "big")
        if pending:
            log.line(
                f"Clearing stale queued response before live monitoring: "
                f"{pending} bytes"
            )
        client.write(PENDING_LENGTH, bytes(4))
        client.write(PENDING_SOCKET, bytes(4))
        client.write(PENDING_CURSOR, bytes(4))
        if int.from_bytes(client.read(PENDING_LENGTH, 4), "big") != 0:
            raise RuntimeError("Failed to clear the stale receive queue")
    finally:
        client.close()


def stop_tcp_sink(
    log: SessionLog,
    process: subprocess.Popen[str],
) -> None:
    if process.poll() is None:
        process.terminate()
    try:
        remainder, _ = process.communicate(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        remainder, _ = process.communicate()
    if remainder:
        log.line("\n[TCP sink events]")
        for line in remainder.rstrip().splitlines():
            log.line(line)


def monitor(
    log: SessionLog,
    xbox: str,
    redirector: Path,
    session_dir: Path,
    seconds: int,
    *,
    from_start: bool,
) -> int:
    command = [
        sys.executable,
        str(HERE / "fifa14_postauth_router_watch.py"),
        xbox,
        "--seconds",
        str(seconds),
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
    if from_start:
        command.append("--from-start")
    log.line("\n[Redirector router and request monitor]")
    process = subprocess.Popen(
        command,
        cwd=HERE.parent,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
    )
    try:
        if process.stdout is None:
            raise RuntimeError("Router stdout pipe is unavailable")
        ready_announced = False
        for line in process.stdout:
            line = line.rstrip()
            log.line(line)
            if line == "POSTAUTH_ROUTER_READY" and not ready_announced:
                log.line(
                    "\nREVIVAL_READY: open Ultimate Team exactly once. "
                    "The complete chain is armed."
                )
                ready_announced = True
        return process.wait()
    except KeyboardInterrupt:
        log.line("\nMonitor interrupted; collecting diagnostics.")
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        return 130


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--xbox", default=DEFAULT_XBOX)
    parser.add_argument("--local-ip")
    parser.add_argument("--wait-seconds", type=int, default=300)
    parser.add_argument("--settle-seconds", type=float, default=5.0)
    parser.add_argument("--monitor-seconds", type=int, default=900)
    parser.add_argument(
        "--from-start",
        action="store_true",
        help="Replay send-ring records that predate router startup",
    )
    parser.add_argument(
        "--diagnostics",
        action="store_true",
        help="Install the optional invasive trace hooks",
    )
    args = parser.parse_args()

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    session_dir = HERE / f"fifa14_revive_session_{stamp}"
    session_dir.mkdir(parents=True)
    log = SessionLog(session_dir / "session.log")
    sink: subprocess.Popen[str] | None = None
    try:
        local_ip = args.local_ip or detect_local_ip(args.xbox)
        local_ip = str(ipaddress.IPv4Address(local_ip))
        log.line(f"Session directory: {session_dir}")
        log.line(f"Xbox: {args.xbox}")
        log.line(f"Mac local IP: {local_ip}")

        if not xbdm_ready(args.xbox):
            raise RuntimeError(f"XBDM is not reachable at {args.xbox}:730")
        log.line("XBDM: reachable")

        redirector, preauth = build_responses(log, local_ip, session_dir)

        if not title_loaded(args.xbox):
            log.line(
                "\nWAITING_FOR_FIFA: launch FIFA 14 and wait for its main menu."
            )
            deadline = time.monotonic() + args.wait_seconds
            while time.monotonic() < deadline and not title_loaded(args.xbox):
                time.sleep(1)
            if not title_loaded(args.xbox):
                raise TimeoutError("FIFA 14 default.xex did not load in time")
            log.line(
                f"default.xex detected; waiting {args.settle_seconds:.1f}s "
                "for title initialization."
            )
            time.sleep(args.settle_seconds)
        else:
            log.line("default.xex: already loaded")

        apply_chain(
            log,
            args.xbox,
            local_ip,
            redirector,
            preauth,
            diagnostics=args.diagnostics,
        )
        if not args.from_start:
            clear_stale_pending_response(log, args.xbox)
        sink = start_tcp_sink(
            log,
            local_ip,
            session_dir,
            args.monitor_seconds,
        )
        if sink.poll() is not None:
            raise RuntimeError("Local TCP sink exited before router startup")
        result = monitor(
            log,
            args.xbox,
            redirector,
            session_dir,
            args.monitor_seconds,
            from_start=args.from_start,
        )
        collect_diagnostics(log, args.xbox)
        log.line(f"\nSession complete: router exit code {result}")
        log.line(f"Full log: {log.path}")
        return 0 if result in (0, 130) else result
    except Exception as error:
        log.line(f"\nERROR: {error}")
        return 1
    finally:
        if sink is not None:
            stop_tcp_sink(log, sink)
        log.close()


if __name__ == "__main__":
    raise SystemExit(main())
