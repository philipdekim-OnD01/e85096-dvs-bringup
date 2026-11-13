#!/bin/sh
# pll-abab.sh: A/B/A/B, A = 0x75 (experimental_00_75_00), B = 00/8B/B0 (recommended_00_8b_b0), receiver at the DT
# speed (1600), 10 min each with 20 raw VC0 buffers before and after, 128-line decoding (docs 11.48,
# DVS-PLL-TESTS.md). plltest.py puts setfile and the DT speed back after every run.
# Root: systemd-run --unit=pllabab sh /var/tmp/pll-abab.sh. Results: /var/tmp/plltest/10-abab-*/summary.json
for a in "experimental_00_75_00 1-A" "recommended_00_8b_b0 2-B" "experimental_00_75_00 3-A" "recommended_00_8b_b0 4-B"; do
    set -- $a
    python3 -I /var/tmp/plltest.py "$1" 0 10 --sample 20 --out "/var/tmp/plltest/10-abab-$2-$1"
done
