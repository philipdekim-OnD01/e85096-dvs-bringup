#!/bin/sh
# X session of e850-desktop (:0) and of the GPU X (:1, modules/e850-kms/x-kms.sh): Openbox, a panel (lxpanel:
# menu, launchers, taskbar, volume, tray, clock), Bluetooth (blueman) and Wi-Fi (wpa_gui) icons, a terminal and
# Korean input (fcitx5-hangul: Ctrl+Space, Hangul or right Alt). Each session gets its own D-Bus session bus:
# fcitx5 runs once per bus, and :0 and :1 can run at the same time as the same user (11.89, 11.90).
[ -n "$E850_SESSION_BUS" ] || E850_SESSION_BUS=1 exec dbus-run-session -- sh "$0" "$@"
export GTK_IM_MODULE=fcitx QT_IM_MODULE=fcitx XMODIFIERS=@im=fcitx SDL_IM_MODULE=fcitx XDG_MENU_PREFIX=lxde-
# Qt programs (wpa_gui) take the GTK icon theme: Adwaita has the Wi-Fi signal icons
export QT_QPA_PLATFORMTHEME=gtk3
xset s off -dpms 2>/dev/null
"$HOME/.local/bin/xbg" "$HOME/.local/share/wallpaper.bgrx" 2>/dev/null || xsetroot -solid '#24323f' 2>/dev/null
fcitx5 -d 2>/dev/null
lxpanel --profile e850 &
blueman-applet 2>/dev/null &
wpa_gui -t -q 2>/dev/null &   # Wi-Fi icon with the signal; click: networks (the user is in netdev)
lxterminal &
exec openbox
