#!/bin/sh
# X session of e850-desktop: Openbox (right click = menu: terminal, Firefox) and one terminal.
xset s off -dpms 2>/dev/null
xsetroot -solid '#24323f' 2>/dev/null
lxterminal &
exec openbox
