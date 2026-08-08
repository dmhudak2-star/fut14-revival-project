# Milestone: the full FUT menu, and the Play menu

Screenshots:
* `runtime/screens/futmenu-024850.png` -- the FUT main menu
* `runtime/screens/tab2-025022.png` -- the Play menu
* `runtime/screens/saison-025052.png` -- the season error that followed

## The FUT main menu

```text
ACCUEIL FUT | JOUER | ÉQUIPES | BOUTIQUE | TRANSFERTS | MON CLUB
Fondateur FUT   CRÉDITS 0   POINTS FIFA 0   BILAN 0-0-0
```

Every retail tab is present and navigable.

## The Play menu

```text
SAISON JOUEUR SOLO (DIV 0)     SAISON EN LIGNE (DIV 0)
COMPÉTITION JOUEUR SOLO        COMPÉTITION EN LIGNE
ÉQUIPE DE LA SEMAINE           MATCH SIMPLE EN LIGNE
                               AFFRONTER UN AMI EN LIGNE
```

`SAISON JOUEUR SOLO` is the offline mode, and the right target for an offline
revival: no opponent matchmaking required.

## Navigation notes for the virtual controller

Worth recording, because two presses went somewhere unintended:

* `RIGHT` on the FUT home does **not** move along the tab bar -- it moves into
  the right-hand panel, which opened CLASSEMENTS (unavailable, by design).
* `RB` / `LB` are what switch tabs.
* `B` on the FUT home raises "Voulez-vous vraiment quitter FIFA 14 Ultimate
  Team ?" -- answer `Non`, or the session ends.

## What entering the season needed

```text
02:50:44  GET /ut/game/fifa14/season/list   404
```

A 404 there surfaces as "un problème de communication est survenu avec les
serveurs FIFA Ultimate Team", and the title returns to the FIFA main menu. Now
answered `{"seasons":[]}`, the shape the PC revival carries.

## A stuck button, and how it looked

Worth writing down, because it cost an hour and looked like a game fault.

After killing a cycle mid-press, the virtual controller was left holding a
button:

```text
enabled          = 1
packet           = 2000
buttons          = 0x0010     <- START, held
remaining_frames = 0
```

`0x0010` is START. `remaining_frames` had run out, but the button state was
never cleared, so the pad reported START held down forever.

A held button produces no edge, so nothing the title watches for ever fires.
The symptom was the attract videos looping endlessly while every START and A
this project sent appeared to do nothing -- which reads exactly like "the
virtual input does not reach the video player", and sent me looking in the
wrong place. The screen navigator made it worse by matching dark video frames
against the `fut_error` signature at distance 20-40 and reporting a FUT error
on a title that had only just booted.

`xbox360_virtual_input.py <host> apply` clears the state. Check `status` before
concluding the title is stuck: `buttons` should read `0x0000` at rest.
