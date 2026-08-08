# Milestone: the club renders, with real cards

Screenshot: `runtime/screens/inv-082035.png`

The FUT squad screen is fully drawn:

```text
Fondateur FUT      Note 88      Collectif 92      4-4-2
Hart · Sergio Ramos · Piqué · Lahm · Neuer · Kompany
Ribéry · Robben · Ibrahimović · Rooney · Van Persie
Cristiano Ronaldo · Messi · Di María · Dani Alves · Pedro
```

Cards carry their portrait art, nation flag, club badge and six stats, the
bench and reserves are populated, and the club header shows a rating and a
chemistry score. This is the verification the inventory work was missing: it
was server-side confirmation only until now.

That the art resolves is the part that matters. `resourceId` carries the asset
id in its low 24 bits under a version byte, and getting it wrong produces a
record that parses cleanly and draws a blank card -- a failure that looks like
a rendering bug rather than a data one. It resolves.

## Entering FUT takes two patches, not one, and the order matters

This is why launching Ultimate Team by hand produced an endless loader.

1. **At launch** -- `fifa14_early_local_server.py` patches the hostname
   redirect, the plaintext redirector and the native FUT-resource redirect.
   Without `--launch-title` it waits on the debug notification channel and
   patches on the modload event, so it can catch a launch started from the
   console.
2. **At the main menu, before Ultimate Team is selected** --
   `fifa14_tu3_helperfunctions_runtime_patch.py` repoints three native TU3
   continuation branches.

Applying the second one while already inside the FUT loader does nothing: the
launcher has been walked past, and the branches it would have corrected are
behind. The symptom is exact and reproducible -- the HTTP trace stops at
`accountinfo`, `/ut/auth` never follows, and `modules` shows CardsDLL was never
mapped.

## Re-entering FUT needs a full relaunch

Backing out to the FIFA main menu and selecting Ultimate Team again does not
work. The title keeps its FUT session in memory, and the server's account state
is rewritten from that session within seconds of clearing the file -- the
`OPTQ`, `OPTS` and `FirstTimeFlag` come straight back. With them present the
client treats the FUT login as already done and never issues `/ut/auth`.

The working sequence is all three together:

```text
clear runtime/local-account.json
restart the Blaze server on the cleared file
relaunch the title
reach the FIFA main menu
apply the helperFunctions patch
select Ultimate Team
```

## Known defect in this screenshot

The positions are wrong -- Messi at right back, Ronaldo at centre back.
`fut_inventory.py` assigns `preferredPosition` by slot index rather than from
the player's real position, so chemistry suffers. It does not prevent play.
