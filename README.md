# FIFA 14 FUT Offline Revival — Xbox 360 research

Research tooling and notes for restoring the FIFA 14 Ultimate Team front-end
on a personally owned Xbox 360 RGH/JTAG after the official services were shut
down.

> **Current status:** research prototype. The toolchain can redirect and inspect
> the legacy Blaze path, initialize the native Cards lifecycle, bypass the
> initial Blaze gate, enter the FUT stadium background and drive native ION
> navigation events. It **does not yet reach a usable FUT menu or complete club
> creation**. The current blocker is between the active ION flow and loading the
> first external FUT screen.

This repository contains only original research scripts and documentation. It
does not contain FIFA files, XEX modules, patched game archives, console keys,
firmware, stealth-service files or captured user sessions.

## Scope and principles

- Offline/private preservation research only.
- Tested against one Xbox 360 FIFA 14 build: timestamp `0x534C8977`, title base
  `0x82000000`, original size `0x023EC400`.
- Every live patch is build-specific and must verify original bytes before it
  writes anything.
- Read-only inspection comes first; mutating experiments are explicit and
  reversible.
- The local Blaze implementation is incomplete and is not an EA service clone.

No affiliation with or endorsement by Electronic Arts or Microsoft is implied.
Use a legally obtained copy of the game and do not connect modified research
setups to Xbox Live.

## What has been demonstrated

1. XBDM access over TCP port 730 and stable code/data dumps.
2. The title reaches the retired EA endpoint but receives no useful FUT service.
3. The native Cards root can be initialized through the title's normal state-2
   lifecycle. Its auth object is created with the expected vtable.
4. `LoadFUTSkipBlaze` can be changed transactionally to return true without
   modifying the broad global flag read by many unrelated code paths.
5. The registered `EnterFUT2` action and native ION `SendNavEvent` interface can
   be validated and invoked.
6. The FUT loader reports available, and `cards0.big`, `cards0.bh` and
   `CardsDLLzf.xex.dll` are mounted.
7. The active patched `mainFeFlow` was found to wait for `FUTStartUp` instead
   of the stock `advanceRequest` event.
8. Dispatching `FUTStartUp` was consumed by the live flow and changed the FUT
   stadium spinner into the next native black `Chargement…` dialog. A following
   direct `advance` was accepted but produced no visible transition.
9. No usable external FUT screen is instantiated yet and `pow/auth` is not
   entered.

See [docs/STATUS.md](docs/STATUS.md) for exact addresses, observations and the
open hypotheses.

## Requirements

- Xbox 360 RGH/JTAG that you own.
- FIFA 14 extracted from your own copy.
- XBDM and JRPC2 configured separately; neither is distributed here.
- Python 3.10+ on a trusted computer on the same private LAN.
- Optional: `capstone>=5` for offline PowerPC disassembly.
- A full backup of every game file you intend to change.

Use documentation addresses such as `192.0.2.25` only as placeholders. Replace
them with your Xbox's private LAN address.

## Safe first run

These commands are read-only:

```bash
python3 tools/xbdm_dirlist.py XBOX_IP 'game:\\' --contains cards
python3 tools/fifa14_fut_loader_status.py XBOX_IP
python3 tools/fifa14_cardsdll_native_init.py XBOX_IP status
python3 tools/fifa14_send_nav_event.py XBOX_IP FUTStartUp --dry-run
```

The last command validates the live navigation object and active flow chain but
does not send an event.

Before any patch, verify the exact title build:

```bash
printf 'modules\r\n' | nc -w 3 XBOX_IP 730
```

Stop if `default.xex` does not match the supported signature. Addresses in this
repository are not portable across updates, regions or repacks.

## Research workflow used here

The short version is:

1. Capture the original network failure and separate transport failure from
   application flow failure.
2. Dump `default.xex`, XAM and `powdllzf` sections from live memory.
3. Identify `EnterFUT2`, `LoadFUTSkipBlaze`, Cards root initialization and ION
   navigation through static cross-references plus guarded live probes.
4. Initialize Cards through the native state-2 callback rather than fabricating
   object fields.
5. Apply the two-site Skip-Blaze patch transactionally.
6. Enter FUT once, poll loader readiness and observe the Cards/auth journals.
7. Validate ION `SendNavEvent`; send only one event at a time and observe after
   each transition.

The full, reproducible method is in [docs/METHOD.md](docs/METHOD.md). Recovery
rules and known-dangerous experiments are in [docs/SAFETY.md](docs/SAFETY.md).

## Local Blaze harness

The server-side pieces are intentionally small and inspectable:

- `blaze_tdf.py`: TDF parsing/building helpers.
- `fifa14_blaze_listener.py`: local listener.
- `fifa14_build_redirector_response.py`: redirector reply builder.
- `fifa14_build_preauth_response.py`: synthetic pre-auth response builder.
- `fifa14_revive_session.py`: orchestration of the current experimental path.

Example:

```bash
python3 tools/fifa14_revive_session.py \
  --xbox XBOX_IP \
  --local-ip MAC_OR_PC_IP \
  --monitor-seconds 1800
```

The current harness proves routing and framing work, but it is not yet a
complete backend. Do not expose it to the Internet.

## Live patches used at the current milestone

The scripts below are **experimental**. Read their source and
[docs/SAFETY.md](docs/SAFETY.md) first.

```bash
# Wait for the native state-1 listener and relay one failure to state 2.
python3 tools/fifa14_state1_failover_slot.py XBOX_IP wait-apply

# Guarded, transactional Skip-Blaze entry patch.
python3 tools/fifa14_skip_blaze_entry_patch.py XBOX_IP apply

# Read loader and Cards state.
python3 tools/fifa14_fut_loader_status.py XBOX_IP
python3 tools/fifa14_cardsdll_native_init.py XBOX_IP status

# Validate navigation without dispatching.
python3 tools/fifa14_send_nav_event.py XBOX_IP createClub --dry-run
```

Restore volatile patches before changing direction:

```bash
python3 tools/fifa14_skip_blaze_entry_patch.py XBOX_IP restore
python3 tools/fifa14_state1_failover_slot.py XBOX_IP restore
```

Most volatile patches disappear when the title unloads, but explicit restore is
still preferred because it verifies ownership of the patched bytes.

## Archive experiments

`tools/archive/` contains builders that operate on files supplied locally by the
researcher. Generated `.big`, `.bh`, `.nav` and chunk files are ignored and must
never be committed.

The current CreateClub experiment modifies two navigation resources:

- `mainFeFlow`: route `LaunchFUT` directly to `futFlow`.
- `futLogInFlow`: choose `createClub` as its initial state and load
  `futloginviewmodel` before `FutCreateClub`.

The resulting archives are deliberately absent. Build them from your own game
files and keep the originals. The console-side swap helper refuses to run while
FIFA 14 is mapped and never overwrites a destination file.

## Repository layout

```text
tools/          XBDM, Blaze, Cards, traces and guarded live patches
tools/archive/  offline archive/chunk builders
docs/           status, method, safety and address notes
examples/       non-sensitive example configuration
scripts/        repository safety checks
```

Many scripts in `tools/` record the investigation chronologically. They are not
all part of the recommended path; consult the safety classification before use.

## Contributing

Evidence is more useful than guesses. A useful report includes the exact build
signature, original bytes, observed state before/after and a reversible cleanup
path. Never submit game files, memory dumps, console identifiers or live-service
credentials.

See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).

## License

Original source code in this repository is available under the MIT License. EA,
Microsoft and third-party assets remain the property of their respective
owners and are not covered by this license.
