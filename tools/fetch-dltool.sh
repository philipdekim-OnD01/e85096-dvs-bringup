#!/usr/bin/env bash
set -euo pipefail
output=${1:-"$(dirname "$0")/../.work/dltool"}
git clone https://gitlab.com/LinaroLtd/e850-96/tools/dltool.git "$output"
make -C "$output"
