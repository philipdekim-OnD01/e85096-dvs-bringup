#!/bin/sh
# Start the microscope at every boot (root, on the board):  sudo sh install.sh [SRC]
# SRC (default: this script's directory) holds scope.py scope.html scope-run.sh gpiohold.py e850-hubpwr.ko
# and e850-scope.service. Undo: systemctl disable --now e850-scope.
set -e
S=${1:-$(dirname "$0")}
D=/opt/e850-scope
systemctl stop e850-scope 2>/dev/null || true      # a running one, also the transient dev unit of that name
systemctl reset-failed e850-scope 2>/dev/null || true  # a failed transient unit stays loaded and hides the new file
install -d -m 755 $D /var/lib/e850-scope
for f in scope.py scope.html scope-run.sh gpiohold.py e850-hubpwr.ko; do install -m 644 "$S/$f" $D/; done
install -m 644 "$S/e850-scope.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now e850-scope
systemctl is-active e850-scope
