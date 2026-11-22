#!/bin/sh
# Second X server on vt8 with modesetting + glamor on e850-kms (11.86). The fbdev desktop on vt7 keeps
# running: Ctrl+Alt+F7 goes back to it, Ctrl+Alt+F8 to this one.
#   sudo sh /var/tmp/e850-kms/x-kms.sh        (e850-kms loaded first: load.sh; run it from vt7,
#                                              it restarts a running Xorg :1)
#   stop: sudo pkill -f "Xorg :1"              log: /var/tmp/e850-kms/Xorg.1.log
#   sudo STRACE=/home/philip/.local/bin/strace sh /var/tmp/e850-kms/x-kms.sh   (syscall trace)
# Clients: DISPLAY=:1 with the same Mesa environment (kenv). No X authorization: local clients only
# (-nolisten tcp); shortcut: fine for this test, use -auth before leaving it running.
set -e
D=$(cd "$(dirname "$0")" && pwd)
U=philip
L=/home/$U/gpu/mesa-kbase/lib
[ "$(id -u)" = 0 ] || { echo "run as root (sudo)"; exit 1; }
[ -e /dev/dri/card0 ] || { echo "/dev/dri/card0 missing: sh $D/load.sh"; exit 1; }
mkdir -p "$D/empty.d"
if [ -e /tmp/.X11-unix/X1 ]; then  # restart: stop the running one first
    pkill -f "Xorg :1 vt8" || true
    for i in 1 2 3 4 5; do [ -e /tmp/.X11-unix/X1 ] || break; sleep 1; done
fi
# The Mesa build must include its own libGL (-Dglx=dri): glamor finds GL functions with libepoxy,
# which otherwise opens the system glvnd libGL; that one knows nothing of this Mesa and its
# glGetString returns NULL ("your GL is broken", 11.86).
# STRACE=<strace binary>: trace the server's socket and DRM calls into $D/Xorg.1.st (debugging)
TR=
[ -z "$STRACE" ] || TR="$STRACE -f --seccomp-bpf -o $D/Xorg.1.st -e signal=none
    -e trace=recvmsg,sendmsg,writev,close,ioctl,openat,fcntl,dup,dup3,exit_group"
# -noreset: without it the server resets when its first client (xbg) exits
setsid $TR env LD_LIBRARY_PATH=$L LIBGL_DRIVERS_PATH=$L/dri LD_PRELOAD=$L/libpanfrost_kbase_drm_shim.so \
    /usr/lib/xorg/Xorg :1 vt8 -nolisten tcp -noreset -config "$D/xorg-kms.conf" -configdir "$D/empty.d" \
    -logfile "$D/Xorg.1.log" >/dev/null 2>&1 &
for i in 1 2 3 4 5 6 7 8 9 10; do [ -e /tmp/.X11-unix/X1 ] && break; sleep 1; done
[ -e /tmp/.X11-unix/X1 ] || { echo "Xorg :1 did not start; see $D/Xorg.1.log"; tail -20 "$D/Xorg.1.log"; exit 1; }
grep -E "glamor|GL|EGL|DRI3|broken" "$D/Xorg.1.log" | head -20
# the desktop of e850-feature desktop (panel, menu, terminal, Korean input), every program with the GPU Mesa
# (the kenv environment), so Firefox started from the menu or the terminal renders on the GPU (11.89)
S=/opt/e850-features/desktop/session.sh
[ -x $S ] || { echo "$S missing: e850-feature install desktop"; exit 1; }
# env -i: from the root console (e850-x) the session got root's XDG_RUNTIME_DIR (/run/user/0), where menu-cached
# cannot make its socket (empty menu, no launchers), and no LANG (11.90)
runuser -u $U -- env -i DISPLAY=:1 HOME=/home/$U USER=$U LOGNAME=$U SHELL=$(getent passwd $U | cut -d: -f7) \
    LANG=C.UTF-8 PATH=/usr/local/bin:/usr/bin:/bin LD_LIBRARY_PATH=$L LIBGL_DRIVERS_PATH=$L/dri \
    LD_PRELOAD=$L/libpanfrost_kbase_drm_shim.so setsid sh $S >/dev/null 2>&1 </dev/null &
echo "Xorg :1 on vt8 (Ctrl+Alt+F1: text console, F7: the fbdev desktop if it runs; e850-x stop)"
