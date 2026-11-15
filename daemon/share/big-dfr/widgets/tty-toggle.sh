#!/bin/sh
# Toggle: if you're on the graphical screen, jump to the first free text TTY
# (not occupied by any logged-in session -- so not the greeter's/compositor's
# either); if you're already on a text TTY, switch back to the graphical
# screen. No state file needed: the graphical session's VT number stays
# stable in loginctl no matter which TTY you're currently viewing.

occupied=$(loginctl list-sessions --no-legend 2>/dev/null \
    | awk '{print $1}' \
    | while read -r id; do loginctl show-session "$id" -p VTNr --value 2>/dev/null; done \
    | grep -v '^0$')

graphical_vt=$(echo "$occupied" | head -1)
graphical_vt=${graphical_vt:-1}

active_vt=$(cat /sys/class/tty/tty0/active 2>/dev/null | sed 's/^tty//')

if [ "$active_vt" = "$graphical_vt" ]; then
    target=""
    for vt in 2 3 4 5 6; do
        if [ "$vt" != "$graphical_vt" ] && ! echo "$occupied" | grep -qx "$vt"; then
            target="$vt"
            break
        fi
    done
    [ -n "$target" ] && chvt "$target"
else
    chvt "$graphical_vt"
fi
