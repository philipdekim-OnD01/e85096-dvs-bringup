# E850-96: 벤더 5.10 커널로 Linux 부팅하기 (A안) — 진행 매뉴얼

기록 시작: 2026-10-31 (KST)  
목표: Android 10 LK(부트로더)는 그대로 두고, Linaro 벤더 5.10 커널 + busybox initramfs → Debian rootfs로 부팅한다. 그 위에서 OV5647 카메라 드라이버를 개발한다.  
선행 문서: [진행 계획](E85096-OV5647-VENDOR510-DEBIAN-PLAN.md), [Chip ID·백업 원시 기록](E85096-OV5647-CHIPID-RAW-LOG.md)

> 이 문서는 진행하면서 계속 덧붙인다. 각 단계에 **성공/실패와 실제 로그**를 함께 적는다. 성공하지 않은 단계는 성공했다고 쓰지 않는다.

---

## 0. 전체 구조

```text
LK (Android 10 부트로더, eMMC boot0 — 건드리지 않음)
  └─ boot 파티션의 boot.img (header v2) 를 읽음
       ├─ kernel : 벤더 5.10 Image (GKI 형태, 대부분의 SoC 드라이버가 모듈)
       ├─ ramdisk: busybox initramfs  ← Android vendor_boot 역할: 모듈 139개 중 필요한 것을 순서대로 적재
       └─ dtb    : s5e3830.dtb + s5e3830-erd3830.dtbo 를 미리 합친 하나의 DTB
                    └─ /chosen bootargs 를 Linux용으로 바꿈
  └─ initramfs /init → root=/dev/mmcblk0p24(userdata)에 넣은 Debian 으로 switch_root
                       root 가 없거나 실패하면 UART(ttySAC0)에 busybox 셸
```

왜 initramfs가 꼭 필요한가: 이 커널 설정에서는 eMMC(`dw_mmc*`), 타이머(`exynos_mct`), 핀 제어(`pinctrl-samsung-core`), 클록(`clk_exynos`), PMIC(`s2mpu12*`)가 전부 **모듈**이다.

```text
CONFIG_MMC_DW=m  CONFIG_MMC_DW_EXYNOS=m  CONFIG_PINCTRL_SAMSUNG=m  CONFIG_EXYNOS_PMU=m  CONFIG_REGULATOR_S2MPU12=m
CONFIG_EXT4_FS=y CONFIG_MMC_BLOCK=y      CONFIG_SERIAL_SAMSUNG=y   CONFIG_BLK_DEV_INITRD=y
```

---

## 1. 준비물과 기존 산출물 확인

| 항목 | 값 |
| --- | --- |
| 커널 빌드 산출물 | Docker 볼륨 `e850-out` (`arch/arm64/boot/Image` 46,926,148 B, 2026-09-27) |
| 커널 릴리스 | `5.10.157-00010-g3b6b9ca51034-dirty` |
| 모듈 | `modules.order` 기준 139개 |
| DTB | `arch/arm64/boot/dts/exynos/s5e3830.dtb`, `board/erd/s5e3830-erd3830.dtbo` |
| 빌드 컨테이너 | `ubuntu:22.04` arm64 (Apple Silicon에서 네이티브 실행) |

```bash
docker run --rm -v e850-out:/o ubuntu:22.04 sh -c 'cat /o/include/config/kernel.release; wc -l < /o/modules.order'
```

### 1.1 5.10 DTB 기본 bootargs의 함정

```bash
docker run --rm -v e850-out:/o ubuntu:22.04 sh -c 'apt-get -qq update >/dev/null; apt-get -qq install -y device-tree-compiler >/dev/null 2>&1; fdtget /o/arch/arm64/boot/dts/exynos/s5e3830.dtb /chosen bootargs'
```

```text
maxcpus=1 console=ram s3c2410_wdt.tmr_atboot=1 clocksource=arch_sys_counter clk_ignore_unused printk.devkmsg=on firmware_class.path=/vendor/firmware ...
```

`s3c2410_wdt.tmr_atboot=1`은 워치독을 부팅하자마자 켠다. Android에서는 `watchdogd`가 주기적으로 신호를 주지만 우리 initramfs에는 없으므로 그대로 두면 일정 시간 뒤 리셋된다. 아래 빌드 스크립트는 bootargs를 새로 쓴다.

---

## 2. boot.img 만들기

도구: [`tools/linux510/`](../tools/linux510/)

| 파일 | 역할 |
| --- | --- |
| `build-bootimg.sh` | 컨테이너 안에서 initramfs·합친 DTB·boot.img 생성 |
| `init` | initramfs의 첫 프로그램(모듈 적재 → root 전환 또는 UART 셸) |
| `autoload.deny` | 부팅 때 자동으로 올리지 않을 모듈(무선, 모뎀, GPU, 카메라, 오디오, 워치독 등). 파일은 initramfs에 들어 있어 셸에서 `modprobe`로 수동 적재 가능 |
| `mkbootimg_v2.py` | Android boot image header v2 패커 |

