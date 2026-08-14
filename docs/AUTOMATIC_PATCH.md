# The patch applies itself

Starting FIFA 14 used to take a person: launch the title, walk it to the main
menu on the pad, say so, and then run `tools/fut.sh --patch`. Nobody can ship
that.

## The signal was never the menu

The TU3 helperFunctions patch has to land **before Ultimate Team is entered**.
Applied from inside the FUT loader it does nothing -- the launcher has already
been walked past, the trace stops at accountinfo, `/ut/auth` never follows and
CardsDLL is never mapped.

Before is the whole requirement. The main menu was a proxy for it, and a
person was the only thing watching for the main menu.

`fifa14_tu3_helperfunctions_runtime_patch.py` never needed that proxy. It
already polls until the APT appears in memory, validates the header, the
length and all three branch contexts before writing a byte, and reports
"already patched" if it runs twice. It waits for the thing itself.

Measured against a title that had just been launched and left sitting on its
intro -- nobody at the pad, no menu reached:

    Found in the hinted window around 0xBDD78B00.
    TU3 helperFunctions APT 0xBDD78B00: original
    Verified: three native TU3 continuation branches patched.

Five seconds.

## What actually made it unsafe to start early

The fallback sweep. It reads the heap in 8 MB chunks, and running it against a
title still on the splash once froze this console hard enough to drop it off
the network. That single event is why a human was inserted into the loop.

`--hint-only` never runs it. The scan looks only at the 4 MB window around the
last address the APT was really found at, which
`runtime/helperfunctions-apt.json` remembers from run to run -- so the hint
tracks the console's heap instead of going stale. That window is cheap enough
to poll from the moment the title starts.

`await_patch` polls it every two seconds for `HINT_GRACE` seconds (150 by
default) and only then allows the full sweep, by which time the title is long
past the splash. In practice the sweep never runs.

## The screen navigator is gone from the flow

The default path used to drive the pad to the main menu with
`fifa14_screen_navigator.py` so the patch had something to land on. It needs
the framebuffer, XBDM screenshot capture is not dependable on this console,
and a navigator that timed out left the sweep running against the splash --
the exact failure above. Waiting for the APT replaces all of it.

## What a run looks like now

    == serveur
    == lancement du titre
       Verified: retail hostnames preserved, Blaze connect=192.168.1.40, ...
    == patch helperFunctions (automatique)
       Verified: three native TU3 continuation branches patched
    PRÊT. Rien à faire, tout est en place.

27 seconds from a console sitting on the dashboard, with nothing typed in the
middle of it.
