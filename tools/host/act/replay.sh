#!/bin/sh
# replay.sh DIR: every DIR/*.gz board recording (dvsctl POST /rec) without a .bin goes through dvsview's --replay
# (the Docker test build: the board's decoder), then one line per clip: replayed events (.bin size / 8), the
# events the board counted (.json), complete, and whether they match (docs 11.39-11.40).
set -eu
D=$(cd "$1" && pwd)
R=$(cd "$(dirname "$0")/../../linux510" && pwd)
docker run --rm -v "$R:/s:ro" -v "$D:/r" debian:12 sh -c '
	set -e
	apt-get -qq update && apt-get -qq install -y gcc zlib1g-dev >/dev/null
	gcc -O2 -Wall -DDVSVIEW_TEST -o /tmp/t /s/dvsview.c -lpthread -lm -lz
	for g in /r/*.gz; do
		b=${g%.gz}.bin
		[ -e "$b" ] || { /tmp/t --replay "$g" "$b.tmp" >/dev/null && mv "$b.tmp" "$b"; }
	done' 2>&1 | grep -v debconf || true
python3 - "$D" <<'EOF'
import glob, json, os, sys

bad = incomplete = 0
for j in sorted(glob.glob(os.path.join(sys.argv[1], '*.json'))):
    m, b = json.load(open(j)), j[:-5] + '.bin'
    n = os.path.getsize(b) // 8 if os.path.exists(b) else -1
    ok = n == m['events']
    bad += not ok
    incomplete += not m['complete']
    print('%-40s %-9s replay %9d  board %9d  %s%s' % (os.path.basename(j)[:-5], m['label'], n, m['events'],
          'ok' if ok else 'MISMATCH', '' if m['complete'] else '  (incomplete)'))
print('%d clips, %d mismatched, %d incomplete' % (len(glob.glob(os.path.join(sys.argv[1], '*.json'))), bad,
                                                   incomplete))
sys.exit(bad > 0)
EOF
