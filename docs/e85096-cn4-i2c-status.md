# E850-96 CN4 DVS Bring-up Status

## Base

Kernel branch:

    android-exynos-4.14-linaro

Device Tree:

    arch/arm64/boot/dts/exynos/exynos3830-e850-96-common.dtsi

## Confirmed existing configuration

The Linaro E850-96 Device Tree already contains the basic
configuration required for initial CN4 DVS I2C bring-up.

Confirmed:

    Camera power: LDO32 = 3.3 V
    CAM_GPIO: GPG1[5]
    CAM_GPIO default: HIGH
    Camera I2C controller: HSI2C2
    SDA: GPC1[4]
    SCL: GPC1[5]
    I2C frequency: 400 kHz

Therefore an additional Device Tree patch is not currently
required for the first I2C communication test.

## Next Test

After booting the E850-96 board:

    ls /sys/class/i2c-adapter/

    for x in /sys/class/i2c-adapter/i2c-*; do
        echo -n "$x : "
        cat "$x/name"
    done

Identify the adapter corresponding to HSI2C2.

Then:

    i2cdetect -y -r <bus-number>

If the DVS slave address is detected, the next step is to read
the sensor CHIP_ID and initialize the sensor registers.

## Target Architecture

    NRV DVS / S5KRC1S
           |
           | I2C
           v
        HSI2C2

    NRV DVS / S5KRC1S
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
        Userspace

The conventional ISP is not required for the intended
event-based vision application.

## Current Status

CN4 power configuration: confirmed in Device Tree

HSI2C2 configuration: confirmed in Device Tree

Physical I2C communication: not tested yet

DVS CHIP_ID: not tested yet

MIPI CSI-2: not tested yet

CSIS DMA capture: not tested yet
