# E850-96 DVS Bring-up Notes

## Phase 1: Power

CN4 camera interface uses 3.3 V for camera power and control signals.

Verify DVS electrical compatibility before connection.

## Phase 2: HSI2C2

The E850-96 CN4 camera I2C interface maps to Exynos850 HSI2C2.

Expected Device Tree node:

    &hsi2c_2

Initial test:

    ls /sys/class/i2c-adapter/

Then identify the HSI2C2 adapter.

Example:

    i2cdetect -y -r <bus>

## Phase 3: Sensor identification

Once the DVS responds on I2C:

    read CHIP_ID

Then verify the returned value against the DVS documentation.

## Phase 4: Sensor initialization

Initially perform the DVS initialization from userspace.

Possible tools:

    i2cget
    i2cset
    i2ctransfer

A complete V4L2 sensor driver is not required for the first
communication tests.

## Phase 5: MIPI CSI-2 / CSIS

Target path:

    DVS
      -> MIPI CSI-2
      -> Exynos850 CSIS
      -> DMA
      -> DRAM
      -> Userspace

The key research question is whether the DVS CSI-2 event packets
can be captured through CSIS DMA without routing the data through
the conventional ISP pipeline.