### 2.1 패커 검증 — 원본과 바이트 단위 일치

LK가 받아들이는 형식인지 확인하려고, 백업해 둔 Android 10 `boot.img`를 풀어서 이 패커로 다시 만들었다.

```text
Android 10 boot.img 헤더: header_version 2, page_size 0x800, kernel_addr 0x10008000, ramdisk_addr 0x10000000,
                          dtb_addr 0x10000000, cmdline "androidboot.hardware=exynos850 androidboot.selinux=permissive buildvariant=eng"
재포장 결과: repack IDENTICAL to stock boot.img (first 24401920 bytes)
```

### 2.2 빌드

```bash
mkdir -p out
docker run --rm -v e850-out:/o:ro -v "$PWD/tools/linux510:/t:ro" -v "$PWD/out:/out" \
    ubuntu:22.04 sh /t/build-bootimg.sh                    # root= 없이: UART 셸까지만
```

실제 출력:

```text
modules: 139 copied, 91 autoload
/out/boot.img: kernel 46926148 ramdisk 8265126 dtb 147218 bytes
```

- 모듈은 `strip --strip-debug`로 디버그 정보를 뺐다(`fimc-is.ko`는 57 MB → initramfs 전체 8 MB).
- boot 파티션 크기는 100 MB(104,857,600 B)이고 boot.img는 55 MB라 들어간다.

---

## 3. 시험 1 — RAM 부팅(`fastboot boot`), eMMC 쓰기 없음

```bash
adb reboot bootloader
fastboot devices                    # ... fastboot
fastboot boot out/boot.img
```

**결과: 실패(멈춤).** `fastboot boot`이 끝나지 않았고 그 뒤 LK fastboot가 `getvar`에도 응답하지 않았다. eMMC는 바뀌지 않았으므로 전원을 껐다 켜면 Android 10으로 돌아온다.

동시에 Mac 쪽 USB-TTL이 실패해 UART 로그를 얻지 못했다.

```text
termios.error: (22, 'Invalid argument')                                         ← 시리얼 포트 설정 실패
kernel: (IOUSBHostFamily) ... device 4 (FT232R USB UART@01110000) endpoint 0x00: status 0xe00002d6 (timeout)
kernel: (IOUSBHostFamily) ... (Exynos3830 LK Bootloader@01140000) endpoint 0x81: aborting 1 requests
```

그때 Chrome(pid 723)이 아직 실행 중이었다. 시험 전에 **Chrome과 ChatGPT 앱을 ⌘Q로 완전히 종료**하고 `pgrep -fl -i 'chrome|chatgpt'`로 확인한다.

LK 바이너리(Linaro dltool의 `lk.bin`) 문자열에서 확인한 사실:

```text
download:            boot            FAILdata too large: %u > %u
FAILdownload data size is bigger than buffer size
Entering fastboot: Abnormal RST_STAT: 0x%x
Entering fastboot: xct boot fail code - %d
```

- `boot` 명령과 크기 초과 오류가 있으므로, 크기 문제였다면 FAIL 응답이 왔어야 한다. 원인은 UART 로그로 확인해야 한다(다음 시험).
- 비정상 리셋 뒤에는 LK가 스스로 fastboot에 들어가는 경로가 있다.

### 다음 시험 전 체크리스트

1. 보드 전원 재투입 → Android 10 부팅 확인(`adb devices`).
2. Chrome·ChatGPT 완전 종료 확인, USB-TTL을 뽑았다 다시 꽂기.
3. UART 기록 시작 → `fastboot getvar max-download-size` 확인.
4. `fastboot boot` 재시도. 크기가 문제면 `Image.gz`(압축 커널) 또는 모듈 수를 줄인 initramfs로 boot.img 크기를 줄인다.

### 3.1 모듈 적재 순서 수정 (시험 1 이후)

커널 빌드 설정(`build.config.erd3830*`)에 Android가 첫 단계에서 쓰는 순서 파일이 있다.

```text
VENDOR_BOOT_MODULE_ORDER=vendor_boot_module_order_s5e3830.cfg
```

```text
exynos-chipid_v2.ko exynos-reboot.ko clk_exynos.ko hardlockup-watchdog.ko exynos_mct.ko
exynos_tty.ko pinctrl-samsung-core.ko i2c-exynos5.ko i2c-s3c2410.ko   ← 이 9개를 먼저, 그다음 modules.order
```

`build-bootimg.sh`가 이 파일(`tools/linux510/vendor_boot_module_order_s5e3830.cfg`)을 앞에 두고 나머지 `modules.order`를 이어 붙이도록 고쳤다. 결과: 자동 적재 92개.

