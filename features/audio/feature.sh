#!/bin/sh
# about: ABOX + AUD3004X sound card (3.5 mm headphone out), ALSA default = dmix on RDMA0
# Firmware: Linaro A13 BSP files (host/fetch-fw.py). The machine driver is our patched build
# (modules/exynos3830-sound, docs 11.77): the stock one fails to register the card on this DT.
set -e
ROOT=$(cd "$(dirname "$0")/.." && pwd); . "$ROOT/common.sh"; H=$DEST/audio
FW="calliope_sram.bin calliope_dram.bin calliope_sram_2.bin calliope_dram_2.bin abox_tplg.bin"
case $1 in
install)
    ko_install exynos3830_sound
    for f in $FW; do install -m 644 "$ROOT/fw/audio/$f" /lib/firmware/; done
    apt_need alsa-utils; users_to_group audio
    [ -e /etc/asound.conf ] && ! grep -q e850-feature /etc/asound.conf && cp /etc/asound.conf /etc/asound.conf.orig
    install -m 644 "$H/asound.conf" /etc/asound.conf
    ln -sf "$H/audio.sh" /usr/local/sbin/e850-audio
    unit_install "$H/e850-audio.service" enable
    systemctl start e850-audio;;
remove)
    unit_remove e850-audio.service; rm -f /usr/local/sbin/e850-audio
    if [ -e /etc/asound.conf.orig ]; then mv /etc/asound.conf.orig /etc/asound.conf; else rm -f /etc/asound.conf; fi
    for f in $FW; do rm -f /lib/firmware/$f; done
    ko_remove exynos3830_sound
    log "audio: the card stays until reboot (the driver cannot be loaded twice in one boot)";;
status)
    cat /proc/asound/cards; systemctl is-active e850-audio || true;;
esac
