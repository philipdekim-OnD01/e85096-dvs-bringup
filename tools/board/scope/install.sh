#!/bin/sh
# Start the microscope at every boot (root, on the board):  sudo sh install.sh [SRC]
# SRC (default: this script's directory) holds scope.py scope_rec.py scope.html scope-run.sh gpiohold.py e850-hubpwr.ko
# and e850-scope.service, and py/ = the OpenCV wheel unpacked (object analysis; without it the viewer runs, analysis off):
#   python3 -m zipfile -e opencv_python_headless-4.11.0.86-cp37-abi3-manylinux_2_17_aarch64.manylinux2014_aarch64.whl py
# (PyPI sha256 6efabcaa9df731f29e5ea9051776715b1bdd1845d7c9530065c7951d2a2899eb, works with Debian 12's numpy 1.24).
# Photos and the record (scope.db) stay in /var/lib/e850-scope. Undo: systemctl disable --now e850-scope.
set -e
S=${1:-$(dirname "$0")}
D=/opt/e850-scope
systemctl stop e850-scope 2>/dev/null || true      # a running one, also the transient dev unit of that name
systemctl reset-failed e850-scope 2>/dev/null || true  # a failed transient unit stays loaded and hides the new file
install -d -m 755 $D /var/lib/e850-scope
for f in scope.py scope_rec.py scope.html scope-run.sh gpiohold.py e850-hubpwr.ko; do install -m 644 "$S/$f" $D/; done
if [ -d "$S/py" ]; then
    rm -rf $D/py.new && cp -r "$S/py" $D/py.new && chown -R root:root $D/py.new && chmod -R go-w $D/py.new
    rm -rf $D/py && mv $D/py.new $D/py
fi
install -m 644 "$S/e850-scope.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now e850-scope
systemctl is-active e850-scope
