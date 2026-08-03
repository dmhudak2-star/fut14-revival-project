#!/usr/bin/env python3
"""Route the supported FIFA 14 Xbox build to a local Blaze server at startup.

The title module is stopped on its XBDM modload notification, before game code
starts.  The original EA hostnames are intentionally preserved so the title's
resolver follows its normal path.  A narrow DirtySock connect hook redirects
only Blaze ports to the local server, and the Redirector connection profile is
set to unencrypted Blaze.  All changes are volatile and disappear when the
title unloads.
"""

from __future__ import annotations

import argparse
import ipaddress
import select
import sys
import time

from fifa14_connect_bypass import (
    CONNECT_CALLSITE,
    CONNECT_LOG,
    CONNECT_STUB,
    ORIGINAL_CONNECT_CALL,
)
from fifa14_connect_journal import PATCHED_CONNECT_CALL
from fifa14_connect_redirect import (
    CONNECT_RESULT_STUB,
    CONNECT_RESULT_STUB_BYTES,
    SOCKET_SECURITY_STUB,
    SOCKET_SECURITY_STUB_BYTES,
    build_stub as build_connect_stub,
)
from fifa14_early_redirector_patch import Connection, HOSTS
from fifa14_redirector_profile_patch import (
    PROFILE_POINTER,
    STANDARD_INSECURE,
    XBOX360_SECURE,
    encoded,
    state,
)
from fifa14_xnet_startup_patch import (
    NOSECURE_MODE_BRANCH,
    NOSECURE_MODE_ORIGINAL,
    NOSECURE_MODE_PATCHED,
    XNET_BYPASS_BRANCH,
    XNET_BYPASS_ORIGINAL,
    XNET_BYPASS_PATCHED,
)
from fifa14_plain_recv_hook import cmpwi, conditional_branch
from fifa14_plain_send_hook import addi, addis, branch, insn, write_chunks
import fifa14_login_callback_trace as login_callback_trace
import fifa14_ea_login_state_trace as ea_login_state_trace
import fifa14_postauth_dispatch_trace as postauth_dispatch_trace
import fifa14_useradded_trace as useradded_trace
import fifa14_fut_resource_url_trace as fut_resource_url_trace
import fifa14_connection_result_trace as connection_result_trace
import fifa14_connected_owner_path_probe as connected_owner_trace


FIFA_PDATA = 'pdata=0x82329200'
FIFA_PSIZE = 'psize=0x0009e1e0'

# Authentication2 receives a null Xbox token on the current offline setup.
# This narrow shim substitutes a local placeholder only for that null/null
# callback result; a genuine ticket is left untouched.
TICKET_SITE = 0x82F3ED00
TICKET_ORIGINAL = bytes.fromhex("7D8802A6")  # mflr r12
TICKET_STUB = 0x83C8DB80
TICKET_DUMMY = 0x83C8DBC0
TICKET_STUB_SIZE = TICKET_DUMMY - TICKET_STUB
TICKET_DUMMY_SIZE = 0x40
TICKET_VALUE = b"XBL2.0 x=offline;offline-fifa14-token"

AUTH2_CONFIG_SITE = 0x82F401B8
AUTH2_CONFIG_ORIGINAL = bytes.fromhex("7D8802A6")  # mflr r12
AUTH2_CONFIG_STUB = 0x83C8DA00
AUTH2_CONFIG_STUB_SIZE = 0x40
AUTH2_JOURNAL = 0x83C8DA80
AUTH2_JOURNAL_SIZE = 0x40


def build_auth2_config_stub() -> bytes:
    words = [
        addis(12, 0, 0x83C9),
        addi(12, 12, -0x2580),      # AUTH2_JOURNAL
        0x816C0000,                 # lwz r11, 0(r12)
        addi(11, 11, 1),
        0x916C0000,                 # stw r11, 0(r12)
        0x906C0004,                 # stw r3, 4(r12)
        0x908C0008,                 # stw r4, 8(r12)
        0x90AC000C,                 # stw r5, 0xC(r12)
        int.from_bytes(AUTH2_CONFIG_ORIGINAL, "big"),
        branch(AUTH2_CONFIG_STUB + 9 * 4, AUTH2_CONFIG_SITE + 4, False),
    ]
    image = b"".join(insn(word) for word in words)
    return image.ljust(AUTH2_CONFIG_STUB_SIZE, b"\0")


