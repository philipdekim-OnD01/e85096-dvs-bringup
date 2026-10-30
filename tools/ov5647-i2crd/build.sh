#!/bin/sh
# Build i2crd (static aarch64 Linux ELF) on macOS with Apple clang only.
# No cross toolchain or linker is needed: clang emits an ELF object, link.py wraps its .text.
set -e
cd "$(dirname "$0")"
clang -target aarch64-linux-gnu -O2 -ffreestanding -fno-builtin -fno-stack-protector \
      -mgeneral-regs-only -fno-pic -fno-asynchronous-unwind-tables -c i2crd.c -o i2crd.o
python3 link.py i2crd.o i2crd
file i2crd
