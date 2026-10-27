# E850 CSIS WDMA raw capture module

`e850-csis-dma.ko` drives one Exynos850 MIPI-CSIS v1.2 link, its D-PHY and
the CSIS WDMA directly, without the ISP:

    DVS --CSI-2--> CSIS0/1/2 --> WDMA VC0 --> 8 DMA buffers in DRAM --> read()

Each CSI-2 frame (FS..FE) of the configured virtual channel and data type is
stored in packet mode into one of eight coherent DMA buffers. `read()` on
`/dev/e850-csis<N>` returns the oldest completed buffer as
`struct e850_dvs_frame_hdr` + payload ([UAPI](include/uapi/e850-csis-dvs.h)).
`tools/dvs-decoder/dvs-capture` records these into a capture file.

Build like `modules/dvs`:

```sh
make -C modules/csis KDIR=/absolute/path/to/kernel-build \
  KERNEL_SOURCE=/absolute/path/to/kernel-source \
  CROSS_COMPILE=/absolute/path/to/aarch64-none-linux-gnu-
```

The DT node is in [`dts/exynos850-e850-96-dvs-csis.dtsi`](../../dts/exynos850-e850-96-dvs-csis.dtsi)
(CSIS2 2-lane enabled for the 15-pin CN4 / 22-to-15-pin FPC; CSIS0 alternative disabled). Stream parameters
(`nrv,data-lanes`, `nrv,lane-rate-mbps`, `nrv,data-type`,
`nrv,virtual-channel`, `nrv,line-bytes`, `nrv,lines-per-frame`) are DT
properties; `lane_mbps=` and `settle=` module parameters override the rate
and HS settle for bring-up experiments.

Debugging: `/sys/kernel/debug/e850-csis<N>/regs` dumps the CSIS, WDMA and
WDMA-common registers; `.../stats` shows frame, drop and error counters
(also `E850_DVS_IOC_G_STATS`).

## Sources and status

Register offsets, field layouts, the stream-on order and the D-PHY
`0x0503_0008` tuning come from the GPL-2.0 Linaro `android-exynos-4.14` BSP
(`is-hw-csi-v1_2.[ch]`, `is-device-csi_v4.c`, `phy-exynos-mipi.c`,
`is-hw-settle-8nm-lpp.h`, `exynos3830-camera.dtsi`). Clocks use the mainline
`CMU_IS` bindings.

Build-tested against Linux 7.2.8 arm64 (W=1, modpost). **Not yet tested on
hardware.** Unverified assumptions, in order of risk:

1. CN4 routing: CN4 is a 15-pin FPC (lanes 0/1 + clock), CSIS2 is assumed;
   fall back to the CSIS0 node if no frames arrive.
2. The IS power domain is on after boot (mainline has no PD node for it).
3. WDMA slot rotation: the completed slot is read from `ACT_FRAMEPTR`
   at frame start; `FCNTSEQ=0xff` enables all eight slots.
4. Lane rate: `nrv,lane-rate-mbps` must match the sensor PLL
   (recipe registers 0x30a5..0x30a7) for a correct HS settle.
