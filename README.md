# FIFA 14 FUT Offline Revival — Xbox 360

Research toolkit and technical notes for restoring the FIFA 14 Ultimate Team
front-end on a personally owned Xbox 360 RGH/JTAG after the official services
were shut down.

> **Status — 2026-08-02:** active research prototype. A local Blaze 3 server
> now completes the title's native redirector, PreAuth, Authentication2,
> PostAuth and UserSessions path. Passive Xbox traces observed the retail game
> publish both `EVENT_BOOT_LOGIN_SUCCESS` and `EVENT_LOGIN_SUCCESS`; this is no
> longer a frontend-only skip. Selecting Ultimate Team reaches the native
> bottom-left loader and causes the expected early Blaze/OSDK request burst.
> The server now implements the concrete public BlazeSDK/Zamboni schemas found
> in that burst. A first cold Xbox bootstrap consumed the new typed responses,
> reached the normal main menu and remained connected with periodic pings. The
> FUT selection phase of that newest run has **not yet been performed**. The
> project still does not reach a usable FUT menu or persist a club.

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
- [Current local service path](#current-local-service-path)
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
| Local redirector and plaintext sockets | Confirmed through the title's real DirtySock/XNet connect path |
| Blaze PreAuth | Confirmed; supported build accepts the FIFA 14 Xbox response |
| Authentication2 | Confirmed; local OAuth code and native Blaze login complete |
| PostAuth and UserSessions | Confirmed; PostAuth, authenticated user, UserAdded and extended-data notifications are consumed |
| Native login events | Confirmed passively: boot-login success at title entry and login success after FUT selection |
| Early OSDK bootstrap | Observed; Util, Messaging, Census, Clubs, Stats, Association Lists, settings and online-pass routes requested |
| Native FUT transition | Reaches the bottom-left loader instead of immediately failing at the original EA gate |
| CardHouse/FUT service call | **Not yet observed in the newest native server session** |
| Typed OSDK response batch | Implemented, covered by tests and served successfully through a cold boot to the main menu; FUT follow-up pending |
| Earlier Cards/ION research | Preserved as evidence, including validated `FutCreateClub` assets, but no longer used as the primary entry path |
| Usable FUT/Create Club UI | **Not reached** |

The cleanest current visible state is the normal main menu followed, after one
FUT selection, by the game's native bottom-left loading indicator. In the same
run the local server remained connected and received normal 20-second pings,
while the passive title trace recorded a second native login-success
publication. The next boundary is therefore between successful account/OSDK
bootstrap and the first CardHouse request, not the original network gate.

## What is proven

### Network and service entry

- The Xbox exchanges traffic with the retired EA endpoint and receives replies
  on the surrounding Xbox/Microsoft service path.
- Packet capture alone cannot reveal the internal FUT error because the useful
  payload is proprietary and/or encrypted.
- The supported build can be redirected before title execution to a private
  LAN server without globally falsifying Xbox Live or EA availability.
- The redirect patch follows the title's DirtySock `SocketControl('xins')`
  path for only the local plaintext ports and records the real connect result
  and same-thread WSA error.
- The title accepts a complete local PreAuth/Authentication2/PostAuth sequence
  and continues to send normal pings after login.
- The local Nucleus-compatible HTTP redirect returns only an offline
  authorization code; no real credential, profile token or captured session is
  stored.

### Native login and FUT entry state

- `Authentication2.Login` returns the exact FIFA 14 session shape and emits
  authenticated-user, UserAdded and extended-data notifications.
- `Util.PostAuth` is reached and consumed in the native route; the earlier
  empty-PostAuth hypothesis is disproven.
- A passive, build-guarded trace recorded the title's own login-success
  publisher once in boot state 2 and again in state 1 after selecting FUT.
- No forced login-success event, `EnterFUT2`, `FUTStartUp`, screen load or popup
  suppression is used by the current server path.
- The natural FUT selection produces an OSDK bootstrap request burst and a
  bottom-left loader. This is application progress, but it is not proof of an
  initialized FUT session because no CardHouse command has yet followed.

### Public protocol schemas now implemented

The current response batch is derived from the public BlazeSDK types and the
public Zamboni common components rather than guessed empty replies:

| Component | Routes handled in the current bootstrap |
| --- | --- |
| Util (`9`) | config, ping, telemetry (`9:5`), PreAuth, PostAuth and persistent user settings |
| Messaging (`15`) | fetch count (`MCNT=0`) and empty message retrieval |
| Rooms/Census | native empty acknowledgements for view updates and census subscription |
| Association Lists (`25`) | typed empty `LMAP` list |
| Clubs (`11`) | component settings scalars and typed empty invitations list |
| Stats (`7`) | typed key-scope map, stat-group list and all fourteen period fields |
| OSDK Settings (`2249`) | `O_TKfilter` plus its `O_SG_TCKR` setting group |
| OSDK Online Pass (`2268`) | typed empty feature-gate `LIST` |
| Sponsored Events (`2076`) | non-empty local events URL |
| CardHouse (`2148`) | minimal new-user login and no-player response, ready for when the client reaches it |

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

The project does not yet implement a complete FUT backend, create a server-side
FUT session, persist a local club or reach the FUT home screen. The newest
typed OSDK response batch has passed 21 local tests and was consumed during a
cold console bootstrap through the main menu. Its FUT-selection phase has not
yet been observed.

At the end of the last completed Xbox observation:

- native boot login succeeded;
- native FUT-time login success was published;
- the local Blaze connection stayed alive;
- the title remained on the bottom-left loader;
- no `CardHouse.Login` (`2148:101`) arrived.

The next question is whether selecting FUT in that clean session causes the
corrected telemetry/OSDK path to issue its first CardHouse request. If it does
not, the correct next step is to passively trace the consumer of those
responses and the natural FUT-loader availability transition. It is **not** to
force another frontend event or visually hide the loader.

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
  -> local redirector / native DirtySock connection
  -> Blaze PreAuth
  -> local Nucleus code + Authentication2.Login
  -> PostAuth + UserSessions notifications
  -> native BOOT_LOGIN_SUCCESS
  -> early Util/OSDK bootstrap
  -> native LOGIN_SUCCESS after FUT selection
  -> first CardHouse/FUT session request      <-- current unresolved edge
  -> Cards/FUT account or Create Club state
  -> FUT user interface
```

Earlier experiments could drive the Cards/ION frontend as far as a queued
`FutCreateClub` view, but those experiments mixed real state with forced
navigation and could not prove a FUT session existed. They remain useful for
asset and object-layout research, not as the current success path.

## Current local service path

`server/fifa14_blaze_server.py` is the current primary experiment. It is a
small, observable Blaze 3 service with four TCP listeners, a local identity
HTTP endpoint and JSONL journaling. Unknown commands are still recorded, but
the commands already observed during FIFA 14's bootstrap now have typed
responses.

The paired launcher, `tools/fifa14_early_local_server.py`, waits for XBDM's
`default.xex` module-load event and applies all volatile changes before the
title executes. It combines:

- local endpoint redirection for Blaze and identity HTTP;
- scoped plaintext/unsecure handling for local test sockets;
- XNet startup handling required by this retail build;
- passive PostAuth, UserAdded and native login-state traces;
- strict supported-build and original-byte validation.

It does not patch a permanent title file. A title unload or reboot removes the
runtime changes.

The last completed native journal contained this broad order:

```text
PreAuth -> Auth2.Login -> PostAuth -> network/hardware info
-> account -> messages -> census -> association lists -> clubs settings
-> OSDK settings -> stats -> sponsored events -> online-pass gates
-> user settings -> entitlements -> invitations -> periodic ping
```

That order is evidence from the client, not a claim that every route is
semantically complete. Raw runtime journals stay under `runtime/` and are
ignored by Git.

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
[`docs/SAFETY.md`](docs/SAFETY.md) before any mutating step. Keep the console
and research computer on the same trusted private LAN.

### 1. Start the local server

From the repository root, substitute the Mac/research-host IP:

```bash
python3 server/fifa14_blaze_server.py \
  --advertise RESEARCH_HOST_IP \
  --journal runtime/live-blaze.jsonl \
  --account-state runtime/local-account.json
```

The default listeners are `10041`, `42124`, `42126` and `42127`; the local
identity endpoint uses `18080`. Do not expose these listeners to the Internet.

### 2. Arm before title execution

Leave FIFA 14 unloaded in XeXMenu, then run:

```bash
python3 tools/fifa14_early_local_server.py XBOX_IP \
  --local-ip RESEARCH_HOST_IP \
  --timeout 300 \
  --trace-login-flow
```

Wait until it prints `Waiting for default.xex`, then launch FIFA 14 once. The
script must observe the module-load event and finish with verified patch/trace
messages before the title is tested.

### 3. Observe title login before FUT

At the main menu, do not click FUT immediately. Confirm that the launcher trace
contains the boot login-success publication and that the server journal shows
Authentication2, PostAuth and UserSessions activity.

Read the passive journal without changing game state:

```bash
python3 tools/fifa14_ea_login_state_trace.py XBOX_IP read
```

### 4. Make one natural FUT attempt

Select Ultimate Team once. Record the exact visible result and the elapsed
time. Do not send navigation events or suppress a popup. Then read the passive
trace again and inspect only the new server journal entries.

The immediate success criterion for the next run is a first CardHouse request,
especially `2148:101`. A loader alone is evidence of progress, not completion.

### 5. End an observation cleanly

Return to the dashboard before changing hook code or endpoint behavior. Runtime
patches disappear when the title unloads. Keep `runtime/*.jsonl` locally for
analysis; the repository safety check prevents publishing them.

The full evidence-oriented procedure is documented in
[`docs/METHOD.md`](docs/METHOD.md).

## Archive and Create Club validation

`tools/archive/` contains offline builders and inspectors. They operate only on
files supplied locally by the researcher. Generated `.big`, `.bh`, `.nav` and
chunk files are ignored by Git and must not be published.

These tools document an earlier branch of the investigation. Archive-swapping
and direct navigation experiments are now quarantined from the recommended
server path. `tools/fifa14_quarantine_data1_experiments.py` can identify that
state, and `tools/fifa14_restore_original_data1.py` provides the guarded
recovery path for researcher-owned backups.

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

The current server-side code is intentionally small and inspectable:

- `tools/blaze_tdf.py` parses and builds the subset of TDF used in experiments;
- `server/fifa14_blaze_server.py` provides redirector, Blaze core and local
  identity services with typed responses and JSONL journaling;
- `tools/fifa14_early_local_server.py` applies the guarded volatile redirect
  before title execution and can arm passive login-flow traces;
- `tools/fifa14_connect_redirect.py` implements the scoped DirtySock/XNet
  connection redirect and journals native socket results;
- `tools/fifa14_ea_login_state_trace.py`,
  `tools/fifa14_postauth_dispatch_trace.py` and
  `tools/fifa14_useradded_trace.py` observe native state without publishing
  fake success events;
- `server/fifa14_xbdm_blaze_bridge.py` and
  `tools/fifa14_early_blaze_bridge.py` preserve the earlier bridge experiment,
  but are not required by the current direct local-server runbook.

Run the protocol and code-generation tests with:

```bash
python3 -m unittest discover -s tests -v
```

The current suite contains 21 tests covering redirect code generation, XNet
startup bytes, passive trace code caves, typed protocol payloads, fragmented
TCP framing and local HTTP redirect behavior. The server is still a bootstrap,
not a complete EA or FUT service clone.

## Tool map

The repository preserves both the current path and earlier diagnostic tools so
the investigation remains auditable.

| Area | Representative tools |
| --- | --- |
| XBDM files/memory | `xbdm_dirlist.py`, `xbdm_getfile.py`, `xbdm_putfile.py`, `xbdm_dump_range.py`, `xbox360_xbdm_dump.py` |
| Build and state checks | `fifa14_fut_loader_status.py`, `fifa14_cardsdll_native_init.py`, `fifa14_thread_toc_scan.py` |
| Current local server | `server/fifa14_blaze_server.py`, `fifa14_early_local_server.py`, `fifa14_connect_redirect.py`, `fifa14_xnet_startup_patch.py` |
| Passive native login traces | `fifa14_ea_login_state_trace.py`, `fifa14_postauth_dispatch_trace.py`, `fifa14_useradded_trace.py`, `fifa14_login_callback_trace.py` |
| Network/Blaze diagnostics | `fifa14_blaze_listener.py`, `fifa14_revive_session.py`, `blaze_tdf.py`, `fifa14_dirtysock_mode_state.py` |
| Guarded entry patches | `fifa14_state1_failover_slot.py`, `fifa14_skip_blaze_entry_patch.py` |
| Native navigation | `fifa14_send_nav_event.py`, `fifa14_enterfut2_action_call.py` |
| Passive traces | `fifa14_native_enterfut_trace.py`, `fifa14_blaze_frame_dispatch_trace.py`, `fifa14_connection_result_trace.py` |
| Archive research | `tools/archive/build_fifa14_createclub_patch.py`, `patch_fifa_big_entry.py`, `extract_big_navs.py` |
| Archive recovery/quarantine | `fifa14_quarantine_data1_experiments.py`, `fifa14_restore_original_data1.py` |

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
- treating a hidden popup, forced navigation event or visual loader transition
  as proof of a FUT session;
- mixing a custom `data1` archive with the local-server experiment;
- forcing events repeatedly without reading the corresponding journal;
- reusing heap addresses from a previous title session;
- renaming or replacing archives while the title is loaded.

See [`docs/SAFETY.md`](docs/SAFETY.md) for recovery procedures.

## Next research steps

The next useful experiment is one cold run of the completed response batch:

1. leave the current local server running with a fresh journal boundary;
2. arm `fifa14_early_local_server.py --trace-login-flow` while FIFA is unloaded;
3. launch the supported build from XeXMenu and verify native boot-login success;
4. select FUT once and verify the second native login-success publication;
5. determine whether the client now sends `CardHouse.Login` (`2148:101`);
6. if CardHouse is reached, implement only the next real request and the local
   club/account state it requires;
7. if CardHouse is not reached, trace the natural OSDK response consumer and
   FUT-loader availability state without altering frontend navigation.

The next milestone is a real CardHouse session transition, followed by the
native Create Club flow. A different popup, a hidden popup or a loader without
the corresponding protocol request is not counted as success.

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
