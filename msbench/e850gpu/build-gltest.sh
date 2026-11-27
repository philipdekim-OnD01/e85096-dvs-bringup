#!/bin/sh
# build-gltest.sh: gltest.c for the boards (Debian 12 arm64 container = the boards' glibc). Output: ~/e850-logs/tflite-gl/gltest
set -e
D=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$HOME/e850-logs/tflite-gl"
docker run --rm -v "$D:/s:ro" -v "$HOME/e850-logs/tflite-gl:/out" debian:12 sh -c '
apt-get -qq update >/dev/null && apt-get -qq install -y --no-install-recommends gcc libc6-dev libegl-dev libgles-dev >/dev/null
gcc -O2 -Wall -o /out/gltest /s/gltest.c -lEGL -lGLESv2 && strip /out/gltest'
