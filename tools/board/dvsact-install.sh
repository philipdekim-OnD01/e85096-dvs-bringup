#!/bin/sh
# dvsact-install.sh (root, from the stage dir, /var/tmp/dvsact-stage): install what is staged there for the person
# action classifier (docs 11.38), print the sha256 of each installed file. A file it replaces is kept once as
# <file>.prev-act (the state before the classifier: cp it back to undo; dvsact.py is the classifier's own, not
# backed up: rm it to undo). Running it again gives the same result.
# install(1) unlinks before it writes: the running dvslive keeps its mapped libdvsview.so until it restarts.
set -eu
cd "$(dirname "$0")"
put() {	# put FILE DIR MODE [new]: new = a file of the classifier, never backed up
	[ -e "$1" ] || return 0
	d=$2/$1
	if [ $# -lt 4 ] && [ -e "$d" ] && [ ! -e "$d.prev-act" ] && ! cmp -s "$1" "$d"; then cp -p "$d" "$d.prev-act"; fi
	install -o root -g root -m "$3" "$1" "$d"
	sha256sum "$d"
}
put libdvsview.so /usr/local/lib 755
for f in dvsctl.py dvsctl.html; do put $f /usr/local/share/dvsview 644; done
put dvsact.py /usr/local/share/dvsview 644 new
for f in ov5647-cap.py dvslive.sh; do put $f /usr/local/bin 755; done
if [ -d ort ]; then	# onnxruntime and its pure-Python dependencies, unpacked wheels
	py=/usr/local/lib/python3.11/dist-packages
	mkdir -p $py
	for e in ort/*; do rm -rf "$py/${e#ort/}"; cp -r "$e" $py/; done
	chown -R root:root $py
	echo "$(cd ort && find . -type f | sort | xargs sha256sum | sha256sum | cut -c1-64)  $py ($(ls ort | wc -l) entries, digest of the file list)"
fi
if [ -e nginx-dvs.conf ]; then	# http://<board>/ -> the DVS page (11.45); undo: rm sites-enabled/dvs, ln -s the default back
	install -o root -g root -m 644 nginx-dvs.conf /etc/nginx/sites-available/dvs
	sha256sum /etc/nginx/sites-available/dvs
	ln -sf ../sites-available/dvs /etc/nginx/sites-enabled/dvs
	rm -f /etc/nginx/sites-enabled/default
	nginx -t && systemctl reload nginx
fi
install -d -o philip -g philip /var/lib/dvsact /var/tmp/dvsrec
echo "dvsact-install: done"
