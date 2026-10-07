# DVS I2C/RESET bring-up module

This snapshot registers an I2C driver for `nrv,rc1s` at 7-bit address 0x10.
It requests the active-low GPC0[2] RESET GPIO, performs a reset, and tests
register 0x303B using I2C write/readback. Failures retry with exponential
backoff. It does not implement CSI, PHY, DMA, streaming, or a userspace ioctl ABI.
The PAD_CONTROL test changes sensor settings; use this on a bring-up device.

Build against a prepared Linux 7.2.8 arm64 tree and matching configuration:

```sh
make -C modules/dvs KDIR=/absolute/path/to/kernel-build \
  KERNEL_SOURCE=/absolute/path/to/kernel-source \
  CROSS_COMPILE=/absolute/path/to/aarch64-none-linux-gnu-
```

Install only on the matching kernel. With the DTS applied, `insmod dvs.ko`
starts the reset/I2C test. Use `dmesg` to inspect the result. Do not load this
snapshot alongside another driver owning the same sensor/reset GPIO.
The original board showed 0xAA reading back as 0x2A and 0x55 as 0x55.
A ready message validates that transaction, not chip identity or MIPI reception.

The existing main.c retains the author's experimental probe/work flow.

Validated on 2026-10-07 after flashing the shared board DTB: the client was
`0-0010`, the module bound as `dvs`, and the worker reported
`DVS ready: I2C response verified (0x303B=0x55)`. This is the Linux adapter
number, not the HSI2C controller suffix. Determine it from the running DT.

## Device Tree

`dts/` contains a Linux 7.2.8 E850-96 board wrapper and a reusable DVS
I2C/RESET include. The wrapper includes the upstream
`exynos850-e850-96.dts`, which reserves secure memory at
0xBAB00000–0xBFFFFFFF (85 MiB, no-map) and the ramoops area. It also retains
the 12 MiB WLBT no-map reservation. No WLAN device is instantiated.

After flashing the prepared ESP, the live DT contained `dvs@10`,
`compatible = "nrv,rc1s"`, address 0x10, and GPC0[2] active-low RESET.
The 12 MiB reservation was present and CSI/PHY nodes were absent.

## Recovery tools

From the repository root:

```sh
tools/fetch-dltool.sh
tools/boot-fastboot.sh .work/dltool
```

`fetch-dltool.sh` downloads and builds the public upstream dltool and its
payloads into ignored `.work/`. Host packages: git, make, GCC,
libusb-dev/libusb-0.1-4, and fastboot. Follow the upstream dltool README for
USB udev/group permissions.

`boot-fastboot.sh` guides SW1=1000 USB device-mode startup, runs dltool to
RAM-boot LK, and waits for one fastboot device. It does not write eMMC or
install U-Boot. The recovery tool was syntax-checked but not executed during
the 2026-10-07 validation.

## Validation scope

Standalone module build, DTS compilation, and shell syntax checks passed.
The shared module was loaded on the board and reported the ready message
shown above. CSI/PHY/DMA and the SDK sensor initialization recipe are not
included in this module. No kernel-source patch is required.
