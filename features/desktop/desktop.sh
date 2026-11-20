#!/bin/sh
# e850-desktop start|stop|status|enable|disable -- the X desktop on HDMI (e850-feature desktop).
# start stops dvslive (it draws on the same framebuffer); stop starts it again if it is enabled.
case $1 in
start|stop) exec sudo systemctl "$1" e850-desktop;;
enable|disable) exec sudo systemctl "$1" e850-desktop;;
status)
    echo "e850-desktop $(systemctl is-active e850-desktop), dvslive $(systemctl is-active dvslive 2>/dev/null)"
    pgrep -a Xorg | cut -c1-100;;
*) sed -n 2,3p "$0"; exit 1;;
esac