---

## 4. OV5647 CIS 드라이버 (보드 없이 진행한 부분)

묶음: [`modules/ov5647-pablo/`](../modules/ov5647-pablo/README.md)

### 4.1 커널 트리에 적용

```bash
docker run --rm -v e850-kernel:/work/kernel -v "$PWD/modules/ov5647-pablo:/m:ro" e850-build-env:1 \
    sh /m/apply.sh /work/kernel
```

```text
#define SENSOR_NAME_OV5647		219          (두 exynos_is_dt.h에 추가)
16:#include "camera/s5e3830-e850-ov5647-camera.dtsi"   (보드 DT: S5KRC1S → OV5647)
```

### 4.2 모듈 빌드 — 첫 시도 실패와 해결

트리 안에서 단일 대상으로 빌드하면 modpost가 실패한다.

```bash
make O=/work/out ... drivers/media/platform/exynos/camera/sensor/module_framework/cis/is-cis-ov5647.ko
```

```text
ERROR: modpost: "is_dev" [.../is-cis-ov5647.ko] undefined!
ERROR: modpost: "sensor_cis_set_registers" [.../is-cis-ov5647.ko] undefined!
ERROR: modpost: "find_peri_by_cis_id" [.../is-cis-ov5647.ko] undefined!
make[2]: *** Deleting file 'modules-only.symvers'
```

- 이 심볼들은 `fimc-is.ko`가 내보내며 `Module.symvers`(15,280줄)에는 들어 있다. 단일 대상 modpost가 모듈 심볼을 읽지 않는 것이 원인이다.
- **주의:** 이 실패로 빌드 볼륨의 `modules-only.symvers`가 지워졌다. 다음 전체 빌드 때 다시 생성된다.
- `KBUILD_EXTRA_SYMBOLS`로 fimc-is 심볼을 따로 넘기면 `exported twice`로 실패한다. 외부 모듈 빌드에서는 modpost가 `Module.symvers` 전체를 이미 읽기 때문이다.

해결: 외부 모듈(`M=`)로 빌드한다([`Kbuild`](../modules/ov5647-pablo/Kbuild), [`build-module.sh`](../modules/ov5647-pablo/build-module.sh)).

```bash
docker run --rm -v ~/e850-a13-build:/work -v e850-kernel:/work/kernel -v e850-out:/work/out \
    -v "$PWD/modules/ov5647-pablo:/m:ro" e850-build-env:1 bash /m/build-module.sh
```

```text
  CC [M]  /work/out/ext-ov5647/is-cis-ov5647.o
  LTO [M] /work/out/ext-ov5647/is-cis-ov5647.lto.o
  MODPOST /work/out/ext-ov5647/Module.symvers
  LD [M]  /work/out/ext-ov5647/is-cis-ov5647.ko
-rw-r--r-- 1 root root 665456 Oct 30 16:10 /work/out/ext-ov5647/is-cis-ov5647.ko
```

### 4.3 DTB 다시 빌드와 확인

```bash
docker run --rm -v ~/e850-a13-build:/work -v e850-kernel:/work/kernel -v e850-out:/work/out e850-build-env:1 bash -c \
  'export PATH=/work/prebuilts/clang/host/linux-x86/clang-r450784e/bin:$PATH; cd /work/kernel;
   make O=/work/out ARCH=arm64 LLVM=1 LLVM_IAS=1 CROSS_COMPILE=aarch64-linux-gnu- -j10 dtbs'
```

boot.img를 다시 만든 뒤 합친 DTB를 풀어 확인:

```text
modules: 140 copied, 92 autoload            ← is-cis-ov5647.ko 포함(자동 적재는 안 함)
10:	is_sensor_ov5647@36 {
2452:		ov5647@36 {
2456:			compatible = "samsung,exynos-is-cis-ov5647";
(s5krc1s 노드 없음)
```

`.config` 변경은 `CONFIG_CAMERA_CIS_OV5647_OBJ=m` 한 줄뿐이다(변경 전 사본: `/work/out/.config.before-ov5647`).

---

## 5. Debian rootfs 이미지

스크립트: [`tools/linux510/build-rootfs.sh`](../tools/linux510/build-rootfs.sh) — Debian 12 bookworm arm64 minbase + systemd, ssh, i2c-tools, v4l-utils, python3-numpy.

```bash
docker run --rm --privileged -v e850-out:/o:ro -v "$PWD/tools/linux510:/t:ro" -v "$PWD/out:/out" \
    ubuntu:22.04 sh /t/build-rootfs.sh
```

결과(2026-10-31 01:21 KST): `rootfs.simg` 398,062,088 B(sparse, 펼치면 3 GiB ext4).

