# Current research status

Last updated: 2026-08-01.

## Supported title

- Platform: Xbox 360 RGH/JTAG
- FIFA 14 `default.xex` timestamp: `0x534C8977`
- Runtime base: `0x82000000`
- Runtime size: `0x023EC400`
- `powdllzf.xex.dll` runtime base observed: `0x89700000`

All addresses below are build-specific.

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
