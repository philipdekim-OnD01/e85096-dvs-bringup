#!/bin/sh
# about: DVS (S5KRC1S on CN4): drivers, decoder, `dvs` in an X terminal (view window + page :8080)
# The camera modules are the e850-out builds the boards run (fimc-is with the CSI parameters of 11.24/11.28,
# is-cis-s5krc1s, csis-irqstat). The decoder (libdvsview.so, dvsview.c) and its scripts come from tools/ (bundle.sh).
# dvslive.service (the view on HDMI at boot) is installed, not enabled: systemctl enable --now dvslive.
# Without the sensor, `dvs` shows demo.gz (a dvs_record() file) if the bundle has one (docs 11.97).
set -e
ROOT=$(cd "$(dirname "$0")/.." && pwd); . "$ROOT/common.sh"; H=$DEST/dvs
SHARE=/usr/local/share/dvsview
case $1 in
install)
    ko_install fimc-is is-cis-s5krc1s csis-irqstat
    install -m 644 "$H/libdvsview.so" /usr/local/lib/
    install -m 755 "$H/ov5647-cap.py" "$H/dvslive.sh" "$H/dvs" /usr/local/bin/
    install -d "$SHARE" /var/lib/dvsview
    install -m 644 "$H/dvsctl.py" "$H/dvsctl.html" "$H/dvsact.py" "$H/dvsx11.py" "$SHARE/"
    [ ! -f "$H/dvsfont.json" ] || install -m 644 "$H/dvsfont.json" "$SHARE/"
    [ ! -f "$H/demo.gz" ] || install -m 644 "$H/demo.gz" /var/lib/dvsview/demo.gz
    unit_install "$H/dvslive.service"
    log "dvs: run 'dvs' in a terminal in X (sudo asks for the password); page on :8080";;
remove)
    unit_remove dvslive.service
    rm -f /usr/local/bin/dvs /usr/local/bin/dvslive.sh /usr/local/lib/libdvsview.so /var/lib/dvsview/demo.gz
    rm -f "$SHARE/dvsx11.py" "$SHARE/dvsctl.py" "$SHARE/dvsctl.html" "$SHARE/dvsact.py" "$SHARE/dvsfont.json"
    ko_remove fimc-is is-cis-s5krc1s csis-irqstat
    log "dvs: fimc_is stays loaded until reboot (it cannot be loaded again once removed); ov5647-cap.py is the image's";;
status)
    echo "dvslive: $(systemctl is-active dvslive 2>/dev/null)$(pgrep -f 'ov5647-cap.py --live' >/dev/null && echo ', capture running')"
    lsmod | grep -E '^(fimc_is|is_cis_s5krc1s) ' | cut -d' ' -f1 | tr '\n' ' '; echo
    [ ! -f /var/lib/dvsview/demo.gz ] || echo "demo: /var/lib/dvsview/demo.gz";;
esac
