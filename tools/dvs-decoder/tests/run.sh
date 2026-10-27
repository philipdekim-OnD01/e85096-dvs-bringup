#!/usr/bin/env bash
# End-to-end check: synthesize a capture, decode it, verify counts and CSV.
set -euo pipefail
bin=${1:?usage: run.sh BUILD_DIR}
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

"$bin/test_frame2b"
"$bin/dvs-synth" -n 10 -o "$work/synth.dvsc" >/dev/null
out=$("$bin/dvs-decode" -q -o "$work/events.csv" --pgm "$work" "$work/synth.dvsc")
echo "$out"

# 10 frames x 720 rows ON, 9 frames x 720 rows OFF; seq 5 is skipped.
expect="summary: frames=10 seq_gaps=1 dropped=1 csis_err=0 dma_err=0 short=0 events=13680 on=7200 off=6480 reserved=0 fps=1000.0"
grep -qxF "$expect" <<<"$out" || { echo "unexpected summary, want: $expect" >&2; exit 1; }

[[ $(wc -l <"$work/events.csv") -eq 13681 ]] || { echo "bad CSV row count" >&2; exit 1; }
[[ $(sed -n 2p "$work/events.csv") == "0,0,10,0,1" ]] || { echo "bad first event" >&2; exit 1; }
# Frame 1 (seq 1, sensor ts 1000): OFF at x=10 precedes ON at x=11 per row.
grep -qxF "1,1000,10,0,0" "$work/events.csv" || { echo "missing OFF event" >&2; exit 1; }
grep -qxF "1,1000,11,0,1" "$work/events.csv" || { echo "missing ON event" >&2; exit 1; }
[[ $(ls "$work"/frame_*.pgm | wc -l) -eq 10 ]] || { echo "missing PGM output" >&2; exit 1; }

# A corrupt record must be reported, not silently accepted.
head -c 200000 "$work/synth.dvsc" >"$work/trunc.dvsc"
if "$bin/dvs-decode" -q "$work/trunc.dvsc" >/dev/null 2>&1; then
    echo "truncated capture was not rejected" >&2; exit 1
fi
echo "run.sh: all checks passed"
