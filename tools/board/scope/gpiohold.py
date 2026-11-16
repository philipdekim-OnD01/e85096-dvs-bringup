#!/usr/bin/env python3
"""gpiohold.py CHIP LINE VALUE: drive one GPIO line (GPIO chardev v1 ioctl) and hold it until killed.
The board has no libgpiod tools and no /sys/class/gpio."""
import fcntl, signal, struct, sys

GPIO_GET_LINEHANDLE_IOCTL = 0xC16CB403      # _IOWR(0xB4, 3, struct gpiohandle_request), 364 B
GPIOHANDLE_REQUEST_OUTPUT = 1 << 1

chip, line, value = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
req = bytearray(364)
struct.pack_into("<I", req, 0, line)                   # lineoffsets[0]
struct.pack_into("<I", req, 256, GPIOHANDLE_REQUEST_OUTPUT)
req[260] = value                                        # default_values[0]
req[324:334] = b"e850-scope"                  # consumer_label
struct.pack_into("<I", req, 356, 1)                     # lines
with open(f"/dev/gpiochip{chip}", "rb") as f:
    fcntl.ioctl(f, GPIO_GET_LINEHANDLE_IOCTL, req)
print(f"gpiochip{chip} line {line} = {value}, holding", flush=True)
signal.pause()
