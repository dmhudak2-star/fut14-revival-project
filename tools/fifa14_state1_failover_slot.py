#!/usr/bin/env python3
"""Turn one natural FIFA 14 network failure into the native state-2 event.

The vtable slot is changed only after the listener has naturally reached
state 1.  The game's own failure callback therefore invokes the ordinary
state-2 fan-out on the original network thread.  The slot is restored before
any Cards post-check and from every timeout/exception/Ctrl-C path.
"""

from __future__ import annotations

import argparse
import time

from fifa14_natural_cards_state2 import (
    AUTH_VTABLE,
    LISTENER_VTABLE,
    STATE2_TRANSITION,
    find_listener,
    u32,
    validate_cards,
)
from fifa14_plain_send_hook import Xbdm, verify_module


STATE1_TRANSITION = 0x8251A560
STATE0_TRANSITION = 0x8251A658
STATE0_SLOT = LISTENER_VTABLE + 0x08
STATE2_SLOT = LISTENER_VTABLE + 0x0C
LISTENER_STATE = 0x0974

# These are consumed by the Cards +C lifecycle in addition to the globals
# already checked by fifa14_natural_cards_state2.validate_cards().
EXTRA_HOST_GLOBALS = (0x897C335C, 0x897C33B4)


def be32(value: int) -> bytes:
    return value.to_bytes(4, "big")


def verify_powdll(client: Xbdm) -> None:
    module = next(
        (
            line
            for line in client.multiline("modules")
            if 'name="powdllzf.xex.dll"' in line.lower()
        ),
        None,
    )
    if module is None or "base=0x89700000" not in module.lower():
        raise RuntimeError(f"Unexpected or missing powdllzf: {module}")


def preflight(client: Xbdm) -> tuple[int, int, int]:
    verify_module(client)
    verify_powdll(client)
    listener = find_listener(client)
    root, observer_count = validate_cards(client, listener)

    if u32(client, listener) != LISTENER_VTABLE:
        raise RuntimeError("The live listener vtable changed")
    if u32(client, LISTENER_VTABLE + 0x04) != STATE1_TRANSITION:
        raise RuntimeError("Unexpected state-1 vtable slot")
    if u32(client, STATE2_SLOT) != STATE2_TRANSITION:
        raise RuntimeError("Unexpected state-2 vtable slot")
    state0 = u32(client, STATE0_SLOT)
    if state0 not in (STATE0_TRANSITION, STATE2_TRANSITION):
        raise RuntimeError(f"Unexpected state-0 vtable slot: 0x{state0:08X}")
    for address in EXTRA_HOST_GLOBALS:
        value = u32(client, address)
        if not value:
            raise RuntimeError(f"Cards host global 0x{address:08X} is null")
    return listener, root, observer_count


def restore_slot(client: Xbdm) -> None:
    current = u32(client, STATE0_SLOT)
    if current == STATE0_TRANSITION:
        return
    if current != STATE2_TRANSITION:
        raise RuntimeError(
            f"Refusing to overwrite foreign state-0 slot 0x{current:08X}"
        )
    client.write(STATE0_SLOT, be32(STATE0_TRANSITION))
    restored = u32(client, STATE0_SLOT)
    if restored != STATE0_TRANSITION:
        raise RuntimeError(f"State-0 slot restore failed: 0x{restored:08X}")


def restore_slot_fresh(host: str, attempts: int = 3) -> None:
    """Restore with fresh XBDM connections after a poisoned socket/timeout."""
    errors: list[str] = []
    for attempt in range(1, attempts + 1):
        recovery: Xbdm | None = None
        try:
            recovery = Xbdm(host)
            verify_module(recovery)
            restore_slot(recovery)
            return
        except Exception as error:
            errors.append(f"attempt {attempt}: {error}")
            if attempt != attempts:
                time.sleep(0.15)
        finally:
            if recovery is not None:
                try:
                    recovery.close()
                except Exception:
                    pass
    detail = "; ".join(errors)
    raise RuntimeError(
        "CRITICAL: automatic state-0 slot restoration failed "
        f"({detail}). Run immediately: python3 "
        f"outputs/fifa14_state1_failover_slot.py {host} restore"
    )


