# Current research status

Last updated: 2026-08-10.

## 2026-08-10 the cups have a shape, and the season document had invented names

`Compétition Joueur Solo` was the last FUT mode still served empty, after a
generated catalogue froze the title and the list was emptied until its fields
could come from the binary. They now do: CardsDLL's `.rdata` carries its own
sorted JSON member-name table, and every member of the native tournament
document sits in it. The freeze reduces to one line — `rounds` is an array of
`{id, difficulty, rewardMultiplier, coins}` records and was served as a count.

The same table was then turned on the season document, which had never been
checked: seven of its members, plus a `relegated`/`promoted` boolean pair,
appear nowhere in it. A parser cannot read a member it has no name for, so the
season screen had been reading constructor defaults throughout. Both documents
now use only names the module carries.

Full reasoning, routes and the parts that remain choices rather than findings:
`docs/TOURNAMENTS.md`.

Not yet confirmed on the console — the mode has not been opened since the
change.

## 2026-08-06 the client walks its own path once the configuration is served

The FUT settings belong in `OSDK_CORE`, which this server returned empty. Two
keys there decide whether CardsDLL ever speaks: without
`OSDK_EASW_ALLOWED_LOCALES` the native gate falls back to `----` and refuses to
build its authentication request at all, and without `OSDK_EASW_AUTH_URL` it has
nowhere to send it. The allow-list echoes the four-byte locale the console
reports in PreAuth -- this one sends `LANG` `0x66724652`, literally `frFR`.

That reframes the earlier approach. Writing `EASW-Session` and `EASW-Token`
straight into the JSON builder's registers satisfied one constructor and made
`Authentication` return 1, but the session behind them never existed, which fits
every symptom up to that point: a gate opens and the step after it never fires.

With the configuration served the console issued routes never seen before:

```text
POST /authentication360            signed form, gamertag/xuid/locale/skuid
POST /ut/auth                      CardsDLL's own auth route
GET  /ut/game/fifa14/phishing/trusteddevice
GET  /ut/game/fifa14/phishing/question      the security question
POST /ut/game/fifa14/phishing/validate      accepted
GET  /ut/game/fifa14/settings
GET  /fut/loc/XBox360/leaderboards.FRE_FR.xml
PUT  /ut/game/fifa14/match/reset
GET  /ut/game/fifa14/user
GET  /ut/game/fifa14/userdata
GET  /tutorials
```

`/pow/auth` disappeared from the flow entirely. The retail error dialog is gone
and the title now sits on the native `Chargement...` popup -- step 2 of the
objective -- with no unhandled route behind it.

What it waits on next is the completion of `FirstTimeInit`'s operation `0xDF`.
The submit trace resolves that call at runtime:

```text
submissions = 1
     0  request=0xB630A120 operation=0xDF submit=0x83593B28
          request vtable = 0x8218A330
```

Both the submit method and the request vtable live in `default.xex`, not in
CardsDLL, so the submission crosses into the title's own service layer. After it
the console sends only Blaze Util pings: no component 2148 frame, no further
HTTP. Hooking `0x83593B28` is the next measurement.

## 2026-08-06 the FUT bootstrap stops after FirstTimeInit

Arming every traced operation of the CardsDLL FUT API on the module's `modload`
notification, then selecting FUT, gives the whole sequence in one run:

```text
LoginToFUT             1 call(s)
     0  r3=0xBD9DF12C r4=0x00000000 r5=0xBD9DF108 lr=0x824112FC
FirstTimeInit          1 call(s)
     0  r3=0xBD9DF12C r4=0x00000000 r5=0xBD9DF140 lr=0x824112FC
GetIdentityData        never called
GetUserStatsData       never called
CardsDownloaded        never called
CreateClub             never called
CreateMatch            never called
ServiceQuickMatch      never called
ServiceCreateSession   never called
GetRandomOpponent      never called
FinalShutdown          never called
FUT service object = 0xB5AA7018
```

The front-end calls exactly two operations, both from `0x824112FC` in
`default.xex`, and then stops. `GetIdentityData` — the next step of the retail
first-use flow — is never reached, and the retail error dialog follows.

`FirstTimeInit` resolves to vtable slot `+0x08`, `0x8908D3D0`. Unlike
`LoginToFUT`, which is purely local, it issues a request:

```text
0x8908D3E0  bl 0x89185500      ; manager
0x8908D3EC  bl 0x8908CA10      ; obtain the request object
0x8908D3F0  li r4, 0xDF        ; operation id 223
0x8908D404  vtable+0x4C(r31, 0xDF)
0x8908D418  vtable+0x04(r31)   ; release
```

So the boundary is now a single named request: operation `0xDF` submitted
through slot `+0x4C` of the object `0x8908CA10` returns. Nothing follows it, and
no HTTP route or Blaze frame corresponding to it reaches the local server.

Note the id space: the operation-name table at `0x890A6980` covers ids 0..81, so
`0xDF` belongs to a different enumeration and still needs a name.

Two practical notes from the same session. The TU3 `helperFunctions` APT moves
with the heap: after signing in a different profile it left its usual
`0xBDD7xxxx` neighbourhood and no ordering heuristic found it quickly, while
restoring the previous profile put it straight back. And Xbox Live connectivity
is not required by any of this — the local server serves every route — but a
console left retrying a failed Live sign-in blocks the dashboard before the
title can start.

## 2026-08-06 FUT security first-use checkpoint

The native Xbox Cards Authentication boundary is closed. A controlled run with
the deterministic entry hook at `0x897381E8` produced, in order, a real
`POST /pow/auth` on the local server, the client's own
`Easw-Session-Data-Nucleus-Id`, and a real
`GET /ut/game/fifa14/user/accountinfo`. The hook reports
`invocation_count = 1` with both REST URL slots local.

