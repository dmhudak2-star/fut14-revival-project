# FIFA 14 FUT Offline Revival — Xbox 360

Research toolkit and technical notes for restoring the FIFA 14 Ultimate Team
front-end on a personally owned Xbox 360 RGH/JTAG after the official services
were shut down.

> **Status — 2026-08-01:** active research prototype. The game can be moved
> past its original EA/Blaze failure, initialize the native Cards objects,
> enter the FUT stadium, consume the patched `FUTStartUp` navigation event and
> request `external/ion_fut/screens/FutCreateClub`. The requested Create Club
> asset has been extracted and validated as complete. The project **does not
> yet reach a usable FUT menu or complete club creation**: execution currently
> stops on the black native `Chargement…` popup because the asynchronous ION
> flow does not complete the first external-screen transition.

This repository contains only original research scripts, documentation and
non-sensitive example configuration. It does **not** contain FIFA game files,
XEX modules, patched archives, console keys, profiles, stealth-service files,
packet captures, memory dumps or captured user sessions.

## Contents

- [Project goal](#project-goal)
- [Current milestone](#current-milestone)
- [What is proven](#what-is-proven)
- [What is not working yet](#what-is-not-working-yet)
- [Supported build and requirements](#supported-build-and-requirements)
- [Architecture under investigation](#architecture-under-investigation)
- [Safe first run](#safe-first-run)
- [Reproducing the current research path](#reproducing-the-current-research-path)
- [Archive and Create Club validation](#archive-and-create-club-validation)
- [Local Blaze harness](#local-blaze-harness)
- [Tool map](#tool-map)
- [Safety, recovery and reboot semantics](#safety-recovery-and-reboot-semantics)
- [Next research steps](#next-research-steps)
- [Repository policy](#repository-policy)

## Project goal

The goal is a private, offline preservation environment that lets FIFA 14 load
its original FUT user interface and create a local club without depending on
the retired EA backend. This is not an attempt to reconnect the title to the
official service, bypass ownership checks, operate a public replacement
service or reproduce Xbox Live.

The intended end state is:

1. launch an owned copy of FIFA 14 on an owned RGH/JTAG console;
2. initialize the original Cards/FUT front-end through native game code;
3. satisfy the minimum legacy protocol and local state expected by the title;
4. reach Create Club and the FUT home screen;
5. keep all generated club data and backend state local to the researcher.

No affiliation with or endorsement by Electronic Arts or Microsoft is
implied. Use a legally obtained copy of the game and keep invasive experiments
away from Xbox Live.

## Current milestone

The investigation has crossed several distinct boundaries:

| Boundary | Result |
| --- | --- |
| Xbox/XBDM access | Confirmed; stable module, memory and file inspection over TCP 730 |
| Natural EA traffic | Confirmed; the original failure is not explained by a simple local firewall block |
| Native Cards lifecycle | Confirmed; root and child objects are created through the title's state-2 path |
| Initial Blaze gate | Guarded two-site bypass confirmed on the supported build |
| FUT loader | Available; mounted Cards archives and `CardsDLLzf.xex.dll` observed |
| FUT stadium | Reached with the native spinner instead of the immediate EA popup |
| Patched launcher event | `FUTStartUp` consumed by the active flow |
| First external screen request | `FutCreateClub` is present in live `loadView` state |
| Create Club archive asset | Extracted, decompressed and validated as a complete inner BIGF package |
| ION view dispatcher | Live dispatcher, subscribers, route and queued action observed |
| Usable FUT/Create Club UI | **Not reached** |

The most advanced visible state is the FUT stadium followed by a black
`Chargement…` dialog. In one run the stadium background returned after native
`globalviewmodel` registration, but the loading popup remained. This is useful
progress: the blocker is now after asset selection and before completion of the
asynchronous view/flow transition.

## What is proven

### Network and service entry

- The Xbox exchanges traffic with the retired EA endpoint and receives replies
  on the surrounding Xbox/Microsoft service path.
- Packet capture alone cannot reveal the internal FUT error because the useful
  payload is proprietary and/or encrypted.
- Redirecting endpoints or forcing a socket result changes timeout behavior,
  but does not by itself construct the native Cards and ION state required by
  FUT.

### Native Cards/FUT state

- The Cards root can be initialized through the game's normal state-2
  lifecycle instead of fabricating object fields.
- The root reaches its initialized flag and creates the expected auth and child
  objects with validated vtables.
- `LoadFUTSkipBlaze` can be patched transactionally at its narrow entry points;
  the broad shared global flag is deliberately not modified.
- `EnterFUT2`, the FUT loader and ION navigation interfaces have been resolved
  and guarded against the supported build.
- A native `FUTStartUp` dispatch advances the patched launcher from the stadium
  spinner to the next `Chargement…` state.

### External view and archive state

- The live `loadView` consumer requests:

  ```text
  background: game/background/BackgroundFIFA.swf
  screen:     external/ion_fut/screens/FutCreateClub
  popup:      ToFe
  ```

- The `FutCreateClub` entry in the mounted Cards archive is not absent or
  trivially corrupt. Its multi-chunk XMem/LZX payload decompresses to a valid
  inner BIGF archive containing ten entries and the expected ActionScript
  symbols, including `FutCreateClub`, `CreateUser` and `CREATE_CLUB`.
- The ION dispatcher had two live subscribers in the observed session.
- The screen route existed and a queued screen action reached the native
  screen-executor bridge.
- The `globalviewmodel` consumer was present but detached. Registering it
  through the native ION API caused visible front-end activity and restored the
  stadium background, proving that view-model registration matters, but it did
  not complete the pending screen transition.

## What is not working yet

The project does not yet implement a complete Blaze/FUT backend, persist a
local club or render the Create Club screen. The immediate open question is why
the queued ION screen action does not produce the completion event expected by
the active flow.

The remaining failure is currently narrowed to one of these boundaries:

- the queued action reaches the screen executor but no registered handler
  accepts the controller/action pair;
- a handler accepts it but the external view fails before publishing its
  completion event;
- a required view model remains detached or disabled;
- the action is dispatched on the wrong front-end phase/thread;
- a prerequisite configuration/auth state is still missing even though the
  screen asset itself is valid.

The prepared passive flow-dispatch trace targets this distinction. It has not
yet produced a post-reboot observation, so no stronger claim is made here.

## Supported build and requirements

Research has been performed against one Xbox 360 build only:

| Component | Observed value |
| --- | --- |
| Platform | Xbox 360 RGH/JTAG |
| FIFA 14 `default.xex` timestamp | `0x534C8977` |
| Runtime base | `0x82000000` |
| Runtime image size | `0x023EC400` |
| `powdllzf.xex.dll` base in the observed run | `0x89700000` |

All static addresses and expected instructions are build-specific. Heap
addresses shown in logs are session-specific and must never be copied into a
new run without rediscovery.

Requirements:

- an Xbox 360 RGH/JTAG that you own;
- FIFA 14 extracted from your own copy;
- XBDM configured separately; JRPC2 is needed by tools that make native calls;
- Python 3.10+ on a trusted computer on the same private LAN;
- optional `capstone>=5` for offline PowerPC disassembly;
- full backups of every title file before an archive experiment.

Install the optional Python dependency with:

```bash
python3 -m pip install -r requirements.txt
```

Use documentation addresses such as `192.0.2.25` only as placeholders. Replace
`XBOX_IP` with the console's current private address.

## Architecture under investigation

The observed path is not a single “server available” boolean:

```text
FUT tile
  -> EnterFUT2 / initial Blaze gate
  -> native Cards root lifecycle
  -> FUT loader and mounted cards0 archives
  -> mainFeFlow / futLogInFlow navigation
  -> ION loadView consumer
  -> event-to-action route
  -> screen-executor QueueAction
  -> external FutCreateClub view
  -> completion event expected by the flow
  -> Create Club UI
```

The current prototype reaches `QueueAction` with a `FutCreateClub` request. The
next unresolved edge is between that queued action and the completion event.
This is why more endpoint redirection or repeatedly forcing the same popup
event is unlikely to solve the current blocker.

## Safe first run

Start with read-only checks:

```bash
# Confirm XBDM and record the loaded build.
printf 'modules\r\n' | nc -w 3 XBOX_IP 730

# List Cards-related files mounted by the title.
python3 tools/xbdm_dirlist.py XBOX_IP 'game:\\' --contains cards

# Inspect loader and native Cards state.
python3 tools/fifa14_fut_loader_status.py XBOX_IP
python3 tools/fifa14_cardsdll_native_init.py XBOX_IP status

# Validate the navigation path without sending an event.
python3 tools/fifa14_send_nav_event.py XBOX_IP FUTStartUp --dry-run
```

Stop immediately if `default.xex` does not match the supported signature. Do
not assume addresses are portable across title updates, regions or repacks.

## Reproducing the current research path

This is an investigation runbook, not a one-command installer. Read
[`docs/SAFETY.md`](docs/SAFETY.md) before any mutating step.

### 1. Establish the live baseline

1. Launch FIFA 14 and wait at the normal main menu.
2. Record `modules` and `modsections`.
3. Read Cards and loader status.
4. Make only one FUT attempt per observation cycle.

### 2. Let the title create Cards objects

The state-1 relay waits for the natural listener state and forwards one failure
to the native state-2 slot:

```bash
python3 tools/fifa14_state1_failover_slot.py XBOX_IP wait-apply
```

Validate the resulting objects before continuing:

```bash
python3 tools/fifa14_cardsdll_native_init.py XBOX_IP status
```

Success means the Cards root is initialized and the auth/child pointers and
vtables pass the script's guards. Do not manually zero the loader or Cards
state afterward.

### 3. Apply only the narrow Skip-Blaze patch

```bash
python3 tools/fifa14_skip_blaze_entry_patch.py XBOX_IP apply
```

The patch verifies expected bytes, applies only the supported two sites and
owns its restore path. It does not patch the broad shared global byte.

### 4. Enter FUT once and inspect

Select the FUT tile once, then collect state instead of repeatedly clicking:

```bash
python3 tools/fifa14_fut_loader_status.py XBOX_IP
python3 tools/fifa14_cardsdll_native_init.py XBOX_IP status
python3 tools/fifa14_send_nav_event.py XBOX_IP FUTStartUp --dry-run
```

If the active patched flow still advertises `FUTStartUp`, dispatch only one
event and observe the screen and journals before another action. Direct event
bursts have produced freezes and do not provide clean evidence.

### 5. Restore before changing direction

```bash
python3 tools/fifa14_skip_blaze_entry_patch.py XBOX_IP restore
python3 tools/fifa14_state1_failover_slot.py XBOX_IP restore
```

If a script times out, reconnect and run its `status` action before applying it
again. Never overwrite a site whose bytes are neither the exact original nor
the script's exact patch.

The full evidence-oriented procedure is documented in
[`docs/METHOD.md`](docs/METHOD.md).

## Archive and Create Club validation

`tools/archive/` contains offline builders and inspectors. They operate only on
files supplied locally by the researcher. Generated `.big`, `.bh`, `.nav` and
chunk files are ignored by Git and must not be published.

The current archive research established that:

- the mounted `cards0.big` and `cards0.bh` agree on the `FutCreateClub` entry;
- the compressed entry consists of three XMem/LZX chunks;
- decompression yields a valid inner BIGF package of 689,596 bytes;
- the package contains the expected Create Club assets and ActionScript names.

This rules out the simplest “FutCreateClub is missing” hypothesis. It does not
prove that every runtime dependency or view-model binding is present.

A separate restart-based experiment can build a researcher-local
`data1.big/.bh` pair with modified navigation resources:

```bash
python3 tools/archive/build_fifa14_createclub_patch.py \
  ORIGINAL_DATA1_BIG ORIGINAL_DATA1_BH \
  PATCHED_MAINFEFLOW_NAV PATCHED_FUTLOGINFLOW_NAV \
  OUTPUT_DATA1_BIG OUTPUT_DATA1_BH
```

Keep originals and generated files outside this repository. Never swap active
archives while FIFA 14 is mapped.

## Local Blaze harness

The server-side code is intentionally small and inspectable:

- `tools/blaze_tdf.py` parses and builds the subset of TDF used in experiments;
- `tools/fifa14_blaze_listener.py` provides a local listener;
- `tools/fifa14_build_redirector_response.py` builds a redirector reply;
- `tools/fifa14_build_preauth_response.py` builds a synthetic pre-auth reply;
- `tools/fifa14_revive_session.py` orchestrates the experimental path.

Example:

```bash
python3 tools/fifa14_revive_session.py \
  --xbox XBOX_IP \
  --local-ip RESEARCH_HOST_IP \
  --monitor-seconds 1800
```

The harness proves parts of routing, framing and response injection. It is not
a complete EA service clone and is not the current ION-screen fix. Bind it only
to a trusted private interface; do not expose it to the Internet.

## Tool map

The repository preserves both the current path and earlier diagnostic tools so
the investigation remains auditable.

| Area | Representative tools |
| --- | --- |
| XBDM files/memory | `xbdm_dirlist.py`, `xbdm_getfile.py`, `xbdm_putfile.py`, `xbdm_dump_range.py`, `xbox360_xbdm_dump.py` |
| Build and state checks | `fifa14_fut_loader_status.py`, `fifa14_cardsdll_native_init.py`, `fifa14_thread_toc_scan.py` |
| Network/Blaze | `fifa14_blaze_listener.py`, `fifa14_revive_session.py`, `blaze_tdf.py` |
| Guarded entry patches | `fifa14_state1_failover_slot.py`, `fifa14_skip_blaze_entry_patch.py` |
| Native navigation | `fifa14_send_nav_event.py`, `fifa14_enterfut2_action_call.py` |
| Passive traces | `fifa14_native_enterfut_trace.py`, `fifa14_blaze_frame_dispatch_trace.py`, `fifa14_connection_result_trace.py` |
| Archive research | `tools/archive/build_fifa14_createclub_patch.py`, `patch_fifa_big_entry.py`, `extract_big_navs.py` |

Not every script is part of the recommended runbook. Some are retained solely
to document disproven hypotheses. Review docstrings and the risk classes in
[`docs/SAFETY.md`](docs/SAFETY.md) before use.

## Safety, recovery and reboot semantics

- XBDM port 730 provides powerful unauthenticated-style debug access on many
  setups. Keep it on a trusted private LAN.
- Back up all title archives and launch configuration.
- Do not test invasive patches while connected to Xbox Live.
- Prefer read-only probes; use mutating scripts only when their original-byte
  and vtable guards pass.
- Do not repeatedly reapply a timed-out hook.
- A title unload or console reboot removes volatile memory patches, code caves,
  journals, heap pointers and native registrations.
- A reboot does **not** remove archive files copied to storage. Restore those
  explicitly from known backups.
- After every reboot, rediscover modules and live heap objects before resuming.

Known unsafe or misleading approaches:

- clearing loader manager `+0x114` to retry initialization;
- patching the broad shared Skip-Blaze global;
- assuming a socket timeout proves the intended game path executed;
- forcing events repeatedly without reading the corresponding journal;
- reusing heap addresses from a previous title session;
- renaming or replacing archives while the title is loaded.

See [`docs/SAFETY.md`](docs/SAFETY.md) for recovery procedures.

## Next research steps

The next useful experiment is deliberately narrow:

1. cold-launch the supported build and re-establish the guarded Cards/FUT state;
2. reproduce the black `Chargement…` state once;
3. install the passive two-site flow-dispatch trace;
4. capture whether the screen action enters the dispatcher and whether any
   registered handler matches it;
5. if a handler matches, trace its completion publication rather than forcing
   another navigation event;
6. if no handler matches, identify the missing registration or binding and
   invoke its native lifecycle on the front-end tick;
7. only after the Create Club screen renders, design the minimum local data and
   backend responses needed to create and persist an offline club.

The success criterion for the next milestone is not a different error popup.
It is an instantiated `FutCreateClub` screen or a precise native failure point
with a reproducible journal.

## Repository policy

Before every commit or push:

```bash
python3 scripts/repo_safety_check.py
python3 -m compileall -q tools scripts
```

Never commit:

- game executables, archives, navigation or media assets;
- memory dumps, packet captures, screenshots or raw session logs;
- console identifiers, profiles, keys, tokens or credentials;
- XBDM/JRPC binaries, launch configuration or stealth-service files;
- generated patched game files.

Repository layout:

```text
tools/          XBDM, Blaze, Cards, tracing and guarded live-patch tools
tools/archive/  offline archive/chunk builders and inspectors
docs/           exact status, reproducible method and safety notes
examples/       non-sensitive example configuration
scripts/        repository hygiene checks
```

Evidence is more useful than guesses. A useful contribution includes the exact
build signature, original bytes, object/vtable guards, state before and after,
and a reversible cleanup path. See [`CONTRIBUTING.md`](CONTRIBUTING.md) and
[`SECURITY.md`](SECURITY.md).

## License

Original source code in this repository is available under the MIT License.
EA, Microsoft and third-party assets remain the property of their respective
owners and are not covered by this license.
