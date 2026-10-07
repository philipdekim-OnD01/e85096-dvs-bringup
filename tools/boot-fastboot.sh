#!/usr/bin/env bash
# RAM-boot the upstream dltool/LK payload; does not flash eMMC.
set -euo pipefail
source_dir=${1:?Usage: boot-fastboot.sh /path/to/upstream/dltool}
source_dir=$(realpath "$source_dir")
[[ -x "$source_dir/dltool" ]] || { echo "Build upstream dltool first: make -C $source_dir" >&2; exit 1; }
command -v fastboot >/dev/null
printf 'Power off, set SW1 to 1000, connect the USB device port, then hold SW2 for 1 second.\n'
read -r -s -n 1 -p 'Press any key to continue... '
printf '\n'
(cd "$source_dir" && ./dltool)
for ((attempt=0; attempt<30; attempt++)); do
    mapfile -t devices < <(fastboot devices | awk '$2 == "fastboot" {print $1}')
    if (( ${#devices[@]} == 1 )); then
        printf 'LK fastboot ready: %s\n' "${devices[0]}"
        exit 0
    fi
    if (( ${#devices[@]} > 1 )); then
        echo 'Disconnect other fastboot devices.' >&2; exit 1
    fi
    sleep 1
done
echo 'Timed out waiting for LK fastboot.' >&2
exit 1
