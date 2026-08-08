# Milestone: the FUT login completes

Screenshot: `runtime/screens/after404-023229.png`

For two days every session ended the same way: the modal `Chargement…` popup
on `fcc_login1`, forever, with `InitialLoginDone` and `OnError` both silent.

## What broke it

The tutorial feed. Every recorded session, however deep, ended on the same
tail -- `userdata`, then `/tutorials`, then nothing at all. The server was
answering that request with an empty `<MESSAGES>` document whose shape had
never been checked against CardsDLL's parser.

CardsDLL names `RetrieveShouldShowTutorial` and
`RetrieveShouldShowTutorialComplete` as a pair, so the retrieval is
asynchronous and the login waits on its completion. The empty document was
accepted at the HTTP level and never completed at the parser level, so
`DoInitialLoginSteps` waited on a callback that could not arrive.

Turning the step off via `FUT/DISABLE_TUTORIALS` and `FUT/FORCE_TUTORIALS`
did **not** work -- the client asks for the feed either way, so those keys do
not gate it. What worked was answering **404**, the one reply whose meaning is
unambiguous.

## What immediately followed

```text
02:31:32  GET  /tutorials                              404
02:31:32  GET  /ut/v2/game/fifa14/store/transaction
02:31:32  GET  /ut/game/fifa14/clientdata/tutorialpopups
02:31:36  GET  /ut/game/fifa14/purchased/items
02:31:36  GET  /ut/game/fifa14/club/stats/staff
02:31:36  GET  /ut/game/fifa14/user
02:31:36  GET  /ut/game/fifa14/clientdata/pileSize
02:31:36  GET  /ut/game/fifa14/clientdata/userHubData
02:31:36  GET  /ut/game/fifa14/clientdata/managerquest
02:31:36  GET  /ut/game/fifa14/squad/list
```

Seventeen requests, none unhandled. That is the PC revival's post-login
sequence, in its order.

## What is on screen now

The `Chargement…` popup is gone, replaced by the FUT stadium background, and a
real FUT dialog with an OK button:

> Nous sommes désolés, mais une erreur s'est produite lors de la connexion à
> FIFA 14 Ultimate Team. Vous allez être réorienté vers le menu principal.

This is `OnError` firing, and `OnError` had never fired before. It is not a
regression: it is proof the login helper ran all of its steps and returned.
The screen changed for the first time in two days.

## The next wall, already named by the reference

`squad/list` was answered `{"squad":[]}`. The PC revival's own comment:

> the `fcc_login2` ActionScript deliberately treats an empty GetSquads vector
> as an error and never issues the active-squad request

That is this dialog. Fixed by serving one squad summary on `squad/list` and
`squad/active`, as the reference does.