AUTH2_CONFIG_STUB_BYTES = build_auth2_config_stub()
AUTH2_CONFIG_PATCH = insn(branch(
    AUTH2_CONFIG_SITE,
    AUTH2_CONFIG_STUB,
    False,
))


def build_ticket_stub() -> bytes:
    words = [
        # Share the passive Authentication2 journal with the IdentityParams
        # entry probe.  The real ticket callback is replaced below only to
        # supply a placeholder for a null offline XBL token; these stores make
        # that otherwise hidden native call observable without changing its
        # arguments or outcome.
        addis(11, 0, 0x83C9),
        addi(11, 11, -0x2580),       # auth2 journal 0x83C8DA80
        addi(10, 0, 1),
        0x914B0020,                  # stw r10, 0x20(r11)
        0x906B0024,                  # stw r3, 0x24(r11)
        0x908B0028,                  # stw r4, 0x28(r11)
        0x90AB002C,                  # stw r5, 0x2C(r11)
        cmpwi(4, 0),
        0,  # bne original
        cmpwi(5, 0),
        0,  # bne original
        addis(4, 0, (TICKET_DUMMY + 0x8000) >> 16),
        addi(4, 4, TICKET_DUMMY & 0xFFFF),
        addi(5, 0, len(TICKET_VALUE)),
    ]
    original = len(words)
    words.extend((int.from_bytes(TICKET_ORIGINAL, "big"), 0))

    def address(index: int) -> int:
        return TICKET_STUB + index * 4

    for index in (8, 10):
        words[index] = conditional_branch(
            address(index), address(original), 4, 2
        )
    words[-1] = branch(address(len(words) - 1), TICKET_SITE + 4, False)
    image = b"".join(insn(word) for word in words)
    if len(image) > TICKET_STUB_SIZE:
        raise AssertionError("offline ticket shim exceeds its code cave")
    return image.ljust(TICKET_STUB_SIZE, b"\0")


TICKET_STUB_BYTES = build_ticket_stub()
TICKET_PATCH = insn(branch(TICKET_SITE, TICKET_STUB, False))
TICKET_DUMMY_BYTES = (TICKET_VALUE + b"\0").ljust(
    TICKET_DUMMY_SIZE, b"\0"
)


