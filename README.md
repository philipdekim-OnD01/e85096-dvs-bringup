# E850-96 DVS Bring-up

Experimental DVS sensor bring-up project for the Linaro E850-96
development board based on Samsung Exynos850.

Status: Work in Progress / Experimental

## Start Here: Linux Migration and Student Forks (2026-10-26)

- **[E850-96 Android → Linux conversion / flashing guide](docs/E85096-ANDROID-TO-LINUX.md)**: supported architecture, preparation, official U-Boot sequence, recovery warnings, and post-boot checks.
- **[Student Fork and Pull Request guide](docs/STUDENT-FORK-PR.md)**: fork this repository, work on a branch, and submit changes for review.
- **[Windows 11 + WSL2 Ubuntu: Linux/Android driver development](docs/WINDOWS-WSL2-DRIVER-DEV.md)**: setup commands, ARM64 cross-build prerequisites, Android kernel differences, USB/ADB/Fastboot access; students can keep Windows installed.
- **[DVS I2C/RESET driver](modules/dvs/src/main.c)** and **[module build notes](modules/dvs/README.md)**: reference implementation merged through [PR #1](https://github.com/philipdekim-OnD01/e85096-dvs-bringup/pull/1).

This repository is the **upstream** project. The student/developer repositories should be **forks of this repository**, not additional forks created inside the same owner account. The I2C/RESET module was tested on a Linux 7.2.8 E850-96; **MIPI/CSIS DMA capture and full DVS decoding are not yet implemented in this repository**. Review the Linux package README and verify board-specific images before destructive eMMC flashing.


## Objective

The objective of this project is to connect an event-based
Dynamic Vision Sensor (DVS) to the E850-96 platform and investigate
a direct MIPI CSI-2 data path without using the conventional ISP.

Target architecture:

DVS
  |
  | MIPI CSI-2
  v
Exynos850 CSIS
  |
  | DMA
  v
DRAM
  |
  v
Userspace event processing

The ISP is not required for this application.

## Hardware Platform

Board: Linaro / 96Boards E850-96

SoC: Samsung Exynos850

Camera connector: CN4

Camera interface: MIPI CSI-2

Camera control interface: HSI2C2

Initial sensor:
NRV DVS / Samsung S5KRC1S

## Initial Software Platform

Initial kernel:

android-exynos-4.14-linaro

Initial OS:

Android engineering environment

Future target:

Mainline Linux
+ U-Boot
+ Debian

## Initial Bring-up Plan

1. Enable CN4 camera power

2. Configure LDO32 for 3.3 V

3. Enable CAM_GPIO

4. Enable Exynos850 HSI2C2

5. Detect the DVS on the I2C bus

6. Read the DVS CHIP_ID

7. Initialize the DVS using userspace I2C commands

8. Configure MIPI CSI-2

9. Configure Exynos850 CSIS

10. Capture event data through DMA

11. Decode events in userspace

## Initial Bring-up Status (Historical; See 2026-10-07 Verification Below)

CN4 power configuration:
Confirmed in Device Tree / Hardware not tested

HSI2C2 configuration:
Confirmed in Device Tree / Hardware not tested

DVS I2C:
Not verified

CHIP_ID:
Not verified

DVS initialization:
Not verified

MIPI CSI-2:
Not tested

Exynos850 CSIS:
Not tested

DMA capture:
Not tested

Userspace event decoding:
Not tested

### Additional verification — 2026-10-07

DVS I2C communication was verified on E850-96 running Linux 7.2.8 after
flashing the prepared DTB. The driver completed a register 0x303B
write/readback test with value 0x55. CHIP_ID and MIPI reception remain
unverified. See [the DVS module README](modules/dvs/README.md) for details.

## Directory Structure

    patches/
        Kernel and device-tree patches

    dts/
        Device Tree files and experiments

    tools/
        I2C and sensor bring-up tools

    modules/
        External kernel drivers
        dvs/
            DVS I2C/RESET driver and build instructions

    logs/
        Boot, kernel, I2C and CSIS logs

    docs/
        Bring-up notes and architecture documentation

## E850-96 References

Linaro E850-96:

https://gitlab.com/Linaro/96boards/e850-96

E850-96 kernel:

https://gitlab.com/Linaro/96boards/e850-96/kernel

## DVS References

NRV:

https://nrv.kr/

NRV Documentation:

https://nrvcorp.github.io/docs/

NRV GitHub:

https://github.com/nrvcorp

jAER:

https://github.com/SensorsINI/jaer

## Disclaimer

This repository contains experimental research and educational work.

Only publicly available source code and documentation should be
included.

Samsung Electronics confidential source code, internal documentation,
non-public register information, firmware, or NDA-protected material
must not be committed to this repository.
