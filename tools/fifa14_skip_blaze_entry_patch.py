#!/usr/bin/env python3
"""Apply or restore the two-site FIFA 14 Skip-Blaze entry patch.

The volatile patch makes ``LoadFUTSkipBlaze`` return true and makes the
EnterFUT2 decision branch use its existing fast path.  The two owned sites
are prevalidated and published as one transaction.  If a setmem reply is
lost, rollback is performed through fresh XBDM connections because the
original connection can no longer be trusted.

The fast-path callsite at 0x828352F4 is deliberately not owned by this
script.  Both its native getter call and the known Cards UI dual hook are
accepted and left untouched.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import re
import sys
import time

import fifa14_cards_ui_init_once_v2 as cards_ui_v2
from fifa14_plain_send_hook import Xbdm


MODULE_NAME = "default.xex"
MODULE_BASE = 0x82000000
MODULE_TIMESTAMP = 0x534C8977

LOAD_FUT_SKIP_BLAZE = 0x82805D30
LOAD_FUT_SKIP_BLAZE_ORIGINAL = bytes.fromhex("3D6083D9896B22A9")
LOAD_FUT_SKIP_BLAZE_PATCHED = bytes.fromhex("386000014E800020")
LOAD_FUT_SKIP_BLAZE_TAIL = 0x82805D38
LOAD_FUT_SKIP_BLAZE_TAIL_ORIGINAL = bytes.fromhex(
    "314BFFFF7C6A59104E800020"
)

ENTER_FUT2_BRANCH = 0x82835198
ENTER_FUT2_BRANCH_ORIGINAL = bytes.fromhex("4082015C")
ENTER_FUT2_BRANCH_PATCHED = bytes.fromhex("4800015C")

FAST_PATH = 0x828352F4
FAST_PATH_ORIGINAL = bytes.fromhex("4BF9107D")  # bl 0x827C6370
FAST_PATH_DUAL_HOOK = bytes.fromhex("49455D0D")  # bl 0x83C8B000
FAST_PATH_DUAL_HOOK_TARGET = 0x83C8B000

POW_MODULE_NAME = "powdllzf.xex.dll"
POW_MODULE_BASE = 0x89700000
CARDS_ROOT_GLOBAL = 0x897C3608
CARDS_AUTH_OFFSET = 0x3A08
CARDS_AUTH_VTABLE = 0x89707078


@dataclass(frozen=True)
class PatchSite:
    name: str
    address: int
    original: bytes
    patched: bytes


PATCH_SITES = (
    PatchSite(
        "EnterFUT2 branch",
        ENTER_FUT2_BRANCH,
        ENTER_FUT2_BRANCH_ORIGINAL,
        ENTER_FUT2_BRANCH_PATCHED,
    ),
    PatchSite(
        "LoadFUTSkipBlaze",
        LOAD_FUT_SKIP_BLAZE,
        LOAD_FUT_SKIP_BLAZE_ORIGINAL,
        LOAD_FUT_SKIP_BLAZE_PATCHED,
    ),
)


class UnexpectedSiteError(RuntimeError):
    """A patch site contains bytes that are not owned by this script."""


def u32(raw: bytes) -> int:
    if len(raw) != 4:
        raise ValueError(f"Expected four bytes, received {len(raw)}")
    return int.from_bytes(raw, "big")


def branch_target(source: int, instruction: bytes) -> int:
    """Decode the target of a relative PowerPC b/bl instruction."""
    word = u32(instruction)
    if word >> 26 != 18 or word & 2:
        raise AssertionError(f"0x{word:08X} is not a relative b/bl")
    displacement = word & 0x03FFFFFC
    if displacement & 0x02000000:
        displacement -= 0x04000000
    return (source + displacement) & 0xFFFFFFFF


def conditional_branch_target(source: int, instruction: bytes) -> int:
    """Decode the target of a relative PowerPC bc instruction."""
    word = u32(instruction)
    if word >> 26 != 16 or word & 2:
        raise AssertionError(f"0x{word:08X} is not a relative bc")
    displacement = word & 0xFFFC
    if displacement & 0x8000:
        displacement -= 0x10000
    return (source + displacement) & 0xFFFFFFFF


def validate_static() -> None:
    """Catch address, opcode, and ownership regressions without an Xbox."""
    if MODULE_TIMESTAMP != 0x534C8977:
        raise AssertionError("Unexpected FIFA 14 build timestamp")
    expected_sites = (
        (
            0x82835198,
            bytes.fromhex("4082015C"),
            bytes.fromhex("4800015C"),
        ),
        (
            0x82805D30,
            bytes.fromhex("3D6083D9896B22A9"),
            bytes.fromhex("386000014E800020"),
        ),
    )
    actual_sites = tuple(
        (site.address, site.original, site.patched) for site in PATCH_SITES
    )
    if actual_sites != expected_sites:
        raise AssertionError("Skip-Blaze patch sites or opcodes changed")
    if len(PATCH_SITES) != 2 or len({site.address for site in PATCH_SITES}) != 2:
        raise AssertionError("The transaction must own exactly two unique sites")
    for site in PATCH_SITES:
        if len(site.original) != len(site.patched):
            raise AssertionError(f"{site.name} changes instruction span length")
        if not site.original or len(site.original) % 4:
            raise AssertionError(f"{site.name} is not instruction aligned")

    if LOAD_FUT_SKIP_BLAZE_PATCHED != bytes.fromhex("386000014E800020"):
        raise AssertionError("LoadFUTSkipBlaze must be 'li r3,1; blr'")
    if (
        LOAD_FUT_SKIP_BLAZE_TAIL != LOAD_FUT_SKIP_BLAZE + 8
        or LOAD_FUT_SKIP_BLAZE_TAIL_ORIGINAL
        != bytes.fromhex("314BFFFF7C6A59104E800020")
    ):
        raise AssertionError("LoadFUTSkipBlaze native tail changed")
    if conditional_branch_target(
        ENTER_FUT2_BRANCH, ENTER_FUT2_BRANCH_ORIGINAL
    ) != FAST_PATH:
        raise AssertionError("Original EnterFUT2 branch target changed")
    if branch_target(ENTER_FUT2_BRANCH, ENTER_FUT2_BRANCH_PATCHED) != FAST_PATH:
        raise AssertionError("Patched EnterFUT2 branch misses the fast path")
    if branch_target(FAST_PATH, FAST_PATH_ORIGINAL) != 0x827C6370:
        raise AssertionError("Unexpected native fast-path getter")
    if (
        branch_target(FAST_PATH, FAST_PATH_DUAL_HOOK)
        != FAST_PATH_DUAL_HOOK_TARGET
        or not u32(FAST_PATH_DUAL_HOOK) & 1
    ):
        raise AssertionError("Unexpected Cards UI dual-hook encoding")
    if (
        cards_ui_v2.FAST_SITE != FAST_PATH
        or cards_ui_v2.FAST_ORIGINAL != FAST_PATH_ORIGINAL
        or cards_ui_v2.STUB != FAST_PATH_DUAL_HOOK_TARGET
        or cards_ui_v2.insn(
            cards_ui_v2.branch(cards_ui_v2.FAST_SITE, cards_ui_v2.STUB, True)
        )
        != FAST_PATH_DUAL_HOOK
    ):
        raise AssertionError("Cards UI v2 hook definition changed")


def find_module(client: Xbdm, name: str) -> str | None:
    pattern = re.compile(rf'name="{re.escape(name)}"', re.IGNORECASE)
    return next(
        (line for line in client.multiline("modules") if pattern.search(line)),
        None,
    )


def module_field(module: str, field: str) -> int | None:
    match = re.search(rf"\b{re.escape(field)}=0x([0-9A-Fa-f]+)", module)
    return int(match.group(1), 16) if match is not None else None


def verify_fifa_build(client: Xbdm) -> None:
    module = find_module(client, MODULE_NAME)
    if module is None:
        raise RuntimeError("FIFA 14 default.xex is not loaded")
    base = module_field(module, "base")
    timestamp = module_field(module, "timestamp")
    if base != MODULE_BASE or timestamp != MODULE_TIMESTAMP:
        base_text = "missing" if base is None else f"0x{base:08X}"
        timestamp_text = (
            "missing" if timestamp is None else f"0x{timestamp:08X}"
        )
        raise RuntimeError(
            "Unexpected FIFA 14 build: "
            f"base={base_text}, timestamp={timestamp_text}"
        )


def verify_cards_auth(client: Xbdm) -> tuple[int, int]:
    """Require the live pow Cards authentication object before applying."""
    pow_module = find_module(client, POW_MODULE_NAME)
    if pow_module is None or module_field(pow_module, "base") != POW_MODULE_BASE:
        raise RuntimeError(f"Unexpected or missing powdllzf module: {pow_module}")

    root = u32(client.read(CARDS_ROOT_GLOBAL, 4))
    if root == 0:
        raise RuntimeError(
            f"Cards root global 0x{CARDS_ROOT_GLOBAL:08X} is null"
        )
    auth = u32(client.read(root + CARDS_AUTH_OFFSET, 4))
    if auth == 0:
        raise RuntimeError(
            f"Cards auth root+0x{CARDS_AUTH_OFFSET:X} is null "
            f"(root=0x{root:08X})"
        )
    vtable = u32(client.read(auth, 4))
    if vtable != CARDS_AUTH_VTABLE:
        raise RuntimeError(
            "Unexpected Cards auth vtable: "
            f"0x{vtable:08X}, expected 0x{CARDS_AUTH_VTABLE:08X}"
        )
    return root, auth


def classify_site(current: bytes, site: PatchSite) -> str:
    if current == site.original:
        return "original"
    if current == site.patched:
        return "patched"
    return f"unexpected:{current.hex().upper()}"


def read_sites(client: Xbdm) -> dict[PatchSite, tuple[bytes, str]]:
    return {
        site: (
            current := client.read(site.address, len(site.original)),
            classify_site(current, site),
        )
        for site in PATCH_SITES
    }


def fast_path_state(client: Xbdm) -> tuple[str, bool]:
    current = client.read(FAST_PATH, 4)
    if current == FAST_PATH_ORIGINAL:
        return "native getter", True
    if current == FAST_PATH_DUAL_HOOK:
        try:
            receiver = cards_ui_v2.discover_connected_receiver(client)
            stub = cards_ui_v2.build_stub(receiver)
            cards_ui_v2.validate_layout(stub)
            expected = stub.ljust(
                cards_ui_v2.STUB_SLOT_END - cards_ui_v2.STUB, b"\0"
            )
            live = client.read(cards_ui_v2.STUB, len(expected))
        except Exception as error:
            return f"Cards UI dual hook invalid ({error})", False
        if live != expected:
            return "Cards UI dual hook invalid (stub image mismatch)", False
        return (
            f"Cards UI dual hook exact, receiver=0x{receiver:08X} "
            "(preserved)",
            True,
        )
    return f"unexpected:{current.hex().upper()}", False


def handler_tail_state(client: Xbdm) -> tuple[str, bool]:
    current = client.read(
        LOAD_FUT_SKIP_BLAZE_TAIL, len(LOAD_FUT_SKIP_BLAZE_TAIL_ORIGINAL)
    )
    if current == LOAD_FUT_SKIP_BLAZE_TAIL_ORIGINAL:
        return "original", True
    return f"unexpected:{current.hex().upper()}", False


def transaction_state(states: dict[PatchSite, tuple[bytes, str]]) -> str:
    labels = tuple(state for _, state in states.values())
    if all(state == "original" for state in labels):
        return "original"
    if all(state == "patched" for state in labels):
        return "patched"
    if all(state in ("original", "patched") for state in labels):
        return "mixed-owned"
    return "unexpected"


def print_status(
    states: dict[PatchSite, tuple[bytes, str]], fast_state: str, tail_state: str
) -> None:
    for site, (_, state) in states.items():
        print(f"0x{site.address:08X} {site.name}: {state}")
    print(f"transaction: {transaction_state(states)}")
    print(
        f"0x{LOAD_FUT_SKIP_BLAZE_TAIL:08X} LoadFUTSkipBlaze tail: "
        f"{tail_state}"
    )
    print(f"0x{FAST_PATH:08X} fast-path callsite: {fast_state}")


def require_known_sites(
    states: dict[PatchSite, tuple[bytes, str]], action: str
) -> None:
    unexpected = [
        f"0x{site.address:08X}={current.hex().upper()}"
        for site, (current, state) in states.items()
        if state not in ("original", "patched")
    ]
    if unexpected:
        raise UnexpectedSiteError(
            f"Refusing {action}; foreign bytes at " + ", ".join(unexpected)
        )


def restore_transaction(client: Xbdm) -> bool:
    """Restore both owned sites, accepting an interrupted owned transaction."""
    _, tail_ok = handler_tail_state(client)
    if not tail_ok:
        raise UnexpectedSiteError(
            "Refusing restore; the LoadFUTSkipBlaze native tail is foreign"
        )
    states = read_sites(client)
    require_known_sites(states, "restore")
    changed = False
    # Restore in exact reverse publication order: handler, then branch.
    for site in reversed(PATCH_SITES):
        if states[site][1] == "original":
            continue
        changed = True
        client.write(site.address, site.original)
        if client.read(site.address, len(site.original)) != site.original:
            raise RuntimeError(
                f"Restore verification failed at 0x{site.address:08X}"
            )
    final = read_sites(client)
    if transaction_state(final) != "original":
        raise RuntimeError("Final two-site restore verification failed")
    _, tail_ok = handler_tail_state(client)
    if not tail_ok:
        raise RuntimeError("LoadFUTSkipBlaze tail changed during restore")
    return changed


def restore_fresh(host: str, attempts: int = 3) -> None:
    """Restore with new XBDM sockets after a possibly lost setmem ACK."""
    errors: list[str] = []
    for attempt in range(1, attempts + 1):
        recovery: Xbdm | None = None
        try:
            recovery = Xbdm(host)
            verify_fifa_build(recovery)
            restore_transaction(recovery)
            return
        except BaseException as error:
            errors.append(f"attempt {attempt}: {error}")
            if attempt != attempts:
                time.sleep(0.15)
        finally:
            if recovery is not None:
                try:
                    recovery.close()
                except Exception:
                    pass
    raise RuntimeError(
        "CRITICAL: automatic two-site rollback failed ("
        + "; ".join(errors)
        + "). Run restore immediately with a fresh XBDM connection: "
        f"python3 outputs/fifa14_skip_blaze_entry_patch.py {host} restore"
    )


def apply_transaction(host: str, client: Xbdm) -> bool:
    _, tail_ok = handler_tail_state(client)
    if not tail_ok:
        raise UnexpectedSiteError(
            "Refusing apply; the LoadFUTSkipBlaze native tail is foreign"
        )
    states = read_sites(client)
    require_known_sites(states, "apply")
    _, fast_ok = fast_path_state(client)
    if not fast_ok:
        raise RuntimeError(
            "Refusing apply: the EnterFUT2 fast path is not exactly verified"
        )
    state = transaction_state(states)
    if state == "patched":
        return False
    if state != "original":
        raise RuntimeError(
            "Refusing apply from a partial owned transaction; run restore first"
        )

    patch_maybe_live = False
    try:
        for site in PATCH_SITES:
            # XBDM can commit setmem and lose its acknowledgement.  Mark the
            # transaction dirty before issuing every write.
            patch_maybe_live = True
            client.write(site.address, site.patched)
            if client.read(site.address, len(site.patched)) != site.patched:
                raise RuntimeError(
                    f"Patch verification failed at 0x{site.address:08X}"
                )
        final = read_sites(client)
        if transaction_state(final) != "patched":
            raise RuntimeError("Final two-site apply verification failed")
        _, tail_ok = handler_tail_state(client)
        if not tail_ok:
            raise RuntimeError("LoadFUTSkipBlaze tail changed during apply")
        _, fast_ok = fast_path_state(client)
        if not fast_ok:
            raise RuntimeError(
                "EnterFUT2 fast path changed or failed validation during apply"
            )
        patch_maybe_live = False
        return True
    except BaseException as apply_error:
        if patch_maybe_live:
            try:
                restore_fresh(host)
            except BaseException as rollback_error:
                raise RuntimeError(
                    f"{rollback_error}; apply failed with: {apply_error}"
                ) from apply_error
        raise


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Transactional live FIFA 14 Skip-Blaze entry patch"
    )
    parser.add_argument("host", help="Xbox 360 IP address")
    parser.add_argument("action", choices=("status", "apply", "restore"))
    args = parser.parse_args()

    validate_static()
    client = Xbdm(args.host)
    try:
        verify_fifa_build(client)
        states = read_sites(client)
        fast_state, fast_ok = fast_path_state(client)
        tail_state, tail_ok = handler_tail_state(client)
        print_status(states, fast_state, tail_state)

        if args.action == "status":
            return 0

        if args.action == "restore":
            if not tail_ok:
                raise UnexpectedSiteError(
                    "Refusing restore; the LoadFUTSkipBlaze tail is foreign"
                )
            require_known_sites(states, "restore")
            if transaction_state(states) == "original":
                print("Already restored.")
                return 0
            try:
                changed = restore_transaction(client)
            except UnexpectedSiteError:
                raise
            except BaseException as restore_error:
                print(
                    f"Primary restore failed ({restore_error}); retrying with "
                    "fresh XBDM connections.",
                    flush=True,
                )
                restore_fresh(args.host)
                changed = True
            if changed:
                print("Verified: both original Skip-Blaze entry sites restored.")
            return 0

        if not tail_ok:
            raise UnexpectedSiteError(
                "Refusing apply; the LoadFUTSkipBlaze tail is foreign"
            )
        if not fast_ok:
            raise RuntimeError(
                "Refusing apply: the EnterFUT2 fast path is not exactly verified"
            )
        root, auth = verify_cards_auth(client)
        print(f"Cards root = 0x{root:08X}")
        print(f"Cards auth = 0x{auth:08X}")
        print(f"auth vtable = 0x{CARDS_AUTH_VTABLE:08X} (verified)")

        changed = apply_transaction(args.host, client)
        if changed:
            print("Verified: transactional Skip-Blaze entry patch active.")
        else:
            print("Already patched; Cards auth and fast path verified.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrupted; any partial apply was rolled back.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1)