def arm_login_flow_traces(control: Connection) -> None:
    """Publish the passive title-login trace set before execution."""
    for module, label in (
        (postauth_dispatch_trace, "postAuth dispatch"),
        (login_callback_trace, "LoginStateLogin callbacks"),
        (useradded_trace, "UserAdded/local-user identity"),
    ):
        for index, probe in enumerate(module.PROBES if hasattr(module, "PROBES") else module.CALLBACKS):
            current = control.read(probe.site, 4)
            original = probe.original
            if current != original:
                raise RuntimeError(
                    f"{label} site 0x{probe.site:08X} is not retail: "
                    f"{current.hex().upper()}"
                )

    for probe, (entry_state, return_state) in zip(
        ea_login_state_trace.PROBES,
        ea_login_state_trace.probe_states(control),
    ):
        if entry_state != "original" or return_state not in ("original", "none"):
            raise RuntimeError(
                f"native EA-login state site for {probe.name} is not retail: "
                f"entry={entry_state}, return={return_state}"
            )

    if control.read(AUTH2_CONFIG_SITE, 4) != AUTH2_CONFIG_ORIGINAL:
        raise RuntimeError("Authentication2 IdentityParams entry is not retail")
    if control.read(connection_result_trace.SITE, 4) != connection_result_trace.ORIGINAL:
        raise RuntimeError("Blaze connection-result entry is not retail")

    write_chunks(
        control,
        postauth_dispatch_trace.JOURNAL,
        bytes(len(postauth_dispatch_trace.PROBES) * postauth_dispatch_trace.RECORD_SIZE),
    )
    for index, probe in enumerate(postauth_dispatch_trace.PROBES):
        write_chunks(
            control,
            postauth_dispatch_trace.stub_address(index),
            postauth_dispatch_trace.build_stub(index, probe),
        )
        control.write(
            probe.site,
            postauth_dispatch_trace.patch_for(index, probe),
        )

    write_chunks(
        control,
        login_callback_trace.JOURNAL,
        bytes(len(login_callback_trace.CALLBACKS) * login_callback_trace.RECORD_SIZE),
    )
    for index, callback in enumerate(login_callback_trace.CALLBACKS):
        write_chunks(
            control,
            login_callback_trace.stub_address(index),
            login_callback_trace.build_stub(index, callback),
        )
        control.write(
            callback.site,
            login_callback_trace.patch_for(index, callback),
        )

    write_chunks(
        control,
        useradded_trace.JOURNAL,
        bytes(len(useradded_trace.PROBES) * useradded_trace.RECORD_SIZE),
    )
    for index, probe in enumerate(useradded_trace.PROBES):
        write_chunks(
            control,
            probe.stub,
            useradded_trace.build_stub(index, probe),
        )
        control.write(probe.site, useradded_trace.patch_for(probe))

    # Authentication2's config callback remains fully retail.  The ticket
    # callback is logged by the local-placeholder shim itself because both
    # features necessarily own the same entry instruction.
    write_chunks(
        control,
        AUTH2_JOURNAL,
        bytes(AUTH2_JOURNAL_SIZE),
    )
    write_chunks(
        control,
        AUTH2_CONFIG_STUB,
        AUTH2_CONFIG_STUB_BYTES,
    )
    control.write(
        AUTH2_CONFIG_SITE,
        AUTH2_CONFIG_PATCH,
    )

    connection_stub = connection_result_trace.build_stub()
    write_chunks(
        control,
        connection_result_trace.JOURNAL,
        bytes(connection_result_trace.JOURNAL_SIZE),
    )
    write_chunks(control, connection_result_trace.STUB, connection_stub)
    control.write(
        connection_result_trace.SITE,
        insn(branch(
            connection_result_trace.SITE,
            connection_result_trace.STUB,
            False,
        )),
    )

    for index, probe in enumerate(postauth_dispatch_trace.PROBES):
        if control.read(probe.site, 4) != postauth_dispatch_trace.patch_for(index, probe):
            raise RuntimeError(
                f"postAuth trace verification failed at 0x{probe.site:08X}"
            )
    for index, callback in enumerate(login_callback_trace.CALLBACKS):
        if control.read(callback.site, 4) != login_callback_trace.patch_for(index, callback):
            raise RuntimeError(
                f"login callback trace verification failed at 0x{callback.site:08X}"
            )
    for probe in useradded_trace.PROBES:
        if control.read(probe.site, 4) != useradded_trace.patch_for(probe):
            raise RuntimeError(
                f"UserAdded trace verification failed at 0x{probe.site:08X}"
            )

    if control.read(AUTH2_CONFIG_SITE, 4) != AUTH2_CONFIG_PATCH:
        raise RuntimeError("Authentication2 IdentityParams trace verification failed")
    expected_connection_patch = insn(branch(
        connection_result_trace.SITE,
        connection_result_trace.STUB,
        False,
    ))
    if control.read(connection_result_trace.SITE, 4) != expected_connection_patch:
        raise RuntimeError("Blaze connection-result trace verification failed")

    ea_login_state_trace.arm(control)

    # Observe the owner that receives the established-connection state and the
    # gated observer call that is expected to resume a deferred online-mode
    # request.  This is passive: the original entry instruction and original
    # observer call target are both preserved by the trace trampolines.
    entry_stub = connected_owner_trace.build_entry_stub()
    call_stub = connected_owner_trace.build_call_stub()
    connected_owner_trace.validate_layout(entry_stub, call_stub)
    entry_image = entry_stub.ljust(
        connected_owner_trace.ENTRY_SLOT_END - connected_owner_trace.ENTRY_STUB,
        b"\0",
    )
    call_image = call_stub.ljust(
        connected_owner_trace.CALL_SLOT_END - connected_owner_trace.CALL_STUB,
        b"\0",
    )
    entry_patch = insn(branch(
        connected_owner_trace.ENTRY_SITE,
        connected_owner_trace.ENTRY_STUB,
        False,
    ))
    call_patch = insn(branch(
        connected_owner_trace.CALL_SITE,
        connected_owner_trace.CALL_STUB,
        True,
    ))

    if control.read(connected_owner_trace.ENTRY_SITE, 4) != connected_owner_trace.ENTRY_ORIGINAL:
        raise RuntimeError("connected-owner entry is not retail")
    if control.read(connected_owner_trace.CALL_SITE, 4) != connected_owner_trace.CALL_ORIGINAL:
        raise RuntimeError("connected-owner observer call is not retail")
    for address, image in (
        (connected_owner_trace.ENTRY_STUB, entry_image),
        (connected_owner_trace.CALL_STUB, call_image),
    ):
        current = control.read(address, len(image))
        if current not in (bytes(len(image)), image):
            raise RuntimeError(
                f"connected-owner trace cave 0x{address:08X} is occupied"
            )

    write_chunks(
        control,
        connected_owner_trace.JOURNAL,
        bytes(connected_owner_trace.JOURNAL_SIZE),
    )
    write_chunks(control, connected_owner_trace.ENTRY_STUB, entry_image)
    write_chunks(control, connected_owner_trace.CALL_STUB, call_image)
    control.write(connected_owner_trace.ENTRY_SITE, entry_patch)
    control.write(connected_owner_trace.CALL_SITE, call_patch)
    if (
        control.read(connected_owner_trace.ENTRY_SITE, 4) != entry_patch
        or control.read(connected_owner_trace.CALL_SITE, 4) != call_patch
    ):
        raise RuntimeError("connected-owner trace verification failed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host", help="Xbox IP address")
    parser.add_argument("--local-ip", required=True, help="Mac/server IPv4")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument(
        "--trace-login-flow",
        action="store_true",
        help="arm passive postAuth and LoginStateLogin traces before execution",
    )
    parser.add_argument(
        "--trace-fut-resource",
        action="store_true",
        help="capture the native 'fut' resource URL before its temporary string is freed",
    )
    parser.add_argument(
        "--redirect-fut-resource",
        action="store_true",
        help="route only native futBoot.xml loading to the local HTTP service",
    )
    args = parser.parse_args()

    if args.trace_fut_resource and args.redirect_fut_resource:
        parser.error(
            "--trace-fut-resource and --redirect-fut-resource are mutually exclusive"
        )

    local_ip = str(ipaddress.IPv4Address(args.local_ip))
    connect_stub = build_connect_stub(int(ipaddress.IPv4Address(local_ip)))

    notify = Connection(args.host)
    control: Connection | None = None
    stopped = False
    try:
        notify.command(
            'debugger connect override name="FIFALocalServer" user="CodexMac"'
        )
        notify.command("notify reconnectport=1", expected=205)
        control = Connection(args.host)

        print("Waiting for default.xex. Launch FIFA 14 now.", flush=True)
        deadline = time.monotonic() + args.timeout
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

            # Replacing these strings with a numeric address prevents this
            # build's resolver from reaching connect().  Verify the retail
            # names and leave them untouched.
            for address, original in HOSTS:
                before = control.read(address, len(original))
                if before != original:
                    raise RuntimeError(
                        f"Unexpected hostname bytes at 0x{address:08X}: {before!r}"
                    )

            nosecure_branch = control.read(NOSECURE_MODE_BRANCH, 4)
            xnet_branch = control.read(XNET_BYPASS_BRANCH, 4)
            if (
                nosecure_branch != NOSECURE_MODE_ORIGINAL
                or xnet_branch != XNET_BYPASS_ORIGINAL
            ):
                raise RuntimeError(
                    "FIFA nosecure branches are not the supported retail image"
                )
            control.write(NOSECURE_MODE_BRANCH, NOSECURE_MODE_PATCHED)
            control.write(XNET_BYPASS_BRANCH, XNET_BYPASS_PATCHED)
            if (
                control.read(NOSECURE_MODE_BRANCH, 4) != NOSECURE_MODE_PATCHED
                or control.read(XNET_BYPASS_BRANCH, 4) != XNET_BYPASS_PATCHED
            ):
                control.write(NOSECURE_MODE_BRANCH, NOSECURE_MODE_ORIGINAL)
                control.write(XNET_BYPASS_BRANCH, XNET_BYPASS_ORIGINAL)
                raise RuntimeError("Full nosecure mode publication failed")

            current_call = control.read(CONNECT_CALLSITE, 4)
            if current_call != ORIGINAL_CONNECT_CALL:
                raise RuntimeError(
                    "DirtySock connect callsite is not the supported retail image"
                )
            control.write(CONNECT_LOG, bytes(0x3C))
            control.write(SOCKET_SECURITY_STUB, SOCKET_SECURITY_STUB_BYTES)
            if (
                control.read(
                    SOCKET_SECURITY_STUB,
                    len(SOCKET_SECURITY_STUB_BYTES),
                )
                != SOCKET_SECURITY_STUB_BYTES
            ):
                raise RuntimeError("Socket security helper verification failed")
            control.write(CONNECT_RESULT_STUB, CONNECT_RESULT_STUB_BYTES)
            if (
                control.read(CONNECT_RESULT_STUB, len(CONNECT_RESULT_STUB_BYTES))
                != CONNECT_RESULT_STUB_BYTES
            ):
                raise RuntimeError("Connect result helper verification failed")
            control.write(CONNECT_STUB, connect_stub)
            if control.read(CONNECT_STUB, len(connect_stub)) != connect_stub:
                raise RuntimeError("Connect redirect stub verification failed")
            control.write(CONNECT_CALLSITE, PATCHED_CONNECT_CALL)
            if control.read(CONNECT_CALLSITE, 4) != PATCHED_CONNECT_CALL:
                control.write(CONNECT_CALLSITE, ORIGINAL_CONNECT_CALL)
                raise RuntimeError("Connect redirect publication failed")

            current_profile = int.from_bytes(
                control.read(PROFILE_POINTER, 4), "big"
            )
            print(f"Redirector profile before: {state(current_profile)}")
            if current_profile not in (XBOX360_SECURE, STANDARD_INSECURE):
                raise RuntimeError(
                    f"Unexpected Redirector profile 0x{current_profile:08X}"
                )
            if current_profile != STANDARD_INSECURE:
                control.write(PROFILE_POINTER, encoded(STANDARD_INSECURE))
            verified_profile = int.from_bytes(
                control.read(PROFILE_POINTER, 4), "big"
            )
            if verified_profile != STANDARD_INSECURE:
                raise RuntimeError("Redirector profile verification failed")

            ticket_entry = control.read(TICKET_SITE, 4)
            if ticket_entry != TICKET_ORIGINAL:
                raise RuntimeError(
                    "Authentication2 ticket callback is not the supported retail image"
                )
            ticket_cave = control.read(TICKET_STUB, TICKET_STUB_SIZE)
            ticket_dummy = control.read(TICKET_DUMMY, TICKET_DUMMY_SIZE)
            if ticket_cave not in (bytes(TICKET_STUB_SIZE), TICKET_STUB_BYTES):
                raise RuntimeError("Authentication2 ticket code cave is not empty")
            if ticket_dummy not in (bytes(TICKET_DUMMY_SIZE), TICKET_DUMMY_BYTES):
                raise RuntimeError("Authentication2 ticket data cave is not empty")
            control.write(TICKET_STUB, TICKET_STUB_BYTES)
            control.write(TICKET_DUMMY, TICKET_DUMMY_BYTES)
            control.write(TICKET_SITE, TICKET_PATCH)
            if (
                control.read(TICKET_SITE, 4) != TICKET_PATCH
                or control.read(TICKET_STUB, TICKET_STUB_SIZE)
                != TICKET_STUB_BYTES
                or control.read(TICKET_DUMMY, TICKET_DUMMY_SIZE)
                != TICKET_DUMMY_BYTES
            ):
                control.write(TICKET_SITE, TICKET_ORIGINAL)
                raise RuntimeError(
                    "Authentication2 ticket shim verification failed"
                )

            if args.trace_login_flow:
                arm_login_flow_traces(control)
            if args.trace_fut_resource:
                fut_resource_url_trace.arm(control)
            if args.redirect_fut_resource:
                fut_resource_url_trace.arm(
                    control,
                    f"http://{local_ip}:18080/futBoot.xml",
                )

            print(
                "Verified: retail hostnames preserved, "
                f"Blaze connect={local_ip}, profile=standardInsecure_v3, "
                "DirtySock=-nosecure, XNetStartup=bypass-security, "
                "socket=unencrypted, empty-XBL-ticket=local-placeholder"
                + (
                    ", passive login-flow traces=armed"
                    if args.trace_login_flow else ""
                )
                + (
                    ", passive FUT-resource URL trace=armed"
                    if args.trace_fut_resource else ""
                )
                + (
                    ", native FUT-resource redirect=armed"
                    if args.redirect_fut_resource else ""
                )
            )
            control.command("go")
            stopped = False
            print("Execution resumed. Continue to the FIFA menu.")
            return 0

        raise TimeoutError("default.xex modload event was not observed")
    finally:
        if control is not None:
            if stopped:
                try:
                    control.command("go")
                    print("Execution resumed during cleanup.")
                except Exception:
                    pass
            control.close()
        notify.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted; cleanup attempted.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as error:
        print(f"\nError: {error}", file=sys.stderr)
        raise SystemExit(1)