def show_status(client: Xbdm) -> None:
    listener, root, observer_count = preflight(client)
    print(f"network listener = 0x{listener:08X}")
    print(f"observer count   = {observer_count}")
    print(f"listener state   = {u32(client, listener + LISTENER_STATE)}")
    print(f"state-0 slot     = 0x{u32(client, STATE0_SLOT):08X}")
    print(f"Cards root       = 0x{root:08X}")
    print(f"Cards auth       = 0x{u32(client, root + 0x3A08):08X}")


def wait_apply(
    host: str, client: Xbdm, wait_seconds: float, result_seconds: float
) -> None:
    listener, root, observer_count = preflight(client)
    if u32(client, STATE0_SLOT) != STATE0_TRANSITION:
        raise RuntimeError("State-0 slot is already redirected; run restore")

    print(f"Network listener: 0x{listener:08X} ({observer_count} observers)")
    print("ARMED: click Ultimate Team once now.", flush=True)

    armed_deadline = time.monotonic() + wait_seconds
    while True:
        state = u32(client, listener + LISTENER_STATE)
        if state == 1:
            break
        if time.monotonic() >= armed_deadline:
            raise RuntimeError("Listener did not reach state 1 within the window")
        time.sleep(0.025)

    patch_maybe_live = False
    restored = False
    observed_state2 = False
    try:
        # Set this before setmem: the Xbox may apply the write even if the
        # XBDM acknowledgement is lost and client.write raises.
        patch_maybe_live = True
        client.write(STATE0_SLOT, be32(STATE2_TRANSITION))
        if u32(client, STATE0_SLOT) != STATE2_TRANSITION:
            raise RuntimeError("State-0 slot redirection did not verify")
        state_after_write = u32(client, listener + LISTENER_STATE)
        if state_after_write == 2:
            observed_state2 = True
        elif state_after_write != 1:
            raise RuntimeError(
                f"Listener left state 1 during redirection "
                f"(state={state_after_write})"
            )
        print("State 1 observed; one failure-to-success relay installed.", flush=True)

        result_deadline = time.monotonic() + result_seconds
        last_state = state_after_write
        if not observed_state2:
            while time.monotonic() < result_deadline:
                last_state = u32(client, listener + LISTENER_STATE)
                if last_state == 2:
                    observed_state2 = True
                    break
                if last_state != 1:
                    raise RuntimeError(
                        f"Listener reached unexpected state {last_state}"
                    )
                time.sleep(0.01)
            else:
                raise RuntimeError(
                    f"No natural failure callback within the window "
                    f"(state={last_state})"
                )
    finally:
        # Restore before *any* Cards post-check.  First try the existing
        # connection, then use new connections if that socket timed out.
        if patch_maybe_live:
            try:
                restore_slot(client)
            except Exception as current_error:
                print(
                    f"Primary restore path failed ({current_error}); "
                    "retrying with a fresh XBDM connection.",
                    flush=True,
                )
                restore_slot_fresh(host)
            restored = True
        if restored:
            print("State-0 vtable slot restored and verified.", flush=True)

    # Use a fresh socket for the post-check as the polling connection may
    # have recovered from a timeout during the finally block.
    post = Xbdm(host)
    try:
        verify_module(post)
        if u32(post, STATE0_SLOT) != STATE0_TRANSITION:
            raise RuntimeError("State-0 slot is not original after restore")
        state = u32(post, listener + LISTENER_STATE)
        auth = u32(post, root + 0x3A08)
        auth_vtable = u32(post, auth) if auth else 0
    finally:
        post.close()
    print(f"listener state = {state}")
    print(f"Cards auth     = 0x{auth:08X}")
    print(f"auth vtable    = 0x{auth_vtable:08X}")
    if state != 2:
        raise RuntimeError("Native listener did not finish in state 2")
    if not auth or auth_vtable != AUTH_VTABLE:
        raise RuntimeError("State 2 completed but Cards auth was not created")
    print("VERIFIED: native state2 created the Cards authentication object.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("status", "wait-apply", "restore"))
    parser.add_argument("--wait-seconds", type=float, default=300.0)
    parser.add_argument("--result-seconds", type=float, default=45.0)
    args = parser.parse_args()

    client = Xbdm(args.host)
    try:
        if args.action == "status":
            show_status(client)
        elif args.action == "restore":
            verify_module(client)
            restore_slot(client)
            print("State-0 vtable slot is original and verified.")
        else:
            wait_apply(args.host, client, args.wait_seconds, args.result_seconds)
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrupted; restoration attempted.")
        raise SystemExit(130)
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
