#!/usr/bin/env python3
"""gpiodump.py (root): who owns gpa0-0 (cable/ID sense in mainline) and gpa3-5 (USB-A VBUS enable, active low in
mainline; the vendor ERD3830 DT has it in i2c5-bus), and the gpiolib view of banks gpa0 and gpa3. No /dev/mem here."""
import re

P = "/sys/kernel/debug/pinctrl/11850000.pinctrl-samsung-pinctrl/"
for line in open(P + "pinmux-pins"):
    if re.search(r"\((gpa0-0|gpa3-5)\)", line):
        print("mux:", line.strip())
show = False
for line in open("/sys/kernel/debug/gpio"):
    if line.startswith("gpiochip"):
        show = bool(re.search(r" gpa[03]:", line))
    if show and line.strip():
        print("gpio:", line.rstrip())
