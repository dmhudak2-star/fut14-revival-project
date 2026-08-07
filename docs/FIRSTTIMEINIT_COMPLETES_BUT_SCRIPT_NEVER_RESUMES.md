# FirstTimeInit completes; the script side never resumes

Measured on the run of 2026-08-08 01:11, with the probe at `0x8910AAF8`
removed and `accountinfo` serving an empty persona list.

## What now works

The FUT login runs its whole server-side bootstrap, in one second:

```text
01:12:04  POST  /ut/auth
01:12:04  GET   /ut/game/fifa14/phishing/trusteddevice
01:12:05  GET   /ut/game/fifa14/settings
01:12:05  PUT   /ut/game/fifa14/match/reset
01:12:05  GET   /ut/game/fifa14/user
01:12:05  GET   /ut/game/fifa14/userdata
```

Two things changed here and both hold:

* `/ut/auth` is issued again. It had disappeared for four consecutive cycles,
  and the cause was a probe this project had installed at `0x8910AAF8` -- on
  the tail every notification id shares, walked constantly rather than once
  per login. Six cycles before it reached `LoginToFUT`; four after it did not.
* No `phishing/question` and no `phishing/validate`. Answering the trusted
  device as `{"trusted":true,"changed":false,"exists":true,"locked":false}`
  removes the security-question detour at the protocol level, so the account
  state can no longer drift into the shape that suppressed `/ut/auth`.

Not one request in the session went unanswered: zero 404s, zero unhandled
routes. The client is not waiting on this server.

## What the native side reports

```text
LoginToFUT            1   r3=0xBD9BDE2C lr=0x824112FC
FirstTimeInit         1   r3=0xBD9BDE2C lr=0x824112FC
FirstTimeInitNotify   1   r3=0xB5BEC700 r4=0x0EBDBBE4 r5=0x10  lr=0x8910A9F4
FirstTimeInitReturn   1   r3=0x00000001                        lr=0x890A06D0
GetIdentityData       never
```

`FirstTimeInit` is submitted, dispatched as operation `0xDF` to the listener
at `0xB5AA032C`, **returns 1**, and **notifies**. The native half of the login
succeeds. This retires the previous reading of this stall as a failed or
never-completed `FirstTimeInit`.

The ION boundary also shows delivery, not silence:

```text
active_receiver_handler  delivered_event=0x0027  payload_vtable=0x8200B87C
active_receiver_handler  delivered_event=0x272B  r7=0x00000001
```

`0x272B` carries `1`, matching `FirstTimeInit`'s return value.

## What still does not happen

The screen is unchanged: `fcc_login1` with its own modal `Chargement…`,
`nButtons:0`. So `InitialLoginDone` was not called -- it is the only thing
that deletes that popup -- and the ION action pipeline committed no event at
all (`3 traced, 0 original, 0 unexpected`). No navigation was sent.

`GetIdentityData` is no longer the right judge of this. `fcc_login1`'s decoded
ActionScript puts it on the returning-user path; the `NEW_USER` path runs
`ContinueToCreateClub` straight into the icebreaker. Serving an empty persona
list deliberately selects that path, so `GetIdentityData` staying at zero is
consistent with the intent, not evidence against it. The popup is the judge.

## Where this leaves the boundary

Narrower than it was. Not "the login is broken", not "FirstTimeInit fails",
not "the server is missing a route":

> the native operation completes and publishes, an ION receiver is handed the
> completion, and the ActionScript callback registered by
> `CardsLoginHelper.Init(..., OnError, InitialLoginDone)` still does not run.

`OnError` does not run either -- an error would raise
`ONL_SERVERS_DOWN`/`OSDKCards_ShowErrorPopup`, and neither is on screen. So the
callback pair is not being invoked at all, rather than invoked and failing.

The next measurement is the handle, not the name: `CardsDLL` holds no
`InitialLoginDone` or `DoInitialLoginSteps` string, so the callback is
registered by handle. `r3=0xB5BEC700` at the notify site is the object being
notified, and it is the bridge to trace next.

## Correction: FirstTimeInit is UI registration, not a login step

Disassembled from the console at `0x8909FC40`, the body the `0xDF` case calls.
It builds names and hands each to `r29->vtable+0x10` with a function pointer.
The name table at `0x8900B000` says what is being registered:

```text
futloginviewmodel            futloginwcviewmodel
futsecuritycheckpointviewmodel
futgamehubviewmodel          futsquadswcviewmodel
futauctionsearchviewmodel    futonlineseasonsviewmodel
futofflineseasonsviewmodel   futlogoutviewmodel
futNewUser
```

These are ION viewmodel registrations. `FirstTimeInit` means the first-time
initialisation of the FUT UI layer, not first-time setup of a new player. The
`0xDF` tail confirms it does nothing else:

```text
0x8910A9EC  bl 0x8909FC40      the registration body
0x8910A9F4  li r11, 0
0x8910AAEC  bl 0x8910A6A0      publish GameSceneEnable = 0
```

So it succeeds because succeeding is all it was ever going to do. Reading its
`r3=1` and its notification as a login completion was a misreading of the
name, and this document's earlier section is wrong on that point: there is no
"completion that never reaches the login helper", because this operation is
not the login.

What that leaves is narrower and plainer:

> `LoginToFUT` is called once. No FUT API call follows it, ever.

`GetIdentityData`, `CardsDownloaded`, `CreateClub` are all downstream of a
sequence that does not resume after its first step. That, and not the
notification bus, is where the next measurement belongs.
