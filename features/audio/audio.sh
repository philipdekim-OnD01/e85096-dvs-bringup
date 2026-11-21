#!/bin/sh
# e850-audio start | route [mixer path...] (root). start = load the card once per boot, then route.
# Default route: RDMA0 -> SIFS0 -> UAIF0 -> AUD3004X DAC -> headphone (mixer_paths.xml media-headset
# without Android's BD_MIXER/RDMA3 part). Paths are names from mixer_paths.xml.
H=/opt/e850-features/audio
CARD=ExynosMadera
route() {
    [ $# -gt 0 ] || set -- route-sifs0-to-uaif0 dev-headset
    python3 $H/mixer-path.py /opt/e850-features/fw/audio/mixer_paths.xml "$@" | amixer -c $CARD -s -q
}
case $1 in
start)
    if ! grep -q "\[$CARD" /proc/asound/cards; then
        # a failed card registration leaves state that panics the next try (docs 11.77): never retry
        [ -d /sys/module/exynos3830_sound ] && { echo "exynos3830_sound is loaded but has no card: reboot"; exit 1; }
        [ "$(modinfo -F filename exynos3830_sound)" = /lib/modules/$(uname -r)/extra/exynos3830_sound.ko ] ||
            { echo "the patched exynos3830_sound is not the one modprobe picks"; exit 1; }
        modprobe exynos3830_sound
        n=0; until grep -q "\[$CARD" /proc/asound/cards; do
            n=$((n + 1)); [ $n -gt 20 ] && { echo "no card after 10 s"; exit 1; }; sleep 0.5
        done
    fi
    route;;
route) shift; route "$@";;
*) sed -n 2p "$0"; exit 1;;
esac
