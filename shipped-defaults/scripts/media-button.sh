#!/bin/sh
# Media icon in the control strip: only shows while a player is running (Playing/Paused).
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=/run/user/$(id -u)/bus}"
case "$(playerctl status 2>/dev/null)" in
  Playing|Paused) echo '{"icon":"play_pause"}' ;;
  *) echo "" ;;
esac
