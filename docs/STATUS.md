# Current research status

Last updated: 2026-08-03.

## Supported title

- Platform: Xbox 360 RGH/JTAG
- FIFA 14 `default.xex` timestamp: `0x534C8977`
- Runtime base: `0x82000000`
- Runtime size: `0x023EC400`
- `powdllzf.xex.dll` runtime base observed: `0x89700000`

All addresses below are build-specific.

## 2026-08-03 main-menu ION exit checkpoint

Two controlled runs narrowed the newest blocker beyond FutCfg and title login.
First, the local Blaze listener was unavailable until the user selected FUT.
That selection caused a new Redirector/PreAuth connection, local OAuth,
Authentication2, PostAuth, UserAdded, the native state-1 login-success
publication and a successful `/futBoot.xml` fetch. The title then reached the
same persistent bottom-left loader. This rules out reuse or timing of the
title-screen Blaze session as the current cause.

Second, a clean boot with Blaze available established a before/after passive
baseline. Before FUT selection, the connected-owner continuation counters were
`entry=0`, `observer=0`. After selection both remained zero, and none of the 16
FUT/auth/navigation completion probes fired. The server received no new route
at the click; it continued only its existing 20-second Util pings.

The live ION state during the loader was:

```text
background.name = load
background      = game/background/BackgroundFIFA.swf
screen.name     = unload
screen          = <empty>
popup.value1    = ToFe
ION subscribers = 1
CardsDLL        = not mapped
```

The retail `mainfeflow.nav` defines `mainMenu.onExit` as an unload of
`game/screens/fluxHub/FluxHub`; entering the containing `futLauncher` would
then execute `sendScreenEvent("FUTStartUp", "")`. The observed unload state,
together with the untouched `FUTStartUp` action probe, supports the following
boundary: the natural FUT selection begins the main-menu exit but its
asynchronous ION unload does not complete, so the target launcher state is not
entered. This is an inference from the live ION state plus the retail state
graph, not a synthetic frontend transition.

The next passive experiment journals all four native stages without changing
their arguments or results:

- `IONLoadViewEnqueue` at `0x82D5DCA8`;
- `ProcessAction` at `0x82D62138`;
- `ChangeState` at `0x82D61928`;
- `PreScreenComplete` at `0x82D62398`.

The goal is to identify the first absent stage in the natural unload. No
frontend event, screen completion, navigation action or CardHouse request will
be synthesized.

## 2026-08-02 native FutCfg checkpoint

The newest clean run uses the normal Blaze login and redirects only the native
`fut` resource lookup to the local HTTP endpoint `/futBoot.xml`. The retail
client fetched that document through its own resource pipeline and accepted the
minimal `FutCfg` schema.

The live adapter snapshot showed:

- parser-complete byte `+0x14D == 1`;
- parser error bytes `+0x13C`, `+0x14E` and `+0x14F` all zero;
- `futNotAvailable` byte `+0x152 == 0`;
- all four required parsed values at `+0x11C`, `+0x120`, `+0x140` and `+0x148`
  nonzero;
- native status routine `0x82782028` returning `0x1B`.

Static disassembly of `0x82782028` and the EnterFUT handler at `0x828350C8`
confirms that `0x1B` satisfies the unmodified native entry gate: required bits
0 and 4 are set while rejection bits 2 and 5 are clear. The local XML and its
parser are therefore no longer the current blocker.

After selecting FUT, the Blaze connection stayed alive and exchanged normal
Util pings, but no `CardHouse.Login` (`2148:101`) followed and
`cardsdllzf.xex.dll` was not mapped. The unresolved edge is now after native
FutCfg validation/authentication and before CardHouse bootstrap.

The object field `+0x114` must not be described as a proven WebSession or FUT
readiness flag. `0x82782078` uses it as an in-flight state (`0 -> 1`) and the
surrounding vtable/strings identify a broader OSDK/EASW download/configuration
manager. Its return to zero is compatible with a completed or terminated
operation and is not evidence that FutCfg failed.

### ZamboniUltimateTeam comparison

