# Milestone: inside FUT, at club creation

Screenshots:
* `runtime/screens/after404-023229.png` -- the login error that preceded it
* `runtime/screens/hub-023946.png` -- **the FUT club-creation screen**

The console is showing FIFA 14 Ultimate Team's club-creation screen:

```text
1. NOM DE CLUB :   Latina FC
2. ABREVIATION :   Lat
3. CONFIRMER NOM DU CLUB
```

with the generated crest and the retail caption "Le nom de votre club vous
permettra d'être reconnu dans l'univers de FIFA 14 Ultimate Team."

## What got here

One change from the previous milestone: `squad/list` and `squad/active` now
answer with a squad summary instead of `{"squad":[]}`.

```json
{"squad":[{"id":1,"squadName":"FIFA 14 TOTY","rating":95,"chemistry":78,"formation":"f442"}]}
```

The PC revival's own comment said why this matters -- `fcc_login2` treats an
empty GetSquads vector as an error and never issues the active-squad request.
With one squad present, it issues it, and the flow continues.

## The native trace, before and after

```text                     before      after
LoginToFUT                     1          1
FirstTimeInit                  1          1
GetIdentityData          never          4
CardsDownloaded          never          1
total recorded calls           4          9
```

`GetIdentityData` and `CardsDownloaded` had never once been called in this
project's history.

## The HTTP sequence, complete

```text
02:35:43  POST /ut/auth
02:35:43  GET  phishing/trusteddevice
02:35:43  GET  settings          PUT match/reset
02:35:43  GET  user              GET userdata
02:35:43  GET  /tutorials                       404
02:35:43  GET  store/transaction GET clientdata/tutorialpopups
02:35:47  GET  purchased/items   GET club/stats/staff
02:35:47  GET  user              GET clientdata/pileSize
02:35:47  GET  clientdata/userHubData
02:35:47  GET  clientdata/managerquest
02:35:47  GET  squad/list        GET squad/active
02:35:48  GET  eventfeed
02:35:49  GET  clubUser          GET hub
02:35:49  GET  leaderboards/options
```

Thirty requests, none unhandled.

## Next

Confirm the club name and follow the flow toward a first match. The remaining
FUT API entries still at zero are `GetUserStatsData`, `CreateClub`,
`CreateMatch`, `ServiceQuickMatch`, `ServiceCreateSession` and
`GetRandomOpponent`; `CreateClub` is the one this screen should raise.
