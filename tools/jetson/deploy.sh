#!/bin/sh
# Build the E850 DVS decoder and settings for the Jetson Orin Nano (Ubuntu 24.04 arm64, JetPack R39) in Docker on
# the Mac and copy them to ~/dvs-jetson on the Jetson (docs 11.108). Nothing there needs root: philip is in the
# video and i2c groups.
#   sh tools/jetson/deploy.sh philip@192.168.0.195 [--autostart]
# --autostart: GNOME starts dvs-jetson at login instead of the NRV window (its .desktop is moved to
# ~/.config/autostart-off/, so that undoes it). The NRV system service nrv-dvs-streamer still starts at boot until
# `sudo systemctl disable nrv-dvs-streamer`; dvs-jetson stops it with its own run.py stop either way.
set -eu
T=$1
R=$(cd "$(dirname "$0")/../.." && pwd)
O=$(mktemp -d)
docker run --rm --platform linux/arm64 -v "$R/tools/linux510:/s:ro" -v "$O:/o" ubuntu:24.04 sh -ec '
    apt-get -qq update >/dev/null
    DEBIAN_FRONTEND=noninteractive apt-get -qq install -y gcc pkg-config zlib1g-dev libegl-dev libgles-dev \
        libx11-dev libgtk-3-dev libcjson-dev >/dev/null 2>&1
    gcc -O2 -Wall -shared -fPIC -o /o/libdvsview.so /s/dvsview.c -lpthread -lm -lz
    gcc -O2 -Wall -o /o/dvsgl /s/dvsgl.c -lEGL -lGLESv2 -lX11
    gcc -O2 -Wall -o /o/dvspanel /s/dvspanel.c $(pkg-config --cflags --libs gtk+-3.0 libcjson) -lm'
# dvsact.py: only its STATUS for the page's /act (no classifier model on the Jetson)
cp "$R/tools/linux510/dvsctl.py" "$R/tools/linux510/dvsctl.html" "$R/tools/linux510/dvsx11.py" "$R/tools/linux510/dvsact.py" \
    "$R/tools/jetson/dvs-jetson.py" "$R/tools/jetson/dvs-jetson" "$O/"
# beside, then renamed in: a running dvs-jetson keeps its libdvsview.so and dvspanel (new files from its next start)
ssh "$T" 'rm -rf ~/dvs-jetson/.new && mkdir -p ~/dvs-jetson/.new'
scp -q "$O"/* "$T:dvs-jetson/.new/"
ssh "$T" 'cd ~/dvs-jetson && mv -f .new/* . && rmdir .new'
rm -rf "$O"
ssh "$T" 'cd ~/dvs-jetson && chmod 755 dvs-jetson dvs-jetson.py && python3 dvs-jetson.py --selftest'
if [ "${2:-}" = --autostart ]; then
    ssh "$T" 'set -e; mkdir -p ~/.config/autostart ~/.config/autostart-off
        [ -f ~/.config/autostart/nrv-dvs-native.desktop ] && mv ~/.config/autostart/nrv-dvs-native.desktop ~/.config/autostart-off/
        printf "[Desktop Entry]\nType=Application\nName=DVS (E850 decoder)\nExec=%s/dvs-jetson/dvs-jetson\nX-GNOME-Autostart-enabled=true\n" "$HOME" \
            > ~/.config/autostart/dvs-jetson.desktop
        ls ~/.config/autostart'
fi
