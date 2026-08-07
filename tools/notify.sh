#!/bin/zsh
# Send a short progress note by mail, for when nobody is watching the console.
#
# Used for two things only: a milestone worth interrupting someone for, and a
# blocker that cannot be resolved without them. Everything else belongs in the
# journals and the commit log, which survive; mail does not.
set -u

TO=${NOTIFY_TO:-sallakimrane@gmail.com}
SUBJECT=${1:?usage: notify.sh "subject" [body-file]}
BODY=${2:-}

{
    if [[ -n "$BODY" && -f "$BODY" ]]; then
        cat "$BODY"
    else
        cat
    fi
    printf "\n-- FIFA 14 FUT revival, %s\n" "$(date '+%Y-%m-%d %H:%M')"
} | mail -s "$SUBJECT" "$TO"

print "notified $TO: $SUBJECT"