이미지를 풀어 확인한 내용:

```text
/etc/fstab    : /dev/mmcblk0p24  /  ext4  defaults,noatime  0  1
/etc/hostname : e850-96
/etc/passwd   : root, e850 (uid 1000)        그룹: sudo, video, i2c
/lib/modules/5.10.157-00010-g3b6b9ca51034-dirty : .ko 140개 (is-cis-ov5647.ko 포함)
serial-getty@ttySAC0 : root 자동 로그인
```

- 계정: `e850` / 비밀번호 `e850`(실습용, 네트워크에 연결하기 전에 바꾼다).
- userdata(p24)에 굽는다: `fastboot flash userdata rootfs.simg`. Android 10의 사용자 데이터는 지워진다(백업 대상에서 제외했던 영역).
- 첫 빌드는 마지막 확인용 `du`가 `/proc` 항목 때문에 오류를 내 종료 코드 1로 끝났다(이미지는 정상). 스크립트에서 그 줄을 뺐다.

---

## 6. 다음 순서 (보드 전원 재투입 후)

2026-10-31 01:25 KST 현재 보드는 `fastboot devices`에 보이지만 명령에 응답하지 않는다(시험 1의 중단된 다운로드 이후). eMMC는 바뀌지 않았다.

1. 보드 전원을 껐다 켠다 → Android 10 부팅, `adb devices` 확인.
2. Chrome·ChatGPT 완전 종료, USB-TTL 다시 꽂기, UART 기록 시작(115200 8N1).
3. `adb reboot bootloader` → `fastboot getvar max-download-size`.
4. 크기가 boot.img(55 MB)보다 크면 RAM 부팅:
   ```bash
   fastboot boot out/boot.img          # root= 없음: UART에 busybox 셸이 뜨면 성공
   ```
   셸에서 확인: `dmesg | grep '\[init\]'`, `ls /dev/mmcblk0p*`, `modprobe is-cis-ov5647`.
5. 성공하면 rootfs와 boot를 굽는다(되돌리기: 백업의 `mmcblk0p15.img.gz`를 `fastboot flash boot`).
   ```bash
   fastboot flash userdata out/rootfs.simg
   fastboot flash boot out/boot-root.img   # build-bootimg.sh root=/dev/mmcblk0p24
   fastboot reboot
   ```
6. 크기가 작으면: boot.img를 `img2simg`로 sparse로 바꿔 `fastboot flash boot`로 시험한다(RAM 부팅 대신 eMMC 기록, 백업으로 복구 가능).

이 단계부터는 보드를 직접 다뤄야 하므로 결과가 나오는 대로 이 문서에 이어서 기록한다.

## 7. 전원 재투입 후 확인 (2026-10-31 오전)

Chrome을 ⌘Q로 종료한 뒤 Android 10으로 정상 부팅했다(`adb devices` → `device`).

### 7.1 USB-TTL이 맥에 잡히지 않음

`/dev/cu.usbserial-*`가 없다. USB 장치 목록에도 FTDI(idVendor 0x0403)가 보이지 않는다. 드라이버보다 케이블·포트·어댑터 쪽 문제다.

```bash
ioreg -p IOUSB -l -w0 | grep -E '"USB Product Name"|"idVendor"'
# USB2.0 HUB / Billboard / Flash Card Reader/Writer / Exynos  ← FT232R 없음
```

허브를 거치지 말고 맥 본체 포트에 직접 꽂는다. 다른 케이블로도 바꿔 본다. 꽂았을 때 어댑터 LED가 켜지는지 확인한다.

### 7.2 이 보드의 LK는 모르는 `getvar`에 멈춘다

```bash
adb reboot bootloader
fastboot getvar max-download-size   # max-download-size: 805306368  (768 MB, 0.004 s)
fastboot getvar product             # 응답 없음 → 이후 모든 명령 무응답
fastboot reboot                     # FAILED (usb_read failed with status e00002ed)
```

- 한도는 768 MB다. 55 MB boot.img는 크기 문제가 아니다.
- 온보드 Android 10 LK는 `getvar product`에 응답하지 않고 그대로 멈춘다. Linaro `lk.bin`과는 다른 바이너리다.
- 복구 방법은 **전원 재투입**뿐이다. eMMC는 바뀌지 않는다.
- 시험 1의 멈춤도 같은 종류일 수 있다. 최신 `fastboot` 클라이언트는 `boot`·`flash` 전에 `getvar`(has-slot, partition-type, is-logical 등)를 먼저 보낸다.

**규칙:** 이 LK에는 `getvar max-download-size` 말고는 `getvar`를 보내지 않는다.

다음 시험에서는 `fastboot -v boot`로 실제로 오가는 명령을 남기고, UART로 LK 출력을 함께 기록한다.