The request body recovered from that exchange is the exact Xbox contract:

```json
{"isReadOnly":false,"sku":"FFA14XBX","clientVersion":1,
 "nuc":2535469248587161,"nucleusPersonaId":0,
 "nucleusPersonaDisplayName":"Imskobogota6z","locale":"fr-FR",
 "method":"cas","priorityLevel":5,
 "identification":{"EASW-Session":"...","EASW-Token":"..."}}
```

The `/ut/auth` and `accountinfo` response schemas match the Loopizzle PC
reference exactly, so the remaining failure is not a JSON contract.

Selecting FUT then raises the retail connection-error popup with no server
traffic at all. The localization-key ring trace names the failing step:

```text
TXT_EASFC_SERVER_ERROR        (powdllzf.xex.dll string at 0x8970E780)
FUT_SECURITY_TITLE
FUT_SECURITY_TIP
FUT_SECURITY_CHOOSE_QUESTION
Unknown_FCC_Error
```

The client therefore enters the real FUT first-use path and builds the security
question screen before failing on an FCC error code it has no label for. The
correlated native state is: FUT loader `state = 1` and `available = 1`,
`IONUnloadViewEnqueue` and `IONActionDispatch28` at 8 invocations each,
`ViewManagerEnterFlow` and `ScreenFlowConstructor` still at 0, and no Blaze
component 2148 frame ever sent.

Disassembling `CardsDLLzf.xex.dll` — mapped at `0x89000000` only once FUT is
entered — resolved that popup exactly. It is emitted by `0x8909F448`, which has
two callers: the message dispatcher `0x8911A998`, whose `0x65` case (CardHouse
`Login`, component 2148 command 101) branches straight to it, and the FUT tick
`0x8909FA50`, which decrements a watchdog at `this+0x48` and fires when it
reaches zero.

A trace armed on the `CardsDLLzf.xex.dll` modload notification, so it captures
the very first FUT attempt, reports `handler invocations = 0`. The dispatcher is
never reached: no `0x65` result arrives, neither success nor failure.

Attributing the dialog to the watchdog instead is *not* established. Both
constructors initialise `this+0x48` to `-1`, which the tick treats as disabled,
and no code that arms a positive countdown has been found. More importantly the
localization trace resolves `Unknown_FCC_Error` from a heap address
(`0xBE0EA9A4`) rather than from the CardsDLL rdata string at `0x8900B16C`, so
the dialog may come from the FUT front-end resolving the same key without
`0x8909F448` running at all. The dispatch trace now also records that routine,
so the next reproduction settles it.

What is solid is that the result dispatcher is not reached, the console opens a
single Blaze connection, sends no component 2148 frame, issues no HTTP after
`accountinfo`, requests no unmodelled route, and the connect hook counts no
additional connection.

## 2026-08-06 CardsDLL FUT API table

The initializer at `0x89107480` builds CardsDLL's FUT surface as 12-byte
records holding a handler and the operation name. Reconstructed statically from
the module image it yields 75 named operations, including the whole path a
first match needs:

| Operation | Handler |
| --- | --- |
| `LoginToFUT` | `0x89105D18` |
| `FirstTimeInit` | `0x89105D50` |
| `GetIdentityData` | `0x89105EA0` |
| `CardsDownloaded` | `0x89105E68` |
| `CreateClub` | `0x891061E0` |
| `CreateMatch` | `0x89106218` |
| `ServiceQuickMatch` | `0x89106130` |
| `MatchReady` | `0x89226270` |

Tracing `LoginToFUT` from the CardsDLL modload notification shows the front-end
does call it, once, from `0x824112FC` in `default.xex`:

```text
LoginToFUT invocations = 1
     0  r3=0xBD9DE7B0 r4=0x00000000 r5=0xBD9DE744 lr=0x824112FC
FUT service object = 0xB5AA3018
```

The live service object is `0xB5AA3018` with vtable `0x89008E90`, whose slots
are:

```text
+0x00 0x8908F5E0   +0x10 0x8908B540   +0x20 0x8908F630   +0x30 0x8908D5A0
+0x04 0x8908D350   +0x14 0x8908B518   +0x24 0x8908ED78   +0x34 0x8908FD28
+0x08 0x8908D3D0   +0x18 0x8908D438   +0x28 0x8908B568   +0x38 0x89090270
+0x0C 0x8908B4F0   +0x1C 0x8908D4A8   +0x2C 0x8908D520   +0x3C 0x890906B0
```

`LoginToFUT` dispatches through slot `+0x04`, `0x8908D350`. That method is
synchronous: it builds local state through `0x8909EA30`, `0x8909DD90` and
`0x8909EBD8` — the routines surrounding the FUT manager constructor — then emits
a `_global` / `LoginToFUT` telemetry record. It waits for no server reply, so
the online step that should follow is a different entry in the same table.

One further live observation separates two distinct end states. Applying the TU3
`helperFunctions` patch before selecting FUT leads to the error dialog; when the
patch landed only after the selection, the title instead sat on the FUT stadium
loader with a persistent spinner and `CardsDLLzf.xex.dll` was never mapped. The
patch is therefore what carries the flow far enough to fail.

Two hypotheses were tested and eliminated with live evidence: an incomplete
response schema, and a persona-name mismatch between the Blaze session and the
FUT identity. The mismatch was real and is fixed — Blaze now advertises the same
`DSNM` the client presents to FUT — but the popup is unchanged.

Transport note: the retail Redirector negotiates OldProtoSSL, which Python's
OpenSSL rejects with `WRONG_VERSION_NUMBER`. The plaintext redirector profile
(`standardInsecure_v3`, XNet `global-nosecure`) is the transport that reaches a
local login end to end.

## Earlier status

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