[`ZamboniUltimateTeam`](https://github.com/ZamboniDevelopment/ZamboniUltimateTeam)
implements a real CardHouse component for NHL/HUT. It handles the protocol
after the client initiates component `2148`: login `101`, gamer set/get
`103`/`104`, configuration `106`, deck information `301`, and the later card,
squad, store and tournament calls. For a new user it returns an empty login and
then persists the native `gamerSetInfo` creation flow.

It does not implement FIFA's `FutCfg`, DIME/resource loading or the client-side
transition that produces the first CardHouse request. Its public PPU patches
also do not provide that bootstrap. Our existing local server already has the
same initial `2148:101`, `2148:103` and `2148:104` shape, so importing the NHL
database layer before FIFA sends its first CardHouse frame would not advance the
current blocker.

The next passive target is the completion path reached from the adapter's
download/configuration vtable method `0x82798A68`, together with the native
authentication callback after `0x82782078`. The success criterion remains an
actual client-originated `2148:101`, not a frontend transition or forced event.

## Confirmed native objects and entry points

| Purpose | Address/value |
| --- | --- |
| Cards root global | `0x897C3608` |
| Cards root vtable | `0x89708AE0` |
| Cards root initialize | `0x89748A38` |
| Cards auth field | root `+0x3A08` |
| Cards auth vtable | `0x89707078` |
| Cards `pow/auth` entry | `0x897381E8` |
| FUT adapter getter | `0x827C6370` |
| FUT loader start | `0x82782078` |
| FUT loader poll | `0x827C63B0` |
| EnterFUT2 wrapper | `0x82DA6850` |
| EnterFUT2 handler | `0x828350C8` |
| LoadFUTSkipBlaze | `0x82805D30` |
| ION interface global | `0x83DA4604` |
| ION SendNavEvent | `0x82805C10` |
| `advance` string | `0x82077254` |
| `advanceRequest` string | `0x8207FBB4` |
| `createClub` string | `0x8212CE18` |
| Active patched launcher event | `FUTStartUp` (runtime byte array) |

## Latest successful state

- Native listener state 1 was observed and relayed once to native state 2.
- Cards root reached `+0x80 == 1`.
- Cards child objects at `+0x3A08`, `+0x3A0C`, `+0x3A10` and `+0x3A14`
  were created by the native lifecycle.
- The auth object had vtable `0x89707078`.
- The two-site Skip-Blaze patch was active and verified.
- Selecting FUT no longer immediately displayed the EA server popup.
- The title displayed the FUT stadium background and a persistent spinner.
- The FUT loader poll returned available (`1`).
- `game:\` contained `cards0.big`, `cards0.bh` and `CardsDLLzf.xex.dll`.
- `pow/auth` invocation count remained zero.
- A native EnterFUT2 dispatch set loader state `+0x114` to `1` and darkened the
  loading screen.
- Direct `createClub`, `advance` and `advanceRequest` navigation probes were
  accepted by the native dispatch API, but no external FUT screen appeared.
- Extraction of the archive actually mounted by the title showed one decisive
  difference from stock: `launchFUTFlow` waits for `FUTStartUp`, not
  `advanceRequest`.
- Sending `FUTStartUp` once changed the stadium spinner into a black native
  `Chargement…` dialog. The Cards root/auth objects remained valid.
- One subsequent direct `advance` was accepted but left the dialog unchanged.
- The live `loadView` consumer identified the pending screen as
  `external/ion_fut/screens/FutCreateClub`, with `ToFe` in the popup state.
- The corresponding `FutCreateClub` payload from the mounted Cards archive was
  decompressed across all three XMem/LZX chunks. It produced a valid 689,596
  byte inner BIGF package with ten entries and the expected Create Club
  ActionScript symbols. The current blocker is therefore not a simply missing
  or truncated screen asset.
- The observed ION dispatcher exposed two subscribers and an active screen
  route. A passive queue trace recorded one screen action reaching the native
  screen-executor bridge.
- The live `globalviewmodel` consumer was present but detached. Native
  registration caused front-end activity and restored the stadium background,
  but the black `Chargement…` popup remained.
- A screen-executor manager/interface lookup completed and returned a valid
  interface object. It did not by itself complete the view transition.

## Interpretation

The original failure is no longer best described as a blocked UDP endpoint.
The title can enter the local FUT front-end path and complete the Cards root
lifecycle. The unresolved boundary is the active navigation flow / external FUT
view registration and its expected completion event.

The first launcher-state mismatch and the missing-asset hypothesis are now
resolved. The next high-value step is a passive trace of the flow-action
dispatcher at entry `0x83622D20` and handler-match site `0x83622DA4`. The goal
is to distinguish “no registered handler accepted the queued action” from “a
handler accepted it but never published the external-screen completion event.”

Heap addresses observed for the dispatcher, consumers, routes and executor are
session-specific. They must be rediscovered after every title unload or console
reboot; they are evidence, not portable constants.

## Known non-solutions

- Forcing only the generic login-success event did not exit loading on PC.
- Patching the broad global byte read by `LoadFUTSkipBlaze` is unsafe because
  many unrelated code paths consume it.
- Resetting loader `+0x114` from 1 to 0 is unsafe; it can permit duplicate
  initialization and has caused freezes.
- Network packet capture alone cannot reveal proprietary/encrypted internal FUT
  errors.
- Repeated blind UI clicks do not provide new evidence.
- Replaying `GameSceneEnable(rendering=1)` did not dismiss the loading popup.
- Calling the observed unload-loading target returned normally but caused no
  visible transition.
- The presence of a valid `FutCreateClub` archive entry does not prove that its
  required view model, controller registration or completion publication is
  active.
