#!/bin/sh
# about: X desktop on HDMI (Xorg fbdev, Openbox, terminal, Firefox, mpv): e850-desktop start|stop
# needs: usbhost
# Not started at boot (it costs ~300 MB RAM and the HDMI): e850-desktop start / stop / enable.
# No GPU: the Mali userspace is Android-only, so X draws with the CPU on /dev/fb0 (docs 11.80).
set -e
ROOT=$(cd "$(dirname "$0")/.." && pwd); . "$ROOT/common.sh"; H=$DEST/desktop
# kbd: chvt in e850-desktop.service; xserver-xorg-legacy: the setuid Xorg.wrap that reads Xwrapper.config (11.80)
# lxpanel..lxtask: menu, panel, file manager and small tools; fcitx5*: Korean input (11.89); zram-tools (11.90);
# wpagui, blueman: Wi-Fi and Bluetooth settings (11.90; the user joins netdev and bluetooth, wpa_supplicant
# needs ctrl_interface GROUP=netdev and update_config=1, see README)
PKGS="xserver-xorg-core xserver-xorg-legacy xserver-xorg-video-fbdev xserver-xorg-input-libinput xinit xauth kbd x11-xserver-utils
      openbox lxterminal fonts-nanum firefox-esr mpv
      lxpanel pcmanfm mousepad gpicview xarchiver galculator lxtask fcitx5 fcitx5-hangul fcitx5-frontend-gtk3 zram-tools
      wpagui blueman qt5-gtk-platformtheme"
case $1 in
install)
    # who logs in on the desktop: /etc/default/e850-desktop USER=..., else philip, else uid 1000
    [ -f /etc/default/e850-desktop ] && . /etc/default/e850-desktop
    DESKTOP_USER=${USER_NAME:-$(id -un philip 2>/dev/null || getent passwd 1000 | cut -d: -f1)}
    [ -n "$DESKTOP_USER" ] || die "no desktop user"
    echo "USER_NAME=$DESKTOP_USER" > /etc/default/e850-desktop
    # before apt: the packages bring libpam-systemd, and the next login would panic the kernel (11.80)
    install -D -m 644 "$H/e850-no-cpu-delegate.conf" /etc/systemd/system/user@.service.d/e850-no-cpu-delegate.conf
    systemctl daemon-reload
    apt_need $PKGS
    install -D -m 644 "$H/10-e850-fbdev.conf" /etc/X11/xorg.conf.d/10-e850-fbdev.conf
    [ -e /etc/X11/Xwrapper.config ] && ! grep -q e850-feature /etc/X11/Xwrapper.config &&
        cp /etc/X11/Xwrapper.config /etc/X11/Xwrapper.config.orig
    install -m 644 "$H/Xwrapper.config" /etc/X11/Xwrapper.config
    ln -sf "$H/desktop.sh" /usr/local/bin/e850-desktop
    install -D -m 644 "$H/lxpanel.panel" /etc/xdg/lxpanel/e850/panels/panel
    # compressed swap in RAM, half of the memory (lz4): two Firefoxes left ~250 MB free (11.90)
    sed -i 's/^#\?ALGO=.*/ALGO=lz4/; s/^#\?PERCENT=.*/PERCENT=50/' /etc/default/zramswap && systemctl restart zramswap
    install -D -m 644 "$H/fcitx5-profile" /etc/xdg/fcitx5/profile
    install -D -m 644 "$H/fcitx5-config" /etc/xdg/fcitx5/config
    install -m 755 "$H/firefox" /usr/local/bin/firefox
    # menu and launcher entries start Firefox through that wrapper (/usr/local/share comes first in XDG_DATA_DIRS)
    install -d /usr/local/share/applications
    sed 's|^Exec=/usr/lib/firefox-esr/firefox-esr|Exec=/usr/local/bin/firefox|' /usr/share/applications/firefox-esr.desktop \
        > /usr/local/share/applications/firefox-esr.desktop
    DESKTOP_USER=$DESKTOP_USER unit_install "$H/e850-desktop.service"
    log "desktop: user $DESKTOP_USER. 'e850-desktop start' (stops dvslive while it runs), 'e850-desktop stop'";;
remove)
    unit_remove e850-desktop.service; rm -f /usr/local/bin/e850-desktop /etc/X11/xorg.conf.d/10-e850-fbdev.conf
    rm -rf /etc/xdg/lxpanel/e850; rm -f /etc/xdg/fcitx5/profile /etc/xdg/fcitx5/config /usr/local/bin/firefox \
        /usr/local/share/applications/firefox-esr.desktop
    if [ -e /etc/X11/Xwrapper.config.orig ]; then mv /etc/X11/Xwrapper.config.orig /etc/X11/Xwrapper.config
    else rm -f /etc/X11/Xwrapper.config; fi
    # the user@ drop-in stays: libpam-systemd stays with the packages
    log "desktop: packages kept; to free the space: apt-get purge $(echo $PKGS) && apt-get autoremove";;
status)
    sh "$H/desktop.sh" status;;
esac
