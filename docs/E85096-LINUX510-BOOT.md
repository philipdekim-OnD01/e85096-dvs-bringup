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

### 7.3 시험 2 — RAM 부팅 재시도: LK의 `fastboot boot`는 동작하지 않는다

USB-TTL을 맥 본체 포트에 직접 꽂자 `FT232R USB UART`가 잡혔다. Chrome을 종료한 상태에서 UART를 기록하며 다시 시험했다.

```bash
adb reboot bootloader
fastboot -v boot out/boot.img
# Sending 'boot.img' (54056 KB)    OKAY [  1.352s]
# Booting                           ← 응답 없음
```

UART(LK 로그):

```text
[    4.448082 ] Entering fastboot: reboot bootloader command
[   17.148808 ] fb: download:034ca000      ← 55 MB 수신 성공
[   18.500557 ] fb: boot                   ← 이후 출력 없음, LK 무응답
```

eMMC에서 정상 부팅할 때 LK는 커널로 넘어가기 전에 아래 줄을 찍는다. `fb: boot` 뒤에는 이 줄이 하나도 나오지 않았다.

```text
kernel size: 0x0166600c
ramdisk size: 0x000c212c
DTB size: 0x0001bef4
DTBO: Merge Complete (size:179932)!
updated bootargs: ...
```

**결론:** 이 Android 10 LK는 `boot` 명령을 받기만 하고 실행하지 않는다. 다운로드는 정상이므로 USB나 크기 문제가 아니다. RAM 부팅 대신 boot 파티션에 쓰는 방법으로 바꾼다.

### 7.4 boot 파티션 쓰기 — fastboot 대신 root adb의 `dd`

LK fastboot는 모르는 명령에 멈추는 경우가 많다. 그래서 Android 10(root adb) 안에서 직접 쓴다. boot 파티션은 `mmcblk0p15`이고 크기는 100 MB다.

```bash
adb push out/boot.img /data/local/tmp/boot-linux.img
adb shell 'dd if=/data/local/tmp/boot-linux.img of=/dev/block/mmcblk0p15 bs=1M conv=fsync && sync'
adb reboot
```

되돌리기(Android 10 복구)는 아래 둘 중 하나로 한다. 첫 번째는 Linux initramfs 셸에서 하는 방법이다. 두 번째는 LK fastboot가 응답할 때 쓰는 방법이다.

```bash
# 1) Linux 셸에서
gunzip -c mmcblk0p15.img.gz | dd of=/dev/mmcblk0p15     # 이미지를 보드로 옮길 수 있을 때
# 2) fastboot에서
gunzip -k mmcblk0p15.img.gz && fastboot flash boot mmcblk0p15.img
```

### 7.5 시험 3 — eMMC boot 파티션에서 5.10 커널 첫 부팅: 커널은 OK, init에서 패닉

boot 파티션에 쓰기 전에 현재 내용이 백업과 같은지 해시로 확인한다. 원본은 보드 안에도 한 부 더 둔다.

```bash
adb shell sha256sum /dev/block/mmcblk0p15            # d0fc5688...
gunzip -c mmcblk0p15.img.gz | shasum -a 256          # d0fc5688...  (같음)
adb push out/boot.img /data/local/tmp/boot-linux.img
adb shell 'cp /dev/block/mmcblk0p15 /data/local/tmp/boot-android10.img
           dd if=/data/local/tmp/boot-linux.img of=/dev/block/mmcblk0p15 bs=1M conv=fsync && sync'
adb shell 'head -c 55353344 /dev/block/mmcblk0p15 | sha256sum'   # 2361b615... = 맥의 boot.img
adb reboot
```

UART에서 확인한 결과, LK가 새 이미지를 읽고 5.10 커널로 넘어갔다. bootargs도 DTB `/chosen`에서 가져왔다.

```text
[    6.089455 ] kernel size: 0x02cc0944
[    6.292637 ] bootargs: console=ttySAC0,115200n8 earlycon=exynos4210,mmio32,0x13820000 maxcpus=1 ...
[    0.000000] Booting Linux on physical CPU 0x0000000000 [0x410fd051]
[    0.000000] Linux version 5.10.157-00010-g3b6b9ca51034-dirty ... clang version 14.0.7
[    0.000000] Machine model: Samsung ERD3830 board based on EXYNOS3830
[    0.000000] earlycon: exynos4210 at MMIO32 0x0000000013820000 (options '')
[    2.144356] Warning: unable to open an initial console.
[    2.161877] Run /init as init process
[    2.211197] request_module fs-devtmpfs succeeded, but still no fs?
[    2.548861] Kernel panic - not syncing: Attempted to kill init! exitcode=0x00000100
[    2.648924] Reboot failed -- System halted
```

**원인 세 가지**

| 증상 | 원인 | 고침 |
|---|---|---|
| `still no fs?` | GKI 설정에 `# CONFIG_DEVTMPFS is not set`. Android는 init이 `/dev`를 직접 만든다 | 커널에 `CONFIG_DEVTMPFS=y`, `CONFIG_DEVTMPFS_MOUNT=y`. 옛 커널에서도 동작하도록 init에 tmpfs, `mknod`, `mdev` 대안을 넣음 |
| `unable to open an initial console` | initramfs에 `/dev/console`이 없음 | cpio에 `mknod /dev/console c 5 1` |
| init 종료 → 패닉 | `/tmp`가 없어서 `modprobe ... 2>/tmp/err` 리다이렉트가 전부 실패. 모듈이 하나도 안 올라가 `/dev/ttySAC0`도 없음 → `exec sh`가 EOF로 종료 | `/tmp`를 tmpfs로 마운트. init은 끝나지 않는 루프로 바꿈 |
| 재부팅 대신 정지 | 리부트 드라이버(`exynos-reboot.ko`)가 모듈인데 적재 전이었음 | 1단계 모듈로 가장 먼저 올림 |

**교훈:** 이 상태가 되면 보드는 전원 재투입 말고 되돌릴 방법이 없고, 다시 켜도 같은 이미지로 부팅해 또 멈춘다. 그래서 다음 이미지에는 아래 안전장치를 넣었다(7.6).

### 7.6 원격으로 다룰 수 있게 하는 안전장치

1. **2단계 모듈 적재**
   - 1단계(`tools/linux510/modules.stage1`): 리부트, 클록, 타이머, UART, pinctrl, I2C, PMIC, eMMC.
   - 그다음 UART로 `press Enter within 5 s for a rescue shell`을 띄우고 5초 기다린다.
   - Enter를 누르면 나머지 모듈을 올리기 **전에** 셸에 들어간다. 2단계 모듈 중 하나가 커널을 죽이더라도 다음 부팅에서 막을 수 있다.
2. **`restore-android`**
   - initramfs 안에 백업 `mmcblk0p15.img.gz`(Android 10 boot, 9.9 MB)가 들어 있다.
   - 이 명령 하나로 boot 파티션을 원본으로 되돌리고 재부팅한다.
3. **`to-fastboot`**
   - misc 파티션의 BCB command에 `bootonce-bootloader`를 쓰고 재부팅한다.
   - LK가 이것을 읽고 fastboot에서 멈춘다. `adb reboot bootloader`와 같은 원리이고, LK 로그의 `Recovery: command: 32 bootonce-bootloader`가 그 증거다.
4. **`part-dev NAME`**
   - GPT 파티션 이름(`boot`, `misc`, `userdata`)으로 장치 경로를 찾는다.

boot.img를 만들 때는 백업 폴더를 `/b`로 넘긴다.

```bash
docker run --rm -v e850-out:/o:ro -v "$PWD/tools/linux510:/t:ro" -v "$PWD/out:/out" \
    -v ~/Desktop/E850_Android10_backup_20261031:/b:ro ubuntu:22.04 sh /t/build-bootimg.sh
```

### 7.7 `fbmini` — 필요한 명령만 보내는 fastboot 클라이언트

`fastboot flash`는 쓰기 전에 `getvar`(has-slot, is-logical 등)를 여러 번 먼저 보낸다. 7.2절에서 보았듯이 이 LK는 모르는 질문에 멈춘다. 그래서 `download`, `flash`, `reboot`만 보내는 클라이언트를 libusb로 만들었다. 맥에는 Homebrew libusb가 이미 있다.

```bash
cd tools/linux510
clang -O2 -o fbmini fbmini.c -I/opt/homebrew/include/libusb-1.0 -L/opt/homebrew/lib -lusb-1.0
./fbmini getvar max-download-size
./fbmini flash boot ../../out/boot.img
./fbmini flash userdata ../../out/rootfs.simg      # sparse 그대로 (768 MB 한도 안)
./fbmini reboot
```

### 7.8 멈춘 보드를 fastboot로 들어가게 하기

LK는 부팅할 때마다 Volume Up 키를 10번 읽는다(`Volume up key: 1` × 10, 1 = 안 눌림). **Volume Up을 누른 채 전원을 넣으면** fastboot로 들어갈 것으로 예상한다. 아직 확인하지 않았다.

이 방법이 안 되면 Exynos USB 다운로드 모드를 쓴다(SW1 DIP 1번 ON, Linaro dltool의 `smdk-usbdl`). 이 경우 RAM에서 Linaro LK가 뜨고, 그 LK의 fastboot로 boot를 다시 쓴다.

### 7.9 DEVTMPFS를 켠 커널 다시 빌드

`build.sh` 없이 `make`를 직접 부를 때는 `build.config.erd3830_t`가 넘겨주던 변수를 직접 넣어야 한다.

- `TARGET_SOC`가 없으면 `drivers/staging/nanohub/ipc_chub.h: fatal error: '.h' file not found`로 실패한다.
- `KCFLAGS`가 빠지면 모든 파일이 다른 플래그로 다시 컴파일된다(첫 시도에서 43분 걸림).

```bash
# e850-build-env:1 컨테이너 안 (/work/out = 빌드 출력)
cd /work/out
/work/kernel/scripts/config --file .config -e DEVTMPFS -e DEVTMPFS_MOUNT   # 전: .config.before-devtmpfs
make -C /work/kernel O=/work/out ARCH=arm64 LLVM=1 LLVM_IAS=1 CROSS_COMPILE=aarch64-linux-gnu- olddefconfig
make -C /work/kernel O=/work/out ARCH=arm64 LLVM=1 LLVM_IAS=1 CROSS_COMPILE=aarch64-linux-gnu- \
     TARGET_SOC=s5e3830 KCFLAGS=-D__ANDROID_COMMON_KERNEL__ -j$(nproc) Image modules dtbs
```

- `.config` 차이는 `CONFIG_DEVTMPFS=y`, `CONFIG_DEVTMPFS_MOUNT=y` 두 줄뿐이다.
- 빌드는 성공했다(17분).
- 이번 전체 빌드에서 `is-cis-ov5647.ko`도 트리 안에서 함께 빌드됐다(`modules.order`에 포함). 예전 트리 밖 모듈(`ext-ov5647`)은 새 `fimc-is.ko`와 맞지 않으므로 치웠다.

### 7.10 루트 장치를 이름으로 찾기

5.10에서 SD 컨트롤러가 먼저 잡히면 eMMC가 `mmcblk1`이 될 수 있다. 그래서 번호 대신 이름을 쓴다.

- 커널 인자 `root=PARTLABEL=userdata`: init이 `part-dev`로 GPT 이름을 장치로 바꾼다.
- rootfs fstab `LABEL=rootfs`: `mke2fs -L rootfs`로 붙인 레이블.

```bash
docker run --rm -v e850-out:/o:ro -v "$PWD/tools/linux510:/t:ro" -v "$PWD/out-root:/out" \
    -v ~/Desktop/E850_Android10_backup_20261031:/b:ro ubuntu:22.04 sh /t/build-bootimg.sh root=PARTLABEL=userdata
```

| 산출물 | 크기 | SHA-256 앞부분 |
|---|---|---|
| boot.img (셸만) | 64,772,096 | `19bb249c` |
| boot.img (`root=PARTLABEL=userdata`) | 64,772,096 | `2434898b` |
| rootfs.simg | 398,295,560 | e2fsck 오류 없음, `/lib/modules/5.10.157-00010-g3b6b9ca51034-dirty` |

initramfs 안의 `restore/boot-android10.img.gz`를 풀면 원본 boot와 같은 `d0fc5688...`이 나온다.

> 실수 기록: 실행 중인 `sh` 스크립트를 편집하면 안 된다. sh는 스크립트를 읽어 가며 실행한다. 빌드 중에는 스크립트 사본(`cp -R tools/linux510 /tmp/tsnap`)을 마운트한다.

### 7.11 다음 순서 (보드 전원 재투입 때)

1. UART 기록을 켠 채로 **Volume Up을 누르고 전원 ON** → UART에서 `Entering fastboot` 확인.
2. `fbmini`로 굽는다.
   ```bash
   ./fbmini flash userdata rootfs.simg
   ./fbmini flash boot out-root/boot.img
   ./fbmini reboot
   ```
3. UART에서 `[init] press Enter within 5 s` → Enter → 구조 셸에서 `ls /dev/mmcblk*`, `lsmod` 확인 → `exit`로 계속 부팅.
4. Debian 로그인(`root` 자동 로그인 또는 `e850`/`e850`).
5. 문제가 생기면 구조 셸에서 `restore-android`.

### 7.12 시험 4 — fbmini로 굽기: boot는 성공, userdata는 LK가 1 GiB FILL에서 멈춤

전원 재투입 때 Volume Up을 10초 누르고 있었더니 LK가 fastboot에 들어갔다. 다만 키 때문은 아니었다. 키는 10번 모두 `1`로 읽혔고, LK가 밝힌 진입 사유는 아래와 같다.

```text
[    9.150847 ] Entering fastboot: Abnormal RST_STAT: 0x10000000
```

굽기 결과:

```text
$ ./fbmini flash boot out-root/boot.img
> download:03dc5800   < DATA03dc5800   < OKAY
> flash:boot          < OKAY                 UART: partition 'boot' flashed
$ ./fbmini flash userdata rootfs.simg
... CHUNK TYPE FILL (lba: 11841560, sct: 1310712 ...)     ← 640 MiB까지는 처리
... CHUNK TYPE FILL (lba: 13152280, sct: 2097144 ...)     ← 1 GiB에서 멈춤, USB도 끊김
```

해결은 `tools/linux510/resparse.py`다. FILL 청크를 32 MiB 이하로 쪼갠다. 풀어 낸 내용은 원본과 SHA-256이 같다(`585c5383...`).

```bash
python3 tools/linux510/resparse.py rootfs.simg rootfs-split.simg   # 35 chunks -> 113
```

### 7.13 시험 5 — 새 init이 0.03초 만에 종료

```text
[    2.299199] Run /init as init process
[    2.354360] Kernel panic - not syncing: Attempted to kill init! exitcode=0x00000100
```

원인은 7.6절에서 추가한 `[ -c /dev/console ] && exec </dev/console ...`였다.

- 노드는 있지만 콘솔 드라이버(`exynos_tty.ko`)가 아직 없어서 open이 실패한다.
- **셸에서 `exec` 리다이렉트가 실패하면 셸 자체가 끝난다.**

고친 방법: 서브셸에서 먼저 열어 보고(`if (exec </dev/console) 2>/dev/null`), 실패하면 `/dev/null`을 쓴다.

검증은 arm64 Docker에서 initramfs를 chroot로 실행해서 했다. 20초 뒤에도 init이 살아 있었고, `[init] ... no root filesystem, starting shell`까지 진행했다.

```bash
docker run --rm --privileged -v "$PWD/out-root:/b:ro" ubuntu:22.04 sh -c \
  'apt-get -qq update; apt-get -qq install -y cpio; mkdir /x && cd /x && gunzip -c /b/initramfs.cpio.gz | cpio -id
   timeout 20 chroot /x /init </dev/null; echo rc=$?   # 124 = 아직 실행 중 = 정상'
```

**규칙:** 보드에 굽기 전에 initramfs를 컨테이너에서 한 번 실행해 본다. 보드에서 한 번 실패할 때마다 전원 재투입이 필요하다.

### 7.14 `rebootarg` — 버튼 없이 fastboot로

LK는 misc BCB를 무시한다. 7.8절 로그에 `Recovery: command: 32 bootonce-bootloader`가 있는데도 그대로 부팅했다. LK가 fastboot로 들어간 것은 재부팅 사유가 `reboot bootloader`였을 때다.

busybox `reboot`는 인자를 넘길 수 없어서 `reboot(2)` RESTART2를 직접 부르는 244바이트 바이너리를 만들었다(`tools/linux510/rebootarg/`). 빌드는 i2crd와 같은 방식이다.

```bash
rebootarg bootloader     # 보드 셸에서
```

### 7.15 시험 6 — 쪼갠 rootfs 굽기 성공, 그런데 사용자 공간이 `ttySAC0`을 여는 순간 보드가 멈춤

```text
[  162.875454 ] partition 'userdata' flashed                     ← resparse.py 덕분에 끝까지 기록
...
[    5.453778] mmcblk0: mmc0:0001 DP6DAB 58.2 GiB
[    5.578094] [init] .../modules.stage1: 14 ok, 0 failed
[    5.588193]  mmcblk0: p1 p5 p12 p13 p14 p15 p16 p17 p18 p19 p20 p21 p22 p24
[    5.604615] [0: busybox: 1] exynos-uart 13820000.uart:  Clock rate : 199875000   ← init이 ttySAC0 open
(이후 UART 출력 없음. 9600~921600 baud 모두 0바이트)
```

- 1단계 모듈은 모두 올라왔고 eMMC 파티션도 정상이다.
- 커널 콘솔로서의 `ttySAC0`은 정상이었다. 4.2초에 `console [ttySAC0] enabled` 이후에도 로그가 이어졌다.
- 그런데 **사용자 공간이 `/dev/ttySAC0`을 여는 순간**(uart startup → threaded IRQ 등록 → set_termios) 출력이 끊기고 시스템이 멈췄다.
- 원래 BSP의 bootargs는 `console=ram`이다. 즉 Android 13 BSP는 이 UART를 tty 콘솔로 쓰지 않는다.

**바꾼 구조** (UART는 커널 로그 전용, 로그인은 USB):

| 항목 | 전 | 후 |
|---|---|---|
| 커널 콘솔 | `console=ttySAC0,115200n8` | `console=ram keep_bootcon` + earlycon. earlycon이 부팅 내내 UART로 출력 |
| `exynos_tty.ko` | 1단계에서 적재 | 적재하지 않음(`autoload.deny`) |
| 셸, 구조 셸 | UART | **USB ACM 가젯** `/dev/ttyGS0` → 맥 `/dev/cu.usbmodem*` |
| 네트워크 | 없음 | **USB NCM 가젯** `usb0`. 보드 192.168.7.1, DHCP로 맥에 주소 배정 → `ssh e850@192.168.7.1` |
| Debian getty | `serial-getty@ttySAC0` | `serial-getty@ttyGS0` (root 자동 로그인), ttySAC0은 mask |
| 멈췄을 때 | 영원히 정지 | **하드웨어 워치독**: busybox `watchdog -T 60 -t 5`. 사용자 공간이 멈추면 60초 뒤 리셋 |
| fastboot 진입 | 버튼, 우연 | `rebootarg bootloader` (`to-fastboot`이 호출) |

USB 가젯은 configfs로 init이 직접 만든다(VID 0x1d6b, PID 0x0104, `acm.usb0` + `ncm.usb0`). 커널 설정에 `CONFIG_USB_CONFIGFS_ACM=y`, `CONFIG_USB_CONFIGFS_NCM=y`가 이미 들어 있다. `switch_root` 뒤에도 가젯은 그대로 남고, Debian의 getty가 `ttyGS0`을 넘겨받는다.

1단계 모듈에 `s3c2410_wdt`, `phy-exynos-usbdrd-super`, `usb_notify_layer`, `usb_notifier`, `s2m_pdic_module`, `dwc3-exynos-usb`를 더했다.

### 7.16 USB 가젯 — E850-96에는 PDIC가 없다 (`modules/e850-vbus`)

```text
s2mu106_charger_set_mode: S2MU106_CHG_CTRL0 = 0xff      ← ERD3830의 충전/PDIC 칩. 이 보드에는 없음
dwc3_gadget_pullup: current_dr_role = 255               ← 컨트롤러가 device 역할로 바뀌지 않음
```

이 커널의 DT는 ERD3830 레퍼런스 보드용이다. 케이블 연결(VBUS)은 S2MU106 PDIC → `usb_notifier` → `dwc3_exynos_vbus_event()`로 들어온다. E850-96에는 이 칩이 없어서 이벤트가 오지 않고, DWC3는 클록이 꺼진 채 남는다.

`e850-vbus.ko`를 만들었다. 적재되면 `dwc3_exynos_vbus_event(dev, true)`를 한 번 부른다. init은 이것을 적재한 뒤 UDC 상태가 `configured`가 될 때까지 기다리고, 그때만 `ttyGS0`을 쓴다.

```text
e850-vbus: VBUS on for 13600000.usb: 0
dwc3 13600000.dwc3: Turn on gadget dwc3-gadget
phy_exynos_usbdrd 135d0000.phy: Turn on LDO
__dwc3_gadget_start ---
dwc3_otg_start_gadget: start check usb configuration timer
```

### 7.17 진짜 원인 — 첫 CPU idle 진입에서 SoC가 멈춘다 (`cpuidle.off=1`)

7.15~7.16의 "UART를 열면 멈춤", "가젯 시작 후 리셋", "루트 마운트 때 리셋"은 모두 같은 현상이었다. 워치독(`watchdog -T 30 -t 200ms`)을 켜면 멈춤이 리셋으로 바뀌고, LK는 `Entering fastboot: Abnormal RST_STAT: 0x1000000`으로 fastboot에 들어간다. 이 덕분에 사람 손 없이 이분 탐색을 할 수 있었다(`cycle.sh`: 빌드 → `fbmini` 굽기 → 재부팅 → 판정).

| 커널 인자 (공통: `e850.nousb e850.noswitch`) | 결과 |
|---|---|
| 2단계 전부 | 모듈 91개 적재 직후 리셋 (11.6초) |
| `e850.stage2=0` | 6.2초에 리셋 (루트 마운트는 성공) |
| `e850.stage2=0 e850.nowdt` | 리셋 없이 멈춤 → 워치독은 원인 아님 |
| `e850.stage2=0 cpuidle.off=1` | **60초 이상 생존** |

init이 일을 마치고 CPU가 처음 idle에 들어가는 순간 멈췄다. 모듈을 적재하는 동안에는 CPU가 바빠서 버텼다. Android에서는 `exynos-cpupm` 등이 idle 상태와 웨이크업을 먼저 설정해 둔다. 여기서는 일단 `cpuidle.off=1`로 idle 진입을 끈다(전력 소모는 늘어남).

시험용 커널 인자 정리:

| 인자 | 뜻 |
|---|---|
| `e850.nousb` | USB 가젯·VBUS 생략 |
| `e850.stage2=N` | 2단계 모듈 앞 N개만 적재 |
| `e850.noswitch` | 루트 마운트 후 Debian으로 넘어가지 않음. 60초 동안 `alive` 로그를 찍은 뒤 `rebootarg bootloader`로 fastboot 복귀 |
| `e850.nowdt` | 워치독 끔 (멈추면 전원 재투입 필요) |

## 8. Debian 12 부팅 성공, 그리고 HDMI 출력

### 8.1 Debian 부팅 (`cpuidle.off=1` 이후)

```text
[   21.825408] [init] switching to /dev/mmcblk0p24
Debian GNU/Linux 12 e850-96 ttyGS0
e850-96 login: root (automatic login)
root@e850-96:~# systemctl is-system-running
running
```

맥에서 접속: `screen /dev/cu.usbmodeme850_961 115200` (USB-C 케이블 하나, root 자동 로그인).

남은 문제 두 가지:

1. **udev가 카메라 모듈을 자동 적재하다 패닉:**
   - `fimc_is` `sensor_module_probe` NULL 접근.
   - 원인은 OV5647 DT의 `power_seq`에 `use = <1>;`가 빠진 것이었다(`sensor dt callback is NULL`). 고쳤다.
   - 그리고 initramfs에서 빼 둔 모듈 49개를 rootfs에서도 `blacklist`에 넣었다(`/etc/modprobe.d/e850-blacklist.conf`, `build-rootfs.sh`가 `autoload.deny`로 생성). 손으로 `modprobe`하는 것은 그대로 된다.
2. **USB 네트워크(NCM/ECM)는 맥 → 보드 방향 수신이 0이다:**
   - 로그에 `workqueue uether ... cpus=7 ... pending: process_rx_w`가 남는다. 수신 작업이 꺼진 CPU 7(`maxcpus=1`)에 걸려 있다.
   - 해결은 다음 과제로 둔다. 셸은 USB 시리얼(ACM)로 충분하다.

**버튼 없이 fastboot로 들어가기** — `to-fastboot`(워치독 데몬을 강제 종료하면 약 20초 뒤 리셋되고, LK가 `Abnormal RST_STAT`로 fastboot에 들어간다):

```bash
root@e850-96:~# to-fastboot         # = sync; kill -9 $(pidof watchdog)
# Mac: fastboot devices → ./fbmini flash boot ... → ./fbmini reboot
```

(5.10의 `reboot bootloader`(PMU SYSIP_DAT0 = 3)는 이 Android 10 LK가 알아듣지 못해 그냥 재부팅된다.)

### 8.2 HDMI: DECON → DSIM0 → LT8912B → HDMI (`modules/e850-hdmi`)

E850-96의 HDMI는 SoC의 MIPI-DSI를 **Lontium LT8912B** 브리지가 HDMI로 바꾼다. 필요한 값은 모두 Android 10의 DTB와 DTBO(idx 2)에서 꺼냈다.

| 항목 | 값 |
|---|---|
| LT8912B | `i2c@13840000`(i2c_1), 0x48(main) / 0x49(DSI·CEC). 칩 ID 레지스터 0x00/0x01 = `0x12 0xb2` |
| 핀 | reset GPA2[2](active low), int GPA2[0], HPD GPA2[4], **GPG1[2] `mipi_switch_hdmi` = high** |
| panel_0 | gpios `<gpg2 5>`, `<gpg1 1>`, regulator `vdd_ldo28` |
| 타이밍 | 1920×1080@60, h-porch 148/88/44, v-porch 36/4/5, 4 lanes, HS 1000 MHz, PMSK 2/308/2/45371 |

5.10 BSP에서 막힌 곳과 해결 (시험 순서대로):

| 증상 | 원인 | 해결 |
|---|---|---|
| DT 오버레이 `FDT_ERR_NOTFOUND` | SoC DT에서 `//#include "s5e3830-dpu.dtsi"`(디스플레이 전체가 주석) | 주석 해제 |
| `MIPI DSIM read Timeout`, `panel is not matched` | LK가 `panel_0`의 `ddi_id`를 **0xff**로 덮어씀(`kd101n65_get_id panel ID is 0xff`) | lcd_info id와 패널 ops id를 0xff로 |
| `panel is not matched`, `finding...`도 없음 | 오버레이 파일 안의 `/ { ... }`는 오버레이 자신의 루트라 적용되지 않음 | `&{/} { ... }` |
| 패널 id가 없을 때 NULL 접근 | `panel_list[10]` 중 2칸만 채워져 있음 | `panel_lt8912b_ops` 등록(DSI 명령 없는 빈 ops) |
| `decon0: ... command mode` | BSP DECON `psr_mode = <2>` | `psr_mode = <0>`(video) |
| DSIM probe가 중간에 반환 | 비디오 모드는 `memory-region`(fb_handover) 필수인데 rmem dtsi에서 주석 | `&{/reserved-memory}`에 `fb_handover`(0xF9800000, 40 MB) |
| `PMSK(36 4 5 0)` | `CONFIG_EXYNOS_DSIM_DITHER`라서 pmsk를 **14칸** 읽음 | 14칸으로 |
| 언더런 irq에서 NULL(`dsim_irq_handler`) | LK가 DSIM을 켜 둔 채로 넘김 → `dsim->panel` 설정 전에 irq 발생 | `if (dsim->panel && ...)` 1줄 패치 |
| `/dev/fb0` write → `ENODEV` | Exynos fb는 write 미지원 | `mmap` |
| fd를 닫으면 화면이 꺼짐 | `decon_release` → `decon_disable` | `fb-hold.py`가 fd를 열어 둔 채 대기(systemd 서비스) |
| 모니터 "No HDMI" | DSI EoT 패킷 사용 중(`eotp enabled`) | lcd_info에 **`eotp_disabled = <1>`** (메인라인 LT8912B는 `MIPI_DSI_MODE_NO_EOT_PACKET`) |

LT8912B 레지스터는 메인라인 `drivers/gpu/drm/bridge/lontium-lt8912b.c`(v6.6) 순서를 그대로 쓴다: init → lanes → mipi basic → video setup(1080p) → dds → rxlogicres → lvds. 사용자 공간에서 `i2cset`으로 쓴다(`tools/linux510/lt8912b-hdmi.sh`).

부팅하면 자동으로 켜진다(`e850-hdmi.service`):

```text
ExecStartPre = modprobe dpu
ExecStart    = fb-hold.py          # /dev/fb0 unblank + 컬러바 + 열어 둔 채 대기
ExecStartPost= lt8912b-hdmi.sh     # DSI 클록이 돈 뒤 브리지 설정
```

**결과: HDMI 모니터에 1920×1080 컬러바 출력 확인 (2026-11-01).**

```bash
# 직접 해 보기
systemctl status e850-hdmi
lt8912b-hdmi.sh status          # 칩 ID, HPD
fb-test.py                      # 다른 그림을 그리려면 fb-hold.py를 참고(mmap + FBIOPAN_DISPLAY)
```

적용 방법(커널 트리): `sh modules/e850-hdmi/apply.sh /work/kernel` → `make ... modules dtbs`.

## 9. 진행 중: HDMI 로그인 프롬프트와 OV5647 캡처

### 9.1 HDMI에 로그인 프롬프트 (VT + fbcon)

GKI 설정에는 `# CONFIG_VT is not set`이 들어 있다. 그래서 프레임버퍼 콘솔(fbcon)과 `tty1`이 없다. 아래 설정을 켜고 다시 빌드한다(전: `.config.before-vt`). Debian에는 `getty@tty1`이 기본으로 켜져 있어서, 이 커널이면 HDMI에 `e850-96 login:`이 뜰 것으로 예상한다.

```text
+ CONFIG_VT=y  CONFIG_VT_CONSOLE=y  CONFIG_HW_CONSOLE=y  CONFIG_VT_HW_CONSOLE_BINDING=y
+ CONFIG_CONSOLE_TRANSLATIONS=y  CONFIG_DUMMY_CONSOLE=y
+ CONFIG_FRAMEBUFFER_CONSOLE=y   (fb.ko 안에 fbcon.o가 들어간다. CONFIG_FB=m)
+ CONFIG_FRAMEBUFFER_CONSOLE_DETECT_PRIMARY=y  CONFIG_FONT_8x8=y (CONFIG_FONT_8x16=y는 원래 있음)
```

```bash
scripts/config --file .config -e VT -e CONSOLE_TRANSLATIONS -e VT_CONSOLE -e HW_CONSOLE \
    -e VT_HW_CONSOLE_BINDING -e DUMMY_CONSOLE -e FRAMEBUFFER_CONSOLE -e FRAMEBUFFER_CONSOLE_DETECT_PRIMARY
make ... olddefconfig && make ... Image modules dtbs      # 공통 헤더가 바뀌어 대부분 재컴파일
```

### 9.2 OV5647: 모듈 적재까지 확인

```text
root@e850-96:~# modprobe fimc-is              # 패닉 없음 (power_seq use = <1> 이후)
[@][SS1:D] is_sensor_probe(0)                  ← 우리 센서 인스턴스 1 (CSI1)
exynos-is-sensor: probe of 14400000.is_sensor0 failed with error -22   ← ERD의 다른 슬롯, 무시
root@e850-96:~# modprobe is-cis-ov5647
[@]cis_ov5647_probe: OV5647 bound to sensor instance 1, addr 0x36
/sys/bus/i2c/devices/7-0036/driver -> .../i2c/drivers/OV5647
```

센서 영상 노드: `/dev/video102` = `exynos-is-ss1` (VC 노드: video214~217 `ssdevice_idvc0~3`).

캡처를 하려면 `S_INPUT` → `S_FMT`(MPLANE) → `REQBUFS`(MMAP) → `QBUF` → `STREAMON` 순서로 호출해야 한다.

- **`S_INPUT` 값**(`CONFIG_USE_SENSOR_GROUP=y`, `is-video.h`):
  - 비트 구성: scenario[31:26] stream[25:24] **position[23:16]** **vindex[15:8]** intype[7:4] **leader[3:0]**
  - position 1(FRONT), vindex 2(`IS_VIDEO_SS1_NUM`), leader 1 → **`0x10201`**
- **센서 찾기:** `is_sensor_s_input()`은 position 1(FRONT)의 센서를 `exynos_is/vender/front_sensor_id`로 찾는다. ERD 카메라 dtsi를 OV5647 dtsi로 바꾸면서 이 노드도 빠졌다. 그래서 OV5647 dtsi에 `front_sensor_id = <SENSOR_NAME_OV5647>`(219)를 넣었다.
- **픽셀 형식:** `V4L2_PIX_FMT_SBGGR10`. 이 BSP는 한 줄을 `ALIGN(w*5/4, 16)` 바이트(packed 10-bit)로 잡는다.

다음: VT 커널과 vender DT로 다시 굽고 `v4l2-ctl -d /dev/video102 -i 0x10201 --set-fmt-video=... --stream-mmap`.

### 9.3 HDMI 텍스트 콘솔 — fbcon이 DECON에서 동작하게 하기

VT를 켠 첫 부팅에서 패닉이 났다.

```text
Console: switching to mono frame buffer device 240x67
Kernel panic - not syncing: CFI failure (target: 0x0)    fbcon_cursor ← fbcon_fb_registered
```

| 증상 | 원인 | 해결 (`modules/e850-hdmi/apply.sh`) |
|---|---|---|
| CFI failure target 0x0 | Android DECON `fb_ops`에 `fb_fillrect/copyarea/imageblit`이 없음(NULL) | `cfb_fillrect/cfb_copyarea/cfb_imageblit` 연결 |
| modpost: `cfb_*` undefined | `FB_CFB_*`는 select 전용이라 손으로 켠 값을 olddefconfig가 지움 | `EXYNOS_DPU30`이 `select FB_CFB_*` (Kconfig 패치) |
| `cfb_imageblit` NULL 쓰기 | `decon_fb_alloc_memory()`가 vmap → memset → **vunmap + `screen_base = NULL`** | 두 줄 삭제(버퍼는 `system-uncached` 힙이라 캐시 문제 없음) |

결과: HDMI에 `e850-96 login:` 프롬프트가 나온다(2026-11-01 확인). 키보드가 없어서(USB host 꺼짐) tty1도 root 자동 로그인으로 둔다(`getty@tty1.service.d/autologin.conf`). 보드 셸에서 `명령 | tee /dev/tty1`을 쓰면 결과를 모니터로 볼 수 있다.

### 9.4 OV5647 — Linux 드라이버로 칩 ID 읽기 성공

`S_INPUT(0x10201)` 첫 시도:

```text
[M219] pin_ctrl(act(1), pin(96), val(1), nm(cam_gpio high (module power enable))
exynos5-hsi2c 138c0000.hsi2c: HSI2C Error Interrupt occurred(IS:0x00000400, ...)
sensor_ov5647_cis_init: chip ID read failed (-6)
```

원인은 **CAM_VDD_33(PMIC LDO32)이 꺼져 있었던 것**이다. Android 10에서는 이미 켜져 있었는데(그래서 CAM_GPIO만 올려도 칩 ID가 읽혔음), Linux에서는 쓰는 곳이 없어서 꺼진다. `regulator_summary`에서 확인한 값은 `vdd_ldo32 state=disabled uV=2800000`이었다. 게다가 전압도 다르다. Android 10 DT는 3.3 V(`0x325aa0`), ERD PMIC dtsi는 2.8 V다.

수정 (`s5e3830-e850-ov5647-camera.dtsi`):

```dts
&l32_reg { regulator-min-microvolt = <3300000>; regulator-max-microvolt = <3300000>; };
power_seq / scenario_normal_on:
    10 vdd_ldo32 on (PIN_REGULATOR 1 2000) → 20 cam_gpio low → 30 cam_gpio high (20 ms) → 40 on_i2c
scenario_normal_off: off_i2c → cam_gpio low → vdd_ldo32 off
```

```text
[M219] pin_ctrl(act(5), pin(0), val(1), nm(vdd_ldo32)
[M219] pin_ctrl(act(1), pin(96), val(0), nm(cam_gpio low)
[M219] pin_ctrl(act(1), pin(96), val(1), nm(cam_gpio high (module power enable))
[M219] pin_ctrl(act(6), pin(0), val(1), nm(on_i2c)
sensor_ov5647_cis_init: OV5647 chip ID 0x5647            ← Linux 5.10 Pablo 드라이버에서 성공
[MOD:OV5647] sensor_module_init(0)
[SS1:D] is_sensor_s_input(in:1, sce:0)(pos:1)-0
```

다음: `S_FMT`(640×480 SBGGR10) → `REQBUFS` → `STREAMON`으로 RAW 프레임 받기.

### 9.5 OV5647 RAW 캡처 — 어디까지 왔나 (`tools/linux510/ov5647-cap.py`)

`v4l2-ctl`은 쓸 수 없다. 노드가 ENUM_FMT를 구현하지 않아 `BG10`을 거부한다. 그래서 ioctl을 직접 부르는 도구를 만들었다. 순서와 이 BSP에서 알아낸 것:

| 단계 | 결과 / 알아낸 것 |
|---|---|
| `S_INPUT` | `(scenario<<26) \| (1<<16) \| (2<<8) \| 1`. position 1, vindex 2 = SS1, leader. 전원 시퀀스와 칩 ID `0x5647` 확인 |
| `S_CTRL V4L2_CID_IS_S_SENSOR_SIZE` (0x009a1045) | 센서 모드는 S_FMT가 아니라 이 값(`(w<<16)\|h`)으로 찾는다 → `sensor mode(640x480@60) = 0 (lane:2)`, `mode_change: 640x480 RAW10` |
| `S_FMT` | 센서 노드는 group **leader**라서 버퍼 type이 `OUTPUT_MPLANE`(10)이다 |
| `REQBUFS`/`QUERYBUF` | 플레인 2개: 이미지 384000 B(640×480×10bit), 메타 51200 B |
| `QBUF` | MMAP이어도 Pablo는 플레인을 dma-buf fd로 다룬다 → `/dev/dma_heap/system-uncached`에서 할당해 `DMABUF`로 넘긴다 |
| `STREAMON` (scenario 0) | **실패**: `is_hw_camif_cfg: ischain is null`, `is_group_buffer_queue: BUG(!sensor)` |
| `STREAMON` (scenario 1, VISION = stand-alone) | **커널 Oops**: `is_groupmgr_init` ← `is_devicemgr_binding` (이 BSP에서는 stand-alone 경로가 깨져 있음) |

**원인:** OV5647은 ISP가 필요 없는 RAW 센서이고, 하드웨어 경로(CSIS → WDMA → 메모리)도 있다. 그런데 Pablo 프레임워크는 센서 그룹 뒤에 ISP 체인(ischain, 3AA)이 연결돼 있다고 가정한다(`group->device->sensor`, camif의 CSIS→IPP mux).

**다음 할 일(둘 중 하나):**

1. ischain 없이 bypass하도록 fimc-is를 최소한으로 패치한다.
   - `is_hw_camif_cfg`: ischain이 없으면 OTF mux를 건너뛴다.
   - `is_group_buffer_queue`: `group->device->sensor`가 없으면 센서 그룹의 sensor를 쓴다.
   - 그 밖에 is_sensor_start 쪽도 손봐야 한다.
2. 아니면 3AA 노드도 열어 ischain을 연결한다. ISP 펌웨어(`is_lib.bin`)가 필요할 수 있다.

DT 변경(이번에 추가): `exynos_is/vender/front_sensor_id = 219`, LDO32 3.3 V + power_seq, VISION scenario용 power_seq.

### 9.6 ISP 없이(bypass) 센서 그룹 돌리기 — `modules/ov5647-pablo/bypass.sh`

OV5647은 ISP가 필요 없는 RAW 센서다. 경로는 CSIS(MIPI 수신) → WDMA → 메모리다. 그런데 Pablo는 센서 그룹 뒤에 ISP 체인(ischain)이 있다고 가정해서, 아래 다섯 곳을 고쳤다. stand-alone(VISION) 모드는 이 BSP에서 `is_groupmgr_init`이 Oops를 내서 쓰지 않는다.

| # | 위치 | 증상 | 패치 |
|---|---|---|---|
| 1 | `is_group_buffer_queue` | `BUG(!sensor)` | `device->sensor`가 없으면 `group->sensor` |
| 2 | `is_hw_camif_cfg` | `ischain is null` → back_start -ENODEV | ischain이 없으면 CSIS→3AA OTF mux 설정 생략 |
| 3 | `is_sensor_s_input` | `is_group_start: BUG(!group->device->sensor)` | `group->device->sensor = sensor` (반대 방향 `sensor->ischain`은 NULL 유지 → ISP 펌웨어 호출 생략) |
| 4 | `is_sensor_s_input` | `is_sensor_shot` NULL(`group->head`) | 센서 그룹 혼자 `head = tail = group` |
| 5 | `tasklet_csis_str_otf` | 프레임 시작 tasklet에서 NULL+0x20 | ischain이 없으면 센서 그룹의 `smp_trigger`만 처리 |

사용자 공간(`ov5647-cap.py`)에서 알아낸 규칙:

- **`V4L2_CID_IS_S_STREAM`(0x009a100e) = 1:** STREAMON은 CSIS/DMA 쪽(back start)만 켠다. 센서 스트리밍(front start)은 이 컨트롤로 켠다.
  ```text
  Sensor Stream On ... [CSI1] settle(640x480@60) = 15, speed(275 Mbps), 2 lane ... [CSI1][VC0] size(640x480)
  sensor_ov5647_cis_stream_on: stream on        ← 프레임 카운트 F2, F3, ...가 약 16 fps로 증가
  ```
- **메타 플레인의 shot magic:** `camera2_shot_ext.shot.magicNumber`는 메타 플레인 오프셋 `0xb38 + 0xbc98 = 51152`에 있고, 값은 `0x92345678`(v8_20_0)이다. 오프셋은 `llvm-dwarfdump --name=camera2_shot_ext fimc-is.ko`로 구했다. 이 값이 없으면 `shot magic number error`로 프레임이 취소된다.
- **`STREAMON`을 먼저 하고 그다음 `QBUF`:** STREAMON 전에 큐에 넣은 버퍼는 vb2가 `start_streaming`보다 먼저 드라이버로 넘긴다. 그러면 그 shot들이 `is_group_start()`보다 먼저 실행되고, `is_group_start()`가 대기 중인 세마포어(`smp_resource`)를 `sema_init()`으로 다시 초기화한다. 그 결과 그룹 스레드가 영원히 깨어나지 못한다. `/proc/<is_gw13>/stack`의 `down_interruptible ← is_group_shot`과, 양쪽에서 찍은 세마포어 주소가 같다는 것으로 확인했다.
- **OUTPUT 큐 완료 신호:** 완료된 버퍼는 POLLOUT(쓰기 가능)으로 알린다.

**현재 상태:** DQBUF로 버퍼가 약 15 fps로 돌아온다. 그러나 이미지 플레인에는 데이터가 없다(시작 표시값 0x5a 그대로). Pablo에서 RAW(VC0)는 leader(`/dev/video102`)가 아니라 **VC 캡처 노드**(`ssdevice_idvc0` = `/dev/video214`)로 DMA된다. 다음은 그 노드를 열고, leader 메타의 `node_group.capture[]`에 VC0 요청을 넣는 일이다.

보드에 빠르게 파일을 보내는 도구(raw tty + `head -c`, 7.6 MB를 5초에 전송): 맥에서 `python3 acmpush2.py fimc-is.ko /tmp/fimc-is.ko`.

### 9.7 첫 RAW 프레임 — VC0 캡처 노드, 테스트 패턴, 실제 영상

Pablo에서 RAW(VC0)는 leader 버퍼가 아니라 **VC 캡처 노드**로 DMA된다.

- 센서 인스턴스 1의 VC0 노드는 `/dev/video214`다(`IS_VIDEO_SS1VC0_NUM` = 110 + 4 = 114, 이름은 `ssdevice_idvc0`).
- 노드 타입은 `CAPTURE_MPLANE`다.
- leader 메타의 `shot_ext.node_group`(@0x58)에서 `capture[0]`(@0x58+0x30)에 `{vid 114, request 1, crop 0,0,640,480, BG10}`을 넣는다.
- 구조체 오프셋은 모두 `fimc-is.ko`의 DWARF(`llvm-dwarfdump`)에서 읽었다.

| 단계에서 만난 문제 | 원인 | 해결 |
|---|---|---|
| `SysMMU WRITE PAGE FAULT at 0xf8dde040` (CSISX2), 이어서 `CSIS_ERR_DMA_DMAFIFO_FULL` 패닉 | VC0 DMA가 RAW10을 **픽셀당 16비트**(LE, 10비트 유효)로 쓴다. 640×480×2 = 614400 B인데 플레인은 packed 기준 384000 B | dma-buf를 플레인의 2배로 할당한다(IOMMU는 dma-buf 전체를 매핑함) |
| `csi_dma_tag`: `WARN_ON(!f_subdev)` 뒤 Oops | 프레임 시작 때 `csis_check_vc_dma_buf`가 PROCESS로 옮긴 VC0 프레임에는 태그(`frame->subdev`)가 없음 | bypass 패치 6: DMA 채널 subdev를 쓰고, leader 프레임은 알 때만 갱신 |
| 경고 백트레이스 폭주 → 워치독 리셋 | 위 경우마다 `WARN_ON(!ldr_framemgr)`가 115200 bps earlycon으로 출력됨 | 태그 없는 프레임이면 경고 생략 |
| 스트림당 VC0 3장만 나옴 | 그룹 shot 자원(`smp_resource` = async 1 + sync 2)은 leader 프레임 완료 때 반납되는데, bypass에서는 leader가 완료되지 않음(완료는 원래 3AA 몫) | 지금은 스트림당 3장으로 쓴다. 프레임 끝에서 leader를 완료시키는 패치는 SoC 멈춤을 일으켜 적용하지 않음 |
| 받은 3장이 모두 검은 화면 | 모드 테이블이 이미 스트리밍을 시작하고, 노출은 수동 `0x000200`(약 2줄)이다. 3장이 `stream_on`보다 먼저 지나감 | `is-cis-ov5647`: 모드 테이블 직후 노출 1000줄, 게인 0x20, AEC/AGC 자동(0x3503=0) 설정(모듈 인자 `aec`, 기본 켬) |

**테스트 패턴**(`modprobe is-cis-ov5647 test_pattern=1`, 0x503D = 0x80)으로 경로 전체를 검증했다. 가로 방향으로 흰·노랑·시안·초록·마젠타·빨강·파랑·검정 8색이 정확히 나온다. CSIS 수신, DMA, 16비트 해석, BGGR 순서가 모두 맞다는 뜻이다.

![OV5647 colour bars through CSIS VC0 DMA](img/ov5647-linux510-testpattern.png)

**첫 실제 프레임**(640×480, 어두운 방, AEC, 초점 조정 전). 값 범위는 0~93/1023이고 `raw10-view.py`가 자동으로 밝기를 늘렸다.

![first OV5647 frame on Linux 5.10](img/ov5647-linux510-first-frame.png)

직접 해 보기 (보드 셸):

```bash
modprobe fimc-is && modprobe is-cis-ov5647          # test_pattern=1 이면 컬러바
ov5647-cap.py -n 3                                   # /tmp/ov5647_0..2.raw (VC0, u16 per pixel)
raw10-view.py /tmp/ov5647_2.raw                      # HDMI 가운데에 2배로 표시 + /tmp/ov5647_2.ppm
```

맥으로 가져오기: `python3 tools/linux510/acmpull.py /tmp/ov5647_2.ppm out.ppm`. 보드에서 맥으로 한 번에 크게 보내면 데이터가 빠지므로, gzip 뒤 16 KiB 조각마다 md5로 확인한다.

**남은 일:**

1. leader 프레임 완료 처리(연속 스트리밍).
2. 같은 부팅에서 스트림을 여러 번 열고 닫을 때의 CSIS 오류(`CSIS_ERR_LOST_FS_VC`) 뒤 리셋.
3. 노출과 프레임 길이(VTS) 조정, 더 큰 모드(1296×972, 1920×1080).

### 9.8 연속 스트리밍과 HDMI 실시간 영상 (`bypass.sh` 6b·7·8, `ov5647-cap.py --live`)

9.7까지는 스트림 한 번에 3장에서 멈췄다. 원인은 두 가지였고, 각각 패치로 풀었다.

**① leader 프레임이 끝나지 않는다 → 패치 7.** 프레임 끝(`is_sensor_notify_by_fend`)에서 leader 버퍼를 직접 완료한다.

- 원래 코드는 `is_group_done()`을 부른다. 이 함수는 ischain 그룹과 메타데이터를 건드린다.
- bypass에서는 이 함수를 부르지 않고 꼭 필요한 두 가지만 한다.
  - shot 카운트를 올린다.
  - `gtask->smp_resource` 세마포어를 돌려준다.

**② VC0 버퍼가 다시 걸리지 않는다 → 패치 8.** ISP 없는 M2M 경로에서는 센서 **line 인터럽트**(14번째 줄)가 다음 프레임의 VC0 버퍼를 건다.

- 이 경로는 `is_sensor_notify_by_line → is_devicemgr_shot_callback → is_sensor_group_tag → VC0 s_rx_buffer` 순서로 간다.
- 그런데 leader 프레임을 `fcount == line_fcount`인 것으로 찾는다.
- bypass에서는 두 카운터가 어긋난다. 그래서 매번 `(SENSOR LINE IRQ) There's no Shot`가 나고 VC0 DMA가 꺼졌다(`DMA off [0/0/3]`).
- 패치 8은 찾지 못하면 "VC0가 아직 안 걸린, 가장 오래된 처리 중 leader 프레임"을 대신 쓴다.

**③ 1 CPU에서 인터럽트를 놓친다 → 패치 6b.** VC0 서브디바이스에는 `leader`가 없다.

- 그래서 `GET_SUBDEV_FRAMEMGR(NULL)`이 프레임마다 전체 백트레이스와 함께 WARN을 찍었다.
- CPU가 하나뿐이라 그동안 CSIS 인터럽트를 놓쳤다(`interrupt lost`, `start/end overlapped`).
- 패치 6b로 이 WARN이 나지 않게 했다.

캡처 도구도 한 군데 고쳤다. 드라이버는 VC0 프레임을 버릴 때 메타의 `capture[].request`를 0으로 지운다. 그래서 leader 버퍼를 다시 넣을 때마다(QBUF) 메타를 새로 쓴다(`write_shot()`).

결과는 다음과 같다.

```bash
modprobe fimc-is && modprobe is-cis-ov5647
ov5647-cap.py -n 30                 # 30장 연속 (파일 저장, 초당 약 1.5장: 저장이 병목)
ov5647-cap.py --live -n 200 -b 8    # HDMI 실시간: "live: 60 frames, 17.7 fps" ... 200장
ov5647-cap.py --live                # Ctrl-C까지 계속
```

`--live`의 처리 방식은 다음과 같다.

- BGGR 2×2 칸 하나를 RGB 한 점으로 바꾼다(320×240). 화면에는 4배로 키워 1280×960으로 그린다.
- 채널별 LUT에 검정 레벨, 그레이월드 화이트밸런스, 자동 밝기, 감마 2.2를 넣는다. LUT는 30장마다 다시 계산한다.
- numpy만 쓰고, `/dev/fb0` mmap에 직접 쓴다.
- CPU 하나(A55)에서 초당 15~17장이 나온다. 센서는 60 fps로 돌기 때문에 남는 프레임은 버린다.

**남은 문제:**

1. 스트림을 켜는 순간 가끔 SoC가 멈춘다(첫 스트림 4번 중 2번). watchdog이 리셋해 fastboot로 돌아오며, 1 CPU라 마지막 커널 로그가 남지 않는다. 다음 시도는 `watchdog_thresh=4`와 `softlockup_panic=1`로 스택을 남기는 것이다.
2. 같은 부팅에서 두 번째 스트림을 열면 한동안 돌다가 리셋된다.
3. 카메라 모듈이 올라간 채로 재부팅하면 종료 단계에서 멈출 수 있다. 재부팅 전에 `rmmod`하거나 fastboot에서 `fbmini reboot`로 복구한다.

### 9.9 스트림 시작 시 멈춤 해결 (`bypass.sh` 6c, `is-cis-ov5647.c`)

**증상**: 부팅 후 첫 스트림을 켜면 2번 중 1번꼴로 보드가 멈췄습니다. 로그는 `ischain is NULL` 다음에 끊기고,
하드웨어 watchdog 리셋(LK: `Abnormal RST_STAT`) 뒤 fastboot에서 멈췄습니다.
멈추지 않은 경우에도 같은 부팅에서 두 번째 스트림을 켜면 리셋됐고, 영상이 1.5 fps밖에 안 나올 때도 있었습니다.

**원인 찾기**: 멈추지 않은 경우의 UART 로그를 보면 프레임마다 다음 경고가 스택 덤프와 함께 찍혀 있었습니다.

```
WARNING: ... at drivers/media/platform/exynos/camera/is-device-csi_v4.c:747 csi_dma_tag+0x434/0x560
```

747번 줄은 패치 6이 넣은 `WARN_ON(!ldr_framemgr && frame->subdev)`입니다. 패치 8 덕분에 VC0 프레임에 tag가
붙게 되면서, 프레임이 전달될 때마다 이 경고가 발생했습니다(bypass 모드의 VC0 subdev에는 leader가 없기 때문입니다).

스택 덤프 하나는 약 3 KB이고, 115200 bps UART로 내보내는 데 0.25초가 걸립니다. 이 출력이 CSIS 인터럽트 안에서 일어나니
CPU가 1개뿐인 시스템에서는 다른 일을 할 수 없습니다. 그래서
- 영상이 1.5 fps로 떨어지고(STREAMON에서 센서 시작까지도 6.7초),
- 운이 나쁘면 watchdog keepalive 스레드가 제때 돌지 못해 **하드웨어 watchdog이 리셋**합니다.
  리셋은 panic이 아니라서 panic 메시지가 남지 않고, 그래서 "로그 없이 멈춤"으로 보였습니다.

**수정**
1. `bypass.sh` 6c: 이 WARN을 지웁니다. bypass에서는 leader가 없는 것이 정상입니다.
2. 콘솔 로그 레벨을 낮춥니다. `/etc/sysctl.d/90-lockup.conf`에 `kernel.printk = 4 4 1 7`을 넣으면
   경고 이상만 UART로 나갑니다. 전체 로그는 `dmesg`로 볼 수 있습니다.
3. `is-cis-ov5647.c`: 모드 테이블을 쓰고 나면 센서가 곧바로 영상을 내보내는데, CSIS는 0.4초 뒤 stream on 때에야 켜집니다.
   그래서 모드 설정 끝에서 센서를 stream off(MIPI LP-11, `0x4800`/`0x4202`/`0x300d`)로 돌려 두고,
   `cis_stream_on`에서 처음 출력을 시작하게 했습니다.

빌드와 설치:

```sh
# fimc-is.ko: bypass.sh(6c 포함)를 적용한 커널 소스로 modules 빌드 → 보드의 /lib/modules/$(uname -r)/ 에 복사
# is-cis-ov5647.ko: modules/ov5647-pablo/build-module.sh
```

**결과** (2026-11-01)

| 시험 | 결과 |
|---|---|
| 같은 부팅에서 스트림 4회 연속 (300 + 150×3장) | 모두 정상, `WARNING` 0개 |
| 재부팅 → 첫 스트림, 3회 반복 | 3/3 정상 (이전에는 4번 중 2번 멈춤) |
| 카메라 모듈을 올린 채 재부팅 | 3/3 정상 종료 (fastboot에 멈추지 않음) |
| HDMI 실시간 영상 | 평균 약 12 fps (30장마다 9~19 fps 사이를 오감) |

**남은 문제**: 첫 스트림을 끌 때 약 24초가 걸립니다. 드라이버가 남은 요청을 `forcely trigger`로 하나씩 끝냅니다.

## 10. Wi-Fi — 칩 안의 Maxwell(Samsung SCSC) 무선랜

Exynos 850에는 Wi-Fi/BT 코어(WLBT, "Maxwell" mx140)가 SoC 안에 들어 있다. 5.10 커널에는 드라이버가 이미 모듈로 빌드되어 있다(`scsc_mx.ko`, `scsc_platform_mif.ko`, `scsc_wlan.ko`). DT에도 `scsc_wifibt@119C0000` 노드가 있다. 없는 것은 **펌웨어**와 **사용자 공간 도구**뿐이다.

### 10.1 펌웨어: Android vendor 이미지에서 꺼내기 (`tools/linux510/wifi-fw-extract.sh`)

Android 10 백업의 `super`(mmcblk0p12)는 동적 파티션 묶음이다. 그 안의 `vendor` ext4에서 `/etc/wifi`를 꺼낸다(`mx140.bin` 1.5 MB, `mx140/conf/*.hcf`, `slsi_reg_database.bin`).

```bash
sh tools/linux510/wifi-fw-extract.sh ~/Desktop/E850_Android10_backup_20261031 out/
#  lpunpack-min.py: system [(3072000 sectors @2048)], vendor [(614400 sectors @3074048)]
#  -> out/wifi-fw.tgz  (vendor/etc/wifi/...)
```

`lpunpack-min.py`는 LP 메타데이터(slot 0)를 직접 읽는 30줄짜리 파서다. extent가 하나뿐인 선형 파티션만 다룬다.

### 10.2 드라이버가 펌웨어를 찾는 경로

`mx140_file.c`는 `request_firmware("../etc/wifi/mx140.bin")`을 부른다. 즉 `/lib/firmware/../etc/wifi`다. Debian rootfs에는 `/lib/firmware` 자체가 없어서 `..`가 풀리지 않았다(`Direct firmware load ... failed with error -2`). 다음 두 가지로 해결했다.

```bash
tar xzf wifi-fw.tgz -C /              # /vendor/etc/wifi
mkdir -p /lib/firmware /lib/etc
ln -s /vendor/etc/wifi /lib/etc/wifi
```

### 10.3 올리기 (`tools/linux510/e850-wifi.sh`)

```bash
modprobe scsc_mx && modprobe scsc_wlan    # wlan0, p2p0, wlan1 생김 (blacklist는 이름 지정 modprobe에는 안 걸림)
ip link set wlan0 up                      # 이때 mx140.bin을 내려받고 WLBT 코어를 부팅
iw dev wlan0 scan                         # 주변 AP가 보이면 성공
```

`slsi_read_regulatory: Failed to read regdb`와 `WLBTD not running` 경고는 나오지만 스캔은 된다.

사용자 공간 도구는 `wpasupplicant`, `iw`, `libnl-*`, `libpcsclite1`, `wireless-regdb`다. 보드에 아직 네트워크가 없으므로 맥의 `debian:bookworm`(arm64) 컨테이너에서 `apt-get download`로 받는다. 받은 패키지는 `acmpush2.py`로 보내 `dpkg -i`로 설치했다. 접속은 `e850-wifi.sh SSID 암호`로 한다(wpa_supplicant@wlan0 + systemd-networkd DHCP).

### 10.4 접속 성공 — DHCP와 DNS

```bash
e850-wifi.sh TP-Link_36D7 '<암호>'
#  wpa_supplicant: Associated ... WPA: Key negotiation completed [PTK=CCMP GTK=CCMP]
#  inet 192.168.0.236/24 ... dynamic wlan0      (5 GHz, ch149, -70 dBm)
ping -c 2 8.8.8.8 && ping -c 1 deb.debian.org
```

처음 시도에서 걸린 두 가지는 다음과 같다(스크립트에 반영함).

1. **`.network` 파일 권한.** 암호 파일 때문에 `umask 077`을 걸었더니 `20-wlan0.network`도 0600이 됐다. networkd는 `systemd-network` 사용자로 돌아서 `Permission denied`가 나고, wlan0이 `unmanaged`로 남았다. 암호 파일만 0600으로 만든다.
2. **DNS.** minbase rootfs에는 systemd-resolved가 없다. `/etc/resolv.conf`에는 Docker 빌드 때 쓰던 `192.168.65.7`이 남아 있었다. 게이트웨이와 `8.8.8.8`로 바꿨다.

암호는 보드의 `/etc/wpa_supplicant/wpa_supplicant-wlan0.conf`에 PSK 해시로만 남고, 저장소에는 넣지 않는다. 이제 보드에서 `apt-get install`이 바로 된다.

### 10.5 부팅할 때 자동 연결 (`tools/linux510/e850-wifi.service`)

`e850-wifi.sh SSID 암호`를 한 번 실행해 접속 정보를 저장한 뒤, 서비스를 설치합니다.
서비스는 `e850-wifi.sh up`을 실행합니다. 이 명령은 드라이버를 올리고 wlan0을 켠 다음,
저장된 네트워크(`/etc/wpa_supplicant/wpa_supplicant-wlan0.conf`)에 다시 접속합니다.
접속 정보가 없으면 서비스는 실행되지 않습니다.

```sh
cp e850-wifi.sh /usr/local/bin/ && chmod 755 /usr/local/bin/e850-wifi.sh
cp e850-wifi.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable e850-wifi
```

확인 결과: 재부팅하고 약 1분 뒤 `systemctl is-active e850-wifi` → `active`.
wlan0은 DHCP로 192.168.0.236을 받았고, `ping 8.8.8.8`이 응답했습니다.

---

## 11. 서비스(SSH·웹)와 CPU 코어 켜기

### 11.1 SSH·SFTP·웹 서버

Wi-Fi(10장)가 되면 Debian arm64 패키지를 `apt`로 바로 받는다. 커널·드라이버와 상관없는 사용자 프로그램이다.

```sh
printf 'deb http://deb.debian.org/debian bookworm main\ndeb http://deb.debian.org/debian bookworm-updates main\ndeb http://security.debian.org/debian-security bookworm-security main\n' > /etc/apt/sources.list
apt-get update && apt-get install -y systemd-timesyncd sudo nginx
useradd -m -s /bin/bash -G sudo <사용자> && passwd <사용자>   # Debian 기본값은 root 비밀번호 SSH 로그인 차단
```

| 서비스 | 확인 |
|---|---|
| SSH / SFTP (`openssh-server`, 이미 설치됨) | 맥에서 `ssh <사용자>@192.168.0.236` → `SSH-2.0-OpenSSH_9.2p1` |
| nginx | 맥에서 `http://192.168.0.236/` → HTTP 200 |
| 시간 동기화 | 보드 시계가 2026년 4월로 시작한다. timesyncd가 없으면 `apt`·HTTPS가 날짜 오류를 낸다 |

FTP(vsftpd)는 비밀번호가 평문으로 가므로 넣지 않았다. 파일 전송은 SFTP를 쓴다.

### 11.2 CPU가 1개만 켜진 이유

`/proc/cmdline`의 `maxcpus=1`은 Samsung DT bootargs에 원래 있던 값이다. 커널은 8개를 인식한다(`possible 0-7`, `online 0`). 부팅 옵션을 바꾸지 않고 실행 중에 코어를 하나씩 켜서 시험한다.

```sh
echo 1 > /sys/devices/system/cpu/cpu1/online
cat /sys/devices/system/cpu/online
```

### 11.3 CPU1 lockup — 원인은 MCT 타이머

| 시도 | 결과 |
|---|---|
| 그냥 켜기 | 몇 초 뒤 `Watchdog detected hard LOCKUP on cpu 1` → 리셋 → fastboot |
| CPU1을 바쁘게 한 상태에서 켜기 | 같음 |
| `exynos-ecc-handler` 제거(IRQ 71) | 같음 |
| `exynos_mct`·`hardlockup-watchdog` 제거, arch_timer만 사용 | **CPU1이 30초 넘게 동작, 타이머 인터럽트 934회** |

lockup 당시 CPU1의 hrtimer 인터럽트 수가 0이었다. MCT 로컬 타이머가 인터럽트를 올려도(`INTCSTAT=1`) CPU1까지 전달되지 않았다. 그래서 tick을 받지 못한 CPU1을 hardlockup 감시가 lockup으로 판정했다. ARM arch timer는 코어마다 PPI로 직접 인터럽트를 받으므로 이 문제가 없다.

반영한 것(세 군데 모두 빼야 한다):

- `tools/linux510/modules.stage1`: `exynos_mct` 줄을 주석 처리. stage1에 이름으로 따로 들어 있어서 deny 목록에 걸리지 않는다.
- `tools/linux510/autoload.deny`: `^exynos-ecc-handler`, `^exynos_mct`, `^hardlockup.watchdog` 추가. 파일 이름이 `hardlockup-watchdog.ko`(대시)라서 `.`으로 맞춘다. `vendor_boot_module_order_s5e3830.cfg`에서 온 항목도 이 목록으로 걸러진다.
- 보드 rootfs: `/etc/modprobe.d/e850-blacklist.conf`에 `blacklist exynos_mct`, `blacklist hardlockup-watchdog`, `blacklist exynos-ecc-handler`. udev가 다시 올리지 않게 한다. `build-rootfs.sh`는 `autoload.deny`로 이 파일을 만들므로 새 rootfs에는 자동으로 들어간다.

검증: 새 boot.img의 initramfs에서 `modules.load`·`modules.stage1`에 `mct|hardlockup|ecc`가 없음을 확인했다. 플래시 후 정상 부팅됐고, `lsmod`에도 셋 다 없었다.

부작용: hardlockup 감시가 없어진다. 멈춤 복구는 `s3c2410_wdt` 대신 쓰는 사용자 공간 `watchdog` 데몬에 맡긴다.

### 11.4 남은 일

1. ~~CPU2~7 켜기~~ — **해결(11.8절). 8코어 모두 동작.**
2. ~~`maxcpus=1` 빼기~~ — 11.8절에서 함께 반영.
3. ~~카메라 fps 다시 재기~~ — 11.10절. 그대로 12~16 fps.
4. ~~장시간 안정성~~ 통과(11.11). 남음: `cpuidle.off=1` 빼기는 실패(11.12, 펌웨어 문제로 보여 보류), ~~hotplug~~ 해결(11.13), ~~카메라 fps~~ 55~63 fps로 해결(11.10).
5. ~~HDMI 글자색~~ 흰색으로 해결(11.14).
6. 다음(사용자 계획, [세션 기록](SESSION-LOG-20261101-02.md) 5절): MySQL + Apache로 출입 기록(nginx와 80번 포트를 어떻게 나눌지 결정 필요), YOLO nano(ncnn/ONNX Runtime, 카메라 → HDMI).

### 11.5 보드를 USB로 연결하면 맥의 인터넷이 끊기는 문제

보드의 USB NCM 가젯은 맥에 `E850-96 Linux`(en13) 네트워크 장치로 잡힌다. 맥의 서비스 순서에서 이 장치가 Wi-Fi보다 위에 있다. 그런데 보드의 networkd DHCP 서버는 기본값(`EmitRouter=yes`)으로 자기(192.168.7.1)를 기본 게이트웨이로 알려 준다. 그 결과 맥의 모든 트래픽이 보드로 가서 인터넷과 DNS가 끊긴다(`ENOTFOUND`).

- 보드: `build-rootfs.sh`의 `10-usb0.network`에 `EmitRouter=no`를 넣었다. 이미 만든 rootfs에는 직접 넣는다.
  ```sh
  sed -i 's/^EmitDNS=no$/EmitDNS=no\nEmitRouter=no/' /etc/systemd/network/10-usb0.network && systemctl restart systemd-networkd
  ```
- 맥(바로 적용): 시스템 설정 → 네트워크 → … → 서비스 순서 설정에서 Wi-Fi를 맨 위로 올린다.

### 11.6 CPU2~4: `failed to come online` (멈춤 없음)

MCT를 뺀 이미지에서 CPU1을 켠 상태(`online 0-1`)로 시험했다.

```text
echo 1 > /sys/devices/system/cpu/cpu2/online   → rc=1
CPU2: failed to come online
CPU2: failed in unknown state : 0x0
```

CPU3·CPU4(다른 클러스터, MPIDR 0x10000)도 같다. 보드는 리셋되지 않고 CPU 0~1로 계속 동작한다.

- DT: 8개 모두 `enable-method = "psci"`이고 MPIDR은 0x0, 0x100, 0x200, 0x300, 0x10000 … 순서로 정상이다.
- `__cpu_up()`(arch/arm64/kernel/smp.c)은 PSCI `CPU_ON`이 성공을 돌려준 뒤 5초를 기다린다. 상태 0x0은 그 코어가 커널 진입점(`secondary_entry`)에 한 번도 들어오지 못했다는 뜻이다. 그러니 커널 쪽 lockup(CPU1 때의 MCT 문제)과는 다른 문제다. 펌웨어(EL3)나 전원 쪽에서 코어가 실제로 켜지지 않는다.
- exynos-cpuhp: `available cpu 0xff`, `set online cpu 0xff`, `cpu_online_mask 0x3`. Samsung hotplug 드라이버가 막고 있지는 않다.

다음에 볼 것: CPU1만 켜지는 이유(부팅 때 LK/EL3가 CPU1만 미리 준비했는지), `exynos-cpupm`의 cluster/core 전원 콜백, Android 10에서 8코어가 켜질 때의 PMU/ACPM 순서.

### 11.7 꺼진 코어에 전원이 들어가지 않는다 (`modules/cpu-pmu-enable`)

`exynos-cpupm`은 probe 때 켜져 있던 CPU에만 `cal_cpu_enable()`(flexpmu wake-up 마스크 `GRP2_INTR_BID_ENABLE` 해제)을 호출한다. `maxcpus=1`이면 CPU0만 해당된다. 보드에 `/dev/mem`이 없으므로, 나머지 CPU에 이 함수를 호출하고 PMU 상태(`cal_cpu_status`)를 찍는 작은 모듈을 만들었다.

```sh
docker run --rm -v ~/e850-a13-build:/work -v e850-kernel:/work/kernel -v e850-out:/work/out \
    -v "$PWD/modules/cpu-pmu-enable:/m:ro" e850-build-env:1 bash /m/build-module.sh
insmod cpu-pmu-enable.ko          # mask=0xfc (CPU2-7) 기본값
# cpu-pmu-enable: cpu2 status 0 -> enable 0 -> status 0
# cpu-pmu-enable: cpu4 status 1 ...   (앞서 CPU_ON을 시도한 뒤라 전원은 켜졌지만 커널에 못 들어옴)
```

| 시험 | 결과 |
|---|---|
| 부팅 후 처음 CPU1 켜기 | 성공 |
| CPU1 끄기(`psci: CPU1 killed`) → 다시 켜기 | 실패, `unknown state 0x0` |
| 부팅 후 처음 CPU2 켜기 | 실패. 그 뒤 CPU1은 성공 |
| `cal_cpu_enable()` 뒤 CPU2 켜기 | 실패. PMU 상태도 계속 0(전원 꺼짐) |

결론: 꺼져 있는 코어를 PSCI `CPU_ON`으로 켜는 경로가 동작하지 않는다. CPU1은 부트로더가 켜 둔 채(WFE 대기) 넘겨준 것으로 보인다. 그래서 첫 번째만 깨어나고, 한 번 끄면 다시 전원이 들어가지 않는다. CPU4는 전원은 켜졌지만 커널 진입점까지 오지 못했다.

다음 후보:
1. Android 10(같은 부트로더)에서는 8코어가 켜진다. Android에서 hotplug 전에 무엇이 다른지 확인한다. 예: `exynos_mct` 로드, ACPM/APM 펌웨어 준비 순서, `cpuidle.off=1` 여부.
2. EL3(BL31)의 `CPU_ON`이 APM(flexpmu)에 전원 요청을 보내는 방식(ACPM IPC 채널)이 Linux 쪽 초기화를 기다리는지 확인한다.
3. 우회: `maxcpus=1`을 빼고 부팅 때 커널이 8개를 한꺼번에 켜게 한다. 부팅 시점에는 부트로더가 준비한 상태라 될 가능성이 있다. MCT를 이미 뺐으므로 CPU1 lockup 원인은 없다.

### 11.8 해결: DT의 CPU `reg`(MPIDR)를 이 보드 BL31 방식으로 (8코어 동작)

`maxcpus=1`만 빼고 부팅해도 결과는 같았다. 그때 로그에서 원인이 보였다.

```text
CPU1: Booted secondary processor 0x0000010000      ← DT의 CPU1 reg는 0x100인데 클러스터1 코어0이 깨어남
CPU2..7: failed to come online
```

| | CPU0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
|---|---|---|---|---|---|---|---|---|
| vendor 5.10 DT (`s5e3830.dtb`) | 0x0 | 0x100 | 0x200 | 0x300 | 0x10000 | 0x10100 | 0x10200 | 0x10300 |
| Android 10 boot.img DT | 0x0 | 0x1 | 0x2 | 0x3 | 0x100 | 0x101 | 0x102 | 0x103 |

이 보드의 BL31(Android 10 시절)은 PSCI `CPU_ON`의 대상을 `클러스터<<8 | 코어`로 해석한다. 그래서 5.10 DT 값으로는 다음과 같이 된다.
- `0x100` → 클러스터1 코어0이 깨어나 논리 CPU1로 동작한다. 그래서 CPU1이 처음 한 번은 켜졌다.
- `0x200`, `0x300` → 없는 클러스터라 아무것도 켜지지 않는다(`unknown state 0x0`).
- `0x10000…` → 해석되지 않는다.

11.3~11.7절의 증상이 모두 이것으로 설명된다. CPU4의 PMU 상태만 1이었던 것도 같은 이유다.

수정(`tools/linux510/build-bootimg.sh`):
- merged.dtb의 `/cpus/cpu@000N/reg`를 Android 10 값으로 바꾼다(`fdtput`).
- 커맨드라인에서 `maxcpus=1`을 뺀다.

결과(부팅 직후):

```text
CPU1: Booted secondary processor 0x0000000100
CPU2: Booted secondary processor 0x0000000200
...
CPU7: Booted secondary processor 0x0000010300
nproc → 8, online 0-7
```

- IPI0/IPI1 카운터가 8개 CPU 모두에서 증가한다.
- 30초 동안 busy loop 8개를 돌리면 CPU 0~7에 고르게 나뉜다. lockup은 없었다.
- Wi-Fi, nginx, USB 네트워크는 그대로 동작한다.
- `modules/cpu-pmu-enable`은 진단용으로 남겨 두었다. 이제는 필요 없다.

참고: 코어가 스스로 읽는 MPIDR(0x100, 0x10000 …)은 DT 값(0x1, 0x100 …)과 다르다. 그래도 IPI는 정상이다. 아직 확인하지 않은 것: `cpuidle.off=1`을 빼도 되는지, 장시간 안정성. hotplug는 11.9절.

### 11.9 CPU hotplug(끄고 다시 켜기)는 아직 안 된다 → 11.13에서 해결

8코어로 부팅한 뒤 시험했다.

```text
echo 0 > cpu2/online   → psci: CPU2 killed (polled 0 ms)
echo 1 > cpu2/online   → CPU2: failed to come online / unknown state 0x0   (CPU5도 같음)
```

- `cpu-pmu-enable.ko mask=0x24`로 깨우기 마스크를 미리 풀어도 같다. 이때 PMU 상태는 1(전원 켜짐)이다. 즉 전원은 들어오지만 코어가 커널 진입점까지 오지 못한다.
- `kvm-arm.mode=none`을 붙여도 같다. 로그에 `KVM is not available`이 나온다. 커널이 EL1로 들어오므로 pKVM은 관련이 없다.
- 부팅 때는 부트로더가 코어를 준비해 둔 상태라 켜진다. `CPU_OFF`로 한 번 꺼진 코어를 BL31이 다시 웜부트시키는 경로가 막혀 있다.

실사용 영향: 부팅 때 8코어가 켜지고, `cpuidle.off=1`이라 코어가 꺼질 일이 없다. 그러니 hotplug를 쓰지만 않으면 문제없다. **`echo 0 > cpuN/online`은 하지 말 것.** 그 코어는 재부팅할 때까지 돌아오지 않는다. (11.13의 `e850-cpuhp.ko`가 올라가 있으면 된다.)

### 11.10 8코어에서 카메라 실시간 영상 fps

```text
ov5647-cap.py --live -n 200 -b 8
live: 60 frames, 16.0 fps / 90: 12.0 / 120: 15.8 / 150: 13.4 / 180: 14.1
```

1코어일 때(15~17 fps)와 거의 같다. `--live`는 Python 스레드 하나에서 디모자이크, LUT, framebuffer 쓰기를 모두 하므로, 코어가 늘어도 빨라지지 않는다. 더 빠르게 하려면 프레임 수신과 변환을 여러 프로세스로 나누거나(`multiprocessing`) C로 다시 짜야 한다.

**재측정과 원인 정정 (2026-11-02 03시).** 위의 "Python 한 스레드가 병목"이라는 판단은 틀렸다.

- 보드에서 `--live` 한 프레임 처리를 단계별로 쟀다. 합계가 약 27 ms(최대 약 37 fps)였다. 가장 큰 것은 4배 확대 `repeat()`(16 ms)였다. 그다음은 uncached DMA 버퍼 읽기(4.7 ms)와 LUT 세 번(3.6 ms)이었다.
- `show()`를 바꿨다. LUT를 XRGB8888 자리로 미리 밀어 둔 `uint32`로 만들고, 세 번 찾아 OR한 뒤, framebuffer를 `(행, s, 열, s)` 모양으로 보고 broadcast로 한 번에 쓴다. 이렇게 하면 LUT+확대+쓰기가 21 ms에서 10 ms로 준다. 화면 결과는 예전과 바이트 단위로 같다(Mac에서 numpy로 비교).
- 그런데 fps는 그대로였다(바꾸기 전 11.5~20, 바꾼 뒤 11.1~16.2). UART 로그의 `[bypass] FS`(프레임 시작) 간격이 대부분 60~65 ms였다. **센서가 초당 약 16장만 내보낸다.** 드라이버 로그에는 `sensor mode(640x480@60)`라고 나오지만, 실제 OV5647 PLL/VTS 설정(`0x3034=0x1a, 0x3035=0x21, 0x3036=0x46`)이 그만큼 빠르지 않다.
- 그래서 더 빠르게 하려면 CPU 쪽이 아니라 센서 레지스터(PLL, HTS/VTS)를 고쳐야 한다. 30 fps 단위로 16과 11.4가 번갈아 나오는 것은 가끔 프레임을 놓치는 것이다. 원인은 아직 모른다.
- 덤으로 알아낸 것: `rmmod fimc_is` 뒤에는 `modprobe fimc-is`가 `Device or resource busy`로 실패한다. 카메라 모듈을 다시 올리려면 재부팅해야 한다. 이 보드에서 `systemctl reboot`는 LK가 `Abnormal RST_STAT: 0x1000000`으로 보고 fastboot에 들어간다. Mac에서 `fastboot reboot`를 하면 정상 부팅한다.

**센서 16 fps의 원인과 해결 (2026-11-02 04시): 이제 55~63 fps.**

- 스트리밍 중에 I2C로 센서 레지스터를 읽었다(`/root/ovreg.py 7 380e 380f ...`, I2C_RDWR라 드라이버가 잡고 있어도 된다). VTS(0x380e/f)가 리셋값 `0x7b0`(1968줄)이었다. 55 MHz ÷ (1852 × 1968) ≈ 15 fps라서 관측과 맞는다.
- mainline `ov5647.c`는 VTS를 모드 표가 아니라 vblank 컨트롤로 쓴다. 그런데 모드 표만 옮긴 Pablo 드라이버에는 그 부분이 없었다. 고친 내용: `sensor_ov5647_cis_mode_change()`가 모드 표 다음에 `ov5647_modes[mode].vts`를 0x380e/f에 쓴다(VGA는 `0x1f8`).
- 그런데 VTS만 고치면 오히려 **4~5 fps**가 된다. 커널 명령줄의 `loglevel=8 ignore_loglevel` 때문에 카메라 드라이버의 프레임마다 나오는 메시지(`[bypass] FS`, `There's no Shot` 등)가 인터럽트 안에서 115200 baud UART로 나간다. 그러면 CSIS가 인터럽트를 놓친다(`interrupt lost`, `start/end overlapped`). 이렇게 망가진 뒤에는 같은 부팅에서 다음 스트림이 프레임 0장이 된다.
- 그래서 `ov5647-cap.py`가 실행되는 동안만 `/sys/module/printk/parameters/ignore_loglevel`을 `N`으로, 콘솔 loglevel을 1로 바꾸고, 끝나면(`atexit`) 원래대로 돌려놓는다. 메시지는 dmesg에는 그대로 남는다.
- 결과(8코어, `ov5647-cap.py --live -n 600 -b 8`을 연속 두 번): **63.1 fps, 55.9 fps.** 두 번째 실행도 정상이다. 같은 조건에서 VTS를 고치기 전은 콘솔이 조용해도 16.0 fps였다(그때는 16과 11.4가 번갈아 나오던 흔들림도 없어졌다).
- 보드에는 새 `is-cis-ov5647.ko`(strip 후 md5 `968bfa3d...`)를 `/lib/modules/$(uname -r)/`에 넣었다. 원래 모듈은 `/root/is-cis-ov5647.ko.orig`에 있다.
- 덤: 이번에는 `systemctl reboot`가 fastboot에 들어가지 않고 바로 다시 부팅한 적도 있었다(두 번 중 한 번). 리셋 원인은 아직 모른다.

### 11.11 장시간 안정성 (통과)

- 대기 상태: 8코어로 부팅한 뒤 45분 동안 LOCKUP, Oops, RCU stall이 없었다. 온도는 33~35℃였다.
- 1시간 부하 시험: 2026-11-02 00:41(KST)에 시작했다. 보드의 `/root/stress1h.sh`가 busy loop 8개를 돌리고, 1분마다 loadavg, online CPU, 온도를 `/root/stress1h.log`에 남긴다. 끝나면 `DONE`이 찍힌다.
- 결과(00:41~01:41 KST): 통과. `DONE 16:41:17`(UTC)까지 60번 기록이 모두 `on=0-7`이었고 loadavg는 8.0~8.7이었다. UART 로그에 LOCKUP, RCU stall, Oops, BUG, panic이 없었다.
- 온도: 5분 만에 85℃에 이르렀고, 그 뒤 83~87℃(최고 87℃)를 유지했다. 세 번째 센서는 최고 72℃였다. 부하를 멈추자 49~50℃로 내려갔다. 방열판이 없어 87℃는 여유가 적다. 계속 부하를 줄 때는 방열을 보강해야 한다.

### 11.12 `cpuidle.off=1` 빼 보기 (2026-11-02 04시): 실패, 부팅 초반에 멈춤

8코어 이미지(`boot-nokvm`)에서 명령줄의 `cpuidle.off=1`만 뺀 `boot-idle`을 구웠다. 빌드는 `tools/linux510`을 복사해 sed로 그 인자만 지웠다.

- 결과: **1.93초에 멈췄다.** UART 마지막 줄은 내장 드라이버 initcall(`usbhid: USB HID core driver`)이었다. 그 뒤로 로그가 늘지 않았고 USB도 나타나지 않았다.
- 워치독 데몬(`watchdog -T 30`)은 initramfs 1단계에서야 켜진다. 그래서 이번 멈춤은 리셋으로 바뀌지 않는다. **전원을 껐다 켜야** 한다. 그러면 LK가 비정상 리셋으로 보고 fastboot에 들어간다(7.12). 그때 `boot-nokvm`을 다시 굽는다.
- 7.17(1코어)에서는 initramfs가 끝나고 첫 idle에서 멈췄다. 8코어에서는 CPU1~7이 부팅 직후부터 idle이라 훨씬 일찍 멈추는 것으로 보인다. 다만 이 시점에는 exynos cpuidle 드라이버(모듈, 4.2초)가 아직 없다. 그러니 idle은 WFI뿐이어야 한다. `cpuidle.off=1`이 있을 때와 무엇이 다른지는 아직 모른다. 한 번만 시험했으므로 재현성도 확인하지 않았다.
- 복구(04:40): 전원을 껐다 켜자 LK가 fastboot에 들어갔다. 미리 띄워 둔 대기 루프(`until fastboot devices | grep -q fastboot; ...`)가 `boot-nokvm`을 다시 구웠다(`fastboot reboot`가 `usb_read failed`를 내도 재부팅은 된다). 다시 8코어, `cpuidle.off=1`로 정상이다.
- hotplug(11.9)와 같은 뿌리로 보인다. 둘 다 코어가 전원을 끄거나 깊은 idle에 들어간 뒤, Android 10 시절 BL31이 그 코어를 다시 깨우는 경로를 거친다.
- 결론: 당분간 `cpuidle.off=1`을 유지한다.

**두 번째 시도 (2026-11-02 05~06시): C2를 허용하면 들어간 코어가 깨어나지 않는다.** 실험 코드는 저장소에 넣지 않았다(Mac `~/e850-logs/e850-cpuhp-c2-experiment.c`).

| 구성 | 결과 |
|---|---|
| `cpuidle.off=1` 제거 + DT C2에 `wakeup-latency-us = 0xf0000000`(governor가 고르지 않음) | 부팅 정상, `psci_idle` 드라이버 동작, WFI만 사용. 1.9초 멈춤은 C2(PSCI `CPU_SUSPEND` 0x10000, 코어 전원 끔) 때문이었다 |
| 모듈로 latency를 230 µs로 되돌림 + cpu_pm notifier에서 hotplug와 같은 PMU 쓰기(INT_EN bit3 포함) | CPU3~7이 C2에서 돌아오지 않음. `rcu_preempt kthread starved ... ->cpu=3`: C2에서 arch timer가 멈춘다 |
| + DT에 `local-timer-stop`(broadcast는 `bc_hrtimer`) | 타이머 문제는 사라졌지만, CPU4가 다른 코어로 보낸 IPI(`smp_call_function_single`)에서 영원히 기다림 → soft lockup |
| notifier 제거(PMU 쓰기는 vendor `exynos-cpupm` 훅만), CPU7 또는 CPU6~7만 허용 | 허용된 코어가 C2에 들어간 뒤 돌아오지 않음(`workqueue lockup - pool cpus=7`). `usage`는 C2에서 나올 때 오르므로 계속 0이었다 |

- Android 10 DT의 C2도 같은 PSCI 값(0x10000)과 latency를 쓴다. 다만 `compatible = "exynos,idle-state"`라서 4.14의 Samsung 전용 cpuidle 드라이버가 처리했다.
- Linaro의 Exynos850 hotplug 시리즈 v7 소개글(2026-09-30): "As a next steps it will be possible to enable idle states on top of it (if we get our hands on correct firmware)." Linaro도 지금 펌웨어로는 E850-96의 idle 상태를 켜지 못했다. 사용자 정리 문서는 [E85096-C2-CPUIDLE-REFERENCE-20261102.md](E85096-C2-CPUIDLE-REFERENCE-20261102.md).
- 하지 않은 시험: C2 진입 직전에 GIC-400 CPU 인터페이스(GICC_CTLR.Enable)를 끄기. 커널 GICv2 드라이버는 `CPU_PM_ENTER`에서 레지스터를 저장만 한다.
- 결론: C2는 펌웨어(BL31/APM) 문제일 가능성이 크다. 보드는 `boot-nokvm`(`cpuidle.off=1`)으로 되돌렸다. C2를 막은 부트 이미지(`~/e850-logs/boot-c2t`)는 `cpuidle.off=1` 없이도 부팅되지만 얻는 것이 없다(WFI만 쓰므로 같다).
- 덤으로 알게 된 것: 멈춘 상태에서도 워치독 데몬이 살아 있는 CPU에서 계속 신호를 보낼 수 있다. 그래서 리셋까지 몇 분이 걸리거나 전원을 껐다 켜야 할 수 있다. 다시 시험한다면 `s3c2410_wdt.tmr_atboot=1`(커널이 워치독을 부팅 때부터 켬)도 함께 넣어, 멈춰도 사람 손 없이 fastboot로 돌아오게 한다.

### 11.13 해결: CPU hotplug (`modules/e850-cpuhp`)

mainline에 Linaro의 Exynos850 hotplug 패치가 올라와 있다([PATCH v5 4/6] "soc: samsung: exynos-pmu: add Exynos850 CPU hotplug support", Alexey Klimov, 2026-06, E850-96에서 시험). vendor 5.10 `exynos-cpupm`과 비교하면 두 가지가 다르다.

1. `CLUSTERx_CPUy_INT_EN`(PMU 0x11860000 + 0x1044 + cluster×0x400 + core×0x80)의 **bit 3**을 끌 때 1, 켤 때 0으로 바꾼다. vendor의 `flexpmu_cal_cpu_s5e3830.h`에서는 바로 이 줄들이 주석으로 막혀 있다.
2. 켜기 쪽(INFORM 지우기, GRP2 wake-up 마스크 해제와 pending 지우기)을 **`CPU_ON` 전에** 부팅 CPU에서 한다(`CPUHP_BP_PREPARE_DYN`). vendor는 새 코어가 이미 돌기 시작한 뒤에(`CPUHP_AP_ARM_TWD_STARTING`) `cal_cpu_enable()`을 부른다. 그런데 코어는 그 전 단계를 넘지 못한다.

`modules/e850-cpuhp/e850-cpuhp.c`가 이 두 가지를 cpuhp 콜백으로 넣는다. 끄기는 `CPUHP_AP_ONLINE_DYN` teardown(꺼지는 코어에서 `CPU_OFF` 전)이고, 켜기는 `CPUHP_BP_PREPARE_DYN`이다. 레지스터는 `ioremap`으로 직접 쓴다.

```text
e850-cpuhp: cpu2 offline: inform 1 int_en fcc status 1 grp2_en 4
psci: CPU2 killed (polled 0 ms)
e850-cpuhp: cpu2 before on: inform 10001 int_en ffc status 0 grp2_en 4    ← 꺼진 동안 전원 0, BL31이 INFORM에 0x10000을 더함
CPU2: Booted secondary processor 0x0000000200 [0x410fd051]
```

시험(`/root/hp.sh 30 300`): CPU1~7 전부 끄고 켜기 30회 + 무작위 한 코어 토글 300회. 1분 19초 걸렸고 **실패 0**, `failed to come online` 0, 끝난 뒤 `online=0-7`.

설치: 보드의 `/lib/modules/$(uname -r)/e850-cpuhp.ko` + `/etc/modules-load.d/e850-cpuhp.conf`(부팅 때 자동 적재). `build-rootfs.sh`도 `ext-e850-cpuhp`가 빌드돼 있으면 같은 설정을 넣는다. 빌드:

```sh
docker run --rm -v ~/e850-a13-build:/work -v e850-kernel:/work/kernel -v e850-out:/work/out \
    -v "$PWD/modules/e850-cpuhp:/m:ro" e850-build-env:1 bash /m/build-module.sh
```

다음: `cpuidle.off=1`(11.12)도 같은 깨우기 경로를 쓴다. C2 idle에 들어가기 전과 나온 뒤에도 같은 PMU 쓰기가 필요할 것이다. mainline 시리즈도 idle은 "후속 작업"으로 남겨 두었다.

### 11.14 HDMI 텍스트 콘솔 색 (흰 글씨) (`modules/e850-fbpal`)

명령줄 `vt.default_*`로 기본 글자색(7번)을 흰색으로 정했는데도 화면 글자가 진한 파랑으로 나왔다. framebuffer 픽셀을 직접 읽어(`/root/fbhist.py`: fb0 mmap의 색 히스토그램) 원인 셋을 찾았다.

1. fb0의 `fix.visual`이 부팅 때 **MONO01(0)**이다. fbcon은 붙을 때 한 번만 컬러/흑백을 정하므로 흑백 모드가 된다. 흑백 모드에서는 ANSI 색을 무시하고 "보통 = 1번, 굵게 = 2번" 색으로 그린다(측정: 모든 색이 0xaa0000, 굵게는 0xaa00).
2. DPU의 `decon_setcolreg()`는 DECON이 꺼짐이나 hibernation이면 팔레트 쓰기를 버린다. 그래서 콘솔이 팔레트를 다시 넣어도(`ESC ] R`) 반영되지 않는다.
3. pseudo-palette는 VGA 순서(1 = 파랑, 4 = 빨강)다. ANSI 순서인 `default_red/grn/blu`를 그대로 넣으면 빨강과 파랑이 바뀐다.

`e850-fbpal.ko`는 32bpp RGB면 `fix.visual = TRUECOLOR`로 고치고, `default_*`를 VGA 순서로 바꿔 fb0의 pseudo-palette에 직접 쓴다. `e850-hdmi.service`가 LT8912B 설정 뒤 이 모듈을 올리고 fbcon을 다시 붙인다(`vtcon1/bind` 0 → 1).

결과(재부팅 후): 검정 배경(0x000000), 흰 글씨(0xffffff). `\033[31m/32m/34m`이 각각 0xaa0000, 0xaa00, 0xaa로 나와 색 순서도 맞다. 화면을 눈으로 보는 확인은 사용자에게 부탁한다.

**정정(11.15):** 위의 "결과"는 fb0 메모리만 잰 값이다. 그때 HDMI 출력은 꺼져 있었다. 사용자는 이 설정으로 흰 글씨를 본 적이 없다.

### 11.15 색을 바꾼 뒤 HDMI가 꺼져 있던 문제: `decon_release()` (2026-11-02 07시)

**증상.** 11.14 이후 HDMI에 아무것도 나오지 않았다. 서비스는 active였다. fb0 메모리에는 흰 글자가 있었다(`fbhist.py`). LT8912B 칩 ID도 정상이었다.

**원인(UART 로그로 확인).**

```
e850-fbpal: fb0 palette loaded
sh: decon_release → decon_disable → dsim_disable → panel power(0)   ← echo 0 > vtcon1/bind
Console: switching to colour frame buffer device 240x67             ← echo 1 > vtcon1/bind
decon_pan_display: decon0 state(5), UNBLANK missed
```

- vendor `decon_release()`는 open 수를 세지 않는다. **fb0가 닫힐 때마다** DECON, DSIM, 패널 전원을 끈다. fbcon unbind도 여기에 들어간다.
  - 그래서 `fb-hold.py`가 fd를 쥐고 있어도 rebind 순간 출력이 꺼졌다.
  - `fbhist.py`나 `ov5647-cap.py --live`처럼 fb0를 열었다 닫는 프로그램도 화면을 끈다.
  - 그동안 "마지막 fd를 닫으면 꺼진다"고 적었는데, 이 설명은 틀렸다.
- DECON이 꺼져 있을 때 fbcon이 한 pan은 버려진다(`UNBLANK missed`). DECON을 다시 켜도 누군가 pan을 하기 전에는 fb0가 화면에 나가지 않는다.
  - 측정: unblank만 했을 때 사용자 화면은 검정이었다.
  - 다른 프로세스에서 `FBIOPAN_DISPLAY`를 하자 흰 글자가 잠깐 보였다. 그 프로세스가 fd를 닫는 순간 다시 꺼졌다.

**고친 것.**

| 파일 | 변경 |
|---|---|
| `modules/e850-hdmi/apply.sh` | `decon_core.c`에 `decon_open()`(`.fb_open`)을 추가하고 open 수를 센다(`atomic_t decon_fb_users`). `decon_release()`는 마지막 사용자일 때만 끈다. fbcon도 `fb_open`/`fb_release`를 짝으로 부른다 |
| `tools/linux510/e850-hdmi.service` | fbpal 적재와 fbcon rebind를 `ExecStartPost`에서 `ExecStartPre`로 옮겼다(`fb-hold.py`가 fb0를 열기 전) |
| `tools/linux510/fb-hold.py` | unblank 직후 `FBIOPAN_DISPLAY`를 한다 |

`dpu.ko`는 initramfs 1단계 모듈이 아니다(`autoload.deny`). 서비스가 rootfs에서 올린다. 그래서 모듈만 다시 빌드했다(`kb34.log`). boot.img는 그대로다.

**결과(재부팅 후).**
- 부팅 때 fbcon unbind에서 DECON이 한 번 꺼진다. 그때 사용자가 fbcon 하나뿐이라 정상이다. 그 뒤 `fb-hold.py`가 다시 켠다.
- `fbhist.py`를 두 번 실행했다. 로그에는 `decon_release +`만 있고 `decon_disable`은 없다.
- **사용자가 HDMI 화면에서 흰 글씨를 눈으로 확인했다.**

**확인하지 않은 것.**
- `ov5647-cap.py --live`를 실행했다 끝낸 뒤의 화면
- HDMI 케이블을 다시 꽂았을 때
- DECON hibernation 뒤의 색
- LT8912B HPD(`0xc1` bit7): 화면이 나올 때도 `not connected`로 읽혔다. 이 레지스터 판정은 믿을 수 없다.

### 11.16 콘솔 색 근본 수정: dpu.ko에서 visual과 팔레트 (2026-11-03)

11.14의 `e850-fbpal`과 fbcon rebind를 없애려고 dpu 드라이버 두 곳을 고쳤다(`modules/e850-hdmi/apply.sh`).

| 위치 | 원래 동작 | 고친 것 |
|---|---|---|
| `decon_core.c` 창 생성 | `fix.visual`을 정하지 않는다(0 = MONO01). 값은 `decon_set_par()`에서만 정해지는데, 이 함수는 DECON이 꺼져 있으면 그냥 return한다. fbcon이 붙을 때 DECON은 꺼져 있다 | `fbinfo->fix.visual = FB_VISUAL_TRUECOLOR`(32bpp 고정) |
| `decon_dsi.c` `decon_setcolreg()` | DECON이 OFF이면 쓰기를 버린다. 매 호출마다 `printk("@@@@ ...")`를 한다 | OFF 검사와 printk, 쓰지 않게 된 `decon` 변수를 뺐다. pseudo-palette(`u32[16]`)는 메모리일 뿐이라 하드웨어를 건드리지 않는다 |

- `e850-hdmi.service`에서 fbpal 적재와 fbcon rebind 줄을 뺐다.
- 빌드는 `kb36.log`이고, 경고는 없다.
- 새 dpu.ko의 md5는 `4512e825…`다. 11.15판은 `/root/dpu.ko.opencnt`에 남겼다.

**결과(재부팅 후, 2026-11-03).**
- `Console: switching to colour frame buffer device 240x67`: 부팅 때부터 색 모드로 붙는다. 전에는 mono였다.
- `e850_fbpal`은 적재되지 않았다.
- 부팅 뒤 `decon_disable`이 없다. 전에는 rebind 때 한 번 꺼졌다.
- tty1에 `\033[31m RED \033[32m GREEN \033[34m BLUE \033[0m WHITE`를 쓰고 fb0 메모리를 쟀다. 결과는 0xaa0000, 0x00aa00, 0x0000aa, 0xffffff, 배경 0x000000이다. 색 순서도 fbcon이 맞게 바꾼다.

**결과(전원을 껐다 켠 뒤, 2026-11-06).**
- dmesg에서 `colour frame buffer device`가 찍혔다.
- `@@@@` 0건, `decon_disable` 0건이다.
- 사용자가 HDMI 화면에서 흰 글씨를 확인했다.

**확인하지 않은 것:** DECON hibernation 뒤의 색, 케이블을 다시 꽂았을 때, `ov5647-cap.py --live`를 끝낸 뒤. OV5647은 DVS로 교체되어 마지막 항목은 시험할 수 없다.

### 11.17 DVS(S5KRC1S) I2C 통신 확인 (2026-11-06, 재부팅 없음)

OV5647을 떼고 DVS S5KRC1S를 CN4에 연결했다. 22핀→15핀 Raspberry Pi FPC로 2-Lane 연결이고, 센서는 내부 클록을 쓴다. 드라이버를 만들기 전에 I2C만 확인했다. 참고 자료는 연결/포팅 가이드 슬라이드와 `modules/dvs/README.md`(Linux 7.2.8, 2026-10-07)다.

**버스(관찰).** CN4 13·14번(I2C4) = HSI2C2 = 핀 gpc1-4/5(`hsi2c2-bus`) = `138c0000.hsi2c` = Linux **`i2c-7`**. OV5647 때 쓰던 버스와 같다. DT의 `7-0036`(OV5647) 노드는 남아 있지만 fimc_is를 올리지 않아 바인딩된 드라이버는 없다.

**전원(관찰).** CAM_VDD_33(`vdd_ldo32`)은 `disabled`(3.3 V 설정, 사용자 0)였다. RESET 후보인 CN4 12번(MCLK2/GPC0[2], legacy GPIO 110)은 아무도 잡지 않은 입력 상태였다. 이 상태에서 `i2cdetect -y -r 7` 결과는 응답 없음이었다.

**보드에 한 것.** `modules/dvs-power` 모듈을 만들었다(`/root/dvs-power.ko`, 자동 적재하지 않음).
- insmod: `regulator_get(NULL,"vdd_ldo32")` enable → RSTN Low 10 ms → High
- rmmod: RSTN Low → LDO 끔
- MCLK는 내보내지 않는다.

**결과.**
```text
dvs-power: vdd_ldo32 on (3300000 uV), rstn gpio 110 high
i2cdetect -y -r 7   → 0x10, 0x36 응답 (전원 끄면 둘 다 사라짐)
0x10 reg16 0x0000..0x000f = 모두 0x00   (S5K 계열의 흔한 ID 위치지만 여기선 0)
0x10 0x303B: 0xAA 쓰기 → 0x2A, 0x55 쓰기 → 0x55   (10-07 기록과 같음), 끝나고 0x00으로 되돌림
0x10 0x3915 = 0x24, 0x3001.. = 0x27 0x20 0x00 0x03 0x07 0xd0 ...  (0이 아닌 값 다수)
0x36 reg16 0x300a, 0x0000.. 및 무주소 읽기 = 모두 0x00
```

**판단.**
- I2C 주소 0x10에서 레지스터를 쓰고 되읽을 수 있다. 제어 통신은 확인했다.
- **CHIP_ID는 확인하지 못했다.** S5KRC1S 레지스터 맵이 없고, 0x0000은 0이다.
- 0x36의 정체는 모른다. DVS 모듈 전원으로 켜지는 다른 장치로 보이지만 가설이다.
- RSTN 극성과 22핀 18번 ↔ 15핀 12번 연결은 직접 재지 않았다. RSTN을 Low로 둔 상태에서 응답하는지도 시험하지 않았다.
- rmmod 뒤 gpc0-2는 출력 Low(CON=1, DAT=0)로 남는다. 재부팅하면 원래 입력 상태로 돌아간다.

### 11.18 S5KRC1S Pablo CIS 드라이버: OV5647과 같은 ISP 우회 경로 (2026-11-06, 부팅 시험 전)

**매뉴얼에서 확인한 것.** 참고 자료는 `S5KRC1S_센서_user_manual.pdf`(사용자 제공, 해석본)다.
- Table 3: RSTN Low = Hardware Standby, RSTN High = Software Standby(I2C 가능). RESET은 **active-low**다.
- Table 4: `SEL_MODE_r` 0x3000[1:0]은 0=SW standby, 1=Monitor, 2=Stream. `MODE_SELECT_r` 0x0100[0]은 RO다. 11.17에서 0x3000을 읽은 값 0x00은 SW standby라는 뜻이다.
- CCI는 최대 400 kHz, MIPI CSI-2는 4-lane(레인당 최대 2.5 Gbps)이다. 960×720, RAW2와 32비트 압축 프레임 형식을 쓴다.
- **칩 ID 레지스터는 해석본에 없다.** OTP에 칩 ID가 있다는 설명만 있다.

**드라이버.** 커널 트리에 있던 S5KRC1S 브링업 드라이버를 `modules/s5krc1s-pablo`로 가져와 고쳤다. 바꾼 내용은 그 README의 표에 있다.
- 핵심 변경: RSTN을 GPG1[5]에서 GPC0[2]로 옮겼다. MCLK를 끄고 LDO32 3.3 V를 켠다. `front_sensor_id` = 42와 VISION 시나리오를 넣었다. probe에서 `__init`을 빼고 `module_i2c_driver`로 바꿨다.
- ISP 우회(bypass) 패치(`modules/ov5647-pablo/bypass.sh`)는 이미 fimc-is에 들어 있어 그대로 쓴다.
- setA, 0x3915 2-lane, 모드(RAW8 8192×127, 1600 Mbps/lane)는 바꾸지 않았다. 측정하지 않은 값이다.

**빌드와 보드 변경.**
- `apply.sh`로 커널 트리(볼륨 `e850-kernel`)의 보드 dts include를 OV5647에서 S5KRC1S로 바꿨다. dtbs를 다시 빌드했다.
- 부트 이미지는 `~/e850-logs/boot-dvs/boot.img`다. 저장소 빌드라 `kvm-arm.mode=none`은 없다(커널이 무시하는 인자). merged.dtb 차이는 카메라 노드뿐이다. 전원 순서의 GPIO가 gpc0 2를 가리키고, LDO32가 3.3 V이고, `front_sensor_id`가 0x2a인지 확인했다.
- 보드의 `/lib/modules/$(uname -r)/is-cis-s5krc1s.ko`를 새 판(md5 `2cd773b7…`)으로 바꿨다. 원본은 `/root/is-cis-s5krc1s.ko.orig`다. 지금 구워진 DT에는 이 센서 노드가 없어서 재부팅 전에는 영향이 없다.

**아직 하지 않은 것.** boot-dvs 굽기와 재부팅(사용자 허락 필요). 그다음 `modprobe fimc-is && modprobe is-cis-s5krc1s`, S_INPUT(0x10201)으로 SEL_MODE 응답을 확인하고, CSI 프레임 수신을 시험한다.

### 11.19 boot-dvs 부팅, 드라이버 동작, CSI 수신 없음 (2026-11-06)

**보드에 한 것.**
- `boot-dvs`를 구웠다. 처음에는 fastboot 대기 루프의 `sleep`이 막혀 fastboot 창을 놓쳤고, 워치독이 다시 리셋해 정상 부팅됐다. 두 번째에 구웠다.
- 부팅 정상. nginx, ssh, e850-hdmi, e850-wifi 모두 active다.
- `/proc/cmdline` 앞에는 `kvm-arm.mode=protected`가 붙는다. 이미지 밖에서 붙는 값이다.
- `/usr/local/bin/ov5647-cap.py`에 `--raw8` 옵션(SBGGR8, 8비트)을 더했다. 이전 판은 `/root/ov5647-cap.py.bak`이다.
- `modules/csis-peek`을 만들었다. CSIS 레지스터를 한 번 출력하고 `-EAGAIN`으로 적재를 끝내는 모듈이다. 이 커널에는 `/dev/mem`이 없다.

**관찰.** 실행한 명령은 `modprobe fimc-is && modprobe is-cis-s5krc1s`와 `ov5647-cap.py --raw8 -W 8192 -H 127`이다.
```text
cis_s5krc1s_probe: S5KRC1S bound to sensor instance 1, addr=0x10          (7-0010 → S5KRC1S)
[M42] pin_ctrl vdd_ldo32 → sen_rst low (pin 110) → sen_rst high → on_i2c
sensor_s5krc1s_cis_init: S5KRC1S 960x720 DVS, SEL_MODE(0x3000)=0x00
sensor mode(8192x127@30) = 0 (lane:2); 0x3915=0x06; CSI2_DI=0x2a ... 되읽기 모두 설정값
[CSI1] settle = 8, speed(1600 Mbps), 2 lane; [VC0] DMA format(2a) (8192, 127)
sensor_s5krc1s_cis_stream_on: stream ON → SEL_MODE=0x02
is_sensor_dtp: DTP detected        (300 ms 안에 frame start 없음) → VC0 프레임 0
```
- 스트리밍 중 I2C로 읽은 값: `0x30A0=0x01`(전역 설정이 들어감), `0x30A2=0x11`, `0x30A3=0x1f`, `0x3000=0x02`.
- CSIS 오류 인터럽트는 0건이다.
- CSIS1(0x14410000) 스트리밍 중 레지스터:
  - `CMN_CTRL=0x4901`, `PHY_CMN_CTRL=0x7`(클록 레인 + 데이터 2레인 켬)
  - `INT_SRC0/1=0`, `FRM_CNT_CH0=0`
  - **`PHY_STATUS=0xf1`을 10 ms 동안 계속 유지**: STOPSTATECLK=1, STOPSTATEDAT=0xF, ULPS 없음
- 스트리밍 중 `0x3915`를 0x26으로 바꿔도 결과가 같았다. 0x26은 MIPI_CK_MODE=10b(클록 항상 켬)이고, 가이드 `DVS_S5KRC1S_I2C_Initialization_Operation_Guide.md` 11절의 권장값이다.

**판단.**
- 센서 쪽은 I2C, 전원, RESET, 모드 전환까지 동작한다.
- CSIS는 클록 레인에서 HS를 한 번도 보지 못했다. 센서 MIPI 송신부가 HS 클록을 내지 않거나, 클록 레인이 SoC까지 오지 않는다.
- 가설 1(유력): PLL과 출력 클록이 설정되지 않았다.
  - setA는 "vendor 전체 초기화 표가 아님"이라고 스스로 적고 있다.
  - 매뉴얼 블록도에는 "PLL & Clock generator via External Clock"이 있다. RPi5 계획 문서의 데이터시트 추출본에는 EXTCLK 6–64 MHz, PLL reference 6–12 MHz가 있고, "PLL divider 표는 vendor table 필요"라고 되어 있다.
  - CN4 12번은 RSTN으로 쓰므로 SoC MCLK를 줄 수 없다. 모듈에 발진기가 있는지("Internal Clock")는 확인하지 않았다.
- 가설 2: 22→15 케이블의 클록 레인(8·9번) 배선 문제. 도통은 재지 않았다.
- STOPSTATEDAT=0xF는 리셋값과 같아서, 선이 끊겨 있어도 이렇게 보일 수 있다. 이 값만으로 둘을 구분할 수 없다.

**다음.**
- vendor init 시퀀스(PLL/출력 PLL, "Delta_10_2000FPS")를 확보한다.
- 모듈의 클록원(발진기 유무)을 확인하고, 케이블 8·9번 도통을 잰다.
- 그 전에 소프트웨어로 해 볼 수 있는 것은 4-lane/1-lane 설정(0x3915[1:0])과 Monitor 모드뿐이다. 클록 레인 상태를 바꿀 근거는 없다.

### 11.20 vendor setfile(Delta_10_2000FPS)로 MIPI 수신 시작, CSIS overflow 패닉 (2026-11-06)

**자료.** 라즈베리파이 5용 드라이버 패키지 `~/Documents/S5KRC1S_DVS_MIPI_Driver_RPi5_20260821.tar.gz`와 저장소 `philipdekim-OnD01/DVS.Decoder.RP5.01`을 참고했다.
- vendor setfile `Delta_10_2000FPS.txt`(83줄)가 들어 있다. setA에 없던 PLL P/M/S(0x3070–0x3073), PLL 분주(0x3079–0x307B), PLL_PD(0x307E), D-PHY PLL PMS(0x30A5–0x30A7), 클록 enable(0x3080/81/83)이 모두 있다.
- RPi5 저장소의 `sensor_registers_before_key.txt`에는 동작 중 레지스터 값이 있다. `0x3915=0x04`(4-lane), `0x3901=0x2a`, `0x3910=0x81`, `0x391A=0x7f`다.
- 저장소에는 `modules/s5krc1s-pablo/setfile/`로 넣었다.

**0x36의 정체.** setfile의 `0x3091=0x6C`(CCI_ADDRESS_CONTROL_2ND)를 7비트로 바꾸면 0x36이다. 11.17의 덤프에서도 0x3091은 0x6c였다. 0x36은 센서 자신의 두 번째 CCI 주소다.

**실패한 실험: 스트리밍 중에 setfile 쓰기.** setA로 스트림을 켠 뒤 standby로 내리고, Delta 전체와 0x3915=0x06 또는 0x26을 쓰고 다시 stream을 켰다. 쓰기는 들어갔다(`0x3080=0x3f`, `0x3915=0x26` 되읽음). 그래도 PHY_STATUS는 0xf1, 프레임은 0이었다.

**드라이버 변경(`modules/s5krc1s-pablo`).**
- setA Global을 Delta 전체로 바꿨다. RO인 0x0100은 뺐다.
- `cis_set_global_setting`에서 setfile을 쓰기 전에 500 ms 기다린다. RPi5 도구가 RSTN High 뒤 0.5 s를 기다리기 때문이다.
- set_two_lane은 `0x3915[1:0]`만 바꾼다. setfile 0x24에 2-lane 비트를 더해 0x26(클록 항상 켬)이 된다.

**결과(재부팅 후 첫 스트림, ssh `dmesg -w`로 Mac에 기록: `~/e850-logs/dvs-dmesg-w.log`).**
```text
sensor_s5krc1s_cis_set_global_setting: Delta_10_2000FPS written
sensor_s5krc1s_set_two_lane: 0x3915=0x26 (2-lane)
stream ON (0x3000=0x02)
[bypass] FS G13 ... fcount 1          ← CSIS frame start
[CSI1][VC0][F1] DMA on [3/1/0]
[WRN][CSIS1] start/end overlapped
is_sensor_dtp: DTP detected
Sensor Stream Off ...
Kernel panic - not syncing: CSIS error!! CSIS_ERR_OVERFLOW_VC     (ksoftirqd/0)
```
- **처음으로 CSIS가 MIPI 프레임을 받았다**(frame start, DMA on, start/end overlapped).
- 스트림을 끄는 시점에 VC overflow가 나고, `OVERFLOW_PANIC_ENABLE_CSIS`(v8_20_0 is-config.h)가 패닉을 냈다. `panic=0`이라 자동으로 재부팅되지 않는다.
  - 첫 번째(11:28 UTC)에는 워치독이 리셋하지 않아 사용자가 전원을 껐다 켰다.
  - 두 번째는 워치독이 리셋했고 보드가 fastboot에 들어갔다.
- `/proc/last_kmsg`에는 LK 로그만 있고 커널 로그는 없다.

**조치.** `apply.sh`에서 v8_20_0의 `OVERFLOW_PANIC_ENABLE_CSIS`를 `#undef`로 바꿨다. v8_10_0과 v8_1_0도 같은 방식으로 꺼 두었다. fimc-is.ko는 다시 빌드한다.

**조치 후 결과(같은 부팅, 재부팅 없음. 새 fimc-is.ko와 is-cis-s5krc1s.ko를 modprobe).**
- 캡처 1(`ov5647-cap.py --raw8 -W 8192 -H 127 -n 3 -b 4 -B 8 -t 3`): 패닉 없이 보드가 살아 있었다. **사용자 공간에 온 VC0 프레임은 0개다.**
  - dmesg: `[CSI1][VC0][F1] DMA on`이 한 번 나온 뒤 CSIS 프레임 카운터가 약 3초 동안 F38까지 올랐다. DMA done은 한 번도 오지 않았다.
  - stop 시점: `CSIS_ERR_VRESOL_MISMATCH`, `DMAFIFO_FULL`, `OVERFLOW_VC`, `start/end overlapped`.
  - 가설(측정 안 함): 센서의 timeout flush(0x3910 bit7)가 프레임을 127줄보다 짧게 끊어 CSIS가 세로 해상도 불일치로 본다. RPi5 기록에서는 프레임이 약 21줄이다.
- 캡처 2: `-t 6`으로 시작하고 1.3초 뒤 `i2ctransfer -f -y 7 w3@0x10 0x39 0x10 0x01`(timeout 끔)을 썼다.
  - 로그는 `[F1] DMA on`(2123.4 s)까지만 있다. 캡처 중에는 콘솔 printk를 꺼서 그 뒤 기록이 없다.
  - **약 2130 s부터 CPU0(sugov:0)과 CPU1(kworker/u16:3)에서 soft lockup이 났다.** ping, ssh, ACM 모두 응답이 없다. 워치독 데몬이 살아 있어 6분이 넘도록 리셋되지 않았다. 전원을 다시 넣어야 한다.
  - 원인이 timeout을 끈 쓰기인지 stop 경로(overflow 처리)인지는 확인하지 못했다. 패닉을 끈 뒤로는 overflow가 패닉 대신 반복되고 있을 수 있다.

### 11.21 프레임 주기를 1000 FPS로 낮추는 시도, 스트림 중 soft lockup 재발 (2026-11-06)

**사용자 요청.** 2000 FPS는 너무 빠르니 1000 FPS로 받는다.

**드라이버 변경.** 매뉴얼 본문에는 0x391x(OUTIF timeout) 정의가 없다. vendor setfile 주석에 따르면 `to_scnt0`(0x3911=0x7C)이 1 µs, `to_scnt1`(0x3912/13=0x03E7, 999)이 1 ms다. setA의 `to_scnt1`을 1999(0x07CF)로 두 배 늘렸다. 이 값이 프레임 주기를 정한다는 것은 **가설**이다. 측정은 아직 못 했다.
- 모듈은 `build-module.sh`(외부 모듈)로 빌드했다. md5 e296fa2f…다. 보드의 `/lib/modules/.../is-cis-s5krc1s.ko`를 바꿨다. 이전 모듈은 `/root/is-cis-s5krc1s.ko.2000fps`에 있다.
- 빌드 함정: 트리 안 단일 타깃(`make .../is-cis-s5krc1s.ko`)으로 빌드하면 안 된다.
  - PATH에 clang이 없으면 syncconfig가 실패하고 `include/generated/autoconf.h`를 지운다. clang을 PATH에 넣고 다시 돌리니 새로 생겼다.
  - clang이 있어도 modpost가 fimc-is 심볼을 찾지 못한다.

**결과.**
- 캡처 1(`-t 3`): 보드가 살아 있었다. 결과는 이전과 같다. F1에서 DMA on, F38에서 `OVERFLOW_VC`, `DMAFIFO_FULL`, `DMA_ABORT_DONE`, `VRESOL_MISMATCH`가 났다. `frame was done with error` 뒤 VC0 NDONE과 CANCEL이 나왔고 사용자 공간에 온 프레임은 0개다.
  - 2000 FPS에서도 3초에 F38이었다. 따라서 이 숫자는 센서 프레임 수가 아니라 드라이버 쪽 카운트로 보인다(가설). 실제 프레임 속도는 아직 모른다.
- 캡처 2(`-t 4`): 1.5초와 2.5초에 `csis-peek.ko`를 insmod해 FRM_CNT(0x500)를 두 번 읽으려 했다.
  - 로그는 stream ON(475.6 s)에서 끝났다.
  - 약 500 s부터 CPU0(sshd)에서 soft lockup이 났다. 다시 전원을 넣어야 했다.

**정리.** lockup은 두 번 났다. 두 번 다 스트림 도중 무언가를 한 캡처였다(I2C 쓰기, insmod). 아무것도 하지 않은 3초 캡처는 두 번 모두 살아 있었다. 원인은 아직 모른다.
- soft lockup 줄에 스택이 없다. 캡처 스크립트가 콘솔 printk를 끈 상태에서 멈췄기 때문이다.
- 그래서 `ov5647-cap.py`에 `--loud`(printk를 끄지 않음)를 추가했다. 다음 시험은 `--loud`로 해서 UART에서 멈춘 위치를 본다.

**IRQ 측정(재부팅 후 첫 캡처, `-t 3`, 스트림 도중에는 `/proc/interrupts`만 두 번 읽음. 하드웨어 접근 없음).**
- IRQ 141(GIC hwirq 174 = SPI 142, DT의 `is_sensor1@14410000` interrupt-name `csi`, CSIS1 메인 인터럽트)이 CPU0에서 **스트림 시작 후 1.2 s 안에 280,796번** 왔다. 1.2 s와 2.2 s 사이에는 늘지 않았다.
- IRQ 142(SPI 145, VC DMA)는 4번이다.
- 약 30만 번/s는 프레임 단위(2000 FPS)가 아니라 줄 단위(2000 FPS × 127줄 ≈ 25만)나 오류 인터럽트가 반복되는 규모다(가설).
- 보드는 살아 있었다. 앞의 두 lockup이 모두 CPU0에서 났으므로, 이 CSIS 인터럽트 폭주가 원인 후보다(확인 안 함).
- 결과는 이전과 같다. F48에서 오류 프레임, 사용자 공간 프레임 0개.

### 11.22 CSIS IRQ 폭주의 원인과 수정, DVS 이벤트 데이터가 메모리에 들어옴 (2026-11-06)

**자동 복구 준비(사용자 외출, 전원을 다시 넣지 않아도 되게).** 보드에 `/etc/sysctl.d/90-e850-autoreboot.conf`를 추가했다.
- 설정: `kernel.softlockup_panic=1`, `kernel.panic=5`. soft lockup → 패닉 → 5초 뒤 리셋.
- sysrq `c`로 시험했다. 패닉 후 자동 재부팅됐다. 그 부팅의 34.7 s에 eMMC(cqhci) `dma_unmap_sg`→`dcache_inval_poc` Oops가 나서 다시 패닉했고, LK fastboot(`Abnormal RST_STAT`)로 들어갔다. `fastboot reboot`로 정상 부팅했다.
  - 같은 Oops는 uart.log에 전에도 3번 있었다(이번 이전 2번). 원인은 조사하지 않았다.
- 어느 쪽이든 Mac에서 `fastboot reboot`로 복구할 수 있다.

**IRQ 원인 측정: `modules/csis-irqstat`.** kretprobe로 `csi_hw_g_irq_src()`가 해석한 irq_src만 센다. CSIS 레지스터는 건드리지 않는다.
- 3초 캡처 결과: ISR 238,427번 중 오류가 있는 것이 238,427번이다.
  - `err_id` bit 16(**VRESOL_MISMATCH**): 238,245번
  - bit 4(OVERFLOW): 8,266번
  - FS 39, FE 38
- 소스 원인: `is-hw-csi-v1_2.c`의 `csi_hw_g_irq_src()`가 VC별 오류 상태(`ERR_LOST_FS/FE` 0x6xx, `ERR_VRESOL` 0x624, `ERR_HRESOL`)를 읽기만 하고 되쓰지 않는다. 이 상태가 남아 있어 link IRQ가 계속 걸린 것으로 본다. v5_x도 같다. 보통 센서에서는 VRESOL 오류가 나지 않아 드러나지 않았다.

**수정: `modules/s5krc1s-pablo/patches/csis-v1_2-err-clear.patch`.** `apply.sh`가 적용한다. fimc-is.ko를 다시 빌드했다(d092d0de…). 보드의 이전 모듈은 `/root/fimc-is.ko.nopanic`에 있다.
1. 읽은 뒤 위 네 레지스터에 읽은 값을 되쓴다(clear).
2. VRESOL mismatch를 오류로 치지 않는다. DVS 프레임 길이는 이벤트에 따라 달라진다.

**결과 1 (패치만).**
- IRQ가 **28만 → 267번**으로 줄었다.
- VC0 버퍼 3개가 사용자 공간에 왔다. 모두 ERROR(flags 0x4040)였다.
- 모든 IRQ에 OVERFLOW(bit 4)가 있었다. dmesg에는 프레임마다 `OVERFLOW_VC`, `DMAFIFO_FULL`, `DMA_ABORT_DONE`, `start/end overlapped`가 나온다. 프레임 간격은 10~60 ms다.
- devfreq는 CAM 650000(최저), INT 100 MHz(최저), MIF 676 MHz였다. 로그에는 `dvfs table is NOT selected`가 있다.

**결과 2 (devfreq 최대: `cat max_freq > min_freq`, mif/int/cam).** 재부팅하면 원래대로 돌아간다.
- **OVERFLOW가 사라졌다.** 이제 오류는 bit 1(**CRC**) 277/277번이다.
- **frame 0은 ERROR 없이 왔다(flags 0x4000).** frame 1과 2는 ERROR였다. 모두 `bytesused 0`이다(bypass 경로가 채우지 않음).
- 프레임 파일은 Mac의 `~/e850-logs/dvs-frames-20261106/`에 있다.

**데이터 확인: `tools/host/dvsdecode.py`.** 32비트 big-endian으로 읽고 매뉴얼 Figure 49/50 형식으로 해석한다.
- frame 0: 첫 줄만 데이터다. 순서는 ref_ts(`08000003`) → sub_ts(`088002ad`) → COLUMN(`04200000`, 첫 열 플래그) → EVENT GROUP이다. 알 수 없는 워드는 0개, 이벤트는 21,181개, 열 89개다.
- frame 1/2: 각각 127줄과 39줄이 차 있다. 3~5%는 표에 없는 워드다.
  - 대부분 `0000532f`, `00005330`처럼 값이 늘어나는 type 000000 워드와 type 000101 워드다. IMU처럼 디코더가 아직 모르는 패킷으로 보인다(가설).
  - frame_end의 프레임 번호가 섞여 있는 곳이 있다. CRC 오류로 데이터가 깨졌는지는 아직 모른다.
- 열 주소 최대값이 1783이다. 매뉴얼(0~959)보다 크다. 원인은 확인하지 않았다.
- **센서의 DVS 이벤트가 MIPI → CSIS → DMA → 사용자 공간까지 처음으로 왔다.**

**남은 것.**
- 프레임마다 CRC 오류가 있다(레인 속도/settle, 2-lane). 이 때문에 ERROR 플래그가 붙는다.
- devfreq를 매번 손으로 올려야 한다.
- 디코더가 type 000000(값 있음)과 000101을 모른다.
- 1000 FPS(`to_scnt1`)의 효과를 아직 측정하지 못했다.

### 11.23 CRC 오류 조사: 레인 속도/settle 파라미터, 패킷 구조 분석 (2026-11-06)

**도구.**
- `modules/s5krc1s-pablo/patches/csi-speed-param.patch`: fimc-is에 `e850_csi_speed`, `e850_csi_settle`(0이면 DT 값)을 추가했다. `/sys/module/fimc_is/parameters`에서 바꾸면 다음 stream on부터 적용된다. fimc-is.ko 7f47a2a8…다. 이 모듈을 올리느라 재부팅을 한 번 했다.
- `tools/board/dvscap.sh [speed] [settle]`: devfreq 최대, 모듈 적재, 파라미터 설정, csis-irqstat, 3프레임 캡처를 한 번에 한다.

**배경(PHY 드라이버).** `phy-exynos-mipi.c`는 speed가 1500 Mbps 이상이면 deskew(skew_cal_en)를 켜고 settle clock 선택을 바꾼다.

**측정.** 센서는 항상 1.6 Gbps(setfile)로 보낸다. 바꾼 것은 수신 쪽 설정뿐이다.

| speed / settle | 버퍼 ERROR 플래그 | CRC가 있는 ISR |
|---|---|---|
| 1600 / 8 (DT, deskew 켬) | 3개 중 2개 | 275/275, 137/137 |
| 1499 / 7(자동), 8, 5, 10, 14 (deskew 끔) | 3개 중 1개 | 약 55% (206~262/375~447) |

settle 값은 결과에 영향이 없었다.

**패킷 구조 분석(`dvsdecode.py`와 즉석 스크립트, 프레임은 `~/e850-logs/dvs-frames-20261106/`).**
- 매뉴얼 표에 없는 워드는 type 000000(0이 아님, 94%)과 000101(6%) **두 종류뿐**이다.
- "MGROUP/SGROUP의 bit 31이 빠진다"는 가설은 **틀렸다**. 그렇다면 offset 7~31(실제 그룹의 약 35%)도 정의되지 않은 type으로 보여야 하는데 하나도 없다.
- SGROUP 주소(7비트)가 89를 넘는 경우도 없다. 무작위 비트 오류로 보기 어렵다.
- 정의되지 않은 워드의 비율은 캡처 안에서 프레임 0 → 2로 갈수록 커진다(0% → 2.6% → 5.8%). 1499에서는 더 높다(5% → 9% → 19%).
  - CRC ISR 비율은 1499 쪽이 낮다. 두 지표의 방향이 반대다. 아직 설명하지 못했다.
- 참고: RPi5 저장소(`DVS.Decoder.RP5.01`)의 production 경로는 compressed packet이 아니라 **RAW2**(2비트 픽셀 래스터, 960×720, 열당 180바이트)다. 이 type들의 해석은 거기에도 없다.
- 데이터시트(REV0.01)에 따르면 compressed 형식의 최대 fixed scan rate는 1355 fps다. system PLL `24 MHz × 157 / 3 = 1256 MHz`, ÷10 = event clock 125.6 MHz로 맞는다. 따라서 내부 클록은 24 MHz다(계산에서 나온 결론).

**판단.**
- CRC 오류의 원인(신호 품질, 2-lane 1.6 Gbps, 센서 CRC 계산 방식)은 아직 모른다.
- 정의되지 않은 패킷이 데이터 손상인지 문서에 없는 패킷인지도 확정하지 못했다.
- DT 기본값(1600)은 그대로 두었다. 파라미터 기본값은 0이라 동작이 바뀌지 않는다.

### 11.24 실제 프레임 주기 측정, CSIS 오류 한도 해제, HDMI에 DVS 이벤트 live 표시 (2026-11-06)

**프레임 주기(관찰).**
- 측정 방법: 캡처(`dvs-frames-20261106/s1499/ov5647_1.raw`)에서 FRAME END 직후의 ref(ms)·sub(µs) 타임스탬프 차이를 쟀다. 단위는 데이터시트 6.5를 따른다.
- 결과: 프레임 간격이 **494 µs(약 2024 fps)**로 일정하다.
- 보드의 CIS 모듈(e296fa2f…)에는 `0x3912 = 0xCF`(to_scnt1 = 1999)가 실제로 들어 있다. 그런데도 1000 fps가 되지 않았다. 가설: to_scnt1은 프레임 주기를 정하는 레지스터가 아니다.
- 진짜 FRAME END는 `0x0c03c800`, `0x0c03d000` …처럼 frame# [18:11]이 하나씩 는다. bit 24가 1인 `0x0d……` 워드는 FRAME END가 아닌 것으로 보인다. `dvsdecode.py`는 아직 이 둘을 같은 것으로 센다.
- 238/750 µs가 번갈아 나오는 구간도 있다. 이 구간에서는 sub 타임스탬프가 기대값보다 정확히 256 작다. **bit 8이 0으로 빠진 것**으로, 특정 비트 위치의 오류다. CRC 오류와 관계가 있는지는 확인하지 못했다.

**스트림이 몇 프레임 만에 멈추던 원인(관찰 → 수정).**
- 증상:
  - `ov5647-cap.py -n 20`도 VC0 2~11프레임 뒤 멈췄다(csis-irqstat FS 21).
  - 그전까지는 3프레임만 받아서 드러나지 않았다.
- dmesg 순서:
  1. 프레임마다 `CSIS_ERR_CRC` / `DMA_ABORT_DONE`이 나고 `frame was done with error`가 찍힌다.
  2. 이어서 `csi_err_handle` → `csi_free_irq`에서 `Trying to free IRQ 142 from IRQ context!` WARN이 난다.
  3. 그 뒤로 CSIS 인터럽트가 0개다.
- 원인: `is-device-csi_v4.c csi_err_handle`은 오류 프레임이 `CSI_ERR_COUNT`(10)개가 되면 `csi_hw_disable` + `free_irq`를 한다. S5KRC1S는 모든 프레임에 CRC가 있어서 이 한도에 바로 걸렸다.
- 수정: `patches/csi-err-stop-param.patch`(apply.sh에 추가)로 `fimc_is.e850_csi_err_stop`을 만들었다.
  - 기본값은 10이라 vendor 동작 그대로다.
  - 0이면 멈추지 않는다.
  - 오류 버퍼는 지금처럼 ERROR 플래그를 달고 사용자 공간에 온다.
- 보드에 바꾼 것: fimc-is.ko a2149bb7…(이전 판은 `/root/fimc-is.ko.prev-11.23`). 적용하려고 소프트웨어 재부팅을 한 번 했다. fastboot에 들어가지 않고 바로 부팅됐다.
- 결과: `e850_csi_err_stop=0`이면 스트림이 계속 간다. 60초에 VC0 211프레임을 받았고, 그 뒤 10분 넘게 계속 돌았다.
- 처음에는 "그리기가 느려 VC0 버퍼가 바닥나서 멈춘다"고 봤다. 이 가설은 틀렸다. 화면을 그리지 않는 캡처 모드에서도 똑같이 멈췄다.

**HDMI live 표시(`ov5647-cap.py --live --raw8`, 새 `DvsLive` 클래스).**
- 표시 방식:
  - 버퍼의 32비트 BE 패킷을 numpy로 한 번에 디코딩한다.
  - x는 COLUMN [10:0]이다.
  - y는 그룹 주소 × 8 + 비트다. SGROUP은 [7:0]이 ON, [15:8]이 OFF다. MGROUP은 group1 / group1 + offset이고 극성은 bit 16/17이다.
  - 960×720 이벤트 영상을 fb0 가운데에 그린다. ON은 초록, OFF는 빨강이다.
  - Mac에서 assert 자체 시험을 통과했다.
- 성능(보드 측정): 버퍼 하나(1 MB) 디코딩에 210~270 ms, 그리기에 33 ms가 든다. uncached dma-buf 복사는 7 ms뿐이라 병목이 아니다.
- 그래서 캡처 루프는 버퍼를 복사·0으로 지우기·다시 넣기만 하고, 디코딩·그리기는 스레드가 가장 최근 버퍼로 한다. bytesused가 0이라, 지우지 않으면 지난번 패킷이 다시 보인다.
- 측정: 버퍼가 초당 약 3.5개 들어오고 화면은 초당 1.5번 바뀐다. 아무도 없는 정지 장면에서 버퍼당 이벤트는 400~700개다.
  - CRC 메시지가 약 2 ms마다 찍히므로, 127줄짜리 MIPI 프레임 하나에 약 250 ms가 걸린다.
  - 가설: 패킷 길이가 8192바이트로 고정이라, 이벤트가 적으면 줄이 천천히 찬다. 11.22의 초당 약 40프레임은 이벤트가 많았던 때다.
- SIGTERM을 KeyboardInterrupt로 바꿨다. `&`로 띄운 프로세스는 SIGINT를 무시하기 때문이다. `pkill -TERM -f 'ov5647-cap.py --live'`로 stream off까지 정상으로 끝나는 것을 확인했다.
- 실행 방법(root):
  - 먼저 devfreq mif/int/cam의 min을 max로 올린다.
  - `modprobe fimc-is; modprobe is-cis-s5krc1s; echo 0 > /sys/module/fimc_is/parameters/e850_csi_err_stop`
  - `(setsid nohup ov5647-cap.py --live --raw8 -W 8192 -H 127 -n 0 -b 8 -B 16 -t 3 >/tmp/dvslive.log 2>&1 </dev/null &)`
- 화면 확인: fb0는 `read()`하면 0바이트가 나온다. mmap으로 읽어야 한다(scratchpad `fbgrab.py`). 받은 화면에는 정지 장면의 잡음 이벤트가 점으로 보였다.
- 부작용 확인:
  - CRC printk는 dmesg에만 쌓이고 UART로는 나가지 않는다.
  - journald 사용량은 30초 동안 14.2M 그대로였다.
  - devfreq를 최대로 둔 채의 발열·전력은 재지 않았다.

### 11.25 HDMI live 갱신 속도 개선: MIPI flush 타임아웃, 데이터 부분만 디코딩 (2026-11-06)

**원인(데이터시트 + 측정).**
- 0x3911–0x3914는 프레임 주기가 아니라 **MIPI FIFO flush 타임아웃**이다. fine count 0x3911 = 1 µs, coarse count 0x3912/13, 0x3914 = 1번이면 flush한다.
- 11.21에서 1000 fps를 노리고 to_scnt1을 1999로 바꾼 것은 이 타임아웃을 2 ms로 늘린 것이었다. 프레임 주기는 그대로 494 µs다(11.24).
- 이벤트가 적은 장면에서는 패킷이 타임아웃마다 하나씩 나간다. 127패킷짜리 MIPI 프레임(VC0 버퍼 하나)에 약 254 ms가 걸렸다. CRC 메시지 간격 2 ms가 바로 이것이다.
- 이 탓에 버퍼가 초당 3.5개만 들어왔다. 화면은 디코딩(버퍼당 250 ms)에도 막혀 초당 1.5번 갱신됐다.

**바꾼 것.**
- setA의 to_scnt1을 vendor 값 999(1 ms)로 되돌렸다.
- CIS 모듈에 param을 둘 추가했다. 0이면 setfile 값이고, mode change(stream on)마다 setfile 뒤에 쓴다.
  - `is_cis_s5krc1s.packets`: 0x391A/B
  - `is_cis_s5krc1s.flush_us`: 0x3912/13
- 보드에 바꾼 것: is-cis-s5krc1s.ko 54365f52…(이전 판 `/root/is-cis-s5krc1s.ko.1999`)를 rmmod/modprobe로 교체했다. 재부팅하지 않았다.
- `DvsLive` 변경:
  - 버퍼 끝의 0을 잘라 데이터가 있는 부분만 넘긴다.
  - 그리기 사이에 들어온 버퍼를 모두 이어서 한 번에 디코딩한다. 건너뛰는 버퍼가 없다. 4 MB를 넘으면 최신 것만 쓴다.

**측정(정지 장면, 아무도 없음).**

| 설정 | VC0 버퍼/s | 화면 갱신/s | 버퍼당 데이터 |
|---|---|---|---|
| 11.24 (flush 2 ms, 버퍼 1개씩 1 MB 디코딩) | 3.5 | 1.5 | 1 MB 전체를 풂 |
| flush 1 ms (vendor) | 9.6 | 약 9 | 4~8 kB |
| **flush_us=250** | 13.4 (그리지 않는 캡처 모드에서도 14) | **10.8** | 4~9 kB |

- `packets=16`으로 프레임을 짧게 하면 VC0 프레임이 **하나도 오지 않았다**(3초 타임아웃). 가설: CSIS VC0 DMA는 설정된 127줄을 다 받아야 프레임을 끝낸다. `packets`는 0으로 되돌렸다.
- 250 µs에서 실제 패킷 간격은 약 0.5 ms로, 스캔 주기 494 µs와 비슷하다.
  - 가설: 스캔 프레임 하나에 패킷이 최대 하나씩 나간다. 그래서 flush를 더 줄여도 빨라지지 않을 것이다. 100 µs 이하는 시험하지 않았다.
  - 더 빠르게 하려면 DT 센서 모드의 줄 수(8192×127)를 줄여야 한다. 이는 부트 이미지를 다시 만드는 일이다.
- 버퍼가 차는 시간은 이벤트 양에 따라 달라진다. 11.22처럼 이벤트가 많으면 이미 초당 약 40개였다. 움직이는 장면에서 화면 속도를 다시 재야 한다.

**실행.** 11.24의 명령 앞에 `echo 250 > /sys/module/is_cis_s5krc1s/parameters/flush_us`를 넣는다. param은 재부팅하면 0(setfile)으로 돌아간다.

### 11.26 32줄 모드 시험: System MMU fault 패닉, eMMC 파티션 인식 실패 (2026-11-06)

**배경.** 11.25에서 센서 패킷 수만 32나 16으로 줄이면 VC0 프레임이 0개였다. 그래서 줄 수를 세 곳에서 함께 바꾸는 시험을 했다.
1. DT에 `mode1`(8192×32)을 추가했다. `mode0`(8192×127)은 그대로 두었다.
2. CIS는 모드별 줄 수 표(`sensor_s5krc1s_mode_lines` = {127, 32})로 패킷 수와 `cur_height`를 정한다. setfile은 하나를 같이 쓴다.
3. 캡처는 `-H 32`로 한다.

**보드에 바꾼 것.**
- `boot` 파티션에 `~/e850-logs/boot-dvs2/boot.img`를 구웠다. DTB 차이는 mode1 추가뿐이다(디컴파일 diff 8줄). cmdline은 boot-dvs와 같다.
- is-cis-s5krc1s.ko 01dd3c98…로 교체했다. 이전 판은 `/root/is-cis-s5krc1s.ko.mode0only`다.

**결과(관찰).**
- 첫 `-H 32` 캡처는 `/dev/video102` open에서 EMFILE("already open")가 났다. 모듈을 올린 직후였다. 장치를 연 프로세스는 없었다.
- 두 번째 캡처는 stream on 뒤 F2에서 **`Unrecoverable System MMU Fault!!` 커널 패닉**이 났다(irq/85-14550000). panic=5로 리셋된 뒤 전원을 건드리지 않고 다시 부팅됐다.
  - 캡처하는 동안 콘솔 printk를 꺼 두어서 UART에 모드·크기 로그가 없다.
  - dmesg 링 버퍼는 watchdog keepalive 메시지(0.2초마다 5줄)로 금방 덮인다.
  - 가설: CSIS DMA가 256 kB(32줄) 버퍼 밖에 썼다. 예컨대 센서가 32줄보다 많이 보냈는데 VRESOL 검사를 꺼 두어서 멈추지 않았을 수 있다. 확인하지 않았다.
- 그다음 부팅은 5초에 `mmc0: cqhci: error IRQ ... cmd error -110`이 났다. eMMC 파티션 표를 읽지 못해(`/dev/mmcblk0p*` 없음) initramfs 셸(`/ #`)에 머물렀다.
  - ACM에서 `reboot -f`를 하니 정상으로 부팅됐다.
  - 이 eMMC 오류는 새 이미지와 관계없는 간헐 현상으로 보인다(11.22 등에서도 cqhci Oops). 확인하지는 않았다.
- 같은 boot-dvs2 + 새 CIS에서 기존 `-H 127` live는 정상으로 동작했다.

**판단.** 32줄 모드는 패닉 위험이 있어 멈췄다.
- 상자 측정처럼 패킷 안의 µs 타임스탬프로 계산하는 용도에서는 버퍼 도착 지연(약 70~130 ms)이 정확도에 영향을 주지 않는다. 그래서 줄 수 단축이 필수는 아니다.
- 다시 시험한다면 다음 순서로 한다.
  1. `--loud`로 콘솔 printk를 켠다.
  2. 실제 줄 수를 센서 레지스터 되읽기로 확인한다.
  3. DMA 버퍼를 127줄 크기로 할당한 채 32줄만 받게 한다(넘쳐도 버퍼 안에 머물도록).

### 11.27 C 디코더·렌더러(libdvsview.so)로 HDMI 30 fps, 버퍼에 센서 시간의 약 14%만 들어옴 (2026-11-06)

**바꾼 것.**
- `tools/linux510/dvsview.c` → `/usr/local/lib/libdvsview.so`를 추가했다(Debian 12 arm64 컨테이너에서 빌드, 명령은 파일 머리에 있다).
  - `ov5647-cap.py --live --raw8`는 이제 V4L2 제어만 한다.
  - 버퍼마다 `dvs_ingest()`를 ctypes로 부른다. 호출 중에는 GIL이 풀린다.
  - C 쪽은 dma-buf를 복사하고, 디코딩하고, 쓴 부분만 0으로 지운다. 이벤트 (t, x, y, 극성)는 4M개짜리 링에 넣는다.
- 렌더 스레드는 `--fps`(기본 30)마다 **센서 타임스탬프 기준으로 한 프레임 주기만큼의 이벤트**를 그린다.
  - 그리는 시각은 실제 시간에서 playout delay를 뺀 시점이다. delay는 버퍼마다 잰 "가장 오래된 이벤트가 보이게 된 시각"으로 정한다.
  - 타임스탬프는 0~100 ms 앞으로 가는 것만 받는다(CRC 비트 오류 대비). 네 번 연속 어긋나면 새 기준으로 삼는다.
- numpy 디코더(DvsLive.events)는 지웠다.

**측정.**
- 정확성: 같은 캡처(`s1499/ov5647_1.raw`, 1 MB)에서 C 디코더도 **345,509개 이벤트**로 numpy와 정확히 같다. self-test(SGROUP/MGROUP, 타임스탬프 거부)도 통과했다.
- 디코딩 속도(보드 A55): **5.2 ms/버퍼**(1 MB, 이벤트 34만 개). numpy는 210~270 ms였으니 약 45배다.
- live:
  - **30.0 fps**, 한 프레임 그리기 2.2 ms(최대 4.9 ms), 프레임당 이벤트 100~700개
  - 버퍼 12~16개/s, ingest 8~9 ms/버퍼. 대부분 uncached 1 MB 복사 시간이다.
- 이상: "behind the newest event" 값이 −60~−106 ms다. 화면 시각이 받은 데이터보다 앞선다. 그래서 아래를 쟀다.

**버퍼가 담는 센서 시간(관찰, 연속 캡처 12개 중 새 버퍼 8개).**

| buf | 데이터 | 센서 시간 범위 | 앞 버퍼와의 간격 |
|---|---|---|---|
| 1 | 1 MB | 8.4–13.3 ms (4.9 ms) | |
| 2 | 520 kB | 59.2–76.5 ms (17.3 ms) | 45.9 ms |
| 3 | 456 kB | 152.5–179.7 ms (27.2 ms) | 76.0 ms |
| 4 | 마커만(DMA 데이터 없음) | – | |
| 5 | 15 kB | 281.9–282.4 ms | 102 ms |
| 6 | 마커만 | – | |
| 7 | 55 kB | 347.6–350.5 ms (3.0 ms) | 65.2 ms |
| 8 | 54 kB | 412.7–416.2 ms (3.5 ms) | 62.2 ms |

- MIPI 프레임은 센서 시간으로 약 65 ms마다 하나다. 그런데 버퍼에는 그 앞쪽 몇 ms만 들어 있다. 408 ms 가운데 **약 56 ms(14%)**만 메모리에 왔다.
- 가설: CRC 오류가 나면 CSIS가 그 프레임의 VC DMA를 끊는다(dmesg `CSIS_ERR_DMA_ABORT_DONE`, `frame was done with error`). 그래서 남은 패킷을 버린다. 확인하지 않았다.
- 결과: 30 fps로 그리지만, 프레임 대부분이 데이터가 없는 구간에 해당한다. 화면은 30 fps로 바뀌어도 이벤트가 보이는 순간은 버퍼 수(초당 약 14번)만큼이다. 연속된 영상과 상자 측정을 하려면 이 손실부터 없애야 한다.
- buf 9 이후는 캡처 모드가 버퍼를 지우지 않아 지난 데이터가 남은 것이라 표에서 뺐다(live 경로는 지운다).

**기타.** 이 측정 중 보드 Wi-Fi가 끊겼다(wlan0 DOWN, scan 반복, `e850-wifi` active). ACM으로는 접속된다. 원인은 보지 않았다.

### 11.28 화면 깜빡임의 세 원인과 수정, 디코더 최적화, 화면 조정 앱 (2026-11-06)

사용자 보고: "화면이 가끔 깜빡거린다". 이 절에서 "어두운 프레임"은 렌더러가 센 값(`dips`)이다. 최근 평균의 절반이 안 되는 이벤트로 그린 프레임을 센다. fade 0에서는 이런 프레임이 검게 번쩍인다.

**바꾼 것.**
- **fimc-is.ko** (`modules/s5krc1s-pablo/patches/csi-crc-keep-param.patch`, `apply.sh`):
  - `fimc_is.e850_csi_crc_keep`(0644)를 1로 두면 CRC 오류는 프레임 오류가 아니다.
  - VC DMA abort, 오류 프레임 flush, 센서 덤프, 로그 줄이 모두 없다. CRC 비트는 `csi->error_id`에도 넣지 않는다(아래 원인 1).
  - 개수는 `fimc_is.e850_csi_crc_count`(0444)로 센다.
- **커널 소스 볼륨(`e850-kernel`)에서 디버그 printk 9줄을 지웠다**(아래 원인 3).
  - `is-device-sensor_v2.c`에 8줄, `is-devicemgr.c`에 1줄이 있었다. 모두 `printk(_ratelimited)(KERN_EMERG "[bypass-dbg] …")`다.
  - 우회 경로를 디버깅할 때 볼륨에 손으로 넣은 줄이다. 저장소의 `bypass.sh`나 패치에는 없다. 그래서 저장소에서 바꿀 것은 없고, 볼륨이 저장소와 같아졌다.
  - 새 fimc-is.ko는 099c323b…다.
- **libdvsview.so** (`tools/linux510/dvsview.c`):
  - VC0 버퍼를 캐시되는 `/dev/dma_heap/system`에서 받는다. 복사 없이 dma-buf에서 바로 디코딩한다.
  - 8192 B 줄마다 첫 0 워드에서 다음 줄로 넘어간다. 읽은 패킷은 0으로 지우고 `DC CIVAC`로 DRAM에 쓴다(아래 "캐시").
  - 종류 비트(31~26)가 지워진 SUB 타임스탬프(`0x008000xx`)를 SUB로 읽는다(아래 원인 2).
  - 잡음 필터(`filter_ms`)를 픽셀당 1비트 맵 2장(약 200 kB)으로 바꿨다. 이웃 8픽셀 중 하나가 `filter_ms` 안에 반응했으면 그린다.
  - 타임스탬프 규칙:
    - 나쁜 타임스탬프 뒤의 이벤트는 버린다.
    - 뒤로 가는 값이 64개 연속되어야 카운터 리셋으로 본다.
    - 데이터 구멍은 표시 시계에서 한 scan(494 µs)으로 건너뛴다.
    - 재생 시각은 최신 데이터를 넘지 않는다. 구멍이 나면 마지막 프레임을 유지한다.
  - 통계에 `dips`(어두운 프레임 수)를 더했다. coverage는 실제 시간 기준으로 바꿨다.
  - 설정 표와 API를 더했다: `dvs_set`, `dvs_params`, `dvs_stats`, `dvs_snapshot`.
- **화면 조정 앱** `tools/linux510/dvsctl.py` + `dvsctl.html` (보드 `/usr/local/share/dvsview/`):
  - `ov5647-cap.py --ctl 8080`이 캡처 프로세스 안에서 HTTP 스레드를 띄운다. 주소는 `http://192.168.0.236:8080/`.
  - 설정 항목: fps, window_ms, fade(잔상), filter_ms(잡음 필터), color, zoom(1440×1080), flip, margin_ms.
  - 미리보기(0.5초마다 PNG)와 통계가 있다.
  - 바꾼 값은 `/var/lib/dvsview/display.json`에 저장되어 다음 실행에도 쓰인다.
  - **인증이 없다.** 표시 방법만 바꾼다.
  - 포트가 이미 쓰이고 있으면 페이지 없이 live만 돈다.
- **`ov5647-cap.py`**:
  - `--ctl`과 `--boxes`(상자 측정, 보류)를 더했다.
  - live raw8의 VC0는 cached heap을 쓴다.
  - `DMA_BUF_IOCTL_SYNC`는 뺐다. 이 드라이버에서는 아무 일도 하지 않는다(아래 "캐시").
- **`tools/board/dvslive.sh`** (보드 `/usr/local/bin`):
  - devfreq 최대, 모듈 적재, `err_stop=0`, `crc_keep=1`, `flush_us=50`, `--ctl 8080`을 한 번에 하고 live를 띄운다.
  - 로그는 `/tmp/dvslive.log`다.

**결과(같은 장면, live 60초, 2초마다 통계).**

| 단계 | 오류 프레임 | 어두운 프레임 | 2초 구간 커버리지 | 버퍼/s | 지연 |
|---|---|---|---|---|---|
| 사용자 공간 수정 전 | 약 14.6 ms마다 | 38초에 187장 | – | – | 36~234 ms |
| 사용자 공간 수정 뒤(fimc-is 908e4ee0) | 약 14.6 ms마다 | 45장 | – | – | 13~76 ms |
| + CRC를 error_id에서 뺌(113b4d93) | 0 | 52장, 12장(두 번 잼, 모두 시작 후 18~24초) | 28~99% | 58.5~68.2 | 12~62 ms |
| + 지워진 SUB 복구(lib 42e1bb22) | 0 | 0 | 4~10초에 28~37%, 그 밖 73~99% | 58~68 | 11~32 ms(첫 2초 66 ms) |
| + EMERG printk 제거(099c323b) | 0 | **0** | **93~100%**(첫 2초 74%) | **67.5~68.2**(첫 2초 제외) | **21~24 ms**(첫 4초 제외) |

- 앞의 두 줄은 앞선 측정(길이가 다르다)이다. 나머지 세 줄은 같은 방법(62초, 2초 통계)으로 쟀다. 아래 두 줄은 버퍼별 기록을 더한 디버그 판(같은 소스 + fprintf)으로 쟀다.
- 마지막 단계는 그 뒤 128초를 더 보았다. 어두운 프레임 0장, 90% 아래 구간 2개였다.
- 마지막 단계의 커널 로그에는 `start/end overlapped`, `There's no Shot`, `DMA off`, `frame was done with error`가 하나도 없다. 버퍼별 기록에도 빈 버퍼와 느린 구간이 없다.
- CRC 오류는 그대로다. 초당 8679개로, 버퍼(127줄)당 약 128개다. 거의 모든 줄에 오류가 있지만 데이터는 쓸 수 있다.

**원인 1: CRC가 있는 프레임마다 CSIS가 오류 프레임으로 flush했다(관찰 → 수정).**
- `csi_err_check()` 3단계가 CRC 비트를 `csi->error_id`에 쌓았다.
- 그러면 frame-end tasklet이 프레임마다 `csi_err_handle()` → `csis_flush_vc_buf_done(…, VB2_BUF_STATE_ERROR)`를 불렀다.
  - dmesg: `frame was done with error`가 약 14.6 ms마다, `NDONE(n, E3)`, `csis_flush_vc_buf_done:486(!ldr_framemgr)`
- flush는 DMA가 아직 쓰고 있는 다음 버퍼까지 돌려주었다. 결과:
  - 66초 동안 빈 버퍼 42묶음(30~60 ms 구멍)
  - 데이터가 약 133 ms(버퍼 8개) 늦게 오는 버퍼
- 수정: crc_keep일 때 CRC 비트를 `error_id`에서 뺐다. 오류 프레임은 0이 됐다.

**원인 2: 링크가 워드의 종류 비트를 지운다. 그동안 SUB가 모두 사라지면 표시 시계가 멈춘다(관찰 → 디코더 수정).**
- 디코더가 버퍼별로 센 값: 시작 후 16.2~16.5초와 18.3~18.6초에 데이터(230~320 kB)와 REF는 그대로였다. 그런데 **SUB 타임스탬프가 0개**인 버퍼가 16~23개 이어졌다.
  - 표시 시계가 멈춘 채 이벤트가 한 시각에 쌓였다. 그래서 그 구간의 프레임당 이벤트가 41만~69만 개로 늘었다.
  - 이어서 나쁜 타임스탬프가 섞이는 구간이 왔고, 거기서 어두운 프레임이 났다.
- 원시 덤프(시작 후 15~26초, 725개 버퍼, 4800만 워드)로 확인한 것:
  - SUB는 오고 있었다. **첫 바이트가 `0x08`에서 `0x00`으로 바뀌어**(`0x088000bc` → `0x008000bc`) 종류가 000000이 됐을 뿐이다.
  - 같은 꼴이 다른 패킷에도 있다. COLUMN `0x04…` → `0x00…`, EVENT `0x81…` → `0x01…`이다. 비트 31~26만 0이 되고 비트 25~24는 남는다. 그래서 첫 바이트는 0x00(105만 개)이나 0x01(8.4만 개)뿐이고 0x02/0x03은 없다.
  - 11.23의 "매뉴얼에 없는 type 000000 워드"가 이것이다. 그중 85%(97만 개)는 현재 프레임 번호와 열 순서가 맞는 COLUMN이다. 이 COLUMN을 놓친 열의 이벤트는 앞 열(중앙값 1열, 90%가 3열 이내)에 그려진다. 열 패킷의 약 6%다.
  - SUB가 지워질 확률은 바로 앞 REF의 비트 15~8(ms 카운터의 둘째 바이트) 값에 매여 있다.
    - 0x41, 0x49, 0x50, 0x51, 0x53, 0x55, 0x58, 0x59, 0x5b일 때: 90~100%
    - 0x44~0x47, 0x4c~0x4f, 0x56, 0x57, 0x5e~0x66, 0x3c~0x3f일 때: 0%
    - 이 바이트는 256 ms마다 바뀐다. 그래서 SUB가 몽땅 사라지는 구간이 약 250 ms씩 생긴다.
  - REF 쌍의 약 6.5%에서 REF도 지워진다.
  - 종류가 온전한 COLUMN 가운데 비트 23/22나 20/19가 1인 것이 각각 약 5%, 열 번호가 960 이상인 것이 1.4% 있다. 비트 오류인지 매뉴얼에 없는 필드(매뉴얼은 COLUMN에 sub 타임스탬프가 있다고 쓴다)인지는 확인하지 않았다.
- 가설(측정하지 않음): 2-lane 1.6 Gbps, 22→15핀 FPC 연결의 신호 품질 문제다. 오류가 앞뒤 바이트 값에 따라 달라지는 것(심볼 간 간섭)과 CRC 오류가 거의 모든 줄에 있는 것이 이 가설과 맞다. 센서 MIPI 속도를 낮추려면 PLL 레지스터를 바꿔야 해서 시도하지 않았다.
- 수정: `(w & 0xfffffc00) == 0x00800000`인 워드를 SUB로 읽는다.
  - 덤프의 후보 5472개는 모두 SUB 자리에 있다. 온전한 REF 바로 뒤가 4076개, 지워진 REF 바로 뒤가 1396개다. 값이 1000 이상인 것도 없다.
  - 다른 패킷이 지워져 이 꼴이 되기는 어렵다. 단일 그룹 이벤트는 95%가 비트 17:16이 `10`이고, 병합 이벤트는 g2가 0인 것이 1% 미만이다. COLUMN·REF·FRAME END는 비트 23이 0이다.
  - REF는 되살리지 않았다. REF를 놓치면 직전 ms 값을 쓰게 되어 오차가 0~−1 ms에 그친다(뒤로 가는 값은 건너뛴다).
  - COLUMN 복구(위 6%)도 하지 않았다. 깜빡임과 관계없는 화질 문제다.
- 결과: 같은 커널에서 어두운 프레임이 52장/12장에서 0장으로 줄었다.

**원인 3: 5초마다 디버그 printk가 CPU0의 인터럽트 안에서 UART로 나갔다(관찰 → 수정).**
- 버퍼별 기록: 약 5.0초(버퍼 약 330개)마다 150~230 ms 동안 버퍼가 29 ms 간격으로 왔다. 센서 시간도 버퍼마다 프레임 2개씩 건너뛰었다. 소비자는 제때 처리했으니 커널 쪽에서 프레임이 하나 걸러 하나씩 빠진 것이다.
  - 그 직후 빈 버퍼(0~27 kB)가 이어졌다.
  - 그다음에는 앞쪽 줄은 새 데이터, 뒤쪽 줄은 약 103.7 ms(버퍼 7개) 전 데이터인 버퍼가 나왔다.
  - 이 예전 데이터는 한 비트 보정으로 받아들여지는 경우가 많았다(버퍼당 22~28개). 그래서 커버리지가 28~37%까지 떨어졌다.
- 같은 시각 dmesg: `[CSIS1] start/end overlapped`, `There's no Shot`, `shot mismatch`, `DMA off`/`DMA on`, `tasklet_csis_str_otf: 320 callbacks suppressed`.
- `… callbacks suppressed`는 `printk_ratelimited`의 기본 창(5초, 10개)이다.
  - 창이 열릴 때마다 프레임마다 부르는 7곳의 `printk_ratelimited(KERN_EMERG "[bypass-dbg] …")`가 10번씩 찍혔다.
  - EMERG는 콘솔 로그 레벨(캡처 중 1)과 관계없이 콘솔로 나간다. Mac의 `uart.log`에도 live 중에 이 줄만 나가 있었다. 모두 CPU0의 swapper/0, ksoftirqd/0, sugov:0 문맥이었다.
  - 115200 baud에서 한 줄에 약 6 ms(타임스탬프 간격으로 확인)가 걸린다. `console_unlock()`은 `printk_safe_enter_irqsave()` 안에서 콘솔 드라이버를 부른다(소스 확인). 창 하나에 수십 줄이니 CPU0이 수백 ms 동안 인터럽트를 제때 받지 못한다.
- 수정: 그 9줄을 볼륨에서 지우고 fimc-is.ko를 다시 빌드했다(099c323b). 재부팅 뒤 위의 dmesg 메시지는 0개, 느린 구간도 0개다.
- 워치독 keepalive·RTC 읽기(`dev_info`)는 live 중에는 UART로 나가지 않았다.

**캐시(관찰, 소스로 확인).**
- Pablo는 DQBUF 때 버퍼를 unmap한다(`is_queue_buffer_finish` → unmap → `dma_sync_sgtable_for_cpu`, invalidate). 그래서 캐시로 읽어도 새 데이터다.
- 사용자 공간이 버퍼를 쥐고 있는 동안에는 붙은 매핑이 없다. 그래서 `DMA_BUF_IOCTL_SYNC`는 아무 일도 하지 않는다.
- QBUF 때 map → `dma_sync_sgtable_for_device(FROM_DEVICE)` → `__dma_inv_area`(DC IVAC)가 dirty 줄을 버린다. 그래서 디코더가 쓴 0이 DRAM에 닿지 않았다.
  - DMA가 일부만 채운 다음 차례의 프레임에, 8버퍼(약 133 ms) 전 이벤트가 남았다.
- 수정: 지운 직후 `DC CIVAC`(EL0 허용, SCTLR_EL1.UCI)와 `dsb sy`로 DRAM에 쓴다.

**디코더·렌더러 속도(측정).**
- 줄 단위 디코딩: 캡처 8개와 live 덤프 16개에서 모든 줄이 "처음부터 이어진 패킷, 그 뒤 0"이었다. 채운 비율은 18~100%다. 첫 0 워드에서 다음 줄로 넘어간다.
- 잡음 필터: 32비트 시각 맵(2.8 MB)은 이벤트당 약 285 ns였다(A55에서 DRAM miss 세 번). 1비트 맵 2장으로 바꾸어 약 4.4배 빨라졌다. live에서 filter 4 ms를 켜면 ingest가 7.3 → 9.5 ms다.
- 지금 live: 초당 버퍼 68개, 버퍼당 패킷 약 225 kB(이벤트 약 9만 개), ingest 7~8 ms, 그리기 17~18 ms(프레임당 이벤트 약 20만 개), 30 fps.
  - CPU는 python3 프로세스 약 100%(두 스레드 합), journald 약 7%다.
- 핫픽셀(관찰): 0.58초 동안 69.1만 픽셀 중 46.6만 개가 반응했다. 상위 1000픽셀이 이벤트의 21%, 상위 2477픽셀이 29%다. 핫픽셀 마스크로 줄일 수 있는 것은 20~30%뿐이라 하지 않았다.

**flush_us(관찰).** `is_cis_s5krc1s.flush_us`를 바꿔 보았다.
- 250: 초당 약 15버퍼
- 50: 초당 62~68버퍼
- 100: 화면이 망가졌다. 원인은 보지 않았다.
- `dvslive.sh` 기본값은 50이다.

**틀렸던 가정.**
- `DMA_BUF_IOCTL_SYNC`가 캐시를 맞춰 준다고 보았다. 이 드라이버에서는 아무 일도 하지 않는다(위 "캐시").
- 11.23의 "type 000000은 문서에 없는 패킷일 것"은 틀렸다. 종류 비트가 지워진 워드다(원인 2).
- 11.23에서 "MGROUP/SGROUP bit 31이 빠진다"를 버린 근거(type 6~31이 없음)는, 비트 31만이 아니라 비트 31~26이 한꺼번에 지워진다는 것과 맞는다.
- 핫픽셀 비율을 처음에 잘못 읽었다. 다시 잰 값이 위의 21%/29%다.

**남은 것.**
- 종류 비트가 지워진 COLUMN(약 6%) 복구. 프레임 번호와 열 순서로 판별할 수 있다(덤프에서 94%가 다음 COLUMN과도 맞음).
- REF 비트 14가 약 290 ms 동안 0으로 읽히는 구간(16.27~16.56초에 한 번). 들어갈 때와 나올 때 이벤트를 약 45 ms어치 잃는다. 어두운 프레임은 없었다.
- 링크 자체(CRC 초당 약 8700개). 센서 MIPI 속도 낮추기나 배선 개선은 하지 않았다.
- flush_us=100에서 화면이 망가진 원인, 32줄 모드 MMU fault(11.26), eMMC 파티션 인식 실패(11.26).
- 상자 측정(`--boxes`)과 MySQL 저장은 사용자 지시로 보류했다. 코드는 남아 있고 `--boxes`를 주지 않으면 꺼져 있다.
- 설정 앱의 8080 포트에는 인증이 없다. Apache 포트 결정(BRINGUP_STATUS 4장)과 겹칠 수 있다.

**보드에 바꾼 것.**

| 경로 | 변경 |
|---|---|
| `/lib/modules/$(uname -r)/fimc-is.ko` | 099c323b…(crc_keep + CRC를 error_id에서 뺌 + EMERG printk 없음). 이전 판은 `/root/fimc-is.ko.prev-bypassdbg`(113b4d93), `.prev-crcflush`(908e4ee0), `.prev-11.27`. Mac 사본은 `~/e850-logs/fimc-is-nodbg-099c323b.ko` |
| `/usr/local/lib/libdvsview.so` | 42e1bb22…(SUB 복구 포함). 이전 판은 `.prev-subfix`(bab245d3), `.prev-11.28`(877162f9) |
| `/usr/local/bin/ov5647-cap.py` | ae0fab5c…(`--ctl`, `--boxes`, cached heap, SYNC 뺌) |
| `/usr/local/share/dvsview/dvsctl.py`, `dvsctl.html` | 추가(d7cfc8da…, 34d915da…) |
| `/usr/local/bin/dvslive.sh` | 추가(c5b1b7e9…) |
| `/var/lib/dvsview/display.json` | 설정 앱이 만든다. 지금은 기본값 |

### 11.29 CRC 오류의 원인(센서 D-PHY PLL M)과 수정, 1000 fps, 웹 서비스 (2026-11-07)

사용자 요청: "CRC 오류가 초당 약 8700개로, 거의 모든 줄의 데이터가 깨짐 해결해", "1000프레임으로 낮춰", "웹으로 850에 접속해서 화면을 보고 프레임 수와 각종 설정을 할 수 있게".

**바꾼 것.**
- **CIS 드라이버** (`modules/s5krc1s-pablo/files/.../is-cis-s5krc1s.c`, `-setA.h`):
  - `is_cis_s5krc1s.margin`(0644): DTAG_FRM_MARGIN(0x321D/0x321E)이다. scan이 끝난 뒤 쉬는 시간을 2^12 event clock 단위로 정한다(매뉴얼 Table 14). 0이면 setfile의 2다.
  - `is_cis_s5krc1s.regs`(0644): 시험용이다. mode change 맨 끝에 `"30a6=75,30a7=01"`(16진수, 8비트 레지스터) 형식으로 레지스터를 쓴다. 쓴 값은 dmesg에 찍힌다.
  - setfile의 D-PHY PLL **0x30A6을 0x8D에서 0x75로 바꿨다**(아래 "CRC 원인"). vendor 원본 `setfile/Delta_10_2000FPS.txt`는 그대로 두었다.
- **libdvsview.so** (`tools/linux510/dvsview.c`):
  - scan 주기를 직접 잰다. 받아들인 타임스탬프 간격을 10 µs 칸의 히스토그램에 모은다. 통계 주기(2초)마다 가장 많은 칸을 scan 주기로 삼는다.
  - 데이터 구멍에서 표시 시계가 건너뛰는 폭과 "연속 scan"으로 보는 한계(3 scan)가 이 값을 따른다. 전에는 494 µs로 고정되어 있었다.
  - 통계에 `scan_us`를 더했다.
  - `dvs_png()`: 화면을 RGB PNG로 만든다(zlib level 1, C). `dvs_snapshot()` + numpy + Python zlib을 대신한다. 빌드에 `zlib1g-dev`와 `-lz`가 필요하다.
  - 선택 시험을 더했다: scan 주기 추종, 1 ms scan에서의 구멍, PNG를 다시 풀어 픽셀 확인.
- **웹 서비스** (`tools/linux510/dvsctl.py`, `dvsctl.html`, `tools/board/dvslive.sh`, `tools/board/dvslive.service`):
  - `dvslive.service`(systemd, enabled)가 부팅 때 live와 웹 페이지를 띄운다. 프로세스가 끝나면 3초 뒤 다시 띄운다(`Restart=always`).
  - 주소는 `http://192.168.0.236:8080/`(Wi-Fi), USB로는 `http://192.168.7.1:8080/`이다. **인증이 없다.**
  - 미리보기는 canvas에 그린다. 한 번에 요청 하나씩, 고른 속도(끔/2/5/10/15 fps, 기본 10)로 받는다. 받은 속도를 화면에 보여 준다.
  - 통계에 센서 scan 주기(fps)와 CRC 오류/s를 더했다. CRC는 `fimc_is.e850_csi_crc_count`의 증가분이다.
  - 센서 설정: scan 속도(2000/1500/1000/750/500/250 fps → margin)와 MIPI flush(µs)를 고른다. 적용하면 `/var/lib/dvsview/sensor.json`에 저장한다. 그다음 프로세스가 SIGTERM으로 끝나며 stream off를 한다. 그러면 systemd가 `dvslive.sh`를 다시 띄우고, `dvslive.sh`가 stream on 전에 그 값을 CIS 모듈에 쓴다. 다시 뜨는 데 약 5초 걸린다.
  - 범위를 벗어나거나 모르는 이름, JSON이 아닌 요청은 400으로 거절하고 재시작하지 않는다(5가지 확인).
  - `dvslive.sh`는 이제 foreground에서 `exec`로 돈다. 수동으로 띄울 때도 `systemctl restart dvslive`를 쓴다. 기본값은 margin 17(1015 fps), flush 50 µs다.

**scan 속도(측정, 웹 API로 margin만 바꿈, 각 20초).**

| margin | 잰 scan 주기 | scan/s | 버퍼/s | CRC/s | CRC / 줄 |
|---|---|---|---|---|---|
| 0 (setfile 2) | 495 µs | 2020 | 68.0 | 8845 | 1.02 |
| 17 | 985 µs | 1015 | 39.0 | 4759 | 0.96 |
| 18 | 1015 µs | 985 | 37.5 | 4571 | 0.96 |
| 48 | 1985 µs | 504 | 19.5 | 2309 | 0.93 |

- scan 주기는 약 430 + 32.4 × margin µs다. 매뉴얼 값(2^12 / 125.6 MHz = 32.6 µs)과 맞는다.
- 1000 fps에 가장 가까운 margin은 17(985 µs, 1015 fps)과 18(985 fps)이다. 기본값은 17로 했다. 페이지는 고른 fps 이상이 나오는 가장 큰 margin을 고른다.
- **scan 속도는 CRC의 원인이 아니다.** 데이터시트의 compressed 최대 fixed scan rate(1355 fps, 11.23)보다 낮은 504 fps에서도 줄마다 CRC가 난다.

**CRC 원인 찾기(측정).** CRC/줄은 CRC/s를 (버퍼/s × 127)로 나눈 값이다.
1. 수신 쪽 deskew 끔(`e850_csi_speed=1499`, settle 자동): 버퍼당 데이터가 약 1 kB이고 coverage 0%다. 쓸 수 없다.
2. 센서 D-PHY PLL **S=1**(`regs=30a7=01`, 반속으로 추정) + 수신 800:
   - CRC는 0/s다.
   - 첫 프레임(스트림 시작 버스트)은 127줄이 모두 차 있다. 깨진 워드(type 000000)는 **0개**다(이벤트 24만, COL 6720).
   - 그 뒤 프레임은 첫 패킷 하나(1~2줄, 대부분 timestamp 없음)만 들어온다. 프레임은 초당 40~50개 온다.
   - settle 20/30/50/60을 줘도 같다. 원인은 모른다.
3. 센서 D-PHY PLL **M**(0x30A6, vendor 0x8D = 141)만 바꿈, 수신은 DT(1600, deskew 켬, settle 8):

| 0x30A6 | CRC/s | 데이터 |
|---|---|---|
| 0x68 | 0 | 버퍼 35/s, coverage 100% |
| 0x70 | 1~6 | 버퍼 0.5/s 이하(데이터 없음), 2번 |
| 0x72, 0x74, 0x75, 0x76, 0x78 | **0** | 버퍼 29.5~36/s, coverage 99~100%(0x78은 3번) |
| 0x7A | 5074 | coverage 92%, 원래처럼 줄마다 CRC |
| 0x7C | 90~146 | 데이터 없음, 2번 |
| 0x80 | 3~8 | 데이터 없음, 3번 |
| 0x84, 0x88 | 0 | coverage 99~100% |
| 0x8D (vendor) | 약 4800(margin 17) | 줄마다 CRC, COL의 약 6%가 깨짐(11.28) |

4. 수신을 1400(deskew 끔, settle 자동 69)으로 바꿔도 0x70 실패, 0x75 CRC 0, 0x7A 줄마다 CRC, 0x7C 실패로 **경계가 같다**.
   - 그래서 좋은 구간은 수신 설정이 아니라 센서 쪽이 정한다고 본다.
   - 가설(측정하지 않음): M에 따라 센서 D-PHY PLL의 lock이나 지터가 달라진다. vendor 값 0x8D는 이 센서에서 지터가 큰 쪽에 있다.
5. 0x75에서 원본 7프레임: COL 192,372, 이벤트 880,385, TS 400, FRAME END 201, **깨진 워드 0개**.
6. 새 기본값(setfile 0x75, `regs` 비움, 수신 DT)으로 재부팅했다. 부팅 뒤 3분 동안 `e850_csi_crc_count`가 0이다. 버퍼 35/s, 158 kB/버퍼, coverage 99%, 어두운 프레임 0이다.

- 우연히 잰 것: 센서는 원래 속도인데 수신이 800 설정(settle 40)으로 남았을 때 CRC 4855/s, coverage 7~9%였다.
- **(11.46 정정)** 아래 83%·1328·1404 Mbps 추정은 틀렸다. 0x30A5~A7은 19비트 단어이고 0x30A6의 위 3비트는 P다. "M만 바꿨다"도 틀렸다. 해석은 [DVS-PLL-TESTS.md](DVS-PLL-TESTS.md) 1·2장에 있다.
- **실제 lane 속도는 모른다.** M에 비례한다면 0x75는 원래 값의 83%다. 원래가 1600이면 1328 Mbps, 24 MHz × 141 / 2로 계산한 1692면 1404 Mbps다. DT의 1600은 그대로 두었다. 수신은 1600 설정과 1400 설정 모두에서 CRC 0이었다.

**웹 서비스(측정).**
- `frame.png`(960×720 RGB):
  - 보드 안에서 중앙값 34 ms, 최대 50 ms다(18 kB). Wi-Fi로 Mac에서 받으면 62~99 ms다.
  - 전에는 Python에서 만들어 160~290 ms(89 kB) 걸렸다. 화면 내용이 달라 크기는 바로 비교할 수 없다.
- live 프로세스의 스레드별 CPU(5초, 웹 접속 없음): 디코딩 23%, 그리기 63%(사용자가 고른 44 fps, 1.5배 확대, 잡음 필터 3 ms), HTTP 0%. 8코어 중 1코어를 다 쓰지 않는다.
- **코어 2개를 쓰면 좋은가(사용자 질문).**
  - 이미 2개를 쓴다. 디코딩(Python 주 스레드가 C 함수를 부름, GIL 풀림)과 그리기(C pthread)가 다른 코어에서 동시에 돈다. 웹 요청은 요청마다 다른 스레드가 맡는다.
  - 그리기 한 장이 14.6 ms(1.5배 확대)라서 한 코어로는 약 68 fps가 한계다. 그보다 빨리 그리려면 그리기를 2스레드로 나눠야 한다. 하지 않았다.

**틀렸던 가정.**
- 11.28에서 "2-lane 1.6 Gbps, FPC 연결의 신호 품질"로 추정했다. 링크 문제인 것은 맞았다. 하지만 속도를 낮춰서가 아니라 PLL M 값으로 고쳐졌다. 속도가 더 낮은 0x70도 실패하므로 FPC 신호 품질만으로는 설명이 안 된다.
- 11.23의 "settle 값은 결과에 영향이 없었다"(1499에서 5~14):
  - 1500 미만에서 PHY 드라이버는 다른 settle clock을 쓴다(`settle_clk_sel=1`). 표 값도 800에서 40, 1400에서 69로 크다.
  - 반면 1499를 정렬하면 1500이 되어 표에서 7을 받는다.
  - 그래서 그때 시험한 값은 모두 너무 짧았을 수 있다(가설).
- scan 주기 494 µs를 디코더에 박아 둔 것: 이제 margin을 바꾸면 달라진다. 그래서 디코더가 직접 잰다.

**남은 것.**
- 좋은 구간 0x72~0x78은 좁다(가운데에서 ±3%). 온도나 장시간에서도 안정한지는 측정하지 않았다. 지금까지 확인한 것은 재부팅 뒤 3분이다.
- S=1에서 첫 패킷만 들어오는 원인, 0x70/0x7C/0x80에서 데이터가 없는 원인.
- flush_us=100에서 화면이 망가진 현상(11.28)을 새 링크에서 다시 보지 않았다.
- 디코더의 SUB 복구(11.28)는 남겨 두었다. 깨진 워드가 없으면 하는 일이 없다.
- 8080에는 인증이 없다. Apache 포트 결정(BRINGUP_STATUS 4장)과 겹칠 수 있다.

**보드에 바꾼 것.**

| 경로 | 변경 |
|---|---|
| `/lib/modules/$(uname -r)/is-cis-s5krc1s.ko` | 0b35f571…(margin, regs, D-PHY M 0x75). 이전 판은 `/root/is-cis-s5krc1s.ko.prev-margin`(1b5c7d77, M 0x8D + margin/regs), `/root/is-cis-s5krc1s.ko.prev-11.28`(01dd3c98). Mac 사본은 `~/e850-logs/is-cis-s5krc1s-1129b.ko` |
| `/usr/local/lib/libdvsview.so` | 17f1368f…(scan 주기 측정, `dvs_png`). 이전 판은 `.prev-scan`(42e1bb22) |
| `/usr/local/share/dvsview/dvsctl.py`, `dvsctl.html` | 01bebea2…, 275b6bbe… |
| `/usr/local/bin/dvslive.sh` | 38b7d03d…(foreground, sensor.json, margin 17) |
| `/etc/systemd/system/dvslive.service` | 추가(a1252e90…), `systemctl enable` 함 |
| `/var/lib/dvsview/sensor.json` | 웹 페이지가 만든다. 지금 `{"margin": 17, "flush_us": 50}` |
| `/var/lib/dvsview/display.json` | 사용자가 페이지에서 바꾼 값(fps 44, zoom 1, filter_ms 3, margin_ms 0) |

### 11.30 코어 8개 사용 지도, 웹 전용 코어(CPU7), Wi-Fi 웹 응답 104~113 → 28~40 ms, Wi-Fi 펌웨어 패닉 (2026-11-07)

사용자 요청: "cpu 8개 사용 영역을 알려줘, 그리고 웹 서비스를 위해 하나 할당해서 더 빠른 서비스를 제공해".

**측정 방법.**
- `tools/board/cpumap-cap.sh`(root)가 ftrace로 5초 동안 기록한다.
  - 이벤트: `sched_switch`, `irq_handler_entry/exit`, `softirq_entry/exit`. `record-tgid`도 켠다.
  - 앞뒤로 `/proc/stat`, `/proc/interrupts`, cpufreq `time_in_state`를 찍는다.
  - trace 버퍼는 CPU마다 8 MB다. 결과는 `/tmp/cpumap*`에 둔다.
- `tools/host/cpumap.py`(Mac)는 코어마다 두 이벤트 사이의 시간을 그때 돌던 태스크(또는 hardirq, softirq)에 붙인다. 끝난 live 프로세스 스레드는 웹 연결 스레드로 묶는다. 짧게 사는 스레드는 그것뿐이다.
- 웹 응답은 `tools/host/webbench.py`로 잰다.
  - `/frame.png`를 차례로 받는다.
  - 요청마다 새로 연결하거나(urllib), 연결 하나를 재사용한다(`keep`, 브라우저와 같다).
- 바꾸기 전 지도를 잴 때의 웹 부하:
  - Mac 측정기가 초당 10장을 받았다.
  - 사용자도 같은 Mac(192.168.0.246)의 브라우저로 초당 10장을 보고 있었다.
  - trace에서 frame 크기의 연결 스레드가 5초에 100개였다.

**바꾸기 전 지도(5초, frame 요청 초당 20개).**

| CPU | 사용률 | 내용 |
|---|---|---|
| 0 | 34% | 장치 IRQ 전부(Wi-Fi `scsc_wlbt` 245/s, 타이머 238/s, DSIM 122/s, CSI 107/s), NET_RX softirq 3%, 웹 연결 스레드 19% |
| 1~3 | 33~43% | 그리기 스레드(합 68%), 캡처·디코드(main, 합 15%), 웹 연결 스레드(합 25%) |
| 4 | 7% | 그리기 3.6% |
| 5~7 | 0~2% | 거의 쉼 |

- 0~3번 클러스터는 평균 1956 MHz, 4~7번은 558 MHz(대부분 546)였다. 스케줄러가 일을 0~3번에 모은다.
- live 프로세스는 다음과 같이 썼다.
  - 그리기: 73%(사용자 설정 76 fps)
  - main: 18.5%
  - 웹 연결 스레드: 합 46%(요청마다 새 연결과 새 스레드, HTTP/1.0)
  - 나머지는 journald 4%, 커널(타이머 IRQ, NET_RX 등) 수 %다. 8코어 중 약 1.5코어를 썼다(`/proc/stat` busy 합 154%).
- frame 요청 하나는 CPU를 23 ms(중앙값) 쓴다.
  - 그중 zlib level 1 압축이 13.3 ms다. 12 kB PNG 프레임으로 같은 zlib를 써서 따로 쟀다.
  - 나머지는 RGB 변환, Python, HTTP다.

**Wi-Fi로 받을 때(바꾸기 전, curl 25번 중앙값).**
- 연결에 24 ms, 연결 뒤 첫 바이트에 39 ms(서버 약 30 ms + 왕복 1번), 본문에 35 ms(23 kB)가 걸렸다.
- ping 왕복은 평균 7.4 ms였다. 링크 송신 속도는 39 Mbit/s(MCS 4), 신호는 −66 dBm였다.
- 23 kB에 35 ms는 대역폭으로 설명되지 않는다.

**CPU7 고정 시험(바꾸기 전 코드, 보드 안 localhost 100번씩).** `taskset`/`uclampset`을 웹 accept 스레드에 걸었다. 새 연결 스레드가 이 설정을 물려받는다.

| 상태 | 중앙값 | p90 | 최대 | 초당 |
|---|---|---|---|---|
| 공유 코어(원래) | 34~38 ms | 58 ms | 97 ms | 25~27 |
| CPU7만 | 77~80 ms | 107~108 ms | 112~114 ms | 12.4~12.7 |
| CPU7 + uclamp.min 1024 | 27 ms | 40~41 ms | 45~47 ms | 32.5 |

- **CPU7만 주면 두 배 느려진다.** 4~7번 클러스터가 쉬고 있어서 546 MHz다. 25 ms짜리 요청은 schedutil이 주파수를 올리기 전에 끝난다.
- uclamp.min 1024는 "이 스레드가 돌 때는 최고 클럭"이라는 요청이다. 스레드가 쉬는 동안에는 주파수를 붙잡지 않는다.

**바꾼 것.**
- `tools/linux510/dvsctl.py`:
  - `pin()`: 웹 accept 스레드가 시작할 때 세 가지를 한다. CPU7에 고정한다(`sched_setaffinity`). uclamp.min 1024를 건다(`sched_setattr`, arm64 syscall 274). 이름을 `dvs-web`으로 바꾼다(`prctl`). 연결 스레드는 셋 다 물려받는다.
  - CPU7이 꺼져 있으면(hotplug) 공유 코어에 남고 stderr에 남긴다.
  - HTTP/1.1 keep-alive(`protocol_version`)를 켰다.
  - `TCP_NODELAY`(`disable_nagle_algorithm`)를 켰다. 헤더 뒤 본문이 ACK를 기다리지 않는다.
  - 쉬는 연결은 30초 뒤 끊는다(`timeout`). 브라우저가 미리 열어 둔 연결이 스레드를 계속 붙잡지 않게 한다.
  - 오류 응답(400, 404, 503) 뒤에는 연결을 닫는다. 읽지 않은 요청 본문이 다음 요청으로 읽히지 않게 한다. POST `/nope`(404)와 JSON이 아닌 POST `/params`(400)는 닫고, 한 연결로 GET 5번(`/stats`, `/frame.png`, `/params`, `/sensor`, `/frame.png`)이 이어지는 것을 확인했다.
- `tools/board/e850-cpu7.conf` → `/etc/systemd/system.conf.d/`: `CPUAffinity=0-6`.
  - systemd가 띄우는 모든 프로세스가 CPU0~6에 있다. 서비스, ssh 로그인, live 프로세스의 그리기·캡처 스레드가 모두 여기에 든다.
  - 재부팅해야 적용된다.
- `tools/linux510/dvsctl.html`: 미리보기에 20/30 fps를 더했다(기본 10 그대로).
- Wi-Fi 절전은 바꾸지 않았다(아래).

**결과(측정).**

| Mac에서 Wi-Fi로 | 중앙값 | p90 | 초당(연달아) |
|---|---|---|---|
| 전, 요청마다 새 연결 | 104~113 ms | 186~1098 ms | 2.2~8.2 |
| 후, 요청마다 새 연결 | 41 ms | 48 ms | 23.8 |
| 후, 연결 재사용(브라우저와 같음) | 28~30 ms | 32~37 ms | 31~34 |
| 후, 연결 재사용, 링크 MCS 2(19.5 Mbit/s, −69 dBm) + 브라우저 동시 접속 | 40 ms | 56~62 ms | 23~24 |

- 보드 안(localhost, 서버 시간):
  - 전: 중앙값 29~38 ms, p90 51~60 ms.
  - 후, 혼자: 25.6 ms, p90 27 ms(새 연결).
  - 후, 브라우저와 동시: 18.3 ms, p90 34 ms(연결 재사용).
- 새 연결끼리 비교하면 104~113 ms가 41 ms로 줄었다.
  - 서버 시간은 약 8~12 ms 줄었다(CPU7 + uclamp).
  - 나머지 약 55 ms는 NODELAY의 몫으로 본다. 전에는 본문 꼬리가 ACK를 기다렸다(Nagle). 이 몫은 따로 재지 않은 추정이다.
- 연결을 재사용하면 41 ms가 28~30 ms로 더 줄어든다.
- 바꾼 뒤 지도(재부팅 뒤 1분 반, Mac 측정기가 연결 재사용으로 초당 10장, 5초):

| CPU | 사용률 | 내용 |
|---|---|---|
| 0 | 3.8% | 장치 IRQ(타이머 171/s, DSIM 121/s, CSI 107/s, Wi-Fi), cpufreq 스레드, journald |
| 1 | 77.6% | 그리기 75.9%(사용자 설정 104 fps) |
| 2 | 1.4% | journald, watchdog |
| 3 | 13.2% | 캡처·디코드(main) |
| 4~6 | 0~0.4% | 쉼 |
| 7 | 17.2% | **`dvs-web` 17.5%**(frame 요청 하나에 17.5 ms), 그 밖에는 타이머 IRQ와 IPI 0.4% |

- 4~7번 클러스터는 평균 1400 MHz(대부분 2002)였다. 웹이 돌 때만 올라간다.
- 한 코어로 처리할 수 있는 frame 요청은 초당 약 40~55개다(요청당 18~25 ms).
  - 여러 브라우저가 30 fps로 보면 CPU7이 다 찬다.
  - 그래도 그리기와 캡처는 다른 코어에 있어서 영향을 받지 않는다.
- 30 fps를 고르면 링크가 좋을 때(MCS 4) 약 30장, 나쁠 때(MCS 2) 약 20장을 받는다. 페이지가 받은 속도를 보여 준다.

**시험했지만 넣지 않은 것.**
- Wi-Fi 절전 끔(`iw dev wlan0 set power_save off`): 연결 재사용 중앙값이 29.2~29.5 ms였다(켬 28.4~28.8). p90도 비슷하다. 이득이 없어서 다시 켰다.
- zlib `Z_RLE`(같은 프레임): 11.8 ms에 4.7 kB다. 지금 level 1은 13.3 ms에 12.1 kB다. 크기는 2.5배 작지만 11%만 빨라서 넣지 않았다.

**Wi-Fi 펌웨어 패닉(관찰, 2번).**
1. 2026-11-06 18:58:29 UTC(03:58 KST, 부팅 후 약 2시간 37분)부터:
   - wpa_supplicant가 1초마다 `CTRL-EVENT-SCAN-FAILED ret=-5`를 냈다.
   - `wpa_state=DISCONNECTED`, scan 결과 0개였다. 드라이버는 `slsi_net_open: Fail: called when MLME in blocked state`를 냈다.
   - 05:49 KST에 발견할 때까지 2시간 넘게 그대로였다.
   - 그 앞 30분 동안 Wi-Fi 로그는 없다. 커널 링 버퍼는 이미 덮였다.
   - `ip link set wlan0 down/up`은 EIO로 실패했고, 재부팅으로 살렸다.
2. 21:34:10 UTC(06:34 KST, 재부팅 뒤 6분):
   - `platform_wdog_isr: INT received 126`(WLBT watchdog) → `mxman_fail: WLBT FW failure 0x0` → `WLBT FW PANIC: 0x04`, `Reason: 'panicmon_isr'`, `Auto-recovery: off` 순서로 나왔다.
   - 그 순간 Mac이 scp로 3.5 MB를 받기 시작했다. 앞선 같은 크기의 scp 두 번은 괜찮았다.
   - ftrace 수집은 4분 반 전에 끝났다.
   - 로그는 보드 `/var/tmp/wifi-panic-0634.txt`와 Mac `~/e850-logs/wifi-panic-0634.txt`에 있다. 앞선 부팅들의 journal도 보드에 남아 있다(지속 journal).
- `scsc_mx.disable_recovery_handling=1`이다. 그래서 펌웨어가 패닉하면 재부팅할 때까지 Wi-Fi가 없다.
- 커널 로그가 초당 약 130줄이다. 대부분 카메라 경고다(`is_video_poll is fail(65)`, `output(0x2) is NOT completed`, `dvfs table is NOT selected`, `is_group_shot`/`tasklet_csis_str_otf: N callbacks suppressed`). 그래서 링 버퍼가 몇 분 만에 덮이고, journal이 288 MB다.
- 원인은 모른다. 가설(측정 안 함): 신호가 −66~−69 dBm이고, 펌웨어가 roaming scan을 한다(지난 부팅 로그에 `ROAM_SCAN_STARTED`, RSSI −67, threshold −75). 큰 송신 중에 펌웨어가 멈출 수 있다.

**남은 것.**
- Wi-Fi 안정화(다음 세션).
  - 자동 복구(`disable_recovery_handling=0`) 시험은 커널이 멈출 수 있어서 사용자가 있을 때 한다.
  - 재현 조건(큰 송신, roaming)을 찾는다.
- 카메라 경고 로그가 초당 130줄 나온다.
- watchdog 데몬(initramfs가 띄움)은 CPU7도 쓸 수 있다(0.4%). 커널 unbound kworker도 CPU7에 올 수 있다(지도에서 0.1% 미만).
- 보드의 `/tmp`는 부팅 때 비워진다. 남길 측정 파일은 `/var/tmp`에 둔다.
- 8080에는 여전히 인증이 없다.

**보드에 바꾼 것.**

| 경로 | 변경 |
|---|---|
| `/usr/local/share/dvsview/dvsctl.py`, `dvsctl.html` | b53fd0b9…(pin, keep-alive, NODELAY, timeout, 오류 뒤 연결 닫음), 5171b486…(20/30 fps) |
| `/etc/systemd/system.conf.d/e850-cpu7.conf` | 추가(39e8c1a4…), `CPUAffinity=0-6` |
| 재부팅 3번 | 06:02(Wi-Fi 복구), 06:28(CPU7 설정 확인), 06:39(Wi-Fi 패닉 복구) KST |
| Wi-Fi 절전 | 시험으로 껐다가 다시 켰다(바뀐 것 없음) |
| `/var/tmp/cpumap2/`, `/var/tmp/wifi-panic-0634.txt`, `/var/tmp/webbench.py` | 측정 결과와 도구 |

### 11.31 Wi-Fi 펌웨어 패닉: 원인 조사, 자동 복구 켬(재연결 5.6~6.5초) (2026-11-07)

사용자 요청: Maxwell(mx140) 펌웨어 패닉의 원인을 조사하고, 드라이버 자동 복구를 시험한다. 효과가 있으면 부팅 때 적용한다.

**패닉 경로(소스, `drivers/misc/samsung/scsc`).**
- 펌웨어가 스스로 패닉하면 WLBT reset request(IRQ 126, WDOG)를 올린다.
  - `platform_wdog_isr()`(`platform_mif_3830.c`) → `panicmon_isr()`(`panicmon.c`) → `mxman_fail(SCSC_PANIC_CODE_FW << 15)` 순서로 간다. 로그의 `WLBT FW failure 0x0`는 "출처 = 펌웨어"라는 뜻이다.
  - `mxman_failure_work()`가 펌웨어가 공유 메모리에 쓴 R4 panic record(v2, 53워드)를 읽는다. 패닉 코드는 [3]번 워드다.
- **`WLBT FW PANIC: 0x04`의 뜻은 호스트 소스에 없다.** 펌웨어 내부 번호이고, 드라이버에는 번호→이름 표가 없다.
  - 로그의 `syserr in 0, subcode 0x4: L7 -> L8`은 subsystem 0, subcode 4라는 뜻이다.
  - 펌웨어 기호 정보가 없어서 기록의 레지스터 값(예: [21] `8018e7b4`)을 함수 이름으로 바꿀 수 없다.
- 그다음은 `disable_recovery_handling`에 달렸다.
  - 1(AOSP 기본, 주석 "enable in init.rc"): 서비스를 얼린 채로 둔다. 그리고 `disable_recovery_until_reboot = true`가 된다. **그래서 패닉 뒤에 0을 써도 소용없다.** 패닉 전에 써야 한다.
  - 0: `srvman_unfreeze_services()` → `scsc_mx_module_reset()`(Wi-Fi 드라이버 remove/probe) 순서로 간다.
    - Wi-Fi 드라이버는 STA `wlan0`이 올라가 있으면 L8을 L7로 낮춘다(`Reducing reset level: L8->L7`). 그래서 Android 프레임워크 없이 스스로 복구한다.
    - 복구 순서: `slsi_failure_reset` → `slsi_chip_recovery`(서비스 열기, hcf 올리기, 시작).
    - 다시 연결하는 일은 wpa_supplicant가 한다.
- WDOG IRQ는 펌웨어를 다시 올릴 때마다 해제하고 새로 등록한다(`platform_mif_unmap`/`map`). 그래서 복구 뒤에 나는 패닉도 잡힌다.
- `CONFIG_SCSC_WLBT_AUTORECOVERY_PERMANENT_DISABLE`은 꺼져 있다(`e850-out:/.config`).
- **패닉을 일부러 일으키는 방법:** `/proc/driver/mxman_ctrl0/mx_panic`.
  - 쓰기가 `mxman_force_panic()` → `MM_FORCE_PANIC` 메시지를 보낸다. 펌웨어가 스스로 패닉하므로 실제와 같은 IRQ 126 → `panicmon_isr` 경로를 탄다. 패닉 코드만 `0xf0`으로 다르다.
  - **읽기만 해도 패닉한다**("allow test in user builds"). 권한이 0666이라 아무 계정이나 할 수 있다. `cat`하지 말 것.
  - `mx_fail`(호스트가 일으키는 실패)과 `scsc_mx.syserr_command`(레벨 8)도 있다. 시험에는 쓰지 않았다.
- 런타임 쓰기: `/sys/module/scsc_mx/parameters/disable_recovery_handling`은 `S_IWUSR`라 root가 쓸 수 있다.

**지난 패닉 기록(관찰).**
- 06:34 패닉(부팅 −1): 지속 journal에 R4 기록 53워드가 모두 남아 있다. 사본은 `~/e850-logs/wifi/panic0634-full.txt`(카메라 경고는 뺐음)다.
  - 패닉 앞 30초 동안 Wi-Fi 쪽 로그는 없다. roaming 기록도 없다(그 부팅 전체에서 `ROAM_SCAN_STARTED` 0번).
  - 링크는 5 GHz(`…36:d6`)였다. 그 순간 Mac이 scp로 3.5 MB를 받기 시작했다(11.30).
- 03:58 패닉(부팅 −3, 18:58 UTC): **기록이 없어졌다.**
  - journal이 6분에 16 MB씩 쌓이고 288 MB에서 지워진다. 그래서 약 1시간 50분만 남는다. 이 부팅의 journal은 지금 20:13 UTC부터만 있다.
  - Mac의 `uart.log`에도 없다. live가 콘솔 printk를 1로 낮춘 뒤로는 커널 로그가 UART로 나가지 않는다.
- journald가 커널 메시지를 놓친 적은 없다. 부팅 4개 모두 `Missed N kernel messages`가 0번이다.

**시험 1: 런타임으로 복구를 켜고 강제 패닉 4번(07:14~07:19 KST).**
- 방법:
  - 보드: `tools/board/wifi-statelog.sh`가 0.2초마다 operstate, wpa_state, BSSID, IPv4를 `/var/tmp`에 적는다.
  - Mac: `tools/host/webprobe.py`가 0.25초마다 `:8080/stats`에 새 연결로 요청한다(제한 1초).
  - 두 시계는 NTP로 맞춰져 있고 차이는 0.2초 안이다.
  - live(`dvslive`)는 그대로 돌렸다.
- `echo 0 > …/disable_recovery_handling` 다음에 `echo 1 > /proc/driver/mxman_ctrl0/mx_panic`.

| 번 | 시각(KST) | 링크 끊김 | wpa COMPLETED | 8080 첫 응답 끝 | 다시 붙은 BSS |
|---|---|---|---|---|---|
| 1 | 07:14:38 | +0.11 s | +6.39 s | +6.42 s | 5 GHz `…36:d6` |
| 2 | 07:16:47 | +0.26 s | +5.68 s | +5.85 s | 5 GHz |
| 3 | 07:17:42 | +0.24 s | +5.58 s | +5.60 s | 2.4 GHz `…36:d7` |
| 4 | 07:18:37 | +0.02 s | +6.50 s | +6.81 s | 5 GHz |

- 4번 모두 복구됐다. `mx_recovery_count`가 4가 됐다. `dvslive`, `e850-wifi`, `wpa_supplicant@wlan0`은 모두 active였다.
- 1번의 시간선(journal):
  - +0.73 s: 펌웨어 정지(`mxman_stop`, PMU reset)
  - +0.77 s: 다시 올림. +0.87 s `MM_START_IND`, +1.17 s 서비스 시작
  - +1.17 s: wpa_supplicant의 scan이 한 번 실패(−5)
  - +2.12 s: 38채널 전체 scan 시작. 끝나는 데 4초 걸림
  - +6.13 s: 연결 시작. +6.39 s 완료
  - **재연결 시간의 대부분은 전체 scan이다.**
- 부작용(관찰):
  - 복구 중 카메라 CSI가 `interrupt lost(2)`를 두 번 냈고, `frame is drop`이 1장 났다. 장치 IRQ가 모두 CPU0에 있다.
  - 4번째 복구 때 `WARNING … vsnprintf` 1번이 났다(`slsi_add_log_to_system_error_buffer`, taint W).
    - vendor 버그다(`dev.c`): `buf_size`가 이미 `size − pos`인데 `scnprintf(…, buf_size - pos, …)`로 한 번 더 뺀다. 기록이 2 KB를 넘으면 크기가 음수가 된다.
    - `vsnprintf`는 `size > INT_MAX`면 쓰지 않고 0을 돌려준다(WARN_ON_ONCE). 그래서 메모리는 넘치지 않는다. 진단용 버퍼 기록만 멈춘다.
    - 고치려면 `scsc_wlan.ko`를 다시 빌드해야 한다. 이번에는 하지 않았다.
  - 커널 멈춤이나 재부팅은 없었다.

**시험 2: 재현 조건(복구를 켠 채, 07:20~07:31 KST).**

| 조건 | 양 | 결과 |
|---|---|---|
| 보드 → Mac 3.5 MB(06:34와 같은 크기), ssh `cat` | 20번 | 각 0.95~1.08 s(연결 포함 26~29 Mbit/s). 패닉 0 |
| 보드 → Mac 300 MB(`head -c … /dev/zero`) | 1번 | 65 s, 36.9 Mbit/s. 5 GHz, −71~−72 dBm, tx 97.5 Mbit/s. 패닉 0 |
| Mac → 보드 100 MB | 1번 | 18.6 s, 42.9 Mbit/s. 패닉 0 |
| 보드 → Mac 300 MB + 15 s마다 `wpa_cli roam` | 2번 | 아래. 패닉 0 |

- `wpa_cli roam …36:d7`(2.4 GHz)은 5번 모두 `FAIL`이었다. `roam …36:d6`(이미 연결된 BSS)은 같은 BSS 재결합 5번이 됐다.
  - 5번째 재결합은 AP가 거절했다(`ASSOC-REJECT status_code=1`, 07:26:06). 끊긴 뒤 scan하고 4.6 s 만에 2.4 GHz에 붙었다. 그동안 송신은 8.7 Mbit/s로 떨어졌다.
  - 07:29:32에 펌웨어가 스스로 roaming했다(`ROAM_SCAN` → `ROAM_AUTH_COMPLETE` → 5 GHz).
- 약 11분, 송수신 합 약 1.1 GB(70 MB + 300 MB + 100 MB + 600 MB), 재결합 5번, 끊김 1번, 펌웨어 roaming 1번 동안 **패닉은 0번**이었다. 복구 횟수도 4 그대로였다.
- **재현하지 못했다.** 패닉 빈도는 2.6시간에 1번, 6분에 1번이었다. 그래서 11분 시험으로는 큰 송신, roaming, 신호 가설을 버릴 수도 굳힐 수도 없다.
- 이번 부팅에서도 저절로 대역을 오갔다(22:11:36 5 GHz, 22:11:52 2.4 GHz). 07:06에는 roaming scan 사이에 Mac의 scp가 끊겼다(`Host is down`, 1분 안에 돌아옴). 패닉은 아니었다.

**원인(가설, 측정 안 함).**
- 펌웨어와 드라이버 판이 다르다. 펌웨어는 Android 10 vendor 이미지의 `S612_E3830_C4_Y19W51A`(2019-12-20)다. 드라이버는 Android 13 BSP의 11.20.0.1.0이다.
- 부팅 때 `slsi_get_qsf_mib_data: Type mismatch`, `Failed to read regdb freq_ranges`가 나온다. 맞는 펌웨어가 없어서 시험할 수 없다.
- 0x04의 뜻을 알려면 펌웨어 로그가 필요하다.
  - 펌웨어 로그는 시작 뒤 DRAM 링(`mxl` 2 MB)으로 간다. 패닉 때 `scsc_log_collector`가 이것을 모으지만, 꺼낼 사용자 공간 데몬(wlbtd)이 없어서 버려진다(`WLBTD not running ?`).

**부팅 때 적용(바꾼 것).**
- `tools/linux510/e850-wifi.sh`: `modprobe scsc_mx disable_recovery_handling=0`.
  - `e850-wifi.service`가 모듈을 직접 올린다. scsc 모듈은 `/etc/modprobe.d/e850-blacklist.conf`에 있어 자동으로는 올라오지 않는다.
  - 보드에 넣었다(md5 `47489536…`, 저장소와 같음). 이전 판은 `/root/e850-wifi.sh.prev-recovery`(7d4af31c…)다.
- 07:34 KST에 재부팅했다. 부팅 직후 `disable_recovery_handling`은 `0`이었다. `e850-wifi`, `dvslive`, `wpa_supplicant@wlan0`은 active, 8080은 200이었다.
- 07:57 KST에 사용자가 다시 재부팅했다(ACM 정상). 부팅 설정만으로 강제 패닉(07:58:53): 끊김 +0.18 s, `wlan0` COMPLETED +6.47 s(2.4 GHz), 8080 첫 응답 끝 +7.31 s. `mx_recovery_count` 1. **복구된다.**

**재부팅 뒤 ACM 콘솔이 안 올라옴(관찰, Wi-Fi와 무관).**
- 07:34 재부팅 뒤 Mac에 `/dev/cu.usbmodeme850_961`이 없었다. 보드 UDC 상태는 `not attached`였다.
- UART 로그(이 부팅 5.905 s)에 `dwc3_otg_start_gadget does not have any gadget`가 2번 찍혔다. `e850-vbus`가 VBUS를 알린 시점에 gadget이 아직 컨트롤러에 붙지 않은 것이다.
  - 앞 부팅은 pullup → VBUS → `start_gadget`(타이머 시작) 순서였고 정상이었다.
  - 그 뒤 다시 시도하는 곳이 없다. VBUS는 하드웨어가 아니라 `e850-vbus.ko`가 한 번 알리는 것이라 케이블을 다시 꽂아도 소용없다.
- root로 `rmmod e850_vbus; modprobe e850-vbus`하면 VBUS를 다시 알려 붙을 것으로 본다. **시험하지 못했다.** sudo로 하려던 순간 사용자가 재부팅했다.
- 07:57 재부팅에서는 ACM이 정상으로 올라왔다. 경합은 가끔 난다.

**남은 것.**
- 다음 실제 패닉(0x04)에서 복구되는지는 아직 못 봤다. 경로는 강제 패닉과 같다(IRQ 126 → `panicmon_isr`).
  - 복구가 켜져 있으면 패닉이 눈에 띄지 않는다. `cat /proc/driver/mxman_ctrl0/mx_recovery_count`와 `mx_lastpanic`(코드)으로 확인한다. 둘 다 재부팅 전까지 남는다.
  - journal은 약 1시간 50분만 남는다. 기록을 보려면 그 안에 `journalctl -g 'WLBT FW PANIC|panic_record'`로 꺼내야 한다.
- 카메라 경고 로그를 줄이면 journal이 더 오래 남는다.
- `e850-vbus` 경합: initramfs에서 UDC 상태를 보고 VBUS를 다시 알리는 방법을 찾는다.
- `mx_panic`이 0666이다. 웹 앱 말고 다른 사용자가 생기면 권한을 줄인다.

**보드에 바꾼 것.**

| 경로 | 변경 |
|---|---|
| `/usr/local/bin/e850-wifi.sh` | `modprobe scsc_mx disable_recovery_handling=0`(47489536…). 이전: `/root/e850-wifi.sh.prev-recovery` |
| `/sys/module/scsc_mx/parameters/disable_recovery_handling` | 07:14 런타임으로 0(재부팅 전까지), 07:34부터 부팅 때 0 |
| Wi-Fi 펌웨어 | 강제 패닉 4번(07:14~07:19), 모두 복구 |
| 재부팅 2번 | 07:34 KST(부팅 설정 확인, ACM 안 올라옴), 07:57 KST(사용자, ACM 복구) |
| Wi-Fi 펌웨어(부팅 뒤) | 07:58:53 강제 패닉 1번, 복구 |
| `/var/tmp/wifirec/` | `statelog.sh`, `roamloop.sh`, `blob`(3.5 MB), 상태·roam 기록, journal 발췌 |
| `/var/tmp/panic0634-full.txt` | 06:34 패닉 전후 journal(카메라 경고 뺌) |

Mac: `~/e850-logs/wifi/`(`panic0634-full.txt`, `rec1/`: `web.log`, `state.log`, `t0.txt`, `tx.log`, `roam.log`, `journal*.txt`, 부팅 뒤 시험은 `web3.log`, `state3.log`).

### 11.32 journal 로그 줄이기(60초 7816 → 38줄), e850-vbus 경합 수정 (2026-11-07)

서비스 작업 전 안정화의 1·2단계다. 목표는 두 가지다. 다음 실제 Wi-Fi 패닉(11.31)의 기록이 journal에 남게 한다. 재부팅 뒤 ACM 콘솔이 가끔 안 올라오는 문제를 고친다.

**journal을 채우던 것(관찰, 11.31 부팅, 60초 7816줄 = 초당 130줄).**

| 출처 | 초당 | 메시지 |
|---|---|---|
| fimc-is(카메라, bypass) | 약 106줄 | 프레임마다 `is_video_poll is fail(65)`, `output(0x2) is NOT completed`, `dvfs table is NOT selected` |
| s3c2410_wdt(워치독) | 20줄 | 200 ms마다 두 클러스터 `Watchdog cluster N keepalive!`, ping마다 RTC 읽기(`s2m_rtc_read_time`) |

- 카메라 메시지 세 가지는 bypass 모드(ISP 체인 없음, `sensor->ischain == NULL`)에서는 정상 동작이다.
  - `is_video_poll`의 반환값 65는 poll mask(POLLIN|POLLRDNORM)다. 드라이버가 0이 아니면 모두 오류로 찍었다.
  - VC0 프레임은 태그가 없어서 `out_flag`가 지워지지 않는다.
  - DVFS 테이블은 ischain이 있을 때만 선택된다.
- 워치독 드라이버는 ping마다 RTC를 읽어 `wdt->tm`에 넣는다. 이 값을 읽는 곳은 없다.
- 초당 130줄이면 journal(상한 약 288 MB)이 약 1시간 50분만 남는다(11.31). 패닉 기록을 그 안에 꺼내야 했다.

**바꾼 것 1: 카메라(fimc-is.ko).**
- `modules/s5krc1s-pablo/patches/quiet-bypass-log.patch`를 새로 만들었다. `apply.sh`가 적용한다(`patch -N`, 다시 돌려도 됨).
  - 세 메시지는 ischain이 있을 때만 찍는다. `is_video_poll`은 `EPOLLERR`일 때만 오류로 찍는다.
- 커널 볼륨에만 있던 디버그 출력 2개(`[bypass] shot G%d`, `[bypass] FS G%d`, `pr_info_ratelimited`)도 지웠다. 저장소 스크립트에는 없던 것이다.
- `fimc-is.ko`(f1e7bbf3…)를 보드에 넣었다. 이전 판은 `/root/fimc-is.ko.prev-11.31`(099c323b)이다.
- 08:14 KST 재부팅 뒤: live fps 104, coverage 100, crc 0. 60초 journal은 7816 → 1185줄이었다. 남은 것은 모두 워치독이다.

**바꾼 것 2: 워치독(s3c2410_wdt.ko).**
- `modules/s3c2410-wdt/`를 새로 만들었다. `apply.sh`가 `quiet-keepalive.patch`를 커널 볼륨에 적용한다.
  - `Watchdog cluster %u keepalive!` 2곳: `dev_info` → `dev_dbg`.
  - `s3c2410wdt_gettime()`: RTC 읽기를 뺐다. ping한 태스크, 시각, CPU 기록은 그대로 둔다.
- `s3c2410_wdt.ko`는 1단계 모듈이라 initramfs에 들어 있다. 그래서 부트 이미지를 다시 만들었다.

**바꾼 것 3: e850-vbus 경합(e850-vbus.ko).**
- 원인(소스로 확인): `dwc3_exynos_probe`는 비동기로 돈다. 먼저 `dwc3_otg_start`로 OTG 상태 기계를 시작하고, 그 뒤에 `otg_set_peripheral`로 gadget을 붙인다.
  - 그 사이에 `e850-vbus`가 VBUS를 알리면 `dwc3_otg_start_gadget does not have any gadget`로 끝난다(11.31의 07:34 부팅).
  - 다시 시도하는 곳이 없다.
- 수정: `e850-vbus`가 VBUS를 알리기 전에 `dotg->otg.gadget`이 생길 때까지 10 ms 간격으로 최대 5초 기다린다. 몇 ms 기다렸는지 찍는다(`VBUS on for 13600000.usb after N ms`).
  - `struct dwc3_exynos`와 `struct dwc3_otg`는 glue 드라이버 안에만 있다. 그래서 `Kbuild`에 `-I$(srctree)/drivers/usb/dwc3`를 넣었다.

**부트 이미지 `~/e850-logs/boot-dvs3/boot.img`.**
- boot-dvs2와 비교했다.
  - `cmdline.txt`와 `merged.dtb`는 같다.
  - initramfs에서 다른 파일은 `s3c2410_wdt.ko`, `e850-vbus.ko`, `fimc-is.ko` 3개뿐이다(`diff -rq`). 커널 Image는 그대로다.
- rootfs의 `/lib/modules/.../`에도 같은 `s3c2410_wdt.ko`(dfd6bae7…)와 `e850-vbus.ko`(0990ffa3…)를 넣었다. 이전 판은 `/root/*.ko.prev-11.32`다.
- 08:27 KST에 굽고 재부팅했다.

**결과(측정).**
- 60초 journal: **38줄**(커널 23, systemd-networkd 7, wpa_supplicant 4, 기타 4).
  - 남은 것은 Wi-Fi 이벤트다: Radio Measurement 요청과 응답, EAPOL 재키, 멀티캐스트 필터.
  - keepalive는 0줄이다.
  - 부팅 한 번에 약 19000줄이 나온다(loglevel=8).
- journal 보존 기간(추정, 측정 아님): 11.31의 16 MB/6분 ÷ 초당 130줄 → 줄당 약 340바이트다. 분당 38줄이면 하루 약 19 MB이고, 288 MB면 약 2주다. 부팅마다 약 6.5 MB가 더 든다.
- 워치독 동작:
  - ping 로그가 없어도 보드는 리셋되지 않는다.
  - `to-fastboot`(워치독 데몬을 죽임)를 하자 31초 뒤 LK가 `Abnormal RST_STAT: 0x1000000`으로 fastboot에 들어갔다. 이전 이미지와 같다(약 32초).
- e850-vbus: boot-dvs3로 5번 부팅했다(첫 부팅, `systemctl reboot` 3번, 워치독 리셋 1번).
  - 5번 모두 ACM이 올라왔다.
  - 5번 모두 `after 0 ms`였다. 즉 이번에는 경합 구간에 한 번도 걸리지 않았다.
  - **기다리는 경로가 실제로 돈 적은 아직 없다.** 수정이 맞는지는 `after N ms`(N > 0)가 찍힌 부팅이 나와야 확인된다.
  - journal에 남은 옛 모듈 부팅은 4번이다. 그중 1번(07:34)에서 경합이 났다. 그 전 부팅 기록은 journal에서 지워졌다.
- 매 부팅 뒤 확인: `disable_recovery_handling` 0, `dvslive`, `e850-wifi`, `nginx`, `e850-hdmi` active. live fps 104, crc 0.

**ACM 우회 방법 시험(11.31에서 "시험 못 함").**
- 정상 상태에서 시험했다. 경합 상태에서는 시험하지 못했다.
- Wi-Fi ssh에서 `sudo sh -c "rmmod e850_vbus; modprobe e850-vbus"`를 실행했다.
  - ACM이 바로 끊겼다가 3초 안에 돌아왔다(UDC `configured`).
  - root 자동 로그인도 됐다.
- **ACM 콘솔 안에서 하면 안 된다.**
  - `(setsid sh -c "rmmod …; …; modprobe …" &)`로 해 봤다. `rmmod`까지만 돌고 `modprobe`는 돌지 않았다. 다시 올리는 것은 ssh로 했다.
  - 원인(확인): ACM 셸은 `serial-getty@ttyGS0.service` cgroup 안에 있다. pam_systemd 세션이 없다. 그 unit은 `KillMode=control-group`이다.
  - ACM이 끊기면 unit이 끝나고, systemd가 cgroup 안의 프로세스를 `setsid`한 것까지 모두 죽인다.
  - ACM에서 USB를 끊는 명령은 `systemd-run`으로 따로 띄워야 한다(시험 안 함).

**판단: 패닉 전용 기록 서비스는 만들지 않는다.**
- Wi-Fi 패닉은 커널을 멈추지 않는다. `process_panic_record`가 패닉 기록을 커널 로그로 내고, 그것이 persistent journal에 남는다.
- journal은 이제 약 2주(추정) 남는다. `mx_recovery_count`와 `mx_lastpanic`은 재부팅 전까지 남는다.
- 펌웨어 내부 로그(DRAM `mxl` 링)는 여전히 꺼내지 못한다(11.31).

**보드에 바꾼 것.**

| 경로 | 변경 |
|---|---|
| `/lib/modules/.../fimc-is.ko` | bypass 로그 끔(f1e7bbf3…). 이전: `/root/fimc-is.ko.prev-11.31`(099c323b) |
| `boot` 파티션 | `~/e850-logs/boot-dvs3/boot.img`(08:27 KST). 이전: `boot-dvs2` |
| `/lib/modules/.../s3c2410_wdt.ko` | keepalive 로그·RTC 읽기 뺌(dfd6bae7…). 이전: `/root/s3c2410_wdt.ko.prev-11.32`(81815cba) |
| `/lib/modules/.../e850-vbus.ko` | gadget 기다림(0990ffa3…). 이전: `/root/e850-vbus.ko.prev-11.32`(d011b3b8) |
| 재부팅 | 08:14(fimc-is), 08:27(boot-dvs3), 08:33~08:37 `systemctl reboot` 3번, 08:45 워치독 리셋 1번(`to-fastboot`) |
| `e850-vbus` | 08:38 ACM에서 `rmmod`(ACM 끊김), 08:41 ssh에서 `modprobe`(돌아옴), 08:44 ssh에서 `rmmod; modprobe`(끊겼다가 돌아옴) |

### 11.33 Wi-Fi vendor 버그 2개: system error log 크기(vsnprintf WARN), `mx_panic` 0666 (2026-11-07)

서비스 작업 전 안정화의 3단계다. 11.31에서 찾은 vendor 드라이버 버그 두 개를 고쳤다.

**버그(소스로 확인).**
- `scsc_wlan`의 `dev.c` `slsi_add_log_to_system_error_buffer()`:
  - `buf_size`는 이미 `log_buf_size − pos`(남은 크기)다. 그런데 `scnprintf(…, buf_size - pos, …)`로 한 번 더 뺐다.
  - 4 KB 버퍼에 2 KB가 넘게 쌓이면 크기가 음수가 된다. `vsnprintf`가 `WARN_ON_ONCE(size > INT_MAX)`를 내고 0을 돌려준다. 11.31에서는 4번째 복구 때 났다.
  - 메모리는 넘치지 않았다. 대신 그 뒤의 기록이 버려졌다.
  - 함께 고친 것:
    - 두 번째 `scnprintf`가 메시지를 형식 문자열로 썼다. 메시지에 `%`가 들어가면 엉뚱한 메모리를 읽는다.
    - `pos`를 mutex 밖에서 읽었다.
  - 버퍼를 비우는 곳은 `mgt.c`의 vendor 명령(Android HAL)뿐이다. 그래서 이제는 4 KB가 차면 기록이 멈춘다(WARN 없음).
- `scsc_mx`의 `mxproc.c`: `/proc/driver/mxman_ctrl0/mx_panic`이 0666(소유자 root)이었다.
  - 읽기만 해도 `mxman_force_panic()`이 돈다(vendor 주석: "allow test in user builds").
  - 그래서 아무 사용자나, `/proc`를 훑는 프로그램도 Wi-Fi 펌웨어를 패닉시킬 수 있었다.
  - 0600(root만)으로 바꿨다. 읽어도 패닉하는 동작은 그대로 두었다.
  - 다른 제어 파일(`mx_fail`, `mx_freeze`, `mx_suspend`)은 0664이고 소유자가 root:root다. 일반 사용자는 쓸 수 없어서 두었다.

**바꾼 것.**
- `modules/scsc-wifi/vendor-fixes.patch`와 `apply.sh`를 새로 만들었다. 커널 볼륨에 적용했다(다시 돌려도 됨).
- `make modules`: `mxproc.o`와 `dev.o`만 다시 컴파일됐다(`~/e850-logs/build-1133scsc.log`).
- strip한 `scsc_mx.ko`(ee2b5944…)와 `scsc_wlan.ko`(a02d408e…)를 rootfs `/lib/modules/.../`에 넣었다.
  - 이전 판은 `/root/scsc_mx.ko.prev-11.33`(8e5b6760), `/root/scsc_wlan.ko.prev-11.33`(fca97188)이다.
  - 이 모듈은 switch_root 뒤 `e850-wifi.service`가 rootfs에서 올린다(initramfs에서는 `autoload.deny`의 `^scsc_`로 뺀다). 부트 이미지는 바꾸지 않았다. initramfs의 scsc 사본은 옛 판이지만 쓰이지 않는다.
- 08:58 KST에 재부팅했다.

**시험(측정).**
- `mx_panic`은 `-rw------- root root`다. philip으로 `cat`과 `echo 1 >`를 해 보니 둘 다 `Permission denied`였다. 복구 횟수는 0 그대로였다(패닉 안 남).
- root(ACM)로 강제 패닉 9번(`tools/board/wifi-panicloop.sh 9`, 패닉 사이 8초):
  - `mx_recovery_count`는 9가 됐고, `mx_lastpanic`은 `0xf0`이었다.
  - 2~9번째: 쓰고 나서 `wlan0` COMPLETED + IPv4까지 6.3~6.7초였다. 6번째만 9.5초였다. 1번째 시간은 ACM 출력에서 명령 줄과 붙어 잘렸다.
  - 시험 동안 커널 `WARNING:`은 **0줄**이었다.
  - 11.31에서는 4번째 복구 때 WARN이 났다. 그렇다면 복구 한 번에 500 B 넘게 쌓인다. 9번이면 4 KB를 넘었을 것으로 본다(버퍼 위치는 읽을 방법이 없어 추정이다).
- 이 부팅의 커널 WARN 13줄은 모두 `v4l_querycap`(`v4l2-ioctl.c:1108`, udev `v4l_id`가 카메라 노드를 열 때)이다.
  - 부팅 때마다 나는 원래 WARN이다. taint W는 이것 때문에 처음부터 켜져 있다.
  - 이번에는 고치지 않았다. 11.34에서 고쳤다.
- 시험 뒤: `dvslive`, `e850-wifi`, `nginx`, `e850-hdmi` active. 8080 `/stats` fps 104, crc 0.
- 카메라 `frame is drop`/`interrupt lost`가 13줄 났다. 11.31에서 본 복구 중 부작용과 같다(장치 IRQ가 모두 CPU0).
- 이 부팅에서도 `e850-vbus: … after 0 ms`였다(boot-dvs3로 6번째 부팅).

**보드에 바꾼 것.**

| 경로 | 변경 |
|---|---|
| `/lib/modules/.../scsc_mx.ko` | `mx_panic` 0600(ee2b5944…). 이전: `/root/scsc_mx.ko.prev-11.33`(8e5b6760) |
| `/lib/modules/.../scsc_wlan.ko` | system error log 크기·형식 문자열·lock(a02d408e…). 이전: `/root/scsc_wlan.ko.prev-11.33`(fca97188) |
| 재부팅 | 08:58 KST |
| Wi-Fi 펌웨어 | 09:00:28~09:02:26 KST 강제 패닉 9번, 모두 복구 |
| `/var/tmp/wifi-panicloop.sh` | 강제 패닉 반복 시험(`tools/board/wifi-panicloop.sh`와 같음) |

### 11.34 카메라 `v4l_querycap` WARN 수정(부팅마다 13번, taint W) (2026-11-07)

11.33에서 남긴 부팅 때 WARN을 고쳤다.

**원인(소스로 확인).**
- `v4l2-ioctl.c` `v4l_querycap()`:
  - 드라이버를 부르기 전에 `cap->device_caps = vfd->device_caps`, `cap->capabilities = vfd->device_caps | V4L2_CAP_DEVICE_CAPS`로 채운다.
  - 드라이버가 돌아온 뒤 `WARN_ON(cap->device_caps != vfd->device_caps)`를 본다(1108번 줄). `WARN_ON_ONCE`가 아니라서 부를 때마다 난다.
- 카메라 드라이버의 `vidioc_querycap` 31개 중 28개가 끝에 `cap->device_caps |= cap->capabilities;`를 한다.
  - `capabilities`에는 이미 `V4L2_CAP_DEVICE_CAPS`(0x80000000)가 들어 있다. 그래서 `device_caps`가 늘 달라진다.
  - 나머지 3개(clhc, lmec, lmes)에는 이 줄이 없다.
- `vd.device_caps`는 `is-video.c:1741`에서 방향에 맞게 이미 설정된다(`VIDEO_CAPTURE_DEVICE_CAPS` 또는 `VIDEO_OUTPUT_DEVICE_CAPS`).
- 부르는 쪽은 부팅 때 udev `v4l_id`다(카메라 노드 13개, WARN 하나에 backtrace 약 44줄). `ov5647-cap.py`(dvslive)는 QUERYCAP을 부르지 않는다.

**바꾼 것.**
- `modules/s5krc1s-pablo/patches/querycap-device-caps.patch`를 새로 만들었다. 28개 파일에서 그 한 줄을 지운다. 드라이버가 더하는 비트는 `capabilities`에 그대로 남는다.
  - `apply.sh`가 적용한다(`quiet-bypass-log.patch` 다음).
- 전체 `apply.sh`를 다시 돌렸다(exit 0). 파일 복사와 `sed -i`로 mtime이 바뀌어 카메라 디렉터리 전체(114개 객체)가 다시 컴파일됐다.
  - 다시 빌드한 `is-cis-s5krc1s.ko`는 strip 뒤 보드 것과 md5가 같다(0b35f571). 빌드가 재현되고, 내용이 바뀐 것은 `fimc-is.ko`뿐이다.
- `fimc-is.ko`(ca6c8863…)를 보드에 넣었다. 이전 판은 `/root/fimc-is.ko.prev-11.34`(f1e7bbf3)다. 10:32 KST에 재부팅했다.

**결과(측정).**
- 이 부팅의 커널 `WARNING:` 0줄, `v4l_querycap` 0줄이다. 수동으로 `/lib/udev/v4l_id /dev/video102`를 한 번 더 돌린 뒤에도 0줄이었다.
- `/proc/sys/kernel/tainted`: 12800(W+O+E) → **12288(O+E)**. 이제 W가 켜지면 진짜 WARN이다.
- `v4l_id` 결과: `ID_V4L_PRODUCT=exynos-is-ss1`, `ID_V4L_CAPABILITIES=:video_output:`. `/dev/v4l/by-path` 링크도 있다.
- live: fps 104, coverage 100, crc 0. `dvslive`, `e850-wifi`, `nginx`, `e850-hdmi` active. `e850-vbus: … after 0 ms`(boot-dvs3로 7번째 부팅).

**보드에 바꾼 것.**

| 경로 | 변경 |
|---|---|
| `/lib/modules/.../fimc-is.ko` | querycap `device_caps` 줄 뺌(ca6c8863…). 이전: `/root/fimc-is.ko.prev-11.34`(f1e7bbf3) |
| 재부팅 | 10:32 KST |

### 11.35 상자 크기 측정 1차 시험: 손에 든 상자는 기존 `dvs_boxes`로 재지 못한다 (2026-11-07)

11.29부터 보류했던 상자 측정(`--boxes`)을 다시 시작했다. 이번에는 요구 사항을 정하고 짧은 시험을 했다. 코드는 바꾸지 않았다.

**정한 요구 사항(사용자 답).**

| 항목 | 내용 |
|---|---|
| 최종 목표 | 컨베이어로 흘러가는 상자를 **1.5 m 거리**에서 보고 가로·세로·**높이**를 잰다 |
| 카메라 배치 | 벨트 위에서 수직으로 내려다본다 |
| 벨트 속도 | 모른다. 컨베이어 장치는 아직 없다 |
| 정확도 | 대략 ±1~2 cm |
| 출력 | 파일(JSONL) + HDMI + 웹 |
| 시험 | 거리를 줄여서 먼저 한다 |
| 방법 | 기존 `dvs_boxes()`(`tools/linux510/dvsview.c`)를 확장한다 |
| 시험 상자 | 가로 19 cm, 세로 15 cm, 높이 5 cm |

**설계 구상(spec을 쓰지 않았고 구현도 하지 않았다).**
- 1단계 가로·세로는 지금 코드 그대로다. 8×8 셀 → convex hull → 최소 넓이 사각형, 상자가 화면 가장자리에서 떨어진 프레임의 중앙값을 쓴다.
  - mm로 바꾸려면 `--mm-per-px`를 보정해야 한다. 렌즈 화각을 몰라서 크기를 아는 물체로 잰다.
- 2단계 높이는 운동 시차로 구한다. 윗면은 카메라에 가까워 화면에서 더 빨리 움직인다.
  - 식: 카메라~벨트 거리를 H, 상자 높이를 h, r = 윗면 모서리 속도 / 바닥 모서리 속도라 하면 r = H/(H−h)이고 h = H(1 − 1/r)이다.
  - 사각형 변 중 주점 c가 변의 바깥쪽에 있는 변이 바닥 모서리다. 그 변을 c를 중심으로 r배 하면 윗면이 된다. 윗면 크기는 화소 × mm_per_px / r이다(mm_per_px는 벨트 면 기준).
  - 움직이는 방향으로 c가 상자 범위 안에 있는 샘플은 뺀다.
  - 속도 기준은 상자 자신의 바닥 모서리다. 그래서 상자가 바닥 면에 놓여 있어야 한다. 손에 든 상자로는 높이를 잴 수 없다.

**시험 방법.**
- 카메라는 사용자를 향했다(거리는 재지 않았다). 사용자가 상자를 손에 들고 카메라 앞에서 옆으로 여러 번 옮겼다. 지나가는 모양은 매번 달랐다.
- 보드에서 live를 상자 측정과 함께 띄웠다. 창은 기본 5 ms다.
  ```sh
  mkdir -p /var/tmp/boxbench; systemctl stop dvslive
  systemd-run --unit=dvsbox-test /usr/local/bin/dvslive.sh --boxes /var/tmp/boxbench/boxes.jsonl --mm-per-px 1
  # 끝나면
  systemctl stop dvsbox-test; systemctl start dvslive
  ```
- `--mm-per-px 1`이라 기록의 `length_mm`, `width_mm`과 화면 글자 `mm`는 화소다.
- 시간은 12:14:32~12:17:22 KST였다.

**결과(측정).**
- 기록은 80개였다(`~/e850-logs/boxbench-boxes1.jsonl`).
- 처음 9개(12:14:32~53)는 256~391 × 151~225 화소, 샘플 44~499개였다. 상자를 옮기기 전이라 사람의 움직임을 잡은 것으로 본다(추정).
- 나머지 71개는 제각각이었다.
  - 길이 48~515 화소, 폭 7.5~228 화소, 길이:폭 비율 1.17~11.7이었다.
  - 9개는 `partial`이었다. 화면 가장자리에서 떨어진 프레임이 없었다는 뜻이다.
  - 샘플 수의 중앙값은 9개였다. 43개 기록이 샘플 30개 이하였다.
- 상자 비율 1.27 근처(1.1~1.45)의 기록은 3개뿐이었다. 317×228(샘플 226), 57×40(25), 56×48(2)이다. 셋 다 그림에서 본 상자 크기(약 110×140 화소)와 맞지 않는다.
- live 로그의 프레임당 이벤트는 1만~3.7만 개였다.
- 시험 중 8080에서 받은 미리보기에서 원인이 보였다.
  - 상자 위쪽 모서리가 직선으로 보인다(x 495~610, y 290~430). 그 아래로 상자를 든 손과 팔뚝의 이벤트가 이어진다.
  - 그린 사각형은 약 120 × 308 화소(y 282~590)다. 세로 범위가 상자와 팔을 합친 범위(y 290~600)와 같다. hull이 손과 팔까지 묶었다.
  - 사각형은 이벤트보다 약 130 화소 왼쪽에 있다. 원인은 확인하지 않았다.

![손에 든 상자: 위쪽 직선이 상자 모서리, 아래쪽 곡선이 손과 팔. 흰 사각형은 dvs_boxes가 찾은 것(글자의 mm는 화소)](img/boxbench-hand-20261107.png)

**해석.**
- `dvs_boxes()`는 화면에서 움직이는 것이 상자 하나뿐이라고 전제한다(`dvsview.c` 머리 주석 "One box in view at a time"). 손에 든 상자는 손과 팔이 같이 움직여서 떼어 낼 수 없다. 사람이 보이는 장면에서는 사람의 움직임도 상자로 잡힌다.
- 따라서 이 시험은 전제와 맞지 않는 장면이었다. 알고리즘의 정확도는 이 시험으로 판단할 수 없다.

**다음 방향(사용자 결정 대기).**
1. (권장) 손이 보이지 않게 시험 방법을 바꾼다.
   - 상자를 책상이나 판 위에 놓고 긴 자나 막대로 밀거나 끈으로 당긴다. 사람은 화면 밖에 있는다. 카메라는 위에서 내려다보게 둔다.
   - 컨베이어와 같은 조건이라 지금 코드를 그대로 시험할 수 있다. 높이(2단계)로도 그대로 이어진다.
2. 손이 보여도 되게 알고리즘을 바꾼다.
   - 이벤트에서 직선을 찾아 직사각형을 이루는 네 선만 상자로 본다. 손과 팔은 곡선이다.
   - 새 검출기를 만들어야 해서 일이 크다. spec부터 써야 한다.
3. 손을 상자 뒤에 숨기고 다시 해 본다. 코드는 바꾸지 않지만 팔과 몸이 보일 수 있다.

**보드에 바꾼 것.**

| 경로 | 변경 |
|---|---|
| `/var/tmp/boxbench/boxes.jsonl` | 시험 기록 80줄. Mac 사본은 `~/e850-logs/boxbench-boxes1.jsonl` |
| `dvsbox-test` | 임시 유닛. 멈췄다. `dvslive`를 다시 시작했다(active, `--boxes` 없음, fps 104) |

### 11.36 DVS 사람 행동 분류: 선행 연구 조사, 850 추론 실측, 설계 (2026-11-07)

사용자 요청: "우선 사람인식을 하게 해, mipi 입력단에서 바로 인식했으면 하는데 hdmi로 표시하기전에". 이어서 "사람의 행동을 분류하는것을 하고 싶어, 나타났다, 움직인다. 넘어진다", "카메라가 아니라 dvs로 할꺼야", "전이 학습으로 했으면해 … 찾아봐". 상자 크기 측정(11.35)은 별개 과제로 그대로 둔다(사용자: "과제가 다른거니까").

**정한 것(사용자 답).**
- 목적은 사람 기록(파일)이다. 첫 판은 옆에서 보는 고정 DVS, 한 사람이다.
- 넘어짐은 앉기·천천히 눕기와 구별한다.
- 우리 센서 녹화는 우선 동작당 5~10번이다.
- 방법은 "850에서 현실적인 것"으로 정했다. 아래 실측을 근거로 A′를 골랐다.
  - A′: 60 ms 이벤트 수 프레임 → ImageNet 사전 학습 MobileNetV3-small → GRU, 초당 16번 판정.
- 설계는 `docs/superpowers/specs/2026-11-07-dvs-person-action-design.md`에 있다(spec 검토 대기). 코드는 쓰지 않았다.

**선행 연구(논문 값, 재현하지 않음).** 자세한 표는 spec 3.1절에 있다.
- Samsung Research 2017(DVS 640×480): 프레임 10장을 쌓은 TSN, F1 0.955, Jetson TX1 CPU 31 fps.
- Sony·Intel 2025(IMX636): 60 ms 프레임, 16 Hz. 넘어짐 F1은 CNN+MLP 46%, CNN+S4D 77%, MCUNet+S4D 84%였다. 시간 상태가 결과를 크게 갈랐다. 구조는 이 연구를 따른다.
- 공개 데이터는 PAF(DAVIS346, falling 대 bending 등)와 DailyDVS-200(MIT, fall down·walk·sit down·stand up·pick up 등)이다. 두 데이터 모두 "lie down" 클래스는 없다.
- 채택하지 않은 것과 이유:
  - 점군 방식(TTPOINT, EventMamba): 샘플당 0.6~1 GFLOPs. 우리 이벤트는 대부분 잡음이라 점을 뽑기 전에 걸러야 한다.
  - CLIP 기반(EZSR, EventCLIP): ViT라서 판정 한 번에 수 초다.
  - 큰 영상 모델(MViT, C2D): 클립당 수십 GFLOPs다.

**보드 확인.**
- CPU는 A55 최대 2002 MHz다. 플래그에 `asimdhp`는 있고 `asimddp`는 없다.
- 쓸 수 있는 NPU가 없다. `/sys/class/misc/npu_throughput`은 PM QoS 노드다. GPU는 `mali0` 커널 드라이버만 있다.
- 메모리는 3.5 GB 중 2.9 GB가 비어 있다.
- 보드 numpy의 행렬곱은 0.4 GFLOPS였다. BLAS가 참조 구현(netlib `libblas3`)이라 CPU 성능을 나타내지 않는다.
- 보드에 pip가 없다.

**실측(버린 시험).**
- 방법:
  - Mac에서 무작위 가중치 모델(MobileNetV3-small, 첫 conv 2채널 + Linear 576→128 + GRUCell 128 + Linear 128→8)을 ONNX(opset 17)로 내보냈다.
  - ONNX Runtime 1.31.1 aarch64 wheel과 flatbuffers, packaging, protobuf를 보드 `/var/tmp/ortspike`에 풀어 `PYTHONPATH`로 불러왔다.
  - live(`dvslive`)가 도는 상태에서 `taskset`으로 쟀다. 300번 반복했다.
- 결과:

| 항목 | CPU5 한 코어 | CPU4~6 세 코어 |
|---|---|---|
| 모델, 입력 2×120×160 | 중앙값 27.2 ms (p95 27.5, 최대 27.6) | 14.6 ms (p95 14.9) |
| 모델, 입력 2×60×80 | 14.3 ms (p95 14.7, 최대 28.5) | 8.2 ms (p95 8.4) |
| numpy 프레임 만들기 72,000 / 126,000 / 300,000 이벤트 | 5.6 / 7.8 / 13.6 ms | — |

- 해석:
  - 160×120 입력으로 16 Hz를 돌리면 한 단계에 33~35 ms가 걸린다. 예산 62 ms 안이고, 코어 하나의 55~60%다.
  - 설계 때 추정(코어당 3~5 GFLOPS)보다 2~3배 느렸다. 43 MFLOPs에 27 ms면 실효 약 1.6 GFLOPS다.
  - 재는 동안 live 로그는 104 fps, draw 7.3 ms, ingest 2.9~3.9 ms(max 5.1)였다. 다만 확인한 것은 마지막 8초(세 코어 구간)뿐이다.
- 재지 않은 것: 같은 프로세스 안의 스레드로 돌릴 때의 GIL 영향, 웹 응답 영향.

**보드에 바꾼 것.** 없다. `/var/tmp/ortspike`는 시험 뒤 지웠다. 시험 파일의 Mac 사본은 Claude scratchpad에만 있다(저장소 밖).

### 11.37 사람 행동 분류 구현 1: 학습 데이터 읽기 (2026-11-07)

구현 계획(`docs/superpowers/plans/2026-11-07-dvs-person-action.md`)을 사용자 승인("좋아") 뒤 `dvs-person-action` 브랜치에서 실행하기 시작했다. 과제 1은 이벤트 파일 읽기다.

**만든 것.** `tools/host/act/readers.py`의 `read_events(path)`는 클립 하나를 `Events(t, x, y, p, w, h)`로 돌려준다. t는 0부터 시작하는 int64 µs이고, p는 0 = ON, 1 = OFF다.
- `.aedat`(PAF, DAVIS346 AER-DAT2.0): `#` 머리글 줄 뒤에 big-endian (주소, 시각) 쌍이 온다. 주소 비트 배치는 PAF의 `snn.py`를 따랐다. 바로 세우려고 x = 345 − x, y = 259 − y로 뒤집는다. `snn.py`가 그림을 위아래로 뒤집어 보여 주기 때문이다.
- `.aedat4`(DailyDVS-200, 320×240): `aedat` 패키지로 이벤트 패킷을 이어 붙인다.
- `.bin`(보드 녹화를 `dvsview --replay`로 풀어낸 것, 과제 2): `struct ev` 8바이트, 960×720. 시각은 첫 값과의 uint32 차이로 펼치므로 71.6분마다 넘어가는 표시 시계를 가로질러도 된다.

**확인한 것.** `python -m unittest test_readers`: 3개 통과, 1개 건너뜀.
- PAF 예제 `toy.aedat`: 이벤트 896,992개, 5.04초, 346×260, x 0..345, y 0..259, OFF 59%.
- `.bin`: 합성 데이터로 y의 SUP·OFF 비트 분리와 시계 넘김을 확인했다.
- `.aedat4`: 받은 파일이 없어 시험하지 못했다.

**확인하지 못한 것.** PAF 극성은 `snn.py`의 주석(비트가 0이면 ON)을 따랐다. jAER 원본이나 장면으로 검증하지 않았다. 틀리면 PAF만 ON/OFF 채널이 뒤바뀐다.

**데이터.** 아직 받지 않았다. 클래스별 클립 수는 받은 뒤 적는다.
- PAF(Fall Detection, Action Recognition)는 Tongji SharePoint에 있다. 자동으로 목록을 받는 것은 막혔다. 사용자가 브라우저로 받아야 한다.
- DailyDVS-200은 Google Drive에 `action_<번호>` 폴더로 나뉘어 있다. 필요한 폴더만 사용자가 받는다. 나눔 목록(`train/val/test.txt`)과 동작 설명은 받아 두었다.

**보드에 바꾼 것.** 없다.

### 11.38 사람 행동 분류 구현 2: 보드 분류 스레드, 무작위 모델로 성능 시험 (2026-11-07)

계획 과제 2~4다. 모델 가중치는 아직 무작위다. 학습은 데이터가 온 뒤 과제 7~8에서 한다. 여기서는 보드에서 분류를 돌려도 live 화면과 웹이 느려지지 않는지 본다.

**만든 것.**
- `dvsview.c`(과제 2):
  - `dvs_events(dst, max, cursor)`: 분류기가 링에서 새 이벤트를 받아 간다. RING/2보다 뒤처지면 건너뛰고, 건너뛴 수를 `/stats`의 `act_lost`에 센다.
  - `dvs_record(path)` / `dvs_record(NULL)`: 줄마다 패킷 단어와 0 단어 하나를 gzip으로 녹화한다. 쓰기가 실패하면 녹화만 닫고 ingest는 계속한다.
  - 시험 빌드 `t --replay in.gz out.bin`: 녹화를 다시 디코딩한다.
  - selftest에 세 가지를 더했다.
- `dvsact.py`(과제 3·4):
  - 60 ms 이벤트 수 프레임(2×120×160)을 만드는 `Binner`, 상태 판정 `Judge`, JSONL 한 줄을 만드는 `line()`.
  - `start()`: 캡처 프로세스 안의 데몬 스레드 `dvs-act`다. CPU4~6에 묶고, uclamp min은 1024로 둔다.
  - onnxruntime이나 모델이 없으면 `[dvsact] off: 이유`를 쓰고 live만 돈다.
- `tools/host/act/model.py`, `export.py`: MobileNetV3-small(ImageNet) + GRU를 ONNX로 내보낸다. 무작위 가중치에서 torch와 onnxruntime의 차이는 max_abs 1.8e-7이었다.
- `ov5647-cap.py --act FILE --act-model PATH`. `dvslive.sh`는 `/var/lib/dvsact/enable`이 있을 때만 이 인자를 붙인다. 그 디렉터리는 philip 소유라 root 없이 켜고 끈다.
- `dvsctl.pin(cpus, name)`: CPU 집합과 스레드 이름을 받게 했다.
- `tools/board/dvsact-install.sh`: root가 ACM 콘솔에서 실행한다.

**보드에 바꾼 것.** ACM root 콘솔에서 `sh /var/tmp/dvsact-stage*/dvsact-install.sh`를 실행했다. 다시 돌려도 출력이 같았다(세 번 확인).

| 파일 | sha256 앞 16자리 |
|---|---|
| `/usr/local/lib/libdvsview.so` | 44cc24a277803335 |
| `/usr/local/share/dvsview/dvsctl.py` | 3a5cea10b1ade61c |
| `/usr/local/share/dvsview/dvsact.py`(새 파일) | a5e24f2de5944f6e |
| `/usr/local/bin/ov5647-cap.py` | 0b82333007c0bbed |
| `/usr/local/bin/dvslive.sh` | 6d1fc9ad16d6c14f |
| `/usr/local/lib/python3.11/dist-packages`: onnxruntime 1.31.1, flatbuffers, packaging, protobuf | 파일 목록 digest 090c029ea922a7c8 |

- 설치 전 원본은 `.prev-act`로 남겼다(`libdvsview.so`, `dvsctl.py`, `ov5647-cap.py`, `dvslive.sh`). 되돌리려면 이 넷을 제자리에 복사하고 `dvsact.py`를 지운 뒤 dvslive를 다시 시작한다.
- 새로 만든 디렉터리: `/var/lib/dvsact`, `/var/tmp/dvsrec`(philip 소유).
- 보드에는 원래 onnxruntime, packaging, protobuf, flatbuffers가 없었다. 그래서 시스템 패키지를 가리지 않는다.
- 지금 분류는 꺼져 있다(`enable` 없음). 무작위 모델과 그 기록 2줄은 지웠다.

**첫 5분 시험(무작위 모델)에서 찾은 문제.**
- 계획 기준의 숫자는 대부분 맞았다:
  - 단계 최대 37.8 ms, lost 0.
  - webbench 중앙값 28.1~29.8 ms(켜기 전 30.7).
  - 104 fps. 시작 직후 2초만 77 fps였다.
- 기준에 없던 문제가 있었다. 장면이 바쁠 때 `[dvsact] steps` 줄이 4~6초 동안 나오지 않았다. 그 뒤 2초에 97~107단계가 몰려 나왔다. 분류가 실시간보다 몇 초 밀린 것이다.
- 원인을 보드 한 코어(CPU5)에서 쟀다. Python 프레임 만들기(`split` + `Binner.push`)가 칸당 6만/15만/30만 이벤트에 19/40/63 ms였다.
  - 모델 한 단계가 29 ms이므로, 칸당 이벤트가 약 10만을 넘으면 60 ms 예산을 넘는다.
  - 잡음만으로 칸당 약 8만 이벤트다(1.3M/s).
  - 11.36의 5.6~13.6 ms는 미리 나눈 배열에 `bincount`를 한 번 한 값이었다. Binner 전체 비용이 아니었다.
- 연산별로 나눠 쟀더니 numpy로는 30만 이벤트에 약 25 ms가 남았다. `bincount`가 12.6 ms, gather가 9 ms였다.

**고친 것.**
- 칸 세기를 C `dvs_cells()`(libdvsview)로 옮겼다.
  - 보드는 `Binner.push_ev(struct ev)`를 쓴다.
  - 학습용 `frames_of`는 numpy `push()`를 그대로 쓴다.
  - 둘이 같은 프레임을 내는지는 `test_push_ev_c`로 묶었다(Docker Debian numpy 1.24와 보드에서 통과).
- `searchsorted` 키가 Python int여서 배열 전체를 int64로 복사하던 것을 `np.uint32`로 바꿨다.
- 덩어리의 시각 폭이 3초 이하이면 공백 검사를 건너뛴다.
- 결과는 칸당 6만/15만/30만 이벤트에 3.9/6.8/13.0 ms였다. 모델을 더해도 30만 이벤트 칸이 42 ms다.

**고친 뒤 시험: 같은 장면에서 켬 5분, 끔 5분.**

| 항목 | 분류 켬 | 분류 끔 |
|---|---|---|
| `[dvsview]` fps | 104 (시작 직후 2초만 78.9) | 104 (시작 직후 2초만 93.2) |
| ingest 평균 / 2초 최대의 중앙값 | 2.94 / 3.3 ms | 3.57 / 4.1 ms |
| ingest 최대 > 7 ms인 2초 구간(158개 중) | 4 (시작 구간 포함, 최대 57.5) | 10 (시작 구간 포함, 최대 61.5) |
| 버퍼 평균 크기 | 180 kB | 192 kB |
| webbench 중앙값(100회 × 3) | 28.1 / 28.1 / 28.1 ms | 27.6 / 27.7 / 29.0 ms |
| `[dvsact]` | 2초당 30~38단계(밀림 없음), 단계 중앙값 29.2 ms, 최대 37.7 ms, lost 0 | — |

- 해석:
  - 분류는 live 화면과 웹을 느리게 하지 않는다.
  - 계획 기준 "ingest 최대 ≤ 7 ms"는 끈 상태에서도 자주 넘는다. 장면(이벤트 양)과 서비스 시작에 따른 것이다.
  - 남은 차이는 서비스 시작 직후 2초 동안 fps가 더 떨어지는 것(78.9 대 93.2)뿐이다. onnxruntime을 불러오고 세션을 만드는 구간으로 보이지만 확인하지 않았다.
- 재지 않은 것:
  - 학습된 모델에서의 판정 품질.
  - 사람이 크게 움직일 때의 이벤트 양. 이번 장면은 버퍼당 180~192 kB였다.
  - `dvs-act`가 CPU4~6에서 render 스레드(이번에는 CPU5)와 겹칠 때의 영향을 따로 떼어 재지 않았다.

### 11.39 사람 행동 분류 구현 3: 녹화 `/rec`과 페이지 버튼 (2026-11-07)

계획 과제 5다. 우리 센서로 동작을 녹화하는 장치다. 녹화는 학습용 원자료가 된다. 실제 동작 녹화는 사용자와 함께 한다(과제 6).

**만든 것.**
- `dvsctl.py`:
  - `GET /rec`은 상태를 준다(`idle`/`countdown`/`recording`, 남은 초, 마지막 파일).
  - `POST /rec {"label": .., "s": 1~30}`는 3초를 센 뒤 s초 동안 `/var/tmp/dvsrec/<UTC>-<label>.gz`에 녹화하고 옆에 `.json`을 쓴다.
    - `.json`: label, start, s, events, margin, flush_us, sensor, complete.
    - 응답: 400(라벨이나 길이가 틀림), 409(녹화 중), 507(남은 공간 300 MB 미만).
  - 녹화 중 서비스가 끝나면(SIGTERM, exit) atexit에서 녹화를 닫고 `complete: false`로 적는다.
- `dvsctl.html`: "행동 녹화" 칸을 더했다. 동작 9가지를 한글 이름으로 고르고, 길이 4/6/10/20초, 녹화 버튼, 1초마다 상태를 보여 준다.
- `test_dvsctl.py`: 가짜 lib으로 `rec_check`, 끝까지 녹화, 녹화 중 닫기, 카운트다운 중 닫기를 시험한다(Mac과 보드에서 통과).

**녹화에서 고친 것 두 가지(`dvsview.c`, 과제 2 코드).**
1. **재생이 첫 이벤트를 잃었다.**
   - 원인: 재생은 디코더를 처음 상태(열 없음, 시각 없음)에서 시작한다. 그래서 녹화 첫 줄의 COLUMN 패킷 전 이벤트를 버렸다. 시각 기준도 틀어졌다.
   - 고친 것: 녹화를 열 때 디코더 상태(`REF(raw/1000)`, `SUB(raw%1000)`, `REF(ref)`, `COLUMN(col)`)를 한 줄로 먼저 쓴다.
   - selftest (d): 중간에 시작한 녹화를 재생하면 이벤트 6개와 시각 차가 live와 같다.
2. **녹화 중 버퍼를 잃었다.**
   - 첫 보드 녹화(`none` 6초, 이벤트 10,445,245개, 27 MB) 동안 ingest가 버퍼당 6 → 16~40 ms로 늘었다. 버퍼는 35 → 24.5/s로 떨어졌다.
   - 원인: gzip(level 1)을 ingest 스레드에서 했다.
   - 고친 것: ingest는 패킷을 64 MB 메모리 큐에 복사만 한다. writer 스레드 `dvs-rec`가 gzip으로 쓴다. 녹화를 시작한 dvsctl 스레드의 CPU7 묶음을 물려받는다.
   - 큐가 넘치거나 쓰기가 실패하면 그 녹화만 `-EIO`가 되고, ingest는 막히지 않는다.
   - selftest (e): 녹화하며 디코딩한 시간이 녹화 없이 디코딩한 시간의 2배를 넘으면 실패한다. Docker에서 고치기 전 23.8 대 4.3 ms로 실패했고, 고친 뒤 통과했다.

**보드에 바꾼 것.** ACM root 콘솔로 `dvsact-install.sh`를 실행하고 dvslive를 다시 시작했다.

| 파일 | sha256 앞 16자리 |
|---|---|
| `/usr/local/lib/libdvsview.so` | 0cb43678faa8e06b |
| `/usr/local/share/dvsview/dvsctl.py` | 80784985505cdb7e |
| `/usr/local/share/dvsview/dvsctl.html` | 9f1f55d4642fab34 |

- `dvsctl.html`의 원본도 `.prev-act`로 남았다.

**확인(고친 뒤).**
- `none` 6초 녹화: `20261107T074001Z-none.gz`, 22.6 MB, 이벤트 6,432,835개.
- 녹화하는 동안 `[dvsview]`는 104 fps, 버퍼 35/s, ingest 3.2~3.5 ms(max 3.8)였다. 녹화 전은 2.9 ms였다.
- Mac Docker 시험 빌드 `t --replay`: `replay: 6432835 events`. `.json`의 `events`와 같다.
- `readers.py`로 읽으면 5.83초, 960×720, OFF 46%다.
- 녹화는 Mac `~/e850-logs/dvsrec/`에 받았다. 보드에 rsync가 없어 scp를 썼다.
- 이 클립은 사람 없는 장면이라 `none` 학습 자료로 쓸 수 있다.

**주의.**
- 바쁜 장면은 6초에 22~27 MB다. 동작당 10번 × 9가지를 녹화하면 약 2 GB인데, 보드 `/`에는 1.8 GB가 비어 있다. 녹화 묶음마다 Mac으로 옮기고 보드에서 지운다. 300 MB 아래에서는 507로 거절한다.
- 큐가 넘치는 경우(64 MB, 9 MB/s에서 약 7초 밀림)는 시험하지 않았다.

### 11.40 사람 행동 분류 구현 4: 녹화 확인·학습 도구 (데이터 대기) (2026-11-07)

계획 과제 6~8 가운데 데이터 없이 할 수 있는 부분이다. 보드에 바꾼 것은 없다.

- `tools/host/act/replay.sh DIR`: 보드 녹화(`*.gz`) 중 `.bin`이 없는 것을 Docker 시험 빌드로 푼다. 클립마다 재생 이벤트 수와 보드가 센 수(`.json`)를 나란히 찍는다. 11.39 클립에서 6,432,835 = 6,432,835였다.
- `tools/host/act/dataset.py`:
  - 클래스 대응(spec 7.1)과 활동 곡선(배경 중앙값을 넘는 수, 0.3초 가운데 이동 평균)을 계산한다.
  - 단계 라벨: 동작은 정점 + 0.3초부터, none·still은 1초가 지난 첫 칸(17번째, 1.02초)부터 손실을 건다.
  - 클립마다 `.npz`를 쓰고 `manifest.jsonl`을 만든다. 녹화 도중 끊긴(`complete: false`) 보드 녹화는 쓰지 않는다.
  - PAF 파일 이름 규칙은 아직 모른다. 경로에 클래스 이름이 있으면 그 클래스로 보고, 없으면 건너뛰고 센다.
- `tools/host/act/train.py`, `evaluate.py`:
  - 증강: 공개 데이터에 우리 센서 잡음을 더하고 솎는다. 모든 데이터에 좌우 뒤집기, 확대, 이동을 쓴다.
  - 학습: 클립 8개 배치, 손실을 건 칸만 CE(클래스 가중), AdamW, MPS.
  - 평가: 따로 뺀 클립의 혼동 행렬과 클립마다 넘어짐 기록 수.
- 확인:
  - 합성 클립 4개로 30걸음 만에 손실이 첫 값의 절반 아래로 떨어졌다(CPU 48초).
  - 11.39 `none` 클립 하나로 CLI를 돌렸다. 데이터 만들기 0.65초(97칸, 손실 80칸), MPS 학습 2 에폭, 내보내기 parity 3.3e-7, 평가까지 돌았다.
- 남은 것:
  - 공개 데이터 내려받기(사용자, 브라우저).
  - 보드 앞에서 동작 녹화(사용자, 과제 6).
  - 그 뒤 실제 데이터 만들기, 학습, 평가(과제 7 Step 5, 과제 8 Step 5~9), 그리고 live 합격 시험(과제 9).

### 11.41 사람 행동 분류 구현 5: 공개 데이터 받기와 학습 입력 만들기 (2026-11-08)

공개 데이터는 사용자가 브라우저로 받았다. 이 절에서 보드에 바꾼 것은 없다.

- **PAF (Tongji SharePoint):**
  - 파일은 `.aedat` 412개, 3.6 GB다. 헤더는 `#!AER-DAT2.0`(jAER, 2019), 346×260이고, 레코드가 모두 DVS 극성 이벤트다(APS 프레임·IMU 없음). 사람을 정면에서 찍었다(우리 설치는 옆).
  - Mac의 `~/e850-act-data/paf/{fall,action}/`로 옮겼다. 두 폴더에 내용이 같은 파일은 없다(md5).
  - 실제 폴더 이름이 `sit-down`, `get-up`이라 `PAF_MAP`에 넣었다(`bbe0906`). 이 수정 전에는 앉기·일어서기 90개가 빠졌다.
  - `action/jumping/chenjieneng_5.2.aedat`는 이벤트가 19 ms 동안만 있어 프레임이 나오지 않는다. 건너뛴다.
- **DailyDVS-200:**
  - 저자 Google Drive 폴더는 로그인 없이 열면 비어 보였다. 사용자가 Hugging Face 미러 `Charlieqaq/DailyDVS-200`에서 10 GiB씩 나눈 zip 9조각(85 GB)을 받았다. 저자 공식 링크가 아니다.
  - 합치면 85 GB가 더 필요한데 Mac 여유가 87 GB뿐이었다. 그래서 조각을 한 파일처럼 읽어 zip 목차에서 필요한 15개 클래스만 꺼냈다. 1,740개, 11 GB, 46초 걸렸고 파일마다 CRC를 검사했다.
  - zip 안 폴더는 `action_001`처럼 세 자리이고, 폴더 번호는 클래스 번호 + 1이다. 공식 분할 파일에 적힌 해당 클래스 train 1,170개, val 307개, test 308개가 모두 있다.
  - 분할 파일에는 같은 클립 555개(전 클래스)가 val과 test에 모두 적혀 있다. 학습은 train만 쓴다.
  - `test_readers`의 aedat4 시험이 처음으로 실제 파일로 돌았다(320×240, 4/4 통과).
- **학습 입력** (`dataset.py --paf --daily`, 179초, 464 MB, `~/e850-act-runs/data`): 1,432개 클립.

  | 클래스 | PAF | DailyDVS (train/val/test) | 공개 학습에 쓰는 수 |
  |---|---|---|---|
  | fall | 30 | 78 / 18 / 21 | 108 |
  | walk | 30 | 78 / 18 / 21 | 108 |
  | sit_down | 60 | 144 / 30 / 31 | 204 |
  | stand_up | 30 | 36 / 6 / 12 | 66 |
  | pick_up | 90 | 335 / 76 / 87 | 425 |
  | other | 171 | 20 / 0 / 10 | 191 |
  | none, still, lie_down | 0 | 0 | 0 (보드 녹화로만) |

  - 클립 길이 중앙값은 PAF 4.8~6.6초, DailyDVS 2.6~4.0초다.
  - 손실을 거는 칸이 0인 클립이 있다. 걷기는 PAF 6개, DailyDVS train 10개이고, other는 PAF 7개다. 정점 + 0.3초 규칙에서 걷기처럼 계속되는 동작은 정점이 클립 끝에 오기 쉽기 때문이다. 걷기·기타에 1초 이후 전체를 쓰는 안을 사용자에게 물었다.
- **남은 것:** 걷기·기타 라벨 규칙 결정, 공개 데이터 학습(과제 8 Step 5), 보드 앞 동작 녹화(과제 6).

### 11.43 사람 행동 분류 구현 6: 공개 데이터 학습과 참고 평가 (2026-11-08)

Mac에서만 했다. 보드에 바꾼 것은 없다.

- **라벨 규칙(사용자 결정):** 11.41에서 물은 걷기·기타는 "동작 시작 + 0.3초부터"로 정했다(`112715e`). 시작은 활동량이 처음으로 정점의 30%에 닿는 칸이다. 학습 클립 1,102개 중 손실 칸이 0인 걷기·기타는 없어졌다. 정점이 클립 끝에 오는 한 번짜리 동작 14개(pick_up 10, stand_up 3, fall 1)는 여전히 0이다.
- **메모리 문제:** 첫 학습은 16 GB Mac에서 36 GB(스왑 32 GB 중 31 GB)까지 올라가 2에폭에서 50분 넘게 멈췄다. 그래서 중단했다.
  - 원인: MPS가 한 스텝에 프레임당 약 9 MB를 쓴다(472프레임에 4.4 GB). 배치 모양마다 캐시 블록을 남겨 계속 커진다(12배치에 2.2 → 3.2 GB).
  - 수정(`3062a6f`): 배치를 클립 8개 이하·프레임 256개 이하로 자르고, 스텝마다 `torch.mps.empty_cache()`를 부른다. 다시 돌린 학습은 4.7~5.9 GB로 일정했다.
- **공개 학습** (`train.py --stage public --init imagenet`, PAF 전부 + DailyDVS train, 1,102개, 10에폭): 4,157초(69분). 첫 에폭은 495초, 그 뒤 에폭은 약 400초였다. 결과는 `~/e850-act-runs/public.pt`(4.5 MB).

  | 에폭 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
  |---|---|---|---|---|---|---|---|---|---|---|
  | loss | 1.726 | 1.343 | 1.149 | 1.051 | 0.957 | 0.898 | 0.856 | 0.793 | 0.762 | 0.725 |

  10에폭에서도 loss가 줄고 있었다. 더 돌리지 않은 이유는 목표가 우리 녹화로 미세조정하는 것이기 때문이다.
- **참고 평가** (`evaluate.py`, DailyDVS val·test 330개, 학습에 안 쓴 클립, CPU에서 프레임 하나씩 12분):
  - 처음 돌렸을 때 손실 칸이 없는 pick_up 7개가 `none` 열에 들어갔다. 빈 평균은 NaN이고, NaN의 argmax는 0이기 때문이다. 이런 클립은 행렬에서 빼도록 고쳤다(`test_evaluate_empty_mask`).
  - 그 7개만 다시 돌려 행렬에 0개가 들어가는 것을 확인했다. 아래 행렬은 그 7개를 뺀 323개다.

  | 라벨 \ 예측 | still | walk | sit_down | stand_up | pick_up | lie_down | fall | other | 맞음 |
  |---|---|---|---|---|---|---|---|---|---|
  | walk (39) | 0 | **39** | 0 | 0 | 0 | 0 | 0 | 0 | 100% |
  | sit_down (61) | 0 | 1 | **39** | 0 | 8 | 0 | 13 | 0 | 64% |
  | stand_up (18) | 0 | 3 | 0 | **13** | 1 | 1 | 0 | 0 | 72% |
  | pick_up (156) | 1 | 11 | 39 | 6 | **92** | 0 | 7 | 0 | 59% |
  | fall (39) | 0 | 0 | 7 | 0 | 0 | 0 | **32** | 0 | 82% |
  | other (10) | 0 | 3 | 0 | 3 | 3 | 0 | 0 | **1** | 10% |

  - 전체 216/323(67%)이 맞았다. fall로 예측한 52개 중 32개가 실제 넘어짐이었다(62%).
  - `dvsact.Judge`가 남기는 넘어짐 기록을 보드와 같은 규칙으로 셌다. 넘어짐 클립은 39개 중 35개(90%)에 기록이 1개 이상 남았다. 오경보는 앉기 61개 중 24개(39%), 줍기 163개 중 24개(15%), 기타 10개 중 1개였다. 걷기·일어서기에서는 0개였다.
  - 넘어짐과 가장 많이 헷갈리는 것은 앉기다. 넘어짐을 앉기와 구별하라는 요구(설계 2장)에서 가장 약한 곳이 바로 여기다. 받아들임 기준(설계 10장: 넘어짐이 아닌 30번 중 `fall` 오판 3번 이하, 즉 10%)과 비교하면 앉기 39%는 한참 넘는다. 공개 데이터는 정면에서 찍었고, still·lie_down·none 예가 없다. 그래서 이 숫자는 보드의 옆 설치 성능이 아니다. 미세조정 전 기준선일 뿐이다.
- **남은 것:** 보드 앞 동작 녹화(과제 6). 그다음 우리 녹화로 미세조정(`--init public.pt --holdout 2`), ONNX 내보내기와 일치 검사, 평가(과제 8 Step 6~9).

### 11.44 사람 행동 분류 구현 7: 보드 live 첫 시험, 보드 빈 화면으로 다시 학습 (2026-11-08~09)

- **공개 모델 live 시험** (2026-11-08 22:54 KST):
  - 공개 모델을 ONNX로 내보냈다(PyTorch와의 차이 max_abs 8.4e-6). 보드 `/var/lib/dvsact/model.onnx`에 놓고 `enable`을 만든 뒤, 페이지 `POST /sensor {}`로 dvslive를 다시 시작했다. 설정값은 그대로 두는 재시작이라 root가 필요 없다.
  - 결과는 HDMI에 나오지 않는다(설계 2장·11장). JSONL에만 남는다.
  - 사용자가 시계에 맞춰 앉기 2번, 일어서기 2번, 걸어 나가기를 했다. 24줄이 남았는데 `pick_up` 12, `walk` 6, `stand_up` 6이고 `sit_down`은 0이었다. p는 0.34~0.61이었다.
  - 기록은 움직인 순간에 몰렸고, 서서 가만히 있던 10초에는 없었다. 빈 화면 10초에는 7줄이 남았다.
  - 판정은 29~35 ms, 놓친 프레임 0, live 104 fps였다.
- **원인 측정** (60 ms 칸, `frames_of`):

  | | 60 ms당 이벤트(중앙값) | 이벤트가 있는 칸 |
  |---|---|---|
  | PAF (사람이 움직임) | 10,729 | 19% |
  | DailyDVS (사람이 움직임) | 5,545 | 8% |
  | 보드 빈 화면(11-07) | 66,220 | 79% |

  보드는 아무도 없어도 배경 이벤트가 공개 데이터에서 사람이 움직일 때보다 6~12배 많다. 공개 데이터는 정면이고 빈 화면·가만히·눕기가 없다.
- **기준선:** 빈 화면 2분(22:59~23:01, 시작과 끝을 `/frame.png`로 확인)에 공개 모델이 14줄을 남겼다.
- **보드 빈 화면 녹화:** 사람 없이 `POST /rec {"label":"none","s":6}`를 30초 간격으로 20번 했다(23:01~23:11).
  - 모두 1.01 M events/s로 같았다(사람이 있을 때는 약 2.5 M/s).
  - 11-07 녹화와 합쳐 21개다. Mac `~/e850-act-data/ours/`로 옮겼고(445 MB, 50초), `replay.sh`로 재생한 이벤트 수가 보드가 센 수와 21개 모두 같았다.
- **학습 단계 `mix`** (`8c98901`):
  - `public.pt`에서 시작해 공개 학습 클립과 보드 클립을 함께 쓴다.
  - 공개 클립에는 가짜 Poisson 잡음 대신 보드 `none` 클립의 한 구간을 얹는다. 얹는 것은 확대·이동 뒤라서, 사람만 움직이고 센서 잡음은 제자리에 있다.
  - 보드 클립은 클래스마다 최근 2개를 평가용으로 뺀다.
- **1차 mix (실패):**
  - 학습에 안 쓴 빈 화면 2개를 모두 `walk`로 보았다. 배경을 얹지 않은 PAF 걷기는 `none` 0.97로 보았다.
  - 원인: 확대·이동이 보드 클립의 가장자리 약 20%를 0으로 만들었다(20번 중 20번). 공개 클립은 배경이 덮어 0이 없었고, 실제 보드 입력에도 0인 칸이 없다.
  - 확인: 평가용 빈 화면의 왼쪽 16열만 0으로 지우니 `walk 0.36` → `none 0.86`이 되었다.
  - 수정(`6832719`): 화면 밖에서 들어온 칸은 클립 자신의 값을 쓴다(`test_augment_no_empty_border`).
  - 이 학습은 Mac이 02:51~03:42에 잠들고, Acrobat 9.9 GB·Drive 6.9 GB로 스왑이 17.8 GB 차서 4시간 걸렸다. 다시 돌릴 때는 `caffeinate`로 감쌌다.
- **2차 mix** (3에폭, 811초, loss 0.755 → 0.572, `~/e850-act-runs/mix.pt`, ONNX 차이 7.3e-6):
  - 학습에 안 쓴 빈 화면 2개 중 1개를 맞혔다. 클립 안에서 `none` 확률이 처음 0.12에서 5초 뒤 0.5로 오른다. 6초 클립은 GRU가 자리 잡기에 짧다.
  - 같은 확인 방법으로 보면, 배경을 얹은 공개 학습 클립 30개를 `none`으로 본 칸은 0%이고 맞힌 칸은 87~97%다. 사람과 배경을 헷갈리는 것은 아니다.
  - **보드 live, 빈 화면:** 04:06:23~04:11:23 5분 동안 **0줄**이었다(공개 모델은 2분에 14줄). 다시 시작한 직후에만 12초째에 `appear walk`, 2초 뒤 `none`이 남았다. 판정은 29~37 ms, 놓친 프레임 0, live 104 fps였다.
  - DailyDVS val·test(배경 없는 원본) 197/323(61%)이다. 공개 모델은 67%였다. 넘어짐은 25/39를 맞혔고(공개 32/39), 넘어짐 기록은 25/39, 앉기 오경보는 19/61(공개 24/61)이었다. mix는 공개 클립을 늘 보드 배경과 함께 배웠으므로, 원본으로 하는 이 평가는 보드 조건이 아니다.
- **보드에 바꾼 것:**
  - `/var/lib/dvsact/enable`(분류 켜짐)과 `model.onnx` = mix(sha256 앞 16자리 `9b56d85cb77d6402`)를 두었다.
  - 공개 모델은 `/var/tmp/public.onnx`(`de09fab438f21b89`)에 남겼다.
  - `/var/tmp/dvsrec`에 빈 화면 녹화 20개(약 445 MB)가 있다.
  - dvslive를 3번 다시 시작했다(`POST /sensor {}`).
  - 끄려면 `rm /var/lib/dvsact/enable`을 한 뒤 다시 시작한다.
- **한계와 남은 것:**
  - 빈 화면은 하룻밤 한 조명 조건에서만 찍었다.
  - dvslive가 다시 시작될 때마다 가짜 `appear`가 한 번 남을 수 있다.
  - 옆에서 본 앉기·일어서기·넘어짐과 가만히·눕기는 여전히 사용자 녹화(과제 6)가 필요하다.

### 11.45 앉기·일어서기 녹화와 학습, 상태 페이지, 디코더 코어 분리, 핫스팟 (2026-11-09)

- **녹화** (06:45~06:50 KST):
  - Mac에서 `POST /rec`로 앉기·일어서기를 번갈아 7초씩 10번씩, 모두 20개를 녹화했다. 모두 `complete`였다.
  - 첫 시도(06:37)는 하나도 녹화되지 않았다. macOS bash 3.2가 `$( )` 안 큰따옴표 속의 `\"`를 잃고 `{…,…}`를 중괄호 확장으로 쪼갰기 때문이다. 본문을 변수에 먼저 담아 고쳤다.
  - 사용자는 큐를 보지 못했다. 포트 80에는 nginx 기본 페이지뿐이었다. 그래서 편한 속도로 앉고 서기를 반복했다. 클립 대부분에 동작이 3.5~4초 간격으로 2번 들어갔고, 처음 2개에는 움직임이 없었다.
- **동작마다 자르기** (`segment.py`, `1b0b06f`):
  - 움직임 구간을 찾는다. 기준은 활동이 중앙값의 1.6배를 넘고, 0.3초 이상 이어지는 것이다.
  - 구간 앞뒤에서 사람이 있는 열의 무게중심 행을 잰다(120행 중 서 있음 ~45, 앉음 ~65).
  - 10행 이상 내려가 58 이상이면 앉기, 10행 이상 올라가 50 이하면 일어서기로 라벨을 붙인다.
  - 4개 클립(01455, 01460, 01466, 01473)은 프레임을 그려 눈으로 맞는지 확인했다. 나머지 19개는 규칙만 믿었다.
  - 결과는 앉기 13개, 일어서기 10개다. 원본 20개는 `~/e850-act-data/sitstand-raw`로 옮겼다.
  - mix에서는 보드 클립을 에폭마다 4번씩 쓴다(`OURS_REPEAT`, `ec69062`). 보드 클립 16개가 공개 앉기·일어서기 270개에 묻히지 않게 하려는 것이다.
- **학습** (`mix.pt`에서 3에폭, 998초, loss 0.533 → 0.456, `~/e850-act-runs/sitstand.pt`, ONNX 차이 9.9e-6):
  - 학습에 안 쓴 클립으로 평가했다. 앉기 2/2, 일어서기 2/2를 맞혔다.
  - 빈 화면은 0/2로, 2개 모두 `other`로 보았다(mix는 1/2). 빈 화면 live 시험은 하지 않았다.
- **보드 판정을 앉음·서 있음만으로** (`2c36f9b`, 사용자 요청):
  - `/var/lib/dvsact/classes`(philip 소유)에 `none sit_down stand_up`을 두었다. 판정은 이 클래스의 확률만 보고 합이 1이 되게 다시 나눈다.
  - 그래서 `pick_up` 같은 다른 동작은 앉기와 일어서기 중 가까운 쪽으로 간다. 파일을 지우고 다시 시작하면 9개 클래스로 돌아간다.
  - 페이지는 상태를 "앉음 / 서 있음"으로 보인다.
- **상태 페이지** (`f656578`):
  - `dvsact.STATUS`를 `GET /act`로 내보낸다. 내용은 켜짐 여부, 판정 상태, 이번 칸의 1순위와 p, 최근 변화 10개다.
  - 페이지 맨 위에 상태를 크게 보이고 1초마다 갱신한다. 넘어짐은 빨간색이다.
  - nginx 기본 사이트를 `nginx-dvs.conf`로 바꿨다. `http://<보드>/`가 `:8080`으로 302 넘어간다.
- **빠르게 움직이면 영상이 사라짐:**
  - 07:09:42에 페이지에서 `margin_ms`가 231(기본 15)로, `fps`가 57로 저장되어 있었다.
  - 그래서 렌더러는 약 250 ms 전 이벤트를 그렸다. 정상 속도로 보드를 움직이면 링(4M 이벤트)이 그보다 먼저 덮였다. 그 결과 그릴 이벤트가 0개였다(`events` 0, 버퍼 813~846 kB, CRC 0).
  - `margin_ms`를 15로 되돌린 뒤 같은 동작에서 프레임당 최대 37.6만 개를 그렸고 0개는 없었다. 다만 coverage는 74~87%로, 디코더가 한 코어를 다 쓰고 있었다(버퍼당 27~28 ms × 35/s).
- **디코더 코어 분리** (`90d284f`):
  - A55에서 녹화 버퍼 60개로 측정했다.
    - 디코드 31 ns/이벤트, 배경 필터 39 ns/이벤트였다.
    - 버퍼 지우기와 캐시 비우기는 830 kB에 0.19 ms였다.
  - 그래서 필터를 별도 스레드(`dvs-filter`, CPU3)로 옮겼다. 필터는 디코더가 버퍼마다 깨우고 `fw`까지 처리한다. 렌더러는 필터가 켜져 있으면 `fw` 아래만 그린다.
  - 코어는 디코더(캡처 주 스레드) CPU1, 렌더러 `dvs-render` CPU2로 고정했다. 모두 uclamp min 1024다.
  - 같은 버퍼 60개에서 디코더는 4.57 → 1.23 ms이고, 필터는 2.6 ms다. 남긴 이벤트 수는 filter_ms 2/4/8에서 1,289,713 / 1,720,204 / 2,112,280으로 이전과 같다. 필터 스레드 깨우기 시험도 통과했다.
  - 보드 live, 가만히 있을 때: `ingest_ms` 4.1 → 1.0, 스레드 사용률은 디코더 6%, 필터 9%, 렌더러 47%다.
  - **빠르게 움직일 때의 부하는 측정하지 못했다.** 측정 창(07:57~08:00)에 큰 움직임이 없었다. 사용자가 눈으로 "잘 된다"고 확인했다.
  - 되돌리기: `/var/tmp/libdvsview.so.before-11.45`.
- **핸드폰 핫스팟:**
  - `wifi-add.sh`(`aabd01e`)를 만들었다. 비밀번호를 명령 줄이 아니라 입력으로 받는다.
  - 이번에는 ACM root로 `An` 블록(PSK만, `priority=1`)을 `wpa_supplicant-wlan0.conf`에 덧붙이고 `wpa_cli reconfigure`를 했다.
  - 보드는 `An`에 10.76.30.18로 붙었다. 같은 핫스팟의 맥북에서 웹(:8080, :80 → 302)과 ssh가 됐다(`-o HostKeyAlias=192.168.0.236`).
  - 핫스팟이 켜져 있으면 집 Wi-Fi보다 먼저 붙는다.
- **USB 웹(192.168.7.1) 안 됨, 해결 못 함:**
  - 보드 `usb0`는 carrier 1, UDC `configured`인데 송수신 패킷이 0이다.
  - Mac은 AppleUserECM 제어 dext만 떠 있다. 데이터 쪽(`AppleUserECMData`)은 시작하지 않았고, 링크 0, BSD 이름도 없다. ACM은 같은 케이블에서 정상이다.
  - 케이블을 다시 꽂거나 보드에서 UDC를 다시 붙이는 시험은 하지 못했다.
- **보드에 바꾼 것:**
  - `model.onnx` = sitstand(`6974f8232796fdaa`). mix는 `/var/tmp/mix.onnx`에 남겼다.
  - `/var/lib/dvsact/classes`를 두었다.
  - `dvsact-install.sh`로 `libdvsview.so`(`878ed4dfb6887969`), `dvsctl.py`, `dvsctl.html`, `dvsact.py`, nginx `dvs` 사이트를 설치했다(ACM root). dvslive를 다시 시작했다.
  - `margin_ms` 15로 돌렸다.
  - Wi-Fi `An`을 추가했다.
  - 08:01에 사용자 요청으로 `systemctl poweroff`를 했다.
- **남은 것:**
  - 빠른 움직임에서 새 디코더의 부하와 coverage를 측정해야 한다.
  - 빈 화면에서 sitstand 모델의 오경보를 확인해야 한다.
  - 분류기가 이벤트를 놓친다(`act_lost`, 한 실행에 3,400만 개). 사람이 움직이거나 화면이 흔들릴 때다.
  - 다시 시작할 때 가짜 판정이 한 번 남는다.
  - USB 네트워크가 안 된다.

### 11.46 D-PHY PLL 다시 보기: 19비트 해석, 진단 코드, 시험 준비 (2026-11-11~12)

사용자 요청(붙여 넣은 지시): main의 `docs/DVS_PLL_MINSU_KARIM_REVIEW_2026-11-10.md`를 먼저 읽는다. 센서 드라이버, setfile, PLL, CSI 수신 설정, 디코더를 검토한다. PLL 레지스터 다시 읽기와 진단을 먼저 만든다. 지금 동작하는 설정은 그대로 두고 00/8B/B0과 00/8B/F8 시험을 준비한다. CRC, 프레임 완전성, 버퍼 손실, 타임스탬프 연속성을 확인한다. 0x75를 검증된 설정으로 다루지 않는다. FACT/CAUSE/EVIDENCE/NEXT로 보고하고, 확인한 변경과 시험 문서를 GitHub에 커밋한다.

계획, 해석 표, 판정 기준, 결과 표는 [DVS-PLL-TESTS.md](DVS-PLL-TESTS.md)에 모았다. 여기에는 경과만 적는다.

**확인한 것.**
- 0x30A5~A7을 19비트 단어(P [18:13], M [12:3], S [2:0], Minsu 보고)로 보았다. 11.29 sweep에서 0x68~0x7C는 P=3, 0x80~0x8D는 P=4다. 그래서 11.29의 "M만 바꿨다"와 "0x75는 83%, 1328/1404 Mbps"는 틀렸다(11.29에 정정 줄을 넣었다).
- 00/75/00은 공식값 24×672/3 = 5376 Mbps다. 데이터시트 REV0.01의 output PLL VCO 1250~2500 MHz 밖이다. 실제 lane 속도는 모른다. 0x75는 실험값이다.
- 데이터시트 REV0.01:
  - 식은 `PLL_CLK = MCLK × M / (P × 2^S)`다.
  - S에 대해서는 "not controllable and equals 0"이라고 쓰면서, 표 11에는 S 범위가 있다.
  - Fref는 6~12 MHz, D-PHY는 lane당 2.5 Gbps, PLL lock은 표준 200·최대 1000이다.
  - 0x30A5~A7 주소, lock·상태 레지스터는 없다(UNKNOWN).
- MCLK: SoC가 주지 않는다(11.19). 24 MHz는 11.23의 계산 결론이다. 측정하지 않았다.
- 0x75 녹화 42개(273초)를 새 검사기 `tools/host/dvslink.py`로 봤다.
  - 모르는 워드 0, sub 범위 오류 0이다.
  - scan 단계 272,857개 중 4,427개(1.62%)가 scan 하나를 건너뛰었다. 2개 이상 건너뛴 곳은 없다.
  - frame_end 번호는 2,702번 하나씩 건너뛰었다.
  - 시간이 거꾸로 간 곳 12개는 모두 비트 하나 차이다(ref 워드를 잃은 것과 구분 못 함).
  - 녹화 하나에서 scan을 건너뛴 구간의 줄 경계는 6~7개(정상 4~5개)였다. 가설: CRC 없이 패킷 2~3개가 빠진다. 녹화에는 버퍼 경계가 남지 않아 어디서 빠지는지는 모른다.
- live(0x75): CRC 0, 버퍼 35/s, coverage 99~100%. 관찰 실행 `plltest.py setfile 0 600 --observe`(philip, 16:11 UTC부터 10시간, 이전 lib이라 CRC·버퍼·coverage만 잰다).

**만든 것(저장소).**
- `tools/host/dvslink.py`: 녹화(`.gz`, `.bin`)와 VC0 raw 버퍼를 검사한다. 워드 종류, 모르는 워드, 타임스탬프 단계(다음/1개 빠짐/2개 빠짐/틈/반복/뒤로/비트 하나), frame_end 번호 연속, 줄 채움을 본다. 자체 시험 통과.
- `tools/board/plltest.py`: 시험 한 번 실행기다(파라미터 → dvslive 재시작 → `/stats` 기록 → 판정 → 되돌리기). 자체 시험 통과. 보드에서는 `--observe`만 실행했다.
- CIS 모듈: `pll` 파라미터(이름 4개, 비우면 setfile), mode change·stream on 뒤 0x30A5~A7 다시 읽기(dmesg, `pll_live`). setA.h는 0x75 실험값 주석만 바꿨다.
- `dvsview.c`: `/stats`에 `link` 계수기. `-DDVSVIEW_TEST` 자체 시험 통과.
- `dvsctl.py`: `/stats`에 `pll`. 단위 시험 4개 통과.
- V4L2 sequence 세기도 `ov5647-cap.py`에 넣었지만 이 드라이버는 sequence를 늘 0으로 둔다(보드에서 확인). 그래서 지웠다. 잃은 버퍼는 타임스탬프 틈(버퍼 하나 = 약 28 scan)으로 본다.

**막혔던 것(2026-11-12 풀림).**
- 2026-11-11에는 Docker Desktop이 켜지지 않았다(`open -a Docker`, `open /Applications/Docker.app`). 11-12에는 동작했다.
- ACM 장치 노드는 Mac에 있었지만 응답이 없었고, 보드 UDC는 `not attached`였다. 사용자가 USB를 Mac mini 앞쪽 포트에 다시 꽂자 `configured`가 되고 root 셸이 돌아왔다. UART는 빠져 있었다.

**커널 소스(`e850-kernel` 볼륨).**
- DT mode0 settle은 0(자동)이다. settle은 `is_hw_find_settle()`이 `is-hw-settle-8nm-lpp.h`(10 Mbps 간격, 6500까지)에서 고른다. `ALIGN(speed, 10)`이 2의 거듭제곱용 매크로라 10 단위로 올리지 못한다. 그래서 2244는 표에 없는 값이 되어 이웃 11을, 2298은 2306이 되어 12를 받았다(dmesg). 1600은 8이다.
- PHY 0x0503.0008(`__set_phy_cfg_0503_0008_dphy`): 1500 이상에서 deskew 보정을 켜고, `skew_delay_sel`은 1500~1999 → 3, 2000~2999 → 2다. 속도 상한 검사는 없다. SoC 하드웨어 상한은 모른다.

**설치와 시험(2026-11-12, ACM root).**
- 관찰 실행을 5시간 2분에 멈췄다. CRC 0, 재시작 0, 버퍼 34.5~35.6/s, coverage 98~100%였다.
- dvslive를 멈추고 CIS 모듈을 rmmod/modprobe로 바꿨다. lib, `dvsctl.py`도 바꿨다. 다시 읽기: `00 75 00 word 0x07500 P=3 M=672 S=0`.
- `link` 계수기 첫 10초: scan 누락 159개, frame_end 누락 98개가 모두 버퍼 첫 단계였다.
- `plltest.py` 5번(각 10분, `--sample 20`, `systemd-run`). 표는 DVS-PLL-TESTS.md 8장에 있다.

| 조건 | CRC | 버퍼/s | scan 누락 | 시간 되돌아감 |
|---|---|---|---|---|
| 0x75, 수신 1600 | 0 | 35.2 | 1.58%(99.75% 첫 단계) | 24 |
| 00/8B/B0, 1600 | 0 | 37.8 | 0.92%(모두 첫 단계) | 0 |
| 00/8B/B0, 2244(settle 11) | 0 | 37.8 | 0.92% | 0 |
| 00/8B/F8, 1600 | 0 | 38.0 | 1.53% | 0 |
| 00/8B/F8, 2298(settle 12) | 0 | 38.0 | 1.53% | 0 |

- 모든 시험에서 짧은 버퍼 0, 모르는 워드 0, 잃은 버퍼 0, 재시작 0이었다. 다시 읽기는 프로파일과 같았다. raw 버퍼 200개 안에서는 누락이 없었다.
- 수신 설정을 센서에 맞춰도 결과가 같았다. scan 누락은 PLL 값에 따라 달랐지만 없어지지 않았다. 버퍼 경계 누락은 PLL과 다른 원인(CSIS 프레임 전환 쪽)으로 본다(가설).
- 10분씩 한 번이다. 반복, 1시간, 24시간은 하지 않았다. 기본 setfile(0x75)은 그대로 두었다.

**보드에 바꾼 것.**
- `/lib/modules/.../is-cis-s5krc1s.ko`: 182f06f0…(sha256, strip). 이전 판은 `/root/is-cis-s5krc1s.ko.prev-11.46`(md5 0b35f571).
- `/usr/local/lib/libdvsview.so`: db2b51bd…(sha256). 이전 판은 `.prev-11.46`.
- `/usr/local/share/dvsview/dvsctl.py`: 5ec64db2…(md5, 저장소와 같음). 이전 판은 `/root/dvsctl.py.prev-11.46`.
- `/usr/local/bin/ov5647-cap.py`: 저장소 HEAD와 같다(7d82fa17…). sequence판을 잠깐 올렸다가 되돌렸다.
- `/var/tmp`: `plltest.py`, `dvslink.py`, `pll-ab.sh`, `pll-1146/`, `dvslink-rec.tsv`, `plltest/`(결과, Mac `~/e850-logs/plltest/`에 사본).
- 파라미터는 모두 기본값(`pll` setfile, `e850_csi_speed` 0, settle 0)으로 돌아왔다. dvslive는 동작 중이다.

**첫 10시간 시도와 이상 상태(07:18~07:32 KST).** 사용자 지시로 00/8B/B0, 수신 1600, 10시간을 시작했다.
- 시작 약 30초 뒤 CSIS 경고 묶음이 나왔다(가동 24149초, `start/end overlapped`, `There's no frame`, `DMA off [8/0/0]` → `DMA on`). 그 뒤 경고는 없었지만 실시간 경로가 이상 상태로 남았다.
- 상태: 타임스탬프 비트 고침 45.6만, 오류 5.1만, 재시작 801, 10초마다 짧은 버퍼 35~51개. 타임스탬프로 센 scan이 실제 시간의 1.23배라 "누락 25%"는 실제 손실 비율이 아니다. CRC는 0이었다.
- 직전 raw 버퍼 20개는 깨끗했다. 사용자가 "128번째 줄부터 고치고 시작"을 골라 14분에 멈췄다. 원인은 모른다.

**버퍼 사이 누락의 원인: 디코더가 128번째 줄을 버렸다.**
- raw 버퍼는 모두 128줄이 찼다(DT 127줄. VRESOL mismatch는 11.22에서 통과시켰다). `ov5647-cap.py`는 `dvs_ingest(buf, W × H)`로 127줄만 넘겼다.
- Mac의 raw 버퍼 15개(연속 14경계)에서 128번째 줄에 sub 타임스탬프가 있는 경계가 3개였다. 127줄만 읽으면 그 경계 틈이 scan 2개(하나 빠짐), 128줄을 읽으면 모든 경계가 scan 1개(정상)였다.
- `W × (H + 1)`로 고쳤다(`03fdbff4…`, 보드 설치). 00/8B/B0, 수신 1600, 10분: 버퍼 22,677개 모두 128줄, scan·frame_end 누락 0, 짧은 버퍼 0, 타임스탬프 오류 0, CRC 0, **모든 판정 통과**(이벤트 중앙값 13만, 가장 바쁜 장면).
- 그래서 A/B 첫 회의 누락 차이는 PLL 효과가 아니다. 장면도 시험마다 달랐다(이벤트 중앙값 1.9만~5.7만). 녹화의 1.62%도 같은 원인으로 본다(녹화는 디코딩한 줄만 저장).

**진행 중.** 고친 디코더로 00/8B/B0, 수신 1600, 10시간(`systemd-run --unit=plltest-10h-b`, 결과 `/var/tmp/plltest/7-8bb0-rx0-10h-line128/`, 07:51 KST 시작). 사용자가 Mac mini를 재시동하므로 보드에서 혼자 돈다.

**기준 타임스탬프가 한 바퀴 돌 때 64개를 버린다(10시간 시험 중, 2026-11-12 09:01·10:11 KST).**
- [관찰] 1.18시간째(09:01:18~28 KST)와 2.34시간째(10:11:26~36 KST)에 `ts_bad` +64, `ts_restarts` +1이 났다. 다른 계수기는 모두 0이었다(CRC, scan·frame_end 누락, 짧은 버퍼, 모르는 워드, 비트 고침). 시험은 계속 돈다.
- [가설, 근거 강함] ms 기준값은 22비트(`[21:0]`)라 2²² ms = 4194.3초마다 0으로 돌아간다. `timestamp()`의 비트 고침은 ±2²¹ ms까지라, 이것을 큰 되돌아감으로 보고 64개 뒤에 다시 맞춘다. 두 사건의 간격은 4198~4218초(10초 표본)였다. 4194.3초 예측과는 4~24초 차이다. 센서 ms가 보드 시계보다 0.09~0.56% 느리면 맞는다(센서 시계는 재지 않음). 다음은 11:21:23~53 KST로 예측한다.
- 한 번에 이벤트 약 60 ms(타임스탬프 64개, 버퍼 약 2.4개)를 잃는다. 같은 때의 CSIS `start/end overlapped`는 몇 분마다 나는 경고라 원인으로 보지 않는다.
- [고침, 자체 시험만] `dvsview.c`: 차이를 2²² ms 단위로 접는다. 자체 시험에 ref 2²²−1 → 0(+1 ms)을 넣었다. 고치기 전 코드는 그 시험에서 실패한다. `dvslink.py`도 같다. 보드에는 아직 넣지 않았다(10시간 시험이 끝난 뒤 live를 다시 켜면서 넣는다).

**이상 상태가 다시 났다: 두 번 모두 Wi-Fi 펌웨어 패닉 직후(2026-11-12 10:18~10:24 KST).**
- [관찰] 10:18:26 KST(가동 34882초)부터 약 6분 동안 이상 상태였고, 저절로 풀렸다. 10초마다 짧은 버퍼 약 195개, 타임스탬프 오류 약 3,600, 고침 약 3,900, 재시작 약 49였다. CRC 0, 버퍼 42.3/s, sub 970~1000/s로 센서가 보내는 양은 그대로였다.
- [관찰] 1초 전(34881.16초)에 Wi-Fi 워치독 인터럽트와 `WLBT FW PANIC`이 있었다. 로그 수집에만 476 ms가 걸렸다. 첫 시도의 이상 상태(24149초)도 24148.39초의 Wi-Fi 패닉 직후였다. 부팅 뒤 Wi-Fi 패닉은 이 두 번(`mx_recovery_count` 2)뿐이고, CSIS `interrupt lost`·`already DMA enabled` 묶음도 이 두 때뿐이다(`journalctl -k`, root).
- [관찰] 시작 묶음(0.4초)은 `DMA off`/`on` 9번, `interrupt lost(2)` 4번, `already DMA enabled` + `frame is drop` 2번이었다. 풀린 때(35248.33초)는 `already DMA enabled`, `frame is drop`, `DMA off`/`on` 한 번이었다. 그 사이에는 커널 메시지가 없었다.
- [관찰] 이상 상태 5초 녹화(`~/e850-logs/stuck2/`, dmesg·interrupts도 함께): sub가 +188 ms 갔다가 −186 ms 돌아오는 짝이 36번 있었다. VC0 버퍼 8개 × 23.6 ms = 188.7 ms다. 사용자 공간이 받은 버퍼에 한 바퀴 전 내용이 섞였다고 본다. 녹화의 센서 시간이 1.86초뿐인 이유는 모른다.
- [가설] Wi-Fi 패닉 처리 동안 CPU0에서 CSIS 인터럽트가 늦거나 빠져서, 드라이버의 버퍼–프레임 대응이 한 칸 어긋난다. 그 뒤 DMA가 다 쓰기 전의 버퍼가 넘어온다. 다음 DMA off/on 때 다시 맞을 수 있다. 인터럽트 지연은 재지 않았다. 드라이버 코드도 보지 않았고, 일부러 재현하지도 않았다. PLL 설정과의 관계는 모른다(두 번 모두 00/8B/B0이었고, 0x75 쪽에는 Wi-Fi 패닉이 난 자료가 없다).

**10시간 결과(07:51~17:51 KST).**
- CRC 0, 모르는 워드 0, 버퍼 1,424,980개(39.6/s).
- 이상 구간 382초(10:18:17~10:24:39 KST)를 빼면 짧은 버퍼, scan 누락, frame_end 누락, 비트 고침이 모두 0이었다.
- 타임스탬프 오류는 기준값 한 바퀴 8번(× 64)뿐이었다. 간격은 4198.5~4215.9초이고, 세 번째(11:21:43)는 예측 범위 안이었다.
- `pass: false`의 원인은 이 두 가지뿐이다.
- 원 자료: Mac `~/e850-logs/plltest/7-8bb0-rx0-10h-line128/`, `~/e850-logs/stuck2/`.

**Wi-Fi 패닉 5번과 이상 상태.**
- 00/8B/B0에서 난 패닉(가동 24148·34881초)은 2번 모두 이상 상태가 됐다.
- 0x75(시험 뒤 setfile로 돈 live)에서 난 패닉(가동 69882·76560·78475초)은 3번 모두 이상 상태가 되지 않았다. 그때 live 계수기는 짧은 버퍼 2, 오류 260(시작 4 + 한 바퀴 4번)이었다.
- 76561초에는 `already DMA enabled`, `DMA off/on`이 한 번 있었다.
- 다섯 번 모두 로그 수집에 471~480 ms가 걸렸다(`Took:`).
- 표본이 작아서 PLL과 관계가 있는지는 모른다.

**드라이버 코드(`is-device-csi_v4.c`, 읽기만 함).**
- `csi_s_buffer()`는 프레임마다 다음 버퍼 주소를 DMA에 넣고 그 프레임을 PROCESS로 옮긴다. DMA가 이미 켜져 있으면 `already DMA enabled`를 찍고 그 프레임을 BAD로 PROCESS에 넣는다.
- 인터럽트 처리부는 start와 end가 겹치면 `sw_checker`로 순서를 정해 둘 다 처리한다. 한쪽을 잃으면 `atomic_inc(&csi->fcount)`만 한다.
- 버퍼–프레임 대응이 한 칸 어긋나는 경로가 실제로 어디인지는 아직 찾지 못했다(`csi_dma_tag` 710행, `wq_csis_dma_vc0` 1109행을 볼 차례).

**재현 시험(23:04~23:30 KST).** [확인] 강제 Wi-Fi 패닉 6번(프로파일마다 3번) 가운데 2번이 패닉 2초 안에 이상 상태가 됐다. 00/8B/B0에서 1/3(2회째, 23:12:49부터 시험이 끝날 때까지 120초), setfile(0x75)에서 1/3(1회째, 23:08:23부터 끝날 때까지 120초)이다. setfile 2회째는 패닉 직후 10초 표본 하나에 짧은 버퍼 1, 오류 4, 재시작 1만 있었다(이상 상태 아님). 패닉 전에는 6번 모두 이상이 없었다. 6번 모두 CRC 0이었다. **그래서 Wi-Fi 패닉이 이상 상태를 일으킨다(재현됨, 6번 중 2번). 0x75에서도 난다. PLL 설정 탓이 아니다.** 앞의 "00/8B/B0 2/2, 0x75 0/3"은 우연으로 본다. 원 자료: 보드 `/var/tmp/plltest/8-wifi-*`, Mac `~/e850-logs/plltest/8-wifi-*`. `mx_recovery_count` 6 → 12.
- `tools/board/wifistuck.sh 3`(root, `systemd-run --unit=wifistuck`): 00/8B/B0과 setfile을 번갈아 4분씩 3회 돌리고, 매번 140초째에 Wi-Fi 패닉을 한 번 낸다.
- 고친 `libdvsview.so`(c10b0382)를 넣고 한다. 이전 판은 `/usr/local/lib/libdvsview.so.prev-wrap`(db798ea1)이다.
- 결과는 `/var/tmp/plltest/8-wifi-<프로파일>-<회>/`에 남고, 분석은 `python3 /var/tmp/stuckscan.py /var/tmp/plltest/8-wifi-*/stats.jsonl`로 한다.

**이상 상태 자동 재시작(2026-11-12 23:43~23:56 KST, setfile, 강제 Wi-Fi 패닉 60초마다 10번).** `ov5647-cap.py`가 2초마다 `dvs_stats()`의 짧은 버퍼를 보고, 2초에 `--stuck`(기본 10)개 이상이면 `/dev/kmsg`에 남긴 뒤 자기에게 SIGTERM을 보낸다(dvsctl `/sensor`와 같은 경로). 그러면 스트림을 끄고 systemd가 3초 뒤 다시 띄운다. 시작 10초 동안은 판정하지 않는다. [확인] 10번 가운데 2번이 이상 상태가 됐다(14:49:17·14:53:17 UTC 패닉). 둘 다 3초 뒤 감지됐다(`stuck capture, 26 / 23 short buffers in 2 s`). 프레임은 패닉 10초·14초 뒤 다시 들어왔다. 다시 시작한 뒤 구간은 짧은 버퍼 0, 비트 고침 0, CRC 0이었다. 나머지 8번 중 1번은 짧은 버퍼 3개(기준 아래)였다. 전에는 이상 상태가 2분(시험 끝까지)~14분 갔다. [관찰] 첫 재시작은 스트림을 끈 뒤 프로세스가 SIGABRT로 끝났다(`code=killed, status=6/ABRT`). 두 번째는 정상 종료였다. 그 앞의 plltest 재시작들도 정상 종료였다. 어디서 abort했는지는 모른다(core 없음). systemd는 두 번 모두 다시 띄웠다. 원 자료: 보드 `/var/tmp/plltest/9-stuckfix-setfile*`.

**다음.**
1. 재현 시험 결과를 본다(패닉 뒤 이상 상태가 나는가, 프로파일에 따라 다른가).
2. 고친 lib으로 70분 넘게 돌려 한 바퀴 때 `ts_bad`가 0인지 본다.
3. 재현되면 CSIS 드라이버의 버퍼 처리를 더 보고, live가 이상 상태를 알아채 스트림을 다시 켜는 방법도 함께 정한다.
4. 고친 디코더로 0x75와 00/8B/B0을 번갈아 10분씩 두 번 비교한다. 그다음 24시간 시험을 한다.

### 11.47 Wi-Fi 펌웨어 패닉 0x04의 정체: 펌웨어 Data Abort(힙 할당기), 새 펌웨어(Linaro A13) (2026-11-13)

사용자 요청: "와이파이 문제 해결해". 11.46에서 Wi-Fi 펌웨어 패닉이 카메라 캡처를 어긋나게 한다는 것을 재현했다. 그쪽은 live 자동 재시작으로 막았다. 이번에는 패닉 자체를 본다.

**펌웨어 기호를 찾았다(11.31 정정).** 11.31에서는 "펌웨어 기호 정보가 없다"고 적었다. 실제로는 보드 vendor에 있었다.
- `/vendor/etc/wifi/mx140/debug/common/mx140.sym`(objdump 기호표, `nacho_s612/.../mx140.elf`)
- `log-strings.bin`(펌웨어 로그·패닉 형식 문자열)
- `mx140/debug/wlan/id.txt`(`2019-12-20 13:10 nachos612_core_hardmac_ram_gcc_integrated`, 올라가는 `mx140.bin`과 같은 빌드)
- 사본은 Mac `~/e850-logs/wifi/fw/`에 있다. 부팅 로그상 올라가는 것은 `mx140.bin`과 `mx140/conf/wlan/wlan.hcf`·`wlan_sw.hcf`, `common.hcf`다. `mx140_t`(WlanLite)는 쓰지 않는다.

**R4 기록(v2, 53워드) 읽기.** 드라이버(`fw_panic_record.c`)에 정의된 것은 [1] 길이, [51] sympathetic, [52]뿐이다.
- [확인] **[2]는 `log-strings.bin` 안의 패닉 형식 문자열 위치다.**
  - 강제 패닉은 `0x9beed` = `mxagent.c:252:CMN: SYSERR_SERVICE_MANAGEMENT/MXAGENT_FORCED_TERMINATION(0x%08x)`이고 인자 [3] = `070600f0`이다. 강제 패닉 16번 모두 같았다.
  - **저절로 난 0x04 패닉 6번(가동 24148·34881·69882·76561·78475·82585초)은 모두 `0x9cb57` = `Data Abort`다.** 펌웨어가 잘못된 메모리 접근으로 예외를 냈다. 0x04는 "data abort" 종류 번호로 본다.
- [관찰] 6번의 코드 주소 워드(`tools/host/wlbt-panic.py`로 기호 이름을 붙임):

  | 워드 | 6번의 값 |
  |---|---|
  | [30] | 모두 `panic_dabrt_handler+0x2` |
  | [43] | 모두 `undefined_instruction_handler` |
  | [33] | `pfree_set_free_list_ptr+0x6`, `dplane_dpif_resource_topup_all_rx_pools+0x12`(2번), `pmalloc_debug_validate_free_list+0x32`, `bmsg_gbuf+0x30`, `mbulk_smapper_alloc+0x0` |
  | [34] | `…0000003f`(SYS 모드, Thumb) |
  | [21] | 모두 `common_heap_mem` 안(+0x38e0~+0x4b44) |
  | [24] | 5번이 `xQueueGenericSend+0x3c`, 1번이 `ulTaskNotifyTake+0x24` |

- [가설] [33]·[34]는 abort가 난 위치와 그때 상태(SYS 모드 태스크)로 본다. 그렇다면 abort는 늘 **메모리 할당·해제나 free list 검사, 수신 버퍼 풀 채우기** 안에서 난다. 펌웨어 힙(free list)이 깨져 있고, 할당기가 그 포인터를 따라가다 abort한다고 본다.
  - 깨는 쪽은 모른다. 후보는 태스크 스택 넘침([21]이 스택 포인터라면 스택이 힙 안이다. `mbulk_smapper_alloc+0x0`은 함수 첫 명령, 곧 스택 push에서의 abort일 수 있다)이나 버퍼 넘침이다.
  - v2의 나머지 배치는 소스에 없어서 워드 뜻은 추정이다.
- [관찰] 패닉 앞 60초 동안 Wi-Fi 드라이버 로그는 없었다(4번 확인. 11.31의 06:34 패닉도 같았다).
- [관찰] 간격: 10733, 35001, 6679, 1914, 4110초. 이번 부팅 23시간에 6번(약 3.8시간에 1번)이다.
- 펌웨어는 고칠 수 없다(바이너리뿐). 근본 해결은 드라이버와 맞는 새 펌웨어다(11.31: 펌웨어 2019-12, 드라이버 A13 BSP 11.20.0.1.0). Mac과 커널 볼륨에는 다른 펌웨어가 없다.

**완화 시험 1: 절전 끄기(2026-11-13 00:12~00:47 KST, 중지).** 아래 새 펌웨어로 바꾸기 전에 멈췄다. 변수를 하나씩 바꾸려는 것이다. 35분 동안 패닉은 없었는데, 이 길이로는 아무것도 판정할 수 없다. 절전은 다시 on(기본값)이다.
- 부팅 상태는 `iw dev wlan0 get power_save` = on이었다. 드라이버는 `iw … set power_save off`를 `MLME_POWERMGT`(ACTIVE)로 펌웨어에 보낸다(`slsi_set_power_mgmt`, 연결돼 있을 때만).
- 근거(가설): abort 자리에 수신 버퍼 풀(`dplane_dpif_resource_topup_all_rx_pools`)이 있다. 그리고 절전 진입·해제는 펌웨어 수신 경로를 바꾼다.
- root `systemd-run --unit=wifi-pskeep sh /var/tmp/wifi-pskeep.sh`(`tools/board/wifi-pskeep.sh`): 30초마다 절전이 다시 켜졌으면 끄고, `mx_recovery_count`가 바뀌면 `/var/tmp/wifi-ps.log`에 적는다.
- 판정: 지금 빈도면 24시간에 패닉이 없을 확률은 약 0.2%다(e^−6.3). 그래서 24시간 동안 0번이면 효과가 있다고 본다. 1번이라도 나면 그 기록을 `wlbt-panic.py`로 보고 다음 후보로 간다.
- 시작 때 `mx_recovery_count`는 22였다(강제 패닉 포함). 15:12:16 UTC에 절전을 껐다. ping은 1.2~1.9 ms로 정상이었다.

**새 펌웨어: Linaro Android 13 BSP의 nacho_s612(2026-11-13 00:47 KST, 진행 중).** 사용자 지시: "리나로사이트에서 확인해봐".
- [확인] 출처는 Linaro GitLab `96boards/e850-96/platform/vendor/samsung_slsi`(project 33954066), 브랜치 `android13-e850-96`, 커밋 `11e9c19afbbe6c39da0a7bf32a2d17a5a77087fb`(2025-08-15, 브랜치 끝)다. E850-96 Android 13 BSP의 vendor 트리다. 드라이버(scsc 11.20.0.1.0)와 같은 BSP 세대다.
  - `mx140/firmware/nacho_s612/` 아래 `mx140.bin`: sha256 `3d94b5b5…5bd7c`, md5 `792565d8`. 펌웨어가 보고하는 판은 `O|9107|2023-03-27 09:38:37 UTC|nacho_s612|S612_E3830_R4-1_Y23W13A`이다. 같은 디렉터리 `build_id.txt`는 Y22W51A라고 적혀 있는데, 펌웨어가 보고하는 값을 기준으로 삼는다.
  - 규제 DB: `mx140/firmware/reg_domain/regulatory_db_2_7.bin`을 `slsi_reg_database.bin`으로 넣는다(sha256 `8f5da64d…`). Android 13의 `wlbt_firmware.mk`가 하는 대로다.
  - 옛 펌웨어는 Android 10 vendor의 `S612_E3830_C4_Y19W51A`(2019-12-20)다.
- 받기: `python3 tools/linux510/wifi-fw-linaro.py ~/e850-logs/wifi/wifi-a13.tgz`. 커밋을 고정해 받고 `mx140.bin` sha256을 검사한다. 154파일이고, `mx140/conf/`의 제품별 디렉터리 가운데 ERD 것만 넣는다. 바이너리는 저장소에 넣지 않는다. 다시 받은 것은 보드에 올린 것과 파일 내용이 같았다(`diff -rq`).
- 넣기(root): `sh /var/tmp/wifi-fwswap.sh a13`(`tools/board/wifi-fwswap.sh`). 처음 실행할 때 옛 `/vendor/etc/wifi`를 `/var/tmp/wifi-a10-vendor`에 복사해 둔다. 그다음 파일을 바꾸고 강제 패닉을 한 번 낸다. 복구할 때 드라이버가 `mx140.bin`과 `.hcf`를 다시 읽으므로 재부팅하지 않아도 된다. 되돌리기는 `sh /var/tmp/wifi-fwswap.sh a10`이다.
- [확인] 결과(강제 패닉 15:47:07 UTC):
  - 강제 패닉 뒤 8초 만에 다시 연결됐다(같은 AP, 5745 MHz, 192.168.0.236). 커널 로그는 `WLBT FW: …R4-1_Y23W13A`, `WLBT Driver: 11.20.0.1.0`이다. `mx140/conf/common/common.hcf`, `conf/wlan/wlan.hcf`, `wlan_sw.hcf`를 읽었고 `MIB platform: {"platform_version":"NACHO_S612_ERD"}`이다. `log-strings.bin` 868248바이트는 판 검사를 거쳐 읽혔다.
  - 보드→Mac ssh 100 MB 전송: 12.9초, 61.9 Mbit/s(신호 −68 dBm, rx 234 / tx 195 MBit/s, 한 번만 잼). 11.31의 옛 펌웨어 때는 36.9 Mbit/s였는데 신호 조건이 달라서 비교하지 않는다.
  - `common_sw.hcf` 없음(-2) 경고는 옛 펌웨어를 올릴 때도 똑같이 나왔다(가동 83774초).
  - `mx_recovery_count`는 22에서 23이 됐다(이번 강제 패닉).
- [확인] 이번 강제 패닉으로 카메라 캡처가 다시 이상 상태가 됐다. live 자동 재시작이 처리했다(`dvslive: stuck capture, 10 short buffers in 2 s`, 가동 87006.28초). 이번에는 정상 종료였고(`Deactivated successfully`), 15:47:17 UTC에 다시 떴다. 11.46 시험의 2번에 이어 세 번째 재시작이다.
- [확인] **재부팅해서 부팅 경로를 확인했다(2026-11-13 01:31 KST, `systemctl reboot`, 46초 뒤 ssh).** UART 어댑터는 꽂혀 있지 않았다.
  - 부팅할 때도 새 펌웨어가 올라왔다(`WLBT FW: …R4-1_Y23W13A`). `e850-wifi`가 같은 AP(5745 MHz)에 다시 붙었다. `mx_recovery_count`는 0이고, `e850-vbus`는 `after 0 ms`였다. dvslive는 CRC 0으로 돌았다.
  - **규제 DB를 이제 읽는다.** `slsi_read_regulatory: Regulatory Version: 2.7 , Script Version: 2 , Number of Countries: 252`가 나온다. 앞 부팅(옛 판)에서는 `Failed to read regdb freq_ranges`였다.
  - `slsi_qsf_extract_bool_data: Type mismatch for index: 0/1/2`는 그대로 나온다. 앞 부팅도 같았다. [확인, 소스] `qsfs.c`는 펌웨어에 HE(802.11ax)·TWT·WPA3 켜짐 여부를 MIB로 묻는다. 두 펌웨어 모두 값을 주지 않는다(type 4 = `SLSI_MIB_TYPE_NONE`). 그 값은 false로 남고, Android용 기능 문자열(`qsfs_feature_set`)에만 쓰인다. Wi-Fi 동작에는 상관이 없어서 고치지 않는다.
  - `cfg80211: failed to load regulatory.db`(-2)는 커널 cfg80211 쪽 메시지다. 앞 부팅들에도 있었다. slsi 드라이버는 자기 DB(`slsi_reg_database.bin`)를 쓴다.
- [확인] **전원을 껐다 켠 부팅(2026-11-13 06:13 KST, 사용자가 UART를 꽂으며 전원을 다시 넣음).** UART에 `FLEXPMU-DBG: Cold boot`, `rst_stat:0x40010000 / PINRESET`가 찍혔고 RTC는 2000-01-01로 돌아가 있었다.
  - 콜드 부팅에서도 새 펌웨어가 올라왔고 규제 DB를 읽었다. Wi-Fi는 다시 붙었다.
  - 직전 부팅(01:31~06:13)의 `WLBT FW failure`는 0번이다. 새 펌웨어로 돈 시간은 모두 합쳐 약 5.4시간(44분 + 4시간 42분)이고 패닉은 0번이다. 옛 빈도라면 이 시간에 0번일 확률은 약 24%라서 아직 판정하지 않는다.
  - 직전 부팅의 journal은 05:59:28 KST에서 끝난다. 그 뒤 Mac 감시가 5분마다 ssh로 접속했는데 그 기록이 없다. 전원이 끊기면서 디스크에 쓰이지 않은 것일 수 있다. 다만 그 접속이 실제로 성공했는지 확인하지 못해서 판정하지 않는다.
  - Mac UART 기록기를 다시 켰다(`uartlog.py`, 06:13:30 KST부터 `~/e850-logs/uart.log`). LK부터 커널 32초까지 기록됐다. 그 뒤는 dvslive가 콘솔 printk를 꺼서(`printk` 1, `ignore_loglevel` N) UART가 조용하다. dmesg와 journal에는 그대로 남는다.
  - HDMI: 부팅 때도 그 뒤에도 `LT8912B … HDMI cable: not connected`(HPD low)였다. 케이블이나 모니터 상태는 사용자가 확인하기로 했다. DVS 데이터와 웹 화면(8080)은 정상이었다(CRC 0).
  - [정정, 2026-11-14] HPD 값은 화면이 나올 때도 `not connected`였다(11.15 무렵, 1377행). 그래서 화면이 안 나온 원인의 근거가 되지 못한다. 11-14 09:2x 보드 쪽: `e850-hdmi` active, `LT8912B programmed for 1920x1080@60`, fb0 unblank(`fb-hold.py`), 렌더러 57 fps. 원인은 모른다. 모니터에 화면이 나오는지는 사용자 확인이 필요하다.
- [가설] 저절로 나던 0x04 Data Abort(약 3.8시간에 1번)는 2019년 펌웨어와 A13 드라이버가 맞지 않거나 펌웨어 자체 버그 때문이다. 새 펌웨어가 그것을 없앤다고 본다. 측정하지 않았다.
- 판정: 절전은 기본값(on)으로 두고 24시간(2026-11-14 00:47 KST까지) 강제 패닉 없이 둔다. 교체(00:47)부터 재부팅(01:31)까지 44분 동안은 패닉이 0번이었다(23 그대로). 재부팅 뒤에는 0부터 센다. 이 동안 `mx_recovery_count`가 늘지 않으면 효과가 있다고 본다(옛 빈도라면 0번일 확률 약 0.2%). 1번이라도 나면 `tools/host/wlbt-panic.py`로 R4 기록을 푼다. 이때 기호와 문자열은 새 펌웨어 것(`~/e850-logs/wifi/fw-a13/mx140/debug/common/`)을 쓴다. 확인 방법:
  `ssh philip@192.168.0.236 'cat /proc/driver/mxman_ctrl0/mx_recovery_count'`, root `journalctl -k --since "2026-11-12 15:48" | grep -E "WLBT FW PANIC|r4_panic_record\[2\]"`.

**보드에 바꾼 것.**
- `/vendor/etc/wifi`: Linaro A13 판(위)이다. 재부팅해도 남는다(rootfs). 옛 판은 `/var/tmp/wifi-a10-vendor`에 있고, 넣을 때 쓴 `/var/tmp/wifi-a13.tgz`, `/var/tmp/wifi-fwswap.sh`도 보드에 있다.
- Wi-Fi 절전: 00:12~00:47 KST에 off였다가 지금은 on(기본값)이다. `wifi-pskeep` unit은 멈췄다(`/var/tmp/wifi-pskeep.sh`, `/var/tmp/wifi-ps.log`는 남겼다).
- `/var/tmp/stuck2/panicrec.txt`: 부팅 뒤 모든 R4 기록. root `journalctl -k`에서 뽑았다.
- `/usr/local/bin/e850-wifi.sh`: 펌웨어 출처 주석만 고쳤다(md5 22c43270). 이전 판은 `/root/e850-wifi.sh.prev-11.47`(47489536)이다. `/var/tmp/wifi-fwswap.sh`도 저장소 판(7cc10009)으로 맞췄다.

**rootfs 빌드에 Wi-Fi를 넣었다(01:15 KST).** 지금까지 `build-rootfs.sh`에는 Wi-Fi가 아예 없었다. 펌웨어도 패키지도 `e850-wifi`도 없어서, 보드에는 10장과 11.31에서 손으로 넣었다. 그래서 rootfs를 새로 만들면 Wi-Fi가 없어지거나, 2019년 펌웨어(`wifi-fw-extract.sh`)를 다시 넣게 된다. 고친 것은 다음과 같다.
- debootstrap에 `wpasupplicant`, `iw`, `wireless-regdb`를 넣는다.
- `e850-wifi.sh`, `e850-wifi.service`를 설치하고 서비스를 enable한다. 서비스는 `e850-wifi.sh SSID PASSWORD`로 네트워크를 저장한 뒤에만 돈다(`ConditionPathExists`). 비밀번호는 이미지에 넣지 않는다.
- `/lib/firmware`를 만들고, `/lib/etc/wifi`를 `/vendor/etc/wifi`로 링크한다.
- `out/wifi-a13.tgz`(`wifi-fw-linaro.py`로 받은 것)가 있으면 `/vendor/etc/wifi`에 푼다. 없으면 경고를 낸다.
- `wifi-fw-extract.sh`와 `e850-wifi.sh` 머리 주석에 새 판을 쓰라고 적었다.
- [확인] Docker로 실제 빌드했다. 빌드는 성공했고(`wifi firmware: S612_E3830_R4-1_Y23W13A`, `rootfs.simg` 432 MB), 이미지를 `simg2img`와 `debugfs`로 열어 보았다.
  - 들어 있는 것: `wpa_supplicant`, `iw`, `wpa_passphrase`, `regulatory.db`, `e850-wifi.sh`(0755), `e850-wifi.service`(multi-user에 enable됨), `/lib/etc/wifi` → `/vendor/etc/wifi`.
  - 펌웨어: `mx140.bin` md5 792565d8(root 소유), `slsi_reg_database.bin` 7556바이트, `wlan.hcf`, `log-strings.bin`.
- [미확인] 이 이미지로 부팅해 보지는 않았다. `userdata`에 구우면 지금 보드의 rootfs가 지워지므로 굽지 않았다.

**다음.**
1. (끝남, 11.48) 2026-11-14 00:47 KST에 `mx_recovery_count`와 커널 로그를 봤다. 24시간 패닉 0번이다.
2. (끝남) 다음 부팅 때 regdb와 `Type mismatch` 줄을 본다. regdb는 고쳐졌다. `Type mismatch`는 해가 없다(위).
3. 새 펌웨어에서도 패닉이 나면: 그 기록을 풀고, 절전 끄기를 다시 시험한다(`wifi-pskeep`). 그다음 대역 고정(2.4/5 GHz)을 시험한다.

### 11.48 PLL A/B/A/B 비교와 00/8B/B0 24시간 시험 (2026-11-13)

사용자 지시는 다음 순서였다. Wi-Fi 감시 결과를 먼저 보고, A/B/A/B 40분 비교를 한다. 정상이면 00/8B/B0으로 24시간 시험을 한다. 안정성이 확인되면 기본 setfile을 바꾸고 main에 병합한다.

**Wi-Fi(07:24 KST 중간 확인).**
- [확인] 새 펌웨어로 모두 약 6.6시간 돌았고 패닉은 0번이다(전원을 다시 넣은 뒤로 1시간 10분, 복구 횟수 0).
- 판정은 11-14 00:47 KST에 한다(11.47). 이때 연결은 2.4 GHz(2437 MHz, −57 dBm)였다. 전에는 5745 MHz였다.

**Wi-Fi 24시간 판정(2026-11-14 00:48 KST).**
- [확인] Mac 감시(5분마다 `mx_recovery_count`)는 00:47에 `recovery_count still 0`으로 끝났다. 00:48에 직접 읽은 값도 0이었다(가동 18시간 34분, 2437 MHz, −57 dBm).
- [확인] 지금 부팅의 커널 journal: `WLBT FW PANIC`, `r4_panic_record`, `Data Abort`가 없다. 걸린 454줄은 모두 감시가 읽을 때 남긴 `mx_procfs_mx_recovery_count_read: recovery_count: 0`이다.
- [확인, 근거 약함] 그 전 부팅(01:31~06:13 KST)의 커널 journal은 부팅 12초(01:32:03)까지만 있다. 전원 차단으로 잃은 것으로 보인다(같은 부팅의 다른 journal은 05:59까지 있다). 그래서 이 4.7시간은 감시가 0을 읽었다는 것만 근거다. 그 전 44분은 23에서 그대로였다.
- 판정: 새 펌웨어로 약 24.0시간(5.4 + 18.6) 동안 저절로 난 패닉이 0번이다. 11.47에서 정한 기준을 넘었다. 옛 빈도(3.8시간에 1번)라면 기대값 약 6.3번, 0번일 확률 e^−6.3 ≈ 0.2%다. 로그로 확인되는 지금 부팅 18.6시간만 쳐도 약 0.8%다.
- 남은 변수: 지금 부팅은 확인할 때마다(07:24, 20:34, 00:48) 2.4 GHz였고, 앞의 5.4시간은 5745 MHz였다. 옛 펌웨어 때는 두 대역을 오갔다(11.30). 대역이 패닉에 영향을 주는지는 따로 가르지 않았다. 강제 패닉 복구는 새 펌웨어에서도 된다(11.47, 8초).
- 보드에 바꾼 것: 없다. 감시만 했다.

**A/B/A/B(07:25~08:09 KST).**
- 실행: `tools/board/pll-abab.sh`(보드 `/var/tmp/pll-abab.sh`, root `systemd-run --unit=pllabab`).
- 조건: A = 0x75(`experimental_00_75_00`), B = 00/8B/B0. 수신 1600(DT), 각 10분, 앞뒤 raw 표본 20버퍼, 128줄 디코딩.
- [확인] 4회 모두 `pass: true`였다. 결과 표와 세부는 DVS-PLL-TESTS.md 0장과 8장에 있다.
- [관찰] 버퍼 속도는 A가 35.2/s, B가 37.8/s로 매번 같았다. 센서 scan 주기는 둘 다 985 µs였다. 가설은 DVS-PLL-TESTS.md 0장에 있다.

**24시간 시험(2026-11-13 08:10 ~ 11-14 08:10 KST, 통과).**
- root `systemd-run --unit=plltest python3 -I /var/tmp/plltest.py recommended_00_8b_b0 0 1440 --sample 20 --out /var/tmp/plltest/11-8bb0-24h`
- 시작할 때와 끝날 때 readback은 `00 8b b0 word 0x08bb0 P=4 M=374 S=0 rate=2244`였다.
- [확인] `summary.json`: **`pass: true`, 9개 기준 모두 통과.** 86,400초, 표본 8,608개(10초 간격).
  - CRC 0, 재시작 0(캡처 프로세스 pid 2842 그대로).
  - 버퍼 3,272,178개(37.87/s), 짧은 버퍼 0, 데이터 줄 418,838,784 = 버퍼 × 128.
  - frame_end 87,762,320개 = 타임스탬프 87,762,320개. frame_end 누락 0, scan 누락 0, 모르는 워드 0, 비트 고침 0.
  - 타임스탬프 오류·역행·고침·재시작 0. 22비트 ms 기준값은 이 동안 약 20바퀴 돌았다. wrap 처리(11.47)가 그대로 맞았다.
  - 앞뒤 raw 표본 20버퍼씩: 짧은 버퍼, 모르는 워드, 타임스탬프 누락·오류, frame_end 누락 모두 0.
- [확인] 같은 시간의 커널 로그(root `journalctl -k`, 보드 시계는 UTC).
  - `start/end overlapped` 255번: 몇 분마다 나는 경고다(DVS-PLL-TESTS 0장). 위 계수는 하나도 늘지 않았다.
  - DMA off, `There's no frame` 41줄은 모두 08:10:19~43, 시험이 스트림을 다시 켠 순간이다. 측정을 시작하기 전(20초 대기)이다.
  - 그 밖에는 `perf: interrupt took too long` 1줄뿐이다.
- [확인] 같은 시간 Wi-Fi 패닉 0번(`mx_recovery_count` 0, 위 판정).
- 끝나자 `plltest.py`가 setfile(그때는 0x75)로 되돌렸다(`00 75 00`).
- 판정: 사용자가 정한 조건(A/B/A/B 정상 → 24시간 안정)을 채웠다. 기본 setfile을 바꾼다(11.49).

**보드에 바꾼 것.**
- `/var/tmp/pll-abab.sh` 추가, `/var/tmp/plltest/10-abab-*`와 `11-8bb0-24h` 결과 디렉터리.
- 시험하는 동안에는 live가 00/8B/B0으로 돌았다. 끝나자 setfile(0x75)로 돌아갔다. 11.49에서 setfile 자체를 00/8B/B0으로 바꿨다.

### 11.49 기본 setfile을 00/8B/B0으로 (2026-11-14)

사용자 결정(2026-11-13): A/B/A/B가 정상이고 24시간 시험으로 안정성이 확인되면 기본 setfile을 바꾸고 `dvs-pll-diag`를 main에 병합한다. 조건은 11.48에서 채웠다(A/B/A/B 4회 통과, 24시간 `pass: true`).

**바꾼 것(소스).**
- `is-cis-s5krc1s-setA.h`: 0x30A5~A7을 00/75/00에서 **00/8B/B0**으로 바꿨다(P=4 M=374 S=0, 공식으로 2244 Mbps/lane, VCO 범위 1250~2500 안). 머리 주석에 근거(11.48)를 적었다.
- `is-cis-s5krc1s.c`: `pll` 파라미터 설명과 주석만 고쳤다. 프로파일 표는 그대로다. 0x75는 `pll=experimental_00_75_00`으로 계속 시험할 수 있다.
- `tools/board/plltest.py`: `setfile`의 기대 readback을 `00 8b b0`으로 바꿨다. 자기 시험의 readback 실패 예는 `experimental_00_75_00`으로 바꿨다(통과).
- `modules/s5krc1s-pablo/README.md`: vendor setfile과 다른 점을 고쳐 적었다.
- 실제 레인 속도는 여전히 재지 않았다. 공식값은 협력자가 알려 준 비트 배치에 따른 계산이다.

**빌드와 설치(08:16~08:20 KST).**
- [확인] 바꾸기 전, 마지막 빌드(e850-out, 11.46)를 strip한 것은 보드 모듈과 md5가 같았다(0fe3ba23). 그래서 빌드가 보드 판을 그대로 재현한다.
- 새 빌드(strip): md5 f96703b7, sha256 28c2025b…, 38,984바이트(전과 같음). 보드 판과 비교하면 setfile 표의 두 값(0x30A6 0x75→0x8B, 0x30A7 0x00→0xB0)과 modinfo 문자열, srcversion만 다르다. 코드는 같다.
- 보드: 이전 모듈을 `/root/is-cis-s5krc1s.ko.prev-11.49`(md5 0fe3ba23)로 백업했다. 새 모듈을 넣고 `systemctl stop dvslive` → `rmmod is_cis_s5krc1s` → `systemctl start dvslive`(dvslive.sh가 modprobe)로 바꿨다. 재부팅하지 않았다.
- [확인] 다시 올린 뒤 `pll` 파라미터는 비어 있고(setfile), readback은 `00 8b b0 word 0x08bb0 P=4 M=374 S=0 rate=2244`다.
- `/var/tmp/plltest.py`도 새 판(md5 6787f7a4)으로 바꿨다. 이전 판은 `/var/tmp/plltest.py.prev-11.49`다.

**확인 시험(08:20~08:32 KST).**
- root `systemd-run --unit=plltest python3 -I /var/tmp/plltest.py setfile 0 10 --sample 20 --out /var/tmp/plltest/12-setfile-8bb0-default`
- [확인] **`pass: true`, 9개 기준 모두 통과.** 600초, 표본 61개, CRC 0, 재시작 0, 버퍼 22,666개(37.78/s, 128줄), 짧은 버퍼·모르는 워드·frame_end 누락·scan 누락·타임스탬프 오류 0. 앞뒤 raw 표본 20버퍼씩 깨끗했다. 61개 표본의 readback이 모두 `00 8b b0`이다.
- 끝난 뒤에도 live는 setfile(`00 8b b0`)로 돈다.

**되돌리기.** dvslive를 멈추고 `/root/is-cis-s5krc1s.ko.prev-11.49`를 `/lib/modules/$(uname -r)/is-cis-s5krc1s.ko`로 되돌린 뒤 rmmod/modprobe(또는 dvslive 시작)한다. 모듈을 바꾸지 않고 잠깐 0x75로 돌리려면 `echo experimental_00_75_00 > /sys/module/is_cis_s5krc1s/parameters/pll` 뒤 dvslive를 다시 시작한다.

**보드에 바꾼 것.**
- `/lib/modules/.../is-cis-s5krc1s.ko`: 11.49판(md5 f96703b7, setfile 00/8B/B0). 이전 판은 `/root/is-cis-s5krc1s.ko.prev-11.49`다.
- `/var/tmp/plltest.py`: 6787f7a4. 이전 판은 `.prev-11.49`다. 결과 디렉터리 `/var/tmp/plltest/12-setfile-8bb0-default`가 늘었다.

### 11.50 웹 페이지: 크롭, 레지스터 읽기, 통계 그래프, 접속 주소 (2026-11-14)

사용자 요청: 크롭 기능, 레지스터 읽기, 화면 아래 수치의 변화를 그래프로, 그 밖에 필요한 것. 이어서 DVS live 화면 아래에 접속 주소(쓰고 있는 IP와 USB IP)를 한글로 표시.

**바꾼 것(`tools/linux510/dvsctl.py`, `dvsctl.html`).**
- **크롭(웹 미리보기만):** 미리보기를 끌어 영역을 고르면 그 부분을 확대해 보여 준다(픽셀은 뭉개지 않음). "전체 보기"로 되돌리고, "이미지 저장"으로 지금 보이는 그림을 PNG로 받는다. 그 영역의 켜진 픽셀 수와 비율도 보여 주며, 색이 ON 초록/OFF 빨강이면 ON과 OFF를 나눠 센다. HDMI와 `/frame.png`는 그대로 전체 화면이다. 센서에도 크롭 레지스터(0x3300~0x330C, 지금 `CROP_BYPASS`=1)가 있지만 쓰지 않았다.
- **센서 레지스터 읽기:** `GET /reg?a=30A5&n=3`. 레지스터를 1~64개, 읽기만 한다(쓰기는 없다). `I2C_RDWR` 한 번에 주소를 쓰고 n바이트를 읽는다. 센서 I2C 장치는 드라이버가 `S5KRC1S`인 `*-0010`에서 찾는다(지금 i2c-7).
  - 페이지에는 빠른 단추 7개가 있다: D-PHY PLL, PLL, MIPI, scan margin, SEL_MODE, 센서 크롭, 타임스탬프.
  - 결과는 주소, 이름(setA.h 주석), 16진, 10진, 2진 표로 보여 준다. 0x30A5~A7은 P/M/S와 공식 속도(계산값)로, 0x321D~E는 예상 scan 주기로, 0x3915는 레인 수로 풀어 준다.
- **통계 그래프:** 캡처 프로세스가 2초마다 `/stats`와 CRC·링크 계수기를 1시간(1800줄) 동안 기억한다(`GET /history?since=T`). 페이지는 그래프 16개를 그린다.
  - 그리는 값: fps, 그리기, 프레임당 이벤트, 버퍼/s, 복사, 버퍼 크기, 지연, 데이터 있는 시간, 어두운 프레임, scan 주기. 그리고 초당 CRC, 짧은 버퍼, scan 누락, frame_end 누락, 타임스탬프 오류, 모르는 워드.
  - 기간은 2분, 10분, 30분, 1시간 중에서 고르고, CSV로 받을 수 있다. 그래프에 마우스를 올리면 모든 그래프가 같은 시각의 값을 보여 준다. 오류율이 0보다 크면 ⚠와 빨간 숫자로 표시한다. 선 색은 dataviz 검사를 통과했다(#3987e5, 어두운 면 대비 3:1 이상).
  - dvslive가 다시 시작하면 보드의 기록은 처음부터 다시 쌓인다. 페이지를 열어 둔 동안 받은 기록은 페이지에 남는다.
- **통계 줄:** PLL readback(`00 8b b0`, 공식 속도)과 스트림 시작 뒤의 링크 계수기를 덧붙였다.
- **접속 주소:**
  - HDMI 화면 왼쪽 아래에 "웹 접속 주소 / 무선 Wi-Fi / IP:8080 / 유선 USB / IP:8080"을 한글로 그리고, 5초마다 다시 그린다. 확대를 바꾸면 화면이 지워지고 Wi-Fi 주소가 바뀔 수 있기 때문이다.
  - 자리는 x 4~236, 맨 아래에서 8 px 위다. 1.5배 화면의 왼쪽 여백(240 px) 안이고, 1배 화면에서는 영상 아래다. 어느 쪽이든 영상을 가리지 않는다.
  - 웹 페이지 미리보기 아래에도 같은 주소를 링크로 보여 준다(`/stats`의 `addrs`). IP는 `SIOCGIFADDR`(wlan0, usb0)로 읽고, 주소가 없으면 "연결 안 됨"이라고 쓴다.
- **글자:** 보드에는 한글 글꼴도 PIL도 없다. 그래서 Mac의 `tools/host/hangul-atlas.py`로 배너에 쓰는 107자(출력 가능한 ASCII + 웹접속주소무선유연결안됨, Apple SD Gothic Neo 22 px, 높이 27 px)를 비트맵으로 만들어 보드 `/usr/local/share/dvsview/dvsfont.json`에 넣었다. 시스템 글꼴의 글자 모양이라 저장소에는 넣지 않는다(`out/`). 이 파일이 없으면 배너만 빠진다.
- 이 fb는 `read()`/`write()`가 0바이트를 돌려준다. 그래서 dvsview처럼 mmap으로 쓴다.

**확인.**
- [확인] `test_dvsctl.py` 7개 통과(레지스터 인자, 기록, 배너 글자 자르기·밝기 추가). 페이지 스크립트는 `node --check`를 통과했다.
- [확인] 보드: `/reg?a=30A5&n=3` → `[0, 139, 176]`(00 8b b0). `a=3300&n=13`은 setfile 값과 같았다(01 00 FF 59 FF 00 00 03 BF 00 01 00 00). 범위 밖(`a=FFFF&n=2`)은 400이었다. 스트림이 도는 중에 읽어도 CRC와 짧은 버퍼는 0 그대로였다(3바이트를 하나씩 읽는 데 1.1 ms, 한 번에 읽어도 같은 값).
- [확인] `/history`는 2초마다 한 줄이 쌓였다. headless Chrome으로 페이지를 찍어 보니 그래프 16개, 크롭 줄, 레지스터 단추, 주소 줄이 그려졌다.
- [확인] HDMI: fb0를 mmap으로 덤프해 보니 왼쪽 아래에 한글 배너가 있었다(1.5배, 영상 밖). 모니터 화면으로는 확인하지 못했다(HDMI 출력 문제, 11.47).
- [미확인] 실제 마우스로 크롭 영역을 끌어 보는 것과 레지스터 표가 그려지는 것은 브라우저에서 해 보지 못했다. Chrome 확장이 연결돼 있지 않았다.

**보드에 바꾼 것.**
- `/usr/local/share/dvsview/dvsctl.py`(md5 18d51af9), `dvsctl.html`(14a7f6f7), `dvsfont.json`(0e7f055a, 추가). 11.51에서 다시 바꿨다. 이전 판은 `/root/dvsctl.py.prev-11.50`과 `/usr/local/share/dvsview/dvsctl.html.prev-11.50`(11.46판)이다.
- dvslive를 세 번 다시 시작했다(각 약 5초 끊김).

### 11.51 그래프: MIPI 수준, DRAM 버퍼 상태, 변화 없는 값은 개수 타일로 (2026-11-14)

사용자 요청: MIPI 수준의 데이터량, fps, DRAM 버퍼 상태를 그래프에 넣는다. 3·4번째 줄 그래프(어두운 프레임, scan 주기, 오류 6가지)는 변화가 없다.

**MIPI 수준: `modules/csis-irqstat`.**
- 그동안은 rmmod할 때 dmesg로 합계만 냈다. 이제 합계를 읽기 전용 파라미터로도 내보낸다(`/sys/module/csis_irqstat/parameters/{calls,fs,fe,line,err,err_bits}`).
- `err_bits`는 `err_id[0..3]`의 비트별 개수다.
- dvslive.sh가 `modprobe csis-irqstat`를 한다. 보드 `/lib/modules/.../csis-irqstat.ko`, md5 2913a827.
- [확인] 10초 동안 FS 386, FE 386, line 386, 오류 플래그 386, 비트로 풀린 오류 0이었다. `/proc/interrupts`의 CSI IRQ는 약 116/s로, 프레임마다 3번이다.
- [확인] 오류 플래그는 프레임마다 선다. `err_id`에는 비트가 없다. vendor `csi_hw_g_value_of_err()`는 `INT_SRC0`만 `err_id`로 풀고 `INT_SRC1`은 플래그만 세운다. 그래서 프레임마다 나는 것은 `INT_SRC1` 쪽(VC별 VRESOL 상태, 11.22)으로 본다. 그래프의 "CSIS 링크 오류"는 `err_bits`의 합(CRC, ECC, overflow, FS/FE 손실 등)만 센다.
- 11.22 때 bit 16(VRESOL)이 `err_id[0]`에 세어진 것과는 다르다. 그 뒤 VRESOL 상태를 되써 지우는 패치가 들어간 것과 관계있을 수 있다(확인 안 함).

**DRAM 버퍼 상태: `ov5647-cap.py`.**
- DQBUF 때마다 나머지 VC0 버퍼 7개를 `VIDIOC_QUERYBUF`로 조회한다(vendor는 그대로 `vb2_querybuf`).
- QUEUED는 드라이버가 가진 버퍼로, DMA가 쓸 수 있는 빈 버퍼다. DONE은 채워졌지만 아직 꺼내지 않은 버퍼다. 2초마다 빈 버퍼의 최소와 대기 버퍼의 최대를 `dvsctl.buf_note()`로 넘긴다.
- [확인] 비용은 DQBUF마다 345 µs다(1000번 평균, 로그 `QUERYBUF x 7`). 초당 약 13 ms이고 CPU1의 1.3%다.
- [확인] 스트림 시작 직후 한 표본만 빈 0, 대기 7이었다(모든 버퍼가 차 있었다). 그 뒤 약 4분 동안은 늘 빈 7, 대기 0이었다. 앱이 버퍼를 늦지 않게 가져간다.

**그래프(`dvsctl.html`).**
- 그래프 12개를 세 묶음으로 나눴다.
  - MIPI(CSIS 수신): MIPI 프레임/s, MIPI 데이터량 MB/s, 이벤트 데이터 MB/s, 프레임 채움 %. 앞의 둘은 10초 평균이다. 2초 단위로는 프레임 수가 정수라 38.5와 39 사이를 오갔다.
  - DRAM(VC0 버퍼): 도착 버퍼/s, 빈 버퍼(최소), 처리 대기 버퍼(최대), 버퍼 복사 ms.
  - 화면: fps, 그리기, 프레임당 이벤트, 최신 이벤트보다 늦음.
- MIPI 데이터량은 FE/s × 프레임 크기(8192 B × 128줄 = 1 MiB, 설정값)로 계산한다. 회선에서 잰 바이트가 아니다. 이벤트 데이터는 버퍼의 실제 패킷 바이트(kB/버퍼 × 버퍼/s)다.
- 변화가 없던 값은 위쪽 개수 타일 11개로 옮겼다. 기간 동안 몇 개였는지를 보여 준다.
  - 타일: 버퍼 안 된 MIPI 프레임, CSIS 링크 오류, CRC, 짧은 버퍼, scan 누락, frame_end 누락, 타임스탬프 오류, 모르는 워드, 어두운 프레임, 센서 scan, 데이터 있는 시간.
  - 오류 개수가 0보다 크면 ⚠와 빨간 숫자로 표시한다.
- `/history`에 `bufs`, `mipi_fs/fe/err`, `vc0_n/free/wait`, `frame_bytes`를 더했다. `test_dvsctl.py`는 8개가 통과했다.

**관찰.**
- [관찰] MIPI FE와 디코더에 들어간 버퍼의 차이가 약 30초에 1개씩 늘었다(약 110초 동안 8308 → 8312). 그동안 빈 버퍼는 늘 7이었고, scan 누락(타임스탬프 틈)도 늘지 않았다.
- [가설] 데이터를 잃은 것이 아니라 줄이 없는 MIPI 프레임(FS/FE만 있는 프레임)이 버퍼가 되지 않은 것으로 본다. 그래서 이 타일은 경고로 표시하지 않는다. 확인하지 않았다.
- [관찰] 프레임 채움은 약 30%, 이벤트 데이터는 약 12 MB/s다. MIPI로는 약 40 MB/s(약 320 Mbit/s)를 보낸다. 나머지는 줄마다 붙는 0 채움이다(11.28).

**보드에 바꾼 것.**
- `/usr/local/bin/ov5647-cap.py`(md5 5c0bb717): 이전 판 `/root/ov5647-cap.py.prev-11.51`.
- `/usr/local/bin/dvslive.sh`(5e20af2c): 이전 판 `/root/dvslive.sh.prev-11.51`.
- `/usr/local/share/dvsview/dvsctl.py`(149ffe21), `dvsctl.html`(0133cdc9).
- `/lib/modules/.../csis-irqstat.ko`(2913a827): 새로 넣었고, depmod했고, 지금 적재돼 있다.
- dvslive를 한 번 다시 시작했다.

### 11.52 USB 네트워크(ECM)의 보드→Mac 방향 멈춤, 배너의 fb0 로그 (2026-11-14)

**관찰.** 10:15 KST쯤(보드 시계로 01:15 UTC) Mac에서 `http://192.168.7.1:8080/`가 시간 초과됐다. Wi-Fi 주소(192.168.0.236:8080)는 그때도 정상이었다.
- 보드 `usb0`는 UP이고 주소와 carrier도 있었다. Mac에 DHCP 임대(192.168.7.19)를 준 상태였고, 8080은 0.0.0.0에서 듣고 있었다.
- Mac→보드 방향은 살아 있었다. 보드 `rx_packets`가 Mac의 송신 수와 같이 늘었다.
- 보드→Mac 방향은 멈춰 있었다. 보드가 ping을 보내도 `tx_packets`가 93162에서 늘지 않았다. Mac `en12`의 수신 수는 93182였다. 즉 마지막 20개는 Mac에 도착했지만 보드 쪽에서는 전송 완료가 되지 않았다.
- 같은 USB 장치의 ACM 콘솔(보드→Mac IN도 포함)은 정상이었다. dwc3 `link_state`는 `On`이었다.
- `dmesg`에는 USB 관련 메시지가 남아 있지 않았다. 아래 `decon_release` 로그가 버퍼를 채우고 있었다. UART 로그(`uart.log`)는 11-13 07:35 이후 기록이 없어서 볼 수 없었다.

**시도 1: `ip link set usb0 down; up`(ACM에서).** 실패했다.
- `tx_packets`는 93182가 됐다. u_ether가 엔드포인트를 끄면서 걸려 있던 요청 20개를 정리했기 때문으로 보인다.
- Mac은 링크가 끊긴 것을 보고 169.254 주소로 바꿨다. 이후 DHCP 요청은 보드에 도착했지만(rx 증가) 보드의 응답은 나가지 않았다(tx 그대로).

**시도 2: `systemd-run`으로 UDC `soft_connect`를 `disconnect`, 3초 뒤 `connect`.** 성공했다.
- Mac이 장치를 다시 인식했다. `en12`는 192.168.7.19, ACM `/dev/cu.usbmodeme850_961`가 돌아왔다.
- `http://192.168.7.1:8080/`는 200, `/frame.png`는 25 ms였다.
- 보드는 재부팅하지 않았다. 카메라와 dvslive도 그대로 돌았다.

**다시 멈췄다.** 같은 날 14:53 KST에 rootfs 복제용 전송을 하려다 보니 USB가 또 멈춰 있었다(ping `Host is down`). 재연결(10:25 KST쯤) 뒤 몇 시간 안에 멈췄고, 정확한 시각은 모른다. 보드가 재연결 뒤 완료로 센 송신은 62212개(155449−93237), Mac `en12` 수신은 62267개였다. 이번에도 Mac이 보드보다 많이 받았다. 즉 보드가 마지막 몇십 개의 전송 완료를 받지 못한 채 멈췄다. 이번에는 재연결하지 않고 Wi-Fi ssh 터널로 전송했다(11.53).

**원인은 모른다.** ECM bulk IN 엔드포인트의 완료가 보드에 오지 않는 것(dwc3 쪽)인지, Mac 드라이버가 IN을 더 읽지 않는 것인지 이번 자료로는 가를 수 없다. 언제 멈췄는지도 모른다.

**다음에 또 멈추면.**
1. ACM 셸이나 ssh에서 root로 `systemd-run --unit=usb-reconnect sh -c 'sleep 1; echo disconnect > /sys/class/udc/13600000.dwc3/soft_connect; sleep 3; echo connect > /sys/class/udc/13600000.dwc3/soft_connect'`를 실행한다. ACM도 몇 초 끊긴다.
2. 그 전에 보드 `usb0`의 `tx_packets`와 Mac `netstat -I en12`의 수신 수를 적어 둔다.

**보드에 바꾼 것: 배너의 fb0 로그.** 11.50의 HDMI 주소 배너가 5초마다 `/dev/fb0`를 열고 닫았다. 닫을 때마다 decon 드라이버가 `decon_release + : 0`을 커널 로그에 찍었다. loglevel=8이라 UART로도 나갔다.
- `banner_loop`가 fb0를 한 번만 열고 계속 쓰도록 바꿨다. `fb_put(f, rows, x0, bottom)`가 열린 파일을 받는다.
- `/usr/local/share/dvsview/dvsctl.py`(md5 40cc3f46). 이전 판은 `/root/dvsctl.py.prev-11.50`이다.
- dvslive를 다시 시작했다. 20초 뒤 `dmesg` 마지막 40줄에 `decon_release`는 0개였다. 배너 영역(y 950~1071)의 81줄에 픽셀이 있었다. 두 주소 모두 200을 돌려줬다.

### 11.53 두 번째 보드로 복제 (2026-11-14)

따라 하는 절차는 [BOARD-CLONE.md](BOARD-CLONE.md)로 따로 정리했다. 이 절은 그날의 기록이다.

**목표.** 지금 보드(이하 A, 192.168.0.236)와 같은 시스템을 두 번째 E850-96(이하 B, 출하 Android 10, fastboot 일련번호 `000009f72c9c23e0`)에 올린다. B에도 S5KRC1S 센서가 CN4에 따로 붙어 있다.

**옮긴 것.**
- boot 파티션: A의 boot 파티션 앞 64,933,888바이트의 md5가 `boot-dvs3/boot.img`와 같다(ad99e885). 이 파일을 그대로 구웠다. 다른 파티션(efs, persist, dtbo, bootloader 등)은 건드리지 않았다.
- userdata: A의 루트 파일시스템을 tar로 받아 같은 크기(4 KiB × 786,432), 라벨, UUID의 ext4로 다시 만들었다.
  - 받기: `tools/board/rootfs-send.py`(A, root, 127.0.0.1:9100)와 `tools/host/rootfs-recv.py`(Mac, Wi-Fi ssh 터널, 3.7 MB/s). USB 네트워크는 또 멈춰 있어서 쓰지 않았다(11.52).
  - 결과: 2,589,050,880바이트, 양쪽 md5 `2c041854…` 일치. tar 경고는 `./sys`와 `system.journal` "file changed as we read it" 두 개다.
  - 만들기: `tools/linux510/build-clone.sh`(Docker, loop 마운트에 풀기). tar는 ACL을 이름으로 기록하므로 A의 `/etc/passwd`, `/etc/group`을 먼저 컨테이너에 넣었다. 넣지 않았을 때는 user journal 파일의 ACL에서 `Cannot acl_from_text` 경고가 났다.
  - 바꾼 것: machine-id는 새로 만들었고(`6f31c0bd…`, 공유기에서 따로 DHCP를 받게), `/var/lib/systemd/random-seed`는 지웠다.
  - 그대로 둔 것: ssh 호스트 키, 사용자와 비밀번호, Wi-Fi 설정, 호스트 이름 `e850-96`, `/var/tmp`(녹화 1.1 GB 포함).
- Wi-Fi MAC은 손댈 필요가 없다. 드라이버가 `00:00:0F` 뒤에 SoC `unique_id`를 붙인다(`drivers/net/wireless/scsc/mgt.c:540`). B는 `00:00:0f:09:f7:2c`다.

**fastboot에 넣기.**
- Mac의 Homebrew adb 37.0.1은 macOS 27에서 서버가 시작하다 죽는다(샌드박스 밖, `ADB_LIBUSB=0/1` 모두).
- VOL_UP을 누른 채 전원을 넣어도 LK는 10번 모두 `Volume up key: 1`로 읽고 Android로 부팅했다(7.12의 A와 같다).
- 된 방법: Android 10은 `ro.debuggable=1`이라 UART(ttySAC0)에 `console` 셸(uid shell)이 뜬다. UART로 `reboot bootloader`를 보내자 `Entering fastboot: reboot bootloader command`가 나왔다.
- Linux로 바뀐 뒤에는 5.10의 `rebootarg bootloader`가 통하지 않았다(`retboot reason: 0xcafe`로 그냥 부팅). `to-fastboot`(워치독 데몬 kill -9)로는 34초 만에 `Abnormal RST_STAT`로 들어갔다.

**굽기.**
- `fastboot flash boot`는 됐다(1.4 s + 0.9 s).
- `fastboot flash userdata`(2.5 GB sparse)는 `getvar:is-userspace`를 먼저 보냈다. LK가 거기서 응답을 멈췄다(7.2와 같은 종류). 전원을 다시 넣어야 했다.
- 바꾼 방법: `resparse.py`에 `--split-mib`를 넣었다. 조각마다 파티션 전체를 덮는 sparse(앞뒤는 DONT_CARE)로 700 MB 이하 4개를 만들고, `fbmini flash userdata`로 차례로 구웠다.
  - Mac에서 조각을 순서대로 덮어쓴 결과와 `simg2img`로 푼 원본의 sha256이 같았다(`06ebdf6b…`).
  - LK는 DONT_CARE를 `CHUNK TYPE NONE`으로 찍고 건너뛰었다. 각 조각의 첫 RAW 위치는 계산과 같았다(10,858,512 / 12,382,224 / 13,914,064섹터, userdata 시작은 8,957,968).
  - 첫 조각(18,465청크)은 5분 56초가 걸렸다. LK가 청크마다 UART에 한 줄을 찍기 때문으로 보인다.

**결과(B).**
- 부팅: `systemctl is-system-running` = running, 실패한 유닛 없음.
- 네트워크: 같은 SSID에 붙었고 IP는 192.168.0.168이다. ssh는 `-o HostKeyAlias=192.168.0.236`으로 된다(같은 호스트 키).
- 파일 비교: md5 목록 13,191개 중 13,188개가 이미지와 같았다. 다른 3개는 `root/.cache/…/onnxruntime.db`(-shm, -wal)로, 실행 중에 쓰이는 파일이다.
- 서비스와 모듈: dvslive, nginx, ssh, e850-hdmi, e850-wifi, csis_irqstat, e850_cpuhp가 모두 떴다. `ping`의 `cap_net_raw`도 남아 있다.
- 웹: `http://192.168.0.168:8080/`이 200을 돌려줬고, 주소 줄도 새 IP를 보였다.
- I2C: `/reg`로 0x30A5~A7을 읽으면 A와 B 모두 `00 8B B0`(i2c-7)이다.

**문제(B만): MIPI CRC가 모든 줄에 난다.**
- 같은 PLL 00/8B/B0에서 A는 CRC 0이다. B는 약 5.5분 동안 CRC 1,624,669개였다(csis-irqstat err_bits[1], 줄 수와 같다). 이 밖에 err_bits[2] 3, [7] 10, [17] 7이 있었다.
- "데이터 있는 시간"은 0~3%였다.
- dvslive는 `stuck capture, 15 short buffers in 2 s: restarting the stream`으로 수십 초마다 다시 시작했다. 첫 부팅에서는 약 10번 재시작했다.
- 그 상태에서 PLL 비교(`plltest.py` 4개 설정 × 2분)를 시작했다. 첫 설정의 스트림을 멈출 때 vendor 정지 경로가 `[G:SS1][WRN] subdev[114] stop waiting...`을 28 ms마다 찍었고, 보드가 멈췄다. 부팅 872초에 워치독이 리셋했고, B는 `Abnormal RST_STAT`로 fastboot에 들어갔다. PLL 비교 결과는 얻지 못했다.
- 가설: B의 센서·케이블·보드 가운데 하나가 MIPI 고속 신호 여유가 달라서, A에서 맞은 PLL과 settle이 B에는 맞지 않는다. 어느 쪽인지는 아직 가르지 못했다. 센서를 맞바꾸거나 B의 CN4 케이블을 다시 꽂아 보면 가를 수 있다.

**해결: B의 CN4 카메라 케이블을 다시 꽂았다(16:2x KST).**
- 순서: B의 dvslive를 멈추고 sync한 뒤 `systemctl poweroff`로 껐다. 사용자가 케이블을 다시 꽂고 전원을 넣었다.
- 부팅 1분 뒤: CRC 0, err_bits 모두 0, 데이터 있는 시간 100%, scan 985 µs(A와 같다), short_bufs 0, unknown 0, ts_bad 4(시작 값), dvslive 재시작 0.
- 즉 모든 줄의 CRC는 케이블 접촉 문제였다. PLL은 A와 같은 00/8B/B0을 그대로 쓴다.
- 이번 부팅에서는 UDC가 `not attached`이다. e850-vbus는 `VBUS on … after 0 ms`이고 11.31의 경합 표시는 없다. 케이블을 만질 때 USB-C가 빠진 것으로 보인다(확인 전).
- Mac의 UART 로거(`uart-new.log`)는 B를 끌 때 어댑터가 잠깐 사라지면서 죽었다. 다시 켰다. B의 UART 어댑터는 보드 전원을 따라 사라지는 것 같다(추정).

- 10분 시험(16:25~16:35 KST): 버퍼 22,876개(38.1/s), CRC 0, short_bufs 0, unknown 0, fe_missed 0, ts_bad 4(그대로), err_bits 모두 0, dvslive 재시작 0. ts_missed 2130은 시작할 때 생긴 값으로, 10분 동안 늘지 않았다.
- B의 USB: 케이블을 다시 꽂은 뒤 UDC가 `configured`가 됐다. 그 전 10분 동안 `not attached`였던 것은 케이블 접촉 문제였다(e850-vbus는 정상, 경합 표시 없음).

**A에서 본 것.** A의 dvslive `NRestarts=3`이었다. 그중 한 번은 tar 전송 중인 06:04:25 UTC다. 원인은 root 로그를 못 봐서 모른다(A의 USB를 뽑았고 philip은 journal을 못 읽는다).

### 11.54 웹 화면 90° 회전 (2026-11-14)

카메라를 다른 방향으로 달 수 있으니, 웹 미리보기 아래에 **↺ 왼쪽 90° / ↻ 오른쪽 90°** 버튼을 넣었다(`dvsctl.html`).
- 돌리는 것은 웹 화면뿐이다. HDMI, `/frame.png`, 렌더러의 "뒤집기"(그대로, 좌우, 상하, 180°)는 그대로다. 90°를 HDMI에 적용하려면 렌더러를 고쳐야 하고 화면 비율도 바뀐다. 이번에는 하지 않았다.
- 받은 비트맵을 캔버스에서 돌린 뒤 크롭, 이미지 저장, 켜진 픽셀 세기를 한다. 좌표는 돌린 화면 기준이다. 90°와 270°에서는 미리보기가 720×960이 되고, 너비를 75%로 줄여 높이를 비슷하게 맞춘다.
- 저장 파일 이름에 `-rot90`처럼 각도가 붙는다.
- 회전 값은 브라우저 localStorage(`dvs-rot`, 보드 주소마다 따로)에 남는다. 주소에 `?rot=90|180|270`을 붙이면 그 값이 우선한다.
- 확인: headless Chrome으로 `?rot=0`과 `?rot=90`을 B의 데이터로 찍어 보았다. 90°는 720×960 세로이고, 0°는 예전과 같다. 실제 브라우저에서 버튼을 누르는 동작과 돌린 화면에서 크롭하는 동작은 확인하지 않았다.
- 보드: B에 설치했다(`/usr/local/share/dvsview/dvsctl.html` md5 6df1e198, 이전 판 `/root/dvsctl.html.prev-11.54`). 페이지는 요청마다 파일에서 읽으므로 dvslive는 다시 시작하지 않았다. A에는 `/var/tmp/dvsctl.html.new`까지만 올렸다(root가 필요한데 A의 USB가 빠져 있다).

### 11.55 회전·크롭을 실제 입력으로 확인, A 설치 상태 (2026-11-14)

- A 설치 상태: A의 `/usr/local/share/dvsview/dvsctl.html`은 이미 md5 6df1e198이었다(파일 시각 07:42 UTC = 16:42 KST). 인계 커밋(e3f4aec, 16:46 KST)의 "A 설치 남음"은 커밋할 때 이미 맞지 않았다. A와 B의 8080이 내보내는 페이지 md5도 모두 6df1e198이다.
- 시험: Chrome 확장이 연결되지 않아서 headless Chrome에 CDP `Input.dispatchMouseEvent`로 실제 마우스 입력을 넣었다(`tools/host/dvsctl-rotcrop.py`). 페이지는 보드 주소에서 바로 열었다(같은 출처라 `--disable-web-security`가 필요 없다). 새 프로필이라 localStorage는 비어 있다.
- 결과: A와 B 모두 17개 항목 통과.
  - 오른쪽 90° 버튼: `rot` 1, 캔버스 720×960, 너비 75%, localStorage `dvs-rot`=1.
  - 돌린 비트맵이 원본을 시계 방향으로 돌린 것과 모든 화소가 같다(90°, 180°, 270°, 같은 순간의 원본과 비교, 불일치 0).
  - 90° 화면에서 화면의 (25%, 25%)~(75%, 50%)를 끌면 영역 x 180, y 240, 360×240(기대값과 같음). 그 안에서 한 번 더 끌면 좁혀진다(x 324, y 336, 72×48, 기대값과 같음). 클릭(조각)은 크롭을 바꾸지 않는다.
  - 270° 화면에서 화면 밖(-10%)까지 끌면 0, 0으로 잘린다.
  - 전체 보기, 새로 고침 뒤 회전 유지, `?rot=0`이 저장된 180°보다 우선한다. 페이지 예외 0.
- 처음 돌렸을 때 실패 2개는 시험 쪽 문제였다.
  - 크롭 정보 줄(`#crop-info`)이 글자 수에 따라 20 px(1줄)과 39 px(2줄)을 오간다. 창 높이가 페이지 높이(2000~2044 px)와 비슷하면 세로 스크롤바가 생겼다 없어지며 미리보기가 좌우로 7.5 px 움직인다. 페이지는 이벤트마다 그때 위치로 좌표를 계산하므로 크롭은 맞다. 시험 스크립트가 위치를 먼저 읽어 두어서 6 px 어긋났다. 창 높이를 3000으로 해서 고쳤다.
  - 화면 경계 소수점 좌표에서 누른 것은 캔버스 밖으로 판정됐다. 안에서 밖으로 끄는 시험으로 바꿨다.
  - 페이지는 고치지 않았다. 스크롤바가 화면을 차지하는 브라우저(Windows 등)에서 창 높이가 페이지와 비슷하면 미리보기가 흔들릴 수 있다. macOS 기본(오버레이 스크롤바)에서는 생기지 않는다.
- 같이 본 것(원인 모름): "버퍼 안 된 MIPI 프레임" 타일이 A에서 -50이었다. A의 1시간 기록(2초 표본 1800개)에서 두 표본만 버퍼 +151, frame_end +75였다(29분 간격). 나머지 표본은 -1~+2이고 1시간 합은 +5다. B에서도 30분에 한 번 -75가 있었다.
- 확인하지 못한 것: 실제 모니터의 HDMI 주소 배너, A dvslive `NRestarts=3`의 원인(A root journal이 필요하다. philip은 journal을 읽을 수 없다).

### 11.56 DVS 두 대로 3D 영상: 1단계(Mac에서 계산) 구현, Mac 연산량 (2026-11-14)

사용자 목표는 SLAM이다. 그 첫 과제로 "두 센서로 3D 영상 만들기"를 정했다. 설계는 `docs/superpowers/specs/2026-11-14-dvs-stereo-3d-design.md`, 계획은 `docs/superpowers/plans/2026-11-14-dvs-stereo-3d.md`이다. 사용자가 고른 것: 계산은 먼저 Mac에서 하고 연산량을 본 뒤 경량화해서 보드로 옮긴다. 렌즈는 지금 그대로 쓰고 센서는 나중에 바꾼다. 3시간 외출하는 동안 Claude가 판단해서 진행하고 커밋했다(push는 하지 않음).

**배치 (사용자 설명, 화면 측정):**
- 두 센서가 미니 선풍기를 본다. 간격 15 cm. 보드1(광학렌즈)은 선풍기 바로 앞, 보드2(기본렌즈)는 왼쪽에서 30° 돌려 4 cm 뒤.
- 사용자: 선풍기 크기는 두 화면에서 비슷하고, 보드1은 안쪽만 보인다.
- 측정(`/frame.png` 5장 중간값): 이벤트 덩어리 보드1 90×114 px, 보드2 385×387 px. 켜진 화소 보드1 2.4%(흩어진 잡음이 많다), 보드2 10.7%.
- 가설(확인 안 함): 광학렌즈 쪽이 어두워서 빠르게 도는 바깥 날개를 놓친다.

**구현 (브랜치 `dvs-stereo-3d`):**
- `libdvsview`:
  - `dvs_surface(t_end, win_us, dst, info)`: display clock 창 `(t_end − win, t_end]`의 이벤트로 480×360 time surface를 만든다. 2×2 묶음이고, 값은 그 화소의 가장 늦은 이벤트로 `1 + 254 × (win − age) / win`, 0은 이벤트 없음이다. 극성은 쓰지 않는다. 시각 비교는 32비트 넘어감에 안전하다. 최신에서 RING/2보다 먼 창은 -2다. `dst`가 NULL이면 시계 값만 채운다.
  - `dvs_disp2mono()`: 최근 16버퍼에서 `CLOCK_MONOTONIC − display clock`의 최솟값이다. 계획은 64버퍼였는데 16으로 줄였다. display clock은 끊긴 구간을 scan 하나로 건너뛰므로 이 차이가 끊긴 시간만큼 커진다. 64버퍼(약 1.7초)면 오래된 최솟값이 그만큼 남는다.
- `dvsctl.py`: `GET /evframe?t_end=&win=`. 본문은 zlib이고, 헤더는 `X-Dvs-Newest`, `X-Dvs-Disp2Mono`, `X-Dvs-Mono`, `X-Dvs-Events`, `X-Dvs-Cost-Us`다. `t_end`가 없으면 헤더만 준다. 오류는 425(아직 안 옴), 410(링에서 밀려남), 400(인자).
- `tools/host/dvs3d.py`:
  - 보드 시계: NTP처럼 왕복 시간이 가장 짧은 표본을 쓴다. 30초 동안 모으고, 20 ms가 넘으면 "동기 불확실"이다.
  - 짝: 두 보드에 Mac 시각 T(지금 − 80 ms)의 창을 요청한다.
  - 계산: 정류, StereoSGBM(시차 64, 블록 5), 왼쪽에서 이벤트가 난 화소만 남기기, 3D 변환(최대 2만 점).
  - 하위 명령: `live`(페이지 http://localhost:8090/, `--rec`), `bench`, `calib`.
  - `--png`: `/evframe`이 없는 보드에서 HDMI 화면(`/frame.png`)을 대신 쓴다. 시각이 없어서 요청한 시각으로 짝을 짓는다.
- `tools/host/dvs3d.html`: 점구름을 canvas 2D에 직접 투영한다(드래그 회전, 휠 확대, 거리 색). 정류한 두 화면, 단계별 시간, 보드 상태를 함께 보인다. 외부 스크립트는 없다(three.js 대신, 인터넷 없이도 동작).
- 보정: `tools/host/dvs3d-checker.html`(`/checker`)은 **흰 칸만 100 ms마다 켰다 끈다.** 흑백을 뒤집으면 모든 칸에서 이벤트가 나서 극성 없는 surface가 꽉 찬 사각형이 된다. `dvs3d.py calib`은 150 ms 창으로 9×6 모서리를 찾고, `calibrateCamera` 두 번, `stereoCalibrate`, `stereoRectify` 순서로 `~/e850-logs/dvs3d/calib.npz`를 만든다. 판정은 rms 1 px 미만, 초점거리 비 2배 이하다.
- 바둑판 페이지를 태블릿용으로 고쳤다(2026-11-15): 깜빡임을 JavaScript 대신 CSS 애니메이션으로 바꿔, 메일 첨부 미리보기처럼 스크립트가 꺼진 곳에서도 깜빡인다. headless Chrome에서 스크립트를 끄고 켠 두 경우, 1180×820·820×1180·1280×800 세 크기 모두 켜짐/꺼짐 화면이 번갈아 나왔고, 켜진 화면에서 `findChessboardCornersSB`가 9×6 모서리 54개를 찾았다. 실제 태블릿과 DVS로는 아직 보지 않았다.
- 같은 날 사용자가 두 보드에 같은 렌즈를 달고 태블릿에 바둑판을 띄웠다. 보드마다 `/frame.png` 30장(약 5초)을 받아 `find_corners`를 돌렸더니 두 보드 모두 0/30이었다. 겹친 영상(`~/e850-logs/dvs3d/checker-20261115/acc-*.png`)에서 보드2는 바둑판 바깥 칸의 윤곽만 보이고 안쪽은 번졌고, 보드1은 칸이 전혀 구별되지 않았다. 보드1에서는 화면이 오른쪽 끝에 걸렸다. 가설: 초점(특히 보드1). 검은 칸까지 반응하는 것은 번짐 말고 태블릿 백라이트가 화면 내용에 따라 바뀌는 탓일 수도 있다(가리지 못함). `/evframe`은 이때도 두 보드 모두 404였다.
- 두 보드 센서 레지스터를 `/reg`(읽기 전용)로 비교했다. 아날로그 바이어스 0x0157=0x1F, 0x0166~68=0F/07/0F, 스캔 0x3216~1E, 0x320C=0x7D, 타임스탬프 0x32B1~B4, 동기 0x3A00~02·0x3283·0x32B6(모두 꺼짐), PLL 00/8B/B0, 0x3915=0x26이 **모두 같았다.** 다른 것은 화면 설정뿐이었다. 잡음 필터 `filter_ms`가 보드1 0, 보드2 4였고, 보드1을 4로 맞췄다(`POST /params`, 화면에만 쓰인다. `dvs_surface`는 필터를 보지 않는다). `flip`은 보드1 1(좌우), 보드2 2(상하)로 다르다. `/evframe`은 flip을 적용하지 않은 센서 좌표라서, `/evframe`으로 바꿀 때 Mac에서 보드마다 flip을 적용해야 한다(아직 안 함).

**시험:**
- C 자체 시험(`-DDVSVIEW_TEST`): surface 값, 아직 안 옴, 틀린 창, 32비트 넘어감, RING/2 밖, probe.
- `test_dvsctl.py` 12개, `test_dvs3d.py` 17개 통과. 합성 스테레오(f 600, 15 cm)에서 20 px 시차가 4.5 m ± 10%, 합성 보정 15자세에서 rms < 0.5, 간격 15.0 cm, 초점거리 비 1.00이다.
- debian:12 Docker에서 실제 Linux용 `libdvsview.so`를 ctypes로 불러 `dvsctl.evframe`을 확인했다. probe 200, 빈 링 200(172800 바이트), 425, 400.
- `--png`로 두 보드에서 실제로 받아 페이지를 headless Chrome으로 찍었다. 합성 보정이라 3D 모양은 의미가 없고 그리기 경로만 확인했다(`~/e850-logs/dvs3d/page-synthcalib-20261114.png`).

**보드 설치: 하지 못했다.** 사용자가 외출하면서 USB를 모두 뽑았다(ACM, UART, fastboot가 보이지 않음). 두 보드 모두 root가 없다(sudo는 사용자 비밀번호가 필요하다). 새 파일은 두 보드의 `/var/tmp`에 올려 두었다. `libdvsview.so` md5는 0397eb69, `dvsctl.py`는 f62b7d7c다. 두 보드의 지금 파일은 저장소 직전 판(dvsctl.py 40cc3f46, dvsview.c 0a0b1e5로 만든 c10b0382)이므로, 설치하면 `/evframe` 관련 변경만 들어간다. 설치 명령은 BRINGUP_STATUS 3장 0-1에 있다.

**연산량 (Mac M6, 측정):** `--png`로 2분 녹화(1155쌍, 9.6 Hz, 버린 쌍 0)를 `bench`로 돌렸다. 정류와 3D 변환은 합성 보정으로 쟀다. 원 자료는 `~/e850-logs/dvs3d/bench-mac-m6*.txt`, 녹화는 `~/e850-logs/dvs3d/rec-png-fan-20261114/`에 있다.

| 단계 | 1스레드 중간값 | p90 | 4스레드 중간값 |
|---|---|---|---|
| 정류 | 0.11 ms | 0.16 | 0.10 |
| SGBM (시차 64, 블록 5) | 14.82 ms | 17.71 | 14.42 |
| 마스크 | 0.04 ms | 0.07 | 0.03 |
| 3D 변환 | 0.89 ms | 1.11 | 0.87 |
| 합계 | 15.89 ms | 18.92 | 15.43 |

- SGBM이 93%다. 스레드를 늘려도 거의 줄지 않는다(이 모드의 SGBM은 사실상 1스레드).
- **최종 검토에서 고친 것:** 처음에는 SGBM 시차를 0~64 px로 고정했다. d = f·B/Z이므로, f를 600 px로 가정하면 간격 15 cm에서 약 1.4 m보다 가까운 것은 짝을 찾지 못한다. 선풍기는 그보다 가깝다. 그래서 시차 범위를 보정의 Q(f·B)와 `--zrange`(기본 0.3~3 m)로 정하게 고쳤다. 합성 보정에서는 시차 30부터 272개다. 같은 녹화에서 1스레드 합계 16.03 ms(SGBM 15.25 ms)로, 64개일 때와 거의 같았다(`bench-mac-m6-zrange.txt`). 또 `/evframe`이 없는 보드에 `live`를 돌리면 probe의 404를 세지 않아서, 쌍이 왜 버려지는지 보이지 않았다. 지금은 보드 오류로 센다.
- 같은 쌍 300개, 1스레드에서 설정을 바꿔 쟀다(`bench-mac-m6-variants.txt`). 시차 32는 10.8 ms, SGBM_3WAY는 10.7 ms, **240×180에 시차 32는 2.3 ms**, StereoBM(블록 15)은 2.8 ms다.
- 전송량(`/frame.png`, 960×720 PNG 10 Hz): 보드1 376 KB/s, 보드2 635 KB/s. `/evframe`(480×360 zlib)은 이보다 작을 것이다(측정 안 함).
- **A55 수치는 없다.** 보드에 OpenCV를 설치하려면 다운로드 허락이 필요하다. 게다가 보드2 rootfs가 198 MB(93%), 보드1이 306 MB만 남아 있다. 사용자 결정을 기다린다(BRINGUP_STATUS 4장).

**경량화 방안 (Mac 측정에 근거한 가설, A55 측정 전):**
1. 해상도를 240×180으로, 시차를 32로 줄인다. Mac에서 1/6이다. 깊이 분해능은 절반이 된다.
2. 이벤트가 난 화소만 맞추는 희소 매칭을 C(libdvsview, NEON)로 짠다. SGBM은 이벤트가 없는 화소(보드1 기준 97%)까지 계산한다. 보정 뒤 남는 점은 쌍마다 약 1,800개다(합성 보정 기준).
3. 2단계 판단 기준은 그대로다. A55 합계 100 ms 미만(10 Hz).

**확인하지 못한 것:** 보드에서 `/evframe`(설치 못 함)과 그 비용(`X-Dvs-Cost-Us`), 두 보드 시각 맞추기의 실제 정확도(손전등 시험), 실제 DVS 화면에서 바둑판 모서리 검출, 실제 보정과 3D 정확도(선풍기 원판), 보드1에서 바깥 날개가 안 보이는 원인.

**같이 본 것:** 보드2 `/stats`의 `act_lost`가 6,191,205,571이었다. 행동 분류 읽기(`dvs_events`)가 선풍기 장면에서 계속 뒤처져 이벤트를 건너뛴다는 뜻으로 본다(원인 미확인). 두 보드의 timesyncd(인터넷 NTP) 상태는 보드1 +0.25 ms(흔들림 1.9 ms), 보드2 −35.8 ms(흔들림 30.7 ms)였다. 그래서 짝 맞추기는 이 시계를 쓰지 않고 보드마다 왕복 시간으로 잰다.

### 11.57 세 번째·네 번째 보드로 복제, DVS 센서가 없을 때 웹만 띄우기 (2026-11-15)

**목표.** [BOARD-CLONE.md](BOARD-CLONE.md)대로 세 번째 E850-96(이하 보드3, fastboot 일련번호 `00000a749c9c23e0`)에 보드1과 같은 시스템을 올린다. 보드3에는 DVS 센서가 없다.

**문서와 다르게 한 것.**
- 원본(보드1) USB-C가 붙지 않았다. 11-14에 뺐던 케이블을 다시 꽂았지만 보드1 UDC는 `not attached`, Mac에는 장치가 없었다. 케이블을 다시 꽂아도 같았다. 그래서 3장(root로 원본 확인)과 4장(rootfs 전송)을 못 했다. 원인은 확인하지 않았다(가설: 이 보드는 VBUS를 감지하지 못하고 `e850-vbus`가 적재될 때 한 번만 알리므로, 뺐다 꽂은 뒤 USB가 끊긴 상태에 머문다). `rmmod e850_vbus; modprobe e850-vbus`(sudo)는 시험하지 않았다.
- 대신 11-14 15:07 KST에 받은 rootfs.tar로 `build-clone.sh`를 다시 돌렸다(새 machine-id `16314a53…`, 경고 0). 그 뒤 보드1에서 바뀐 파일(philip 권한으로 `find -newermt`)은 `dvsctl.html`(6df1e198), `/var/tmp`의 임시 파일, `/var/lib/dvsview/display.json`, `/var/lib/dvsact/act.jsonl`뿐이었다. `dvsctl.html`만 부팅 뒤 따로 넣었다.
- 보드3은 전원을 넣자 LK가 `Volume up key is pressed. Entering fastboot mode!`로 fastboot에 들어갔다. 보드2(11.53)와 달리 이 보드의 LK는 VOL_UP을 읽었다. 6장의 UART `reboot bootloader`는 쓰지 않았다.

**굽기와 확인.**
- boot: `fastboot flash boot` 2.6 s. userdata: fbmini 조각 4개 250 s, 31 s, 53 s, 32 s, 합계 약 6분(11-14는 약 12분, 차이의 원인은 모름). UART의 `CHUNK TYPE NONE` 위치로 조각이 이어진 것을 확인했다.
- 재부팅 13초 뒤 ACM이 보였다. machine-id 일치, `running`, 실패 유닛 없음, wlan0 192.168.0.234(DHCP).
- 파일 비교(8장): 13,193개 중 다른 것은 일부러 바꾼 웹 페이지 3개(`dvsctl.html`, 그 백업, `/var/tmp`의 새 판)뿐이었다.

**센서가 없을 때 일어난 일.** dvslive가 약 4분 동안 51번 재시작했다. 매번 `S_CTRL(IS_S_STREAM 1)`이 EINVAL로 실패했다(dmesg `i2c transfer fail(-6)`). 프로세스가 스트림을 끄지 않고 끝나서, 커널이 큐를 정리하며 `is_ssxvc0_stop_streaming ... fail(-22)` 뒤 `WARNING ... videobuf2-core.c:1956 __vb2_queue_cancel`을 냈다(8번). 11.53의 "재시작을 되풀이하다 정지 경로에서 멈춤"과 같은 경로라 dvslive를 멈췄다.

**바꾼 것(저장소).**
- `ov5647-cap.py`: `--live --raw8 --ctl`에서 센서 스트림 켜기가 실패하면 두 노드를 STREAMOFF한 뒤 끝나지 않고 웹 페이지만 띄운 채 기다린다. systemd 재시작 고리에 들어가지 않는다.
- `dvsctl.py`: `NO_SENSOR`가 있으면 `/stats`에 `no_sensor`(실패 이유)를 넣고, HDMI 주소 배너에 "DVS 센서 / 연결 안 됨"을 덧붙인다.
- `dvsctl.html`: `no_sensor`이면 화면 위에 빨간 안내("DVS 센서가 연결되어 있지 않습니다. 보드를 끄고 CN4에 센서를 꽂은 뒤 다시 켜 주세요.")를 띄우고 통계 줄에 실패 이유를 쓴다.
- `acmpush.py`, `acmpull.py`: 포트를 `/dev/cu.usbmodeme850_961`로 고정했다. `glob('/dev/cu.usbmodem*')[0]`이 함께 꽂힌 휴대폰의 ACM 포트를 먼저 골라, 웹 페이지 base64를 휴대폰 모뎀 포트로 한 번 보냈다(데이터 뒤에 CR이 없어 실행된 명령은 없을 것으로 보지만 확인하지 않았다).

**보드3에 실제로 바꾼 것.** 위 세 파일을 설치했다(이전 판은 `/root/prev-11.57/`). `dvsctl.py`는 git HEAD가 아니라 보드에 있던 판(40cc3f46)에 이번 변경만 더한 것이다(be6d299b). HEAD에는 11.56의 `/evframe`이 있어 새 `libdvsview.so`가 필요하기 때문이다. 결과: dvslive `active (running)`, `NRestarts=0`, 로그 `sensor stream on: [Errno 22] Invalid argument: no sensor on CN4? page only`, 커널 WARNING 수는 8 그대로(STREAMOFF로 새 경고 없음), `:8080/` 200, headless Chrome 화면에 빨간 안내가 보였다.

**같이 알게 된 것.** 보드는 `fs.protected_regular = 2`라서 `/var/tmp`에 philip이 만든 파일이 있으면 root도 `>`로 덮어쓰지 못한다. acmpush가 조용히 실패하고 옛 md5를 보였다. 먼저 `rm`한다.

**확인하지 못한 것.** 실제 모니터의 HDMI 배너 줄. 센서가 없을 때 행동 분류가 "사람 없음"으로 보이는 것(그대로 둠). 보드1·2에는 이번 변경을 아직 설치하지 않았다(root가 필요한데 ACM은 보드3에 붙어 있다).

**보드4 (같은 날 오후).** fastboot 일련번호 `00000bf40c9c23e0`, DVS 센서 없음, UART 없음(어댑터는 보드3에 그대로). 연결했을 때 이미 LK fastboot였다(어떻게 들어갔는지는 UART가 없어 모름).
- `build-clone.sh`에 overlay를 더했다. `/x/overlay/<경로>`의 파일을 machine-id를 바꾼 뒤 이미지에 덮어쓴다. 보드3에 넣은 세 파일(`ov5647-cap.py` 16a42b22, `dvsctl.py` be6d299b, `dvsctl.html` d32fb248)을 이렇게 넣었다. 이미 있던 파일이라 소유자 root와 권한(755/644)이 그대로였다. 새 machine-id `e0c18890…`.
- **굽다가 USB가 끊겼다.** 조각 0은 250 s에 OKAY였다. 조각 1은 380,633,088바이트째에서 `LIBUSB_ERROR_IO`로 끊겼다. 그 뒤 LK는 나머지 데이터를 기다리는 상태로 남아 명령에 답하지 않는다(케이블을 옮겨 다시 꽂아도 `download`에 시간 초과). 전원을 다시 넣어야 했다.
  - 같은 명령에서 결과를 보지 않고 `fbmini reboot`까지 보낸 것은 잘못이었다(LK가 데이터로 받아 버린 것으로 본다).
  - 전원을 다시 넣은 첫 번째에는 LK가 아니라 새 boot로 Linux가 떴고, userdata가 조각 0만 새것이라 `EXT4-fs (mmcblk0p24): error loading journal`로 initramfs 셸에 멈췄다. 거기서 `to-fastboot`을 실행했다. `(setsid nohup … &)` 형태는 initramfs에서 듣지 않았고(워치독 데몬이 그대로였다) 그냥 실행하니 30초 뒤 fastboot에 들어왔다.
  - 조각 1을 다시 보내다 118,489,088바이트째에서 같은 오류가 났다. 끊긴 위치가 매번 달라 파일이 아니라 연결 문제로 본다.
  - 그때 보드4의 USB-C는 Mac에서 USB 허브 두 단(UART 어댑터, USB 랜카드와 같은 허브) 뒤에 있었다. 사용자가 Mac 본체 포트로 옮겼고(ioreg로 허브 한 단), 전원을 다시 넣은 뒤 조각 1~3이 28 s, 39 s, 41 s에 OKAY였다. 
  - **원인(관찰):** Mac 로그(`/usr/bin/log show --predicate 'process == "icdd"'`, zsh에서는 `log`가 셸 내장 명령이라 전체 경로를 쓴다)에 보드의 USB 연결·해제가 남는다. 두 실패 시각(14:01:37.951, 14:16:02.973)에 LK 장치가 `Removed` 뒤 31 ms, 98 ms 만에 같은 자리(`@ 0x20141000`)에 다시 `Added`됐다. 보드가 재시작한 것이 아니라(LK가 다시 뜨려면 수 초) USB 연결만 순간 끊겼다 붙은 것이다. 같은 자리에서 전송이 없을 때도 두 번(14:05:34, 14:16:06), Linux gadget일 때도 한 번(14:12:18) 같은 일이 있었다. 같은 허브의 옆 자리(`@ 0x20142000`)에 있던 보드3은 11:40~13:52 동안 이런 일이 없었고, 보드4를 `@ 0x20130000`으로 옮긴 뒤로도 없었다. 그래서 그 허브 포트(또는 그때의 케이블 접촉)를 원인으로 본다. 케이블을 바꿨는지는 기록이 없다.
- 확인: 재부팅 19초 뒤 ACM, machine-id 일치, `running`, 실패 유닛 없음, wlan0 192.168.0.244(DHCP). dvslive는 첫 부팅부터 `page only`, `NRestarts=0`, 커널 WARNING 0개. 파일 13,191개 비교에서 다른 것은 onnxruntime 캐시 2개와 `dvsctl.py`가 바뀌어 다시 만들어진 `__pycache__/dvsctl.cpython-311.pyc`뿐이었다. `:8080/` 200, `/stats`에 `no_sensor`.
- **보드4의 센서(오후 2시 반 이후).** 사용자가 보드4의 CN4에 센서 보드를 꽂았다. 사용자 말로는 고장 난 보드일 수 있다. 전원을 끄고 다시 꽂은 뒤에도 dvslive는 `page only`였다. 보드2와 dmesg 순서(`vdd_ldo32` 켬, `sen_rst` low→high, `on_i2c`, MCLK)가 같고, 첫 I2C 읽기에서 보드2는 `SEL_MODE(0x3000)=0x00`, 보드4는 `HSI2C NO ACK`, `no I2C response at 0x10`이었다. page-only 프로세스가 노드를 연 채로 있을 때 `vdd_ldo32`는 `enabled 3300000 users=1`(보드2 스트리밍 중과 같다)이었고, 그때 `i2cdetect -y -r 7`에서 0x10(UU, 드라이버 소유) 말고는 아무 주소도 답하지 않았다. 그래서 주소가 달라서가 아니라 센서가 버스에 전기적으로 보이지 않는 것으로 본다(센서 보드 고장, 케이블·방향, CN4 중 하나. 가리지 못함). 다음 시험은 이 센서를 보드2에 꽂아 보는 것이다.

### 11.58 상자 크기 측정: 위·옆 DVS 두 대 방식, 별도 저장소 `e850-dvs-boxdim` (2026-11-15)

**결정(사용자).**
- 높이를 어떻게 잴지 다섯 가지를 비교했다. 운동 시차, 스테레오, 레이저 거리 센서, 라인 레이저, 위·옆 두 대다.
- 사용자가 위·옆 두 대를 제안했다. DVS 하나는 위에서, 하나는 옆에서 각각 보드에서 재고, 두 보드가 통신해서 상자 크기를 기록한다.
- 레이저 거리 센서(한 점)는 상자가 벨트 폭의 아무 데로나 지나가면 빗나가서 이 방법이 낫다고 판단했다.
- 저장소: 비공개, 이름 `e850-dvs-boxdim`, 문서는 한글이다. 라이선스는 정하지 않았다(비공개).
- 상자가 나란히 지나가는지는 사용자도 모른다. 미해결 질문으로 남겼다.

**만든 것(저장소 <https://github.com/philipdekim-OnD01/e850-dvs-boxdim>, 첫 커밋 `boxdim: proposal and reference code…`).**
- `docs/PROPOSAL.md`: 목적, 방법 비교, 구성, 측정 원리, 기록 형식, HTTP, 짝짓기, 정확도 예산, 보정 절차, 한계, 개발 단계 M0~M5, 확인한 것과 하지 않은 것.
- `boxdim/` (Python 표준 라이브러리):
  - `geometry.py`: 위 카메라 사각형에서 윗면(k = H/(H−h)배)을 떼어 바닥 면을 구한다. 옆 카메라 높이는 h = z + (cy − row)·d/f다. 둘을 서로 넘겨주며 4번 되풀이한다.
  - `merge.py`: t_us(각 보드의 CLOCK_REALTIME) + `lag_ms`로 `tol_ms` 안에서 짝을 짓는다. 위 기록 둘이 붙어 있으면 `ambiguous`로 표시한다.
  - `link.py`, `__main__.py`: `side`(포트 8092 `/boxes`), `top`(가져오기, 합치기, `/merged`), PC용 `merge`.
- 두 보드 모두 `dvs_boxes()`를 `--mm-per-px 1`로 돌리고, mm 보정은 `boxdim` 설정 파일 한 곳에서 한다.

**확인(측정).**
- PC 테스트 14개가 통과했다. 합성 상자 네 가지를 핀홀 모델로 앞으로 계산해 기록을 만들었고, 0.5 mm 안으로 되찾았다. 식이 자기 자신과 맞는지 본 것이고, 센서 데이터로 본 것은 아니다.
- 같은 테스트를 보드1(Python 3.11.2)의 `/var/tmp`에 풀어서 돌렸고 14개가 통과했다. 끝난 뒤 지웠다.
- 보드1은 `timedatectl`에서 `NTPSynchronized=yes`였다. 보드2는 확인하지 않았다.
- 보정하지 않을 때의 위 카메라 오차는 5 cm 상자가 주점에서 약 0.56 m 떨어진 경우 길이 +14 mm였다(합성 계산).

**아직 안 되는 것.**
- `dvs_boxes()` 기록에 상자 중심이 없어서 `boxdim`이 실제 기록을 버린다. 다음 할 일 M1에서 `box_end()` JSON에 `cx_px`, `cy_px`를 추가한다.
- `dvs_boxes()`는 화면에 상자가 하나뿐이라고 가정한다. 상자가 무작위로 오면 여러 개가 함께 보인다(M3).
- 화각 60°, 렌즈 왜곡 없음, 벨트 폭 50 cm는 가정이다.

**보드에 바꾼 것.** 없다. 테스트용 `/var/tmp/boxdim-test`는 만들었다가 지웠다.

### 11.59 DVS 3D: 바둑판 보정 없이 대략 보정으로 실제 두 보드 시험 (2026-11-15)

**왜.** 두 보드에 같은 렌즈를 달았지만 렌즈가 흐려 바둑판 모서리가 잡히지 않는다(11.56 끝, 0/30). 사용자가 "현 수준으로 테스트용으로" 돌려 보자고 했다.

**바꾼 것(저장소, `tools/host/dvs3d.py`).**
- `dvs3d.py rough [--f 600] [--baseline 0.05] [--png] [--n 20]`: 두 보드가 나란히 있다고 보고, 초점거리 f와 간격은 가정값으로 둔다. 두 화면의 세로 어긋남(dy)만 영상에서 재서 오른쪽 화면을 dy행 옮긴다(`rough_calib`, P2의 cy). `~/e850-logs/dvs3d/calib-rough.npz`에 따로 저장해 진짜 `calib.npz`를 덮지 않는다. 페이지 "보정" 줄에 "대략 (나란히 가정, 세로만 맞춤)"이 나온다.
- 어긋남은 `shift()`가 잰다. 처음에는 위상 상관(`cv2.phaseCorrelate`)이었는데, 실제 두 보드에서 같은 장면을 두 번 재니 (-53, -141), (71, -6)이 나왔다. 눈으로 본 값과 밝은 영역 중심으로 잰 값은 (99, +67)이었다. 한쪽(보드1)은 아주 흐리고 다른 쪽(보드2)은 칸이 보여서 위상 상관이 엉뚱한 곳에 걸린 것으로 본다. 같은 조건의 합성 시험(`test_shift_blurry_left_sharp_right`, 가로 287 px 틀림)을 만들어 실패를 확인한 뒤, 20쌍 이상에서 30% 넘게 켜진 영역의 중심으로 바꿨다. 장면에 큰 물체가 하나(태블릿)일 때만 맞는 방법이다.
- 시험 22개 통과(`test_dvs3d`, 새 것 3개).

**결과(관찰).** `rough --png --n 20`: 세로 +59행(오른쪽 화면이 아래), 시차 86 px. f 600 px, 간격 5 cm 가정으로는 0.35 m다(간격과 f 모두 재지 않았다). 그 뒤 `live --png --calib calib-rough.npz --zrange 0.2,2`를 15분 돌렸다(녹화 `~/e850-logs/dvs3d/rec-rough-20261115/`, 페이지 `page-rough-20261115b.png`). 쌍마다 점 2,500~7,200개, SGBM 16~18 ms, 버린 쌍 0. 점은 한 평면으로 모이지 않고 가까운 덩어리와 먼 점으로 흩어졌다. 보드1 화면이 흐리고 잡음이 많아서로 본다(가설).

**보드1 잡음 필터를 켜면 화면이 빈다(관찰, 원인 모름).** 11.56 끝에서 보드1 `filter_ms`를 보드2와 같은 4로 맞췄더니, 보드1 `/frame.png`가 이벤트 2초 41만 개인데도 0화소였다(3D 페이지 왼쪽이 검게 나옴). 0으로 되돌리자 프레임당 8만~13만 화소로 돌아왔다. 두 보드 `libdvsview.so`는 같고(c10b0382), 두 보드 모두 `dvs-filter` 스레드가 CPU3에서 돈다. 보드2는 4에서 정상이다. **지금 보드1 `filter_ms`는 0이다(원래 값).**

**간격 20 cm, 화면 40 cm(사용자가 잰 값)로 다시.** `rough`에 `--z`(가장 큰 물체까지 잰 거리)를 더했다. f = 시차 × z / 간격으로 정한다(한 점 보정). `rough --png --n 30 --baseline 0.20 --z 0.40`: 세로 +52행, 시차 82 px, f 164 px. 두 보드가 나란히라면 화각 111°라는 뜻이라 보통 렌즈와 맞지 않는다. 두 보드가 안쪽으로 모여 있을 수 있다(가설). 그러면 40 cm가 아닌 거리의 깊이는 틀어진다.
- `live --png --zrange 0.25,1.5`(녹화 `~/e850-logs/dvs3d/rec-rough-20261115-b20/`): 쌍마다 점 1,450~20,000개, SGBM 14.7 ms. 그런데 깊이 중앙값이 0.25 m(탐색 범위 끝)였고, 0.40±0.05 m 안의 점은 2%였다. 녹화 120쌍으로 전처리(그대로, median 5, 켜진 화소 밀도 σ 3·6)와 블록(5, 11, 17)을 바꿔 봤지만 2~7%였다.
- 원인(관찰): 보드1은 프레임마다 화소의 61%가 켜졌다(보드2 26%). 보드1에서 태블릿은 무늬 없는 큰 덩어리이고 보드2보다 약 1.3배 크다(사방 약 40화소 번짐으로 맞는다). 덩어리 둘레에도 잡음이 넓게 깔렸다. 짝을 찾을 무늬가 없으니 SGBM이 범위 끝에 붙는다. **보드1 초점을 맞추기 전에는 이 배치로 깊이를 믿을 수 없다.** 파이프라인 자체(두 보드에서 쌍 받기, 정류, SGBM, 페이지)는 끊김 없이 돌았다(15분 6,367쌍, 버린 쌍 1).
- 두 영상을 겹친 사진(정류 전·후)은 `~/e850-logs/dvs3d/fused-20261115.png`. 정리한 보고서(가능성과 필요한 환경, 세로 A4, 브라우저에서 고쳐 저장 가능)는 [DVS-STEREO-FEASIBILITY-20261115.html](DVS-STEREO-FEASIBILITY-20261115.html).

**보드1 초점을 맞춘 뒤 다시(같은 날, 사용자가 렌즈를 돌림).** 비교 사진 `~/e850-logs/dvs3d/focus-20261115.png`.
- [관찰] 보드1이 선명해졌다. 태블릿 테두리, 상표 글자, 바둑판 70칸, 뒤 배경까지 보인다. 이제 보드2가 상대적으로 흐리다. 보드1은 프레임마다 화소의 69%가 켜진다. 태블릿 밖의 배경 물체도 켜지므로, 깜빡이는 화면 빛이 주변을 비춘다는 가설과 맞는다.
- [관찰] 9×6 모서리는 여전히 두 보드 모두 0/30이다. 10장을 겹친 밝기 영상에서도 못 찾았다. 보드1은 검은 칸도 회색으로 켜지고, 화면 일부에 가로줄과 대각선 경계(HDMI 화면을 쓰는 입력의 그림 깨짐으로 본다)가 있다. `CALIB_CB_LARGER`로 작은 패턴을 찾으면 보드1은 7×5(35개)까지 잡혔다. 보드2의 4×3은 바둑판 밖에서 잡힌 것이라 믿을 수 없다.
- [관찰] 바둑판 칸 크기: 보드1 37.5 px, 보드2 27.7 px. 가로·세로 모두 **1.35배** 차이다. 번짐 때문이 아니다(초점 전에 "번짐으로 1.3배"라고 본 것은 틀렸다). 보드2가 태블릿에서 더 멀거나 두 렌즈의 초점거리가 다르다(둘 중 무엇인지 모름). 바둑판 중심은 보드1 (212, 213), 보드2 (168, 237)이다.
- [관찰] `rough`는 시차 78 px, 세로 +51행을 냈다. 그런데 바둑판 중심으로 재면 44 px, +24행이다. 보드1이 테두리와 배경까지 보게 되어 "밝은 영역 중심" 방식이 치우친 것으로 본다. `shift()`는 이제 이 장면에 맞지 않는다.
- [관찰] 깊이: 40 cm ±5 cm 안의 점은 1~2%로, 초점 전과 같다. 시차 범위를 10~265 px로 넓히면 138~154 px에 44%가 모인다. 78 px보다 약 75 px(바둑판 두 칸, 한 주기) 크다. [가설] 반복 무늬라서 SGBM이 한 주기 옆 칸과 짝을 지었다.
- [실패] 보드2 영상을 1.35배 키우고 세로를 맞춘 뒤 다시 계산했다. 152~160 px에 30%, 88~104 px에 21%가 모였고 44 px 부근에는 모이지 않았다. 칸 크기를 잰 영상(초점 직후)과 녹화(그 뒤) 사이에 태블릿이 움직였을 수 있어 결론을 내지 못했다.
- 녹화 `~/e850-logs/dvs3d/rec-rough-20261115-focus/`, 대략 보정 `calib-rough-focus.npz`(f 156 px, 세로 +51, 위 이유로 믿을 수 없음).

**목표 정정: 깊이가 아니라 두 영상을 합쳐 모양이 또렷한 영상(사용자).** 보드2 영상을 보드1 시점으로 옮겨 겹치고 합쳤다. 스크립트는 시험용(scratchpad)이고 저장소에는 아직 넣지 않았다.
- 맞추기: 두 보드의 20쌍을 겹친 밝기 영상(σ 5로 흐림)에 OpenCV ECC(`findTransformECC`)로 사영 변환을 구했다. 손으로 잰 시작값(배율 1.35, 바둑판 중심) 없이 이동 → 아핀 → 사영 순서로 해도 상관 0.856에 수렴했고, 손으로 시작한 변환과 5점 최대 3.0 px 차이였다. 화면 가운데에서 잰 실제 배율은 보드1이 가로 1.22배, 세로 1.35배 크게 본다(손으로 시작한 변환은 1.23배, 1.32배). 가로·세로가 달라서 보드2가 태블릿을 약 25° 더 비스듬히 보는 것으로 본다(가설). 정렬은 0.1 s, 쌍마다 합성(옮기기 + 흐림 + 곱)은 Mac에서 1.06 ms였다. 재현 스크립트와 자료 `~/e850-logs/dvs3d/fuse-20261115/`(`fuse.py`, `pairs.npz`). 겹친 사진에서 보드2 바둑판이 보드1 칸 위에 앉았다(`~/e850-logs/dvs3d/fused2-20261115.png` 4번).
- 합치기와 결과(화면 밝기 / 화면 밖 밝기, 화면 = 옮긴 보드2가 0.3 넘는 곳을 15 px 넓힌 영역, 시야의 63%): 보드1 혼자 20쌍 2.1, 한 쌍 3.3. 둘 다 본 곳만(기하 평균) 20.7, 한 쌍 43.4. 보드1 × 흐린 보드2 136. **보드2가 있는 영역(σ 6으로 흐린 뒤 0.15~0.4를 0~1로) 안의 보드1 화소**: 화면 밖이 0이 되고 화면 안은 보드1의 선명한 칸과 앞을 가린 물체가 그대로 남았다(`fused3-20261115.png`). 한 쌍으로도 된다.
- 한계: 사영 변환 하나는 평면(태블릿) 하나만 맞춘다. 다른 거리의 물체는 시차만큼 어긋난다(40 cm에 간격 20 cm). 합성은 "두 보드가 함께 본 것"만 남기므로, 한쪽만 본 진짜 물체(보드1에만 보이는 뒤 배경, 보드2 시야 밖)도 지운다. 입력은 아직 시각 없는 `/frame.png`다.
- 보고서(세로 A4, 브라우저에서 고쳐 저장 가능): [DVS-FUSION-20261115.html](DVS-FUSION-20261115.html).

### 11.60 정답 바둑판으로 두 보드 영상과 합성에 점수 매기기 (2026-11-15)

**왜.** 사용자가 목표를 정리했다. 이 실험은 나쁜 입력 두 개로 좋은 영상을 만드는 알고리즘이다. 최종 목표는 E850 보드끼리 MIPI 단 데이터를 주고받아 분류하는 것이고, 지금은 보이는 결과부터 만든다. 정답은 우리가 띄운 바둑판(`dvs3d-checker.html`: 10×7, 흰 칸만 깜빡임)이다. 그래서 모든 방법을 같은 숫자로 비교할 수 있다.

**한 것.** 사용자가 배치를 다시 한 뒤 두 보드에서 `/frame.png` 40쌍을 받았다(4.1 s, 실패 0). 보드 설정은 손대지 않았다. 지금 보드1은 `filter_ms` 0·`flip` 1, 보드2는 `filter_ms` 4·`flip` 2다. 스크립트는 저장소 밖에 있다: `~/e850-logs/dvs3d/eval-20261115/eval.py`(`python3 eval.py pairs.npz`).
- **정답 맞추기.** 바둑판 그림(흰 칸 1, 검은 칸과 한 칸 폭 테두리 0)을 쓴다. 칸 크기를 10~48 px로 바꿔 가며 정규화 템플릿 매칭으로 시작 위치를 찾고, ECC 사영 변환(흐림 σ 6→3→1.5)으로 맞췄다.
  - 칸 크기는 보드1 34.6 px, 보드2 24.3 px로 1.42배 차이다. 11.59 때(37.5/27.7, 1.35배)와 배치가 다르다.
  - 정답 윤곽을 겹친 그림: `overlay.png`.
  - [실패] 첫 판은 흰 칸 덩어리를 감싼 상자로 시작했다. 흰 칸이 대각선으로 붙어 한 덩어리가 되는 바람에 엉뚱하게 맞았다(칸 17 px).
- **점수.** 모두 보드1 시점에서 매긴다.
  - corr: 화면 전체에서 정답(흰 칸 1, 나머지 0)과의 상관. 1이 완벽하고, 배경이 켜지면 내려간다.
  - 판 안 상관: 바둑판 안에서만 잰 상관.
  - 흐림: 판 안에서 가장 잘 맞는 정답의 가우스 σ(px).
  - W/B/O: 흰 칸, 검은 칸, 판 밖의 평균(영상의 99% 값으로 나눔).
- **흐림 값 검사.** 정답을 σ 0, 3, 6으로 흐린 합성 영상에서 정확히 0, 3, 6이 나왔다. 영상을 3 px 밀면 0.5~2.5 커진다. 그래서 실제 영상의 흐림 값은 광학 흐림과 정답 맞춤의 국소 오차(렌즈 왜곡처럼 사영 변환 하나로 못 맞추는 것)가 합쳐진 값이다. 이 값은 순위로만 쓴다.

**결과(관찰).** 누적은 40쌍, 한 쌍은 40쌍의 중앙값이다.

| 영상 | corr 누적 | corr 한 쌍 | 판 안 상관 누적 | 흐림 누적 | W / B / O 누적 |
|---|---|---|---|---|---|
| 보드1 혼자 | 0.559 | 0.318 | 0.520 | 10.5 | 0.92 / 0.76 / 0.44 |
| 보드2 혼자(자기 시점) | **0.688** | **0.632** | 0.568 | 14.0 | 0.90 / 0.72 / 0.01 |
| 보드2를 보드1 시점으로, 자동 정렬(ECC) | 0.564 | 0.513 | 0.458 | 15.0 | 0.89 / 0.78 / 0.01 |
| 보드2를 보드1 시점으로, 정답으로 구한 정렬 | 0.648 | 0.596 | 0.571 | 16.0 | 0.89 / 0.72 / 0.01 |
| 보드2 영역 안의 보드1(11.59), 자동 정렬 | 0.600 | 0.520 | 0.516 | 13.0 | 0.92 / 0.74 / 0.01 |
| 기하 평균, 자동 정렬 | 0.594 | 0.539 | 0.509 | 13.0 | 0.94 / 0.78 / 0.04 |
| 보드2 영역 안의 보드1, 정답 정렬 | 0.658 | 0.597 | 0.585 | 14.5 | 0.92 / 0.72 / 0.00 |
| 기하 평균, 정답 정렬 | 0.663 | 0.610 | 0.594 | 15.0 | 0.94 / 0.74 / 0.04 |

- 두 입력의 결함이 다르다. 보드1은 잡음이다. 판 밖이 0.44로 켜지고, 검은 칸이 흰 칸의 0.83배로 켜진다. 보드2는 흐림이다. 배경은 0.01로 깨끗하지만 흐림 값이 14로 보드1(10.5)보다 크다.
- 지금 점수가 가장 높은 것은 깨끗한 보드2 혼자(0.688)다. 11.59의 합성(0.600)은 보드1보다는 낫지만 보드2 혼자를 넘지 못한다.
- 가장 큰 손실은 자동 정렬이다. 자동 ECC 정렬은 정답으로 구한 정렬과 10.6 px(칸의 1/3) 다르다. 정렬만 정답으로 바꾸면 기하 평균이 0.594에서 0.663으로, 한 쌍은 0.539에서 0.610으로 오른다.
  - 같은 보드1 시점끼리 비교하면 이것이 가장 높다(보드2를 옮긴 것 0.648, 보드1 0.559).
  - 그래도 보드2 자기 시점(0.688)보다는 낮다. 보드2를 1.42배 키워 옮기는 데서 0.04를 잃는다.
- 합성이 아직 못 하는 것은 검은 칸 억제다(B 0.72~0.78). 보드2가 흐려 검은 칸 위까지 퍼지므로, 보드2로는 보드1의 검은 칸 잡음을 지울 수 없다.
- 한 쌍 영상은 깜빡임의 일부만 담는다. 보드2 한 쌍에는 아래쪽 줄만 보일 때가 많다(`eval-labeled.png` 아래 줄). [가설] 시각을 맞춘 이벤트 창(`/evframe`)이 아니라 화면 스냅샷이라서다.
- 그림: `~/e850-logs/dvs3d/eval-20261115/eval-labeled.png`. 윗줄은 40쌍, 아랫줄은 한 쌍이다. 왼쪽부터 정답, 보드1, 보드2→1 자동, 보드2 영역×보드1, 기하 평균 순서다.

**다음(제안, 아직 안 함).**
- 정렬: 두 보드는 고정되어 있으니 정답 바둑판으로 구한 정렬을 저장해 계속 쓴다. 그 평면 거리에서만 맞는다.
- 검은 칸 잡음을 지울 방법을 찾고 같은 점수로 비교한다. 예를 들어 깜빡임 시각에 맞지 않는 이벤트를 버리는 방법이 있는데, 시각이 있는 `/evframe`이 필요하다.
- 정답을 아는 그림 여러 장(도형, 글자)으로 넓혀서 바둑판에만 맞춘 방법이 되지 않게 한다.

### 11.61 AI 합성 준비: 정답 그림 페이지, 렌즈 왜곡 항 시험 (2026-11-15)

**방향(사용자, 승인됨).** 양안은 한쪽 수신 품질이 나빠지면 다른 쪽이 보상하는 구조다. 그래서 11.60에서 깨끗한 보드2 혼자가 단순 합성보다 높게 나온 것은 맞는 결과로 본다. 영역마다 어느 쪽을 믿을지 스스로 정하는 AI 합성을 만든다.

참고 자료(사용자 폴더 `~/Desktop/00.OSDA/CNN20260510/Projects/01.DVS`)에서 가져올 원리는 다음과 같다.
- SAIT 스테레오 DVS(`dvs-master/.../stereo/stereoDisparityExtractor.cpp`, `edge/edgeEventExtractor.cpp`):
  - 두 눈의 이벤트를 한 흐름에 넣고 `eyeID`로 구분한다(시계 하나).
  - 이웃 화소의 시각 패턴으로 경계 이벤트만 받는다. 외따로 켜진 잡음은 여기서 버려진다.
  - 8방향 각도, refractory, 술통 왜곡 보정이 있다.
  - 3×3 창에서 각도가 맞는 쪽을 짝으로 고른다.
  - `eyeBalance`: 두 눈의 이벤트 수 비가 2.5를 넘으면 약한 눈을 믿지 않는다.
- 두 과제 문서: 합성은 이벤트 복셀 그리드 → Tiny Recurrent U-Net → INT8, 판단은 raw 이벤트 → Event Transformer 계열이다.

**정답 그림 페이지(저장소 `tools/host/dvsfuse_page.py`, 시험 `test_dvsfuse_page.py` 5개).**
- 흑백 그림 160장을 바둑판과 같은 10×7 틀에서 장마다 3초씩 보여 주고, 8분마다 처음부터 반복한다. 흰 부분만 5 Hz로 깜빡인다.
- 처음과 16장마다 바둑판을 넣어 화면→센서 변환을 맞추고 다시 확인한다.
- 그림 종류는 도형, 글자, 줄무늬(주기 6~60 px), 점, 덩어리이고, 30%는 흑백을 뒤집었다. 씨앗값이 고정이라 매번 같은 그림이 나온다.
- 출력은 `~/e850-logs/dvs3d/fusepage-20261115/dvs-truth-images-v2.html`(1.36 MB)과 같은 이름의 `.npz`(설계 그림, 실제로 그려지는 정답)다.
- [실패] 첫 판은 그림을 `<img src="data:image/png…">`로 넣었다(0.5 MB). 아이패드 파일 앱에서 열면 검은 화면이었다(사용자).
  - Mac Quick Look 썸네일에서는 첫 그림이 보였으므로 원인은 확인하지 못했다.
  - [가설] iPad 파일 미리보기가 data URI 그림을 불러오지 않는다. 바둑판 페이지는 그림 없이 CSS로만 그려서 됐다.
- v2: 그림을 페이지 안의 SVG 경로(짝홀 채우기)로 그린다.
  - 정답은 그 경로를 다시 칠한 것이다. 설계 그림과 비교하면 치우침이 1% 미만이고, 가장 고운 줄무늬에서 평균 차이가 최대 0.1이다(설계 그림의 계단).
  - 확인: 스크립트를 끈 Chrome(1180×820)에서 0~14초 동안 3초마다 정확히 다음 그림으로 넘어갔다(정답과의 상관 0.983 이상). Mac Quick Look 썸네일도 정상이다.
  - 애니메이션이 멈추는 뷰어에서는 첫 바둑판이 멈춘 채로 보이게 했다. 증상을 구별하기 위해서다.
  - 2026-11-15 17:33에 사용자 메일로 보냈다. 첫 판은 17:18에 보냈다.

**렌즈 왜곡 항(11.60 다음, `~/e850-logs/dvs3d/eval-20261115/lens.py`).** 정답 맞춤을 "사영 변환 + 반지름 왜곡 k 하나"로 바꿨다. 바둑판 네 모서리와 k를 Powell로 맞췄다.
- 자체 검사: 알려진 k(6%)를 흐리고 잡음을 섞은 합성 영상에서 3 px 틀린 시작값으로 시작했다. k 5.89%, 모서리 0.13 px로 되찾았다.
- 실제 결과:
  - 맞춤 상관(판 영역, σ 1.5): 보드1 0.606 → 0.609(k +1.8%), 보드2 0.652 → 0.654(k −8.1%).
  - 합성 점수: 기하 평균(정답 정렬) 0.660 → 0.663, 한 쌍 0.611 → 0.611.
- **[관찰] 렌즈 항은 거의 도움이 되지 않는다.** 11.60의 국소 오차와 보드1 흐림 값(9.5)은 반지름 왜곡 때문이 아니다. 고정 정렬(사영 변환)의 이득(+0.07)이 전부다.

### 11.62 정답 그림 녹화, DVS 설정 최적화, 두 보드 AI 합성 1차 (2026-11-15, 사용자 외출 중 자율 진행)

**지시(사용자).** "실험 시작", "dvs 레지스터 설정 변경하면서 최적화 해서 해", "2시간 후에 올께, 너가 다 알아서해야해". 태블릿(iPad)에는 v2 페이지(11.61)가 떠 있었다.

**녹화와 이름 붙이기(Mac, `~/e850-logs/dvs3d/rec-truth-20261115/`).**
- `rec.py SECONDS`: 두 보드의 `/frame.png`를 동시에 받는다. 초록·빨강 면을 2×2 최대값으로 480×360으로 줄이고 비트로 묶어, 30초마다 `chunk-NNN.npz`에 쓴다.
  - [관찰] 이 화면 설정에서는 색이 극성이 아니라 보드를 가른다. 보드1은 빨강만, 보드2는 초록만 켜졌다. 그래서 두 면을 합쳐 "켜짐"으로 쓴다.
- `build.py`: 녹화의 각 쌍이 몇 번째 그림인지 정한다.
  - 처음에는 11.60의 정렬로 160장과 맞춰 시간축을 찾았다. 그런데 쌍의 2.6%만 제 그림에 맞았다[실패].
  - 원인: 바둑판은 좌우로 뒤집힌 화면과 한 칸 밀린 화면을 구별하지 못한다.
  - 고친 방법:
    1. 바둑판이 48초마다 다시 나오는 성질로 바둑판 구간을 찾는다(48·96·144초 뒤의 쌍과 닮은 위상).
    2. 그 구간으로 화면→센서 변환을 맞춘다.
    3. 보드마다 방향 4가지(그대로, 좌우, 상하, 180°)와 주기 안 위치 10가지 중에서 내용 그림이 가장 잘 맞는 것을 고른다.
  - 결과: 두 보드 모두 **좌우 반전**이었다. 고른 시간축에서 보드2 일치도는 0.700이고, 다른 위치 9개는 0.39~0.43이었다. 보드2에서 제 그림의 순위는 160장 중 중앙값 2위, 보드1은 26위다(보드1 잡음).
  - 두 녹화의 t0는 138.60 s와 138.70 s로 같았다. 약 30분 간격의 두 녹화에서 iPad 페이지와 Mac 시계가 0.1 s 안에서 맞았다.
- 녹화 1(기준 설정, 17:55~18:11): 8,130쌍(8.2쌍/s)이다. 슬롯 경계 앞뒤 0.4 s를 버리고 5,925쌍이 남았다. 160장 모두 보였고, 그림마다 최소 17쌍이다.

**DVS 설정 최적화(`sweep.py`, 18:12~18:24).**
- [제약] ssh의 `sudo`는 비밀번호가 필요했다. `philip`은 `i2c` 그룹이 아니고, USB ACM(root 콘솔)도 꽂혀 있지 않았다. 그래서 아날로그 바이어스(0x0157, 0x0166~68 등) 레지스터는 바꿀 수 없었다.
- root 없이 바꿀 수 있는 것만 바꿨다.
  - 센서: `POST /sensor`의 margin. scan 주기 레지스터 DTAG_FRM_MARGIN(0x321D/0x321E)이며, 바꾸면 dvslive가 다시 뜬다.
  - 화면 생성: `filter_ms`, `window_ms`.
- 원래 값은 `settings-before.txt`에 있다. 보드1은 fps 37, filter 0, window 0이고, 보드2는 fps 23, filter 4, window 0이다. 두 보드 모두 margin 17, flush 50이다.
- 판정: 설정마다 45 s를 녹화했다. 각 쌍의 정답 상관에서 기준 녹화의 같은 그림 중앙값을 뺀 평균을 썼다. 설정마다 다른 그림을 보게 되므로 그림의 난이도를 지우기 위해서다. 처음 쓴 "기준 대비 비율"은 보드1처럼 기준이 0에 가까우면 왜곡되어 버렸다.

| 설정(두 보드에 같이) | 보드1 차이 | 보드1 빈 프레임 | 보드2 차이 | 보드2 빈 프레임 |
|---|---|---|---|---|
| filter 0 | +0.019 | 4% | −0.048 | 0% |
| filter 2 | +0.013 | 42% | +0.004 | 6% |
| filter 4 / 8 | −0.017 / −0.042 | 57% / 76% | −0.003 / −0.009 | 6% / 7% |
| (filter 2) window 20 / 80 / 150 | −0.041 / +0.017 / +0.023 | 81% / 65% / 43% | −0.016 / +0.009 / +0.000 | 12% / 6% / 3% |
| (보드1 filter 2 window 80, 보드2 filter 2) margin 17 (1000 scan/s) | +0.012 | 55% | −0.008 | 8% |
| 같은 조건, margin 48 (500 scan/s) | **+0.050** | 21% | −0.001 | 2% |
| 같은 조건, margin 0 (약 2300 scan/s, 식으로 계산) | +0.001 | 60% | −0.019 | 36% |
| margin 48, 보드1 filter 0 window 80 / 150 | −0.014 / −0.057 | 1% / 1% | — | — |

- [관찰] 보드1은 `filter_ms`를 켜면 빈 프레임이 크게 는다(11.59와 같은 현상, 원인 모름). 그래도 500 scan/s에서는 21%로 줄었고, 필터를 끈 것보다 점수가 높았다. 잡음을 줄이는 이득이 빈 프레임의 손해보다 크다.
- [관찰] 약 2300 scan/s(margin 0)에서는 보드2도 36%가 빈 프레임이다. 디코더·화면이 따라가지 못하는 것으로 본다(가설).
- **보드에 남긴 설정(현재):** 두 보드 모두 margin 48(500 scan/s), filter 2다. 보드1만 window 80이다(`settings-after.txt`). 500 scan/s에서는 보드 부하가 줄어 Mac이 받는 쌍이 8.2쌍/s에서 12.3쌍/s로 늘었다.
- [한계] 설정마다 45 s 한 번씩만 쟀다. 차이 0.01~0.02는 잡음 안일 수 있다.
- 녹화 2(최적 설정, 18:24~18:41, `rec-truth2-20261115/`): 12,207쌍이다. 8,899쌍이 남았고, 그림마다 최소 31쌍이다. 보드1의 바둑판 맞춤 상관은 0.515에서 0.610으로, 보드2의 내용 일치도는 0.696에서 0.752로 올랐다.

**두 보드 AI 합성(`train.py`, 모델 `~/e850-logs/dvs3d/fusenet-20261115/`).**
- 입력: 두 보드의 켜짐 지도를 각자의 고정 변환(정답 바둑판으로 구한 것)으로 페이지 좌표(300×210)에 옮긴다. 이것을 같은 슬롯의 연속 k쌍(1~6)으로 평균한다.
- 정답은 그 슬롯에 그려진 그림이다.
- 모델은 U-Net(24·48·96·192 채널, 파라미터 110만 개)이고, 손실은 BCE + L1, AdamW + OneCycle, batch 16이다. MPS에서 단계당 약 0.34 s 걸렸다.
- **보상 학습:** 표본의 절반에서 한쪽 보드를 무작위로 망가뜨린다. 방법은 통째로 빼기, 흐림 σ 2~6, 잡음 5~40%, 일부 가리기, 약하게(×0.2~0.6) 중 하나다. 좌우·상하 뒤집기도 쓴다.
- **시험 그림:** 번호 % 5 == 2인 내용 그림 30장(바둑판인 32·112번 제외)은 학습에 쓰지 않았다. 녹화 16.5분은 두 바퀴와 셋째 바퀴 앞부분이라 녹화마다 62묶음이다. (처음에 31장이라고 적었으나 30장이 맞다, 종합 보고서를 쓰며 고침.)
- **1차 모델(녹화 1만, 2,000단계, `fusenet-20261115/base/`).** 시험 그림에서 잰 정답 상관의 중앙값이다. 한 쌍은 n 1,120, 5쌍은 n 197이다.

| 조건 | 보드1 | 보드2 | 기하 평균 | 11.59 합성 | **모델** |
|---|---|---|---|---|---|
| 그대로, 한 쌍 / 5쌍 | 0.081 / 0.193 | 0.303 / 0.354 | 0.258 / 0.345 | 0.217 / 0.286 | **0.646 / 0.729** |
| 보드1 뺌 | 0 / 0 | 0.303 / 0.354 | 0 / 0 | 0 / 0 | **0.630 / 0.693** |
| 보드2 뺌 | 0.081 / 0.193 | 0 / 0 | 0 / 0 | 0 / 0 | **0.185 / 0.284** |
| 보드1 흐림 σ 4 | 0.118 / 0.235 | 0.303 / 0.354 | 0.288 / 0.348 | 0.241 / 0.301 | **0.626 / 0.705** |
| 보드2 잡음 20% | 0.081 / 0.193 | 0.258 / 0.288 | 0.227 / 0.317 | 0.176 / 0.263 | **0.637 / 0.709** |
| 보드1 왼쪽 절반 가림 | 0.006 / 0.011 | 0.303 / 0.354 | 0.126 / 0.127 | 0.097 / 0.114 | **0.636 / 0.701** |

  - [관찰] 모든 조건에서 모델이 두 입력 중 좋은 쪽보다 높다. 한쪽을 빼거나 망가뜨려도 그렇다.
  - [관찰] 이득의 대부분은 흐린 보드2를 복원하는 데서 온다. 보드1을 빼도 0.630이고, 보드1을 더하면 +0.016(한 쌍), +0.036(5쌍)이다.
  - 그림 `base/fig-k5.png`:
    - 보드2에서 "BOVX"처럼 번진 글자를 모델이 **E6VX**로 되살렸다(0.54 → 0.78).
    - 흑백이 뒤집힌 "5"에서는 보드2가 하얗게 포화됐다. 모델은 보드1에 남은 흐릿한 5를 그렸다(−0.02 → 0.18). 보드1이 보상한 예다.
    - 주기 약 30 px의 줄무늬는 보드1에만 희미하게 있고 모델은 살리지 못했다(0.36).
- **최종 모델(녹화 1 + 2, 3,000단계, 949 s, `fusenet-20261115/both/`).** 같은 시험 그림으로 쟀다. 각 칸은 한 쌍 / 5쌍이다.

| 녹화 2(최적 설정) | 보드1 | 보드2 | 기하 평균 | 11.59 합성 | **모델** |
|---|---|---|---|---|---|
| 그대로 | 0.178 / 0.378 | 0.296 / 0.375 | 0.186 / 0.434 | 0.189 / 0.457 | **0.713 / 0.804** |
| 보드1 뺌 | 0 / 0 | 0.296 / 0.375 | 0 / 0 | 0 / 0 | **0.638 / 0.760** |
| 보드2 뺌 | 0.178 / 0.378 | 0 / 0 | 0 / 0 | 0 / 0 | **0.343 / 0.606** |
| 보드1 흐림 σ 4 | 0.205 / 0.415 | 0.296 / 0.375 | 0.188 / 0.435 | 0.216 / 0.463 | **0.663 / 0.783** |
| 보드2 잡음 20% | 0.178 / 0.378 | 0.228 / 0.294 | 0.175 / 0.409 | 0.205 / 0.424 | **0.695 / 0.797** |
| 보드1 왼쪽 절반 가림 | 0.044 / 0.131 | 0.296 / 0.375 | 0.111 / 0.190 | 0.109 / 0.204 | **0.668 / 0.780** |

  - 녹화 1(기준 설정)에서는 모델이 0.660 / 0.736이다. 보드1 뺌 0.650 / 0.730, 보드2 뺌 0.202 / 0.304다.
  - [관찰] 설정 최적화로 보드1 한 쌍의 점수가 0.081에서 0.178로 올랐다. 그러자 모델이 보드1을 더 쓴다. 보드1을 뺄 때 잃는 점수가 녹화 1에서 0.010, 녹화 2에서 0.075다.
  - [관찰] 모든 손상 조건에서 모델이 남은 좋은 쪽 입력보다 높다(양안 보상).
  - [실패 예, `both/fig-k5.png`]
    - 주기 약 6 px의 고운 줄무늬는 모든 방법이 0이다.
    - 녹화 2의 흑백 반전 "5"는 보드1의 5쌍이 모두 빈 프레임이었다(필터 문제). 그래서 모델도 0.18에 그쳤다.
  - [한계]
    - 평면 장면 하나뿐이다.
    - 같은 종류의 그림(학습에 없던 그림이지만 생성 방식은 같다)만 시험했다.
    - 입력이 시각 없는 화면 스냅샷이다.
    - 보드 위 속도는 재지 않았다.
- 보고서(세로 A4, 브라우저에서 고쳐 저장 가능, 7쪽): [DVS-FUSION-AI-20261115.html](DVS-FUSION-AI-20261115.html).
- **종합 보고서(같은 날 저녁, 사용자 요청):** [DVS-BINOCULAR-REPORT-20261115.html](DVS-BINOCULAR-REPORT-20261115.html). 11.56·11.59~11.62를 한 흐름으로 묶었다. 그림은 시험 환경 배치도, 정답 그림 시간표, 정렬 과정, 처리 흐름도, U-Net 구조도, 학습 손상 예시, 보상 막대그래프, 결과 영상이다. 결과 영상은 `both/model.pt`로 다시 만든 것이고 점수는 `fig-k5.png`와 같다. 세로 A4이고 PDF로는 19쪽이다.
  - 그림 생성 스크립트는 scratchpad에 두었다(`report2/figs.py`, `svgs.py`, `build.py`). PDF와 HTML 사본은 `~/e850-logs/dvs3d/report-20261115/`에 있다.
  - 2026-11-15 21:17에 사용자 메일로 PDF와 HTML을 보냈다. 보낸편지함에서 첨부 2개를 확인했다.
  - 이 보고서에 새로 넣은 분석: 이득 나누기(보드2 복원 +0.342, 보드1을 더한 몫 +0.075).
  - 쓰면서 고친 것:
    - 시험 그림은 31장이 아니라 30장이다(바둑판 32·112번 제외).
    - margin 0의 scan 속도는 식으로 약 2300/s다(2000이라고 적었었다).
    - 두 녹화의 t0 일치는 "25분 동안"이 아니라 "약 30분 간격의 두 녹화에서"다.
- **강의 자료(2026-11-16, 사용자 요청):** [DVS-BINOCULAR-LECTURE-20261116.html](DVS-BINOCULAR-LECTURE-20261116.html). 16:9 슬라이드 36장, 표지는 김임환 교수·삼성전자 DS교수실이다.
  - 형식: HTML 한 파일(1.7 MB)이고 그림이 파일 안에 들어 있다. 글자를 눌러 고치고 "HTML로 저장"을 누르면 된다. 발표 모드(→ ←, Esc)가 있고, 인쇄하면 16:9 PDF 36쪽이 나온다.
  - 내용: 목적, 의의, 선행 연구 표 2장, 개발 환경, U-Net(누가·언제·왜, 구조, 맞는 이유), VAE 등 후보 비교, 바둑판의 의미, 학습 과정(손실 곡선 포함), 결과, 통계, 한계, 향후 계획, 참고 문헌 25편, 깃 정보.
  - 서지 확인: 삼성 DVS 논문 두 편(Son ISSCC 2017, Suh ISCAS 2020)은 웹에서 서지를 확인했다. 나머지는 알려진 서지를 그대로 적었고 하나씩 대조하지는 않았다.
  - 검사: headless Chrome에서 36장 모두 넘침이 없고 깨진 그림이 0개다. 390 px 폭에서 가로 넘침이 없다. 발표 모드, 키 넘기기, Esc, 저장을 시험해 통과했다(`ui_test.py`).
  - 원본: `~/e850-logs/dvs3d/lecture-20261116/`. 슬라이드 원본과 빌드, 검사 스크립트, 보고서 그림 스크립트를 scratchpad에서 옮겼다. PDF 사본도 함께 두었다.
  - 2026-11-16 07:19에 사용자 메일로 PDF와 HTML을 보냈다. 보낸편지함에서 첨부 2개를 확인했다. 메일 본문에 학습 그림 수 정정(120 → 130장)도 적었다.
- **통계(강의 자료용으로 새로 계산, [관찰]):** `~/e850-logs/dvs3d/fusenet-20261115/stats.py`, 결과는 `stats.json`.
  - 단위: 같은 그림의 창끼리는 독립이 아니어서 시험 그림 30장을 단위로 잡았다.
  - 방법: 그림 단위 부트스트랩을 10,000번 해서 중앙값 차의 95% 구간을 구했다. 그림별 중앙값에는 윌콕슨 부호순위 검정(양측)을 했다.
  - 확인: 다시 계산한 중앙값이 `eval.json`과 같다.
  - 녹화 2, AI 합성 − 보드2: 한 쌍 +0.417 [0.361, 0.451], 28/30, p = 9×10⁻⁹. 5쌍 +0.430 [0.325, 0.534], 28/30.
  - 녹화 2, 두 보드의 몫(AI − 보드1을 0으로 둔 AI): 한 쌍 +0.075 [0.042, 0.097], 27/30, p = 4×10⁻⁶. 5쌍 +0.044 [0.030, 0.071], 27/30, p = 2×10⁻⁶.
  - 녹화 1(기준 설정)의 같은 몫: 한 쌍 +0.010 [−0.007, 0.019], 18/30, p = 0.04. 5쌍 +0.006 [−0.017, 0.022], p = 0.29. 구간이 0을 포함한다. 센서 설정을 바꾼 뒤에야 두 보드의 몫이 0과 구별됐다.
  - 녹화 2에서 보드2를 빼도 보드1만으로 0.343 대 보드1 그대로 0.178이었다. 23/30, p = 7×10⁻⁴.
  - 다중 비교 보정은 하지 않았다. 학습은 씨앗값 하나로 한 번만 했다.
- **고친 것:** 학습에는 바둑판 10장도 들어갔다. `train.py`가 `not TEST`를 학습으로 쓰고, 바둑판 슬롯은 TEST가 아니기 때문이다. 그래서 학습 그림은 130장(내용 120 + 바둑판 10)이다. 종합 보고서 시간표 범례의 "학습 그림 120장"과 7.2절 문장을 이에 맞게 고쳐 다시 만들었다. 메일로 보낸 판(11-15 21:17)에는 고치기 전 범례가 들어 있다.
- 다음:
  - 아날로그 바이어스 최적화(사용자 sudo 또는 USB root 콘솔).
  - `/evframe` 설치(시각, SAIT식 경계 거르기).
  - 그림 종류 넓히기(자연 영상, 손).
  - 모델 축소와 INT8, A55 속도 측정.
  - 보드 사이 MIPI 연결과 분류.

### 11.63 재현 백서(16:9, 57장)와 core 사용률 다시 재기 (2026-11-16, 사용자 외출 중)

**지시(사용자).** "850에 사용된 os, kernel버전, 개발환경, 각 core별 지정 역활과 사용량, 지정방법 코드예, 그리고, 맥에서 사용한 unet 학습방법과 코드, 학습자료는 왜 그런 모양인지 근거, 등등 이 내용을 보고 그대로 할 수 있도록 자세히 백서를 만들어 줘. 16:9 수정가능 html 슬라이드로", "만들어 지면 메일로 보내", "전문용어는 영어를 사용하고, 페이지 하단에 각주로 단어 설명 쉽게써줘".

**만든 것.** [E850-DVS-WHITEPAPER-20261116.html](E850-DVS-WHITEPAPER-20261116.html): 16:9 슬라이드 57장, HTML 한 파일(1.0 MB, 그림 포함). 강의 자료(11.62)와 같은 틀이라 글자를 눌러 고치고 "HTML로 저장", 발표 모드, 인쇄(PDF)가 된다.
- 구성: 01 Hardware·OS·kernel(boot 흐름, command line 인자의 이유), 02 개발 환경(Mac 버전, Docker volume, 접속 경로), 03 build·flash·복구, 04 core 배치(역할, 근거, systemd·C·Python 지정 코드, uclamp 측정, 사용률, 확인 명령), 05 board DVS pipeline과 DVS 설정, 06 학습 자료(정답 그림 페이지 설계 근거 세 쪽, 녹화, 정렬, 학습 입력), 07 U-Net(구조, 코드 네 쪽, 손상 증강, 설정 근거, 실행 순서, 손실 곡선, 결과), 08 재현 checklist(단계, 시간, 통과 기준), 파일 위치, 한계.
- 전문용어는 영어로 두고, 쪽마다 아래에 쉬운 한글 각주를 달았다. 본문의 각주 번호와 각주 줄이 쪽마다 같은지 스크립트로 검사했다.
- 코드 쪽은 손으로 옮겨 적지 않았다. `build.py`의 `CODE` 표가 실제 파일(`dvsview.c`, `dvsctl.py`, `dvsact.py`, `e850-cpu7.conf`, `dvsfuse_page.py`, `rec.py`, `train.py`)에서 시작·끝 문자열로 잘라 넣는다. 코드를 고치면 다시 빌드하면 된다.
- 원본(`build.py`, `slides.html`, `check.py`, `ui_test.py`, `thr.sh`), PDF(57쪽), 학습 코드 zip은 `~/e850-logs/dvs3d/whitepaper-20261116/`에 있다. zip(`e850-dvs-fusion-scripts-20261116.zip`)은 `~/e850-logs/`에 풀면 백서의 "실행 순서" 쪽에 적은 폴더 모양이 된다.

**새로 잰 것([관찰], 2026-11-16 08:27 KST, 두 보드 각 5초, 읽기 명령만).**
- core 사용률(`/proc/stat`), 보드1 / 보드2: CPU0 3.6 / 3.6%, CPU1 5.4 / 5.0%, CPU2 34.1 / 20.3%, CPU3 6.0 / 6.4%, CPU4 14.2 / 4.8%, CPU5 40.4 / 0.4%, CPU6 0.0 / 49.4%, CPU7 0.0 / 0.4%.
- thread 사용률(`/proc/<pid>/task/*/stat`, 따로 잰 5초, 스크립트 `thr.sh`): `dvs-act` 53.8 / 53.8%(허용 4-6), `dvs-render` 36.2 / 20.6%(2), `dvs-filter` 5.4 / 5.8%(3), 디코더(`python3` 주 스레드) 4.4 / 5.8%(1), `dvs-banner`·`dvs-hist` 0.2%(7). 이때 웹을 보는 사람은 없었다.
- 그리기 차이는 화면 설정 fps가 달라서로 본다(보드1 37, 보드2 23, 11.62의 `settings-after.txt`) [가설].
- IRQ: 93개의 `smp_affinity_list`가 0-7인데, 장치 IRQ 수는 CPU0에만 있다(보드1 DSIM 7,492,128, `scsc_wlbt` 2,293,669). irqbalance는 없다. 보드2 GIC는 `arm,cortex-a15-gic`이다.
- ssh 셸의 `nproc`는 7이다(`e850-cpu7.conf`). clock은 두 cluster 모두 546~2002 MHz 12단계, governor는 schedutil이다.
- Mac 환경: Apple M6(12 core, 32 GB), macOS 27.2.1, Python 3.12.2(`~/.venvs/torch-python`), PyTorch 2.12.0(MPS), opencv-python 4.13.0.92, NumPy 2.4.6, SciPy 1.17.1, Docker 29.9.3. 보드: Python 3.11.2, NumPy 1.24.2, onnxruntime 1.31.1, gcc 없음.
- `/proc/cmdline` 앞부분(`stack_depot_disable`, `kvm-arm.mode=protected`, `cgroup_disable=pressure` 등)과 `console=ttySAC0`, `androidboot.*`는 `build-bootimg.sh`의 `CMDLINE`에 없다. LK가 붙이는 것으로 본다 [가설].

**검사.** headless Chrome에서 57장 모두 넘침 0, 깨진 그림 0, 390 px 폭 가로 넘침 없음. 이번에는 `check.py`가 코드 블록(`pre`)의 가로 잘림도 본다(강의 자료 때는 뺐다). 처음 빌드에서 코드 블록 7곳이 넘치거나 잘려서 쪽을 나누고 배치를 바꿨다. JetBrains Mono 합자 때문에 `<=`가 `≤`로 보여서 합자를 껐다. `ui_test.py`(발표 모드, 키, Esc, 저장) 7/7 통과, PDF 57쪽.

**메일.** 2026-11-16 08:45:46에 PDF, HTML, 학습 코드 zip을 사용자 메일로 보냈다. 보낸편지함에서 첨부 3개를 확인했다.

**보드1 응답 없음(관찰).** 08:27 측정 뒤 08:31쯤부터 보드1(192.168.0.236)에 ssh가 시간 초과, ping은 `No route to host`/`Host is down`이다. 보드1에 보낸 명령은 `/proc`·sysfs 읽기, `ps`, `systemctl show`뿐이었다. Mac에 USB(ACM)와 UART가 꽂혀 있지 않아 원인을 보지 못했다. 보드2는 정상이다.

**사용자 전원 재투입(2026-11-16 21:57 KST쯤, 관찰).** 사용자가 "보드 3개 다 올림"이라고 알렸다. 22:00에 확인한 결과는 다음과 같다.
- 보드1(machine-id `4aeede4c`)과 보드4(`e0c18890`): uptime 3분, `dvslive`·`nginx`·`e850-wifi` active, 80번 포트 응답 302.
- 보드2(`6f31c0bd`): 재부팅 없이 1일 7시간째 동작 중, 같은 서비스 active.
- 보드3(.234): ping 실패. 서브넷(.2~.254)을 훑어도 ARP에 보드3 MAC(00:00:0f:0a:74:9c, fastboot 일련번호로 추정)이 없다. `A5069RR4` UART를 `~/e850-logs/uart-board3.log`에 다시 기록하기 시작했지만 줄바꿈을 보내도 0바이트였다. 꺼짐, 멈춤, Wi-Fi 없이 동작 중인 상태 가운데 어느 것인지 구별하지 못했다.
- 보드1이 08:31에 멈춘 원인: 이전 부팅에서 philip이 볼 수 있는 journal은 08:29:11 KST의 ssh 끊김이 마지막이다. 커널 journal은 root가 필요해 보지 못했다. 원인 모름.

**저장소에 바꾼 것(커밋 안 함).** `docs/E850-DVS-WHITEPAPER-20261116.html` 추가, 이 절, `BRINGUP_STATUS.md`. 보드에는 바꾼 것이 없다.

**남은 것.**
- 학습 코드(`rec.py`, `build.py`, `train.py`, `sweep.py`, `eval.py`, `stats.py`)를 저장소로 옮길지 사용자 확인. 지금은 `~/e850-logs/dvs3d/` 아래에만 있다. `stats.py`는 경로가 절대 경로다.
- 보드3이 네트워크에 없다. USB-C를 꽂아 fastboot·ACM으로 상태를 보거나, UART를 본 채로 전원을 다시 넣는다.
- 커밋은 사용자 확인 뒤.

### 11.64 현미경 모드 0단계: 보드4에서 USB 호스트 켜기 시험 (2026-11-17)

**목표(사용자 결정).** SOL 현미경(CX3 UVC, 별도 저장소 `sol-cx3-uvc`)을 E850에 꽂아 HDMI와 Wi-Fi 웹으로 보인다. 레지스터도 읽는다. 부팅 때 DVS 모드와 현미경 모드를 고른다. 용도는 사람들에게 보여 주기와 시료 사진 기록이다. 설치는 보드3에 한다. 보드3이 네트워크에 없어서 0단계 시험은 보드4에서 했다. 보드4에는 사용자가 오늘 DVS 센서를 달았다. dvslive가 영상과 레지스터를 웹과 HDMI에 보인다(사용자 확인).

**알아낸 것.**
- 모든 보드 커널에 `CONFIG_USB_VIDEO_CLASS=y`, `CONFIG_USB_XHCI_HCD=y`, `CONFIG_USB_DWC3_DUAL_ROLE=y`가 있다. fb0은 1920×1080이다. 보드4에는 `v4l2-ctl`과 numpy가 있다.
- USB 컨트롤러는 DWC3 하나(`13600000.dwc3`)다. 호스트로 바꾸면 USB-C의 ACM과 ECM이 없어진다.
- vendor glue에 OTG 상태 sysfs가 있다. `13600000.usb/b_sess`에 0을 쓰면 gadget이 멈춘다. `id`에 0을 쓰면 `a_host`가 되고 xHCI가 올라온다(USB2 루트 포트 1개). `id 1`, `b_sess 1`로 gadget에 돌아오고 ACM도 다시 붙는다. 다섯 번 바꿨고 보드는 멈추지 않았다.
- 메인라인 `exynos850-e850-96.dts`(kernel.org): DWC3 아래 `usb424,9514`(LAN9514 허브 + 이더넷), USB-A 5 V는 `gpa3-5` active low, 케이블 감지는 `gpa0-0`이다.
- 이 보드의 Android 10 DTBO(`mmcblk0p5`, 개정 0/1/2 세 개 모두 같음): `regulator-usb-host-vbus`가 `gpa3-5` active low, `regulator-always-on`, 핀은 출력(`usb-pwr-ctl`)이다. DWC3는 `dr_mode = "host"`, `maximum-speed = "high-speed"`이다. 허브 노드나 허브 GPIO는 없다.
- 지금 쓰는 ERD3830 DT는 `gpa3-5`를 `i2c5-bus`로 두지만, I2C5(`13880000`)에 드라이버가 붙지 않아 핀은 비어 있다(`pinmux-pins`: UNCLAIMED).

**시험(`tools/board/scope/`: `host-spike.sh`, `host-diag.sh`, `host-loop.sh`, `gpiohold.py`, `gpiodump.py`).** 현미경(스위치 ③ NORMAL, EEPROM 부팅)을 보드4 USB-A에 꽂았다.

| 시험 | Mac USB-C | gpa3-5 | 결과 |
|---|---|---|---|
| 1 | 꽂힘 | low 90 s | 루트 포트에 장치 없음 |
| 2 | 뺌 | low 120 s | 장치 없음 |
| 3 | 뺌 | low 40 s | 장치 없음. PORTSC `Powered Not-connected Disabled Link:RxDetect` |
| 4 | 뺌 | high 40 s | 같음 |
| 5 | 뺌 | low 120 s, 그동안 가운데 USB 포트로 옮김 | 같음 |

- 현미경 불은 어느 시험에서도 켜지지 않았다(사용자 관찰).
- gpiolib은 `gpio-29 (e850-scope) out lo/hi`로 보였다.

**판단.**
- LAN9514 허브가 루트 포트에 한 번도 나타나지 않았다. USB-A 포트 전원은 LAN9514가 포트마다 켜는 구조로 보인다(데이터시트: 외부 전원 스위치를 허브가 제어). 그렇다면 현미경 불이 꺼진 것은 허브가 동작하지 않은 결과다. 가설이고, 측정하지 않았다.
- 남은 원인 후보는 셋이다. 신호선 스위치(USB-C 쪽과 허브 쪽), 허브 전원·리셋, 호스트용 PHY 설정. 회로도가 없어서 가리지 못했다.
- WinLink 하드웨어 매뉴얼에 "USB Host ports" 절이 있지만 사이트가 자동 접근을 막았다.

**이어서: 원인을 찾았다(같은 날).** 사용자가 로컬 회로도(`E850-96_Circuits_v1.0.pdf`)와 하드웨어 매뉴얼 v1.12를 줬다. 현미경을 Mac에 꽂으면 불이 켜졌다(사용자 관찰).
- 회로도에서 확인한 사실:
  - USB 신호선은 mux U6(TS3USB221A)이 고른다. micro-USB J1의 VBUS가 Q2를 거쳐 `USB_MODE_SEL`을 정한다. 케이블이 없으면 LAN9514 허브(U16)로 간다. 그래서 현미경을 쓰려면 micro-USB 케이블을 빼야 한다. 사용자가 "USB-C"·"adb"라고 부르는 포트가 이 micro-USB다.
  - 허브는 USB-A P2·P3와 HS 확장 커넥터에 연결된다. 포트 전원 스위치 U14(MIC2026)의 ENA·ENB는 허브의 PRTCTL2·3(풀업)이 켠다. `USB_PWR_CTL`은 gpa3-5이고 low가 허용이다.
  - 허브 전원 VDD_LAN은 PMIC S2MPU12의 **LDO24**다. 허브 nRESET은 RC로 풀리고 GPIO가 없다.
- 원인: ERD3830 DT는 LDO24를 SD 슬롯(dwmmc2)의 `vmmc`로 쓴다. 그래서 SD 카드가 없으면 LDO24가 꺼진 채로 있다. 이 보드의 Android 10 DTBO(rev2)에서는 LDO24가 3.3 V always-on이다.
- 해결: `modules/e850-hubpwr`(이름 `vmmc` 레귤레이터를 3.3 V로 켜 둠)를 보드4에 insmod했다. 그러자 허브, 이더넷, 현미경이 차례로 붙었다(`2-1`, `2-1.1`, `2-1.3`). 현미경 불이 켜지고(사용자 확인) `/dev/video0`이 생겼다. `v4l2-ctl` 캡처는 816×612와 1632×1224 모두 7.20 fps였다.
- 위 판단 중 "허브가 동작하지 않아 불이 꺼졌다"는 맞았다. 허브에 전원이 없었다.
- 남은 의문(측정만 함): 816×612 캡처 하나는 화소가 모두 255였다. 1632 영상은 평균 61로 Mac에서 본 약 132보다 어두웠다. 원인은 모른다.

**보드에 바꾼 것.** `e850-hubpwr.ko`를 `/var/tmp`에서 insmod했다(`/lib/modules`에는 설치하지 않았으므로 재부팅하면 사라진다). 그 밖에는 `/var/tmp` 스크립트와 `systemd-run` 일시 유닛뿐이다. `scope-loop.service`는 끝냈다. 그 뒤 쓴 임시 뷰어 `scope-live.py`(`scope-live2` 유닛)는 11.65의 뷰어로 바꾸면서 껐다.

### 11.65 현미경 웹 뷰어: 연구실 전자현미경 스타일 (2026-11-17)

**요청.** 사용자: "ui/ux를 멋지게 만들어줘 실험실에 있는 전자 현미경 처럼". 목업을 보인 뒤 사용자가 "좋아 만들어"라고 했다.

**구성(`tools/board/scope/`).**
- `scope-run.sh`(root, `systemd-run --unit=e850-scope`):
  1. `e850-hubpwr`가 없으면 insmod한다.
  2. `gpiohold.py 3 5 0`으로 USB-A 전원을 허용한다.
  3. `b_sess 0` → `id 0`으로 `a_host`로 바꾼다.
  4. `scope.py`를 돌리고, 끝나면 다시 띄운다(`/reload`).
  5. `stop` 파일이 생기면 gadget으로 돌아간다.
- `scope.py`(:8090):
  - `v4l2-ctl` YUYV 1632×1224 파이프에서 Y만 쓴다.
  - 평균 1~16장(링 버퍼), 평탄화를 처리한다. 평탄화는 빈 시야 16장으로 gain = 평균/누적을 만들어 `flat.npy`에 둔다.
  - PNG는 zlib으로 직접 만든다.
  - `/frame.png?x,y,w,h,step`: step 2는 전체를 2×2 평균한 미리보기, step 1은 보이는 영역을 원본 화소로 준다. `after=n`이면 새 프레임이 올 때까지 최대 1초 기다린다(long poll).
  - 펌웨어 vendor 요청은 usbfs로 보낸다. 0xD0은 진단, 0xD3·0xD2는 레지스터 읽기·쓰기, 0xD6은 노출이다.
  - 사진은 `/var/lib/e850-scope/photos/`에 원본 PNG, 1/8 썸네일, 조건 JSON으로 남는다. 설정은 `settings.json`에 둔다.
  - 시계와 파일 이름은 `TZ=Asia/Seoul`이다. 보드 시스템 시간대(Etc/UTC)는 그대로 두었다.
- `scope.html`:
  - 어두운 화면, 머리줄(LIVE·fps·시계·정지·촬영).
  - 휠·끌기·핀치 확대와 이동. 확대가 1.6배를 넘으면 원본 화소를 받는다.
  - 십자선, 자동 길이 축척 막대, 거리 측정(µm).
  - 오른쪽 판:
    - 노출(로그 슬라이더)·자동 노출·평균.
    - 표시용 밝기·대비·감마·반전·자동 대비(브라우저만).
    - 평탄화, 보이는 영역 히스토그램(평균·최소/최대·포화).
    - 레지스터 읽기·쓰기(쓰기는 확인 창), 진단값.
    - 촬영 목록(원본 PNG, 데이터 막대 포함 PNG를 브라우저에서 합성).
    - 화소 크기 설정.
  - 아래 데이터 막대: 노출·평균·평탄화·화소·시야·배율·커서 좌표와 값·시료 이름.
  - 단축키: Space, C, F, X, M, +/−.

**시험.**
- Mac에서 `--fake`(현미경 원본 3장 반복):
  - 모든 엔드포인트 응답. 한글 시료 이름 사진 저장, 평탄화 16장, `../` 경로는 404.
  - 헤드리스 Chrome 1600×950 화면을 확인했다. 390 px 틀에서는 문서 폭 390, 넘치는 요소 0.
  - ×6 확대에서 원본 화소 영역을 받았다. 측정선 134.2 px × 2.24 = 300.6 µm가 화면에 301 µm로 나왔다.
  - 첫 실행에서 `send()` 인자 순서 버그로 PNG 응답이 죽었고, 고쳤다.
- 보드4 `--fake`(포트 8094, 가짜 원본이 초당 약 5장): 미리보기 185 KB를 초당 3.9장, 600×400 원본 영역 107 KB를 초당 4.8장 받았다.
- 보드4 실물(micro-USB를 뺀 뒤): 칩 ID 0x4608, USB HS, MIPI 오류 0, 프레임 주기 139 ms. 화면에 시료 영상이 나왔다(헤드리스 Chrome으로 확인).
- 10초 동안 센서는 70장(7.0/s)을 냈고, 온전한 프레임은 59장, 뷰어가 받은 것은 62장(6.2/s)이었다. 같은 시각 dvslive가 CPU 141~165%를 쓰고 있었다.
  - 이 10초는 Mac에서 시험 요청을 보내던 때다. 다시 잰 값(dvslive는 계속 동작):
    - 07:42 KST 20초: 센서 148장(7.36/s), 온전 141장, 뷰어 138장(6.87/s).
    - 시작부터 약 96분 누적: 센서 41542장, 온전 40897장(98.4%), 뷰어 40018장(96.3%).
  - 손실은 웹 요청이 몰릴 때 커지는 것으로 보인다. 측정 구간이 둘뿐이라 확정하지 않았다.
- `/reload`로 새 `scope.py`가 약 2초 만에 다시 스트리밍했다(두 번).

**판단.**
- 온전하지 않은 프레임은 CX3가 입력을 잃은 프레임이다. 펌웨어는 이런 프레임을 짧게 끝낸다(`app.c` Pump). 보드의 uvcvideo는 `nodrop=0`이라 크기가 틀린 프레임을 버린다. 그래서 파이프의 프레임 경계는 어긋나지 않는다. 연속 두 원본 프레임의 줄별 평균도 같았다.
- 입력을 잃는 원인은 USB 쪽이 제때 비우지 못한 것으로 본다. 모든 장치 IRQ(xHCI, Wi-Fi)가 CPU0에 있다. 가설이고, dvslive를 끈 상태에서는 재지 않았다.

**화면 고침(같은 날, 사용자 요청: "화면 확대 축소 기능도 추가해, 마우스든 터치든 지정하는 곳에서 확대 축소, 원래 사이즈 유지 등").**
- 진단의 "줄/프레임"은 "프레임당 줄 수"로 읽혀서 이름을 바꾸고 환산값을 붙였다.
  - 줄·프레임 길이: `16000 clk / 2500줄`. LLP(0x0008)는 288 MHz 클럭 수, FLL(0x0006)은 줄 수다.
  - 줄·프레임 시간: `55.6 µs / 138.9 ms`.
  - 노출: `154줄 = 8.6 ms`(0x0074).
  - 마우스를 올리면 설명이 나온다.
- 영상 오른쪽 아래에 확대 버튼 묶음을 두었다(− 배율% ＋ 맞춤 1:1). 100%는 센서 화소 1개가 화면 점(CSS px) 1개다. 범위는 맞춤과 1:1 중 작은 쪽부터 맞춤의 32배와 1:1의 8배 중 큰 쪽까지다.
- 클릭·탭하면 그 점이 확대 기준점(청록 표시)이 된다. ＋/− 버튼과 키는 이 점을 중심으로 확대·축소한다. 점이 화면 밖이면 가운데를 쓴다. Esc로 지운다.
- 더블클릭·더블탭은 그 자리를 2배로 확대하고, Shift를 누르면 ½로 줄인다. 전에는 더블클릭이 "맞춤"이었다. 맞춤은 F·0 키와 버튼으로 옮겼다.
- 핀치는 처음 두 손가락 사이에 있던 영상 점이 손가락을 따라가게 바꿨다(확대와 이동을 같이 함). Mac 트랙패드 핀치(ctrl+wheel)는 8배 빠르게 했다.
- 창 크기가 바뀌면 맞춤 상태는 맞춤을 유지한다. 확대한 상태는 화면 배율(%)을 유지한다.
- 시험: 헤드리스 Chrome에서 가짜 포인터 이벤트로 확인했다.
  - 탭한 점은 ＋ 뒤에도 화면 (400,300)에 그대로 있었다.
  - 더블탭은 193%가 되었고, 그 자리 영상 좌표가 같았다.
  - 1:1은 100%, 핀치(간격 200→400)는 67→133%였다. 처음 중점의 영상 좌표가 새 중점과 같았다.
  - 범위는 64~2063%(dpr 1)였다.
  - 영상 가장자리에서는 영상 밖이 보이지 않게 막으므로 기준점이 밀린다(의도한 동작).
  - 실제 터치 기기와 트랙패드로는 시험하지 않았다.
- 보드4에는 `scope.html`만 복사했다(페이지는 요청마다 파일을 읽는다).

**확인하지 않은 것.**
- 실물에서의 촬영 저장, 평탄화, 자동 노출, 레지스터 쓰기, 데이터 막대 PNG.
- `/quit` 뒤 gadget 복귀.
- 화소 크기 2.24 µm(Hi-846 화소 1.12 µm × 2로 가정). 축척 막대와 측정값은 이 값에 달려 있다.

**보드4에 바꾼 것.**
- `/var/tmp/e850-scope/`(philip 소유 파일을 root가 실행한다. 개발용 지름길이다).
- `/var/lib/e850-scope/`(설정, 사진).
- 일시 유닛 `e850-scope`. 재부팅하면 사라진다.
- 유닛이 도는 동안 DWC3는 호스트 모드이고 ACM·ECM이 없다. 작업은 Wi-Fi ssh로 한다.
- 고칠 때: `scp … philip@192.168.0.244:/var/tmp/e850-scope/` 다음 `curl http://192.168.0.244:8090/reload`.
- 끄기: `/quit` 또는 `touch /var/tmp/e850-scope/stop`.
- 시작할 때는 root가 필요하다. 이번에는 micro-USB를 꽂아 ACM에서 `systemd-run`을 하고, 사용자가 케이블을 뺐다.

### 11.66 U-Net 강의 자료: 층 수 확인과 문헌 비교 (2026-11-18)

**요청(사용자).** 이번에 쓴 U-Net을 처음 배우는 학생도 읽고 바로 이해하도록 쉽게 설명하는 16:9 편집 가능 HTML 슬라이드. 층마다 설명하고, 장마다 아래에 용어 주석을 단다. 다른 모델과 비교하고 IPA로 성능과 속도를 본다. 첫 장에는 제목, "김임환 교수 · 삼성전자 DS 교수실", 유래(누가·언제·왜), 특징, IPA 요약을 넣는다. 다 되면 메일로 보낸다. 중간에 사용자가 두 가지를 바로잡았다. 첫째, 비교는 실험이 아니라 문헌으로 한다. 둘째, "28층"은 추측이니 실제 층 수를 확인한다.

**실패하고 버린 것.** 처음에는 비교 모델을 직접 학습하려 했다(`train_cmp.py`). 사용자 정정을 받고 프로세스를 끝내고 결과 디렉터리를 지웠다. 그 결과는 강의에 쓰지 않았다.

**층 수(계산, 11.62 모델 `fusenet-20261115/both/model.pt`, `train.py`의 `class UNet`).**
- Conv 층: 15. 3×3 Conv 14개와 1×1 Conv 1개다. 원 논문은 Conv만 세어 "23 convolutional layers"라고 하므로 강의도 이 기준을 쓴다.
- PyTorch 모듈: 43. Conv2d 15, BatchNorm2d 14, ReLU 14다.
- forward 연산 전체: 55. 43개에 Pad 1, MaxPool 3, Upsample 3, Concat 3, Crop 1, Sigmoid 1을 더한다. `TorchFunctionMode`로 한 번 실행하며 기록했다(`trace_ops.py`).
- 가중치가 있는 층(Conv + BatchNorm): 29.
- 어느 방식으로도 28은 나오지 않는다.
- 가중치는 1,097,449개다(Conv 1,095,337, BN 2,112). 곱셈·덧셈은 한 장에 59.9억 번이다. C07~C09가 가중치의 68%이고, C09·C11·C13이 계산의 51%다.
- 원 U-Net의 가중치를 구조대로 세면 31,030,658개다.

**문헌 비교.**
- 원 논문: Ronneberger · Fischer · Brox, MICCAI 2015.
  - ISBI 2012 warping error 0.000353으로 1위.
  - ISBI 2015 IoU는 PhC-U373 0.9203, DIC-HeLa 0.7756.
- 그 밖의 모델: FCN, SegNet, DeepLab v3+, UNet++, Attention U-Net, 3D U-Net, V-Net, nnU-Net, TransUNet, Swin-Unet, UNeXt, SAM, DDPM, Stable Diffusion. 각 논문의 값만 썼다.
- IPA(Martilla & James 1977)는 축을 바꿔 썼다.
  - 가로축은 속도(1000/ms, log)다.
  - 세로축은 ISIC 2018과 BUSI IoU의 평균이다.
  - 여섯 모델의 값은 모두 UNeXt 논문(Valanarasu & Patel, MICCAI 2022) Table 1에서 가져왔다. 256×256 입력, Xeon Gold 6140 CPU에서 잰 값이다.
  - 가운데 선은 속도의 기하평균 5.06장/초, 성능 평균 71.5%다.
  - "빠르고 정확함" 칸에는 UNeXt만 있다. 원조 U-Net은 "느리고 덜 정확함" 칸에 있다.
- 이 강의의 U-Net은 성능축에 넣지 않았다. 과제(두 보드 합성)가 달라서 ISIC·BUSI의 IoU와 비교할 수 없다.

**만든 것.**
- `docs/UNET-LECTURE-20261118.html`: 58장, 약 980 KB, 그림을 안에 넣음.
- 구성: 표지와 요약, 목차, 01 유래(9장), 02 비교·IPA(6장), 03 기초 개념(13장), 04 구조(6장), 05 층별 C01~C15(15장), 06 학습(5장), 정리, 참고문헌 18개.
- 그림과 출처:
  - 층별 그림은 학습된 모델에 시험 그림 한 장을 넣어 뽑은 중간 출력이다. 표준편차가 큰 channel 4개를 골랐다. 새 실험은 아니다.
  - 학습 곡선은 11.62의 `train.log`에서, learning rate는 OneCycle 설정에서 다시 그렸다.
- 슬라이드마다 꼬리표를 달았다. 문헌, 계산(구조·가중치에서 셈), 측정(11.62 기록), 가설이다.
- 원본과 스크립트: `~/e850-logs/dvs3d/unet-lecture-20261118/src/`
  - `layers.py`, `trace_ops.py`, `lsvg.py`, `slides.html`, `build.py`
  - `check.py`: 1280×720 화면 넘침, 안쪽 넘침, 깨진 그림, 폰 폭을 보고 PDF를 만든다.
  - `ui_test.py`
- 결과물: 같은 디렉터리의 `UNET-LECTURE-20261118.pdf`(58쪽, 960×540 pt, 5.2 MB)와 `.html`.

**확인.**
- `check.py`: 58장 모두 넘침 없음, 깨진 그림 0, 폰 폭 390.
- `ui_test.py`: ALL PASS(발표 시작·앞뒤 이동·끝·Esc로 편집·HTML 저장).
- 58장 화면을 모두 눈으로 봤다. 고친 것:
  - 시그모이드 식의 윗첨자가 떨어져 보였다. `.formula`가 flex라서 `<sup>`이 따로 놓였기 때문이다. span으로 감싸 고쳤다.
  - 연표 이름표가 겹쳤다.
  - 원 U-Net 그림과 sliding window 그림의 글자가 작았다.
  - 20쪽과 26쪽에서 내용이 넘쳤다.

**메일.** 2026-11-18 07:01:38에 Mail.app으로 `philip.de.kim@yonsei.ac.kr`에 보냈다. 첨부는 PDF와 HTML 2개다. yonsei 계정의 보낸 메일에서 제목과 첨부 2개를 확인했다.

**보드 상태(관찰, 2026-11-18 07:00).**
- 보드1(.236)과 보드2(.168)는 ping에 응답한다.
- 보드3(.234)과 보드4(.244)는 ping과 ssh(6초) 모두 응답이 없고, ARP에도 없다.
- Mac에 ACM과 fastboot 장치가 없다. UART는 `A5069RR4`(보드3) 하나만 꽂혀 있다. 그 로그(`uart-board3.log`)는 11-15 13:52 이후 새 줄이 없다.
- 보드4는 11.64~11.65 현미경 시험 보드다. 원인은 보지 않았다.

**보드에 바꾼 것.** 없음.

### 11.67 50M 현미경 키트(Hi-5031Q)용 뷰어, 보드4·보드1 파일시스템 확장 (2026-11-19)

**요청(사용자).** 8M(Hi-846) 현미경과 같은 UI/UX로, 센서만 50M CIS로 바꾼 두 번째 SOL 키트를 만든다. 키트의 옛 순정 펌웨어를 새 UVC 펌웨어로 바꾼다.

**펌웨어(비공개 저장소 `sol-cx3-uvc`, LOG 8~10).**
- 센서는 SK hynix Hi-5031Q다(chip ID 0x5031).
- 새 펌웨어는 흑백 YUY2 UVC를 낸다. 프레임 1은 2016×1536, 프레임 2는 1008×768이고 4.37 fps다. Mac USB 3에서 40프레임이 모두 온전했다.
- 셋파일, 데이터시트, PLL·MIPI 설정은 기밀이라 이 저장소에 넣지 않는다.
- 이미지는 129,792 B로 EEPROM(128 KiB)에 들어간다. 사용자 승인을 받아 키트2 EEPROM에 썼고, 다시 읽은 값이 일치했다(LOG 10). EEPROM 부팅은 아직 확인하지 않았다.

**뷰어 수정(`tools/board/scope/scope.py`, `scope.html`).**
- 프레임 크기는 고정값 1632×1224 대신 카메라에서 읽는다(`v4l2-ctl --list-formats-ext`에서 가장 큰 YUYV).
- 센서는 펌웨어 diag의 chipId로 고른다(`SENSORS`: 0x4608 Hi-846, 0x5031 Hi-5031Q).
- 센서마다 다른 것: 줄 길이 클록(MHz), 진단에 보이는 레지스터 이름, 레지스터 목록, 기본 노출(Hi-846 15 ms, Hi-5031Q 200 ms).
- 키트가 바뀌면(설정의 `chip`이 다르면) 그 센서의 기본 노출로 시작한다.
- 크기가 바뀌면:
  - 프레임 평균을 다시 시작한다.
  - 크기가 다른 평탄화 기준은 쓰지 않는다. 사용자가 다시 찍어야 한다.
  - 페이지는 `/stat`의 `size`, `sensor`를 받아 측정점을 지우고 화면을 다시 맞춘다.
- 촬영 JSON에 `sensor`가 추가됐다. `width`, `height`는 실제 영상 크기다.
- `--fake`에 `--size`, `--chip`을 더했다(키트 없이 시험할 때).

**확인(관찰).**
- Mac 가짜 모드, Hi-5031Q 키트의 실제 2016×1536 프레임:
  - `/stat`의 `size`는 [2016, 1536], 센서는 Hi-5031Q였다.
  - 끝을 넘는 영역 요청이 영상 안으로 잘렸다.
  - 촬영 JSON은 width 2016, sensor Hi-5031Q였다.
  - headless Chrome 화면 캡처에서 시야 4.52 × 3.44 mm, Hi-5031Q 레지스터 목록이 보였다.
- Mac 가짜 모드, Hi-846(1632×1224 합성 프레임): [1632, 1224], Hi-846, 288 MHz. 전과 같다.
- 보드4 가짜 모드(포트 8093, 따로 만든 시험 폴더):
  - `/stat`는 [2016, 1536], Hi-5031Q, 4.81 fps였다.
  - `/frame.png`는 1008×768 PNG(243 KB)를 0.15초에 받았다.
  - 촬영 저장: PNG 1.2 MB와 JSON(width 2016, sensor Hi-5031Q)이 생겼다.
  - 시험 폴더는 지웠다.
- **실제 키트를 E850에 꽂아 본 적은 없다.** Hi-5031Q 키트는 지금 Mac USB에 있다.

**보드4 디스크가 가득 참(관찰).**
- 보드4 `/`는 2.9 GB 중 100%를 썼고 남은 공간이 0이었다. 시험 폴더 `mkdir`가 `No space left on device`로 실패했다.
- 사진 폴더 `/var/lib/e850-scope/photos`는 비어 있었다(0장).
- 큰 것은 `/var/tmp` 1.5 GB(`dvsrec` 1.1 GB, `plltest` 198 MB)와 `/var/log/journal` 588 MB다. 둘 다 보드1에서 복제해 온 것으로 보인다(`dvsrec`의 마지막 파일은 11-08이다). journal에는 보드1의 machine-id 디렉터리가 남아 있다. 마지막을 무엇이 채웠는지는 보지 않았다.
- 원인: 파티션 `mmcblk0p24`는 57,950,552,064 B(54 GiB)인데, ext4는 3 GiB(786,432 블록)로 만들어진 채였다. `build-rootfs.sh`(`SIZE=3G`)와 `build-clone.sh`(`BLOCKS=786432`)가 만든 크기 그대로다. 보드1도 같아서 2.9 GB 중 91%였다([BOARD-CLONE.md](BOARD-CLONE.md) 10장에 적어 둔 것).
- 사용자가 사진을 지우라고 했다. 지울 사진이 없었고, 지워도 해결되지 않는 문제라서 아무것도 지우지 않았다.

**파일시스템 확장(사용자 승인, 사용자가 sudo로 실행).**
- 보드4와 보드1에서 `sudo resize2fs /dev/mmcblk0p24`를 했다. 마운트된 채로 했고, 재부팅하거나 서비스를 멈추지 않았다.
- dmesg에는 두 보드 모두 `resizing filesystem from 786432 to 14148084 blocks`, `resized filesystem to 14148084`가 나왔다. EXT4 오류·경고는 없었다.
- 결과:

  | 보드 | 전 | 후 |
  |---|---|---|
  | 보드4 (.244) | 2.9 GB, 100%, 남은 공간 0 | 54 GB, 6%, 49 GB 남음 |
  | 보드1 (.236) | 2.9 GB, 91%, 256 MB 남음 | 54 GB, 5%, 49 GB 남음 |

- 보드2(.168)는 늘리지 않았다. 보드3은 네트워크에 없었다.

**보드에 바꾼 것.**
- 보드4·보드1: ext4를 파티션 크기까지 늘렸다.
- 보드4: `/var/tmp/e850-scope/scope.py`, `scope.html`을 새 판으로 바꿨다. 옛 판은 같은 곳의 `*.bak-1116`이다. `e850-scope`는 꺼져 있다. 다음에 켤 때 새 판이 돈다.

**남은 것.**
- Hi-5031Q 키트를 보드4에 꽂아 실제로 보기. USB 2.0 HS에서도 되는지는 모른다.
- 스위치 ③을 NORMAL로 해서 EEPROM 부팅 확인.
- Hi-5031Q의 화소 크기(뷰어 기본 2.24 µm는 Hi-846 기준이다).
- 보드2 파일시스템 확장.

### 11.68 현미경 LED 켜기·끄기 (2026-11-19)

**요청(사용자).** 현미경 보드에 붙은 LED 보드를 켜고 끄는 기능을 넣는다.

**찾은 것(측정, 자세한 내용은 비공개 `sol-cx3-uvc` LOG 11).**
- LED 칩은 I2C 0x9C의 8채널 LED 구동 칩이다. 레지스터 배치와 기본값으로 보아 TI TLC59108로 본다.
- 채널을 하나씩 255로 켜 보았다. 영상이 밝아진 것은 **OUT3(PWM 레지스터 0x05)** 뿐이었다(200 ms에서 111.7 → 255).
- 순정 펌웨어와 이전 판은 0x03(OUT1)에 썼다. 그래서 LED는 지금까지 늘 꺼져 있었고, 11.64~11.67의 영상은 방 조명으로 찍은 것이다.
- LED 255에서 평균은 5 ms 61.7, 20 ms 195.6이다. 꺼져 있으면 200 ms에 110이다.

**바꾼 것.**
- 펌웨어(`sol-cx3-uvc`): LED를 OUT3에 쓰고, 처음에는 꺼짐(0)으로 시작한다. 키트2 RAM에서 켜기·끄기를 확인했다. 사용자 승인을 받아 키트2 EEPROM에 이 판을 다시 썼고, 다시 읽은 값이 일치했다.
- 뷰어:
  - `scope.py`: 설정 `led`(0~255)와 `/set?led=N`을 넣었다. 스트림을 시작할 때 저장된 값을 다시 보낸다. 촬영 JSON에 `led`가 들어간다.
  - `scope.html`: "조명" 줄에 "LED 켜기/끄기" 버튼을 넣었다. 켜면 255다. 켜거나 끈 뒤 3프레임을 기다렸다가 자동 노출을 한 번 돌린다. 방 조명에 맞춘 노출로는 LED를 켜면 포화하기 때문이다.
  - 아래 정보 막대, 사진 정보, 사진 PNG의 데이터 막대에 LED 켬/끔이 나온다. 예전 사진에는 나오지 않는다.

**확인(관찰).**
- Mac 가짜 모드(포트 8094):
  - `/set?led=255`이면 설정 255, `led=999`이면 255로 잘리고, `led=x`는 무시됐다.
  - 촬영 JSON에 `led` 255가 들어갔다.
  - headless Chrome 화면에서 "LED 끄기"(켜진 상태)와 정보 막대의 "LED 켬"이 보였다.
- 버튼을 눌렀을 때 자동 노출이 따라오는지는 실물로 보지 않았다.

**보드에 바꾼 것.** 보드4 `/var/tmp/e850-scope/scope.py`, `scope.html`을 LED 판으로 바꿨다(sha256 `7403da7a…`, `eb5d124c…`).

**실물 확인(2026-11-19 04:32~, 보드4, 50M 키트를 USB-A에 꽂음, 사용자가 sudo로 `e850-scope`를 띄움).**
- 스트림: 2016×1536 Hi-5031Q, USB 2.0(HS), 4.39 fps. 처음 49프레임은 모두 온전했고 MIPI 오류 0, DMA 리셋 0이었다. 50M 키트가 E850 USB 2.0에서도 된다.
- `/set`으로 켜고 끈 밝기: LED 끔·200 ms 106.6, LED 켬·200 ms 255, LED 켬·10 ms 108.1.
- 버튼(headless Chrome에서 CDP로 클릭):
  - LED를 끄면 자동 노출이 약 3~5초 만에 227~244 ms, 평균 119~128로 맞췄다.
  - LED를 켜면 11.6 ms, 평균 123으로 맞췄다.
  - 같은 시간에 사용자도 자기 브라우저에서 버튼을 눌렀다(내가 누르지 않은 켜기가 기록에 있다).
- LED를 켜자 영상에 촘촘한 점무늬, 검은 알갱이, 가는 선이 보였다. 방 조명에서는 고른 회색이었다. 무엇인지(시료, 센서 덮개 위 먼지 등)는 모른다.

**프레임 손실: 보드가 Wi-Fi로 많이 보낼 때(관찰).** 펌웨어 diag의 frames와 goodFrames를 20초씩 비교했다.

| 조건 | 온전한 프레임 | 잘린 프레임 |
|---|---|---|
| 페이지 1개, LED 켬·11.6 ms(무늬 많음, 미리보기 PNG 670 kB) | 48%, 53% | 7, 4 |
| 페이지 1개, LED 끔·11.6 ms(거의 검정) | 98% | 1 |
| 페이지 1개, LED 끔·200 ms(고른 회색, PNG 약 240 kB) | 80% | 7 |
| 페이지 없음, LED 켬·11.6 ms | 100%, 100% | 0 |
| 보드 안 localhost로 같은 PNG를 프레임마다 받음 | 100% | 0 |
| 페이지 없음, ssh로 60 MB를 Mac에 보냄(약 3 MB/s 이상) | 66% | 5 |

- 해석:
  - LED 전력, 노출, PNG 압축은 원인이 아니다.
  - 보드가 Wi-Fi로 내보내는 양이 많을수록 CX3가 프레임을 잘라 버린다. USB 호스트가 키트의 데이터를 제때 받아 가지 못한다는 뜻이다.
  - 잘린 프레임은 uvcvideo가 버린다(뷰어 n과 goodFrames가 같다). 그래서 사진이 깨지지는 않고, 프레임 수만 준다.
- IRQ: `xhci-hcd:usb2`(114)와 `scsc_wlbt`(123) 모두 CPU0에서 처리된다(affinity 0-7, effective 0). wlan0의 XPS/RPS는 꺼져 있다(00).
- CPU0 사용률은 34%(IRQ 4.8%, softirq 13.8%)로 포화가 아니었다.
- 가설(확인 안 함): Wi-Fi 송신 IRQ와 softirq가 CPU0에서 수 ms씩 USB 완료 처리를 늦춘다.
  - 호스트의 bulk URB 5개(HS에서 최대 각 16 kB)와 CX3 출력 버퍼 4 × 12 kB를 합쳐도 27 MB/s에서 수 ms분밖에 안 된다.
  - 11.64의 Hi-846(816×612 미리보기)에서 96.3%였던 것도 미리보기가 작았기 때문으로 본다.

**Wi-Fi IRQ를 CPU7로(사용자가 sudo로 실행, 재부팅하면 원래대로).** `echo 7 > /proc/irq/123/smp_affinity_list`를 했다. effective는 7이 됐고, `scsc_wlbt` 인터럽트가 CPU7에서 세어졌다(3초에 596번). Wi-Fi와 ssh는 그대로 됐다. 같은 시험을 다시 했다.

| 조건 | CPU0일 때 | CPU7일 때 |
|---|---|---|
| 페이지 1개, LED 켬·11.6 ms | 48%, 53% | 96%, 98%(잘린 프레임 1, 0) |
| ssh로 60 MB 보냄 | 66%(페이지 없이) | 93%(페이지도 열린 채, 잘린 프레임 0) |

- 가설(Wi-Fi 송신 처리가 CPU0에서 USB 완료 처리를 늦춘다)과 맞는다. 다만 원인을 직접 잰 것은 아니다.
- CPU7은 dvslive의 웹 전용 CPU다(11.30). 그 웹 지연이 바뀌었는지는 재지 않았다.
- Wi-Fi 펌웨어가 패닉에서 복구될 때(11.31) 이 설정이 남는지는 보지 않았다.

**보드에 바꾼 것.** 보드4 `/var/tmp/e850-scope/scope.py`, `scope.html`을 LED 판으로 바꿨다(sha256 `7403da7a…`, `eb5d124c…`). 보드4 Wi-Fi IRQ를 CPU7로 옮겼다(재부팅하면 사라짐). 시험용으로 `/var/tmp/localpoll.py`를 넣었다. `e850-scope`는 사용자가 띄웠고 지금 돈다. LED는 켜짐(255, 11.6 ms)이다.

**남은 것.**
- EEPROM 부팅(스위치 ③ NORMAL) 확인.
- Hi-846 키트의 LED도 OUT3인지 확인.
- Wi-Fi IRQ를 CPU7로 옮기는 설정을 부팅 때 적용할지(`e850-wifi.sh`) 결정.

### 11.69 부팅 때 현미경 자동 시작(보드4), Wi-Fi IRQ를 CPU7로 고정(보드1·2·4) (2026-11-19)

**사용자 결정.**
- Wi-Fi IRQ를 CPU7로 옮기는 설정을 보드1·2·4 모두 부팅 때 적용한다(11.68의 시험 결과를 보고 정함).
- 보드4는 부팅하면 현미경도 자동으로 동작하게 한다.
- 모두 커밋하고 push한다.

**바꾼 것(저장소).**
- `tools/linux510/e850-wifi.sh`: `ip link set wlan0 up` 다음에 `scsc_wlbt` IRQ를 모두 CPU7로 옮긴다(`/proc/irq/N/smp_affinity_list`).
  - IRQ는 셋이다(123, 125, 126). 데이터가 오가는 것은 123이다. 125는 1번, 126은 0번 울렸다.
- `tools/board/scope/scope-run.sh`: 프로그램 디렉터리 `D`(`SCOPE_DIR`)와 실행 디렉터리 `R`(`SCOPE_RUN`: 로그, stop·reload 파일)을 나눴다. 기본값은 둘 다 전과 같은 `/var/tmp/e850-scope`다.
- `tools/board/scope/e850-scope.service`(새 파일):
  - 실행 위치: `/opt/e850-scope`(root 소유)
  - 실행 디렉터리: `/var/lib/e850-scope`
  - 순서: `After=e850-wifi.service`, `WantedBy=multi-user.target`
- `tools/board/scope/install.sh`(새 파일, root):
  - 같은 이름의 유닛을 멈추고 `reset-failed`한다.
  - `/opt/e850-scope`에 다섯 파일(`scope.py`, `scope.html`, `scope-run.sh`, `gpiohold.py`, `e850-hubpwr.ko`)을 `install`로 넣는다.
  - 서비스를 넣고 `enable --now`한다.
  - 되돌리기: `systemctl disable --now e850-scope`.

**설치(사용자가 sudo로 실행).**
- 세 보드: `/var/tmp/apply-wifi-irq.sh`를 실행했다. 새 `e850-wifi.sh`를 `/usr/local/bin`에 넣고, IRQ 3개를 지금 바로 CPU7로 옮긴다. Wi-Fi는 다시 시작하지 않는다. 세 보드 모두 `irq 123/125/126 -> CPU7`이 출력됐고, 확인한 값도 같았다.
- 보드4: `install.sh`.

**실패하고 고친 것.**
- 첫 `install.sh`에서 옛 임시 유닛을 멈추는 데 90초가 걸렸고, 결국 강제 종료됐다.
  - 원인: 내가 실행 중이던 `/var/tmp/e850-scope/scope-run.sh`를 scp로 같은 파일 위에 덮어썼다. 셸은 스크립트를 실행하면서 파일을 조금씩 읽는다. 그래서 SIGTERM으로 루프를 빠져나온 뒤 새 파일의 엉뚱한 위치를 읽었고, `scope.py`를 다시 띄우고 기다렸다(로그의 `Terminated` 다음 `http on 8090`).
  - 그 결과 실패한 임시 유닛이 남았다. 이것이 같은 이름의 새 파일을 가려서 `Failed to enable unit: Unit /run/systemd/transient/e850-scope.service is transient or generated`가 났다.
  - `install.sh`에 `reset-failed`를 넣어 다시 실행하니 됐다.
  - 앞으로 `/opt`의 파일은 `install`이 새 파일로 바꾸므로, 실행 중인 셸이 이 문제를 겪지 않는다.
  - 강제 종료 때 정리 코드(gadget 되돌리기)는 돌지 않았다. 새 서비스가 곧 다시 호스트 모드로 바꿨다.

**확인(관찰).**
- 보드4:
  - `e850-scope` enabled, active, `FragmentPath=/etc/systemd/system/e850-scope.service`
  - 로그에 `state a_host`, `streaming … 2016x1536 Hi-5031Q`
  - 4.4 fps, 20초에 88개 중 86개 온전(98%), 잘린 프레임 0(LED 켬, 10.8 ms, 사용자 페이지가 열려 있었을 수 있음)
- dvslive 웹 응답(Mac에서 Wi-Fi, 연결 재사용, 중앙값/p90, 한 번씩):

  | 보드 | 요청 | IRQ CPU0 | IRQ CPU7 |
  |---|---|---|---|
  | 보드1 | `/stats` ×100 | 12.3 / 30.9 ms | 14.5 / 45.2 ms |
  | 보드1 | `/frame.png` ×50 | 54.3 / 74.5 ms(27 kB) | 64.7 / 96.9 ms(29 kB) |
  | 보드2 | `/stats` ×100 | 8.7 / 11.8 ms | 9.7 / 16.6 ms |
  | 보드2 | `/frame.png` ×50 | 27.4 / 32.0 ms(13 kB) | 31.7 / 34.2 ms(29 kB) |

  - 조금 늘었다. 한 번씩만 재서 Wi-Fi 변동 범위 안인지는 모른다.
  - `/frame.png`는 화면 내용에 따라 크기가 달라서 그대로 비교할 수 없다.
  - CPU7에는 dvslive 웹 스레드(`dvs-web`)와 Wi-Fi IRQ가 함께 있다.

**아직 확인하지 않은 것.**
- 재부팅 뒤 자동 시작. 서비스는 enabled이지만 보드4를 재부팅하지 않았다.
- 재부팅 뒤 `e850-wifi.sh`가 IRQ를 옮기는지(부팅 때 IRQ가 이미 있는지).
- Wi-Fi 펌웨어 패닉에서 복구될 때 IRQ 설정이 남는지.

**보드에 바꾼 것.**
- 보드1·2·4: `/usr/local/bin/e850-wifi.sh`를 새 판으로 바꿨다. `scsc_wlbt` IRQ 3개를 CPU7로 옮겼다.
- 보드4:
  - `/opt/e850-scope`(5개 파일)와 `/etc/systemd/system/e850-scope.service`를 넣고 enable했다.
  - 부팅할 때마다 USB가 호스트 모드가 되므로 micro-USB ACM은 평소에 없다. `curl http://192.168.0.244:8090/quit`로 끄면 다음 부팅까지 돌아온다.
  - `/var/tmp`에 `apply-wifi-irq.sh`, `e850-wifi.sh`, `localpoll.py`가 남아 있다.

### 11.70 현미경 자동 촬영 · 물체 개수/크기 분석 · 기록(DB) · 추세 그래프 (2026-11-19)

**사용자 요청과 결정.**
- 사진을 저장하고 DB를 자동으로 관리한다. 날짜·시간, 보이는 물체의 개수, 개수와 크기의 변화를 기록하고, 사진과 함께 추세 그래프를 그린다.
- 일반 제품 형태로 만들고, 조건은 체크박스로 고른다.
- OpenCV로 한다. YOLO는 지금 쓰지 않는다. YOLO는 물체 종류를 구별할 때 필요하고, 그러려면 표시한 사진 수백 장이 있어야 한다. 또 640 px로 줄여서 보므로 작은 물체가 사라진다.
- 설치 방식은 1번(사용자 선택)이다. `opencv-python-headless` wheel 하나를 `/opt/e850-scope/py`에 푼다. Debian `python3-opencv`는 모의 설치에서 패키지 344개가 따라와서 고르지 않았다.
- 설계 문서: `docs/superpowers/specs/2026-11-19-scope-timelapse-analysis-design.md`. 계획: `docs/superpowers/plans/2026-11-19-scope-timelapse-analysis.md`.

**OpenCV(관찰).**
- `opencv_python_headless-4.11.0.86-cp37-abi3-manylinux_2_17_aarch64.whl`(29.5 MB)을 Mac에서 받았다. sha256은 PyPI 값 `6efabcaa…99eb`와 같다.
- 보드4 Debian numpy 1.24.2와 같이 import된다(0.54 s). 4.12는 numpy 2가 필요해서 쓰지 않는다. 풀면 74 MB다.
- 크기가 큰 가우시안 흐림(2016×1536, σ 40)은 6.25 s가 걸렸다. 그래서 배경은 1/8 영상에서 구한다.

**바꾼 것(저장소).**
- `tools/board/scope/scope_rec.py`(새 파일): 분석, 추적, SQLite 기록, 윤곽 JPEG, CSV, 자체 시험(`--selftest`).
  - 분석 순서:
    1. 배경: 1/8 영상의 중앙값. 창은 최대 물체의 2배다. 테두리는 점대칭(2×가장자리 − 거울값)으로 덧댄다.
    2. 흐림: σ = 최소 지름/4. 최소 크기보다 작은 시료 무늬는 물체로 세지 않는다.
    3. 대비: (배경 − 영상)/√배경. 밝은 물체는 부호가 반대다.
    4. 임계값: 중앙값 + k·MAD. k는 민감도 1~5에 따라 5/4/3/2.5/2다.
    5. 경계: 물체마다 대비 최댓값의 절반에서 자른다.
    6. 열림·닫힘, 구멍 메우기.
    7. (선택) 붙은 물체 나누기: 거리 지도에서 최댓값의 0.7 넘는 core가 둘 이상이면, 화소를 가장 가까운 core에 나누고 만나는 곳을 2 px 자른다.
    8. 등가 지름이 [최소, 최대] µm에 드는 것만 센다. 기록은 큰 것부터 5000개까지 하고, 개수는 전부 센다.
  - 추적: 앞 사진의 물체와 중심이 가까운 순서로 1:1 짝짓는다. 거리 한도는 지름과 10 px 가운데 큰 쪽이다.
  - DB `/var/lib/e850-scope/scope.db`(WAL), 표 3개:
    - `runs`: 자동 촬영 한 번
    - `photos`: 시각, 노출, LED, 화소 크기, 개수, 평균 지름, 총면적, 분석 조건
    - `objects`: 위치, 지름, 면적, track
  - OpenCV가 없으면 분석만 꺼지고, 기록과 뷰어는 동작한다.
- `tools/board/scope/scope.py`:
  - 촬영할 때마다(수동 포함) 분석해서 DB에 넣는다. 윤곽 사진은 `.ov.jpg`로 둔다.
  - 자동 촬영 스레드는 `tl_start + k×간격` 칸에 맞춰 찍는다. 놓친 칸은 건너뛴다.
  - 한 장을 찍는 순서:
    1. (LED 켜기) LED 255
    2. (자동 노출) raw 프레임 평균을 118로 맞춘다. 페이지의 규칙과 같다.
    3. 새 raw 프레임 1장 또는 4장을 평균한다.
    4. (평탄화) 기준 영상 이득을 곱한다.
    5. 저장하고 분석한다.
    6. LED와 노출을 찍기 전 값으로 되돌린다.
  - 보관 한도를 넘거나 남은 공간이 2 GB 아래면 자동 촬영을 멈춘다. 지우지는 않는다.
  - 기간이 끝나면 run을 닫는다. 서비스가 다시 시작되면 같은 run을 잇는다.
  - HTTP:
    - `/rec/runs`, `/rec/photos?run=`, `/rec/objects?photo=`, `/rec/tracks?run=`
    - `/rec/csv?run=&what=photos|objects`: UTF-8 BOM 포함(엑셀에서 한글이 깨지지 않음)
    - `/analyze/preview`
  - 요청 처리 중 예외는 모두 400 응답으로 돌려준다.
- `tools/board/scope/scope.html`:
  - 옆 칸 "자동 촬영 · 분석" 묶음:
    - 자동 촬영: 시작/멈춤, 간격 10초~6시간, 기간 1시간~30일 또는 멈출 때까지
    - 촬영 전: LED, 자동 노출, 평탄화, 4장 평균
    - 분석: 대상(어두운/밝은), 크기 µm, 민감도, 붙은 물체 나누기, 개별 물체 추적
    - 기록: 원본 PNG, 윤곽 사진, 보관 GB
    - "분석 미리보기"
  - 머리줄에 `REC N` 표시와 "기록" 버튼(R 키)을 넣었다.
  - 기록 패널:
    - 기록 선택
    - 계열 체크박스: 개수, 평균 지름, 총면적, 개별 물체 크기, 크기 분포, 표
    - 측정값마다 그래프 하나(시간축 공통, 축 하나)
    - 마우스를 올리면 세로선과 툴팁. 누르거나 ←/→ 키로 그 사진과 값, 크기 분포를 본다.
    - CSV 2종
  - 그래프 색은 dataviz 검증기로 이 페이지 표면(#10161d, 어두움)에서 확인했다. 8색 모두 통과했다. 강조색 청록(#3fd0e0)은 밝기 범위를 넘어 그래프 선에 쓰지 않았다.
- `tools/board/scope/install.sh`: `scope_rec.py`도 복사한다. `py/`가 있으면 `/opt/e850-scope/py`로 바꿔 넣는다.

**실패하고 고친 것(합성 영상과 실물 영상).**
- 배경을 넓은 중앙값으로 구하고, 뒤에 가우시안을 하고, 비율 대비를 쓴 첫 판: 원판 12개가 157개로 나왔다.
  - 원인 1: 흐림이 테두리를 반사로 채워서 가장 어두운 모서리 배경을 약 10 grey 올렸다.
  - 원인 2: 모서리의 상대 잡음이 3배여서 전체에 하나로 정한 기준을 넘었다.
- 배경을 닫힘 연산으로 바꿨다:
  - 처음에는 OpenCV가 팽창과 침식에서 테두리를 따로 복제해서, 모서리 배경이 60에서 100으로 밀렸다. 직접 덧대서 고쳤다.
  - 합성 영상에서는 맞았지만, 실물 시료(질감 있음, 중앙값 126)에서는 배경을 질감의 밝은 윗선 195로 잡았다. 그래서 검은 입자를 하나도 못 찾았다.
  - 다시 중앙값으로 돌아가고, 덧대기를 점대칭으로 했다(복제 덧대기는 모서리를 14 grey 올렸다).
- OpenCV watershed는 이웃 차이로 퍼진다. 평평한 거리 지도에서는 붙은 원판 507 px를 303 + 37로 나눴다. 가장 가까운 core로 나누는 방식으로 바꿨다(245 + 244).
- 미리 흐리게 한 뒤 잡음 기준에서 자르면 물체가 부푼다(249 px → 505 px). 크기는 물체마다 최댓값의 절반에서 잰다(249 px).
- 실물 영상의 검은 입자(20~40 µm)는 대비가 질감 잡음의 3.4~5.9배밖에 안 된다. k = 5로는 거의 못 찾아서, 민감도 선택을 넣었다. 기본값은 3(k = 3)이다. Otsu는 질감에서 기준을 예측하기 어렵게 올려서 뺐다.

**확인(관찰).**
- `python3 scope_rec.py --selftest`(보드4): `selftest ok (analysis 0.42 s for 2016x1536)`. 확인한 것:
  - 원판 12개 수(나누기 끄면 11개)
  - 40 µm 원판 지름 ±15 %
  - 20 µm 이상 7개, 밝은 물체 12개
  - 빈 영상 0개, 잡음 3배에서 기본 민감도 ≤1개, k = 5에서 0개
  - 6000개 → 5000개 기록과 truncated
  - 추적 ID 유지와 새 물체 13번
  - CSV 줄 수, 수동 촬영 run
- 실물 프레임(Hi-5031Q, LED 켬, 2016×1536)을 어두운 물체, 20 µm 이상, 민감도 3으로 분석: 8개를 찾았다. 윤곽 사진에서 모두 눈에 보이는 검은 입자였고, 가짜는 보이지 않았다. 한 장 0.3 s.
- 보드4 `--fake`(실물 프레임 2장, 포트 8091, 데이터 `/var/tmp/scope-dev`):
  - 간격 10초로 15장을 찍었다. 칸 시각(:20, :30, :40 …)에 맞았다.
  - 한 장에 9개였고, 추적 8개가 이어졌다. 찍은 뒤 LED와 노출이 되돌아왔다(0, 150).
  - fake에서는 노출이 실제로 바뀌지 않아서 자동 노출이 8번 다 돌고, 한 장에 약 6초가 걸렸다.
- 헤드리스 Chrome(CDP)에서 JS 오류 없음:
  - 옆 칸 값 표시, 기록 패널, 툴팁, 추적과 표, 미리보기
  - 폰 폭 390 px에서 가로 넘침 없음
  - 처음에는 기록 패널의 `drawHist`가 실시간 히스토그램 함수와 이름이 같아서 덮어썼다. 메인 히스토그램이 비었다. 이름을 바꿨다.

**아직 확인하지 않은 것.**
- 서비스 설치 뒤 실물 키트로 하는 자동 촬영(자동 노출이 실제로 맞는지, LED 켜고 끄기, 시간).
- 며칠짜리 실행, 보관 한도 멈춤, 서비스 재시작 뒤 잇기는 코드로만 넣었다. 실제로 시험하지 않았다.
- 화소 크기 2.24 µm(크기 µm 값이 여기에 달려 있다).

### 11.71 현미경 뷰어 화면 재구성: 실시간 · 실험 · 결과 (2026-11-19)

**요청.** "ui/ux가 직관적이지 못 해 불편해, 상용제품처럼 멋지게 만들어줘 시장조사좀 해서".

**시장조사.** 다음 제품을 조사했다: CytoSMART Omni/Lux3, Incucyte, CELLCYTE X, EVOS, Countess 3, ZEISS Labscope. 공통점은 다섯 가지였다.
- 실험 단위의 단계별 흐름
- 이름 붙은 분석 모듈
- 분포 위에서 정하는 크기 조건
- 그래프와 시점별 사진
- 원격 확인

설계 문서는 `docs/superpowers/specs/2026-11-19-scope-ui-redesign-design.md`, 계획은 `docs/superpowers/plans/2026-11-19-scope-ui-redesign.md`이다.

**정한 것(사용자 승인).**
- 주 사용: 연구자가 PC 브라우저에서 쓰고, 폰은 진행 확인용이다.
- 방식: `scope.html`을 그 자리에서 재구성한다. 영상 엔진과 그래프 코드는 그대로 옮겼다.

**바꾼 것(코드).**
- `scope.html` 전체를 다시 썼다.
  - 머리줄 탭 [실시간] [실험] [결과]와 ⚙ 고급 서랍을 두었다. 폰에서는 탭이 아래 막대로 간다.
  - 실시간:
    - 카드: 조명·노출, 물체 표시, 사진, 표시 보정(접힘), 거리 재기
    - "물체 표시"를 켜면 약 1.5초마다 지금 프레임을 분석한다. 물체를 등가 지름보다 조금 큰 원으로 그린다.
    - 실험 중에는 프리셋을 바꿀 수 없다(진행 중인 실험의 조건이 바뀌므로).
  - 실험:
    - 4단계 마법사: 시료 → 무엇을 셀까 → 일정·기록 → 확인·시작
    - 2단계 구성:
      - 프리셋(입자·먼지, 세포, 콜로니, 직접)
      - 큰 미리보기
      - 로그 축 크기 분포와 최소·최대 손잡이(놓으면 다시 센다)
      - 최소 크기 비교 그림 6장(5, 10, 20, 50, 100, 200 µm)
    - 3단계에서 예상 장수·용량과 남은 공간을 보인다.
    - 진행 중에는 상태 카드를 보인다: 진행 막대, 다음 촬영까지, 최근 사진, 실험 끝내기.
  - 결과:
    - 핵심 수치 4칸: 최근 개수와 직전 대비, 평균 지름, 사진 n/전체, 다음 촬영
    - 계열 칩, 그래프와 사진 보기
    - 사진 띠와 타임랩스 재생
    - CSV 2종
    - PDF 보고서: 인쇄 창. 밝은 색과 인쇄 폭으로 그래프를 다시 그리고, 머리말에 실험·분석 조건을 넣는다.
  - 고급: 평탄화 기준, 히스토그램, 화소 크기, 센서 레지스터·상태, OpenCV 상태, 단축키
- `scope.py`:
  - `/analyze/preview`가 `dark`, `min`, `max`, `sens`, `split` 인자를 받는다. cfg는 바꾸지 않는다.
  - `fmt=json`이면 원 목록 `[x, y, d_um]`(최대 3000개)을 돌려준다. 인자가 없으면 지금처럼 윤곽 JPEG를 준다.
  - `/stat`에 `disk`(남은 공간, 사진 폴더 크기, 장수, 30초 캐시)와 `now`(보드 시각, 남은 시간 계산용)를 더했다.
- 바꾸지 않은 것: 분석 알고리즘(`scope_rec.py`), DB, 기존 경로와 cfg 키.

**처음 제안에서 바꾼 것.**
- **"자동 맞춤"을 "최소 크기 비교"로 바꿨다.** 실물 프레임에서 최소 크기별 개수가 한 방향으로 움직이지 않았다(5 µm → 0개, 10 → 1, 20 → 7, 30 → 11). 최소 크기가 흐림 정도도 정하기 때문이다. 그래서 자동 기준 대신 사용자가 눈으로 고르게 했다.
- **비교 그림을 하나씩 채우게 했다.** 보드4에서 한 번에 6가지를 분석하면 7.6초가 걸렸다. 최소 크기별로 따로 재 보니, 흐림 필터가 최소 크기에 비례해 커져서 큰 최소 크기가 느렸다.

  | 최소 크기 | 5~20 µm | 50 µm | 100 µm | 200 µm |
  |---|---|---|---|---|
  | 분석 시간 | 0.32~0.37 s | 0.77 s | 1.94 s | 3.8 s |

  그래서 고른 최소 크기를 먼저 세고(0.4초), 나머지는 하나씩 채운다.

**확인(관찰).**
- 인쇄용(흰 바탕) 그래프 색을 dataviz 검사기로 확인했다. 모두 통과했다. `#c98500` 하나만 대비 2.99로 경고가 났는데, 범례와 표가 있다.
- 헤드리스 Chrome(CDP) 시험 `scratchpad/x/cdpui.py`를 두 곳에서 돌렸다.
  - Mac fake(OpenCV 4.13): JS 오류 0개
  - 보드4 fake 8091(실물 프레임으로 만든 YUYV, OpenCV 4.11, numpy 1.24): JS 오류 0개
  - 보드4 결과:
    - 물체 표시: 입자·먼지 조건에서 8개
    - 마법사 2단계: 최소 20 µm에서 8개, 평균 30.1 µm(11.70의 실물 프레임 결과와 같다)
    - 손잡이를 끌면 최소값이 바뀌고, 놓으면 다시 센다
    - 비교 그림을 누르면 최소 크기가 정해진다
    - 간격 10초 실험을 시작했다. 45초 뒤 결과 탭에 사진 5장이 있었고, 핵심 수치와 사진 띠, 재생이 동작했다.
    - 실험 중 실시간 프리셋이 잠겼다. 실험 탭은 진행 카드를 보였다.
    - 인쇄 화면: 흰 바탕, 그래프 폭 700 px. PDF 2쪽(머리말, 핵심 수치, 그래프, 표 / 사진, 값, 분포)
    - 폰 폭 390 px의 세 탭 모두 `scrollWidth == innerWidth`
    - 실험을 끝내면 마법사로 돌아왔다.
- 고친 것: 첫 진입이 `#exp`일 때 마법사가 비던 것, 끌기 중 분포가 사라지던 것, 매초 LED 버튼 내용을 다시 만들던 것, 폰에서 확대 도구가 축척 막대를 가리던 것, 실험 목록 장수가 늦게 바뀌던 것, favicon 404.

**보드에 바꾼 것.**
- 보드4 `/var/tmp/scope-ui/`(시험용 사본, `py` → `/var/tmp/e850-scope/py`)에서 fake 서버가 포트 8091로 돈다. 데이터는 `/var/tmp/scope-dev`다.
- 서비스(`/opt/e850-scope`, 8090)와 진행 중인 실험 #1은 건드리지 않았다.

**아직 확인하지 않은 것.**
- 사용자의 시안 확인
- 설치 뒤 실물 키트로 하는 물체 표시와 마법사
- 설치(서비스 재시작) 뒤 실험 #1이 이어지는지
- 세포·콜로니 프리셋 값(실물로 확인하지 않은 시작값)

### 11.72 현미경 사용 설명서 · 제품 소개 슬라이드, 인쇄 여백과 멈춤 이유 표시 (2026-11-20)

**사용자 요청.** "좋은 것 같아, 메뉴얼겸 제품 소개 자료 html로 만들어줘 16:9 편집가능 슬라이드로". 앞 절(11.71)의 시안을 승인한 것으로 받았다.

**만든 것(저장소).**
- `docs/SCOPE-MANUAL-20261120.html`(1.83 MB, 27장, 그림을 WebP로 넣은 파일 하나).
  - 틀은 `docs/UNET-LECTURE-20261118.html`과 같다. 글자를 바로 고칠 수 있고(contenteditable), 처음부터·이 장부터 발표, HTML로 저장(`SCOPE-MANUAL-20261120.html`로 내려받음), 인쇄·PDF(1280×720 쪽)가 있다.
  - 1~7장 제품 소개: 한눈에, 할 수 있는 일, 시스템 구성, 사양, 분석 방식, 프리셋.
  - 8~26장 설명서: 접속, 화면 구성, 실시간, 실험 4단계, 결과, 보고서·내보내기, 폰, 고급, 단축키, 문제 해결, 데이터 위치, 한계와 확인 전 항목. 27장은 정리.
  - 화면 사진은 보드4 시험 포트 8091(실물 프레임을 되풀이하는 가짜 카메라)에서 헤드리스 Chrome으로 찍었다(1600×1000, DPR 2). 사진마다 "화면 예시 · 시험 영상"이라고 적었다. 번호 표시 위치는 찍을 때 잰 요소 좌표로 정했다.
  - 측정하지 않은 값은 확인 전이라고 적었다: 화소 2.24 µm, 세포·콜로니 프리셋, 며칠짜리 실험, 재시작 뒤 잇기, Chrome 밖 브라우저.
- 만들 때 쓴 스크립트(저장소 밖, scratchpad): `x/shots.py`(화면 사진과 요소 좌표), `x/mkdeck.py`(잘라 넣기와 번호 위치), `x/deckcheck.py`(27장을 1280×720으로 그려 넘침 검사), `x/deckfn.py`(발표 키, PDF).

**설명서를 쓰다가 찾아 고친 것(`tools/board/scope/scope.html`, 11.71 화면, 아직 설치 전).**
- **PDF 보고서의 A4 여백이 검게 나왔다.** poppler(`pdftoppm`)로 그려 보니 여백이 (18, 18, 18)이었다. 페이지가 `:root{color-scheme:dark}`라서 Chrome이 인쇄 여백을 어두운 바탕(#121212)으로 칠한다. `@media print`에 `:root{color-scheme:light}`를 넣었다. 다시 인쇄하니 여백이 (255, 255, 255)였다.
  - macOS 미리보기 계열(`sips`, `qlmanage`)로 그린 그림에서는 머리말 왼쪽이 흰 상자에 가려 보였다. poppler로는 정상이어서 Mac 렌더러 문제로 봤다.
- **자동 촬영이 스스로 멈춘 이유가 화면에 보이지 않았다.** 서버의 `tl.msg`(기간 끝, 보관 한도, 남은 공간 2 GB 미만)는 진행 카드의 `#erMsg`에만 들어갔다. 진행 카드는 실험이 끝나면 숨는다. 마법사 위에 `#wMsg`를 두고 "지난 실험: …"을 보이게 했다. 사용자가 끈 경우(`멈춤`)는 보이지 않는다.
  - 시험: 8091에서 `tl_hours=0.005`(18초), 간격 10초로 시작했다. 2장 찍고 "정한 기간이 끝나 자동 촬영을 마쳤습니다"로 멈췄고, 실험 탭에 "지난 실험: 정한 기간이 끝나 자동 촬영을 마쳤습니다"가 보였다. JS 오류는 없었다.

**확인(관찰).**
- 27장 모두 1280×720 안에 들어간다. 바닥글 위 664 px, 좌우 40 px을 넘는 요소가 없다. 처음에는 14장(2단계 화면 사진)이 791 px까지 넘쳐서 폭을 줄였다.
- 발표 모드: 처음부터 발표 → 1/27, → 두 번 → 3/27, Esc → 편집 모드로 돌아옴.
- 인쇄 PDF: 27쪽, 쪽 크기 960 × 540 pt(16:9).

**관찰한 사실: 실험 #1은 이미 끝나 있었다.**
- 8090 `/rec/runs`: 실험 #1은 2026-11-19 08:12:42에 시작해 18:30:20에 끝났다. 사진 124장, 5분 간격이었다. `tl.msg`는 `멈춤`이다. 이 문구는 `/set tl_on=0`으로 끌 때만 붙는다.
- 대화 기록에서 18:20~18:31 사이에 내가 실행한 명령은 없다. 사용자의 다음 메시지("너의 추천은?")는 18:31:54에 왔다. 누가 껐는지는 확인하지 않았다.
- 그래서 "설치 뒤 실험 #1이 이어지는지" 확인할 일은 없어졌다.

**보드에 바꾼 것.**
- 보드4 `/var/tmp/e850-scope/`(설치 원본)에 새 `scope.py`, `scope.html`을 넣었다. md5는 저장소와 같다. 다른 파일은 원래부터 같았다. 옛 판은 지금 `/opt/e850-scope`에 설치된 것과 같다.
- 보드4 `/var/tmp/scope-ui/scope.html`(시험 포트 8091)을 같은 판으로 바꿨다. 시험 데이터 `/var/tmp/scope-dev`에는 실험 #3, #4(시험용)가 생겼다.
- 서비스(8090)는 건드리지 않았다. 설치는 사용자가 `sudo sh /var/tmp/e850-scope/install.sh`로 한다.

**아직 확인하지 않은 것.**
- 설치 뒤 실물 키트로 새 화면 쓰기(설명서의 화면 사진은 모두 시험 영상)
- 설명서를 Safari·Edge에서 열기와 편집 뒤 저장(Chrome 헤드리스로만 봤다)

### 11.73 16M 모듈(Hi-1634) 펌웨어, 모듈을 바꿀 때 뷰어가 멈추던 문제 (2026-11-20)

**배경(사용자).** 현미경 몸체는 MIPI→USB(CX3)이고, CIS 모듈(8M Hi-846, 16M Hi-1634, 50M Hi-5031Q)을 바꿔 끼운다. 키트2에 16M 모듈을 끼워 보드4에 꽂았다.

**펌웨어(비공개 `sol-cx3-uvc` LOG 12절).**
- `SENSOR=hi1634` 빌드: 2304×1752 / 1152×876 회색, 3.37 fps.
- 센서 설정 출처는 SK hynix 셋파일의 전체 화소 모드다. 폭은 4608로 잘랐다.
- MIPI는 셋파일의 1696 Mbps 대신 720 Mbps로 했다.
  - MIPI만 나누면(424·565 Mbps) 센서가 행의 앞 1/4·1/3만 보내고 나머지를 0으로 채웠다.
  - 424 Mbps에서는 CX3 출력보다 입력이 느려 데이터가 되풀이됐다.
  - 그래서 MIPI PLL 배수를 낮춰 ISP와 MIPI를 같이 늦췄다.
- Mac 시험(RAM): 60프레임 연속 수신, 온전 62/62, MIPI 오류 0. 실내광 기본 노출은 20 ms(평균 126)다.

**뷰어(`tools/board/scope/scope.py`).**
- `SENSORS`에 Hi-1634를 넣었다(줄 클록 80 MHz, 노출 20 ms).
- 센서별 출력 화소 크기를 넣었다(Hi-846·Hi-5031Q 2.24 µm, Hi-1634 2.0 µm). 칩 ID가 바뀌면 노출과 함께 `pixel_um`도 그 값으로 바꾼다.
- **버그.** 모듈을 바꾸려고 키트를 뽑으면, 뷰어의 `v4l2-ctl`이 없어진 `/dev/video0`을 계속 기다렸다. 보드4 뷰어가 22시간째 마지막 프레임에 멈춰 있었다(새 장치는 `/dev/video1`). 장치 파일이 없어지거나 20초 동안 프레임이 없으면 `v4l2-ctl`을 끝내고 다시 찾게 했다.
- 시험: Mac `--fake 2304x1752 --chip 1634`로 `/stat`(Hi-1634, 2.0 µm)와 `/analyze/preview`를 봤다. 재연결 수정은 실물로 시험하지 않았다.

**보드에 바꾼 것.**
- 보드4 뷰어를 `/reload`로 한 번 다시 띄웠다(서비스는 그대로).
- 새 `scope.py`를 `/var/tmp/e850-scope`에 올렸다. 11.72의 `scope.html`과 함께 사용자가 `install.sh`로 설치한다.

**아직 확인하지 않은 것.**
- 보드4(USB 2.0 호스트)에서 16M 키트. 출력 버퍼가 3개뿐이라 50M보다 프레임을 잘 놓칠 수 있다(추정).
- EEPROM 쓰기(지금은 50M 펌웨어). 모듈을 바꿀 때마다 다시 굽지 않으려면 칩 ID로 센서를 고르는 통합 펌웨어가 필요하다.

### 11.74 현미경 통합 펌웨어: 칩 ID로 8M·16M·50M 모듈을 고른다, EEPROM에 씀 (2026-11-20)

**배경(사용자 결정).** 모듈을 바꿀 때마다 펌웨어를 다시 굽지 않도록 펌웨어 하나가 센서를 고르게 한다. EEPROM에는 쓰기 직전에 다시 허락받고 쓴다.

**펌웨어(비공개 `sol-cx3-uvc` LOG 13절).**
- 전원을 켜면 칩 ID를 묻는다.
  1. 10 ms 뒤 I2C 0x46에 Hi-846을 묻는다.
  2. 200 ms 뒤 0x40에 Hi-1634를 묻는다.
  3. Hi-5031Q는 격리 해제 레지스터를 쓴 다음 묻는다.
- 해상도, 프레임 간격, DMA 버퍼, MIPI 폭을 찾은 센서에 맞춘다. 출력은 센서 전용 빌드와 같다.
- 크기: EEPROM 한도 128 KiB에 맞게 64비트 나눗셈을 없앴다. 우리 C 코드는 Thumb으로 컴파일했다. 결과는 129,380 B다.
- Mac 시험(RAM):
  - 16M: 온전 32/32, MIPI 오류 0, 296 ms
  - 50M: 54/54, MIPI 오류 0, 229 ms
  - 50M 전용 이미지와 밝기가 같았다(같은 노출에서 36.8 대 36.9).
- 8M 모듈은 지금 없어 시험하지 못했다.
- 키트2 EEPROM에 썼고, 다시 읽은 내용이 일치했다. 그 전 내용은 50M 전용 판이었다.

**보드4 확인(EEPROM 부팅).** 사용자가 ③을 NORMAL로 바꾸고 16M 모듈로 보드4에 꽂았다.
- 키트가 EEPROM에서 부팅했다(`04b4:00c3`, 480 Mbps, 칩 ID 0x1634).
- 설치된 옛 뷰어는 없어진 `/dev/video1`을 기다리고 있었다(11.73의 버그). `/reload`로 다시 띄우자 받기 시작했다.
- 2304×1752, 3.36 fps(센서 3.37). 193프레임 모두 온전했고 MIPI 오류는 0이었다. 출력 버퍼가 3개뿐이라 걱정했던 USB 2.0 호스트에서도 넘치지 않았다.
- 저장된 설정(LED 255, 노출 10.8 ms)으로는 포화해서 노출을 1.5 ms로 낮췄다(`/set?exp=15`).

**새 뷰어 설치(사용자, 22:40 UTC).** 11.72~11.73 뷰어가 들어갔다.
- 뷰어가 칩 ID 0x1634를 보고 Hi-1634 설정(화소 2.0 µm, 노출 20 ms, 레지스터 목록)으로 바꿨다.
- 서비스가 다시 시작하는 동안 불량 프레임이 있었다. 그 뒤 60초 동안은 203프레임이 모두 온전했고, 뷰어도 203장(3.38 fps)을 받았다.
- 11.73 시험 때 띄워 둔 가짜 뷰어(8091 포트, 사용자 philip)가 12시간째 CPU를 33% 쓰고 있었다. `/var/tmp/scope-dev/stop`으로 껐다.

**센서 표시.** 지금 어떤 CIS인지는 고급 설정(서랍)의 "센서" 줄에만 나왔다. 실시간 화면 하단 막대 맨 앞에 `센서 Hi-1634 · 16M`처럼 보이게 했다(`SENSORS`에 모듈 화소 수 `mp`를 더함). Mac 가짜 모드에서 `/stat`과 페이지를 확인했고, 보드4 `/var/tmp/e850-scope`에 올렸다. 설치는 사용자가 한다.

**설명서(`docs/SCOPE-MANUAL-20261120.html`) 갱신.**
- 구성과 사양에 교체형 모듈(8M·16M·50M), 칩 ID 자동 인식, 16M 2304 × 1752 약 3.4 fps, 모듈별 화소와 시야를 넣었다.
- 상태 줄에 "센서"를 넣었다. 고급 서랍의 화소 크기와 센서 상태 설명을 고쳤고, 문제 해결에 "모듈을 바꾼 뒤" 줄을 더했다. 27장 모두 넘침이 없었다(1280 × 720 렌더 점검).
- 화면 사진은 이전 판 그대로라 상태 줄 사진에는 "센서"가 없다.
- 뷰어 고급 서랍의 화소 크기 설명에도 모듈별 기본값을 넣어 `/var/tmp/e850-scope`에 다시 올렸다.
- **제품 소개용 최종판(사용자: "메뉴얼은 프로모션용").**
  - 표지와 머리말을 "제품 소개 · 사용 설명서"로 바꾸고, 표지에 8M · 16M · 50M 모듈 줄을 넣었다.
  - 5장 "센서 모듈"을 새로 넣었다(28장이 됨). 모듈마다 출력, 속도, 출력 화소, 시야를 보이고, 8M에는 "통합 펌웨어 확인 전"을 달았다. 교체 순서와 상태 줄 그림도 넣었다. 상태 줄 그림은 새 `scope.html`을 Mac 가짜 모드로 띄워 찍었고, 16M 기본값(20 ms, 2.0 µm)은 손으로 맞췄다. 가짜 모드는 칩이 바뀌는 경로를 타지 않는다.
  - 마지막 장에 "모듈" 줄을 넣었다. 한계 장에 "8M을 통합 펌웨어로"를 넣고, "실험·분석을 실물 시료로 끝까지"로 고쳤다.
  - 28장 모두 넘침이 없었다. PDF도 28쪽이다.
- **메일.** 2026-11-20 08:06에 Mail.app으로 `philip.de.kim@yonsei.ac.kr`에 보냈다. 첨부는 PDF(12 MB, 보기·발표용)와 HTML(1.9 MB, 고치기용) 두 개다. 보낸편지함에서 첨부 2개를 확인했다(약 70초 걸림).

**보드에 바꾼 것.**
- 뷰어를 `/reload`로 다시 띄웠다(서비스는 그대로).
- 노출 설정을 1.5 ms로 바꿨다. 새 뷰어가 칩을 바꾸면서 20 ms로 되돌렸다.
- 시험용 가짜 뷰어를 껐다.

**아직 확인하지 않은 것.**
- 센서 표시판 설치와 실제 화면.
- 보드4에서 50M을 통합 펌웨어로.
- 8M 모듈.

### 11.75 현미경 뷰어 외부 접속: Cloudflare 임시 터널 (2026-11-20)

**사용자 결정.** 보드4 뷰어를 외부에서 연다. 방식은 Cloudflare 임시 링크(계정 없음), 허용 범위는 "전체 조작, 보호 없음"이다. 뷰어에 로그인이 없다는 것을 알리고 고르게 했다. 링크를 아는 사람은 노출·LED·실험을 바꾸고, 센서 레지스터를 쓰고, 뷰어를 끌(`/quit`) 수 있다.

**한 것(보드4, 사용자 philip, sudo 없음).**
- `cloudflared` 2026.11.1 linux-arm64(GitHub 공식 배포, 37,818,397 B)를 받았다. 릴리스 노트의 sha256 `b155cec3…7c5a`와 일치했다. 위치는 `/home/philip/bin/cloudflared`다.
- 실행: `setsid nohup ~/bin/cloudflared tunnel --no-autoupdate --url http://localhost:8090 > /var/tmp/cloudflared.log 2>&1 &`.
- 주소는 `https://<무작위 단어>.trycloudflare.com`이다. 링크만 알면 조작할 수 있으므로 공개 저장소에는 적지 않는다. 지금 주소는 `grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' /var/tmp/cloudflared.log`로 본다.
- 확인(Mac에서 외부 경로로): 페이지 200(117 KB, 0.4 s), `/stat`(Hi-1634, 3.35 fps), `/frame.png` 200(0.8 MB, 1.1 s).
- ICMP 프록시 경고(ping_group_range)는 무시해도 된다. HTTP 터널과 관계없다.

**알아 둘 것.**
- 보드를 재부팅하면 터널이 꺼진다. 다시 띄우면 주소가 바뀐다.
- 임시 터널은 Cloudflare가 시험용으로 제공하는 것이라 가동 보장이 없다.
- 터널이 켜져 있는 동안에는 영상이 Cloudflare를 거친다. 설명서의 "밖으로 나가지 않음"은 터널을 끈 상태에 대한 설명이다.
- 끄기: 보드4에서 `pkill -f 'cloudflared tunnel'`.

**메일.** 사용자 요청("작동되면 접속 주소 메일로 보내")에 따라 18:10:18에 학교 주소로 접속 주소와 위의 주의 사항을 보냈다(첨부 없음). Mail 보낸편지함에서 확인했다.

### 11.76 골라서 넣는 기능 묶음 `features/`: 유선랜·오디오·영상 코덱·BT·USB 호스트·X (2026-11-21)

**사용자 요청(외출 중, 모두 사전 승인).** "보드4에 다 설치하고, 옵션에 따라 X를 올리거나 말거나 하게 해 줘. 오디오, 이더넷 등의 드라이버는 다른 보드에도 다 필요하니 BT 포함, 추가할 수 있도록. 다른 보드들도 여기서 만든 드라이버를 쉽게 추가할 수 있게 깃을 구조화해 줘."

**구조.** 저장소에 `features/`를 새로 만들었다. 자세한 사용법은 `features/README.md`에 있다.
- 기능마다 디렉터리가 하나씩 있다: `usbhost`, `ethernet`, `audio`, `video`, `bt`, `desktop`. 각 디렉터리의 `feature.sh`가 `install`, `remove`, `status`를 맡는다. 첫 줄 주석에 `# about:`(설명)과 `# needs:`(의존 기능)를 쓴다.
- 보드의 명령은 `e850-feature list | status | install 이름… | remove 이름…` 하나다. 공통 함수는 `common.sh`에 있다(`ko_install`, `unit_install`, `apt_need`, `users_to_group`).
- Mac에서는 `features/host/bundle.sh`가 번들 `out/e850-features.tgz`(2.0 MB)를 만든다. 하는 일은 다음과 같다.
  - Docker로 모듈 4개를 빌드하고 strip한다: `smsc95xx`, 패치한 `exynos3830_sound`, `e850-hubpwr`, 패치한 `scsc_wlan`.
  - Linaro 펌웨어를 받는다(`fetch-fw.py`, `fw.sha256`로 확인, `out/fw-cache`에 캐시).
  - 스크립트를 넣는다.

  `features/host/deploy.sh philip@<IP> 기능…`이 scp로 보내고 sudo로 설치한다.
- 보드에 놓이는 자리:

  | 대상 | 위치 |
  |---|---|
  | 번들 전체 | `/opt/e850-features` |
  | 모듈 | `/lib/modules/$(uname -r)/extra/`. `/etc/depmod.d/e850-features.conf`의 `search extra built-in` 덕분에 같은 이름이면 extra가 이긴다 |
  | 펌웨어 | `/lib/firmware/` |
  | 설치 기록 | `/var/lib/e850-features/<기능>` |
- 모듈은 번들의 `KVER`가 `uname -r`과 같을 때만 넣는다.
- 펌웨어(Samsung 바이너리)는 git에 넣지 않는다. 출처는 Linaro `device/samsung/universal3830`의 커밋 `8bfd9659`(android13-e850-96, 2025-08-15)다. 받은 파일은 `calliope_{sram,dram}{,_2}.bin`, `abox_tplg.bin`, `mixer_paths.xml`, `mfc_fw_v10.21.bin`(→ `mfc_fw.bin`)이다.
- X(`desktop`)는 따로 설치한다. 나머지 기능만 넣으면 X 없는 가벼운 보드가 된다.

**시험 순서.** 커널 모듈을 처음 올리는 일은 보드3(ACM, UART, fastboot, `/proc/last_kmsg` 있음)에서 먼저 했다. 보드4에는 확인된 것만 넣었다. 보드4는 Wi-Fi로만 닿기 때문이다.

### 11.77 오디오: 카드 등록 실패는 DT 자식 순서 때문, 패치한 machine 드라이버 (2026-11-21)

**관찰(보드3).**
- 펌웨어를 넣고 `modprobe exynos3830_sound`를 하면 ABOX는 "Calliope is ready to sing"까지 간다.
- 그런데 카드 등록이 실패했다: "No CPU DAIs support playback for stream UAIF0", "can't create pcm UAIF0 :-22".

**원인.**
- `exynos3830_sound.c`는 sound 노드의 자식을 정적 배열 `exynos_dai[]`(rdma0~11, wdma0~4, dual, ddma, uaif0~2, spdy, sifs, nsrc, *_be, usb, echo, fwd)에 **위치로** 대응시킨다.
- 그런데 `build-bootimg.sh`의 fdtoverlay가 overlay의 새 자식 노드를 **거꾸로** 넣었다. merged.dtb의 순서는 0번 fwd … 62번 rdma0이다. 그래서 SIFS2 같은 링크가 UAIF0 자리에 들어갔다.

**수정.** `modules/exynos3830-sound/`(`exynos3830-sound-order.patch`)는 자식을 이름(접두어, 번호, `_be`)으로 정렬한 뒤 대응시킨다. merged.dtb는 바꾸지 않았다.

**실패와 교훈.** 등록에 실패한 같은 부팅에서 원래 모듈을 내리고 패치 모듈을 올리자 커널 패닉이 났다.
- 증상은 `exynos_register_card_work_func`의 `__list_add_valid` BUG였다. 워치독 → fastboot → `fastboot reboot` 순으로 복구했다.
- 기록은 `/var/tmp/last_kmsg-b3-0634.txt`에 있다.
- 그래서 `audio.sh`는 카드 없이 모듈만 올라와 있으면 다시 시도하지 않고 "reboot"만 출력한다.

**결과.** 깨끗한 부팅에서 패치 모듈을 올리자 card0 `ExynosMadera`가 생겼다.
- 재생 장치 RDMA0~11과 `calliope_0`이 있고, 컨트롤은 780개다.
- 경로: Android `mixer_paths.xml`의 기본값 + `route-sifs0-to-uaif0` + `dev-headset`을 쓴다. Android의 BD_MIXER/RDMA3 부분은 뺐다. 그러면 RDMA0 → SPUS OUT0 → SIFS0 → UAIF0 → AUD3004X DAC → HPOUT으로 이어진다.
- `features/audio/mixer-path.py`가 `mixer_paths.xml`을 `amixer -s` 입력으로 바꾼다(selftest 있음).
- 재생 시간:

  | 보드 | 장치 | 입력 | 3초 분량 재생 시간 |
  |---|---|---|---|
  | 보드3 | `hw:0,0` | 48 kHz WAV | 3.193 s |
  | 보드4 | `default` | 44.1 kHz | 3.33 s |
  | 보드4 | `default` | 48 kHz | 3.19 s |

  DAPM에서 HPOUTLN까지 켜졌다. **귀로 들어 보지는 못했다**(사용자 외출 중).

**발견 두 가지(보드4).**
- dmix를 쓸 수 없다: "unable to create IPC semaphore … Function not implemented". 이 커널에는 SysV IPC가 없다. 그래서 `/etc/asound.conf`의 `default`는 `plug → hw:ExynosMadera,0`이고, 한 번에 한 프로그램만 쓴다.
- RDMA PCM은 8~384 kHz를 다 받는다고 알린다. 그러나 44.1 kHz를 넣으면 3초 분량이 2.56초에 끝났다(약 15% 빠름). `ABOX SPUS ASRC0=1`로도 같았다. 그래서 `plug`가 48 kHz S16 2채널로 바꾸게 했다.

**권한.** `philip`이 `audio` 그룹에 없어서 열지 못했다. `audio`·`video` 설치가 uid 1000~59999 사용자를 각 그룹에 넣는다.

**잭 메시지.** 카드가 올라올 때 `aud3004x` 잭 감지 메시지가 32줄 나오고 멈췄다(5초 동안 0줄). HDMI 소리는 아직 없다.

### 11.78 BT: scsc_bt → /dev/vhci → BlueZ, 그리고 BT를 켜면 Wi-Fi 복구가 실패하던 문제 (2026-11-21)

**방식.**
- 커널의 BlueZ 연결부(`scsc_bluez.c`)는 빌드되지 않는다: `wakelock.h`가 없고, 두 번째 `module_init`이 있고, 커널 버퍼를 fops에 넘긴다.
- 그래서 사용자 공간 브리지 `features/bt/e850-btbridge.py`를 만들었다. vendor `scsc_bt.ko`의 `/dev/scsc_h4_0`(H4 바이트 흐름)과 `hci_vhci`의 `/dev/vhci`(한 번 읽고 쓸 때 패킷 하나) 사이에서 패킷을 H4 헤더로 잘라 옮긴다(selftest 있음).
- vhci에 `ff 00`을 먼저 써서 hci0을 바로 만든 뒤 scsc 장치를 연다. 처음에는 vhci가 1초 뒤 자동으로 만들기를 기다렸는데, 그 전에 칩이 먼저 이벤트를 보내 write가 ENODEV로 실패했다.

**결과.**

| 보드 | hci0 | BD 주소 | 스캔 |
|---|---|---|---|
| 보드3 | UP RUNNING | `9C:23:E0:0A:74:9C` | 12초에 27대 |
| 보드4 | UP RUNNING | `9C:23:E0:0B:F4:0C` | 23대 |

- BD 주소는 드라이버가 칩 고유 ID로 만든다(fastboot 일련번호와 같은 값).
- `bt.hcf`는 Wi-Fi 펌웨어 묶음(`mx140/conf/bluetooth/`)에 이미 있다.
- bluetoothd가 hci0을 보고 자동으로 떴다.
- 스캔하는 동안 Wi-Fi 복구 횟수는 0, ping 손실은 0이었다.

**사고(보드4).**
- 상황: BT(`e850-bt.service`)를 켜 둔 채 desktop 패키지 278개를 Wi-Fi로 받았다. 설치는 끝까지 정상이었다(마지막 줄까지 출력, rc=0).
- 증상: 그 직후 보드4가 Wi-Fi에서 사라졌다(ARP incomplete). 보드4는 USB가 호스트 모드이고 유선랜 케이블도 없어서 원격으로 살릴 수 없었다.
- 사용자가 전원을 다시 넣었다. 부팅은 되지만 무선이 안 된다고 알려 왔다.
- **정정(11.80):** 이 사고의 원인은 아래의 Wi-Fi 복구 문제가 아니라 desktop 패키지가 들인 `libpam-systemd` 때문에 생긴 커널 패닉(EMS cgroup 배열 넘침)일 가능성이 크다. 보드3에서 같은 패키지를 설치한 뒤 로그인할 때마다 패닉이 났다. 보드4의 로그는 아직 보지 못했다. 아래 Wi-Fi 복구 경쟁은 보드3에서 따로 재현하고 고친 실제 버그다.

**재현(보드3).** BT 브리지를 켠 채 WLBT 강제 패닉(`wifi-panicloop.sh 1`)을 일으켰다.
- Wi-Fi가 40초 안에 돌아오지 않았고 `/proc/driver/mxman_ctrl0`도 사라졌다.
- 로그:
  1. `scsc_mx_service_open: error: refused due to previous f/w failure`가 나왔다.
  2. `slsi_chip_recovery: fail at line:7644` WARNING이 났다.
  3. 그 뒤 `slsi_net_open: Fail: called when MLME in blocked state`가 나서 wlan0을 다시 올릴 수 없었다.
- `rmmod scsc_wlan; modprobe scsc_wlan`을 하면 커널 패닉이 난다(`mxman_wifi_kobject_ref_get` → `kobject_uevent_env` NULL). 그러니 재부팅 말고는 복구 방법이 없다.

**원인(소스).**
- `mxman_failure_work`는 서비스 시작 금지(`NOT_ALLOWED_START`)를 건 뒤 `scsc_mx_module_reset()`으로 모든 클라이언트를 remove/probe한다. 금지를 푸는 것(`ALLOWED_START_STOP`)은 그 뒤다.
- WLAN probe가 넣은 복구 작업 `slsi_chip_recovery`는 다른 작업 큐에서 돈다. BT 서비스가 열려 있으면 BT remove가 사용자 공간이 장치를 닫을 때까지 기다리느라 순서가 밀린다. 그래서 복구 작업이 금지가 풀리기 전에 서비스를 열고 거부된다. 로그에서도 거부 바로 다음 줄이 `error:0`이었다.
- 복구 작업은 다시 시도하지 않는다.

**수정.**
- `modules/scsc-wlan-recovery/`(`scsc-wlan-recovery.patch`, `build-module.sh`)는 `slsi_chip_recovery`가 서비스 열기에 실패하면 100 ms 간격으로 최대 50번 다시 연다.
- `slsi_sm_wlan_service_open`은 거부를 `-EINVAL`로 바꿔 돌려준다. 그래서 처음에 `-EILSEQ`만 보던 판은 효과가 없었다. 이것은 실패한 실험이다. 고친 판은 오류면 재시도한다. 실패한 열기는 상태를 PROBED로 되돌리므로 다시 열 수 있다.
- 빌드는 외부 모듈이다(디렉터리를 복사하고, include 경로를 절대 경로로, `scsc_wifilogger`는 제외). 패치 없이 같은 방식으로 빌드한 모듈은 트리 빌드와 함수 목록·주소가 같았다(LLVM 해시 접미사만 다름).

**확인(보드3, 고친 판 `ac30b0a4`).**
- BT를 켠 채 강제 패닉: 거부 1번, "retry 1"에서 성공했다. Wi-Fi는 8,094 ms 뒤 돌아왔고, WARNING 0, ping 정상이었다. 브리지는 EIO로 끝난다(서비스는 `Restart=on-failure`). 다시 띄우면 hci0 UP, 스캔 17대였다.
- BT를 끈 채 강제 패닉: 8,139 ms 뒤 돌아왔고 WARNING 0이었다.

**bt 기능에 넣은 것.**
- `bt` 설치가 고친 `scsc_wlan.ko`를 `extra/`에 넣는다.
- 실행 중인 시스템에서 다시 올리면 패닉이 나므로, 다음 부팅부터 쓰인다.
- `e850-bt.service`의 `bt-check.sh`는 `extra/scsc_wlan.ko`가 이번 부팅 **전에** 설치된 경우에만 BT를 켠다. `/sys/module/scsc_wlan/srcversion`이 이 커널에는 없어서 이 방법으로 판정한다.

### 11.79 유선랜(LAN9514)·USB 호스트·MFC (2026-11-21)

**유선랜.**
- `modules/smsc95xx`(커널 소스의 `drivers/net/usb/smsc95xx.c`를 외부 모듈로 빌드, usbnet·MII는 커널에 내장)을 만들었다.
- LAN9514는 USB 허브 뒤에 있어서 USB 호스트 모드일 때만 보인다.
- EEPROM이 없어 부팅마다 MAC가 무작위였다. `10-e850-eth.link`(Driver=smsc95xx, Name=eth0, MACAddressPolicy=persistent)로 고정했다.
- `30-e850-eth.network`는 DHCP이고, RouteMetric 100으로 Wi-Fi(1024)보다 우선하며, RequiredForOnline=no다.
- 보드4에서 `ID_NET_LINK_FILE=/etc/systemd/network/10-e850-eth.link`와 `extra/smsc95xx.ko`가 쓰이는 것을 확인했다. **케이블이 없어 링크와 DHCP는 확인하지 못했다**(carrier 0).

**USB 호스트(`usbhost`).**
- `e850-scope`가 하던 일을 따로 떼었다: `e850-hubpwr`, `gpiohold` gpa3-5, OTG `b_sess`/`id`=0.
- 명령은 `e850-usbhost on|off`이고, 유닛 `e850-usbhost.service`는 부팅 때 켜지 않는다. 켜면 micro-USB ACM/ECM이 없어지기 때문이다.
- `e850-scope`가 돌고 있으면 그쪽에 맡기고 아무것도 하지 않는다.
- 보드4: 허브 0424:9514, 랜 0424:ec00, 현미경 04b4:00c3이 보였다.

**MFC(`video`).**
- `mfc_fw.bin`과 `e850-mfc.service`(`modprobe exynos_mfc`)를 넣었다.
- 보드3·4에서 `/dev/video6` s5p-mfc-dec, `/dev/video7` s5p-mfc-enc(8·9는 secure)이 생겼다.
- 펌웨어는 처음 열 때 올라간다. **디코딩 시험은 아직 하지 않았다.**

### 11.80 X 데스크톱, 그리고 로그인하면 커널이 죽는 문제(EMS cgroup 배열 넘침) (2026-11-21)

**desktop 기능.**
- `features/desktop/`는 Xorg(fbdev, `/dev/fb0`, ShadowFB), Openbox, LXTerminal, 나눔 글꼴, Firefox ESR, mpv를 설치한다(`--no-install-recommends`로 278개, 받기 243 MB, 설치 뒤 901 MB).
- `e850-desktop.service`는 tty7에서 `startx`를 띄운다. `Conflicts=dvslive.service`, `Wants=e850-usbhost`이고 부팅 때 켜지 않는다.
- **USB:** `e850-desktop start`를 하면 `e850-usbhost`도 켜져 micro-USB ACM이 끊긴다. 설계대로이니 그 뒤로는 ssh로 다룬다.

**보드3 준비.**
- 보드3의 파일 시스템은 3 GB(남은 공간 217 MB)였다. 그래서 보드4·보드1과 같이 `resize2fs /dev/mmcblk0p24`로 54 GB로 늘렸다(마운트한 채, dmesg `resized filesystem to 14148084`).
- 첫 설치는 apt 목록이 낡아서 실패했다. `firefox-esr 153.6.0esr-1~deb12u2`를 받으려다 security 저장소에서 404가 났다. 그래서 `common.sh`의 `apt_need`가 설치 전에 `apt-get update`를 하게 했다.
- 두 번째 설치는 RC=0이었다. 첫 시도와 두 번째 시도 모두 BT(`e850-bt`)를 켠 채 받았다(첫 시도에서 171 MB를 12초에 받음). Wi-Fi 복구 횟수는 0이었다.

**관찰: X를 켜자 커널 패닉(보드3).**
- `systemctl start e850-desktop` 다음 순서로 일이 일어났다.
  1. USB가 호스트 모드로 바뀌었다(ACM 끊김).
  2. 4초 뒤 커널 패닉이 났다(UART `~/e850-logs/uart-board3.log`):
     ```
     Internal error: BRK handler: f2005512 [#1] PREEMPT SMP
     pc : freqboost_can_attach+0x1bc/0x1c4 [ems]
      ems_hook_cpu_cgroup_can_attach ← cpu_cgroup_can_attach ← cgroup_migrate_execute
      ← cgroup_attach_task ← cgroup_procs_write ← vfs_write      (Comm: systemd, PID 1)
     ```
  3. 워치독이 리셋했고, LK가 `Abnormal RST_STAT`로 보고 fastboot에 멈췄다.
- `fastboot reboot` 뒤에도 부팅마다 35초쯤 같은 패닉이 났다(2번 연속). 그 시점은 ACM의 root 자동 로그인이다.
- `BRK 0x5512`는 clang UBSAN 함정이다. 0x55xx가 UBSAN이고, 0x12 = 18이 배열 범위 초과다.

**원인(소스).**
- `kernel/sched/ems/freqboost.c`의 `freqboost_can_attach`는 `dst_bg = css->id - 1`로 `bg->group[]`을 쓴다.
- `main.c`의 `cpuctl_task_group_idx`도 `css->id - 1`을 돌려준다. 이 값으로 여러 배열(`tex.enabled[]` 등)을 읽는다.
- 배열 크기는 `ems.h`의 `CGROUP_COUNT`로 9다(Android의 root, foreground, background, top-app, rt, system, system-background, nnapi-hal, camera-daemon).
- 그래서 cpu 컨트롤러가 켜진 cgroup이 9개를 넘고, 실행 대기 중인 태스크가 그런 cgroup으로 옮겨지면 배열 밖을 쓴다.

**왜 이제야 났나.**
- 기본 rootfs에는 `libpam-systemd`가 없다. 보드1에서 확인했다:
  - `libpam-systemd`가 설치되어 있지 않다.
  - `loginctl` 세션이 0개다.
  - 루트 cgroup의 `subtree_control`은 `memory`뿐이다. cpu 컨트롤러가 어디에도 켜져 있지 않다.
- desktop 패키지(`dconf-service` 등)가 `dbus-user-session`을 들이고, `dbus-user-session`은 `libpam-systemd`를 필요로 한다(Debian 12 컨테이너에서 같은 패키지로 확인).
- 그 뒤로는 로그인(ACM 자동 로그인, ssh)마다 다음이 일어난다.
  1. logind 세션과 `user@UID.service`가 생긴다.
  2. Debian 12의 `user@.service`는 `Delegate=pids memory cpu`다.
  3. 사용자 관리자의 `app.slice`·`session.slice`(CPUWeight=100)와 `background.slice`(CPUWeight=30)가 cpu 컨트롤러를 켠다.
  4. cpu cgroup이 9개를 넘는다.

**가설로 남은 것.**
- **보드4 사고:** 11.78의 보드4 사고(desktop 설치 직후 사라짐, 전원을 다시 넣어도 무선 없음)도 이것으로 본다. 같은 패키지를 깔았고, 설치 직후 다음 로그인에서 패닉 → 워치독 → fastboot에 멈춘다. 그러면 Wi-Fi에서 사라진다. 보드4의 `/proc/last_kmsg`로 확인해야 한다.
  - 보드4에는 ACM 자동 로그인이 없다(scope가 USB를 호스트 모드로 바꾼다). 그래도 `sudo`가 `common-session`의 `pam_systemd`를 거치면서 root의 `user@0.service`를 띄울 수 있다. 설치 뒤 deploy 스크립트가 다음 `sudo`를 부른 때가 그 시점일 수 있다. 확인하지 않은 추측이다.
- **Wi-Fi 복구 경쟁:** 11.78의 Wi-Fi 복구 경쟁은 이와 별개로 보드3에서 재현하고 고친 실제 버그다.

**수정.**
- `tools/linux510/build-bootimg.sh`의 명령줄에 `cgroup_disable=cpu`를 넣었다.
  - cpu 컨트롤러가 꺼지면 모든 태스크의 cpu css는 루트(id 1)라서 EMS 번호는 늘 0이다.
  - 이 시스템은 cgroup의 CPUWeight를 쓰지 않는다.
- `features/desktop/`는 apt보다 먼저 `/etc/systemd/system/user@.service.d/e850-no-cpu-delegate.conf`(`Delegate=`, `Delegate=pids memory`)를 넣는다. 새 부트 이미지를 굽지 않은 보드를 위한 것이다.
- 지금 쓸 이미지는 `~/e850-logs/boot-nocpu/boot.img`다.
  - boot-dvs3에서 DTB `/chosen bootargs`에 `cgroup_disable=cpu`만 더했다.
  - 헤더 id(SHA1)는 다시 계산했다. 같은 계산이 boot-dvs3의 id를 그대로 재현하는 것을 확인했다.
  - 저장소 스크립트로 다시 빌드한 판(`boot-dvs4`)은 쓰지 않는다. 커널은 같지만 initramfs의 모듈 여러 개(scsc_wlan, fimc-is 등)가 boot-dvs3과 달라서다. Docker 볼륨이 그 사이 바뀌었다.

**확인(Docker 컨테이너, 사용자 공간만).**
- Debian 12 arm64 컨테이너에서 systemd 252.39를 PID 1로 띄웠다(`--privileged --cgroupns=private`, 컨트롤러 `cpuset cpu io memory hugetlb pids rdma`). `systemd libpam-systemd dbus-user-session`를 깔고 `systemctl start user@1001.service`로 로그인 하나를 흉내 냈다.
- cpu css 수는 루트 1개에 부모의 `subtree_control`에 cpu가 있는 cgroup 수를 더해 셌다.

| | drop-in 없음 | drop-in 있음 |
|---|---|---|
| cpu css 수 | 13 | 1 (루트만) |
| `user@1001`의 `subtree_control` | `cpu memory pids` | `memory pids` |
| `DelegateControllers` | `cpu memory pids` | `memory pids` |

- 로그인 하나로 13개가 된다. 9칸 배열을 넘기기에 충분하다. drop-in을 넣으면 cpu 컨트롤러가 어디에도 켜지지 않는다.
- 커널 쪽(`cgroup_disable=cpu`로 부팅한 보드)은 아직 확인하지 못했다.
- 저장소의 유닛 중 `CPUWeight`·`CPUQuota`·`CPUShares`를 쓰는 것은 없다. 그래서 cpu 컨트롤러를 꺼도 기존 서비스의 동작은 바뀌지 않는다고 본다.

**실패한 시도.** 보드3을 굽지 않고 RAM으로 띄우려고 `fastboot boot`를 했다. LK는 `fb: boot`를 찍고 멈췄다(`getvar`, `reboot`에도 응답 없음). 7.3절에 이미 "동작하지 않는다"고 적혀 있던 것을 놓쳤다. 보드3은 전원을 다시 넣어야 한다.

**강의 자료.** `docs/FEATURES-LECTURE-20261121.html`(27장, 쪽마다 용어 주석, X 측정은 "미확인")과 PDF를 2026-11-21 10:38:23에 메일로 보냈다(Mail.app 보낸편지함에서 첨부 2개 확인). 커밋 1acfe6a, `features-20261121` 브랜치 push.

**아직 하지 못한 것(10:40 기준, 11.81에서 이어짐).**
- boot-nocpu로 부팅한 확인 → 11.81에서 확인.
- 보드에서 drop-in만 있을 때의 확인(컨테이너에서는 위처럼 확인).
- X 화면, Firefox, YouTube 측정 → 11.81.
- 보드4 복구.

### 11.81 보드3에서 확인: boot-nocpu, MFC 부팅 경합, desktop 패키지 누락, headless Firefox (2026-11-21 19:03~)

**boot-nocpu 굽기.**
- 19:02쯤 보드3 전원이 다시 들어왔다. LK가 `Abnormal RST_STAT: 0x10000000`으로 fastboot에 들어갔고, 감시 스크립트가 19:03:02에 boot-nocpu를 구웠다.

**관찰 1: 첫 부팅에서 다른 패닉(MFC).**
- 23.46 s에 커널 패닉이 났다. EMS가 아니었다.
- 순서:
  1. `exynos_mfc`가 `/dev/video6`을 등록했다.
  2. 3 ms 뒤 udev의 `v4l_id`가 그 노드를 열었다.
  3. `mfc_open:374: no mfc device to run`이 찍혔다.
  4. 닫을 때 `mfc_release+0x2c`에서 `fffffffffffff1b0`을 읽어 Oops가 났다.

**원인(소스, `drivers/media/platform/exynos/mfc/mfc.c`).**
- `__mfc_video_device_register()`가 `video_register_device()`를 먼저 부르고 `video_set_drvdata()`를 나중에 부른다(1075, 1083행). 그 사이에 열면 `dev`가 NULL이다.
- `mfc_open()`은 `ret = 0`으로 시작한다. `!dev`이면 `err_no_device`로 가서 0을 돌려준다. 그래서 열기는 성공하는데 `file->private_data`가 비어 있다.
- 닫을 때 `mfc_release()`가 `fh_to_mfc_ctx(NULL)`을 쓴다.

**수정.**
- `modules/exynos-mfc`(패치 한 줄: `!dev`이면 `ret = -ENODEV`)로 고쳤다. 열기가 실패하면 VFS는 release를 부르지 않는다.
- `features/video`가 `extra/exynos_mfc.ko`로 넣는다. 다음 부팅부터 쓰인다.

**관찰 2: eMMC.**
- 패닉 뒤 자동 재부팅에서 `mmc0: cqhci: ... cmd error -110`이 났다. 파티션 표를 읽지 못해 initramfs 셸에 머물렀다(11.26과 같은 현상).
- ACM에서 `reboot -f`를 하자 정상 부팅됐다.

**확인(boot-nocpu, 측정).**
- `/proc/cmdline`에 `cgroup_disable=pressure`(vendor 기본)와 `cgroup_disable=cpu`가 둘 다 있다.
- `cgroup.controllers`는 `cpuset io memory`다(cpu 없음).
- 로그인 세션은 tty1과 ttyGS0의 root 둘이다. `user@0.service`는 active이고, `DelegateControllers=cpu memory pids`를 요구하지만 실제 `subtree_control`은 `memory`뿐이다.
- 부팅 2분 동안 패닉 0회(전에는 35초마다 났다). e850-mfc·e850-bt·e850-wifi는 active, Wi-Fi 복구 0회.
- 데스크톱 세션(philip, `PAMName=login`)이 생긴 뒤에도 패닉은 없었다.

**확인(고친 MFC 모듈).**
- 시험 1(실행 중에 다시 올림):
  - 고친 모듈을 `extra/`에 넣고, `rmmod` 뒤 `modprobe`로 다시 올렸다.
  - 442.316 s와 442.320 s에 udev `v4l_id`가 경합 구간에 두 번 들어갔다("no mfc device to run"). 둘 다 패닉 없이 실패로 끝났다.
- 시험 2(새 부팅): 고친 모듈로 부팅한 19:17 부팅에서도 23.86 s에 같은 구간에 들어갔고, 패닉 없이 지나갔다.
- 정리: 이 경합은 이번 부팅 세 번 중 두 번 났다. video 기능을 넣은 보드(보드4 포함)는 vendor 모듈로 부팅할 때마다 위험하다.
- 새 부팅에서 `modinfo -n`은 `extra/exynos_mfc.ko`이고, `/dev/video6`(디코더)과 `/dev/video7`(인코더) 열기에 성공했다.

**실패한 시험: MFC를 내리기.**
- `/dev/video6~11`을 계속 여는 프로그램을 돌리면서 `rmmod exynos_mfc`를 했다. 그러자 `chrdev_open` → `try_module_get`에서 해제된 모듈 주소를 읽어 패닉했다(497.9 s).
- 내린 뒤 다시 올린 모듈에서는 `Driver 'mfc-core' is already registered`가 나고 `There is no main core`라서 열기가 EINVAL로 실패한다.
- 결론: `exynos_mfc`는 내리지 않는다. 바꾼 모듈은 재부팅해서 쓴다.

**MFC로 디코딩: ffmpeg로는 안 된다(관찰과 소스).**
- 시험용으로 보드3에만 ffmpeg 5.1.9를 깔았다. video 기능에는 넣지 않았다. `h264_v4l2m2m` 등 디코더가 보인다.
- ffmpeg가 장치를 고르는 과정:
  - ffmpeg는 `/dev`를 읽는 순서대로 장치를 고른다. 처음에는 보안 디코더 `/dev/video8`(`s5p-mfc-dec-secure`)을 골라 실패했다.
  - 보안 노드를 0600으로 바꾸자(임시 chmod) `/dev/video6`을 골랐다.
- 그래도 `VIDIOC_REQBUFS`가 EINVAL이었다.
  - vendor 드라이버는 큐를 `VB2_USERPTR | VB2_DMABUF`로만 만든다.
  - `mfc_dec_v4l2.c:820`은 `V4L2_MEMORY_MMAP`을 "Not supported memory type"으로 거부한다.
  - ffmpeg의 v4l2m2m은 MMAP 버퍼만 쓴다.
- MFC를 쓰려면 Android처럼 DMABUF를 넘기는 클라이언트가 필요하다. 만들지 않았다.
- CPU 디코딩(ffmpeg, libx264 ultrafast로 만든 testsrc2 10 s, 30 fps, 8코어):

| 해상도 | 실제 시간 | CPU 시간(user + sys) |
|---|---|---|
| 720p | 0.41 s | 2.25 s |
| 1080p | 0.76 s | 4.70 s |

**desktop 기능의 패키지 누락(관찰).**
- `e850-desktop start`가 `ExecStartPre=/bin/chvt 7`에서 status 203/EXEC로 실패했다. `chvt`는 `kbd` 패키지에 있는데 설치되지 않았다. 09:05에는 이 줄에 닿기 전에 EMS 패닉이 나서 드러나지 않았다.
- `xserver-xorg-legacy`도 없었다. 그래서 `/etc/X11/Xwrapper.config`(`needs_root_rights=yes`)를 읽는 setuid `Xorg.wrap`이 없다.
- 두 패키지를 `PKGS`에 넣었다. Debian 12 컨테이너에서 `/bin/chvt`와 `-rwsr-sr-x /usr/lib/xorg/Xorg.wrap`이 생기는 것을 확인했다.
- 보드3에 깔려면 root가 필요하다. 그런데 usbhost가 켜져 ACM(root 셸)이 끊긴 상태라 깔지 못했다.
  - 사람이 없으면 root로 돌아갈 길이 없다(ssh root 막힘, philip sudo는 비밀번호).
  - UART로 BREAK와 sysrq를 보냈지만 반응이 없었다. ttySAC0를 여는 프로세스(getty)가 없으면 커널이 수신을 켜지 않는 것으로 본다(가설).
- 교훈: usbhost를 켜는 시험은 root를 다시 얻을 길(USB를 빼도 되는 보드, 또는 sudo)이 있을 때 한다.

**headless Firefox로 YouTube(측정).**
- Firefox ESR 153.7, `--headless --marionette`, philip, X 없음. `/var/tmp/ff-yt.py`, Big Buck Bunny(`aqz-KE-bpKQ`), 음소거.
- CPU 사용률은 `/proc/stat`의 8코어 전체 기준이다.

| 화질 | 재생 | 프레임 | 드롭 | 보인 fps | CPU |
|---|---|---|---|---|---|
| 480p(854×480) | 30 s에 30.9 s | 914 | 0 | 30.5 | 35% |
| 720p60(1280×720) | 30 s에 30.9 s | 1845 | 9 (0.5%) | 61.2 | 46% |
| 1080p60(1920×1080) | 30 s에 31.5 s | 1811 | 239 (13%) | 52.4 | 70% |

- 끝난 뒤 온도(`thermal_zone0`)는 480p 56℃, 720p 67℃, 1080p 82℃였다.
- 판단: 720p60까지는 CPU만으로 제 속도다. 1080p60은 프레임이 빠진다.

- 화면에 내보내는 비용(Xorg fbdev ShadowFB 복사)은 빠져 있다.
- **관찰: 두 번째 재생에서 멈춤.** Firefox 하나에서 YouTube 재생을 두 번째로 시작하면, 두 번 다 자식 프로세스 하나가 SIGSEGV로 죽었다. 그리고 페이지 이동이 300 s 시간 초과로 끝났다(720p, 1080p 첫 시도). 화질마다 Firefox를 새로 띄워 쟀다. headless 모드에서만 나는지는 모른다.


**강의 자료 2판.** 28장(이 절의 MFC 경합 쪽 추가, X·MFC·정리 쪽 갱신)과 PDF를 2026-11-21 19:45:59에 메일로 보냈다(보낸편지함에서 첨부 2개 확인). 커밋 e3bc16e.
- 메일에 "micro-USB를 뺐다 꽂거나"라고 썼는데, usbhost 상태는 소프트웨어 설정이라 케이블로는 풀리지 않는다고 본다. 전원을 다시 넣어야 한다.

**X 화면 확인(21:10~, 사용자가 보드3 터미널에서 sudo).**
- 사용자가 ssh(philip)로 `sudo apt-get install -y --no-install-recommends kbd xserver-xorg-legacy`를 했다. `Xorg.wrap`이 `-rwsr-sr-x`로 생겼다.
- `e850-desktop start`는 `/usr/local/bin/e850-desktop: Permission denied`로 실패했다.
  - 원인: 저장소의 `desktop.sh`, `session.sh`, `usbhost.sh`, `audio.sh`가 실행 권한(git mode 100644) 없이 들어가 있었다. 셋은 심볼릭 링크(`e850-desktop`, `e850-usbhost`, `e850-audio`)로, `session.sh`는 xinit이 직접 실행한다.
  - `/opt/e850-features`의 소유자도 root가 아니었다(묶음을 푼 사용자, 보드3은 Mac의 uid 502). root로 도는 서비스가 일반 사용자가 고칠 수 있는 스크립트를 실행하는 셈이다.
- 수정(저장소):
  - 네 파일을 755로 바꿨다.
  - `e850-feature`의 `deploy_self`가 복사한 뒤 `chown -R root:root`, `chmod 755 */*.sh`를 한다.
- 보드3에는 사용자가 같은 것을 손으로 했다(`chown -R root:root /opt/e850-features && chmod 755 /opt/e850-features/*/*.sh`). 그다음 `sudo systemctl start e850-desktop`으로 켰다.
- 결과(관찰): Xorg가 root로(`Xorg :0 vt7 -keeptty -nolisten tcp`), Openbox, LXTerminal이 떴다. 패닉 0회.
  - `fbshot`으로 HDMI 프레임버퍼에 바탕(#24323f)과 터미널 창이 그려진 것을 확인했다.
  - Xorg 로그의 (WW)는 없는 글꼴 디렉터리, fbdev 옛 탐색, VGA arbiter뿐이다. (EE)는 없다.
- X 안의 Firefox(창 모드, 플레이어는 페이지 안 기본 크기)로 YouTube를 쟀다. 화질마다 Firefox를 새로 띄웠다.

| 화질 | 프레임 | 드롭 | 보인 fps | CPU | headless 때 |
|---|---|---|---|---|---|
| 480p30 | 908 | 11 (1%) | 29.9 | 31% | 드롭 0, 35% |
| 720p60 | 1828 | 150 (8%) | 55.9 | 55% | 드롭 0.5%, 46% |

- 720p 재생 중 `top`: RDD(디코더) 프로세스 156%, Firefox 본체 111%, Xorg 22%, 페이지 프로세스 22%.
- 판단:
  - 화면까지 그리면 480p는 제 속도다.
  - 720p60은 8%가 빠진다. GPU 합성이 없어 프레임 복사가 모두 CPU 몫이다.
  - 1080p는 X 안에서 재지 않았다(headless에서도 13% 드롭).
- X는 켜 둔 상태로 두었다(usbhost가 켜져 ACM은 없다). 끄려면 보드에서 `e850-desktop stop`(sudo).

**남은 것.**
- 보드4: 케이블을 옮겨 boot-nocpu를 굽고, `last_kmsg`로 원인을 확인한다. 그다음 묶음(고친 MFC와 패키지 목록)을 다시 배포한다.
- 보드3의 ffmpeg는 시험용으로 깐 것이다.

### 11.82 GPU: vendor kbase 위에서 오픈소스 Mesa(panfrost)로 그리기 (2026-11-22, 보드3)

사용자 지시(2026-11-22 05시): "보드4는 저녁 6시에 정리할 테니 3에 집중해, GPU를 우선 살려".

**출발점(관찰).**
- 커널 드라이버는 이미 있다. 보드3에서 `mali_kbase`가 올라와 있고 `/dev/mali0`(crw-rw-rw-), `gpuinfo` = `Mali-G52 1 cores r0p0 0x7212`.
  - `modinfo`: `r38p1-01eac0 (UK version 11.35)`. 소스는 `drivers/gpu/arm/b_r38p1`, uapi는 `include/uapi/gpu/arm/b_r38p1`.
  - DVFS 표는 1001/865/754/598/433/377 MHz.
- 없는 것은 glibc용 사용자 공간 GL 드라이버다. Mali 라이브러리는 Android(bionic)용뿐이다.
- DT의 GPU 노드(`/sys/firmware/devicetree/base/mali@11500000`)에 `dma-coherent`가 없다. GPU와 CPU 캐시가 일관되지 않다.

**방법: Mesa panfrost + kbase drm-shim.**
- Icecream95의 `kbase-g52` 브랜치(gitlab.freedesktop.org/icecream95/mesa, 커밋 `87d63f238f`, 2022-06, Mesa 22.2-devel, MIT)를 쓴다.
  - Mesa의 `drm-shim`(LD_PRELOAD)이 가짜 `/dev/dri/renderD128`을 만든다. panfrost ioctl을 kbase ioctl로 바꾼다.
  - 커널은 바꾸지 않는다. root도 필요 없다.
- 처음 찾은 panfork(gitlab.com/panfork/mesa)는 저장소가 비워져 있었다(README만 남음).
- 브랜치가 쓰는 ioctl을 커널 uapi와 컴파일러로 대조했다(`tools/gpu/abi-check.c`).
  - 대조한 것: VERSION_CHECK, SET_FLAGS, JOB_SUBMIT, GET_GPUPROPS, MEM_ALLOC, MEM_FREE, MEM_SYNC, atom/event 크기, 메모리 플래그. 모두 같았다.
  - MEM_IMPORT만 다르다(shim 40바이트, 커널 24바이트). shim은 쓰지 않는다. dma-buf를 가져오려면 고쳐야 한다.
- 빌드: Mac Docker의 arm64 Debian 12 이미지 `e850-mesa-build:1`.
  - 옵션: gallium panfrost만, LLVM 없음, EGL/GLES2/GBM, x11 플랫폼.
  - 결과는 `mesa-kbase.tgz` 5.5 MB다. 보드에서는 `~/gpu/`에 풀어 일반 사용자로 쓴다.

**고친 것(시험 순서, 모두 `tools/gpu/mesa-kbase-g52-e850.patch`).**

| # | 증상(관찰) | 원인 | 고침 |
|---|---|---|---|
| 1 | `eglInitialize` 실패. panfrost를 시도하지 않고 swrast를 찾음. `drmGetDevices2` = 0개, 직접 open은 됨(드라이버 panfrost 1.1) | 브랜치가 `HAS_XSTAT 1`을 고정했다. glibc 2.33부터 `stat`/`stat64`가 실제 심볼이다. Debian 12 libdrm은 `stat64@GLIBC_2.33`을 부르므로 가로채지 못했다 | glibc 버전으로 고른다(`drm_shim.c`) |
| 2 | panfrost가 올라온 뒤 Segmentation fault. 앞에 `kinstr_prfcnt_setup: Invalid argument` | 개발용 성능 카운터 덤프가 설정 실패를 무시하고 `MAP_FAILED` 영역을 읽었다 | `PAN_KBASE_PERF=1`일 때만 쓴다(`perf.c`) |
| 3 | 모든 atom이 실패. UART에 `DATA_INVALID_FAULT`, `TILE_RANGE_FAULT`, `Unhandled Page fault in AS0 at VA 0x0`, 그 뒤 `JOB_READ_FAULT` | 메모리를 `CACHED_CPU`로 잡았는데 GPU가 일관되지 않고 Mesa는 캐시를 비우지 않는다. GPU가 낡은 설명자를 읽은 것으로 판단한다 | `CACHED_CPU`·`COHERENT_LOCAL`을 뺀다(write-combine). 큰 이미지 BO만 6번에서 다시 캐시로 잡고 shim이 직접 캐시를 비운다 |
| 4 | 오류 없이 끝나지만 도형이 지나간 타일이 0(투명 검정)이다 | 렌더 타깃이 `AFBC Tiled`였다. shim이 AFBC_FEATURES=0("지원")을 고정으로 돌려준다. 이 G52에서 AFBC 쓰기는 0이 된다(`PAN_MESA_DEBUG=noafbc`이면 그려짐) | 기본은 "AFBC 없음"(`PAN_KBASE_AFBC=1`이면 다시 켠다) |
| 5 | 그리기마다 꼭짓점 위치가 바로 앞 그리기의 것이다(첫 그리기는 0이라 안 보임. 7번 삼각형 자리에 6번 사각형의 앞 세 꼭짓점이 그려짐) | IDVS(위치 셰이더+타일러를 한 작업으로) 때 타일러가 새 위치를 기다리지 않는다. 원인(r0p0 하드웨어인지 kbase 설정인지)은 모른다 | Bifrost(`gpu_id < 0x9000`)에서 IDVS를 끈다(`bifrost_compile.c`, `BIFROST_IDVS=1`이면 켬) |
| 6 | X 창에 보이려고 매 프레임 `glReadPixels`를 하니 1280×720 한 장 읽기가 21 ms(`PAN_MESA_DEBUG=linear`), 113 ms(기본 tiled) | 3번 때문에 모든 메모리가 캐시 없음이다. 캐시 없는 메모리를 CPU가 읽으면 느리다 | 1~64 MB인 NOEXEC BO(이미지·버퍼)만 `CACHED_CPU`로 잡는다. 그 BO를 목록에 넣은 제출 전에 clean(`MEM_SYNC` to device), `WAIT_BO`에서 invalidate(to CPU)한다. Mesa는 GPU가 쓴 BO를 CPU가 만지기 전에 `WAIT_BO`를 부른다. `PAN_KBASE_UNCACHED=1`이면 끈다. `PAN_KBASE_STATS=1`이면 캐시 정리에 쓴 시간을 찍는다 |

- 5번을 가리려고 먼저 해 본 실험(모두 결과 같음, 원인 아님):
  - 타일러 힙을 미리 0/16/128 MB 잡아 둠
  - `PAN_MESA_DEBUG=nocache`/`sync`/`dirty`
  - 제출 전 `dsb sy`
  - `CACHED_CPU`와 Mesa의 `pandecode_mem_sync`
  - atom `core_req` = CS|T(6), CS|T|CF(14)
  - 이 실험 스위치들은 패치에서 뺐다.
- `PAN_MESA_DEBUG=trace`의 설명자 덤프(pandecode)로 색 마스크 15, `AFBC Tiled`, 작업 종류 `Indexed Vertex`(IDVS), 위치 버퍼 재사용(`0x7fa2a8e000`)을 확인했다.
- 6번에서 거친 것:
  - 처음에는 1 MB 이상이면 모두 캐시로 잡았다. 읽기는 21 → 1.5 ms로 줄었지만 그리기가 5 → 28 ms로 늘었다. `PAN_KBASE_STATS=1`로 재니 제출마다 128 MB BO를 clean하고 있었다(1080p 시험 100장에 29 GB, 2.4초).
    - 그 BO는 타일러 힙이다. GPU만 쓰는데 `PANFROST_BO_HEAP` 없이 오고 CPU에도 매핑된다. 이 브랜치의 `pan_bo.c`가 BO 플래그를 `PAN_BO_EXECUTE`만 남기고 지우기 때문이다(`INVISIBLE`, `GROWABLE`도 사라짐).
    - 그래서 64 MB 이하로 제한했다. Mesa 쪽 플래그 처리는 바꾸지 않았다(왜 지우는지 모름).
  - shim에서 BO를 찾은 뒤 `drm_shim_bo_put`으로 놓았더니 double free·힙 손상이 났다. 이 함수는 참조 수가 0이 아닐 때 해제한다(뒤집힌 조건). 참조 수만 줄인다(`p_atomic_dec`).

**결과(보드3, 관찰).**
- `tools/gpu/run-test.sh`(일반 사용자, 화면 없음, EGL surfaceless, GLES2):
  - `GL_RENDERER Mali-G52 (Panfrost)`, `OpenGL ES 3.1 Mesa 22.2.0-devel`
  - 그리기 7가지(삼각형/사각형 × 고정색/varying, 반복)를 읽어 비교하니 모두 맞다(PASS)
  - 1080p 전체 화면 셰이더(픽셀마다 sin·cos)가 300장에 0.92초, **322~334 fps(668~693 Mpix/s)**. 마지막 화면 가운데 값 122,128,128은 계산값 120.5,128,128과 fp16 오차 안이다.
  - 6번(캐시 BO)을 넣은 뒤 기본값은 **103 fps**다. 한 장마다 두 번 제출(정점·타일러, 프래그먼트)하고, 그때마다 1080p 렌더 타깃 8.4 MB와 1 MB 버퍼를 clean한다. 300장에 5.5 GB, 1.29초(장당 4.3 ms)가 든다. `PAN_KBASE_UNCACHED=1`이면 322 fps로 돌아간다.
  - 캐시 판에서도 tiled·linear 모두 PASS.
  - GPU `time_in_state`에 1001 MHz가 잡혔다(DVFS 동작).
- 시험 중 UART에 새 GPU 오류는 없다(3번을 고친 뒤). 보드는 멈추지 않았다.
- 저장소의 `tools/gpu/build.sh`로 빈 디렉터리에서 클론·패치·빌드한 판(캐시 BO 포함)도 보드3에서 PASS였다. 1080p 셰이더는 99 fps(`PAN_KBASE_UNCACHED=1`이면 321 fps), gles-x11 tiled는 42~43 fps였다.

**HDMI에 GPU 화면 보이기(사용자가 고른 1번: 읽어서 X 창에 그리기, root 없음).**
- 사용자가 고른 1번 방법은 GPU가 그린 것을 CPU로 읽어 X 창에 그리는 것이다. 2번(X·Firefox를 GPU로 가속, KMS + dma-buf + DRI3)은 미뤘다.
- `tools/gpu/gles-x11.c`의 동작:
  - 조명을 받고 띠 무늬가 돌아가는 토러스(삼각형 9216개)를 FBO(RGBA 텍스처 + DEPTH16)에 그린다.
  - 매 프레임 `glReadPixels`로 `XImage` 버퍼에 바로 읽고 `XPutImage`로 창에 보낸다.
  - 프래그먼트 셰이더가 BGRA로 쓰고 정점 셰이더가 y를 뒤집는다. 그래서 읽은 바이트를 그대로 X에 넘긴다.
  - 초마다 fps와 그리기·읽기·보내기 시간을 찍는다. 창 제목에도 fps를 보인다. 사용법은 `gles-x11 [w h 초]`.
- MIT-SHM은 쓰지 못한다. 이 커널에 SysV IPC가 없어 `shmget`이 실패하고 `XShmAttach`가 BadAccess를 낸다. 그래서 한 장(1280×720, 3.7 MB)마다 X 소켓으로 보낸다.
- 보드3에서 1280×720으로 잰 값(X는 CPU로 그림). 단위는 ms, fps는 초마다 찍힌 값의 범위다.

| 판 | fps | 그리기 | 읽기 | X로 보내기 |
|---|---|---|---|---|
| 캐시 없음, 기본(tiled) | 7.7 | 재지 않음 | 113 | 재지 않음 |
| 캐시 없음, `PAN_MESA_DEBUG=linear` | 26~27 | 5 | 21 | 11 |
| 1 MB 이상 모두 캐시(타일러 힙 포함), linear | 24~25 | 28~29 | 1.5 | 10~11 |
| 1~64 MB 캐시, 기본(tiled) | 42~43 | 6.2~6.5 | 6.6~6.8 | 10.4~10.7 |
| 1~64 MB 캐시, linear | 45~54 | 6.1~8.1 | 2.0~2.3 | 10.4~11.9 |

- HDMI 화면 캡처(`/var/tmp/fbshot.py`, Mac `~/e850-gpu/shot2.png`)에서 X 창 안의 토러스가 색과 조명 모두 맞게 보였다. linear 판은 20초 동안 988장을 그렸다.
- 지금 가장 큰 몫은 X로 보내기(10~12 ms)다.

**보드에 바꾼 것.** `/home/philip/gpu/`(Mesa 판, 시험 프로그램, 스크립트)만 넣었다. 시스템 파일은 바꾸지 않았다. 지우면 원래대로 돌아간다.

**진행 방식.** 처음에 Claude가 ssh로 시험을 돌리려던 명령이 Claude Code 자동 권한 검사에서 거부됐다("Undermining Oversight"). 그래서 몇 번은 사용자가 보드3에서 스크립트를 돌리고 출력을 붙여 주었다. 사용자가 "직접 해도 된다"고 한 뒤로는 Claude가 ssh로 돌렸다.

**아직 안 되는 것(다음).**
- 화면에 바로 그리지 못한다. X나 Firefox를 가속하려면 다음이 필요하다.
  - DPU용 KMS 장치. 지금은 fbdev뿐이라 `/dev/dri`가 없다.
  - shim의 dma-buf 가져오기·내보내기(MEM_IMPORT 구조체 수정)
  - X의 DRI3
- shim의 제출은 동기식이다(atom 하나를 넣고 끝날 때까지 기다림). 의존성, syncobj, 여러 컨텍스트 동시 사용은 아직 없다.
- 캐시 BO에는 알려진 빈틈이 있다. fence를 기다린 뒤 동기화 없이 매핑해 읽으면 `WAIT_BO`를 거치지 않는다. 이때는 낡은 캐시 줄을 볼 수 있다. 그런 앱은 `PAN_KBASE_UNCACHED=1`로 돌리거나 syncobj 대기에서도 invalidate하게 고쳐야 한다.
- shim은 kbase 메모리를 풀지 않는다(`driver_bo_free`가 없어 `MEM_FREE`를 부르지 않음). Mesa BO 캐시가 재사용하므로 gles-x11 988장은 문제없었다. 오래 돌거나 크기를 자주 바꾸는 앱에서 메모리가 느는지는 재지 않았다.
- X로 보내기를 줄이려면 fb0에 바로 쓰거나(X 없이) 2번(KMS·DRI3)으로 가야 한다.
- AFBC와 IDVS가 왜 안 되는지는 모른다. 이 보드의 Android 판 blob이 AFBC와 IDVS를 쓰는지 확인하지 않았다.

### 11.83 GPU 2번 방법(X·Firefox 가속) 조사와 설계안 (2026-11-22, 보드3)

사용자 지시(2026-11-22): 2번 방법을 시작한다. 먼저 vendor dpu 드라이버가 DRM인지 fbdev인지 조사하고, 할 수 있는 길과 어려움을 정리한 설계안을 낸다. 사용자가 고르기 전에는 보드에 커널 모듈이나 부트 이미지를 넣지 않는다.

**조사 결과(관찰).** 커널 소스(`e850-kernel`), 빌드 결과(`e850-out`), 보드3을 읽기만 했다.
- dpu는 **fbdev 드라이버**다(`drivers/video/fbdev/exynos/dpu30`, `depends on FB`). DRM 코드는 없고 `/dev/dri`도 없다. mainline `exynos_drm`은 Exynos850 DECON을 지원하지 않는다.
- 다만 Android HWC가 쓰던 `S3CFB_WIN_CONFIG` ioctl이 있다. 이 ioctl은 KMS의 atomic commit과 비슷하다.
  - 창(window)마다 dma-buf fd와 acquire fence를 받는다. 창은 4개다(`MAX_DECON_WIN`).
  - 커널은 `dma_buf_get` → DSIM 장치(IOMMU)로 매핑해서 그 버퍼를 바로 화면에 내보낸다(`decon_import_buffer`).
  - 돌려주는 값은 retire fence다.
  - `decon_set_win_config`는 static이고, `decon_drvdata`만 EXPORT되어 있다.
- fb0는 1920×1080, 32 bpp, stride 7680이다. 메모리는 dma-heap `system-uncached`에서 잡는다(`decon_fb_alloc_memory`).
  - `decon_pan_display`는 `info->fix.smem_start`(DPU IOMMU 주소)를 그대로 DPP 주소로 쓴다.
- 커널(GKI)에는 DRM이 이미 들어 있다. `CONFIG_DRM`, `DRM_KMS_HELPER`, `DRM_GEM_CMA_HELPER`, `DRM_GEM_SHMEM_HELPER`가 모두 `=y`다.
  - `Module.symvers`에서 확인한 EXPORT: `drm_simple_display_pipe_init`, `drm_gem_cma_dumb_create`, `drm_gem_cma_prime_get_sg_table`, `drm_gem_prime_export`, `drm_gem_fb_create`, `drm_gem_shmem_*`, `fb_pan_display`, `registered_fb`.
  - `TRIM_UNUSED_KSYMS`가 없다. 그래서 외부 모듈로 KMS 드라이버를 만들 수 있다. 부트 이미지는 바꾸지 않아도 된다.
- CMA는 236 MB이고 그중 177 MB가 비어 있다. `CONFIG_UDMABUF`와 `DRM_VKMS`는 없다.
- `/dev/dma_heap/*`는 root 0600이다. `/dev/fb0`는 root:video 0660이고, philip은 video 그룹이다.
- `dpu.ko`와 `mali_kbase.ko`는 initramfs에서 자동으로 올리지 않는다(`autoload.deny`). rootfs의 `e850-hdmi.service`가 `modprobe dpu`를 한다.
- kbase r38p1:
  - `KBASE_IOCTL_MEM_IMPORT`는 24바이트 union이다(in: flags, phandle, type, padding / out: flags, gpu_va, va_pages). shim의 40바이트 구조체는 맞지 않는다(11.82).
  - UMM 가져오기는 CPU 매핑(`dma_buf_mmap`)을 지원한다. 캐시 동기화(`dma_buf_begin/end_cpu_access`, `BASE_MEM_IMPORT_SYNC_ON_MAP_UNMAP`)도 지원한다. `CONFIG_MALI_DMA_FENCE`는 꺼져 있다(암묵적 fence 없음).
- Mesa(kbase-g52 브랜치):
  - kmsro가 있다. 화면용 버퍼와 공유 버퍼(`PIPE_BIND_SCANOUT|SHARED`)는 KMS 장치의 dumb 버퍼로 잡고, PRIME으로 GPU에 가져온다(`renderonly_create_kms_dumb_buffer_for_resource`). 그래서 shim은 **가져오기만** 하면 된다. 내보내기는 처음에는 필요 없다.
  - pipe-loader는 모르는 KMS 이름이면 kmsro로 넘어간다.
  - DRI megadriver는 `__driDriverGetExtensions_<이름>` 진입점이 있는 이름만 연다. 목록에 `exynos`가 있다.
- 보드3 X:
  - `modesetting_drv.so`와 `libglamoregl.so`가 깔려 있다(xserver-xorg-core 21.1.7).
  - 지금 X는 `Xorg.wrap`(setuid, `needs_root_rights=yes`)로 root로 돈다. setuid를 거치므로 `LD_PRELOAD`(shim)가 넘어가지 않는다.
  - Firefox는 ESR 153이다.

**길(설계안).**

| 길 | 내용 | 커널 | root | X·Firefox 가속 | 어려움 |
|---|---|---|---|---|---|
| A | 외부 모듈로 작은 KMS 드라이버(`/dev/dri/card0`, DRM 이름 `exynos`)를 만든다. 그 위에 kmsro + Xorg modesetting/glamor(DRI3), Firefox EGL | 새 모듈 1개, `insmod`로만 | 모듈 적재, Xorg 실행 | 됨(목표) | 아래 A 항목 |
| B | 커널을 바꾸지 않는다. GPU가 dma-heap 버퍼에 그리고, `S3CFB_WIN_CONFIG`로 그 버퍼를 바로 화면에 낸다(복사 없음) | 없음 | dma-heap 권한(udev 규칙 1개) | 안 됨. 우리 전체 화면 앱만 | 화면을 독차지한다. fb0(X·콘솔) 내용을 같은 화면의 다른 창으로 넣을 수 없다(fb0의 dma-buf fd를 얻을 ioctl이 없음) |
| C | A의 커널·shim 위에 X 대신 Wayland(weston DRM + GL 렌더러, Firefox Wayland) | A와 같음 | A와 같음 | 됨(X가 아님) | Openbox 환경을 바꾼다. Debian 12 weston 10을 새로 깔아야 한다 |
| D | shim이 사용자 공간에서 가짜 KMS(`card0`)를 흉내 내고, 뒤에서 `S3CFB_WIN_CONFIG`를 부른다 | 없음 | Xorg 실행 | 이론상 됨 | 권하지 않음. 흉내 낼 ioctl이 많다(자원·커넥터·CRTC·dumb·flip·vblank 이벤트 fd). Xorg가 udev로 장치를 찾는 것도 속여야 한다 |
| E | Mesa EGL/X11을 고친다. DRI3 없이도 GPU로 그리고, 스왑할 때 읽어서 `XPutImage`로 보낸다(gles-x11 방식을 Mesa 안에) | 없음 | 없음 | Firefox만, X는 CPU 그대로 | 상한이 낮다. 720p 보내기만 10~12 ms(11.82)였다. 1080p는 약 2.25배로 추정한다(재지 않음) |

A의 단계와 어려움:
1. **shim**(커널 위험 없음):
   - `PRIME_FD_TO_HANDLE` → `MEM_IMPORT`(UMM, 24바이트)
   - 가져온 BO의 CPU 매핑
   - `MEM_FREE`(지금은 메모리를 풀지 않는다. X와 Firefox는 오래 돌고 크기가 바뀌는 버퍼가 많아 꼭 필요하다)
   - 실제 `/dev/dri`가 있을 때 `drmOpenWithType("panfrost")`가 shim의 가짜 renderD128을 찾는지 확인
2. **KMS 모듈 `e850-kms`**:
   - CRTC·plane·커넥터 하나씩, 1920×1080 고정, `drm_simple_display_pipe` + GEM CMA(dumb, PRIME).
   - 화면에 내는 방법은 둘이다.
     - (가) dpu.ko를 바꾸지 않는다. 버퍼를 DSIM 장치로 매핑해 DPU 주소를 얻는다. `lock_fb_info` 안에서 fb0의 `smem_start`를 그 주소로 바꿔 `fb_pan_display`를 부른다. 원래 주소로 다시 pan하면 콘솔이 돌아온다.
     - (나) dpu.ko 패치(`modules/e850-hdmi/apply.sh`)로 `decon_set_win_config`를 dma_buf 포인터로 부르는 EXPORT 함수를 넣는다. fence와 여러 창을 쓸 수 있다. 다만 `e850-hdmi`가 부팅마다 올리는 모듈을 바꾸게 된다.
     - (가)로 시작하기를 권한다.
   - 어려움:
     - 페이지 전환 완료 이벤트. DECON vsync 인터럽트를 바로 받을 길이 없어서 pan 뒤 vsync를 기다리거나 타이머로 대신한다.
     - 버퍼 수명. 화면이 다음 버퍼를 잡기 전에 앞 버퍼를 풀면 안 된다.
     - X가 끝나거나 VT를 바꿀 때 fbcon을 되돌려야 한다.
     - PSR/hibernation 상태(지금 설정은 꺼져 있다고 봄, 확인 필요).
   - 모듈은 부팅 때 올리지 않고 손으로 `insmod`한다. 그래서 멈추면 워치독 리셋 뒤 원래 상태로 부팅한다.
3. **Mesa**:
   - `-Dgallium-drivers=panfrost,kmsro`로 빌드한다. DRM 이름을 `exynos`로 하면 Mesa 코드는 바꾸지 않아도 된다(다른 이름이면 `target.c`에 진입점 한 줄).
4. **Xorg**:
   - `Xorg.wrap`을 거치지 않는다. systemd 유닛이 root로 `/usr/lib/xorg/Xorg`를 바로 띄우고 `LD_PRELOAD`·`LD_LIBRARY_PATH`를 준다. 세션은 philip으로 띄운다.
   - xorg.conf: `Driver "modesetting"`, `kmsdev /dev/dri/card0`, `AccelMethod "glamor"`.
   - 어려움:
     - glamor가 2022년 개발 브랜치 panfrost에서 맞게 그리는지(글꼴, RENDER 경로)
     - shim 제출이 동기식이라 생기는 속도 저하
     - vblank 없는 KMS에서 modesetting의 Present 동작(확인 필요)
5. **Firefox**:
   - 환경 변수로 shim을 준다. `gfx.webrender.all` 등으로 하드웨어 WebRender를 강제하고 `about:support`로 확인한다.
   - 어려움:
     - Firefox 153이 요구하는 GL/GLES 판(이 Mesa는 GL 3.1, GLES 3.1, 확인 안 함)
     - 차단 목록
     - 샌드박스된 자식 프로세스에도 shim이 실려 `/dev/mali0`을 열려 할 때의 동작
   - 비교 기준은 11.81의 CPU 수치다(X 안 YouTube 720p60 55.9 fps, 드롭 8%, CPU 55%).

**권하는 순서(Claude 의견).**
1. A의 1단계(shim 가져오기·해제)를 B의 시험 앱으로 먼저 검증한다.
   - 커널은 바꾸지 않는다. 필요한 root 작업은 dma-heap 권한 한 번이다.
   - GPU가 그린 1080p를 복사 없이 HDMI에 내면, dma-buf 가져오기·캐시·DECON 형식·fence를 KMS 모듈 전에 확인할 수 있다.
2. 그다음 A의 2~5단계를 한다.
- C는 A가 끝난 뒤 X 대신 고를 수 있다. D는 권하지 않는다. E는 커널 없이 Firefox만 조금 낫게 할 때의 대안이다.

**보드에 바꾼 것.** 없다. 보드3에서는 읽기 명령만 썼다(`ls /dev`, sysfs, `lsmod`, `/proc/meminfo`). Mac에서는 UART 기록기(`~/e850-logs/uart-board3.log`)를 띄웠다.

### 11.84 B단계: GPU가 fb0 메모리에 바로 그리기(복사 없음), shim의 dma-buf 가져오기와 메모리 해제 (2026-11-22, 보드3)

사용자 지시(2026-11-22): 11.83의 추천대로 B 다음 A로 간다.

**B를 바꾼 이유(관찰, DECON 소스).**
- 사용자 공간 `S3CFB_WIN_CONFIG`는 쓰지 않는다. `decon_update_regs`는 새 프레임을 올린 뒤 `decon_acquire_old_bufs`/`decon_release_old_bufs`로 앞 프레임 버퍼를 푼다(detach, unmap, `dma_buf_put`).
  - win_config를 한 번도 하지 않은 상태에서 "앞 프레임 버퍼"는 기본 창의 `dma_buf_data[0]`, 곧 fb0 메모리다.
  - 이 메모리를 잡고 있는 참조는 그것 하나뿐이다. 그래서 첫 win_config에서 fb0 메모리가 풀리고 DPU IOMMU 매핑도 사라진다. 그런데 fbcon은 남은 vmap(`screen_base`)으로 계속 쓴다.
  - Android에서는 fb0가 부팅 화면용이라 문제가 없었다. vendor에도 fb 버퍼를 따로 두는 `CONFIG_FB_TEST`(`fb_buf_data`) 판이 있지만 꺼져 있다.
- 11.83의 가정 하나를 고친다. `CONFIG_EXYNOS_HIBERNATION=y`, `CONFIG_EXYNOS_WINDOW_UPDATE=y`다. LT8912B는 DSI video 모드라 hibernation이 실제로 쓰이는지는 확인하지 않았다.
- 대신 쓴 방법:
  - 작은 모듈 `modules/e850-fbbuf`가 fb0 버퍼의 dma-buf fd만 꺼내 준다(`/dev/e850-fbbuf`, ioctl 하나). DECON 상태는 바꾸지 않고 참조를 하나 늘릴 뿐이다.
  - GPU는 그 fd를 가져와 fb0 메모리에 바로 그린다. 화면 전환은 fbcon과 같은 `FBIOPAN_DISPLAY`로 한다. A의 (가) 방식이 쓸 경로와 같다.
  - 모듈은 부팅 때 올리지 않는다. root로 `sh /var/tmp/e850-fbbuf/load.sh`를 실행한다.

**모듈에서 겪은 것.**
- 첫 판은 ioctl이 ENODEV였다. fb0 확인을 `info->fbops->fb_mmap == decon_mmap`으로 했기 때문이다. 이 커널은 `CONFIG_CFI_CLANG=y`라 두 모듈이 같은 함수의 주소를 서로 다른 점프 테이블 주소로 본다. 그래서 구조 검사(`win->fbinfo == info`, 버퍼 크기 ≥ `smem_len`)로 바꿨다.
- load.sh가 `chmod`한 뒤 udev가 새 노드에 기본 모드 0600을 다시 씌웠다(그룹은 video로 남음). 그래서 `udevadm settle` 뒤에 권한을 준다.

**shim 수정(`tools/gpu/mesa-kbase-g52-e850.patch`).**
- `DRM_IOCTL_PRIME_FD_TO_HANDLE`: kbase `MEM_IMPORT`(UMM, r38p1의 24바이트 union).
  - 64비트 프로세스는 SAME_VA 영역 cookie를 받는다(`kbase_mem_from_umm`의 `shared_zone`). 그 cookie를 mmap하면 GPU VA = CPU VA가 된다. CPU 매핑은 kbase가 `dma_buf_mmap`으로 만든다.
  - 같은 버퍼는 같은 handle을 돌려준다. 버퍼는 dma-buf inode 번호로 가린다. 이 번호는 `dma-buf.c:490`의 전용 카운터라 버퍼마다 다르다.
- `GET_BO_OFFSET`가 아무것도 안 하던 것을 BO의 GPU VA를 돌려주게 고쳤다(Mesa의 import가 쓴다).
- BO 해제(`driver_bo_free`):
  - 할당한 BO와 가져온 BO 모두 cookie에서 만든 매핑을 munmap한다. kbase는 그 매핑이 닫힐 때 영역을 푼다(`free_on_close`). 지금까지 shim은 GPU 메모리를 한 번도 풀지 않았다.
- 참조 수:
  - `drm_shim_bo_put`의 조건이 뒤집혀 있었다. 0이 되면 남기고 0이 아니면 풀었다.
  - `MMAP_BO`의 lookup은 참조를 돌려주지 않았다.
  - 그래서 handle이 참조 하나를 갖고, lookup마다 get/put하도록 바로잡았다.

**결과(보드3, 관찰).**
- 새 shim으로 회귀 시험:
  - `run-test.sh` PASS, 1080p 셰이더 99.6 fps(11.82와 같음)
  - `gles-x11` 1280×720 41~43 fps(같음)
- 해제 확인: 일회용 시험으로 텍스처를 크기를 바꿔 가며 200번 만들고·그리고·지웠다.
  - VmSize는 첫 20번 뒤 약 375 MB에서 더 늘지 않았다.
  - shim 로그에 해제 10건, 오류는 없었다.
- `tools/gpu/gles-fb`(일반 사용자, X가 떠 있는 상태):
  - fb0 dma-buf(16,588,800바이트 = 1920×2160×4)를 가져와 EGLImage(XRGB8888, pitch 7680)로 FBO에 붙이고 토러스를 그린다.

| 방식 | fps | 그리기 | pan |
|---|---|---|---|
| 한 장(보이는 화면에 바로, 찢김 있음) | 116~164 | 6.1~8.6 ms | 없음 |
| 두 장(`yres_virtual` 2160, `FBIOPAN_DISPLAY`로 전환) | 29.6~30.0 | 10.4~11.7 ms | 21.8~22.9 ms |

- fb0 캡처에서 색(주황·파랑 띠, 배경)과 위아래 방향이 맞았다. gles-x11 장면과 같다.
- 두 장 방식이 30 fps에서 멈추는 이유: `decon_pan_display`는 레지스터를 쓴 뒤 vsync와 shadow 갱신 완료를 기다린 다음에야 돌아온다(평균 22 ms). 그리기와 전환 대기가 겹치지 않아 한 장에 두 프레임 주기가 걸린다.
- 끝날 때 `yres_virtual`과 pan(0,0)을 되돌렸다. `xrefresh` 뒤 X 화면도 돌아왔다. 시험 동안 UART에 GPU·sysmmu·DECON 오류는 없었다.

**A에 주는 뜻.** pan 경로(DPU 주소만 바꿔 다시 그리기)는 동작한다. KMS 모듈의 페이지 전환은 pan을 worker에서 하고, 끝나면 vblank 이벤트를 보낸다. 그러면 X가 기다리는 동안 다음 프레임을 그릴 수 있다. 동기식으로 하면 30 fps가 상한이다.

**보드에 바꾼 것.**
- `e850-fbbuf` 모듈이 올라가 있다(다음 재부팅까지). `/dev/e850-fbbuf`는 root:video 0660이다(load.sh가 줌, 재부팅하면 없어짐). 파일은 `/var/tmp/e850-fbbuf/`에 있다.
- `~/gpu/`의 Mesa를 새 빌드로 바꿨다. 이전 판은 `~/gpu/mesa-kbase.1182`다.
- 시스템 파일은 바꾸지 않았다.

### 11.85 A단계(1): KMS 장치 `e850-kms`와 GPU 페이지 전환 (2026-11-22, 보드3)

**만든 것.**
- `modules/e850-kms`: vendor fbdev DECON 위에 얹은 작은 KMS 장치(`/dev/dri/card0`, DRM 이름 `exynos`).
  - CRTC 하나, XRGB8888 primary plane 하나, HDMI 커넥터 하나(fb0 모드 1920×1080만).
  - 버퍼는 GEM CMA 헬퍼로 DSIM 장치에서 잡는다. DPU에서 IOMMU가 있는 장치는 DSIM 하나라 `paddr`가 곧 DPU 주소다. 적재할 때 DSIM의 IOMMU 도메인이 DMA 형인지 확인한다.
  - 화면 전환은 fb0의 `smem_start`를 잠깐 DRM 버퍼 주소로 바꾸고 `decon_pan_display`를 부른다(11.84에서 확인한 pan 경로). 돌아오면 vblank 이벤트를 보낸다.
  - DRM master가 빠지거나 pipe가 꺼지면 fb0 버퍼를 다시 보인다. 텍스트 콘솔이나 fbdev X가 돌아온다.
  - Mesa DRI megadriver에 `exynos` 이름의 kmsro 진입점이 이미 있어서 이 이름을 썼다.
- Mesa를 `-Dgallium-drivers=panfrost,kmsro`로 다시 빌드했다(`tools/gpu/build-mesa.sh`). kmsro는 화면용 버퍼를 KMS 장치의 dumb 버퍼로 잡고 panfrost(shim)로 가져온다. 그래서 shim에는 가져오기(11.84)만 있으면 된다. 회귀: `run-test` 115 fps, `gles-fb` 한 장 167 fps.
- `tools/gpu/gles-kms.c`: `dumb`(커널만: dumb 버퍼 두 장, 색 막대와 움직이는 흰 사각형, 매 프레임 페이지 전환)와 기본(GBM + EGL 창 표면에 토러스, kmscube 방식). 장면은 `tools/gpu/torus.h`로 gles-x11·gles-fb와 함께 쓴다.

**결과(보드3, 관찰).** 사용자가 root로 `sh /var/tmp/e850-kms/load.sh`를 했다. X(fbdev)와 Firefox가 떠 있는 상태에서 philip으로 돌렸다.
- 적재: `[drm] Initialized exynos 1.0.0 20261122 for 13070000.dsim on minor 0`, card0는 root:video 0660.

| 시험 | fps | 그리기 | 전환 | 화면(사용자 확인) |
|---|---|---|---|---|
| `gles-kms dumb 5` | 30.0 | 0.1 ms | 33.0~33.3 ms | 색 막대 8개와 움직이는 흰 사각형, 끝난 뒤 X 데스크톱으로 돌아옴 |
| `gles-kms 10`(Mali, GBM) | 30.0 | 8.3~10.5 ms | 22.8~25.0 ms | 주황·파랑 띠가 도는 토러스, 끝난 뒤 X 데스크톱으로 돌아옴 |

- GPU 시험: `Mali-G52 (Panfrost), OpenGL ES 3.1 Mesa 22.2.0-devel, gbm drm`, 300프레임(9,216 삼각형). kmsro가 card0 dumb 버퍼를 panfrost로 가져와 그렸다.
- 두 시험 동안 dmesg에 DRM·DECON·sysmmu 오류가 없다. 같은 때 찍힌 `v4l_querycap` WARNING은 사용자가 띄운 Firefox(PID 185165)가 V4L2 장치를 살피며 낸 것이다(11.81과 같음).

**30 fps 상한(관찰과 가설).**
- [관찰] 그리기가 0.1 ms인 dumb 시험도 전환 하나에 33 ms가 걸린다. 그리기 + 전환은 어느 시험이든 33 ms로 맞춰진다. 11.84에서 "그리기와 대기가 겹치지 않아서"라고 본 것은 틀렸다. pan 자체가 연달아 부르면 두 프레임 주기를 쓴다.
- [소스] `decon_pan_display`(decon_dsi.c)는 DPP 설정 → `decon_reg_update_req_window` → `decon_reg_start` 다음 `decon_wait_for_vsync`, 이어서 `decon_reg_wait_update_done_and_mask`(shadow 갱신 완료)를 기다린다.
- [가설] vsync 대기와 shadow 갱신 대기가 서로 다른 vsync에 걸려 두 주기가 된다. 측정하지 않았다. 그래서 nonblocking commit으로 바꿔도 전환은 1초에 30번이 상한이다. 60 fps를 내려면 e850-kms가 pan 순서를 직접 하면서 vsync 대기를 빼야 한다(dpu.ko의 `decon_reg_*`가 EXPORT되어 있는지 먼저 확인).

**보드에 바꾼 것.**
- `e850-kms` 모듈이 올라가 있다(다음 재부팅까지, 부팅 때 올리지 않음). 파일은 `/var/tmp/e850-kms/`.
- `~/gpu/`에 kmsro를 넣은 Mesa와 `gles-kms`. 이전 판은 `~/gpu/mesa-kbase.1182`.
- 데스크톱(사용자 요청): `~/.local/bin/xbg`와 `~/.local/share/wallpaper.bgrx`(오로라 배경, `tools/board/wallpaper/`), `~/.config/lxterminal/lxterminal.conf` 글꼴 `Monospace 16`. 사용자가 root로 `/opt/e850-features/desktop/session.sh`의 `xsetroot -solid '#24323f'` 줄을 `$HOME/.local/bin/xbg … || xsetroot -solid black`으로 바꿨다(저장소 `features/desktop/session.sh`는 그대로).
- HDMI가 한때 안 나왔다(사용자 보고). 그때 DECON·DSIM·DPP 인터럽트는 1초에 61번씩 늘고 있었고 커널 오류도 없었다. 사용자가 모니터 쪽을 확인한 뒤 화면이 나왔다. 어느 조치가 통했는지는 모른다.
- 참고: `/dev/fb0`는 `read()`가 0바이트를 돌려준다. 화면 캡처는 mmap으로 한다.

### 11.86 A단계(2): GPU X(modesetting + glamor + DRI3)와 Firefox 하드웨어 WebRender (2026-11-22, 보드3)

**구성.** 평소 데스크톱(vt7, fbdev X)은 그대로 두고, vt8에 두 번째 X를 띄운다.
- `modules/e850-kms/x-kms.sh`(root): `Xorg :1 vt8 -noreset`, 설정은 `xorg-kms.conf`(modesetting, `kmsdev /dev/dri/card0`, `AccelMethod glamor`)만 쓴다. 서버에 `~/gpu/mesa-kbase`와 kbase shim을 `LD_PRELOAD`한다. 그래서 setuid `Xorg.wrap`이 아니라 Xorg를 직접 실행한다. X 인증은 없고 TCP는 닫았다(시험용). Openbox, LXTerminal, 배경(`xbg`)을 philip으로 띄운다.
- 클라이언트는 `modules/e850-kms/kenv 프로그램…`으로 같은 Mesa 환경에서 실행한다(DISPLAY=:1).
- Ctrl+Alt+F7/F8로 오간다. GPU X가 VT를 놓으면 `e850-kms`의 `master_drop`이 fb0를 다시 보인다(사용자가 화면으로 확인).
- `tools/gpu/gles-x11.c`에 `egl` 모드를 넣었다. EGL 창 표면으로 X 창에 바로 그린다(DRI3 + Present, 복사 없음).

**겪은 문제와 고친 것.**
1. [관찰] 첫 시작: `glamor: Ignoring GL < 2.1, falling back to GLES.` 다음 `glGetString() returned NULL, your GL is broken`, glamor 실패, ShadowFB(소프트웨어)로 떴다. KMS 장치 위에 X 화면은 정상으로 나왔다.
   - [가설] glamor는 GL 함수를 libepoxy로 찾는다. 우리 Mesa에 libGL이 없어서 epoxy가 시스템 glvnd `libGL.so.1`을 열었고, 그 함수들은 이 Mesa를 모르니 NULL을 돌려준다. epoxy는 한 번 찾은 함수를 기억하므로 GLES로 넘어가도 같은 함수를 부른다. epoxy 소스로 확인하지는 않았다.
   - 고침: Mesa를 `-Dglx=dri`로 빌드해 libGL을 함께 넣었다(`tools/gpu/build-mesa.sh`). 결과: `glamor X acceleration enabled on Mali-G52 (Panfrost)`, desktop GL로 켜졌다. DRI3, Present, GLX 확장도 올라왔다(`Disable "glx"`는 듣지 않았다).
2. [관찰] 서버가 시작 직후 한 번 리셋됐다. 첫 클라이언트 `xbg`가 끝나며 연결이 0개가 됐기 때문이다. 배경이 사라졌다. 고침: `-noreset`.
3. [관찰] EGL 창 시험이 `X connection to :1 broken`으로 끝났다. 처음 한 번은 X 오류(DRI3 `PixmapFromBuffers` 실패)와 함께 Xorg 프로세스에 `DATA_INVALID_FAULT`(0x58) GPU 폴트 52줄이 났다.
   - Mesa 로그: `DRM_SHIM: unhandled core DRM ioctl 0x2D`(`PRIME_HANDLE_TO_FD`). 이 Mesa(22.2) panfrost는 SCANOUT만 kmsro(card0 dumb)에 잡고, SHARED(DRI3 창 버퍼)는 kbase 메모리에 잡은 뒤 shim에 내보내기를 시킨다. kbase는 자기 메모리를 dma-buf로 내보내지 못한다. 11.84의 "shim은 가져오기만 있으면 된다"는 틀렸다.
   - 고침(`tools/gpu/mesa-kbase-g52-e850.patch`):
     - `pan_resource.c`: KMS 장치가 있으면 SHARED도 kmsro dumb 버퍼로 잡는다.
     - shim: 가져온 BO는 dma-buf fd의 복제를 갖고 있다가 `PRIME_HANDLE_TO_FD`에 그 복제를 준다. kbase 할당 BO는 EINVAL(shortcut 주석: DRI3 `BufferFromPixmap` 같은 경우는 아직 안 됨).
   - 고친 클라이언트로도 옛 서버에서는 연결이 끊겼다. 클라이언트 strace: `PixmapFromBuffers`를 fd 2개와 보낸 직후 `recvmsg`가 0(서버가 끊음). 서버 fd 한도(1024, 사용 64)와 SELinux(permissive, 거부 기록 없음)는 원인이 아니었다.
   - 서버를 새 Mesa로 다시 띄우자 됐다. 옛 서버가 왜 끊었는지는 확정하지 못했다. 후보: 옛 서버 Mesa에 내보내기가 없었던 것, 또는 3번 첫 시도의 GPU 폴트로 서버의 GPU 상태가 망가진 것.
   - strace는 Debian 12 arm64 패키지에서 꺼내 `~/.local/bin/strace`에 두었다(설치 아님). `x-kms.sh`는 `STRACE=…`로 서버를 추적할 수 있다.
4. [관찰] GPU X의 Firefox가 시작하자마자 SIGSEGV(주소 0)로 죽었다. 충돌 기록: libxul → shim `realpath` → libc. drm-shim의 `realpath`가 `realpath(장치경로, NULL)`(결과를 할당해야 함)에서 NULL에 `strcpy`했다. 고침: NULL 버퍼면 `strdup`, NULL 경로는 libc로 넘긴다.

**결과: GPU가 X 창에 바로 그리기(관찰, 사용자가 화면 확인 "매우 잘 됨").**

| 방식(GPU X) | fps | 그리기 | 넘기기 |
|---|---|---|---|
| EGL 창 640×360 | 346~371 | 1.2 ms | swap 1.5~1.7 ms |
| EGL 창 1280×720 | 78~92 | 6.5~7.4 ms | swap 4.3~5.5 ms |
| EGL 창 1920×1080 | 73~74 | 7.1~8.0 ms | swap 5.5~6.4 ms |
| 복사(`glReadPixels` + `XPutImage`) 1280×720 | 27~29 | 5~6 ms | readback 6 ms + put 23~25 ms |

- 앞의 측정 일부는 X에 strace가 붙은 상태였다. 같은 720p 복사 방식이 fbdev X(:0)에서는 42 fps다.
- KMS에 vblank가 없어서 Present가 화면 주기를 기다리지 않는다. 그래서 60 fps를 넘고, 찢어짐이 생길 수 있다. GPU 폴트와 커널 오류는 없었다.

**Firefox(ESR 153.7) on GPU X.**
- `about:support`(chrome 문맥의 `Troubleshoot.snapshot()`, `/var/tmp/ff-support.py`, `-remote-allow-system-access` 필요):
  - `WEBRENDER available`, `OPENGL_COMPOSITING available`, `X11_EGL available`, `DMABUF available`. 어댑터 `Mali-G52 (Panfrost)`, 드라이버 `mesa/exynos 22.2.0`.
  - WebGL 1·2: `Panfrost -- Mali-G52`, OpenGL ES 3.1.
  - 꺼진 것: `WEBRENDER_COMPOSITOR`, 하드웨어 동영상 디코딩(blocklist), WebGPU, `MESA_THREADING`("No glthread with EGL and X11").
- YouTube Big Buck Bunny 720p60, 30 s(`/var/tmp/ff-yt.py`, 11.81과 같은 방법, Firefox를 매번 새로 띄움). YouTube는 이 화질을 AV1로 보낸다(dav1d).

| 경로 | 보인 fps | 드롭 | CPU(8코어 기준) |
|---|---|---|---|
| :0 fbdev X, 소프트웨어 WebRender(오늘 기준) | 58.3 | 74 (4%) | 58% |
| :1 GPU X(서버에 strace) | 43.1 | 517 (29%) | 56% |
| :1 GPU X | 42.7, 39.8, 38.6, 39.0, 41.1 | 30~37% | 61~62% |
| :1 GPU X, `PAN_KBASE_UNCACHED=1` | 35.2 | 769 (42%) | 61% |
| :1 GPU X, GPU 클럭 1001 MHz 고정 | 45.6 | 458 (25%) | 63% |

- 스레드별 CPU(5 s 동안 `/proc/*/task/*/stat`, `/var/tmp/thrcpu.py`): 페이지 메인 스레드 82%, Firefox Renderer 46%, DOM Worker 40%, dav1d 4개 각 35%, Firefox 메인 20%. 한 코어를 채운 스레드는 없었다. 클럭을 고정한 시험에서는 페이지 메인 스레드가 104%였다.
  - `top -b -n1`의 스레드 %는 측정 구간이 짧아 믿을 수 없었다(메인 스레드가 99.9%로 나왔지만 `/proc/…/syscall` 표본 200개 중 69%가 `ppoll` 대기).
- GPU DVFS(`/sys/kernel/gpu`): Mali-G52 1코어, 377~1001 MHz, Interactive. 재생 중 클럭은 대부분 377~433 MHz였고, 순간 사용률은 0%와 100%를 오갔다. 1001 MHz로 고정하면 순간 사용률이 대부분 0%였다. 그래서 GPU 처리량이 병목은 아니다.
- 판단:
  - X와 Firefox 자체는 GPU로 돈다. 동영상(720p60)은 지금 소프트웨어 경로보다 나쁘다.
  - [가설] CPU도 GPU도 다 차지 않으니 프레임 사이의 대기가 원인이다. 후보: vblank 없는 Present와 Firefox 소프트웨어 vsync 타이머의 어긋남, X가 프레임마다 하는 창 복사(glamor) 대기, 페이지 메인 스레드. 측정하지 않았다.
  - 다음에 볼 것: Firefox 프로파일러(`MOZ_PROFILER_STARTUP`)로 프레임 하나가 어디서 기다리는지 보기, `e850-kms`에 vblank 이벤트 넣기.

**보드에 바꾼 것.**
- `Xorg :1`(GPU X)이 떠 있다. 내리기: `sudo pkill -f "Xorg :1"`. 로그 `/var/tmp/e850-kms/Xorg.1.log`, strace 기록 `Xorg.1.st`(첫 strace 실행분).
- `/var/tmp/e850-kms/`: `x-kms.sh`, `xorg-kms.conf`, `kenv`(저장소 `modules/e850-kms/`와 같음).
- `~/gpu/mesa-kbase`: libGL, SHARED→kmsro, shim 내보내기, `realpath` 수정이 들어간 판. 이전 판(kmsro, GLX 없음)은 `~/gpu/mesa-kbase.1185`.
- `~/gpu/gles-x11`(egl 모드), `~/.local/bin/strace`.
- 시험 파일: `/var/tmp/ff-support.py`, `/var/tmp/thrcpu.py`, 프로필 `/var/tmp/ffprof-kms`, `/var/tmp/ffprof-x0`. Firefox 충돌 기록 하나가 `~/.config/mozilla/firefox/Crash Reports/pending/`에 있다(보내지 않음).
- GPU `dvfs_min_lock`을 1001 MHz로 걸었다가 푸는 중(사용자 sudo). 재부팅하면 어차피 풀린다.

### 11.87 Firefox 동영상이 GPU X에서 느린 원인: shim의 캐시 clean과 동기 제출 (2026-11-22, 보드3)

**질문.** 11.86에서 YouTube 720p60이 GPU X(:1)에서 39~46 fps(드롭 25~37%)로, 소프트웨어 경로(:0, 58.3 fps)보다 나빴다. 사용자가 원인을 찾으라고 했다. 측정은 11.86과 같다(`/var/tmp/ff-yt.py aqz-KE-bpKQ hd720 30`, 시험 Firefox는 `kenv`로 :1에 띄움, 프로필 `/var/tmp/ffprof-kms`). GPU 클럭 고정은 사용자가 푼 상태다.

**1. 대기 위치(관찰).** `/proc/PID/task/TID/syscall`을 약 2 ms마다 읽는 표본기(`/var/tmp/thrwait.py`)로 GPU 프로세스를 봤다.
- Renderer 스레드: RUN 69.2%, `/dev/mali0`에서 `ppoll` 29.5%, futex 0.5%. 벽시계 시간을 다 쓰고 있었다.
- 그 밖의 스레드(Compositor, WRSceneBuilder, ImageBridge, CanvasRenderer)는 대부분 futex에서 기다렸다.
- 프레임마다 Renderer가 CPU 일(약 18 ms)과 GPU 대기(약 8 ms)를 번갈아 하므로, 프레임 하나에 약 26 ms(약 38 fps)가 걸린다. 11.86의 "CPU도 GPU도 다 차지 않는다"는 이 직렬화로 설명된다.
- 원인 후보는 shim 소스에서 찾았다. `pan_ioctl_submit`은 `JOB_SUBMIT` 다음 `poll`과 `read`로 그 작업의 완료 이벤트를 받을 때까지 돌아오지 않는다(동기 제출). 제출 전에는 목록에 있는 CPU 캐시 BO를 모두 clean한다.

**2. shim 통계(관찰).** `PAN_KBASE_STATS=1`이면 5초마다 제출 수, 제출에 쓴 시간(그 가운데 작업 완료를 기다린 `poll` 시간), 캐시 clean 양과 시간을 찍게 했다. Firefox GPU 프로세스, 재생 중 5초 구간(이 단계에서는 clean을 하나도 건너뛰지 않음):

| 항목 | 5초당 |
|---|---|
| 제출 | 1,100~1,800번(프레임당 약 7번) |
| 제출에 쓴 시간 | 1.4~2.9 s, 그 가운데 작업 대기 1.1~2.8 s |
| clean(TO_DEVICE) | 3.8~31.8 GB, 0.44~2.8 s |
| invalidate(TO_CPU) | 0~25 MB, 3 ms 이하 |

- 두 몫을 더하면 Renderer 벽시계 시간의 60~100%가 shim 안이다.
- clean된 BO(크기별 상위 목록을 잠깐 찍어 봄):
  - 1 MB BO: 제출마다 한 번. Mesa의 업로드 버퍼(`u_upload`, 1 MB)로 보인다. 매 그리기마다 CPU가 쓰니 clean이 맞다.
  - 2 MB BO 둘: 제출 2~3번에 한 번.
  - 16.1 MB BO 서넛: 5초에 14~89번. 텍스처 캐시로 보인다. CPU가 쓰지 않았어도 목록에 있으면 매번 16 MB를 훑었다.
- 커널의 clean은 더러운 줄이 없어도 범위 전체를 훑는다. 그래서 약 10 GB/s, 16 MB에 약 1.6 ms가 든다.

**3. 고친 것: CPU가 쓴 리소스 BO만 clean(`tools/gpu/mesa-kbase-g52-e850.patch`).**
- Mesa:
  - 리소스(텍스처·버퍼) BO를 새 플래그 `PAN_BO_CPU_TRACKED`로 만든다. 커널 플래그로는 `PANFROST_BO_E850_CPU_TRACKED`(bit 31)이다. shim 전용이라 진짜 panfrost 커널은 이 플래그를 거부한다.
  - `panfrost_ptr_unmap`에서 쓰기 전송이면 `panfrost_bo_cpu_written()`이 `MMAP_BO` ioctl을 한 번 더 부른다. 진짜 커널에서는 오프셋만 돌려주는 무해한 호출이다.
  - 리소스 BO에 CPU가 쓰는 길은 모두 전송(map/unmap)을 거친다: 직접 쓰기, DISCARD 복사, 타일링, AFBC 스테이징. AFBC 헤더 초기화도 알린다.
- shim:
  - 추적 BO는 `MMAP_BO`가 오면 `dirty`가 된다. 제출 때 `dirty`일 때만 clean하고, clean하기 **전에** 플래그를 내린다. 그 사이에 들어온 쓰기는 다음 제출에서 clean된다.
  - 추적하지 않는 BO(풀, 배치 BO, 선형 변환으로 새로 만든 BO)는 전처럼 매번 clean한다. invalidate(WAIT_BO)는 그대로 둔다.
- 처음 시도는 효과가 0이었다(통계상 건너뛴 양 0 MB, 상위 BO 모두 `tracked 0`).
  - 이 브랜치의 `panfrost_bo_create`가 `flags = flags & PAN_BO_EXECUTE;`로 다른 플래그를 다 지운다. 그래서 `DELAY_MMAP`도 지워지고, 모든 BO가 만들자마자 CPU에 매핑된다. "Mesa가 매핑한 적 없는 BO는 건너뛰기"를 먼저 해 봤지만 효과가 없었던 것도 이 때문이다.
  - `PAN_BO_CPU_TRACKED`는 남기도록 고쳤다. BO 캐시는 플래그가 같아야 다시 쓰므로 추적 BO와 아닌 BO는 섞이지 않는다.

**4. 결과(관찰).** 720p60 30 s:

| Mesa | 보인 fps | 드롭 | 비고 |
|---|---|---|---|
| 11.86 판, 통계만 넣은 판(모두 clean) | 38.1, 38.2, 39.8, 40.9 | 36~38% | 매번 새 Firefox |
| CPU가 쓴 리소스 BO만 clean | 48.0, 50.9, 52.2 | 25%, 15%, 14% | 같은 Firefox에서 1·2·3번째 |
| 같은 판, 통계 끔(최종) | 47.6, 51.8 | 26%, 14% | 새 Firefox에서 1·2번째 |
| 참고: :0 소프트웨어 WebRender(11.86) | 58.3 | 4% | |

- 고친 뒤 5초당 clean은 0.76~0.92 GB, 0.10~0.13 s다(전에는 3.8~31.8 GB, 0.44~2.8 s). 건너뛴 양은 3.4~9.9 GB다. 남은 clean은 거의 다 1 MB 업로드 버퍼다.
- 남은 몫은 작업 대기로, 5초에 2.5~2.9 s(약 55%)다. 제출은 1,500~1,800번이고, 작업 하나에 평균 약 1.7 ms를 기다린다.
- 재생 중 GPU 클럭(0.5초 간격 16번): 377 MHz가 대부분이다. 0(전원 꺼짐)이 4번, 433·598·865 MHz가 한 번씩이다. `gpu_busy`는 16번 모두 0%였다.
- `run-test.sh`(GLES 시험) 통과.

**5. 화면 확인과 렌더링 깨짐(관찰).**
- fb0 mmap(`fbshot.py`)에는 KMS 버퍼가 아닌 원래 fb0 메모리가 보인다(검은 화면에 커서). 그래서 X :1의 루트 창을 `XGetImage`로 뜨는 `tools/gpu/xshot.c`를 만들었다(`DISPLAY=:1 xshot > a.ppm`).
- 재생 시작 10~12초 뒤 화면(스크린샷이 1080p를 읽느라 그 회차 fps가 30.6~44로 떨어짐):
  - **11.86 판에서도 페이지가 깨진다.** 글자 일부가 빠지고(제목, 버튼), 오른쪽 목록 타일 일부가 검거나 옛 내용이다. 그러니 이번 수정 때문이 아니다.
  - `PAN_KBASE_UNCACHED=1`(캐시 없음)이면 더 깨진다. 화면 절반을 가르는 큰 흰 삼각형이 나온다. 그러니 캐시 처리 문제가 아니다. [가설] 그리기 일부를 GPU가 잘못 실행한다(컴파일러 또는 타일러). 확인하지 않았다.
  - 5번 가운데 4번은 플레이어 자리에 YouTube "Something went wrong. Refresh or try again later"가 떴다. 11.86 판과 새 판 모두에서 떴다. 그동안에도 `<video>`의 프레임 수는 늘었다. 그러니 **fps 수치가 화면에 실제로 보인 영상인지는 확인하지 못했다.** 11.86의 수치도 같은 조건이다.

**판단.**
- 원인 1(고침): 제출마다 목록의 캐시 BO를 모두 clean한 것. GPU 프로세스 시간의 10~55%를 썼다. 고친 뒤 38~41 → 48~52 fps.
- 원인 2(남음): 동기 제출. Renderer는 GPU 작업마다 끝날 때까지 기다리므로 CPU 일과 GPU 일이 겹치지 않는다(벽시계 시간의 약 55%가 작업 대기).
  - [가설] GPU가 작업 사이마다 놀아서 DVFS가 클럭을 최저(377 MHz)에 둔다. 그래서 작업 하나가 더 길어진다. 11.86에서 1001 MHz로 고정하면 +14%였다(45.6 fps, 옛 shim).
- 11.86의 "GPU 처리량이 병목은 아니다"는 반만 맞다. GPU가 다 차지는 않지만, GPU 시간이 CPU 시간에 더해지므로 프레임 시간의 절반을 차지한다.

**다음 후보(하지 않음).**
- shim 비동기 제출:
  - atom마다 바로 앞 atom에 ORDER 의존을 걸어 GPU 순서를 지킨다.
  - BO마다 마지막으로 쓴 atom 번호를 기억해 `WAIT_BO`(timeout 0은 바쁨 보고)를 실제로 기다리게 한다. syncobj와 fence는 지금 stub이다: handle은 늘 1이고, fence 생성은 export가 fd -1을 돌려줘 실패한다. 이것을 atom 번호로 구현한다.
  - BO 해제는 GPU가 끝날 때까지 미룬다. kbase 이벤트는 한 스레드가 잠금을 잡고 읽는다.
  - X와 공유하는 버퍼(가져온 dma-buf)를 쓰는 제출은 지금처럼 동기로 둔다. kbase에서는 dma-buf 암묵 동기화를 쓰지 않기 때문이다.
  - 할 일이 크고 X 서버에도 들어가므로 사용자와 정한다.
- X 서버도 새 Mesa로 다시 띄운다(사용자 sudo `x-kms.sh`). 지금 Xorg :1은 11.86 판(모두 clean)을 쓰고 있다.
- 새 shim으로 GPU 클럭 고정 시험(사용자 sudo). `gpu_busy`가 늘 0%인 이유도 본다.
- 렌더링 깨짐은 따로 조사한다.

**보드에 바꾼 것.**
- `~/gpu/mesa-kbase`: 이번 판(쓰기 추적 clean, 제출 통계). 11.86 판은 `~/gpu/mesa-kbase.1186`.
- `~/.local/bin/xshot`.
- 시험 파일: `/var/tmp/thrwait.py`, 로그 `/var/tmp/ffk*.log`, `/var/tmp/yt-*.txt`, `/var/tmp/ffk-720*.txt`.
- Xorg :1과 그 안의 사용자 창은 그대로 두었다. 시험 Firefox는 내렸다.

### 11.88 shim 비동기 제출, 새 Mesa로 X 재시작, 밤샘 시험, 렌더링 깨짐의 원인(셰이더 코어 LS 캐시) (2026-11-22, 보드3)

**목표.** 11.87의 원인 2(동기 제출)를 없앤다. 사용자 지시는 "구현해, 커밋하고 전부 기록해"였다. 그다음 "알아서 밤새 테스트해"(sudo 사용 허락)라고 했다. 그래서 X 재시작과 GPU 클럭 고정도 했다. sudo 비밀번호는 명령에만 썼고 저장소에는 넣지 않았다.

**1. kbase r38p1 소스에서 확인한 것(관찰).**
- atom 번호 0은 "의존 없음"이다. 쓸 수 있는 번호는 1~255다.
- atom 슬롯은 그 atom의 완료 이벤트를 사용자 공간이 읽어야 풀린다. 아직 쓰는 번호로 `JOB_SUBMIT`하면 슬롯이 풀릴 때까지 막힌다.
- `pre_dep`에 `BASE_JD_DEP_TYPE_ORDER`를 걸면 앞 atom이 끝난 뒤에 시작한다. 앞 atom이 실패해도 실패가 번지지 않는다.
- 이 커널은 `CONFIG_MALI_DMA_FENCE`가 꺼져 있다. 그래서 프로세스 사이(X와 클라이언트)에 dma-buf 암묵 동기화가 없다.
- drm-shim은 처리 함수의 반환값을 그대로 ioctl 결과로 돌려준다. 바쁨이나 시간 초과를 알리려면 errno를 정하고 -1을 돌려줘야 한다.
- `tools/gpu/abi-check.c`에 `pre_dep` 위치, 의존 구조체 크기, ORDER 값의 검사를 더했다(0 mismatches).

**2. 구현(`tools/gpu/mesa-kbase-g52-e850.patch`).**
- shim(`panfrost_kbase.c`):
  - 제출마다 순번(seq)을 붙인다. atom 번호는 `1 + (seq-1) % 255`다. 한 번에 64개까지 띄우고, 넘으면 가장 오래된 것을 기다린다(throttle).
  - 모든 atom은 바로 앞 atom에 ORDER 의존을 건다. 그래서 GPU 실행 순서는 제출 순서와 같다.
  - 이벤트는 `event_lock`을 잡은 스레드 하나가 읽는다. 완료 표시와 순번 상태는 `job_lock`이 지킨다.
  - BO마다 마지막으로 쓴 순번(`last_seq`)을 기억한다. `WAIT_BO`는 그 순번까지 기다린다. timeout 0이면 이미 온 이벤트만 읽고, 아직이면 `ETIMEDOUT`(바쁨)을 돌려준다. 기다린 뒤 CPU 캐시를 invalidate하는 것은 전과 같다.
  - syncobj를 실제로 구현했다. handle→순번 표를 쓰고, `out_sync`에 그 제출의 순번을 단다. fence용 "sync file"은 eventfd이고 fd→순번 표로 찾는다. `SYNCOBJ_WAIT`는 WAIT_ALL이면 가장 큰 순번, 아니면 가장 작은 순번을 기다리고, 시간이 지나면 `ETIME`을 돌려준다.
  - BO 해제는 그 BO의 `last_seq`가 끝날 때까지 기다린 뒤 munmap한다.
  - `PAN_KBASE_SYNC=1`이면 예전처럼 제출마다 끝날 때까지 기다린다.
  - `PAN_KBASE_STATS=1` 통계에 대기 종류(sync, throttle, bo, syncobj, free)별 횟수와 시간을 더했다.
- 프로세스 사이 안전(Mesa):
  - **첫 시도(실패):** shim에서, 가져온(공유) BO가 목록에 있는 제출은 동기로 했다. Firefox는 거의 모든 배치에 X와 공유하는 BO를 넣어서 대부분 동기로 남았다. 720p는 51.6 fps(드롭 15%), 55.7 fps(9%)였다.
  - **최종:** Mesa `panfrost_flush`(glFlush, SwapBuffers 등)에서, 그동안 제출한 배치 가운데 `PAN_BO_SHARED` BO를 쓴 것이 있으면(`ctx->shared_pending`) 그 컨텍스트의 마지막 syncobj를 기다린다. 다른 프로세스가 보기 전에 GPU 일이 끝나 있게 된다. 그리기 사이의 제출은 비동기로 흐른다.

**3. 결과(관찰).** YouTube 720p60(`aqz-KE-bpKQ`, 30 s, 매번 새 시험 Firefox, :1 GPU X):

| 판 | 보인 fps / 드롭 | CPU(전체 코어) |
|---|---|---|
| 11.87(동기 제출, CPU가 쓴 BO만 clean) | 47.6~52.2 / 14~26% | 52~64% |
| 비동기 + 공유 BO 제출만 동기(첫 시도) | 51.6 / 15%, 55.7 / 9% | 63%, 54% |
| 비동기 + flush 때 대기, X는 옛 Mesa | 54.3 / 15%, 57.6 / 4%, 58.8 / 3% | 76%, 58%, 55% |
| 같은 판, X도 새 Mesa로 다시 띄움 | 57.7 / 9%, 60.6 / 3%, 58.8 / 3%, 54.8 / 15%, 60.3 / 3%, 59.9 / 3% | 53~76% |
| 같은 판 + GPU 1001 MHz 고정 | 60.4 / 6%, 60.5 / 1% | 68%, 56% |
| 참고: :0 소프트웨어 WebRender(오늘) | 56.8 / 7% | 58% |

- 새 Firefox의 첫 회는 나쁠 때가 많다(54.3, 57.7, 54.8). 두 번째부터는 57.6~60.6 fps로 소프트웨어 경로(56.8~58.3)와 같거나 조금 낫다.
- Firefox GPU 프로세스 shim 통계(5초당): 제출 시간이 약 2.8 s에서 0.18~0.26 s로 줄었다. 프레임마다 한 번 꼴인 syncobj 대기(flush 대기)가 약 290번, 1.6~2.0 s다. bo 대기는 35번 이하(합 0.8 ms 이하), throttle은 가끔이다. kbase 오류 이벤트는 없었다.
- 1080p60은 AV1 소프트웨어 디코딩이 병목이다. GPU X 기본 17.7 / 69%, 17.5 / 74%, 1001 MHz 고정 19.3 / 69%, 22.8 / 66%, :0 소프트웨어 28.5 / 54%, 21.0 / 69%. 회차마다 흔들림이 크다.
- GLES 시험:
  - `gles-test` 1080p 셰이더: 99.6 → 201, 177 fps.
  - `gles-x11` EGL 720p: 약 200~269 fps(창 버퍼가 공유 BO라 flush마다 기다림). `PAN_KBASE_SYNC=1`이면 197.8 fps.

**4. GPU DVFS(관찰).**
- `utilization`은 GPU가 일하는 동안에도 늘 0이다. 클럭은 377 MHz나 0(전원 꺼짐)에 머문다. `time_in_state`도 377000만 는다.
- Interactive governor 설정은 `highspeed_clock 377000`, `highspeed_load 75`, `dvfs_period 100`이다.
- debugfs `/sys/kernel/debug/mali0/dvfs_utilization`은 Firefox가 GPU로 그리는 동안에도 `busy_time: 0 idle_time: 0`이다. kbase가 사용률을 아예 모으지 않는다.
- [가설] 그래서 DVFS가 클럭을 올리지 않는다. 왜 모으지 않는지(kbase 설정, Exynos 플랫폼 코드)는 보지 않았다.
- 시험으로 `echo 1001000 > dvfs_min_lock`(root)을 걸었다. 720p는 60.4 / 6%, 60.5 / 1%이고, flush 대기가 5초당 약 0.87 s로 절반이 됐다. 시험 뒤 `echo 0 > dvfs_min_lock`으로 풀었다(`dvfs_min_lock_status` `[1:0]`).

**5. X :1 재시작과 pkill 함정(보드에 바꾼 것, 실패한 명령).**
- 새 Mesa로 X를 다시 띄웠다: `sudo sh /var/tmp/e850-kms/x-kms.sh`.
- `x-kms.sh`는 안에서 `pkill -f "Xorg :1 vt8"`을 한다. 내 ssh 명령 줄에 같은 글자(`pgrep -f "Xorg :1 vt8"`)가 있어서 그 ssh까지 죽었다(종료 코드 255, X는 정상으로 떴음).
- `pkill -f "ffprof-[x]0"`도 같은 ssh 명령 안에 `--profile /var/tmp/ffprof-x0`이 있어서 그 ssh를 잡았다. 기다리는 루프가 600초 돌았다.
- 규칙: `pkill -f`는 따로 ssh 한 번으로 보내고, 그 명령 줄에 같은 글자를 넣지 않는다.

**6. 그 밖의 관찰.**
- Firefox를 띄울 때마다 커널 WARN이 하나 난다: `v4l_querycap`(drivers/media/v4l2-core/v4l2-ioctl.c:1115), 호출자는 `v4l2test`와 `firefox-esr`. 오늘 바꾼 것보다 먼저 있었고 GPU와 관계없다.
- UART(`~/e850-logs/uart-board3.log`): 11.87 커밋(22:21 KST) 뒤로 GPU fault는 없다. 마지막 fault는 20:21 KST(uptime 90297 s)다. 슬롯 0(fragment)에서 0x58(DATA_INVALID)과 0x5b가 Xorg 쪽에서 몰려 났다. 11.86~11.87 작업 중이었다.
- 한 회는 `quality hd720 0x0`, 프레임 수가 음수였다. 그때 Renderer는 futex에서 쉬고 있었다(shim 교착 아님). YouTube가 재생을 끊은 경우다(8절).

**7. 렌더링 깨짐의 원인: 셰이더 코어 LS 캐시(관찰, 고침).**
11.86~11.87의 깨짐(글자가 빠지고, 터미널 글자 칸이 회색이고, 타일이 검거나 옛 내용, 창을 닫은 뒤 루트가 다시 안 그려짐, 비캐시에서 큰 흰 삼각형)은 11.86 판부터 있었다. glamor와 WebRender는 작은 사각형 여러 개를 한 번에 그린다(인스턴싱). 그래서 시험 두 개를 만들었다(EGL surfaceless, GLES 3.0).
- `tools/gpu/gles-subtex.c`: 512² 텍스처(R8, ALPHA, RGBA8)에 작은 사각형을 `glTexSubImage2D`로 300번 올리고, 올릴 때마다 그 텍스처를 그리고, 50번마다 읽어 CPU 사본과 비교한다. **PASS**. 글리프 캐시 갱신은 맞다.
- `tools/gpu/gles-inst.c`: 512² FBO에 8 px 칸 64×64 격자로 n개 사각형을 인스턴싱으로 그리고(인스턴스별 위치·색 속성, divisor 1), 칸마다 가운데 픽셀을 본다. strip `glDrawArraysInstanced`와 indexed `glDrawElementsInstanced`를 다 본다.
  - **FAIL.** 인스턴스가 통째로 빠진다(가운데 픽셀만이 아니라 6×6 사각형 전체, `MAP=1`). 색이 틀린 경우는 한 번도 없다. 빠지는 수는 회마다 다르다(1~23개).
  - 새 판, `PAN_KBASE_SYNC=1`, `PAN_KBASE_UNCACHED=1`, 11.86 판(`mesa-kbase.1186`)에서 모두 같다. 그러니 비동기 제출과 CPU 캐시 처리 때문이 아니다.
  - n=1~300을 두 번 훑으면(`NS=1-300 PASSES=2`) 87~219 사이의 **홀수 n에서만** 빠졌다(그 범위 홀수의 대부분). 짝수 n과 85 이하, 221 이상은 모두 통과했다.
  - `INSTID=1`(위치를 `gl_InstanceID`로 계산, 인스턴스 속성 없음)이면 덜 빠지지만 빠진다. 속성 읽기 문제가 아니다.
  - `HIGHP=1`(색 varying을 fp32로)과 `TRI6=1`(인스턴스당 꼭짓점 6개)이면 짝수 n도 빠진다. "홀수"는 버퍼 배치 때문이다.
  - `BIFROST_IDVS=1`이면 훨씬 많이 틀린다(n=255에서 129번 인스턴스부터 126개 빠짐, 큰 n은 색도 틀림).
  - **같은 n을 40번 연달아 그리면 첫 번째만 빠지고 나머지 39번은 통과한다.** 타일러가 같은 주소에 있던 **앞 배치의 위치 값**을 읽는다는 뜻이다. 위치 버퍼는 색 varying 버퍼 다음에 64바이트 맞춤으로 놓인다(`ALIGN(32n, 64)`). 그래서 n이 홀수면 앞 그리기(n-1)와 위치 버퍼 시작이 인스턴스 하나만큼 어긋난다. 인스턴스 i는 앞 그리기의 i+1 자리 값을 읽고 옆 칸에 그려진다. 그 칸은 곧 인스턴스 i+1이 덮으므로 "빠짐, 색 틀림 없음"이 된다. 짝수면 앞 그리기와 배치가 같아 옛 값이 우연히 맞다. 87~219 범위의 이유는 모른다.
- 원인을 가르려고 Mesa에 실험 코드를 넣었다. 꼭짓점 job과 타일러 job 사이에 Mali **cache flush job**(job 종류 3, genxml v7에 있지만 Mesa가 쓰지 않던 것)을 끼우고, 무엇을 flush할지는 환경 변수 `PAN_E850_FLUSH`(payload 비트)로 정한다. `NS=80-230`, 1회:

| flush job | 빠진 그리기 | 기본 목록 |
|---|---|---|
| 없음(0) | 44 | FAIL |
| **셰이더 코어 LS clean(1)** | **0** | **PASS** |
| LS clean+invalidate(3) | 0 | PASS |
| 타일러 invalidate(64) | 64 | FAIL |
| LS clean + 타일러 invalidate(65) | 0 | PASS |
| L2 clean(128) | 66 | FAIL |
| L2 clean+invalidate(384) | 62 | FAIL |

- **판단:** 꼭짓점 셰이더가 쓴 위치가 셰이더 코어의 load/store 캐시(write-back)에 남아 있다. 그런데 타일러는 L2에서 읽는다. 꼭짓점 job이 끝났다고 LS 캐시가 L2로 써지지 않는다(이 G52 r0p0 MP1에서). IDVS는 위치 셰이더와 타일링이 한 job 안이라 사이에 flush를 넣을 수 없다. 그래서 IDVS는 계속 끈다(고친 판에서도 `BIFROST_IDVS=1`이면 기본 목록 두 번, 그리기 80개가 모두 FAIL).
- **고친 것:** `pan_cmdstream.c` `panfrost_emit_vertex_tiler_jobs`(v7)에서 꼭짓점 job마다 LS clean flush job을 끼운다(기본값 1). `PAN_E850_FLUSH=0`이면 끈다. 다른 값은 실험용이다.
- 고친 판으로 `gles-inst`의 모든 변형(기본, INSTID, HIGHP, TRI6, SYNC, UNCACHED, n=1~300 두 번씩)과 `gles-subtex`가 PASS다.
- 화면 확인(X :1을 새 Mesa로 다시 띄움, `xshot`):
  - 같은 터미널(`ls -la /usr/bin`, dmesg, 영문 한 줄)을 `PAN_E850_FLUSH=0`으로 띄운 X와 기본값 X에서 떴다. flush가 없으면 줄이 통째로 빠지고, 글자 조각이 다른 줄에 찍히고, 메뉴 막대가 깨진다. 11.86부터 보던 깨짐 그대로다. 기본값(LS clean)에서는 모든 글자가 맞다.
  - GPU Firefox에 글자 크기 7가지, 색 40가지 줄과 작은 색 타일 600개 페이지(`/var/tmp/textpage.html`)를 띄웠다. 모든 줄이 빠짐없이 보였다. YouTube 재생 중 화면에도 영상이 보였고, 탭·주소창 글자가 맞았다. 그 화면에서 페이지 본문은 YouTube가 아직 불러오는 중(회색 자리표시)이라 판단하지 않았다.
- 비용(관찰): 뚜렷한 차이가 없다.
  - `gles-test` 1080p 60프레임: flush 있음 268.5, 255.3 fps, 없음 235.1, 218.1 fps.
  - `gles-x11` EGL 720p 8초: 있음 2,150, 2,028프레임, 없음 2,104, 2,150프레임.
  - YouTube 720p60(새 시험 Firefox, 같은 Firefox에서 1~4회): 59.4 / 6%, 57.8 / 4%, 59.1 / 2%, 59.4 / 5%(4회째는 재생 중 화면 뜸). CPU 51~65%.
- `run-test.sh`에 `gles-subtex`와 `gles-inst`(n=1~300)를 더했다. 고친 판에서 셋 다 PASS다.
- [가설] compute job이 쓰고 같은 체인의 다음 job이 읽는 경우(변환 피드백, 간접 그리기)도 같은 문제가 있을 수 있다. 시험하지 않았다.
- [모름] 메인라인 panfrost(Amlogic G52 r0p0 MP4)에서는 이런 flush 없이 dEQP 인스턴싱 시험을 통과한다. 무엇이 다른지는 모른다.
  - kbase가 GPU 설정 레지스터에 넣은 값은 debugfs로 `quirks_gpu=0xF0000`(IDVS 묶음 크기 0xF), `quirks_sc=0x20000`(SC_TLS_HASH_ENABLE), `quirks_tiler=0`, `quirks_mmu=0`이다. 메인라인 panfrost가 G52에 넣는 값과 같다. 그러니 설정 레지스터 차이는 아니다.
  - 남은 차이는 코어 수(1개), 하드웨어 리비전, kbase의 job 제출 방식이다. 확인하지 않았다.

**8. 오래 돌리기(관찰).**
- 첫 시도(비동기 판, flush 없음, 14:07~14:37 UTC): `ff-yt.py … hd720 1800`으로 YouTube를 한 번에 30분.
  - 결과가 `quality hd720 0x0`, 재생 시간과 프레임 수가 음수, CPU 9%였다. **영상 재생 시험으로는 무효다.** 영상이 중간에 멈추고 Firefox는 페이지만 그렸다(5초에 제출 약 560번, flush 대기 약 40번).
  - 그래도 30분 동안 늘어난 것은 없었다: 시험 Firefox RSS 약 792 MB, Xorg :1 RSS 200,700 kB, kbase GPU 메모리 557 MB(가끔 685). kbase 오류 이벤트도 없었다.
- 둘째·셋째(고친 판): 같은 Firefox에서 120초 재생을 15번 하려 했다. 둘 다 1회째가 `0x0`이었다. 영상이 끝(634.6 s)에 닿았을까 해서 `ff-yt.py`가 0 s로 감고 시작하게 고쳤지만(`seekTo(0)`) 그대로였다.
- 원인(관찰): 5초마다 플레이어 상태를 찍는 `/var/tmp/ytwatch.py`로 봤다.
  - GPU X(:1): 재생 50.5 s까지 정상(1280×720, 프레임 증가)이다. 다음 표본(재생 약 55 s)에서 YouTube가 "Something went wrong. Refresh or try again later."를 띄웠다. `<video>`는 0×0, 플레이어 상태 -1이 된다. 광고는 아니었다(`ad-showing` 없음).
  - 소프트웨어 경로(:0, 새 시험 Firefox, Mesa 없음)도 재생 32 s까지 정상이었다가 재생 약 50 s에 같은 오류가 났다.
  - 그러니 **GPU 경로 문제가 아니다.** YouTube가 자동 조종(Marionette) 세션이나 로그인 안 한 재생을 1분쯤에서 끊는 것으로 본다(가설, 확인하지 않음). 30초 측정(재생 약 8~38 s 구간)은 이 전에 끝나므로 유효하다. 11.87에서 화면에 보이던 같은 오류도 이것이다.
- 넷째(고친 판, 15:04 UTC부터): YouTube 대신 보드에서 만든 20초 AV1 720p60 클립(`/var/tmp/loop720.webm`, ffmpeg `testsrc2`+noise, SVT-AV1 preset 12, 22.9 MB, 약 9 Mbps)을 `<video loop>`로 30분 돌렸다. 새 시험 Firefox(:1 GPU X), 1분마다 프레임·드롭·CPU를 `/var/tmp/locsoak.txt`에 적었다.
  - 결과(관찰): 30분(1분 표본 30개) 동안 108,128프레임 가운데 293프레임 드롭(0.27%), 가장 나쁜 1분이 37프레임이다. 보인 fps는 59.4~60.0(평균 59.89), CPU 32~39%였다. 멈춤이나 미디어 오류는 없었다.
  - 메모리: 시험 Firefox RSS는 데운 뒤(9분째) 790.8 MB에서 끝 796.5 MB, Xorg :1 RSS 198,092 kB와 kbase GPU 메모리 518 MB는 그대로였다.
  - shim 통계(5초당): 제출 약 670번, flush 대기 약 300번(프레임마다 한 번) 합 약 2.0 s. kbase 오류 이벤트 0, 같은 시간 UART에 GPU fault 없음.

**판단.**
- 원인 2(동기 제출)를 없앴다. 720p60이 47.6~52.2 fps에서 약 58~60 fps(새 Firefox의 첫 회는 더 낮을 때가 있음)가 되어 소프트웨어 경로와 같아졌다.
- 11.86부터의 렌더링 깨짐은 이 G52의 셰이더 코어 LS 캐시 때문이었다. 꼭짓점 job이 끝나도 위치 값이 L2에 써지지 않는다. Mesa가 꼭짓점 job과 타일러 job 사이에 LS clean flush job을 넣어 고쳤다. 잰 범위에서 비용은 보이지 않았다.
- 고친 판으로 로컬 AV1 720p60 클립을 30분 돌렸다. 평균 59.9 fps, 드롭 0.27%였고, 메모리는 늘지 않았고, 오류는 없었다.
- 남은 것:
  - GPU DVFS가 사용률을 모으지 않아 377 MHz에 머문다.
  - 1080p60은 AV1 소프트웨어 디코딩이 병목이다.
  - YouTube는 자동 시험 재생을 약 50초에서 끊는다(소프트웨어 경로도 같음). 긴 시험은 로컬 클립으로 한다.

**다음 후보(하지 않음).**
- kbase가 GPU 사용률을 모으지 않는 이유를 찾는다. 고치면 DVFS가 클럭을 올릴 것이다.
- compute job이 쓰고 같은 체인의 다음 job이 읽는 경로(변환 피드백, 간접 그리기)를 시험한다. IDVS는 계속 끈다.
- KMS vblank(30 fps 상한, 11.85).
- X :1에 인증(`-auth`)을 걸고, 부팅 때 띄울지 정한다.

**보드에 바꾼 것.**
- `~/gpu/mesa-kbase`: 이번 판(비동기 제출 + LS clean flush job). 그 전 판은 `~/gpu/mesa-kbase.1188a`(비동기, flush 없음), `.1187`(11.87), `.1186`(11.86).
- X :1을 이 판으로 다시 띄웠다(sudo `x-kms.sh`). 사용자의 :0 Firefox(vt7 데스크톱)는 건드리지 않았다.
- `~/gpu/gles-subtex`, `~/gpu/gles-inst`, `~/gpu/run-test.sh`(새 판).
- `/var/tmp/ff-yt.py`: 재생 전에 0 s로 감는다(`seekTo(0)`).
- 시험 파일: `/var/tmp/soakmon.sh`, `/var/tmp/textpage.py`, `/var/tmp/textpage.html`, `/var/tmp/ytdur.py`, `/var/tmp/ytwatch.py`, `/var/tmp/locsoak.py`, `/var/tmp/loop.html`, `/var/tmp/loop720.webm`, `/var/tmp/shot-1188*.ppm`. 로그는 `/var/tmp/yt-*.txt`, `/var/tmp/ffk-*.log`, `/var/tmp/soakmon*.log`.
- GPU 클럭 고정은 풀었다. 시험 Firefox는 내렸다.

### 11.89 GPU X 데스크톱: 메뉴·파일 관리자·한글 입력, 그리고 BT 스피커(JBL Flip 6) (2026-11-23, 보드3)

사용자가 GPU X(:1)를 쓰면서 세 가지를 요청했다. Firefox가 왜 안 보이는지, 오디오가 깔렸는지와 한글, 메뉴·파일 관리자, 그리고 BT 스피커 연결이다.

**1. :1 터미널에서 띄운 Firefox가 안 보이던 것.**
- 관찰: :1 LXTerminal에서 `firefox &`를 치면 창 없이 `[1]+ Done`(종료 코드 0)으로 끝났다. 그때 :0(vt7)에서 사용자 Firefox(PID 185165)가 기본 프로필을 잡고 있었다(`luhburbr.default-esr/lock -> 127.0.1.1:+185165`). :1 터미널의 환경에는 GPU Mesa 변수(`LD_PRELOAD` 등)도 없었다.
- 해석: 새 firefox가 같은 프로필로 떠 있는 Firefox에 "창 하나 열기"를 넘기고 끝났다. 창은 :0에 생겼을 것이다(보지 않았다).
- 바로 한 일: `sh /var/tmp/e850-kms/kenv firefox-esr --new-instance --profile /var/tmp/ffprof-kms`로 띄워 :1에 창이 뜬 것을 화면 캡처로 봤다.
- 고정: `features/desktop/firefox` → `/usr/local/bin/firefox`. :1에서는 `~/.config/mozilla/firefox/gpu` 프로필을 쓴다. 메뉴와 패널 단추도 이 wrapper를 쓰게 `/usr/local/share/applications/firefox-esr.desktop`의 `Exec`를 바꿨다(`XDG_DATA_DIRS`에서 `/usr/local/share`가 먼저다). 확인: :1 세션 환경으로 실행하니 `firefox-esr --profile …/gpu`가 떴고, 두 번째 실행은 그 Firefox에 새 창으로 열렸다.

**2. 오디오 상태.** audio 기능이 설치돼 있다(카드 `ExynosMadera`, `e850-audio` active). 출력은 3.5 mm 헤드폰 잭뿐이고 HDMI 소리는 없다(11.77). dmix가 안 돼 한 번에 한 프로그램만 쓴다. 이때 장치는 :1 Firefox가 열고 있었다.

**3. 메뉴·파일 관리자·한글 입력.**
- 설치(보드3, apt `--no-install-recommends`, 새 패키지 64개): `lxpanel pcmanfm mousepad gpicview xarchiver galculator lxtask fcitx5 fcitx5-hangul fcitx5-frontend-gtk3`. 먼저 `apt-get -s`로 봤고 `libpam-systemd`는 새로 끼지 않았다(이미 있고, 보드3은 `cgroup_disable=cpu`). 한글 글꼴은 `fonts-nanum`이 이미 있다.
- `features/desktop/session.sh`(:0과 :1이 같이 쓴다):
  - 세션마다 `dbus-run-session`으로 따로 버스를 띄운다. fcitx5는 버스마다 하나만 돌고, :0과 :1이 같은 사용자로 동시에 돈다.
  - `GTK_IM_MODULE=fcitx` 등 입력기 변수와 `XDG_MENU_PREFIX=lxde-`(메뉴 파일 `lxde-applications.menu`)를 넣는다.
  - 배경(`xbg`, 없으면 단색), `fcitx5 -d`, `lxpanel --profile e850`, `lxterminal`, `openbox`를 띄운다.
- 패널 `features/desktop/lxpanel.panel` → `/etc/xdg/lxpanel/e850/panels/panel`: 시작 메뉴, 파일 관리자·터미널·Firefox 단추, 작업 표시줄, CPU, 트레이(fcitx5), 시계. Debian 기본 패널은 `lxde-common`의 실행기를 가리켜서 쓰지 않았다.
- fcitx5 `/etc/xdg/fcitx5/profile`(`keyboard-us` + `hangul`), `config`(한/영: Ctrl+Space, 한/영 키, 오른쪽 Alt).
- `modules/e850-kms/x-kms.sh`: :1도 이 `session.sh`를 GPU Mesa 환경(`kenv`와 같은 `LD_PRELOAD` 등)으로 띄운다. 그래서 :1의 모든 프로그램이 shim을 싣는다. 메뉴나 터미널에서 띄운 Firefox도 GPU로 그린다.
- 보드 반영: 저장소 `features/desktop/`와 `common.sh`를 `/opt/e850-features/`에 올리고 `feature.sh install`을 실행했다. 이것은 서비스를 다시 읽기만 하고 :0을 재시작하지 않는다. 그다음 `x-kms.sh`로 :1만 다시 띄웠다.
- 확인한 것:
  - 화면 캡처에 패널, 파일 관리자, 한/영 표시 `en`이 보였다.
  - D-Bus `InputMethodGroupInfo`로 보니 그룹에 `keyboard-us`와 `hangul`이 있다. `immodules.cache`에 fcitx가 있고, :1 터미널 환경에 `GTK_IM_MODULE=fcitx`가 있다.
  - 사용자가 패널 단추로 Firefox를 띄워 YouTube를 봤다(gpu 프로필, shim 실림).
- 확인하지 못한 것: 실제 한글 타자(보드에 xdotool이 없어 사용자 확인이 필요하다). :0은 다시 띄우지 않아 새 세션이 아직 적용되지 않았다. 패널 시계는 보드 시간대(UTC)로 나온다.

**4. BT 스피커(JBL Flip 6).**
- `bluez-alsa-utils`와 `libasound2-plugin-bluez`(4.0.0)를 설치했다.
  - `bluealsa.service`는 `status=217/USER`("Failed to set up user namespacing: Invalid argument")로 실패했다. 커널에 `CONFIG_USER_NS`가 없는데 유닛이 `PrivateUsers=true`를 쓴다.
  - drop-in `PrivateUsers=no`(`features/bt/bluealsa-no-userns.conf`)를 넣자 떴고, A2DP SBC 엔드포인트를 등록했다.
- 20초 스캔에서 오디오 클래스 장치는 `JBL Flip 6`(`E8:26:CF:1C:1D:DA`, Class 0x240414) 하나였다.
- 실패 1: `pair` → `AuthenticationRejected`.
  - btmon을 보니 보드의 IO Capability Reply가 `No Bonding`이었고, 스피커가 `Pairing Not Allowed (0x18)`로 답했다.
  - 원인: 컨트롤러가 `Pairable: no`였다. bondable이 꺼져 있으면 커널이 No Bonding으로 요청한다.
- 실패 2: `bluetoothctl pairable on` 뒤에는 `Dedicated Bonding`으로 나갔다. 그래도 4 ms 만에 같은 0x18이 왔다.
  - 해석(가설): 스피커가 페어링 모드가 아니었다. 사용자가 버튼을 누른 뒤에 성공했다.
- 성공:
  - 사용자에게 페어링 모드를 부탁했고, `/var/tmp/bt-pairloop.sh`가 약 30초마다 다시 시도했다.
  - 네 번째 시도(17:56:17 UTC)에서 `Pairing successful`이 나왔고, trust와 connect도 성공했다. `Paired/Bonded/Connected: yes`.
  - bluealsa PCM은 `A2DP (SBC): S16_LE 2 channels 48000 Hz`다.
- 출력:
  - `~/.asoundrc`(philip)로 기본 장치를 `bluealsa`로 돌렸다(가장 최근에 연결된 장치). 지우면 헤드폰 잭으로 돌아간다.
  - `aplay Front_Center.wav`(1.43초)가 1.92초에 rc 0으로 끝났다(전송 열기·비우기 포함). 귀로 들렸는지는 사용자 확인이 필요하다.
- Firefox:
  - 사용자가 띄운 :1 Firefox는 `.asoundrc`를 만들기 약 20초 전에 떴다. 그래서 헤드폰 잭(`/dev/snd/pcmC0D0p`)을 열었고, bluealsa 모듈이 올라오지 않았다(`/proc/PID/maps`).
  - alsa-lib은 설정을 프로세스 안에 한 번만 읽는다. 다시 띄워야 BT로 나간다.
- 이 동안 Wi-Fi 펌웨어 복구 횟수 0.
- 메모리: Firefox 두 개(:0 사용자, :1 gpu)와 데스크톱 두 개가 떠 있어 `available` 254 MB였다.

**판단.**
- :1 GPU X가 메뉴·파일 관리자·한글 입력을 갖춘 데스크톱이 됐다.
- BT 스피커는 이 Python vhci 브리지 위에서 A2DP 연결까지 된다.
- 남은 제약:
  - BT와 잭 모두 한 번에 한 프로그램만 쓴다(bluez-alsa 4.0은 섞기 없음, dmix 없음).
  - 스피커가 꺼져 있으면 기본 장치가 열리지 않는다.
  - 새 장치를 페어링할 때는 `pairable on`이 필요하다. 부팅 뒤 유지되는지는 보지 않았다(`/var/lib/bluetooth/…/settings`가 비어 있다).

**다음 후보.**
- 사용자가 한글 타자와 BT 소리를 확인한다.
- 재부팅 뒤 스피커가 다시 붙는지 본다.
- 메모리를 아끼려면 :0 데스크톱을 끈다(사용자 결정).

**보드에 바꾼 것(보드3).**
- apt 패키지 70개: 위의 데스크톱·입력기 64개와 bluez-alsa 6개. 로그는 `/var/tmp/apt-1189.log`.
- `/opt/e850-features/common.sh`와 `desktop/`를 새 판으로 바꿨다. 예전 판은 `/var/tmp/common.sh.bak1189`, `/var/tmp/desktop.bak1189/`에 있다. `feature.sh install`이 다음을 만들었다:
  - `/etc/xdg/lxpanel/e850/`, `/etc/xdg/fcitx5/{profile,config}`
  - `/usr/local/bin/firefox`, `/usr/local/share/applications/firefox-esr.desktop`
  - `user@.service.d/e850-no-cpu-delegate.conf`(저장소 판)
- `/var/tmp/e850-kms/x-kms.sh`를 새 판으로 바꿨다(예전 판 `x-kms.sh.1188`). X :1은 이것으로 다시 띄웠다.
- `/etc/systemd/system/bluealsa.service.d/e850-no-userns.conf`.
- BT: `pairable on`, JBL Flip 6 페어링·신뢰(`/var/lib/bluetooth`).
- `~/.asoundrc`(philip, 기본 출력 BT).
- `/var/tmp/bt-pairloop.sh`, `/var/tmp/btmon-1189*.snoop`.

### 11.90 보드3: 부팅하면 프롬프트, `e850-x`로 GPU 데스크톱, 성능 상태 (2026-11-23)

**요청.** 재부팅하면 프롬프트가 뜨고, 거기서 명령 하나로 X가 뜨게 한다. 그리고 성능 최적화가 되어 있는지 묻는다.

**바꾸기 전(관찰).**
- tty1은 root 자동 로그인(`getty@tty1` drop-in `--autologin root`)이다.
- `e850-usbhost`가 disabled였다. 부팅 뒤 USB 키보드가 동작하지 않는다.
- `dvslive`가 enabled였다. 부팅 때 HDMI에 DVS 화면을 그린다(보드3은 센서가 없다).
- X는 둘 다 손으로 띄웠다(`e850-desktop` disabled, GPU X는 `load.sh` + `x-kms.sh`).
- `dvslive.sh`는 시작할 때 MIF·INT·CAM devfreq를 최대로 고정한다. 그래서 지금 부팅의 MIF는 1539 MHz, INT는 667 MHz에 고정이었다(min = max). **11.82~11.89의 보드3 GPU 측정은 모두 이 고정 상태에서 했다.** dvslive를 끄면 MIF와 INT는 interactive governor가 정한다.

**바꾼 것.**
- `modules/e850-kms/e850-x` → 보드 `/usr/local/bin/e850-x`. root가 아니면 `sudo`로 다시 실행한다. 하는 일:
  1. `systemctl stop dvslive`
  2. MIF·INT `min_freq = max_freq`(측정 때와 같은 상태)
  3. `load.sh`(e850-kms)
  4. `x-kms.sh`
- 끄기는 `e850-x stop`(`Xorg :1 vt8` 종료)이다. 프롬프트로 돌아가기는 Ctrl+Alt+F1이다.
- `systemctl enable e850-usbhost`: 부팅 때 USB-A 호스트(키보드·마우스)가 켜진다. 대신 micro-USB ACM/ECM이 없어서 관리는 Wi-Fi ssh로 한다.
- `systemctl disable dvslive`(보드3만).
- `/etc/motd` 끝에 `GPU desktop: e850-x …` 한 줄(영문: HDMI 콘솔 글꼴에 한글이 없다).
- 재부팅 확인은 아래에 적는다.

**성능 상태(관찰과 측정, 2026-11-23).**
- CPU: 두 클러스터 모두 schedutil, 546 MHz~2.0 GHz(12단계).
- GPU 경로: 비동기 제출, CPU가 쓴 BO만 clean, LS flush(잰 범위에서 비용 없음)(11.87~11.88). YouTube 720p60 약 58~60 fps, 로컬 AV1 720p60 30분 59.9 fps.
- GPU 클럭: DVFS 표는 1001/865/754/598/433/377 MHz다. kbase가 사용률을 모으지 않아 377~433 MHz에 머문다. 1001 MHz 고정 시험에서 드롭이 1~6%로 줄고 flush 대기가 절반이 됐다(11.88). 고정은 지금 걸려 있지 않다.
- 메모리: 3.5 GB에 스왑이 없다. 커널에 zram(lzo-rle, lz4, zstd)은 있지만 설정하지 않았다. 데스크톱 두 개와 Firefox 두 개가 떠 있을 때 `available`은 254 MB였다.
- 아직 안 한 것: 전체 화면에서 페이지 전환이 일어날 때의 30 fps 상한(11.85)은 전체 화면 영상으로 재지 않았다. 1080p60은 AV1 소프트웨어 디코딩이 병목이다(11.88). MFC는 MMAP을 받지 않아 쓰지 못한다(11.81).

**사용자 결정(같은 날).** GPU 클럭 고정과 zram은 하기로 했다. 재부팅 확인은 답이 없어서 하지 않았다.

**GPU 클럭 고정.**
- `e850-x`가 `dvfs_min_lock`을 1001000으로 건다. `e850-x stop`은 GPU 고정을 풀고(0), MIF·INT `min_freq`를 가장 낮은 값으로 되돌린다.
- 확인: `dvfs_min_lock_status` `[1:1001000]`. GPU가 놀 때 `clock`은 0이다(전원이 꺼진다).
- 쉬는 동안 온도는 LITTLE0 57 °C, LITTLE1 56 °C, G3D 53 °C였다. 부하에서는 재지 않았다.

**zram.**
- `zram-tools`를 설치하고 `/etc/default/zramswap`에 `ALGO=lz4`, `PERCENT=50`을 넣었다. 그 결과 `/dev/zram0`은 1,825,280 KiB, 우선순위 100이다.
- desktop 기능이 같은 일을 한다.

**메뉴가 비던 것(고장과 고침).**
- 관찰: 사용자가 HDMI root 프롬프트에서 `e850-x`를 쳤다(18:28:59). 그 뒤 패널에 시작 메뉴 단추와 작업 표시줄만 남고 실행 단추가 사라졌다. `menu-cached`도 떠 있지 않았다.
- 원인: lxpanel 환경에 root 로그인 변수가 그대로 들어가 있었다(`XDG_RUNTIME_DIR=/run/user/0`, `XDG_SESSION_ID=1`, `MAIL=/var/mail/root`, `PWD=/root`). philip이 `/run/user/0`에 쓸 수 없어서 `menu-cached`가 소켓을 만들지 못했다. `LANG`도 없었다.
- 고침: `x-kms.sh`가 세션을 `env -i`로 띄운다. 넘기는 변수는 `DISPLAY`, `HOME`, `USER`, `LOGNAME`, `SHELL`, `LANG=C.UTF-8`, `PATH`와 GPU 변수뿐이다.
- 확인: 같은 root 환경을 흉내 내어 `e850-x`를 실행했다. lxpanel 환경에 `XDG_RUNTIME_DIR`가 없고 `LANG=C.UTF-8`이었다. `menu-cached`가 `~/.cache/menu-cached-:1`로 떴다. xdotool로 시작 메뉴를 눌러 보니 보조 프로그램, 인터넷, 소리와 영상, 시스템 도구, 설정, 실행이 펼쳐졌다. 실행 단추 셋도 돌아왔다.

**한글 입력 시험(xdotool).**
- 편집기(mousepad)에서 Ctrl+Space를 누르자 fcitx5가 `keyboard-us`에서 `hangul`로 바뀌었다. `gksrmf`를 치니 "한글"이 찍혔다(화면 캡처).
- 첫 글자 "ab"는 창 포커스가 넘어가기 전이라 터미널로 들어갔다(시험 방식 문제). 지우고 입력기를 영문으로 되돌렸다.

**Wi-Fi·BT 설정과 아이콘(사용자 요청).**
- 설치(새 패키지 38개 + `qt5-gtk-platformtheme`): `wpagui`, `blueman`. `libpam-systemd`는 끼지 않았다. `polkitd`와 `blueman-mechanism.service`가 들어왔다.
- philip을 `netdev`와 `bluetooth` 그룹에 넣었다.
- wpa_supplicant 설정(`/etc/wpa_supplicant/wpa_supplicant-wlan0.conf`)을 바꿨다. 원본은 `.bak1190`이다.
  - `ctrl_interface=DIR=/run/wpa_supplicant GROUP=netdev`, `update_config=1`(GUI에서 추가한 네트워크 저장).
  - 재시작은 `systemd-run`으로 했다. 60초 안에 `COMPLETED`가 아니면 원본으로 되돌리게 했다(`/var/tmp/wifi-1190.sh`). 7초 만에 다시 붙었다.
- Wi-Fi 아이콘:
  - 처음에는 lxpanel `netstatus`를 넣었다. 그러나 `/proc/net/wireless`가 0이라(이 드라이버는 WEXT 통계를 주지 않는다) 신호가 늘 0%로 나와서 뺐다. 신호는 nl80211(`iw`: -66 dBm)로만 나온다.
  - 대신 `wpa_gui -t -q`(트레이)를 쓴다. 아이콘이 신호 막대로 나오려면 Qt가 Adwaita 아이콘을 써야 해서 `QT_QPA_PLATFORMTHEME=gtk3`을 넣었다.
  - 확인: 트레이에 신호 막대 아이콘이 보인다.
- 사용자가 wpa_gui(Configure 단추)로 끊기·연결을 해 봤다. 저널에 18:40:12 `DISCONNECTED reason=3 locally_generated=1`, 18:40:24 `CONNECTED`가 남았다. 그동안 ssh가 12초쯤 끊겼다.
- BT: `blueman-applet`(트레이 아이콘, `blueman-tray`, `blueman-mechanism`)이 뜬다. 메뉴의 설정(Preferences)에 Bluetooth Manager가 있다. 기기 페어링은 blueman으로 시험하지 않았다.
- 음량: lxpanel `volumealsa`. 팝업 막대(37%)가 `amixer`의 `JBL Flip 6 - A2DP` 47/127과 같다. `~/.asoundrc`의 `ctl.!default`(bluealsa)를 잡는다.
- 세션(`session.sh`)에는 `QT_QPA_PLATFORMTHEME=gtk3`, `blueman-applet`, `wpa_gui -t -q`를 넣었다. 패널은 메뉴, 실행 단추, 작업 표시줄, CPU, 음량, 트레이(한/영, BT, Wi-Fi), 시계다.

**화면(보드3 :1, 2026-11-23 UTC 18시대).**

![GPU 데스크톱(:1)에서 시작 메뉴를 펼친 화면. 메뉴를 고친 직후, Wi-Fi·BT 아이콘을 넣기 전](img/gpu-desktop-menu-20261123.png)

![패널 오른쪽: CPU, 음량(BT 스피커), 한/영, BT(blueman), Wi-Fi 신호(wpa_gui), 시계](img/gpu-desktop-tray-20261123.png)

![mousepad에서 Ctrl+Space 뒤 gksrmf를 친 결과 "한글"(앞의 c는 시험 글자)](img/gpu-desktop-hangul-20261123.png)

**보드에 바꾼 것(보드3, 11.90).**
- 서비스: `e850-usbhost` enabled, `dvslive` disabled. `/etc/motd`에 한 줄.
- `/usr/local/bin/e850-x`. `/var/tmp/e850-kms/x-kms.sh`는 `env -i` 판이다.
- 패키지: `zram-tools`, `xdotool`(시험용), `wpagui`, `blueman`, `qt5-gtk-platformtheme` 등. 로그는 `/var/tmp/apt-1190*.log`.
- `/etc/default/zramswap`(lz4, 50%).
- 그룹: philip을 `netdev`, `bluetooth`에 넣었다. wpa_supplicant 설정을 바꿨다(위, 원본 `.bak1190`).
- `/opt/e850-features/desktop/`와 `/etc/xdg/lxpanel/e850/panels/panel`은 새 판이다.
- GPU `dvfs_min_lock` 1001000, MIF·INT 최대(`e850-x`가 걸었고 `e850-x stop`이 푼다).
- 재부팅 확인은 아직 안 했다. 다음 부팅에 프롬프트, 키보드, `e850-x`, BT 재연결을 본다.

### 11.91 데스크톱 보드 이미지 저장(보드3 → 다른 보드 복제용) (2026-11-23)

**요청.** 오늘 변경을 커밋하고, 모든 과정과 이미지를 저장해서 다른 보드에 복사할 수 있게 한다.

**한 것.** 복제 절차(BOARD-CLONE.md 4~5장)를 보드3에 그대로 썼다. ACM이 없어서(usbhost 켬) 원본 쪽 명령은 Wi-Fi ssh에서 `sudo`로 했다.
- 확인: 보드3 boot 파티션 앞 64,933,888바이트의 md5가 `~/e850-logs/boot-nocpu/boot.img`와 같다(`25643a9a3f6b62b18db07cd895a7c410`).
- 원본에서 `apt-get clean`(358 MB)을 했다. 그 밖에는 손대지 않았다. `/var/tmp` 2.0 GB(DVS 녹화 1.1 GB, 시험 Firefox 프로필 약 0.4 GB 등)도 절차대로 함께 옮긴다.
- 받기: `systemd-run --unit=rootfs-send`(sudo), Mac `rootfs-recv.py`(Wi-Fi ssh 터널). 5,151,877,120바이트를 773초에 받았다(6.7 MB/s, 11-14의 3.7 MB/s보다 빠름). md5 `81004c2481980586bc585a8d9ae1bed8`가 원본 기록과 같다. 18:47:57~19:00:50 UTC.
- `tar=1`. 경고는 `/sys`, 사용자가 열어 둔 Firefox gpu 프로필과 캐시 두 곳(file changed as we read it), 소켓 두 개(ignored)였다. Firefox가 열린 채로 받았으니, 복제본에서 그 프로필이 어긋날 수 있다(확인 안 함).
- 이미지: `build-clone.sh`를 `BLOCKS=2097152`(8 GiB)로 돌렸다(51초).
  - 경고 0, e2fsck 정상. 47,207 파일, 4.9 GB 사용.
  - 새 machine-id는 `6f0d9b3894e235c97ecae32f79618492`다.
  - `resparse.py --split-mib 700`으로 8조각을 만들었다(28,459 → 28,543 청크, FILL 9개를 32 MiB 이하로 나눔).
- 저장: `~/e850-logs/clone-20261123/`(Mac, 9.5 GB). 들어 있는 것: `boot.img`(= boot-nocpu), `rootfs.tar`, `part.simg.0`~`.7`, `manifest.md5`, `build.log`, `MD5SUMS`, `README.txt`.
  - 중간 산출물 `rootfs.simg`는 지웠다(조각과 같은 내용).
  - 비밀 값이 들어 있어 저장소에 넣지 않았다.
- 다른 보드에 굽는 법과 구운 뒤 할 일은 BOARD-CLONE.md 11장에 적었다:
  - boot는 반드시 boot-nocpu를 쓴다.
  - 대상마다 `build-clone.sh`를 다시 돌린다(machine-id).
  - ACM이 없으니 ssh나 HDMI로 확인한다.
  - DVS 보드는 dvslive를 다시 켠다.
  - BT 스피커는 다시 페어링하고, `resize2fs`를 한다.
- 오늘 화면 캡처 세 장을 `docs/img/gpu-desktop-*-20261123.png`에 넣었다(11.90).

**안 한 것.** 이 이미지를 실제 다른 보드에 구워 보지는 않았다. 보드3 재부팅 확인(프롬프트, 키보드, `e850-x`, BT 재연결)도 아직이다.

### 11.92 보드5에 데스크톱 이미지 굽기, 부팅 대기(wait-online)와 bluealsa-aplay 고침 (2026-11-23)

**요청.** 새 보드5를 Mac에 연결했다. 11.91 이미지(`~/e850-logs/clone-20261123/`)를 굽는다.

**보드5.** fastboot 일련번호 `00000ae74e9c23e0`, Wi-Fi MAC `00:00:0f:0a:e7:4e`, 주소 192.168.0.206(DHCP). machine-id는 이미지에 넣은 `6f0d9b3894e235c97ecae32f79618492`다. UART는 꽂지 않았다. DVS 센서가 달렸는지는 확인하지 않았다.

**첫 boot 굽기가 멈춤(관찰).**
- `fastboot -s 00000ae74e9c23e0 flash boot`가 2분 넘게 끝나지 않았다. 정상이면 2.4초다.
- 끊은 뒤 `getvar product`에 8초 동안 답이 없었다. LK가 남은 데이터를 기다리며 멈춘 상태로 보고(7장, BOARD-CLONE 7장과 같음), 사용자가 전원을 다시 넣었다.
- 그때 보드5는 UART(FT232R, 보드3 UART `A5069RR4`)와 같은 USB 2.1 허브 뒤에 있었다.
- 다른 세션의 보드4 감시 스크립트(`b4-recover.sh`, 2026-11-21부터)가 5초마다 `fastboot devices`를 부르고 있었다.

**두 번째 굽기(바꾼 것 두 가지를 함께).**
- 사용자가 보드5 USB-C를 다른 포트로 옮겼다. 그래도 Mac은 `USB2.0 HUB` 뒤(Billboard 장치와 함께)로 본다. 직결은 아니다.
- 사용자 허락을 받아 감시 스크립트를 굽는 동안만 `SIGSTOP`으로 멈췄다. 굽기가 끝나고 `SIGCONT`로 다시 이어 갔다.
- 결과:
  - boot: 2.380초(보내기 1.461초, 쓰기 0.891초), `boot-nocpu`(md5 `25643a9a…`).
  - userdata: `fbmini`로 8조각을 차례로 구웠다. 모두 `OKAY`였다. 조각별로 175/100/33/41/92/48/80/8초, 합계 577초.
  - `fbmini reboot` 뒤 약 1분 만에 Wi-Fi로 찾았다(핑 스윕 + ARP에서 MAC으로).
- 허브 위치와 감시 스크립트 중 어느 것이 첫 멈춤의 원인인지는 가르지 못했다. 두 가지를 함께 바꿨기 때문이다.

**부팅 확인(보드5, ssh).**
- 확인된 것:
  - machine-id `6f0d9b38…`, 커널 `5.10.157-00010-g3b6b9ca51034-dirty`, 명령줄에 `cgroup_disable=cpu`가 있다.
  - zram 1,825,280 KiB.
  - `e850-usbhost`, `e850-wifi`, `e850-bt`, `bluealsa`, `e850-audio`, `zramswap`가 active다. `dvslive`는 inactive(이미지대로 꺼짐).
  - philip이 `netdev`와 `bluetooth` 그룹에 들어 있다.
  - `/dev/dri`는 없다. `e850-x`가 `e850-kms`를 올릴 때 생기니 정상이다.
- 파일 비교: 보드5에서 `usr lib var/tmp root home opt`의 파일 37,094개를 md5로 셌다. 이미지 `manifest.md5`와 다른 줄이 0이었다.
- `resize2fs /dev/mmcblk0p24`: 7.8 GB에서 54 GB로 늘렸다(14,148,084 블록).

**고친 것 1: `systemd-networkd-wait-online`이 실패해서 부팅이 `degraded`였다.**
- 관찰: 이 서비스가 2분을 기다리다 실패했다. 그동안 nginx가 `network-online.target`을 기다렸다(`systemctl list-jobs`).
- 원인: `e850-usbhost`가 부팅 때 호스트 모드를 켠다. 그러면 `usb0`(ECM gadget)는 DOWN으로 남는다. 이 서비스는 기본으로 관리하는 링크를 모두 기다린다. 올라온 것은 `wlan0`뿐이었다.
- 보드3도 같은 설정이라 다음 재부팅부터 같아질 것이었다. 보드3은 11.90에서 usbhost를 켠 뒤 아직 재부팅하지 않았다.
- 수정: 링크 하나만 올라오면 되게 했다. drop-in `systemd-networkd-wait-online.service.d/e850-any.conf`에 `ExecStart=… --any`를 넣었다.
  - 저장소: `features/usbhost/wait-online-any.conf`. `features/usbhost/feature.sh`의 install이 넣고 remove가 지운다.
- 보드에 바꾼 것(보드5, 보드3): 위 drop-in을 손으로 넣고, `daemon-reload`, `reset-failed`, 서비스 재시작을 했다.
  - 두 보드 모두 `wait-online=active`, `systemctl is-system-running` = `running`이 됐다.
  - 이 수정이 들어간 채로 재부팅했을 때 2분을 기다리지 않는지는 아직 보지 않았다.

**고친 것 2: `bluealsa-aplay.service` failed.**
- 관찰: 보드5와 보드3 모두에서 실패했다. 유닛은 `PrivateUsers=true`다. 커널에 `CONFIG_USER_NS`가 없어 `bluealsa`와 같은 이유로 실패한 것으로 본다(11.89). 이번에는 exit-code만 봤고 상태 217은 확인하지 않았다.
- 이것은 휴대폰에서 보드로 소리를 받는 A2DP sink 재생기다. 쓰지 않는다.
- 수정:
  - 두 보드 모두 `systemctl disable --now bluealsa-aplay`, `reset-failed`를 했다.
  - 저장소: `features/bt/feature.sh` install에도 같은 줄을 넣었다.
  - 보드3의 남은 `dvslive` failed 기록(11.90에서 끄기 전 것)도 `reset-failed`로 지웠다.

**보드의 기능 스크립트 사본.** 보드3·5의 `/opt/e850-features`는 11-20 묶음이다. 저장소와 다르다(bt는 11.89 전 판, usbhost에 `gpiohold.py`가 있음). 이번 두 수정은 보드에 손으로 넣었고, 사본은 바꾸지 않았다. 다음 `features/host/deploy.sh` 때 맞춰진다.

**복제 이미지.**
- `~/e850-logs/clone-20261123/part.simg.*`는 보드5에 쓴 machine-id를 갖고 있다. 다음 보드에는 `build-clone.sh`를 다시 돌린다(BOARD-CLONE 11장).
- 이 이미지에는 위 두 수정이 없다. 구운 뒤 따로 넣는다(BOARD-CLONE 11장에 명령을 적었다).

**안 한 것.**
- 보드5 HDMI 프롬프트, USB 키보드로 `e850-x`, 패널과 한글, BT 스피커 재페어링은 확인하지 않았다(사용자가 보드 앞에서 할 일).
- 보드3 재부팅 확인도 아직이다.

### 11.93 보드6: 데스크톱 이미지 + 현미경 뷰어 + 외부 링크 (2026-11-23)

**요청.** 보드6을 굽는다. 현미경 솔루션과 웹서비스까지 넣는다.

**사용자 결정.**
- 굽는 동안 다른 세션의 보드4 감시 스크립트를 일시 정지한다(보드5 때와 같음).
- 웹은 외부 링크(Cloudflare 임시 터널)까지 연다. 뷰어에 로그인이 없다는 것을 알리고 고르게 했다(11.75와 같은 조건).

**보드6.** fastboot `00000bf54c9c23e0`(보드4 `00000bf40c9c23e0`와 다른 보드), Wi-Fi MAC `00:00:0f:0b:f5:4c`, 주소 192.168.0.208(DHCP), machine-id `f580f9d9061fd6a5c6fbb3810c040dba`, UART 없음. Wi-Fi MAC은 fastboot 일련번호 가운데 세 바이트에서 나온다(보드5도 `0ae74e` → `00:00:0f:0a:e7:4e`).

**굽기.**
- 이미지: `build-clone.sh`를 새 machine-id로 다시 돌렸다(49초, 경고 0). 8조각으로 나눴다. 보드5 때의 build.log는 `build-board5.log`로 남겼다.
- 굽는 동안 감시 스크립트를 `SIGSTOP`했고, 끝난 뒤 `SIGCONT`로 다시 이어 갔다.
- boot 2.369초. userdata 8조각 모두 `OKAY`, 조각별 96/73/38/90/213/28/26/8초, 합계 572초. 보드는 보드5의 두 번째 굽기와 같은 USB 자리(`USB2.0 HUB`, Billboard 옆)에 있었다. 이번에는 멈추지 않았다.
- `fbmini reboot` 뒤 52초 만에 Wi-Fi로 찾았다.

**설치(Mac → scp → 보드6 root, `~/e850-logs/scope-board6/board6-setup.sh`).**
- 묶음 `scope-inst.tgz`(29.5 MB, md5 `49300231…`):
  - 저장소 `tools/board/scope/`의 `scope.py`, `scope_rec.py`, `scope.html`, `scope-run.sh`, `gpiohold.py`, `e850-scope.service`, `install.sh`(커밋 `5a2fc2f` 판, 고친 것 없음).
  - `e850-hubpwr.ko`(이미지의 extra 모듈과 md5 같음).
  - OpenCV: PyPI `opencv_python_headless-4.11.0.86` aarch64 휠을 풀었다. sha256 `6efabcaa…`가 `install.sh`에 적힌 값과 같다.
  - `features/usbhost/wait-online-any.conf`.
  - 다른 세션 진행 기록을 읽어 보니 11-20 뒤로 현미경 코드를 바꾼 흔적이 없었다. 보드4에서는 아무것도 가져오지 않았다.
- 스크립트가 한 일:
  - 11.92의 두 수정: wait-online `--any` drop-in, `bluealsa-aplay` 끄기.
  - `systemctl disable --now e850-usbhost`. 보드4처럼 부팅 때 USB 호스트 모드는 `e850-scope`가 맡는다. usbhost 유닛은 그대로 깔려 있다.
  - `install.sh /var/tmp/inst`로 `/opt/e850-scope`와 `e850-scope.service`를 넣고 enable했다. 사진과 기록은 `/var/lib/e850-scope`에 쌓인다.
  - `resize2fs`: 54 GB.
- tar가 "time stamp … in the future" 경고를 냈다. 보드 시계가 NTP로 맞춰지기 전(약 1시간 늦음)이었다. 내용과는 관계없다.

**외부 링크.**
- `cloudflared` 2026.11.1 linux-arm64를 Cloudflare 공식 GitHub 배포에서 받았다. 크기 37,818,397 B, sha256 `b155cec3…7c5a`가 릴리스 노트와 같다(보드4와 같은 판).
- `/home/philip/bin/cloudflared`에 넣었다. 11.75와 같이 philip으로 `setsid nohup … tunnel --no-autoupdate --url http://localhost:8090`을 실행했다. 로그는 `/var/tmp/cloudflared.log`다.
- 주소는 보드의 `/var/tmp/tunnel-url.txt`와 Mac의 `~/e850-logs/scope-board6/tunnel-url.txt`에 있다. 저장소에는 적지 않는다.
- 주소가 나온 뒤 Mac에서 처음 약 40초는 연결이 되지 않았다(curl 000). 공개 DNS(1.1.1.1)는 처음부터 주소를 돌려주고 있었으니, Mac 쪽 이름 풀이가 늦었던 것으로 본다(확인 안 함).
- 재부팅하면 터널이 꺼진다. 다시 띄우면 주소가 바뀐다. 부팅 때 자동으로 띄우지는 않았다(보드4와 같음).

**확인(관찰).**
- `systemctl is-system-running` = `running`, failed 0, zram 있음.
- 뷰어:
  - 같은 네트워크: `http://192.168.0.208:8090/` 200(117,614 B, 0.05초).
  - 외부 링크: 200(0.88초).
  - OpenCV 4.11.0이 `/opt/e850-scope/py`에서 올라온다.
- 처음에는 "현미경을 기다리는 중"이었다. Mac micro-USB가 꽂혀 있으면 USB 허브가 붙지 않는다(11.64). 사용자가 케이블을 빼고 키트를 꽂은 뒤 로그에 `streaming from /dev/video0 … 2304x1752 Hi-1634`가 나왔다. 외부 링크의 `/stat`은 3.36 fps였다(16M 모듈, 11.73의 3.37 fps와 같음).
- OTG 상태 `a_host`.

**50M 모듈로 바꾼 뒤(사용자가 모듈 교체, 관찰).**
- 로그: 05:11:27 `stream ended`, 05:12:37 `streaming … 2016x1536 Hi-5031Q`. 뷰어를 다시 띄우지 않아도 70초 만에 새 모듈로 이어졌다(11.73 수정).
- `/stat`: 칩 ID 0x5031, 4.34 fps(11.67의 4.37과 같음), `mipiErrors` 0. 20초 동안 80프레임 중 80개가 온전했고 잘린 프레임은 0이었다. 바꾼 직후에는 잘린 프레임이 16개 있었다(누적).
- 화면: LED를 끈 채 노출 125.5 ms(뷰어에 저장된 값)에서는 상이 없는 고른 회색(평균 111, 표준편차 1.6)이었다.
  - 뷰어는 밝기를 늘리지 않는다(`frame_png`는 2×2 평균만 함).
  - 노출을 바꾸면 평균 밝기가 따라 바뀌었다: 125.5 ms 103, 40 ms 42.5, 10 ms 22.
  - LED를 켜면(255, 10 ms) 평균 112, 표준편차 26.6이 되고, 작고 둥근 입자가 가득한 시료가 초점이 맞은 채 보였다.
  - 시험 뒤 원래 값(노출 1255, LED 0)으로 되돌렸다.
- 출력은 2016×1536(약 3.1 MP)이다. 50M 센서의 전체 화소가 아니다(11.67에서 정한 모드).

**알아 둘 것.**
- 뷰어의 `/quit`을 누르면 USB가 gadget 모드로 돌아간다. 그러면 USB 키보드와 현미경이 끊기고 ACM이 다음 부팅까지 돌아온다(보드4와 같음).
- `e850-x`(GPU 데스크톱)는 HDMI, 현미경은 웹이라 서로 겹치지 않는다. 둘을 함께 띄운 부하는 재지 않았다.

**안 한 것.** 보드6 재부팅 뒤 현미경 자동 시작, HDMI 프롬프트와 `e850-x`, BT 스피커 페어링은 아직 보지 않았다.

### 11.94 현미경 고해상도 사진(50M 모듈 4032×3072): 키트 펌웨어 형식 2와 뷰어 (2026-11-23)

**배경(사용자 결정).** 세포 수를 세려고 고해상도 사진을 찍는다. 영상은 필요 없고 사진은 늦게 와도 된다. 컬러와 흑백을 고를 수 있어야 한다. 키트 펌웨어(비공개 `sol-cx3-uvc` LOG 14)에 실시간과 별도인 사진 형식을 더했다.

**구조.**
- 키트: UVC 형식 2 `GREY`. 크기는 행 바이트 × 행이다(50M 모듈 5040×3072 = 15,482,880 B).
  - CX3는 한 프레임을 담지 못한다. 그래서 사진 한 장을 센서 프레임 3장에 나눠 보낸다. 프레임 p가 행 `r % 3 == p`를 보낸다.
  - 한 장이라도 깨지면 그 사진을 짧게 끝낸다. uvcvideo가 그 사진을 버린다.
  - 실시간 YUY2(2016×1536, 1008×768)는 그대로다.
- `tools/board/scope/scope_still.py`(새 파일):
  - `reorder`: 행 순서를 되돌린다.
  - `unpack_raw10`: RAW10을 푼다.
  - `live_view`: 펌웨어 실시간 Y와 같은 계산(시험용).
  - `develop`: 검은 수준 64를 빼고 8비트로 만든다. 색 필터가 있는 센서는 디모자이크하고 채널 중앙값을 맞춘다. 없는 센서는 화소를 그대로 둔다.
  - `--selftest`: 세 모듈 크기, 행 순서, 네 가지 색 배열, 화소 그대로 경로.
- `scope.py`:
  - 설정 `shot_res`(live/still), `shot_color`(grey/color).
  - `grab_still`이 실시간을 멈추고 v4l2-ctl로 사진을 받은 뒤 실시간을 다시 켠다. 제한 시간은 15 + 3n초다.
  - 손 촬영과 자동 촬영은 한 번에 하나씩 찍는다(`still_lock`).
  - 사진의 화소 크기는 실시간의 절반(1.12 µm)이다.
  - `/stat`에 `still`(크기, 펌웨어가 못 하면 null), `still_color`, `busy`가 있다.
  - diag에 `inFrameBytes inFrameMs stillFails`를 더했다.
  - `/set`이 빈 값을 받는다. 그전에는 시료 이름을 지울 수 없었다.
- `scope.html`:
  - 촬영 버튼 옆에 해상도("실시간 화질 2016×1536" / "고해상도 4032×3072")와 색 선택이 있다. 펌웨어가 못 하면 고해상도를 고를 수 없다.
  - 촬영 중에는 버튼을 막는다.
  - 실험 요약에 사진 종류를 보이고, 용량 추정이 사진 크기를 쓴다.

**확인(보드6, 키트 RAM 적재, 관찰).**
- 사진 크기와 행 순서:
  - 받은 파일은 늘 15,482,880 B의 정확한 배수였다.
  - 실시간 Y와 사진을 같은 식으로 줄인 것의 평균 절대 차이는 0.83이었다. 행 순서를 되돌리지 않으면 16.28이다.
- 센서 프레임을 일부러 깨뜨렸다(MIPI PHY 지연을 잠깐 바꿈). `stillFails`가 1 늘었고 파일은 정확히 6장이었다.
- 뷰어 고해상도 촬영:
  - 4032×3072 PNG(약 7.6 MB). 사진 받기 0.8~2.8초(10장 평균 1.8초).
  - 응답은 17~19.5초였다. 대부분 분석 시간(14~15초)이다. 보드6 설정은 최소 크기 100 µm이고, 이 설정에서 실시간 크기 분석은 2.0초다.
- 옛 펌웨어: 고해상도를 고를 수 없고, 요청하면 "이 키트 펌웨어는 고해상도 사진을 주지 않습니다".
- 사진 받는 중 키트를 리셋했다. 18초 뒤 오류가 왔고, 키트가 돌아오자 실시간이 이어졌다.
- 자동 촬영(10초 간격)과 손 촬영이 겹치지 않았다.

**50M 모듈의 색(가설).**
- 측정: LED와 실내광 두 조명 모두에서 2×2 네 자리의 원값 평균이 0.2% 안으로 같았다(예: 221.1 / 220.8 / 220.8 / 221.1). 영상 어디서나 같았고, 체커보드 무늬도 없었다.
- 색 필터가 없는 모듈로 보고 컬러 선택을 껐다. 사진은 흑백이고 화소를 그대로 쓴다(디모자이크는 해상도만 깎는다).
- 색 있는 물체로는 아직 확인하지 않았다.

**보드6 실시간이 2~4.4 fps로 떨어진 것(관찰).** 보드6의 Firefox(GPU 데스크톱 :1)가 뷰어 화면을 3시간째 열어 두고 있었다(CPU 94%). 잘린 프레임이 늘었다. 옛 키트 펌웨어로 바꿔도 같아서 이번 변경 탓은 아니다. 뷰어는 다른 PC에서 여는 편이 낫다.

**보드에 바꾼 것.**
- 보드6:
  - `/opt/e850-scope`에 새 뷰어를 설치했다.
  - `e850-wifi.sh`를 Wi-Fi IRQ → CPU7 판으로 바꿨다(11.69). 보드3을 복제한 이미지에 이 변경이 빠져 있었다. 보드3·보드5도 아직 옛 판이다.
  - 시험하는 동안 `e850-usbhost`를 켜 두었다(`e850-scope`가 하던 호스트 모드).
- 키트2: ③을 다운로드에 두고 새 펌웨어를 RAM에 올렸다. EEPROM은 그대로다.
- 보드6 갤러리에 시험 사진(시료 이름 t49, t410)과 자동 촬영 기록 하나가 남아 있다.

**50M 전체 화소 가능성 시험(키트 RAM, 버리는 펌웨어, 관찰).**
- 센서 전체 화소 모드를 폭 8064로 잘라 키트가 USB로 보내지 않고 받기만 하게 했다.
- 한 프레임 61,931,520 B(8064×6144, 10비트)가 정확히 들어왔다. MIPI 오류는 0이었다.
- 받는 속도 125 MB/s(주기 522 ms), 149 MB/s(439 ms), 174 MB/s(400 ms)에서 모두 온전했다.
- 영상 내용과 색 필터 여부는 USB로 꺼내지 못해 보지 못했다.
- 사진 하나를 센서 프레임 7~8장에 나눠 보내면 50MP 사진 한 장에 약 3초로 추정한다(2단계, 사용자 결정 전).
- 시험 뒤 고해상도 펌웨어를 다시 올리고 뷰어를 켰다(4.37 fps).

**저장 먼저, 분석은 뒤에서(사용자 결정, 같은 날 09:20).**
- 까닭(관찰): 08:54 고해상도 사진은 받기 0.9초, 현상·PNG 저장 약 1초, 분석 19.3초였다. 응답이 늦은 것은 거의 다 분석 때문이다.
- `scope.py`:
  - 손 촬영(`/capture`)은 PNG와 JSON(`pending: true`, 물체 수 없음)을 쓰고 바로 답한다. 분석은 `an_loop` 스레드가 차례로 한다. 끝나면 JSON을 다시 쓴다(임시 파일 → `os.replace`). 윤곽 JPEG와 기록(scope.db)도 이때 쓴다.
  - 대기열은 4장까지다. 넘으면 다음 촬영 응답이 그만큼 늦는다.
  - 자동 촬영은 예전처럼 분석까지 기다린다. 분석이 촬영 간격보다 길 때 밀린 사진이 메모리에 쌓이지 않게 하려는 것이다.
  - 뷰어가 다시 켜지면 `pending`으로 남은 사진을 PNG에서 지금 설정으로 분석한다. PNG가 없는 사진은 `pending`만 지운다(물체 수 없음).
- `scope.html`: 응답에 `pending`이 있으면 "물체 수는 분석이 끝나면 갤러리에 나옵니다"를 띄운다. 3초마다 `/photos`를 보다가 분석이 끝나면 물체 수를 알린다. 갤러리와 사진 창에 "분석 중"을 보인다.
- 확인(보드6, 관찰):
  - 가짜 카메라로 띄운 둘째 뷰어(포트 8091, `/var/tmp/scopetest`)로 시험했다. 고치기 전에는 응답에 `pending`이 없어 실패했다. 고친 뒤에는 응답이 `pending: true`, 물체 수 없음이었고, 약 1초 뒤 `/photos`에 숫자가 채워졌다. 멈춘 뷰어에 남긴 `pending` 사진은 다시 켠 뒤 분석됐다.
  - 실제 고해상도 사진 한 장(09:18, 시료 이름 `bgtest`로 잠깐 바꿔 찍음): 응답 3.2초, 물체 수는 클릭 뒤 18.5초에 채워졌다.
- 09:16 뷰어를 다시 설치한 뒤 키트가 RAM 펌웨어를 잃고 부트로더(`04b4:00f3`)로 떴다(관찰). 그때는 허브 전원이 다시 들어간 것으로 봤다. 그러나 10:10의 서비스 재시작만으로는 키트가 다시 붙지 않았다(아래). 09:16에 무엇이 전원을 끊었는지는 확인하지 않았다. `/var/tmp/sol`에서 `fx3flash.py load sol_uvc.img`로 다시 올렸다(4.36 fps, 고해상도 4032×3072). EEPROM에 넣기 전까지는 뷰어를 다시 켤 때마다 이 일을 해야 한다.
- 이때 사용자가 시료 이름 "고해상도비교실험…"으로 비교 사진을 찍던 중이었다(09:10 실시간, 09:12 고해상도). 설치와 펌웨어 다시 올리기로 약 2분 동안 현미경이 끊겼다.
- 분석 보고서(현재 문제와 E850 MIPI 직결 검토, 사진 포함)를 비공개 Artifact와 PDF로 만들었다. 공개 저장소에는 넣지 않았다.

**키트2 EEPROM에 고해상도 펌웨어를 썼다(사용자 요청, 09:25).** 재부팅 뒤에도 고해상도를 쓰려는 것이다. 보드6에서 키트 프로그래머로 썼고, 다시 읽어 이미지와 일치했다. 쓰기 전 내용(13절 통합 펌웨어)은 비공개 저장소 `backup/`에 두었다. 스위치 ③이 아직 다운로드라 키트는 RAM에서 같은 펌웨어로 돈다. ③을 NORMAL로 옮긴 뒤 EEPROM 부팅을 확인한다.

**EEPROM 부팅 확인(10:10~10:12, 사용자가 ③을 NORMAL로 옮김).**
- 먼저 `systemctl restart e850-scope`를 했다. 서비스는 다시 떴지만 키트는 다시 붙지 않았다. dmesg의 마지막 `usb 2-1.3` 연결은 09:25(EEPROM 쓰기 뒤 RAM 적재)였고, 진단 카운터(frames 11,747, streamStarts 3)도 그대로 이어졌다. 서비스 재시작은 키트 전원을 끊지 않는다.
- 그래서 실행 중인 키트(`04b4:00c3`)에 리셋 요청 0xE0을 보냈다(`CyU3PDeviceReset`, 칩 전체 리셋 → ROM 부트로더가 스위치를 읽음). 5초 뒤 부트로더(`FX3`)를 거치지 않고 바로 `SOL Microscope`(`00c3`, 480 Mbps)로 붙었다. EEPROM에서 부팅한 것이다.
- 뷰어는 저절로 다시 받았다. 리셋 뒤 60초: 진단 카운터가 새로 시작(streamStarts 1), 325프레임 모두 온전, MIPI 오류 0, 4.37 fps, `/stat`의 `still`은 [4032, 3072].
- 전원을 실제로 뽑았다 꽂는 시험과 EEPROM 부팅 상태에서 고해상도 사진 찍기는 하지 않았다. 사용자 갤러리를 건드리지 않으려고 사진은 찍지 않았다. EEPROM 내용은 RAM에서 시험한 이미지와 같다(09:25 다시 읽기 일치).

**안 한 것.** 사용자 시료로 실시간과 고해상도 비교, EEPROM 쓰기(허락 전), 8M·16M 모듈 사진(자체 시험만).

### 11.95 강의 자료 2종: 리눅스 bring-up과 현미경 실험실 (2026-11-23)

**사용자 요청.** "2개의 리포트를 써서 나에게 메일로 보내, 첫번째는 850보드의 모든 디바이스 드라이버 올린 순서와 방법 그리고 깃주소, 엑스윈도우 올리고, 최적화 한내용, GPU살린 내용등을 강의 자료 형태로 만들어 HTML편집가능 16:9 슬라이드로, 두번째는 지금까지 전자현미경 850에 연결하고 웹으로 실험실 만든것에 대한 모든것을 위와 같은 형식으로 정리해."

**만든 것(저장소, 아직 커밋 전).**
- `docs/E850-BRINGUP-LECTURE-20261123.html`(26장, 0.8 MB): 출발점, 부팅 구조, 복구 수단, 드라이버를 올린 순서 18단계(장치, 드라이버·방법, 막힌 곳, 기록 절), cpuidle, USB 가젯과 HDMI, fbcon, OV5647, Wi-Fi, CPU 8코어, DVS, `features/`, 오디오·BT·유선랜·MFC, X와 EMS cgroup 패닉, 최적화 2장, GPU 3장, 데스크톱 보드, 복제, 함정, 정리(저장소 주소).
- `docs/SCOPE-LAB-LECTURE-20261123.html`(21장, 1.0 MB): 목표와 구성, 데이터 흐름, 키트 펌웨어(백업·역분석·흑백 UVC), 실시간 전체 해상도가 안 되는 이유, 모듈 3종과 통합 펌웨어, LED, USB 호스트, 운영(서비스, Wi-Fi IRQ, 외부 링크), 뷰어, 서버, 분석 8단계, 실험·기록, 고해상도 사진, 실시간과 비교, 저장 먼저, 50M 시험, MIPI 직결, 함정, 정리.
- 틀은 `docs/FEATURES-LECTURE-20261121.html`과 같다: 글자를 바로 고침(contenteditable), 처음부터·이 장부터 발표, HTML로 저장(같은 파일 이름으로 내려받음), 인쇄·PDF(1280×720 쪽). 그림은 WebP·PNG를 파일 안에 넣었다.
- 생성 스크립트는 저장소 밖(scratchpad `decks/engine.py`, `deck1.py`, `deck2.py`, `check.py`)에 있다.

**넣지 않은 것.** SK hynix 데이터시트·셋파일 내용과 레지스터 값, Cloudflare 터널 주소, 보드 주소와 비밀번호. 센서 이름과 모듈별 크기·속도는 이미 공개 문서(11.67~11.74)에 있는 값만 썼다.

**확인(관찰).**
- 헤드리스 Chrome으로 모든 장을 1280×720으로 그려 넘침을 검사했다(내용 아래 끝 650 px, 각주 668 px, 폭 1281 px 한도). 두 덱 모두 넘친 장이 없었다. 검사기가 넘침을 잡는지는 일부러 넘치는 장을 넣어 확인했다.
- 인쇄 PDF: 26쪽과 21쪽, 쪽 크기 960 × 540 pt. 모든 쪽을 줄인 그림으로 봤다.
- 쓰는 중에 기록과 다른 숫자를 고쳤다: 현미경 프레임 손실은 "온전한 프레임 48~53% → 96~98%", LS flush 확인은 밤샘 시험이 아니라 시험 프로그램 PASS, DVS 칸의 "1000"은 근거가 없어 HDMI 30 fps로 바꿈, 복제 시간은 보드6 기록(8조각, 572초).

**공개 여부(사용자 확인 필요).** 현미경 자료 16장에 사용자 시료 사진(08:54 고해상도 사진)을 확대한 그림 두 장이 들어 있다. 같은 사진으로 만든 분석 보고서는 공개 저장소에 넣지 않았다. 이 HTML을 커밋하기 전에 사용자에게 물었다. 사용자 답: "공개해도 되, 푸시해, 샘플이미지야".

**메일.** 2026-11-23 09:49에 Mail.app으로 `philip.de.kim@yonsei.ac.kr`에 보냈다. 제목은 "E850-96 강의 자료 2종: 리눅스 bring-up · 현미경 실험실 (2026-11-23)"이고, 첨부는 PDF 2개(보기·발표용, 3.4 MB와 7.7 MB)와 HTML 2개(고치기용)다. 본문에 스위치 ③ 이야기와 공개 여부 질문을 넣었다. 약 80초 뒤 메일함에서 첨부 4개, 크기 17.2 MB인 한 통을 확인했다(처음 잠깐 같은 시각의 첨부 0개 항목이 함께 보였으나 다시 보니 message-id가 하나였다).

**저장.** 사용자가 "다 저장해", 이어서 "푸시해"라고 했다. 공개 저장소 `features-20261121`(11.92~11.95)과 비공개 `sol-cx3-uvc`의 `still-capture` 브랜치(LOG 14, 처음 올림)를 push했다. 키트 EEPROM 부팅은 같은 날 10:12에 확인했다(11.94).

### 11.96 BT 검색이 아무것도 못 찾음: 커널 검색 상태가 STOPPING에 갇힘, 브리지 감시 (2026-11-23)

**사용자 요청.** "BT가 잘 안잡혀, 잘 접속되도록 기능을 보강해" (보드6, 22:43 KST). 이어서 "BT음질이 안좋아"(23:01).

**관찰(보드6, 가동 17시간 42분).**
- `e850-bt`, `bluetooth`, `bluealsa` 모두 active였다. 사용자는 blueman-manager로 장치를 찾던 중이었다. `bluetoothctl show`는 `Discovering: yes`인데 장치는 하나도 없었다.
- btmon 15초 동안 HCI 명령·이벤트가 하나도 없었다(blueman의 RAW 소켓 열고 닫기만 있음). 컨트롤러는 `Read Local Version`에 4 ms 만에 답했다.
- 무선은 정상이었다. BlueZ를 거치지 않은 `hcitool inq`(10초)가 장치 3개를 찾았다: JBL Flip 6(RSSI −57), MacBook Pro, Samsung TV.
- 커널 mgmt: `Stop Discovery`가 Rejected(검색 중이 아님), `Start Discovery`가 Busy(정지 상태도 아님). 커널 소스(`net/bluetooth`)로 보면 이 둘을 동시에 만족하는 오래가는 상태는 STOPPING뿐이다. STARTING은 `discov_update()`가 바로 FINDING이나 STOPPED로 넘긴다.
- `hcitool lescan`의 `Command Disallowed`는 이상이 아니다. 컨트롤러가 LE 확장 광고를 지원해서(LE features `ff 79`), 커널이 확장 스캔 명령을 쓰고 일반 명령은 막힌다.
- 커널 로그에는 command timeout이 없었다(`Bluetooth: hci0: unexpected event for opcode 0x0000` 한 줄뿐, 부팅 때).

**원인(코드로 확인, 이번 고착의 직접 계기는 못 봄).** `hci_request.c`의 `discov_update()`는 STOPPING에서 정지 요청(`hci_req_stop_discovery`: Inquiry Cancel, LE 스캔 끄기, Remote Name Request Cancel)을 보낸다. 그 가운데 하나라도 오류로 끝나면 STOPPED로 넘기지 않는다. 그 뒤 STOPPING을 벗어나는 길은 Remote Name Request Complete 이벤트(`hci_check_pending_name`)뿐이다. Inquiry Cancel의 0x0c 경합은 이 커널에 이미 고쳐져 있다. 남는 후보(가설)는 펌웨어가 다른 정지 명령에 오류를 주고 이름 완료 이벤트를 보내지 않는 경우다.

**재현(결함 주입).**
- 브리지 복사본이 검색 중 "LE 확장 스캔 끄기"의 응답을 0x0c로 바꾸게 했다.
- 첫 시도에서는 결함이 검색 시작 때의 스캔 끄기(본딩된 MX Master용 수동 스캔을 끄는 것)에 걸렸다. 그래서 검색 시작만 실패했고 갇히지 않았다.
- 검색이 돈 뒤에 걸자 처음 증상이 그대로 나왔다: 정지 뒤에도 `Discovering: yes`, Start Busy.
- 밖에서 `hcitool cmd`로 스캔을 꺼 두는 방법은 재현되지 않았다. 커널이 그 응답을 보고 스캔 플래그를 지워서, 정지 요청에 보낼 명령이 남지 않았다.

**고침(`features/bt/e850-btbridge.py`).**
- 별도 스레드가 15초마다 본다. 브리지가 본 마지막 검색 명령(Inquiry, Inquiry Cancel, 이름 요청·취소, LE 스캔 파라미터·켜기/끄기, 일반·확장) 뒤로 30초가 지났고 bluetoothd가 검색 중이라고 하면(`busctl ... Discovering`) 갇힌 것으로 본다.
  - 판정에서 다른 명령은 뺐다. 첫 판은 "아무 HCI 명령 30초 없음"이었는데, blueman-manager가 연결된 장치의 RSSI·링크 품질·송신 전력을 1초마다 읽어서 결함 주입 시험에서 67초 동안 움직이지 않았다.
  - 정상 검색의 검색 명령 간격은 최대 약 11초다(LE 5.12초, Inquiry 5.12초, bluetoothd 재시작 약 5.6초, 22:48 기록).
- 1차 조치: 가짜 Remote Name Request Complete(status 0x02, 주소 0)를 커널에 넣는다. STOPPING이면 STOPPED로 가고, 다른 상태에서는 아무것도 하지 않는다(`hci_check_pending_name`). 연결은 건드리지 않는다.
- 2차 조치: 다음 검사에서도 그대로면 `bluetoothctl power off/on`을 한다(연결이 끊긴다).
- 0이 아닌 Command Complete/Status는 모두 로그에 남긴다(`hci command 0x....: status 0x..`). 다음에 무엇이 실패했는지 알기 위해서다.
- 감시를 처음에는 전달 루프 안에 넣었다가 별도 스레드로 옮겼다. `busctl`이 도는 수십 ms 동안 A2DP 패킷이 멈추지 않게 하기 위해서다(음질 문제 보고 뒤).
- 자체 시험(`--selftest`): 응답 코드 해석, 가짜 이벤트 틀, 검색 명령 판별(Read RSSI 제외), 판정 단계. 먼저 실패하는 것을 보고 구현했다.

**확인하지 못한 것.** 스레드 판의 감시가 결함 주입으로 만든 STOPPING을 실제로 푸는지. 그 시험 직전에 보드6이 네트워크에서 빠졌다(아래).

**보드6 네트워크 끊김(22:59 KST부터, 원인 모름).**
- 결함 주입 시험 때문에 22:55~22:58 사이에 BT 브리지를 세 번 내렸다 올렸다(`e850-bt` 정지, `btb-faultA`, `btb-faultA2`). 그 직후부터 ping, ssh, Cloudflare 터널(530, 뒤에는 응답 없음)이 모두 안 된다.
- 23:01에 사용자가 "BT음질이 안좋아"라고 했으니 그때 보드와 BT는 살아 있었다.
- 가설(측정 안 함): 브리지를 다시 열 때마다 WLBT 펌웨어의 BT 서비스가 멈췄다 켜지는데, 이것이 Wi-Fi 연결을 끊었다. Wi-Fi가 다시 붙으려고 계속 스캔하면 같은 안테나를 쓰는 A2DP가 밀려 소리가 끊긴다. 두 증상이 한 원인일 수 있다.
- 보드6에는 UART와 ACM이 없다(USB 호스트 모드). Mac에서 닿을 길이 없어 사용자가 볼 때까지 기다린다.

**보드6에 바꾼 것(마지막으로 본 상태).**
- 22:48 `bluetoothctl power off/on`(갇힌 상태가 풀렸다). 그 뒤 사용자가 JBL Flip 6과 MX Master(BLE HID, `event10`)를 페어링·연결했다. 둘 다 `Trusted: no`다.
- 22:55 `e850-bt.service`를 멈췄다. 지금 BT 브리지는 임시 유닛 `btb-faultA2`(`/var/tmp/btfix/btb-faultA.py`, 스레드 전 판에 결함 주입을 더한 것; 결함은 `/var/tmp/btb-fault-on`이 있을 때 한 번만 건다)다. 재부팅하면 `e850-bt`가 다시 뜬다.
- **끄지 않은 것:** 22:48에 띄운 `btmon -w /var/tmp/bt-fix.snoop`(nohup)이 계속 기록하고 있을 수 있다. A2DP를 쓰면 시간당 100 MB 넘게 쌓일 수 있다.
- `/var/tmp/btfix/`(새 브리지와 결함 주입 복사본), `/var/tmp/bt-*.snoop`, `/var/tmp/bt-fix.txt`.

**다음.** 보드6이 돌아오면 btmon을 끄고, `e850-bt`를 새 브리지로 되돌리고(번들 설치), 결함 주입으로 감시를 확인한다. 페어링한 두 장치를 trust하고, Wi-Fi가 끊긴 원인을 journal에서 찾는다. 음질은 Wi-Fi가 붙은 상태에서 다시 본다.

### 11.97 `dvs` 기능: X 터미널에서 DVS 디코더를 띄우면 창에 모양, 웹 :8080 (2026-11-23)

**사용자 요청.** "추가 기능으로 DVS 드라이버와 디코더도 넣어서, 터미널에서 실행 시킬 수 있도록 하자", "그냥 엑스윈도우 터미널에서 dvs decoder를 실행하면 쉐이프가 나오게 하고 웹서비스도 작동하게 해", "6번 보드에 설치해". 자러 가며 "DVS도 설치하고, 안정화도 해놔, 아침까지".

**설계.** 디코더(`libdvsview.so`)와 캡처(`ov5647-cap.py`), 웹(`dvsctl.py`)은 그대로 두고 그리는 곳만 X 창으로 바꿨다.
- `dvsview.c`:
  - `dvs_start(NULL, fps)`는 fb0 대신 메모리 960×720 페이지에 그린다. 1.5배 확대는 꺼진다(페이지가 960×720이라).
  - `dvs_frame(&w, &h, &stride)`가 그 주소를 준다.
  - `dvs_replay(path)`는 `dvs_record()` 파일(줄마다 패킷 워드, 0 워드)을 127줄 버퍼로 다시 묶어 `dvs_ingest()`에 넣는다. 속도는 디스플레이 시계(`S.t`)에 맞추고, 끝나면 되감아 되풀이한다. 되감을 때 시각 기준(`have_ts`)만 지운다. 그러지 않으면 뒤로 간 시각 64개를 버린 뒤에야 다시 맞춘다.
- `dvsx11.py`(새 파일): ctypes Xlib로 "DVS" 창을 만들고 그 페이지를 fps마다 `XPutImage`로 올린다.
  - SysV IPC가 없어 MIT-SHM은 쓰지 않는다.
  - 창 닫기, q, Esc는 프로세스에 SIGINT를 보낸다. 그러면 캡처 루프가 Ctrl+C 때처럼 스트림을 끄고 끝난다.
  - X 서버가 죽으면 Xlib이 바로 exit하지 않도록 IO 오류 처리기가 SIGINT를 보내고 기다린다.
- `ov5647-cap.py`:
  - `--x11`이면 `dvs_start(NULL)`, 창, fb0 주소 배너 끔(`dvsctl.start(..., banner=False)`; X도 fb0를 쓴다).
  - `--replay FILE`이면 센서가 없을 때(11.57의 "no sensor" 분기) 그 녹화를 되풀이한다. 페이지의 `no_sensor`에 "replaying ..."가 붙는다.
  - 화면 쪽 시작(`DvsLive()`)이 실패하면 스트림을 끄고 끝낸다. 전에는 켠 채로 죽었다(`stream_off()`로 뺌).
- `dvslive.sh`: `DVS_X11=1`이면 `--x11`, `/var/lib/dvsview/demo.gz`가 있으면 `--replay`, `DVS_LOG=-`이면 로그를 터미널로 보낸다. 서비스로 돌 때(HDMI)는 전과 같다.
- `features/dvs/`:
  - `dvs` 명령: X 터미널에서 실행하면 sudo(비밀번호)로 `DISPLAY`·`XAUTHORITY`를 넘긴다. `dvslive.service`가 돌고 있으면 멈췄다가 끝날 때 되살린다.
  - `feature.sh`: 모듈 3개(`fimc-is`, `is-cis-s5krc1s`, `csis-irqstat`)를 `extra/`에 넣는다. 다시 빌드하지 않고 보드들이 쓰는 e850-out 빌드(11-07, 11-13, 11-14)를 strip했다. 디코더, 스크립트, 데모, `dvslive.service`도 넣는다(서비스는 켜지 않음).
  - `bundle.sh`가 `libdvsview.so`를 Debian 12 arm64 Docker에서 빌드한다. 파일은 `tools/`에서 복사한다(저장소에 두 벌 두지 않음). 데모 녹화 `out/dvs-demo.gz`와 배너 글꼴 `out/dvsfont.json`은 git 밖이라 있을 때만 넣는다.
- 데모 녹화: 보드1 `/var/tmp/dvsrec/20261108T214957Z-stand_up.gz`(7초, 이벤트 1280만, 35 MB, 사람이 일어서는 장면).

**시험(Mac Docker, Debian 12 arm64).**
- `dvsview.c` 시험(`-DDVSVIEW_TEST`): 먼저 `dvs_frame`·`dvs_replay`가 없어서 실패하는 것을 보고 구현했다.
  - 메모리 페이지는 960×720, stride 3840, 검은색이다.
  - 재생: 1 ms 간격 50줄 녹화를 300 ms 동안 돌리면 버퍼 8개, 이벤트 400개다(제 속도로 되풀이).
  - 기존 시험도 모두 통과했다.
- `dvsx11.py --selftest`(Xvfb): 그린 페이지를 창에서 다시 읽어 초록 사각형과 검은 바탕을 확인한다. `put()`을 비우면 실패하는 것도 봤다.
- 끝에서 끝: 실제 녹화로 `dvs_replay` + `dvsx11` + `dvsctl`(:8080)를 함께 띄웠다.
  - 렌더러: 30 fps, 프레임당 이벤트 4~7만, 디스플레이 뒤처짐 34~45 ms, 데이터 99~100%. 7초 녹화가 끝난 뒤에도 같았다(되풀이 확인).
  - X 화면 캡처("DVS" 창 960×720)와 웹 `/frame.png` 모두에 사람 모양이 나왔다(ON 초록, OFF 빨강).
  - `/stats`의 `no_sensor`도 나왔다.
- 번들: 38 MB(데모 35 MB 포함), `fimc-is.ko` strip 후 7.6 MB.

**보드에서 확인하지 못한 것.** 보드6에 설치하지 못했다. 22:59부터 네트워크에서 빠졌다(11.96). `ov5647-cap.py --x11 --replay`의 센서 없음 분기는 V4L2 장치가 있어야 해서 Docker로는 시험하지 못했다. 보드6의 X에서 root가 창을 여는 것(`XAUTHORITY`)과 8080 포트가 비어 있는지도 보지 못했다.

**보드6 확인 결과는 11.98.** 6번에는 DVS 센서가 달려 있었다. 위의 "센서 없음" 가정은 틀렸다.

### 11.98 보드6: 밤새 Wi-Fi 끊김의 실제 모습, `dvs` 보드 확인과 고침, C 설정 패널, 자동 실행, BT SBC XQ (2026-11-24)

**사용자 요청(08:3x KST, 보드6을 쓰면서).**
- "현미경은 뺏어, DVS나 현미경은 연결하면 자동 인식으로 하게 해"
- "DVS는 C로 콘트롤 프로그램 만들어서 영상과 함께 띠워서 조정하자. 코드 있을꺼야 찾아 봐"
- "DVS는 영상이 매끄럽게 나오는데 GPU영향일까?", "dvs영상도 gpu를 사용해서 좀 더 빠르게 매끄럽게 보이게 할 수 있나?"
- "BT는 음질이 않좋은데, 보드탓인가?"

**밤새 끊김(관찰, 지난 부팅의 journal).** 보드는 멈추지 않았다. 커널 로그가 23:15 UTC(08:15 KST)까지 이어진다. 사용자가 08:16쯤 전원을 다시 넣었다.
- 13:58:38 UTC: 결함 주입이 LE 스캔 끄기에 0x0c를 돌려줬다(`failed to disable LE scan`).
- 13:58:39: wpa_supplicant가 같은 AP(5 GHz `…36:d6`)를 다시 스캔하고 13:58:45에 다시 붙었다(4-way 완료, carrier 회복). 그 앞의 끊김 사유는 로그에 없다.
- 그 뒤 **DHCP 주소를 다시 받은 기록이 없다.** networkd는 `DHCP lease lost` 다음 아무것도 남기지 않았다.
- `slsi_tx_data: no fcq for 33:…:00:16, drop Tx frame`(IPv6 멀티캐스트를 보낼 큐가 없음)이 재결합 전 28시간 동안 5번이었는데, 재결합 뒤에는 2분마다 났다(278번).
- 결함을 건 브리지는 스캔 끄기를 칩에 보내지 않고 가로챘다. 그래서 **컨트롤러가 밤새 LE 스캔을 계속했다**(10분마다 LE 이벤트 약 1,400개, 16:00~23:15 UTC btmon 기록). 14:00~16:00 기록은 비어 있다(이유 모름).
- `mx_recovery_count` 0: 펌웨어 패닉은 아니었다.
- 가설(확인 안 함): 재결합 뒤 드라이버의 데이터 경로(멀티캐스트·브로드캐스트 큐)가 서지 않아 DHCP DISCOVER가 나가지 못했다. 재결합의 계기가 BT 스캔 부하인지는 모른다. 이 상태를 되살리는 장치(주소 없음 감시)는 아직 없다(아래 "다음").

**`dvs` 보드 확인(보드6, `e850-feature install dvs`).**
- 6번 CN4에 DVS 센서가 있다. `S_INPUT 8192x127`, 센서 레지스터 읽기 정상.
- 첫 실행은 1초 뒤 끊겼다. fimc-is를 처음 올리면 `/dev/video*`가 새로 생기고, 떠 있던 **Firefox(사용자 것, :1)가 그 노드들을 카메라로 훑으며 열고 닫았다.** vendor 드라이버가 이것을 `sudden close, call sensor_front_stop()`으로 받아 센서를 멈췄다. 3초 동안 프레임이 없어 캡처가 끝났다. 같이 찍힌 `v4l_querycap` WARN은 부팅 때 `v4l_id`에서도 나는 것이다.
- 두 번째 실행(모듈이 이미 있음)은 정상이었다: 57 fps, 버퍼 38~69/초, 지연 29~62 ms. X 화면에서 "DVS" 창에 사람 모양을 확인했다. 웹 :8080 응답 200.
- 이때 드러난 래퍼 결함: 캡처가 끝나면(위처럼 끊김, 또는 웹에서 센서 설정을 바꿀 때 일부러 끝남) `dvs`가 통째로 끝났다. 서비스는 그때마다 다시 띄운다.

**고침.**
- `ov5647-cap.py`: SIGINT(q, Esc, 창 닫기, Ctrl+C)로 끝나면 종료 코드 130. SIGTERM(웹의 센서 설정, pkill)은 전과 같다. `--need-sensor`: 센서가 응답하지 않으면 스트림을 끄고 3으로 끝낸다.
- `features/dvs/dvs`: 캡처가 끝나면 2초 뒤 다시 띄운다. 130이면 끝, 3이면 "no DVS on CN4"로 조용히 끝, 바로 실패가 3번 이어지면 포기한다. `/run/dvs.lock`으로 하나만 돈다(둘이면 `dvslive.sh`가 서로의 캡처를 끈다). 손으로 띄운 `dvs`는 자동 실행 유닛 `e850-dvs`를 먼저 멈춘다. `--auto`이면 데모 대신 `--need-sensor`.
- `dvsview.c`: `dvs_get(name)`(아무 스레드에서나 읽기). `dvsx11.py`는 창에 올리는 횟수를 고정 30에서 디코더의 `fps` 설정으로 바꿨다. 패널에서 fps를 바꾸면 창도 따른다.
- 시험: `features/dvs/test-dvs.sh`(대역 `dvslive.sh`로 재시작 규칙 5가지; 고치기 전 판에서 실패하는 것을 봄). `dvsview.c` 시험에 `dvs_get`(없을 때 링크 실패를 먼저 봄).

**C 설정 패널 `dvspanel`(`tools/linux510/dvspanel.c`, GTK 3).**
- 찾은 코드: RP5 시절 "DVS Settings" 패널(`~/Documents/ChatGPT/DVS/hand-tuning/hdmi_controls.py`, PyQt5, 영상 창 오른쪽에 붙음, 2026-10-12)이다. C 코드는 없었다. 그 배치를 따르고 내용은 이 보드의 API에 맞췄다.
- :8080 웹이 쓰는 API의 클라이언트다(HTTP/1.0, 127.0.0.1). 표시 설정 7개(`fps`, `window_ms`, `fade`, `filter_ms`, 색, 뒤집기, `margin_ms`)는 바로 `/params`로 보낸다. `zoom`은 뺐다(메모리 페이지가 960×720이라 X에서는 효과 없음). 센서 2개(`margin`, `flush_us`)는 "적용"으로 `/sensor`에 보내고, 캡처가 다시 시작하면 값을 다시 읽는다. 맨 위 줄은 2초마다 `/stats`(fps, 이벤트/장, 버퍼/초, 지연). "표시 기본값" 단추.
- 창은 페이지가 처음 답할 때 보인다. 그래서 센서 없는 보드의 `--auto`에서는 나타나지 않는다. 자리는 DVS 창 오른쪽(x 968).
- `dvs`가 root로 함께 띄우고 끝날 때 끈다(`NO_AT_BRIDGE=1`).
- 시험(`tools/linux510/dvspanel-test.sh`, Docker Debian 12 arm64, Xvfb): JSON 읽기, 대역 서버에서 위젯 값 불러오기, 잔상 슬라이더를 100으로 옮기면 `POST /params {"fade": 100}`이 나가는지. POST를 지운 판에서는 실패하는 것을 봤다.
- 보드6 화면 확인: 두 창이 나란히 떴고, 보드에 저장된 값(57 fps, 잡음 거르기 4, 좌우 뒤집기)을 읽어 보였다. **사람이 패널을 눌러 바꾸는 것은 아직 확인하지 않았다.**
- `bundle.sh`가 Debian 12 arm64 Docker에서 빌드한다(`libgtk-3-dev`). 보드에는 `libgtk-3-0`이 있다(데스크톱).

**자동 인식.**
- 현미경: 이미 자동이다. `e850-scope`의 `scope.py`가 USB 비디오 장치를 1초마다 찾고, 빼면 기다리고, 꽂으면 잡는다(11.64~11.69 모듈 교체용). 08:44에 `/stat`이 "현미경을 기다리는 중"이었다.
- DVS: MIPI(CN4)라 전원이 켜진 채 꽂으면 안 된다. 그래서 "데스크톱이 뜰 때 센서가 응답하면 연다"로 했다. `modules/e850-kms/x-kms.sh` 끝에서 `dvs`가 깔려 있으면 `systemd-run --unit=e850-dvs -E DISPLAY=:1 dvs --auto`. 센서가 없으면 3으로 조용히 끝난다(fimc-is는 그때 한 번 올라간다).
- 보드6 확인: 같은 명령을 손으로 실행해 두 창이 떴다. `e850-x`로 데스크톱을 새로 띄워 확인하지는 않았다(사용자가 쓰는 중).
- **Firefox 함정:** 부팅 뒤 첫 실행 때 Firefox가 떠 있으면 위처럼 1초 만에 끊긴다. 이제는 `dvs`가 다시 띄우니 몇 초 뒤 이어진다. 데스크톱 시작 때는 보통 Firefox보다 먼저 뜬다.

**GPU 질문(측정한 것).** 창에 그리는 일은 CPU다(libdvsview, 프레임당 2~10 ms). 창으로 보내는 것은 XPutImage이고, GPU는 X 서버(glamor)가 화면에 올리는 데만 쓴다. 57 fps로 보낼 때 캡처 프로세스 CPU 116%, Xorg 53%, 전체 74% 놀고 있음(top 5초). GPU 데스크톱의 화면 전환은 11.85에서 초당 30번이 상한이었다. 그러니 57 fps가 화면에 다 보이는지는 모른다(재지 않음).

**BT 음질.**
- 관찰: JBL Flip 6로 가는 SBC가 bit-pool 40, Joint Stereo, 48 kHz → 279 kbps(지난밤 연결은 44.1 kHz). bluealsa 기본 품질이 "high"인데도 40이니 스피커 상한이 40이라고 본다(가설, 스피커 능력값은 못 봄).
- 바꿈: `--sbc-quality=xq`(bluealsa 4.0, `features/bt/bluealsa-sbc.conf` → `bluealsa.service.d/e850-sbc.conf`). 다시 연결한 뒤 **44.1 kHz Dual Channel bit-pool 38, 452 kbps**로 잡혔다.
- 44.1 kHz이면 Firefox의 48 kHz를 ALSA가 변환한다. 기본 변환기는 선형 보간이었다. `libasound2-plugins`(+`libspeexdsp1`, `libpam-systemd` 안 딸려 옴)를 넣고 `/etc/alsa/conf.d/60-e850-rate.conf`로 `speexrate_medium`. null 장치로 확인: `Converter: linear-interpolation` → `libspeex (external)`.
- **귀로 나아졌는지는 사용자 확인 전.** 떠 있던 Firefox는 새 ALSA 설정을 안 읽을 수 있다.
- 둘 다 `features/bt`에 넣었다(설치 때 함께 들어감).
- JBL과 MX Master를 trust했다.

**보드6에 바꾼 것.**
- 시험 흔적 지움: `/var/tmp/bt-*.snoop`(밤샘 16 MB 포함), `bt-fix.txt`, `btfix/`, `btb-fault-on`.
- `e850-feature install dvs` 두 번(11.97 판, 이 절의 판). 이때 `/opt/e850-features`가 통째로 바뀌어 **새 BT 브리지(감시 스레드)도 들어갔다. `e850-bt`는 다시 띄우지 않았다**(마우스와 스피커를 쓰는 중). 다음 부팅부터 쓰인다.
- `/var/tmp/e850-kms/x-kms.sh`에 자동 실행(원본 `x-kms.sh.prev-11.98`).
- `e850-dvs` 유닛으로 DVS 창과 패널을 띄워 두었다(q로 끝내면 다시 뜨지 않는다).
- bluealsa: `e850-sbc.conf`, `libasound2-plugins`, `60-e850-rate.conf`; bluealsa 재시작, JBL 다시 연결.
- fimc-is, is-cis-s5krc1s, csis-irqstat이 올라가 있다(재부팅 전까지). CAM devfreq 최대.

**확인하지 못한 것.** 감시 스레드가 STOPPING을 실제로 푸는지(11.96). 패널을 사람이 눌러 바꾸는 것. `e850-x`로 데스크톱을 처음부터 띄울 때의 자동 실행. 화면이 실제로 초당 몇 번 바뀌는지. BT 소리가 귀로 나아졌는지.

**다음.** Wi-Fi "연결됐는데 주소 없음" 감시(예: 3분 동안 IPv4가 없으면 `wpa_cli reassociate`, 그다음 `wpa_supplicant@wlan0` 재시작). 끊긴 상태를 일부러 만들 방법이 없어 시험 계획부터 세운다. 다른 보드에서 x-kms.sh는 복제 이미지 판이라 자동 실행이 없다(`dvs` 기능이 없으니 영향도 없다).

### 11.99 보드6: C 패널을 웹 설정창과 같게, 빠른 동작 때 어두운 프레임 고침, 얼굴 가로 길이 표시(YuNet) (2026-11-24)

**사용자 요청(09시경 KST, 보드6을 보면서).**
- "웹서비스에서 나오는 설정창의 정보들과 같이 c로 화면조정을 업데이트해줘"
- "화면에 보이는 가로 길이가 17센치야, 그래서 비율로 사람의 얼굴사이즈를 측정해줘, 현재 내 얼굴 사이즈는 19센치, 1미터 거리에서 화면에 보이는 내 얼굴길이는 3센치, 얼굴인식 cnn 모델 찾아서 내 얼굴의 위치를 잡고 가로 길이를 계속 보여주도록 해 , DVS화면에서"
- "동작이 빨라지면 dvs화면이 안나오네? GPU를 써서 해결해"

**C 패널 `dvspanel`을 웹 설정창(`dvsctl.html`)과 같은 내용으로.**
- 창 940×1000, 두 칸(각 칸 스크롤).
  - 왼쪽: 행동 판정(지금 상태, 후보, 최근 판정 변화 8줄), 웹 접속 주소, 통계 여러 줄(웹과 같은 문구, CRC 오류/s, PLL, 스트림 시작부터의 누적), 센서 없음 경고, 표시 단추(기본값·잡음 제거·잔상)와 설정 7개(웹의 이름·단위·설명), 센서 scan 속도 목록(웹의 `marginFor`/`scanFor`, 예상 scan, 지금 margin)과 MIPI flush, 적용 메시지(스트림이 내려갔다 돌아와야 "적용됨", 30초 넘으면 경고).
  - 오른쪽: 통계 그래프(기간 2분~1시간, 타일 11개는 웹과 같은 계산: 카운터 증가분·⚠, 그래프 12개는 cairo, 10초 평균, 6초 넘는 빈틈에서 선을 끊음, CSV는 `/var/tmp/dvs-stats-*.csv`), 행동 녹화(동작 9개, 4/6/10/20초, 400/409/507 메시지), 센서 레지스터 읽기(주소·개수, 16진·10진·2진 표).
- 뺀 것:
  - 미리보기와 자르기: DVS 창이 옆에 있다.
  - zoom(전체 화면): X 창에서는 효과가 없다.
  - **레지스터 이름과 묶음 단추: SK hynix setfile에서 온 것이라 공개 저장소의 C 소스에 넣지 않았다.**
- JSON은 cJSON(`libcjson1`, 보드6에 이미 있음, `dvs` 기능이 `apt_need`). 슬라이더 움직임은 웹처럼 120 ms 묶어 보낸다. 1초마다 행동·녹화, 2초마다 통계·그래프(`/history?since=`).
- 시험 `dvspanel-test.sh`(Docker Debian 12 arm64, Xvfb, 대역 서버가 `/params` `/sensor` `/stats` `/act` `/rec` `/history` `/reg`): 값 불러오기, 통계 문구, CRC 타일 "⚠ 10개", 주소, 행동 문구, 잔상 POST, 녹화 POST, 레지스터 표. `SHOT=`이면 화면을 찍는다. 통과.
- 보드6 화면(09:16 xshot): 두 칸 모두 실제 값으로 나왔다(행동 "앉음", 통계, 그래프). **사람이 눌러 바꾸는 것은 아직 확인하지 않았다.**

**빠른 동작 때 화면이 안 나옴(관찰 → 원인 → 고침).**
- 관찰(`/history`, 고치기 전): MIPI가 받고 버퍼에 못 들어간 프레임 0, 짧은 버퍼 0, 데이터 있는 시간 98~100%, 그리기 3~8 ms(57 fps의 예산 17.5 ms). 그런데 큰 움직임 구간(kB/버퍼 500~705)에서 **어두운 프레임이 2초에 41~74장**(최대 65%)이었다. 그리기 속도 문제가 아니라 그릴 데이터가 없던 것이다. 그래서 GPU로는 고쳐지지 않는다고 판단했다.
- 원인(코드): 잡음 필터(`filter_ms` 4, 보드6 설정)를 켜면 렌더는 필터 스레드가 넘긴 데까지(`fw`)만 그린다. 그런데 재생 지연(`S.delay`)은 디코더가 버퍼를 다 읽은 시점으로 쟀다. 필터 스레드는 버퍼 전체를 거른 뒤에야 `fw`를 옮긴다. 큰 움직임에서 필터 시간이 여유(`margin_ms` 15)를 넘으면 프레임 창이 `fw` 너머로 가서 비었다. 이것은 코드와 관찰이 맞아떨어져서 낸 결론이다. 필터 시간 자체는 재지 않았다.
- 고침(`dvsview.c`):
  - 지연 갱신을 `seen()`으로 뺐다. 필터가 꺼져 있으면 디코더가, 켜져 있으면 필터 스레드가 부른다.
  - 필터는 32,768개씩 걸러 `fw`를 옮긴다. 큰 버퍼도 앞부분부터 그릴 수 있다.
  - 렌더는 필터가 넘긴 마지막 이벤트 시각에서 프레임을 끝낸다(`drawable_end`). 빈 프레임 대신 직전 장면을 유지한다.
- **실수 하나:** 필터 스레드에서 링의 32비트 시각을 64비트로 되돌릴 때 `now_us()`를 기준으로 했다. 표시 시계와 단조 시계는 2^31 µs 넘게 다르다. 그래서 지연이 4295초 어긋났고, 보드6 DVS 창이 1~2분 동안 비었다(`-4283846 ms behind`, 0 events/frame). 기준을 `S.newest`(64비트 표시 시계)로 바꿨다. 시험도 표시 시계를 2^32 µs 너머에 두도록 고쳤다. 고치기 전 판에서 실패하는 것을 봤다.
- 시험: `drawable_end`(필터 켬/끔, 아직 거른 것 없음), 필터 스레드가 지연과 기준점을 정하는지(2^32 넘는 시계). 고치기 전에는 링크 실패, 그다음 기준점 불일치를 봤다.
- 보드6에서(고친 판): 09:16 화면에서 프레임당 13.8만 이벤트, 19.1 MB/s, 어두운 프레임 0장/2초. 보통 움직임에서도 0. **올린 뒤 몇 분만 본 것이다. 아주 빠른 동작을 여러 번 거친 기록은 아직 적다.**

**얼굴 가로 길이(`tools/linux510/dvsface.py`).**
- 모델: OpenCV zoo YuNet `face_detection_yunet_2023mar.onnx`(MIT, 232,589 B, sha256 `8f2383e4…52fa4`, GitHub `opencv/opencv_zoo`의 LFS 주소에서 받음). git에는 넣지 않는다. `bundle.sh`가 `out/face/`에 받고 체크섬을 본다.
- 입력 크기: 이 모델은 입력만 640×640으로 고정돼 있다. 안의 reshape는 -1, upsample은 배율이다. 그래서 `dvsface.py --free-size`가 입력 높이·너비를 이름 붙은 차원으로 바꿔 `out/face/yunet-free.onnx`를 만든다(Docker, `onnx` 패키지). 보드에는 `/var/lib/dvsview/face.onnx`로 간다.
- 입력: `dvs_surface()`(480×360, 지난 100 ms의 이벤트, 2×2 묶음)를 이벤트가 있으면 255로 이진화하고, 화면의 뒤집기를 그대로 적용해(얼굴이 바로 서게) 480×384로 패딩한다. 점수 0.4 이상 중 최고 하나를 쓴다. 가로 폭은 최근 5번의 중앙값이다.
- 길이 계산(사용자 측정값): 960 px 창이 모니터에서 17 cm, 1 m 거리의 19 cm 얼굴이 화면에서 3 cm. 그래서 cm = px × 17/960 × 19/3 = px × 0.1122. 3 cm(169 px) → 19.0 cm. **카메라 하나로는 거리를 모르니 이 값은 얼굴이 약 1 m에 있을 때만 맞는다.**
- 표시: `dvs_mark(x, y, w, h, label)`(새 함수). 렌더가 노란 2 px 상자와 그 위 `W 18.5 cm`(5×7 글꼴에 `c`를 더함)를 그린다. 0.6초 동안 새로 오지 않으면 사라진다. 화면(X 창, HDMI)에 그대로 나온다.
- 실행: `ov5647-cap.py --face MODEL`. `dvslive.sh`는 모델 파일이 있으면 붙인다. 스레드 `dvs-face`는 CPU4~6(`dvs-act`와 같은 코어), onnxruntime 1스레드.
- 시험(Mac Docker, onnxruntime 1.17):
  - 사진(scikit-image astronaut): 점수 0.93.
  - 그 사진의 윤곽선(Sobel 상위 6~20%, DVS를 흉내): 얼굴 54~91 px에서 0.44~0.88. 640×640으로 늘렸을 때는 절반 크기에서 못 찾았다. 그래서 자기 크기로 넣는다.
  - 빈 영상: 없음.
  - 화면 cm 계산.
- 보드6: 검출 한 번 134 ms(640×640일 때 300 ms). **아직 얼굴을 찾은 적이 없다.** 그때 센서에는 몸통만 보였고 얼굴은 화면 밖이었다. 실제 DVS 얼굴에서 되는지, 숫자가 19 cm 근처인지는 사용자 시험이 필요하다.

**보드6에 바꾼 것.** `e850-feature install dvs`를 세 번 했다(마지막 09:20). 그래서 `/usr/local/lib/libdvsview.so`, `/usr/local/bin/dvspanel`, `/usr/local/share/dvsview/dvsface.py`, `/var/lib/dvsview/face.onnx`가 들어갔다. `e850-dvs` 유닛은 매번 멈추고 다시 띄웠다. 시험용 화면은 `/var/tmp/facegrab/`에 있다.

**확인하지 못한 것.** 실제 얼굴 검출과 cm 값, 패널 조작, 아주 빠른 동작이 길게 이어질 때, `dvs-face`가 `dvs-act`(한 단계 약 35 ms)를 늦추는지.

**다음.**
- 사용자가 1 m에서 얼굴을 넣고 고개를 움직이며 시험한다. 못 찾으면 순서대로 해 본다: 이진화 대신 time surface 밝기, 필터를 통과한 이벤트만, 창 길이 50~200 ms, 점수 기준.
- 거리도 원하면, 얼굴 19 cm를 가정해 거리를 추정해 함께 보일 수 있다.

### 11.100 보드6: Wi-Fi "붙었는데 주소 없음"의 원인(같은 AP 재연결), 감시, 어두운 프레임 재확인, 얼굴 기록, BT 브리지 재시작 (2026-11-24)

BRINGUP_STATUS의 시작점(11.99 다음 할 일 4가지)을 이어서 했다. 사용자는 보드 앞에 있었고, 손 흔들기·Wi-Fi 시험·BT 재시작·sudo를 허락했다(sudo 비밀번호는 명령에만 썼다).

**1. 빠른 동작 때 어두운 프레임(11.99 고침의 확인).**
- 관찰(`/history`, 09:20~09:45, 고친 판): kB/버퍼 500~710, 57 fps인 39행에서 dips 중앙값 10/2초(9%), 90%값 32, 최대 63. 41 이상은 39행 중 2행. 고치기 전(11.99)에는 같은 구간에서 41~74였다.
- dips는 "최근 평균의 절반 미만인 프레임"이다. 화면이 검다는 뜻이 아니다.
- 센서 시각 분석(Mac): 손을 빠르게 흔드는 8초를 녹화했다(`POST /rec`, 5,955만 이벤트, 초당 750만, `~/e850-logs/dvsrec-dips/`). 보드 디코더(`tools/host/act/replay.sh`)로 풀어 센서 시각의 창으로 나눴다.
  - 시각은 982 µs마다 단조로 늘고 빈틈이 없다.
  - 같은 dips 규칙: 35 fps 4~6개/8초(창 위상 9가지), 57 fps 28~38개(7%), 120 fps 83개(8.7%). 평균의 5% 미만인(거의 검은) 창은 0개.
  - 같은 8초의 보드 지표(35 fps): 약 9개.
  - 11-08 앉기·서기 녹화 2개(초당 190만): 57 fps에서 0~1개.
- 판단: 고친 뒤 남은 dips는 손이 방향을 바꿀 때 이벤트가 실제로 줄어드는 것으로 본다. 렌더 코드에서 고친 뒤 dips가 생기려면 창 안의 데이터가 적어야 한다(디코더가 링을 4M 이벤트 넘게 앞서는 경우는 빼고). 보드 지표가 센서 시각 분석보다 약 1.5~2배인 것은 설명하지 못했다(창 끝이 멈춤·건너뜀으로 고르지 않은 탓으로 추정, 확인 안 함).
- 실패한 방법: `/evframe`으로 창을 이어서 읽는 프로브. Wi-Fi 왕복으로도, 보드 localhost에서도 큰 움직임 때 뒤처져 창 대부분이 링에서 밀렸다(1,735개 중 1,678개). 그래서 녹화로 바꿨다.
- 따로 보지 않은 것: `/stats`의 `act_lost`가 912,011,387이다(행동 분류 리더가 놓친 이벤트 누계).

**2. 얼굴 가로 길이(11.99) 기록.** journal(00:20 이후 약 8,000번 검출)에서 얼굴을 찾은 2초 구간은 5번이고, W 20.5~23.6 cm였다. 사용자 시험(00:57) 때 찍은 화면 4장은 모두 손 흔들기 장면이었고 상자가 없었다. 머리는 화면 왼쪽 위에 약 80 px로 작게 보였다(1 m보다 먼 것으로 추정). 1 m 정면 시험은 아직 못 했다. 가만히 있는 얼굴은 DVS 이벤트가 적다.

**3. BT 브리지 재시작(사용자 허락).** 01:01:48에 `systemctl restart e850-bt`를 했다. JBL Flip 6은 30초 뒤(01:02:19) A2DP SBC 44.1 kHz로 다시 붙었다. MX Master는 재시작 전에도 연결돼 있지 않았다. 이제 11.96 감시 스레드가 든 브리지가 돈다. 감시가 실제로 STOPPING을 푸는지는 아직 못 봤다.

**4. Wi-Fi "붙었는데 주소 없음".**
- 감시 `tools/linux510/e850-wifiwatch.sh` + `e850-wifiwatch.service`. wlan0에 IPv4가 180초 동안 없으면 단계를 하나 밟고, 180초마다 다음 단계로 간다.
  1. `wpa_cli disconnect` 다음 `reconnect`
  2. `wpa_supplicant@wlan0` 재시작 + `networkctl reconfigure wlan0`
  3. AP에 붙어 있을 때(COMPLETED)만, 끊김마다 한 번: WLBT 펌웨어 재시작(`mx_panic`, 11.31. BT도 함께 끊긴다)
  - 그다음 다시 1부터. 단계마다 journal에 남는다(`journalctl -u e850-wifiwatch`).
  - 새 이미지는 `build-rootfs.sh`가 넣는다.
  - 시험 `tools/linux510/e850-wifiwatch-test.sh`(대역 명령, 14개 통과). "한 번만" 규칙을 지운 판, 주소 확인을 망가뜨린 판에서는 실패하는 것을 봤다.
- 보드6 시험(root).
  - 시험 A(`tools/board/wifiwatch-trial.sh`, 01:03): tc로 DHCP를 막고 주소를 지웠다. WAIT 60. 이때 단계 1은 `reassociate`였다. 60초의 reassociate 뒤 networkd는 `Lost carrier`, `DHCP lease lost`, `Gained carrier`를 남기고 DHCP를 다시 하지 않았다. 81초에 막음을 풀어도 그대로였다. 단계 2(약 120초) 뒤 128초에 주소가 돌아왔다.
  - 시험 B(01:06): 막음 없이, 정상 연결에서 `wpa_cli reassociate`만 했다. 주소가 사라졌다. 60초의 `networkctl reconfigure`만으로는 안 됐다. 감시 단계 1(reassociate, 01:09)도 안 됐다. 단계 2(01:12:28) 뒤 7초 만에 DHCP 주소를 받았다. 보드는 약 6분 동안 네트워크에 없었다.
  - 커널 로그: reassociate마다 `slsi_connect ... vifStatus=2`이고 `CurrBssid`와 `NewBssid`가 같다. 연결된 채 같은 AP로 connect가 오면 드라이버는 `slsi_set_roam_reassoc()`(펌웨어 로밍 경로)로 간다. 그 뒤 `no fcq for 33:…, drop Tx frame`이 28번 났다(tx.c:546, AP 피어의 `scsc_wifi_fcq_transmit_data()`가 음수). wpa 재시작 때는 DEAUTH 다음 `vifStatus=0`(새 연결)이었고 바로 DHCP가 됐다.
  - **11.98 밤샘 끊김(11-23 13:58:45)도 `vifStatus=2`, 같은 BSSID였다**(지난 부팅의 커널 로그).
  - 결론(관찰 근거): 밤샘 끊김의 원인은 연결된 채 같은 AP로 다시 연결한 것이다. 그러면 SCSC 드라이버의 송신 큐가 프레임을 거부하고, DHCP 브로드캐스트가 나가지 못한다. 11.98의 "networkd가 아무것도 남기지 않았다"도 같은 모습이다. 그날 wpa_supplicant가 왜 다시 연결했는지는 모른다.
  - 그래서 단계 1을 disconnect+reconnect로 바꿨다. 시험 C(01:15): reassociate로 고장 냈다(12초부터 주소 없음). 28초에 disconnect+reconnect를 하자 DEAUTH, `vifStatus=0`이 찍혔고 carrier 1초 뒤 DHCP를 받았다(36초에 주소). 한 번 시험했다.
- **보드6에 바꾼 것.** `/usr/local/bin/e850-wifiwatch.sh`(새 단계 1), `/etc/systemd/system/e850-wifiwatch.service`(enable, active). 시험 파일과 기록은 `/var/tmp/{wifiwatch-trial,reassoc-trial,fresh-trial}.*`. tc 막음은 지워졌다(clsact 없음). `mx_recovery_count`는 0이다. 다른 보드에는 넣지 않았다.

**확인하지 못한 것.** 감시가 실제 밤샘 끊김을 되살리는지(재현은 reassociate로만 했다). 드라이버 근본 수정. 1 m 얼굴 시험. BT 감시 스레드의 실제 동작.

**다음.**
- 드라이버: `slsi_set_roam_reassoc()`로 같은 BSS에 다시 붙은 뒤 AP 피어의 fcq가 왜 열리지 않는지 찾는다. `scsc_wlan`은 다시 올리면 패닉하므로(11.78) 바꾼 모듈은 재부팅해야 쓰인다(허락 필요).
- 다른 데스크톱 보드(3, 5)에도 감시를 넣는다(sudo).
- 얼굴: 1 m 정면에서 고개를 움직이며 시험한다. 안 되면 11.99 "다음"의 순서대로 해 본다.

### 11.101 보드6 고장, 보드4에 보드6 상태를 다시 만들기 (2026-11-24)

**사용자 알림(11:1x KST):** "보드가 쇼트가 나서 죽었어. 내 실수로, 다시 다른 보드를 연결했으니 이미지와 어플리케이션 지금까지 했던 거 그대로 복사해." 죽은 보드는 보드6(192.168.0.208)이다.

**새 보드 찾기.**
- Mac에 ACM·USB 네트워크가 없고, Wi-Fi에서는 보드1(.236)만 응답했다. UART(A5069RR4)는 전원을 다시 넣어도 한 글자도 나오지 않았다. 이 보드에 UART가 제대로 꽂혀 있지 않은 것으로 본다(확인 안 함).
- `fastboot devices`에 `00000bf40c9c23e0`(보드4)이 보였다.
- **두 세션이 겹쳤다.** 다른 세션(`features-20261121`, 보드4 기능 작업)이 11-21부터 보드4를 기다리는 감시를 걸어 두었다. 사용자가 USB를 꽂은 11:20에 그 세션이 boot-nocpu를 굽고 video·bt를 설치한 뒤 재부팅 시험을 하고 있었다. 이 세션이 모르고 11:25에 `fastboot flash boot boot-nocpu`를 보냈다. 그 명령이 멈춰 11:27에 껐다. 그 세션이 boot 파티션 앞 64,933,888 B의 md5가 boot-nocpu(`25643a9a…`)와 같음을 확인했다. 그다음 보드4를 넘겨받았다.
- **보드4 부팅 실패의 원인(그 세션의 판단).** 그 전 두 부팅의 journal이 22.6 s와 26.5 s에서 끊겼다. 커널 마지막 줄은 24.07 s의 MFC release였다. `extra/`에 고친 `exynos_mfc`가 없었으니 11.81의 vendor MFC와 udev `v4l_id` 부팅 경합으로 본다. `/proc/last_kmsg`에는 LK 로그뿐이었다. 11.80(cgroup cpu) 때문인지 이것 때문인지는 그 세션이 boot-nocpu와 고친 MFC를 함께 넣어서 가르지 못했다. 11-21부터 보드4가 Wi-Fi에서 사라진 것도 이 부팅 패닉 때문으로 본다.

**보드4 자료 백업(사용자 선택: 덮어쓰기 전에 백업).** 11:30~11:37에 ssh에서 root tar로 Mac `~/e850-logs/board4-backup-20261124/board4.tar`에 받았다(3,154,319,360 B, md5 `282cbe11…`, 항목 4,037개). 담은 것: `/var/lib/e850-scope`(파일 1,122개, `scope.db` 포함. 11-19 실험 #1 사진), `/var/tmp`(DVS 녹화 42개 등), `/home/philip`, `/root`, `/var/lib/{dvsview,dvsact,bluetooth}`, `/opt`, `/etc`. 비밀 값(비밀번호 해시, ssh 키, Wi-Fi 비밀번호, BT 링크 키)이 들어 있으니 Mac에만 둔다.

**굽기.** `build-clone.sh`로 보드4용 이미지를 새로 만들었다(machine-id `606a4996…`, 경고 0). 보드6에 구웠던 조각은 `clone-20261123/board6-old/`로 옮겼다. `to-fastboot`으로 fastboot에 넣었다(11:37:28 → 11:38:58). boot는 이미 boot-nocpu라 굽지 않았다. userdata 8조각은 `fbmini`로 구웠다.
조각마다 걸린 시간은 190, 345, 86, 127, 224, 171, 182, 39초로 모두 OKAY였다(11:39~12:01). 12:01:51에 `fbmini reboot`를 보냈다.

**첫 부팅.** HDMI에는 root 프롬프트가 떴다(사용자 확인). 그런데 6분 동안 Wi-Fi에도 fastboot에도 나타나지 않았다. 사용자가 키보드로 `e850-usbhost off`를 쳤고, 그 뒤(보드 기준 13:56쯤 다시 부팅) .244로 붙었다. 처음 6분 동안 왜 안 붙었는지는 보지 못했다(UART 출력 없음, 그 부팅의 journal은 보지 않음).

**보드6 추가분 넣기**(`~/e850-logs/scope-board4/board4-setup.sh`, root, `systemd-run`으로 실행, 14:00).
- 11.92의 두 수정(wait-online `--any`, `bluealsa-aplay` 끄기).
- `e850-usbhost` disable. 현미경 뷰어가 부팅 때 USB를 맡는다.
- 현미경 뷰어: 저장소 현재 판(`scope_still.py` 포함, 11.94)과 11.93의 OpenCV·`e850-hubpwr.ko`. 묶음 `scope-inst.tgz`(md5 `be312f98…`).
- `resize2fs`로 54 GB.
- Wi-Fi 감시 `e850-wifiwatch`(11.100 판, enable).
- `x-kms.sh` 자동 실행 판. 복제본의 판과는 DVS 4줄만 다르다. 원본은 `x-kms.sh.prev-11.98`로 남겼다.
- `e850-feature install bt dvs`(묶음 `94731b4e`, 보드6에 쓴 것): `libasound2-plugins`(+`libspeexdsp1`), SBC XQ, speex 변환, 감시 스레드가 든 BT 브리지, `dvs`, `dvspanel`, `libdvsview.so`, 얼굴 모델.
- 화면 설정 `/var/lib/dvsview/display.json`을 보드6의 마지막 값으로 맞췄다(fps 35, `filter_ms` 4, 좌우 뒤집기, `margin_ms` 15. 원본 `.prev-11.101`).
- 결과: `system running`, 현미경 active, usbhost disabled, 감시 active.

**재부팅 뒤 확인(14:01).**
- `running`, 실패한 유닛 0. `cgroup_disable=cpu`.
- `e850-scope`, `e850-wifiwatch`, `e850-bt`, `bluealsa`, `e850-wifi` 모두 active.
- `scsc_wlan`·`exynos_mfc`는 `extra/`의 고친 판이 올라왔다. hci0 UP(BD `9C:23:E0:0B:F4:0C`). `mx_recovery_count` 0.
- zram 1.8 GB. 현미경 페이지 :8090은 Mac에서 200.
- `sudo e850-x`로 GPU 데스크톱(:1)이 떴다(glamor, DRI3).
- **DVS는 뜨지 않았다.** 자동 실행된 `dvs --auto`가 `no I2C response at 0x10` → "no DVS on CN4"로 설계대로 조용히 끝났다. 보드4 CN4에 지금 DVS 센서가 없다. 센서는 전원을 끈 채 옮겨 달아야 한다(MIPI).

**보드6에서 되살리지 못한 것.** Firefox 기록·북마크(복제본의 프로필이다), 보드6 `/var/tmp`의 녹화와 시험 파일, BT 페어링. 새 보드는 BD 주소가 달라 JBL과 MX Master를 다시 페어링해야 한다.

**하지 않은 것.** Cloudflare 임시 터널(:8090 외부 공개). 띄우려 했으나 이 세션의 권한 검사가 막았다(인증 없는 외부 공개). 사용자가 결정한다. 보드6과 같이 하려면 philip으로 `~/bin/cloudflared tunnel --no-autoupdate --url http://localhost:8090`을 띄운다(바이너리 `~/e850-logs/scope-board6/cloudflared-linux-arm64`, 11.93).

**보안.** 보드4에 사용자가 임시로 만든 `/etc/sudoers.d/90-claude-temp`(NOPASSWD)는 userdata를 덮으면서 사라졌다(14:00 확인, README만 있음). 그 세션에 알렸다.

### 11.102 보드4 부팅의 MFC 두 번째 경합 흔적, 보드6 되살아남, GPU 데스크톱에서 Chrome이 멈춤, 보드6을 보드4로 다시 복제 (2026-11-24)

**보드4가 굽고 난 첫 부팅에서 멈춘 것(관찰).**
- 그 부팅(journal `-b -3`)은 23.78 s에 `Internal error: Oops: 96000005 [#1] PREEMPT SMP`(CPU5, 주소 0x8 읽기)로 끝났다. 바로 앞 줄은 `abox_if_cmpnt_probe`였다. `panic_on_oops=1`과 `panic=0`이라 그 자리에서 멈췄다. call trace는 journal에 남지 않았고 pstore도 비어 있었다.
- 그다음 부팅의 커널 로그(`/proc/last_kmsg`, Mac `~/e850-logs/board4-last_kmsg-20261124.txt`)에 MFC 경합이 있었다. 이 보드에는 11.81에서 고친 `exynos_mfc`(`extra/`)가 올라가 있었다.
  1. 23.354 s: `mfc_core_probe is called`(modprobe 601).
  2. 23.353 s: udev `v4l_id`(625)가 `/dev/video6`을 열었다. `There is no main core`로 실패했다. 이것은 11.81에서 고친 경로이고 패닉은 없었다.
  3. 23.640 s: 다른 `v4l_id`(623)가 열기 경로에서 `mfc_alloc_firmware`를 불렀다. 펌웨어를 IOVA 0x10000000(PA 0x8a2400000)에 맵했다. 이때 probe는 아직 끝나지 않았다.
  4. 23.682 s: probe 자신의 `mfc_alloc_firmware`가 두 번째 버퍼(PA 0x8a2500000)를 같은 IOVA에 맵하려 했다. `Trying mapping 1MB@0x10000000 on valid FLPD` WARN(`samsung-iommu.c:590`)이 났고 `-EADDRINUSE`(-98)로 실패했다. 그래도 `mfc_core_probe is completed`.
- 해석(가설, 확인 안 함):
  - 11.81과 다른 두 번째 경합이다. `mfc_core_probe`가 core를 등록한 뒤 펌웨어를 잡기 전까지 틈이 있고, 그 틈에 열기 경로가 펌웨어를 먼저 잡는다.
  - `mfc_alloc_firmware`는 `core->fw_buf.sgt`가 있으면 바로 돌아간다. 그런데 probe가 두 번째 버퍼를 할당했다. 열기가 실패하면서 `fw_buf`를 풀고 IOVA는 남겼는지, 두 호출이 검사를 동시에 지났는지는 소스로 가르지 않았다.
  - 23.78 s Oops가 이 경합에서 나왔는지는 모른다(trace 없음). 같은 보드의 다음 굽기 뒤 부팅에서 23.84 s에 같은 경합으로 패닉한 trace가 남았다(아래 "보드6을 보드4로 다시 복제"의 부팅 A).
- trace를 남기려면 다음에 멈췄을 때 전원을 끊지 않고 워치독 리셋을 기다린다. 그래야 `last_kmsg`가 남는다. 보드4 UART도 다시 꽂아 본다.

**보드6 되살아남(14:1x KST).** 사용자: "보드6 살아 났어, 그대로 복사해 똑 같이". 보드6(.208, machine-id `f580f9d9…`)이 ssh에 응답했다(가동 4분, 루트 54 GB 중 6.8 GB 사용). 화면에는 DVS 창(얼굴)과 BT `JBL Flip 6` 연결이 보였다.

**GPU 데스크톱(:1)에서 Chrome이 뜨지 않음.** 사용자: "보드6에서 크롬브라우저가 안되네".
- 관찰:
  - `google-chrome-stable` 158.0.8127.36 arm64가 설치되어 있다(dpkg 11-23 13:55, 저장소 기능에는 없음).
  - lxpanel이 띄운 `chrome`은 스레드 1개로 CPU 95%를 1분 넘게 썼다. 자식 프로세스도 창도 없었다.
  - 그 환경에는 GPU 세션이 모든 클라이언트에 넣는 `LD_PRELOAD=…/libpanfrost_kbase_drm_shim.so`가 있었다(`x-kms.sh`).
- 시험:
  - `chrome --version`은 shim 없이 바로 끝났다. shim이 있으면 20초 시간 초과였다.
  - strace로 보면 라이브러리를 다 올리고 `getrandom` 네 번 뒤로는 시스템 호출 없이 돈다.
- 가설(확인 안 함): Chrome은 malloc을 자기 할당기(PartitionAlloc)로 바꾼다. 그 초기화 중에 shim이 가로챈 함수가 다시 malloc을 불러, 초기화 잠금에서 도는 것으로 본다.
- 해결: Chrome만 shim 없이 띄운다.
  - `features/desktop/google-chrome` 래퍼(`env -u LD_PRELOAD -u LD_LIBRARY_PATH -u LIBGL_DRIVERS_PATH`)를 둔다.
  - 보드에는 `/usr/local/bin/google-chrome`, `google-chrome-stable`(링크), `/usr/local/share/applications/google-chrome.desktop`(Exec만 바꿈)이 생긴다.
  - desktop 기능은 Chrome이 깔려 있을 때만 이것들을 넣는다. Firefox 우회(11.89)와 같은 방식이다.
- 확인:
  - shim 없이 띄운 Chrome 창이 :1에 그려졌다(xshot).
  - 래퍼를 넣은 뒤 사용자가 메뉴에서 띄운 Chrome(부모 lxpanel, 환경에 `LD_PRELOAD` 없음)이 "Sign in to Chrome" 화면을 띄웠다.
  - GPU 프로세스는 EGL 초기화에 실패하고(`GLX is not present`) 소프트웨어로 그렸다.
- 보드6에 바꾼 것: 위 세 파일을 넣었다. 멈춰 있던 chrome 2개(2054, 2689)를 껐다.

**Chrome GPU 가속(사용자: "크롬GPU가속해").**
- 원인 확인(소스): drm-shim의 `mmap()`/`mmap64()`는 먼저 `init_shim()`을 부른다(`src/drm-shim/drm_shim.c`). `init_shim()`은 처음 불릴 때 `_mesa_set_create`, `asprintf`로 메모리를 잡는다. Chrome의 PartitionAlloc은 초기화 도중(`getrandom` 뒤) 주소 공간을 `mmap`으로 잡는다. 그래서 초기화 중인 할당기로 malloc이 다시 들어가 돈다. strace 모양과 맞는다.
- 수정(Mesa 패치 `tools/gpu/mesa-kbase-g52-e850.patch`):
  - `fd < 0`(익명 매핑)이면 `init_shim()` 없이 `syscall(SYS_mmap, …)`로 바로 넘긴다.
  - shim fd는 렌더 노드를 열 때만 생기므로 음수일 수 없다.
  - shim만 다시 빌드했다(`build.sh`, 묶음 `mesa-kbase.tgz`의 shim md5 `505496dc…`).
- 시험(보드6):
  - 고친 shim으로 `chrome --version`이 바로 끝났다(전에는 20초 시간 초과).
  - `run-test.sh`의 세 검사(gles-test 정확도, subtex, inst)가 옛 shim과 새 shim에서 모두 PASS였다(1080p 368 → 351 fps, 같은 수준).
  - Firefox를 새 shim으로 headless 시작해 화면을 저장했다(종료 코드 0). GPU 창 Firefox는 다시 시험하지 않았다.
- Chrome 설정:
  - X :1에는 GLX가 없다(`xorg-kms.conf`가 끔). ANGLE 기본값(GLX)으로는 실패한다.
  - `--use-gl=angle --use-angle=gl-egl`이면 ANGLE이 Mesa EGL을 쓴다.
  - `--disable-gpu-sandbox`, `--ignore-gpu-blocklist`는 필요 없었다.
- CDP `SystemInfo.getInfo` 결과:
  - 렌더러 `ANGLE (Panfrost, Mali-G52 (Panfrost), OpenGL ES 3.1 Mesa 22.2.0-devel)`, `ANGLE_OPENGL_EGL`, Skia GaneshGL, `sandboxed: True`.
  - `gpu_compositing`, `rasterization`, `webgl`, `2d_canvas`, `video_decode`가 `enabled`이고, `video_encode`는 software다.
- 측정과 확인(DVS 라이브 CPU 약 180%와 사용자 YouTube가 같이 돌 때):
  - WebGL Aquarium(물고기 500, 캔버스 1024×1024)은 10~13 fps이고 그림이 정상이다.
  - 한글 위키백과 페이지는 글자·표가 깨지지 않는다(xshot).
  - GPU 샌드박스를 켠 채로 GPU 프로세스가 1분 47초 동안 죽지 않았다.
  - YouTube는 GPU Chrome에서 재 보지 않았다.
- 래퍼 `features/desktop/google-chrome`: `DISPLAY`가 `:1`이면 shim 환경을 그대로 두고 `--use-gl=angle --use-angle=gl-egl`을 붙인다. 그 밖에서는 전처럼 shim을 뺀다. 메뉴에서 띄운 판으로 확인했다(GPU, 샌드박스 켬).
- 보드6에 바꾼 것:
  - `~/gpu/mesa-kbase/lib/libpanfrost_kbase_drm_shim.so`를 새 판으로 바꿨다. 이전 판은 `.prev-11.102`로 남겼다. 이미 떠 있는 Xorg :1과 프로그램은 옛 판을 쓴다.
  - `/usr/local/bin/google-chrome`을 새 래퍼로 바꿨다.
  - 시험용은 `~/gpu/shim-test/`에 남겼다.
  - 이미 떠 있던 사용자 Chrome은 소프트웨어 판이다. 닫고 다시 띄워야 GPU를 쓴다.

**DVS 화면을 GPU로 그림.** 사용자: "dvs쪽으로 들어오는 영상이 매우 빨라서 이미지 처리를 잘 못 해 GPU를 잘 활용해서 깨짐없이 보이도록 해".
- 전 상태(관찰, 보드6, 공이 도는 장면):
  - 프레임당 이벤트는 약 95만~107만이다. 창이 한 프레임(28.6 ms, 35 fps)이니 초당 약 3,500만 이벤트다.
  - CPU 렌더는 21 ms(최대 23.7)였고 최신 이벤트보다 88~118 ms 늦었다. 디코더, 필터, act 스레드가 각각 코어 하나를 다 썼다.
  - 필터는 이벤트당 39 ns(11.45)다. 초당 3,500만 이벤트면 코어 1.4개 분량이라 따라가지 못한다.
  - 링(4M 이벤트)은 약 115 ms 분량이다. 재생 지연, 창, 필터 되돌아보기를 합하면 110 ms 안팎이었다. 그래서 렌더러가 읽는 동안 디코더가 덮어쓰는 경계에 있었다. 이것이 잘린 프레임의 원인인지는 따로 재지 않았다(가설).
  - 코드 구조상 CPU 화면은 그린 페이지를 XPutImage로 보내는 동안(약 20 ms) 다음 프레임을 같은 페이지에 그린다.
- 구조:
  - `dvsview.c` `dvs_share()`: 이벤트 링과 프레임 정보를 memfd 하나에 둔다(헤더, 960×720 오버레이, 링). 렌더 스레드는 프레임마다 [l, a, b), end, 설정을 seqlock으로 쓰고 futex로 깨운다.
  - `dvsgl.c`(새 프로그램): X 창 "DVS"에 GLES 3.1 compute로 그린다.
    - pass 1: 이벤트마다 저장 버퍼에 `atomicMax`로 픽셀별 마지막 이벤트(시각, 극성)를 남긴다. 이 Mesa에는 `imageAtomicMax`가 없어서(셰이더 오류 `no function with name 'imageAtomicMax'`) 이미지 대신 저장 버퍼를 쓴다.
    - pass 2: 픽셀마다 이웃 8개의 마지막 이벤트가 filter_ms 안에 있으면 그린다. 배경 잡음 필터를 GPU에서 하는 것이다. fade도 여기서 한다.
    - pass 3: 오버레이(상자, 얼굴 표시)를 얹어 창 크기에 맞춘다.
    - 키: q, Esc, 창 닫기는 종료 0이다. f, F11은 전체 화면이다.
  - GPU 뷰어가 따라오는 동안(16 프레임 안) CPU는 8장에 1장만 그린다. 웹 페이지 `/frame.png`용이고 필터를 하지 않는다. 그동안 CPU 필터 스레드는 쉬고, 재생 지연은 디코더가 정한다.
  - `dvsx11.py` `start_gl()`:
    - `ov5647-cap.py --x11`이 `dvs_share()` 뒤 `dvsgl FD`를 GPU Mesa 환경(`/home/philip/gpu/mesa-kbase/lib`, kenv와 같음)으로 띄운다.
    - dvsgl이 0이 아닌 값으로 끝나면 예전 XPutImage 창으로 돌아간다. `DVS_GL=0`이면 GPU 뷰어를 쓰지 않는다.
    - GPU Mesa나 `/usr/local/bin/dvsgl`이 없는 보드는 전과 같이 동작한다.
- 보드에서 찾아 고친 것(순서대로):
  1. 처음 띄웠을 때 dvsgl은 초당 6.7장이었고 10초에 258번 "lapped"였다(복사하는 사이 디코더가 덮어씀). 링 4M(약 115 ms)이 모자랐다. 그래서 `RING`을 1<<24(16M 이벤트, 128 MB, 약 450 ms)로 키웠다.
  2. 그러자 지연이 77 ms에서 약 290 ms로 늘었다. 필터 스레드는 `RING / 2`만큼 뒤처질 때까지 건너뛰지 않는다. 필터가 켜져 있으면 재생 지연은 필터가 지난 곳에서 정해진다(11.99). 고친 것은 두 가지다.
     - 필터가 건너뛰는 한도를 `FLAG` 2M(예전 `RING / 2` 값)으로 링 크기와 뗐다.
     - GPU 뷰어가 있으면 디코더가 지연을 정하고 필터는 쉰다.
  3. 렌더 스레드가 19 ms(최대 45 ms)로 주기 28.6 ms를 넘었다. a, b, l, m 네 포인터를 이벤트 하나씩 넘기며 프레임마다 약 400만 항목을 읽었기 때문이다. 링의 시각은 순서대로다(`S.t`는 dt ≥ 0으로만 는다). 그래서 이분 탐색 `seek()`로 바꿨고 1.2 ms가 됐다.
  4. 8장에 한 번 CPU 프레임(약 25 ms)을 그리는 동안 GPU 발행이 밀렸다. 발행을 CPU 그리기 앞으로 옮겼다.
- 결과(보드6, 같은 장면, 1분):
  - dvsgl은 초당 35.0~35.1장으로 렌더 35 fps와 같다. 프레임당 이벤트는 약 122만(창과 필터 되돌아보기)이고, CPU+GPU 시간은 프레임당 22.5 ms, lapped는 0이다.
  - 최신 이벤트보다 31~35 ms 늦다(전에는 77~118 ms). 어두운 프레임은 0이다.
  - 렌더 스레드는 평균 3.6 ms다. 최대 30~34 ms는 8장에 한 번 그리는 웹용 CPU 프레임이다.
  - dvs-filter 스레드가 top에서 빠졌다(전에는 100%). dvsgl은 CPU 25~37%를 쓴다.
  - xshot으로 보면 GPU 창은 공 전체와 배경 윤곽이 빠짐없이 나온다. 웹 `/frame.png`는 필터가 없어 바탕에 잡점이 있다.
- 시험:
  - `dvsgl --selftest`: 합성 장면 10가지(링 감김 포함). 보드6에서 kenv로 돌려 ok.
  - dvsview 테스트: 모두 ok(`share ok`, `gl view ok`). `seek()`를 일부러 틀리게 바꾸면 실패한다.
  - `dvsx11.py --selftest`(Xvfb): dvsgl이 실패하면 CPU 창, 0으로 끝나면 SIGINT인 시험을 더했고 ok.
- 확인하지 않은 것:
  - 눈으로 본 tearing 여부. 창 모드에서는 X가 복사하고, e850-kms에는 vblank가 없다.
  - 전체 화면(f)에서 flip하는지.
  - 센서 없이 demo를 재생할 때.
  - 메모리: 링이 128 MB라 python RES가 105 MB에서 220 MB로 늘었다.
- 보드6에 바꾼 것:
  - `/usr/local/lib/libdvsview.so`, `/usr/local/bin/ov5647-cap.py`, `/usr/local/bin/dvsgl`(새), `/usr/local/share/dvsview/dvsx11.py`.
  - 이전 판은 `/var/tmp/dvs-prev-11.102/`에 있다. 시험 파일은 `/var/tmp/dvsgl-test/`에 있다.
  - 캡처를 SIGTERM으로 다시 시작했다(`dvs`가 다시 띄움).
- 저장소: `tools/linux510/dvsgl.c`(새), `dvsview.c`, `dvsx11.py`, `ov5647-cap.py`, `features/host/bundle.sh`(dvsgl 빌드), `features/dvs/feature.sh`(설치와 삭제).

**보드6을 보드4로 다시 복제, 이미지를 Mac에 저장.** 사용자: "보드6 살아 났어, 그대로 복사해 똑 같이", "향후 이 전체를 복사할 수 있도록 이미지로 만들어서 맥에 저장해둬". 11.101에서 다시 만든 보드4 대신 보드6 루트 파일시스템 전체를 쓴다.
- 받기:
  - 14:17~15:07 KST에 `rootfs-send`(root, Nice 10)로 받았다. 7,430,318,080 B, 약 2.4 MB/s.
  - Mac 쪽 md5 `7b2c7466…`가 보드6 값과 같다.
- tar를 뜬 뒤 바뀐 파일(`find / -xdev -newermt`)에서 6개를 `overlay/`에 넣었다.
  - 넣은 것: shim, Chrome 래퍼, dvsgl, libdvsview.so, ov5647-cap.py, dvsx11.py.
  - 넣지 않은 것: 시험 사본(`/var/tmp/dvsgl-test`, `~/gpu/shim-test`), 로그, BT 캐시.
- 이미지:
  - `build-clone.sh`에 `MACHINE_ID` 환경 변수를 더했다. 같은 보드에 다시 구울 때 그 보드의 id(그래서 DHCP 주소)를 유지한다.
  - `BLOCKS=2621440`(10 GiB), 보드4 id `606a4996…`. e2fsck 정상, 7.0 GB 사용.
  - 700 MiB 조각 11개로 나눴다.
- 보드4를 fastboot에 넣기:
  - 부팅 때 `e850-scope`가 USB 호스트를 맡아서 Mac에는 아무것도 보이지 않았다.
  - `systemctl stop e850-scope`로 gadget이 되자 ACM이 보였다. 그래서 케이블이 Mac에 꽂혀 있음을 알았다.
  - 덮기 전에 `board4-setup.{sh,log}`와 `.bash_history`를 `~/e850-logs/board4-backup-20261124/before-b6-clone/`에 남겼다.
  - `to-fastboot` 15:24:42 → fastboot 15:25:25.
- 굽기: 11조각 모두 OKAY였다(15:25~15:53, 조각당 127~335초, 합계 1,704초). 15:53:56에 reboot를 보냈다.
- 굽고 난 뒤 부팅(관찰, last_kmsg는 Mac `~/e850-logs/board4-last_kmsg-20261124-b.txt`):
  1. 부팅 A: 21.03 s에 루트를 마운트했다. 23.84 s에 `v4l_id`(635)에서 Oops가 나 `Kernel panic - not syncing`이 됐다.
     - trace: `mfc_open` → `mfc_rm_instance_init` → `mfc_alloc_firmware` → `mfc_iommu_map_firmware` → `iommu_map_sg` → `__iommu_map_sg+0x4c`, 주소 0x8 NULL 역참조.
     - 같은 때 `modprobe`(594)가 `mfc_core_probe`를 돌고 있었다(`[QoS]-------------------Default table` 줄 바로 뒤).
     - 이 절 첫머리의 "MFC 두 번째 경합"이 실제로 패닉을 낸다는 것이 확인됐다. probe가 끝나기 전에 열기 경로가 IOMMU 맵을 부른다. 그 시점에 무엇이 NULL인지는 소스로 확인하지 않았다.
  2. 부팅 B:
     - 5 s부터 eMMC `cqhci: error IRQ … cmd error -110`과 `I/O error`(sector 122142712 등)가 났다.
     - 28.5 s에 initramfs가 `mounting /dev/mmcblk0p24 on /newroot failed: Invalid argument`로 셸에 떨어졌다.
     - ACM으로 본 슈퍼블록은 정상이었다(UUID, 라벨, 블록 수). 그 셸에서 dd로 읽자 다시 시간 초과가 났고, 워치독 리셋으로 fastboot에 들어갔다(16:02).
  3. 부팅 C: `fastboot reboot` 뒤 정상이었다. `running`, eMMC 오류 0.
  - 해석(가설): 패닉 뒤 리셋을 거치면서 eMMC가 command queue 상태를 풀지 못하고 다음 부팅까지 이어졌다(전원은 끊기지 않았다). 11.101 첫 부팅의 6분 공백도 같은 것이었는지는 모른다.
- 확인(부팅 C):
  - machine-id `606a4996…`, 주소 .244. `resize2fs`로 54 GB가 됐다.
  - `e850-scope`, `e850-wifiwatch`, `e850-hdmi`, nginx는 active다. `e850-usbhost`와 dvslive는 disabled다. failed 유닛은 0이다.
  - overlay 파일의 md5가 보드6과 같다.
  - 전체 manifest 대조(42,074개): 모두 같다(`md5sum -c --quiet` 출력 없음, rc 0).
- Mac 이미지: `~/e850-logs/clone-20261124-b6/`(권한 700)에 README.txt, MD5SUMS, boot.img(boot-nocpu)를 같이 두었다. 절차는 BOARD-CLONE.md 12장이다.
- 보드4에 바꾼 것: userdata 전체(보드6 상태). boot는 그대로 boot-nocpu다.

### 11.103 보드6: 행동 분류의 이벤트 묶기를 C로, 캡처 종료 때 exit 134, 화면 해상도 고르기(배율) (2026-11-25)

**CPU4가 99%인 까닭.** 사용자: "cpu번이 거의 99% 이상 사용중이고 7번은 놀고 있는데 이유는?"
- [관찰] CPU4~6에는 행동 분류(`dvs-act`)와 얼굴(`dvs-face`)이 있다. 20초 동안 스레드 CPU를 재 보니 `dvs-act`가 94.9%였다. CPU7은 웹(`dvs-web`)과 Wi-Fi IRQ 전용이라(11.30, 11.45, 11.69) 거의 0이다.
- [관찰] 예전 `dvs-act`는 Python에서 `dvs_events()`로 이벤트를 받아 numpy로 60 ms 칸에 셌다. 이것이 센서 속도를 따라가지 못해 링 규칙으로 건너뛰었다. 45초 실행에서 `lost 1084759148`이었다(그때 추정한 전체 약 16억 개의 약 70%).

**이벤트 묶기를 C로.** 사용자: "좋아 c로 해야지".
- `dvsview.c`:
  - `struct dvs_binner`, `dvs_bin_run(s, ring, size, wr, acc)`, `dvs_act_bin(s, acc)`를 넣었다. 60 ms(`STEP_US`)마다 2×120×160 칸을 세고, 3초(`GAP_US`) 넘게 비면 처음부터 다시 센다.
  - `dvs_events()`와 `dvs_cells()`는 지웠다.
- 속도(같은 이벤트 약 210만 개, `binbench.py`):
  - 첫 판은 30~35 ns/이벤트였다. 구조체 필드 별칭 때문에 매번 메모리를 읽었고, 이벤트마다 `%`를 계산했다.
  - 로컬 변수와 링의 연속 구간 단위로 바꾸자 22.5 ns가 됐다.
  - 칸 안의 이벤트만 세는 빠른 안쪽 루프를 넣자 약 20 ns가 됐다.
- `dvsact.py`: 스레드를 둘로 나눴다. 둘 다 CPU4~6에 고정한다(`dvsctl.pin`).
  - `dvs-bin`: C 묶기를 60 ms마다 돈다.
  - `dvs-act`: `queue.Queue(8)`에서 받아 onnxruntime 추론과 판정을 한다.
  - 2초마다 `[dvsact] steps N, step median …, queued Q, lost L`을 찍는다.
- 결과(보드6, 같은 장면, 스레드 CPU 20초, A/B):

| | 예전 | 새 판 |
|---|---|---|
| 스레드 CPU | `dvs-act` 94.9% | `dvs-bin` 75.2% + `dvs-act` 59.8% |
| 판정 | 칸을 건너뜀(위의 lost) | 2초에 33~35번(기대 33.3), step 중앙값 34~38 ms, 최대 50~90 ms |
| 대기열 | 없음 | 0~1 |
| lost | 약 70% | 0 |
| 코어별 사용률(CPU4/5/6, 1초 표본) | 95~100 / 30~55 / 0~30 | 85~93 / 60~88 / 16~35 |

  - CPU7은 두 판 모두 0~3%다. dvsview와 dvsgl은 그대로 35 fps이고, 최신 이벤트보다 약 31 ms 늦다.
  - 사용률이 는 것은 예전 판이 버리던 이벤트를 이제 모두 세기 때문이다.
- 시험:
  - `test_dvsact.py`는 12개 모두 OK(debian:12 arm64 Docker).
  - 새 `test_cbinner`: 무작위 이벤트, 5.4초 공백, uint32 시각 감김, 무작위 조각 단위의 쓰기. 결과가 Python `Binner`의 프레임과 같다. `s->t0 = tu`로 일부러 틀리게 하면 실패한다.
  - dvsview C 시험은 모두 ok다. 새로 읽는 곳에서 시작하는 경우, 링을 한 바퀴 넘겨 잃는 경우도 시험한다.

**캡처 종료 때 exit 134.**
- [관찰] SIGTERM으로 재시작할 때 가끔 `terminate called without an active exception`(SIGABRT, exit 134)가 났다. 09:39~09:55에 6번 중 2번이었다.
- `dvs`는 연속 3번 실패하면 멈춘다.
- [가설] onnxruntime 안의 daemon 스레드가 인터프리터 종료 정리와 부딪친다.
- 고침: `ov5647-cap.py`의 `leave(code)`는 atexit 함수를 돌리고 출력을 비운 뒤 `os._exit(code)`로 끝낸다.
- 결과: 고친 뒤 SIGTERM 재시작 10번이 모두 exit 0이었다.

**화면 해상도.** 사용자: "화면 해상도를 더 높일 수 있나? 그 어플도 설치해 해상도 변경하는".
- HDMI 신호는 1080p보다 높일 수 없다.
  - [관찰] 보드6 :1의 `xrandr`는 `HDMI-1` 1920x1080 60 Hz 한 모드만 보인다. EDID는 0바이트이고, e850-kms는 fb0의 모드 하나만 낸다(11.85).
  - LT8912B(MIPI DSI → HDMI 브리지)는 1080p60이 최대다. 출처: [Adafruit LT8912B](https://learn.adafruit.com/adafruit-lt8912b-dsi-to-dvi-converter?view=all), [커널 바인딩 lontium,lt8912b](https://www.kernel.org/doc/Documentation/devicetree/bindings/display/bridge/lontium%2Clt8912b.yaml).
- 할 수 있는 것은 X의 배율이다. `xrandr --scale-from WxH`는 데스크톱을 다른 크기로 배치하고, X가 1080p 신호에 맞게 늘이거나 줄인다. 보드4 :1(modesetting + glamor)에서 시험했다.
  - 2560x1440:
    - rc 0, 화면 2560x1440, Xorg 로그 `Allocate new frame buffer 2560x1440`.
    - DRM 평면은 1920x1080 fb(35)를 내보내고, 2560x1440 fb(37)는 따로 있다. 그래서 X가 줄인 그림을 내보낸다고 본다.
  - 처음에는 배경이 바둑판처럼 반복됐다. `xbg`가 1920x1080 픽스맵을 root에 걸기 때문이다. `xbg`를 화면 크기에 맞춰 늘이도록 고쳤다(가장 가까운 화소, `xbg 파일 [폭 높이]`). 다시 찍은 xshot에서는 배경이 화면을 채운다.
  - `--scale 1x1`로 1920x1080에 돌아온다.
  - `gles-x11 1280 720 7 egl`(vsync 없음)을 화면 크기별로 돌렸다:

| 화면 크기 | gles-x11 fps | Xorg CPU |
|---|---|---|
| 1920x1080 | 294~310 | 약 54% |
| 2560x1440 | 252~272 | 약 44% |
| 1280x720(창이 화면 전체) | 159~177 | 약 35% |

    화면에 실제로 바뀌어 보이는 것은 e850-kms의 30 fps 상한(11.85)이다. 그래서 이 숫자는 GPU 여유를 비교할 뿐이다.
- 앱 `e850-res`(`features/desktop`, Python GTK3):
  - 이미 있는 `python3-gi`를 써서 새 패키지가 없다. zenity는 webkit, gstreamer 등 100개 넘는 패키지를 끌어와서 쓰지 않았다. lxrandr와 arandr는 모드 목록(1920x1080 하나)만 보여서 소용없다.
  - 고를 수 있는 크기: 1280x720(크게), 1600x900, 1920x1080(실제 해상도), 2240x1260, 2560x1440(작게, 덜 선명).
  - 고른 값은 `~/.config/e850-res`에 남는다. `session.sh`의 `e850-res --apply`가 X가 시작될 때 다시 건다.
  - 메뉴(설정)에 "Screen resolution (화면 해상도)"가 있다. 명령줄로는 `e850-res 2560x1440`이다.
  - `--selftest`는 ok다(xrandr 출력 해석, 잘못된 크기 거부).
  - 보드4에서 창을 띄워 xshot으로 봤다. 한글 라벨과 지금 크기가 보인다.
- 확인하지 않은 것:
  - HDMI 화면을 눈으로 보지 않았다(원격). 2560x1440에서 글자가 얼마나 선명한지도 모른다.
  - 창의 "적용" 버튼을 눌러 보지 않았다(명령줄 경로만 시험했다). 패널 메뉴에 항목이 실제로 보이는지도 보지 않았다.
  - 배율을 켠 채 DVS GPU 창, Chrome, 전체 화면을 써 보지 않았다.
  - :0(fbdev X)에는 RandR 변환이 없어 오류가 날 것이다(시험하지 않음).
- 보드에 바꾼 것:
  - 보드6:
    - 행동 분류: `/usr/local/lib/libdvsview.so`, `/usr/local/share/dvsview/dvsact.py`, `/usr/local/bin/ov5647-cap.py`. 이전 판은 `/var/tmp/dvs-prev-11.103/`에 있다.
    - 해상도: `/usr/local/bin/e850-res`, `/usr/local/share/applications/e850-res.desktop`, `/opt/e850-features/desktop/{session.sh,e850-res,e850-res.desktop}`, `~/.local/bin/xbg`. 이전 판은 `/var/tmp/xbg.prev`와 `/var/tmp/session.sh.prev`에 있다.
    - 화면 크기는 바꾸지 않았다(고른 값 없음).
  - 보드4:
    - 위와 같은 파일을 넣었다(DVS 파일 3개의 이전 판은 `/var/tmp/dvs-prev-11.103/`).
    - 시험하려고 띄운 X는 `e850-x stop`으로 내렸다. `~/.config/e850-res`는 1920x1080이다.
- Mac 이미지 `~/e850-logs/clone-20261124-b6/overlay`에 위 파일을 넣었다(13개). MD5SUMS와 README도 고쳤다. `rootfs.simg`와 `part.simg.*`는 11.102 overlay로 만든 그대로다. 다음 복제 때 `build-clone.sh`를 다시 돌린다.
- 저장소: `tools/linux510/{dvsview.c,dvsact.py,test_dvsact.py,ov5647-cap.py}`, `features/desktop/{e850-res,e850-res.desktop,session.sh,feature.sh}`, `tools/board/wallpaper/xbg.c`.

### 11.104 DVS 설정 패널을 높이 700으로, 그래프 두 열·기간 1분, DVS 창을 왼쪽 끝에 (2026-11-25)

사용자: "dvs control pannel이 세로로 너무 길어, 세로 길이를 70%로 줄이도록 UI/UX를 변경해, 가로 폭은 적당해". 이어서: "아직 콘트롤 패널 공간이 남으니 그래프를 가로크기를 반으로 줄여서 다 보이게 해줘, 그래프 기간은 1분을 추가하고 1분이 디폴트로".
- [관찰] 전: 보드6의 `dvspanel` 창은 940x1022였다(기본값 940x1000). 작업 영역(높이 1014)을 넘었다. 왼쪽 열 끝의 "센서 설정 적용" 버튼이 화면 밖에 있었고, 오른쪽은 그래프 12개가 아래로 길게 이어졌다.
- 바꾼 것(`tools/linux510/dvspanel.c`):
  - 창은 940x700이다(`PANEL_W`, `PANEL_H`). 폭은 그대로다.
  - 왼쪽 열:
    - 제목과 지금 판정을 한 줄에 둔다.
    - 프리셋 버튼을 "표시 (바로 적용)" 줄에 붙였다.
    - 설정마다 있던 설명 줄은 풍선 도움말(tooltip)로 옮겼다.
    - 색과 뒤집기를 한 줄에 둔다.
    - 센서 "적용" 버튼을 MIPI flush 줄에 붙였다.
    - 최근 판정 변화는 8줄에서 5줄로 줄였다.
  - 오른쪽은 탭 두 개다.
    - "그래프" 탭:
      - 기간, CSV, 타일이 있다. 타일은 값과 범위를 한 줄에 쓰고, 개수 타일 밑의 "최근 N분"은 뺐다(기간은 위에 있다).
      - 그래프 12개는 두 열(반 폭)이고 높이는 40이다. 범위(최대·최소)는 그래프 안 왼쪽 위와 아래에 작은 글씨로 그린다.
    - "통계·녹화·레지스터" 탭: 통계 글(숫자 대부분이 그래프·타일과 겹침), 행동 녹화, 레지스터 읽기가 있다.
  - 기간에 1분을 더했고 1분이 기본이다(1분, 2분, 10분, 30분, 1시간).
- DVS 창 위치(`dvsgl.c`):
  - [관찰] `e850-dvs`를 다시 띄우면 DVS 창이 패널보다 먼저 떴다. 그러면 openbox가 빈 화면 가운데(480,154)에 놓아 패널이 가렸다.
  - 캡처만 다시 시작하면 남은 왼쪽 영역의 가운데(4,154)에 놓였다.
  - 고침: `dvsgl`이 창에 USPosition (0,0)을 준다. 다시 띄운 뒤 (1,20)에 놓였다. CPU 대체 창(`dvsx11.py`)은 고치지 않았다.
- 시험:
  - `dvspanel-test.sh`(Docker Debian 12 arm64, Xvfb):
    - 보드와 같은 글꼴(fonts-nanum)을 넣었다. 대역 서버의 최근 판정을 7개로 늘렸다(5줄만 보이는지 시험).
    - 자체 시험은 940x700에 배치한 뒤 왼쪽 열과 그래프 탭에 스크롤이 생기지 않는지 본다.
    - 통과했다. 그래프 탭은 17 px 남는다.
    - 그래프 높이를 70으로 바꾸면 "graph tab needs 824 px, shows 661"로 실패한다. 높이 한도를 500으로 바꾸면 왼쪽 열(632 px)에서 실패한다.
    - 첫 판의 높이 검사(`gtk_widget_get_preferred_height`)는 가장 좁은 폭 기준이라 785 px로 잘못 쟀다. 실제 배치의 스크롤 범위로 바꿨다.
  - 보드6 `dvsgl --selftest`(kenv): ok.
  - 보드6 화면(xshot): 패널 940x700, 왼쪽은 "적용" 버튼까지 보인다. 그래프 탭은 12개가 다 보이고 기간은 1분이다. DVS 창은 (1,20)에 있다.
- 모르는 것:
  - [관찰] 10:36에 띄운 패널이 10:40쯤 940x989(작업 영역 높이)가 돼 있었다. 다시 띄운 뒤 캡처 재시작과 1분 관찰에서는 700을 유지했다.
  - 원인은 모른다. 사용자가 창 크기를 바꿨을 수도 있다. 예전 판의 1022도 기본값 1000보다 크다.
- 보드에 바꾼 것:
  - 보드6과 보드4: `/usr/local/bin/dvspanel`, `/usr/local/bin/dvsgl`. 이전 판은 `/var/tmp/dvs-prev-11.104/`에 있다.
  - 보드6: `e850-dvs`를 세 번 다시 띄웠다(stop 뒤 x-kms.sh와 같은 `systemd-run`). 캡처를 SIGTERM으로 한 번 재시작했다.
  - 센서 설정(이 절의 마지막 화면에서 margin 2, flush 988 µs)은 건드리지 않았다.
  - Mac 이미지 overlay에 두 파일을 넣었다(14개). MD5SUMS와 README도 고쳤다.
- **고친 회귀(같은 날):**
  - [관찰] 새 패널은 CPU를 30~34% 썼다(보드6, 20초). 같은 때 띄운 예전 판은 9%였다. `Xorg`도 70%였다.
  - 원인: `draw_chart()`가 그리는 도중에 값 라벨을 `gtk_label_set_text`로 바꿨다. GTK는 그때마다 창을 다시 배치하고 다시 그린다. 예전에는 화면에 보이는 그래프가 4~5개라 비용이 작았지만, 12개가 다 보이자 커졌다.
  - 고침: 그리기는 그리기만 한다. 값 라벨은 `redraw_graphs()`에서 새 기록을 받을 때 한 번, 글자가 바뀔 때만 바꾼다.
  - Xvfb 대역 서버에서 잰 CPU: 예전 판 1%, 고치기 전 33%, 고친 뒤 1%. 보드6: 패널 9%, `Xorg` 15%(전 70%).
- 저장소: `tools/linux510/{dvspanel.c,dvspanel-test.sh,dvsgl.c}`.

### 11.105 센서 2000 fps·MIPI flush 988 µs의 영향, CPU와 GPU, DRAM 버퍼 16개, SD 카드 (2026-11-25)

사용자: "2000fps로 내가 변경함, mipi flush 최대로 키웠는데 어떤 영향이 있는거야? cpu usage는 왜 올라간거야? GPU사용이 잘 안돼?", "디램버퍼를 8개에서 16개로 늘리는것은 ?", "sdcard 인식되도록 드라이버 설치해".

**센서 설정 A/B**(보드6, 설정마다 다시 시작 25초 뒤 20초, `/var/tmp/dvsmeasure.sh`, `dvsab.sh`). 장면은 맞추지 않았다(공이 움직이는 정도가 바뀐다). 오전(09:51, 1000 fps·50 µs)에는 프레임당 약 100만 이벤트였다.

| scan / flush | 프레임당 이벤트(31 fps) | 버퍼/s × kB | MB/s | 늦음 | CPU0~7 % | `dvs-bin` | 분류기가 잃은 이벤트 |
|---|---|---|---|---|---|---|---|
| 2000 fps / 988 µs(사용자 설정) | 162만 | 64.5 × 1024 | 66 | 39 ms | 58 76 59 54 85 89 73 4 | 99.8% | 초당 1,640만(약 33%) |
| 2000 fps / 50 µs | 235만 | 88.5 × 777 | 68.8 | 33 ms | 67 95 67 62 83 89 76 6 | 95.8% | 초당 3,570만(약 49%) |
| 1000 fps / 50 µs | 193만 | 57.5 × 778 | 44.7 | 35 ms | 68 76 68 72 87 90 78 6 | 100.5% | 초당 1,650만(약 27%) |
| 2000 fps / 988 µs(되돌린 뒤) | 234만 | 67.0 × 1024 | 68.6 | 40 ms | (재시작 중) | | 초당 3,300만(약 45%) |

- 잃은 비율은 `[dvsact] lost`의 2초 증가분을 (프레임당 이벤트 × 31)로 나눈 추정이다.
- scan 2000 fps(495 µs):
  - 시간 분해능이 두 배다.
  - 1000 fps보다 이벤트는 약 22%, MIPI 데이터는 약 54% 많다(scan마다 붙는 시각 워드로 추정).
  - 버퍼가 초당 88.5개(전 57.5개)라 디코더 코어(CPU1)가 95%(전 76%)다.
- flush 988 µs:
  - 데이터 양은 같다(68.6 대 68.8 MB/s). 버퍼가 늘 꽉 찬 1 MB(프레임 채움 100%)로 덜 자주 온다.
  - 바쁜 장면에서는 늦음이 33 → 40 ms다(한 번씩 잼).
  - 조용한 장면(11:34, 13 MB/s)에서는 버퍼가 초당 12.4개뿐이었다. 최신 이벤트보다 71 ms 늦었고, 1분에 어두운 프레임 17장, 데이터 있는 시간 80%였다. [가설] 버퍼가 차기를 기다리는 동안 화면 프레임(32 ms)에 새 이벤트가 없다.
  - 꽉 찬 버퍼가 센서 쪽에서 이벤트를 잃게 하는지는 재지 못했다.
- CPU가 오른 까닭:
  1. 장면 이벤트가 오전의 약 2배다(1000 fps에서도 193만 대 100만).
  2. 2000 fps의 데이터 증가.
  3. 11.104 패널 회귀(패널 30%, `Xorg` 70%). 고쳤다.
  - 묶기 스레드 `dvs-bin`은 약 20 ns/이벤트라 초당 약 5,000만 개가 한계다. 그래서 세 설정 모두 한 코어를 다 쓰고 이벤트를 잃는다.
- GPU:
  - GPU는 DVS 창(`dvsgl`)만 그린다. 세 설정 모두 23~25 fps였고(오전 35 fps), 프레임당 2,097,152개였다. 이것은 `CAP`(1<<21, 최신 것부터) 한도다. 프레임당 CPU+GPU 시간은 약 40 ms다.
  - 그래서 GPU 창은 한계에 있고, 한도를 넘는 이벤트는 그리지 않는다.
  - MIPI 해독, 분류기 묶기·추론, 얼굴 검출은 CPU에서 돈다.
  - kbase `utilization`은 같은 부하에서 0과 100을 오가서 쓸 수 없다(GPU DVFS 사용률을 모으지 않음, 11.82).

**DRAM(VC0) 버퍼 8개 대 16개**(2000 fps·988 µs, `dvs --auto -b 16`, 다시 시작 75초 뒤 1분 기록, `/var/tmp/bufab.sh`):
- vendor 한도는 `IS_MAX_BUFS = VIDEO_MAX_FRAME`(32)다.
- 8개: 빈 버퍼 최소 7, 처리 대기 최대 0, MIPI 프레임 1389 / 버퍼 1392.
- 16개: 빈 버퍼 최소 15, 대기 최대 0, 1230 / 1229. MemAvailable 약 22 MB 줄었다.
- 늦음은 둘 다 43 ms다. `is_sensor_group_tag fail(-22)`는 둘 다 스트림을 시작할 때 난다.
- 디코더가 받는 대로 풀어서 한 번에 1개 넘게 쌓이지 않았다. 앞서 그래프의 "빈 버퍼 최소 0, 처리 대기 7"은 다시 시작한 직후 구간이었다.
- 그래서 16개의 이득은 재지 못했다. 표준 `-b 8`로 되돌렸다.

**SD 카드:**
- [관찰] 커널에는 이미 다 있다. dw_mmc(`13500000.dwmmc2`, card-detect GPIO), exFAT·FAT·FUSE가 내장돼 있다.
  - 10:57:21에 꽂은 카드를 바로 알아챘다(`mmc1: card inserted`, SDR104 200 MHz SDHC, 튜닝 오류 한 번 뒤 성공).
  - `mmcblk1` 29.7 GiB, exFAT 파티션 하나.
  - 빠진 것은 마운트해서 보여 주는 사용자 공간이었다.
- desktop 기능에 넣은 것:
  - 패키지 `udisks2 gvfs exfatprogs dosfstools`(추천 패키지 없이 29개, `libpam-systemd`는 새로 들지 않음).
  - polkit 규칙 `/etc/polkit-1/rules.d/50-e850-udisks.rules`: 데스크톱 사용자가 마운트, 해제, 꺼내기를 한다. X 세션에는 logind seat이 없어서 udisks2의 `allow_active`가 안 맞는다.
  - udev 규칙 `/etc/udev/rules.d/80-e850-hide-emmc.rules`: eMMC(카드 종류 `MMC`)는 `UDISKS_IGNORE`. 그러지 않으면 Android 파티션 4개가 파일 관리자에 볼륨으로 나왔다.
- 확인(보드6):
  - philip이 `udisksctl mount`로 비밀번호 없이 `/media/philip/NO NAME`(exfat, uid 1001, rw)에 마운트했다.
  - GIO VolumeMonitor(pcmanfm이 쓰는 것)에는 SD 볼륨만 나온다(꺼내기 가능).
  - 처음 마운트할 때 "not properly unmounted"가 나서 `fsck.exfat -y`를 했다(clean, 16 디렉터리, 93 파일). 64 MB를 쓰고 다시 읽어 md5가 같았다.
  - 직접 읽기는 36.7 MB/s(DVS가 도는 중)다.
- 확인하지 않은 것: pcmanfm 창에서 클릭으로 마운트·꺼내기, 카드를 뺐다 다시 꽂기.
- 보드6에 바꾼 것:
  - 패키지 4개와 그 의존성, 위 규칙 두 개.
  - `/opt/e850-features/desktop/`의 `feature.sh`와 규칙 파일(이전 판 `/var/tmp/desktop-feature.sh.prev`).
  - SD 카드는 마운트된 채로 두었다.
- 보드4:
  - 11:3x까지 패널 고친 판을 넣었다.
  - 그 뒤 Wi-Fi(ARP)와 Mac USB(fastboot)에 모두 보이지 않아 SD 지원은 넣지 못했다. 원인은 모른다.
  - 12:27(UTC)에 다시 부팅해 있었다. 누가 전원을 다시 넣었는지는 확인하지 않았다.
    - [관찰] 앞 부팅의 journal은 11:33:22(가동 652초)에 ssh 세션이 끝나는 줄에서 끊긴다. 오류나 패닉 줄은 없고, `/sys/fs/pstore`도 없다. 원인은 여전히 모른다.
    - 현미경(`e850-scope`)은 부팅 때 떠서 50M Hi-5031Q 2016×1536을 4.3~4.4 fps로 받는다(프레임 340장 중 336장 온전, MIPI 오류 0, LED 255).
    - 사용자 요청("현미경 작동 시켜")으로 GPU 데스크톱(:1)에 `google-chrome --app=http://127.0.0.1:8090/`을 띄우고 앞으로 올렸다. 화면에서 LIVE 4.3 fps를 확인했다.
    - SD 지원은 아직 넣지 않았다.
- 저장소:
  - `features/desktop/{feature.sh,e850-udisks.rules,80-e850-hide-emmc.rules}`.
  - 측정 스크립트는 보드6 `/var/tmp/`에만 있다(`dvsmeasure.sh`, `dvsab.sh`, `bufab.sh`).

### 11.106 현미경 뷰어의 실시간 화면을 GPU로: PNG 대신 회색 바이트, WebGL2 LUT (2026-11-25)

사용자: "보드켜졌지? 현미경 작동 시켜", "이것도 GPU를 사용해서 최적화 하도록 하는건 어때?, 그리고 외부 접속 주소 연결해줘".

**보드4 현미경(관찰):**
- 12:27(UTC) 부팅 뒤 `e850-scope`가 50M Hi-5031Q 2016×1536을 4.3~4.4 fps로 받고 있었다(11.105).
- GPU 데스크톱(:1)에 `google-chrome --app=http://127.0.0.1:8090/`을 띄웠다.

**어디에 CPU를 쓰나(고치기 전, 보드4, 20초, 뷰어 창 하나):**
- CPU0~7 합 257%.
- 서버 `scope.py` 62%, Chrome 렌더러 60%, GPU 프로세스 38%, 브라우저 10%, Xorg 14%, 커널 스레드 43%(그중 `uvcvideo` kworker 약 26%).
- 서버 한 프레임(실제 영상으로 잰 것):

  | 단계 | 시간 |
  |---|---|
  | YUYV에서 Y 뽑기 | 6.8 ms |
  | 4장 평균(uint16) | 30.7 ms |
  | 2×2 평균 | 15.9 ms |
  | zlib 1 PNG | 78.0 ms |

- 실시간 PNG는 1008×768 화소(774,144 B)에 696,394 B다. 시료 잡음 때문에 1.11배밖에 줄지 않는다.
- 페이지는 프레임마다 다음을 했다.
  1. PNG를 풀었다(`createImageBitmap`).
  2. 2D 캔버스에 그렸다가 `getImageData`로 다시 읽었다.
  3. JS로 화소마다 LUT(밝기·대비·감마·반전)를 RGBA에 썼다.
  4. `putImageData`로 올렸다.
  5. 화면 캔버스에 다시 그렸다.

**바꾼 것:**
- 서버:
  - `/frame.png?…&fmt=raw`는 같은 영역의 회색 바이트를 줄 단위로 준다(`application/octet-stream`, 크기는 `X-Region`의 w/step × h/step).
  - `fmt`가 없으면 예전처럼 PNG다(실험 마법사의 `grabFrame`이 쓴다).
- 페이지:
  - 실시간 화면은 `fmt=raw`를 받는다.
  - WebGL2 캔버스(`#gv`)를 `#cv` 아래에 두었다. 프레임은 R8 텍스처로 한 번 올리고, LUT는 256×1 텍스처로 올린다. 셰이더가 LUT를 적용한다.
  - 2× 미만 확대에서는 LINEAR, 그 이상에서는 NEAREST다(예전 `imageSmoothingEnabled`와 같다).
  - 측정선, 물체 원, 십자선은 투명하게 지운 `#cv`에 그린다.
  - 히스토그램과 평균 등 통계는 프레임마다 JS로 한 번 센다. 밝기 슬라이더를 움직이면 LUT만 다시 올린다.
  - WebGL2가 없거나, `?gl=0`이거나, 컨텍스트를 잃으면 2D 캔버스로 그린다(JS LUT, 예전 방식에서 PNG 풀기와 다시 읽기만 뺐다).

**시험(보드4):**
- 서버: 가짜 프레임으로 8091에 따로 띄운 서버에서 같은 프레임(n 일치)의 `fmt=raw` 바이트가 PNG를 푼 화소와 같았다(`assert`).
- 페이지: 보드4 Chrome(:1, ANGLE GL-EGL)의 시험 창 두 개에서 확인했다. 창 제목에 경로를 찍는 줄은 시험 사본에만 넣었다.
  - 제목이 "[GL]"과 "[2D]"로 나와 경로가 맞게 갈렸다.
  - 맞춤 화면, 861% 확대(원본 화소, NEAREST), 커서 화소 값 표시가 됐다.
  - 반전 + 자동 대비(밝기 −47, 대비 ×1.47)에서 두 창의 영상 영역 화소 평균 차는 0.67, 평균 밝기는 144.5 대 144.6이었다(멈춘 프레임이 서로 달라 0은 아니다).
- 실제 서비스에서 20초씩:

  | 판 | CPU 합 | `scope.py` | 렌더러 | GPU 프로세스 | Xorg | 커널 스레드 |
  |---|---|---|---|---|---|---|
  | 예전(PNG + JS LUT) | 257% | 62 | 60.5 | 38.0 | 13.7 | 42.9 |
  | 회색 바이트 + 2D(`?gl=0`) | 248% | 38.8 | 54.4 | 43.3 | 15.4 | 48.2 |
  | 회색 바이트 + WebGL2 | 237% | 39.1 | 42.1 | 42.1 | 16.7 | 50.2 |
  | WebGL2, 화면 멈춤 | 96% | 21.1 | 7.4 | 3.4 | 0.7 | 38.3 |

- 해석:
  - 줄어든 것은 서버 PNG 압축(−23)과 렌더러의 화소 변환(−18)이다. 전체로는 약 0.2코어다.
  - 남은 화면 비용은 프레임당 렌더러 약 35, GPU 프로세스 약 39, Xorg 약 16이다(멈춤과 비교). [가설] Chrome이 창을 합성해 X로 보내는 경로다. GPU 프로세스는 CPU에서 Mesa·shim(큰 BO cache clean, 11.82)을 돈다. 재지 않았다.
  - 화면과 상관없이 남는 비용:
    - USB 수신 `uvcvideo` 약 31%(YUYV 6 MB/프레임. 실시간도 GREY로 보내면 반으로 준다. 키트 펌웨어를 고쳐야 한다).
    - 서버 4장 평균과 2×2 평균 약 21%(numpy. OpenCV NEON으로 줄일 여지가 있다).
  - fps는 센서가 정한다(4.37 fps). GPU로 바꿔도 빨라지지 않는다.

**외부 접속:**
- 11.75처럼 `~/bin/cloudflared tunnel --no-autoupdate --url http://localhost:8090`(trycloudflare 임시 주소)을 philip으로 띄우려 했다. 이 세션의 권한 검사가 다시 막았다(11.102와 같음).
- 이 뷰어에는 인증이 없다. 링크를 아는 사람은 누구나 노출, LED, 센서 레지스터, 촬영, `/quit`을 쓸 수 있다.
- 사용자가 보드4 터미널에서 직접 띄우거나, 먼저 뷰어에 비밀번호를 넣을지 정한다.

**보드에 바꾼 것:**
- 보드4·보드6 `/opt/e850-scope/scope.py`(md5 03bc65a1…), `scope.html`(d70c9b9f…)을 넣고 `/reload`했다.
- 예전 판은 각 보드 `/var/tmp/scope-prev/`에 있다.
- 보드4 데스크톱에 현미경 Chrome 앱 창을 띄웠다.
- 시험 서버(8091)는 내렸다. 측정 스크립트는 보드4 `/var/tmp/cpumeas.py`에만 있다.
- Mac 복제 이미지 overlay에 `opt/e850-scope/scope.{py,html}`를 넣고 MD5SUMS를 다시 만들었다.
- 저장소: `tools/board/scope/{scope.py,scope.html}`.

### 11.107 ifconfig, 바탕화면 아이콘(DVS, 현미경), 보드6 scan 누락과 MIPI flush 1 µs (2026-11-25)

사용자: "ifconfig가 왜 안 먹지? 그리고 dvs 및 전자현미경 실행 버튼 바탕화면에 올려줘", "보드6 dvs scan 누락이 올라가는데? 왜?", "그동안 작업한게 저장이 안되었나?", "보드는 내가 껏다가 켯어".

**ifconfig:**
- [관찰] Debian 12 기본 rootfs에 `net-tools`가 없다.
- 데스크톱 세션 PATH(`x-kms.sh`가 `env -i`로 줌)는 `/usr/local/bin:/usr/bin:/bin`이라, `/usr/sbin`의 `ifconfig`, `iw`도 찾지 못한다.
- 고침:
  - desktop 기능 PKGS에 `net-tools`(의존성 없음, `libpam-systemd` 안 들어옴)와 `xdotool`을 넣었다.
  - `session.sh`가 `PATH`에 `/usr/sbin:/sbin`을 붙인다. 이것은 다음 데스크톱 시작부터 적용된다. 지금 떠 있는 터미널에서는 `/usr/sbin/ifconfig` 또는 `ip a`를 쓴다.

**바탕화면 아이콘:**
- 바탕화면:
  - 지금까지 바탕은 `xbg`가 root 창에 그렸고 아이콘이 없었다.
  - `session.sh`가 `pcmanfm --desktop --profile e850`을 띄운다(`~/Desktop`의 아이콘).
  - 기본 설정은 `/etc/xdg/pcmanfm/e850/desktop-items-0.conf`(단색 #24323f)다.
  - 보드4·6은 `~/.local/share/wallpaper.bgrx`를 GdkPixbuf로 `wallpaper.png`로 바꾸고, 사용자 설정에서 `wallpaper_mode=crop`으로 쓴다(해상도를 바꿔도 화면을 채운다).
- `e850-open dvs|scope`:
  - 이미 열린 창(`^DVS$`, `^SOL Microscope`)이 있으면 `xdotool`로 앞으로 가져온다.
  - 없으면 dvs는 `systemctl start e850-dvs-desk.service`를 한다(desktop 시작 때의 `e850-dvs`가 돌고 있으면 시작하지 않는다).
  - scope는 `google-chrome --app=http://127.0.0.1:8090/`(Chrome이 없으면 firefox)를 띄운다.
- DVS 권한:
  - `dvs`는 root가 필요하고, 사용자로 실행하면 sudo 비밀번호를 묻는다. 아이콘에서는 입력할 곳이 없다.
  - 그래서 고정 유닛 `e850-dvs-desk.service`(`DISPLAY=:1`, `ExecStart=/usr/local/bin/dvs`, 센서가 없으면 녹화 재생, 130 = 사용자 종료)를 두었다.
  - polkit 규칙 `50-e850-dvs.rules`는 데스크톱 사용자에게 이 유닛의 start·stop만 허락한다.
- libfm(pcmanfm 1.3.2):
  - 바탕화면의 `.desktop`을 두 번 누르면 실행 권한(755든 644든), 심볼릭 링크와 상관없이 "This text file … seems to be an executable script" 창을 띄웠다. "Execute"를 누르면 항목을 제대로 실행했다.
  - libfm 문자열에 신뢰(trust) 설정이 없다. 끄는 방법은 `quick_exec=1` 하나다. `/etc/xdg/libfm/libfm.conf`와 사용자 `~/.config/libfm/libfm.conf`에 넣었다.
  - 그래서 파일 관리자에서 실행 스크립트를 두 번 누르면 묻지 않고 실행한다.
- desktop 기능의 install은 보드에 있는 것만 아이콘을 넣는다: `/usr/local/bin/dvs`가 있으면 DVS, `/opt/e850-scope`가 있으면 현미경. 그 기능들을 넣은 뒤 desktop을 다시 install한다.
- 시험(보드4, xdotool로 두 번 누르기):
  - 현미경 아이콘: 확인 창 없이, 최소화한 현미경 창이 다시 앞으로 왔다(창은 1개 그대로).
  - DVS 아이콘: 비밀번호 없이 유닛이 active가 되고, "DVS"·"DVS 설정" 창이 떴다(센서가 없어 녹화 재생).
  - philip이 `systemctl stop e850-dvs-desk`로 끄는 것도 polkit이 허락했다.
- 시험(보드6):
  - DVS가 돌고 있을 때 아이콘을 누르면 `e850-dvs` PID가 그대로였다(다시 시작하지 않음). 창 쌓임은 "DVS 설정, DVS" → "DVS 설정 아래 DVS"로 바뀌어 맨 위로 왔다. 입력 포커스는 바탕화면에 남는다.
  - 보드6은 DVS 창(0,0)이 왼쪽 위 아이콘을 가려서, 사용자 설정에서 아이콘을 아래쪽 (24,860), (124,860)에 두었다.
- 함정: `pkill -f "pcmanfm --desktop"`이 그 명령을 실은 ssh 셸까지 죽여, 뒤의 명령이 실행되지 않았다(CLAUDE.md의 함정 그대로). pkill은 따로 보냈다.

**보드6 scan 누락(관찰):**
- 사용자가 보드6을 껐다 켰다(14:07 UTC 기록 끝, 14:12 부팅).
- 부팅 때는 사용자가 정한 2000 fps·flush 988 µs가 남아 있었다(14:12 로그에 버퍼 1024 kB).
- 14:14:23에 `sensor.json`이 `{"margin": 0, "flush_us": 1}`로 저장됐다. 이 파일은 `POST /sensor`(설정 패널·웹 페이지의 적용)만 쓴다. 8초 뒤 캡처가 그 설정으로 다시 시작했다.
- 누락은 버퍼가 시작하는 곳에서 생겼다: `ts_miss_first` 5,336 / `ts_miss` 5,435.
- 같은 장면(프레임당 이벤트 161만~163만)에서 flush만 바꿔 15초씩 잰 결과(`/var/tmp/flushab.py`, 끝나고 flush 1로 되돌림):

  | flush | 버퍼 | scan 누락 | frame_end 누락 | 늦음 |
  |---|---|---|---|---|
  | 1 µs | 320~346/s × 148 kB | 131~156/s (6.5~7.7%), 간격 17~19번/s, 98%가 버퍼 시작 | 95~101/s | 17~20 ms |
  | 50 µs | 79.5/s × 671 kB | 0 | 0 | 28 ms |
  | 988 µs | 52.5/s × 1024 kB | 18/s (0.9%), 간격 0.4번/s | 0 | 32 ms |

- [가설] flush가 짧으면 센서가 MIPI 프레임을 초당 300번 넘게 닫고 연다. 그 경계에서 scan이 빠진다. 988 µs에서는 버퍼(1 MB)가 꽉 차서 가끔 길게 빠진다.
- 보드6은 사용자 설정(flush 1 µs) 그대로 두었다.
- 덧붙임(사용자: "mipi flush를 키우면 스캔누락이 적어지나? 어떤 원리?"):
  - 같은 flush 1 µs에서 장면이 조용해지자(프레임당 52만 이벤트, 버퍼 361/s × 81 kB) scan 누락이 초당 1.3개(간격 0.2/s, 간격당 6.8 scan)로 줄었다.
  - 같은 20초 동안 CSIS frame end는 360.5/s, 디코더에 온 버퍼는 360.0/s(버퍼 안 된 MIPI 프레임 0.5/s)였다.
  - 그러니 누락은 flush만이 아니라 이벤트 양에도 달려 있다. 프레임 경계가 많고(짧은 flush) 이벤트가 많을 때(161만/프레임) 크다.
  - 988 µs의 누락은 간격당 약 45 scan(22 ms)으로, 버퍼 하나의 길이(1/52.5 s = 19 ms)와 비슷하다. 프레임 하나가 통째로 빠진 것으로 보인다(가설).
  - [가설, 재지 않음] 프레임 경계마다 센서가 보내기를 잠시 멈추고, 그동안 출력 FIFO에 쌓인 이벤트가 넘친다. 센서의 넘침 표시 레지스터는 찾지 않았다.
- flush를 정하는 곳(사용자: "mipi flush 주기 설정은 무엇으로 하는거야? 레지스터로?"):
  - 센서 S5KRC1S 레지스터다(11.25).
    - `0x3910` bit7 `use_timeout`(setfile 0x81).
    - `0x3911` `to_scnt0`(기준 시계 1 µs).
    - `0x3912/0x3913` `to_scnt1`(타임아웃, µs. setfile은 999 = 1 ms).
    - `0x3914` `to_mcnt`(1번).
  - 경로:
    1. 패널이나 웹 페이지의 적용(`POST /sensor`)이 `/var/lib/dvsview/sensor.json`에 저장하고 캡처를 끝낸다.
    2. `dvslive.sh`(`tools/board/dvslive.sh`)가 다시 뜨며 `/sys/module/is_cis_s5krc1s/parameters/flush_us`에 쓴다.
    3. stream on(mode change) 때 CIS 드라이버(`modules/s5krc1s-pablo`, `is-cis-s5krc1s.c`)가 setfile 다음에 0x3912/13을 쓴다(1~1023).

**저장소(사용자: "깃에 전부 기록해 각각 레포에"):**
- e85096 `features-20261121`에 11.100~11.107을 커밋하고 push했다.
- sol-cx3-uvc `still-capture`에 LOG 15(보드4에서 50M 키트 실시간, 실시간 GREY의 여지)를 커밋하고 push했다.
- sol-cx3-uvc의 `fw/cx3-ov5640-ref/`(Cypress CX3 SDK 예제, "UNPUBLISHED, LICENSED SOFTWARE")는 넣지 않고 `.gitignore`에 적었다.

**저장 여부(관찰):**
- 보드6:
  - 껐다 켠 뒤에도 dvspanel(8add174b), dvsgl(9522f27c), 현미경 뷰어(03bc65a1, d70c9b9f), SD 패키지와 규칙이 그대로다.
  - philip 권한의 `journalctl --list-boots`는 앞 부팅 끝을 12:42로 보였다. root로 보면 14:07까지 기록이 있다.
- 껐다 켜기 전 보드6은 13:5x UTC쯤 Mac에서 ping·ssh가 안 됐다. 그동안에도 DVS 로그는 계속 찍혔다. wpa_supplicant 연결 끊김 기록이 없고, 주소도 있어서 `e850-wifiwatch`는 나서지 않았다. 원인은 모른다(11.100의 송신 막힘과 같은지는 확인하지 않았다).
- 저장소의 바뀐 파일은 ee474dc 뒤로 커밋하지 않았다(Mac 작업 트리에만 있다).

**보드에 바꾼 것:**
- 보드4·6:
  - `net-tools`.
  - `/usr/local/bin/e850-open`, `/usr/local/share/applications/e850-{dvs,scope}.desktop`.
  - `/etc/systemd/system/e850-dvs-desk.service`, `/etc/polkit-1/rules.d/50-e850-dvs.rules`.
  - `/etc/xdg/pcmanfm/e850/`, `/etc/xdg/libfm/libfm.conf`(quick_exec).
  - `/opt/e850-features/desktop/`. 이전 판은 `/var/tmp/session.sh.prev-107`, `/var/tmp/desktop-feature.sh.prev-107`에 있다.
  - `~/Desktop/e850-{dvs,scope}.desktop`, `~/.config/pcmanfm/e850/desktop-items-0.conf`, `~/.config/libfm/libfm.conf`, `~/.local/share/wallpaper.png`.
  - 지금 세션에 pcmanfm 바탕화면을 띄웠다.
- Mac 복제 이미지 overlay에 보드6의 위 파일을 넣었다(46개, MD5SUMS·README). `net-tools`는 패키지라 굽고 나서 설치한다.
- `build-clone.sh`가 overlay의 `home/<사용자>/` 아래 새 파일을 그 사용자 소유로 바꾼다. Docker에서 `~/Desktop` 소유를 확인했다.
- 저장소: `features/desktop/{session.sh,feature.sh,e850-open,e850-dvs.desktop,e850-scope.desktop,e850-dvs-desk.service,e850-dvs.rules,pcmanfm-desktop.conf}`, `tools/linux510/build-clone.sh`.


### 11.108 Jetson Orin Nano(192.168.0.195)에 E850의 C 디코더와 설정 패널 (2026-11-26)

사용자: "젯슨오린나노가 850보다 dvs 디코딩을 잘 못 하는 것 같아, 지금 사용하는 c로 짠 디코더와 컨트롤러로 바꿔".

**Jetson 상태(관찰, 바꾸기 전):**
- Orin Nano, JetPack R39.2.1(Ubuntu 24.04, 커널 6.8.12-tegra), 6코어 A78AE. 계정 philip(video, i2c 그룹, sudo는 비밀번호). Mac 키는 사용자가 Jetson 셸에서 `authorized_keys`에 넣었다.
- NRV 저장소 `~/DVS.JS.ORIN.01`(협력 측 코드, 이 저장소에 넣지 않는다):
  - 수신기: 시스템 서비스 `nrv-dvs-streamer.service`(enabled, philip으로 실행) → C++ `dvs_streamer`. 설정은 **RAW2** `Delta_10_2000FPS_RAW2.txt`(2880×61, 약 1600 fps, 프레임 주기 624 µs).
  - 화면: GNOME 자동 시작이 Tk 창 `session.py`(user 서비스 `nrv-dvs-native`)를 띄운다.
  - 센서 드라이버 `nrv_dvs`(i2c 2-0010), `/dev/video0`(tegra VI). 레지스터는 V4L2 배열 control `nrv_register_writes`에 `(addr << 8) | value` 워드로 넣는다. 드라이버가 STREAMON 때 쓰고, 0x0100은 받지 않는다.
  - DT `cam1.dts`: mode0 8192×128 RAW8(AER), mode1 2880×61(RAW2), 4 lane, `pix_clk_hz` 1.25 GHz.
- RAW2 1576초 동안(`run.py status`):
  - 버퍼 2,515,163개, dropped 0, error_buffers 0.
  - 디코더 쪽은 `raw2_timestamp_ambiguous` 260, `order_errors` 20, `frame_gaps` 1, `invalid` 1.
  - CPU는 `dvs_streamer` 71%, `session.py` 55%.
- Jetson의 AER setfile(`settings/Delta_10_2000FPS.txt`, "Update 10.13")은 E850 setfile과 아래가 다르다. 뜻을 아는 것은 이름(주석)뿐이다.
  - DTAG 타이밍: 0x3210~15, 0x3219, 0x321A, 0x321C, 0x3225~28, 0x320C(0x3D, E850 0x7D).
  - 그 밖: 0x3901 = 2A(RAW8 DT), 0x3A10 = 02, PLL 0x30A6/A7 = 8B/F8.
  - 패킷 형식(event group, column, timestamp, frame end, big-endian, 0 패딩)은 같다. Jetson의 `group_aer_decoder.cpp`에서 확인했다.

**바꾼 방식:**
- E850의 Exynos 캡처(`ov5647-cap.py`)만 Jetson용 앞단 `tools/jetson/dvs-jetson.py`로 바꿨다. 디코더 `libdvsview.so`(dvsview.c), 설정 페이지 `dvsctl.py`/`dvsctl.html`, X 창 `dvsx11.py`, GPU 뷰 `dvsgl`, 패널 `dvspanel`은 E850 소스 그대로다.
- 레지스터 목록:
  - 바탕은 Jetson의 AER setfile이다. 링크(4 lane, PLL)가 이 수신기 DT에 맞춰져 있어서 그대로 쓴다.
  - 그 위에 E850 드라이버가 setfile 다음에 쓰는 값을 같은 순서로 덧붙인다: 0x391A/B 패킷 127, 0x3912/13 `flush_us`, 0x321D/E `margin`. 값은 패널·페이지의 `sensor.json`에서 온다(dvslive.sh와 같다).
  - 0x0100은 뺀다. 모두 102 워드다(드라이버 한도 4096).
  - `--print-regs`로 확인할 수 있다.
- 캡처:
  - `S_FMT` 8192×128 RGGB8이면 드라이버가 mode0을 고른다.
  - USERPTR 버퍼 16개(1 MiB, 캐시 메모리, NRV SDK와 같은 방식).
  - DQBUF마다 버퍼 전체를 `dvs_ingest()`에 넘긴다. 센서는 0x391A/B보다 한 패킷을 더 보낸다(11.22). 그래서 DT 높이 128 = 127 + 1이다.
- E850과 다른 값:
  - 웹 스레드 CPU5. dvsview.c의 해독 1, 그리기 2, 필터 3은 그대로다.
  - 저장 위치 `~/.config/dvs-jetson/`. 페이지의 "now" 값은 `$XDG_RUNTIME_DIR/dvs-jetson/`.
  - I2C 드라이버 이름 `nrv_dvs`, 주소 배너 인터페이스는 `wlP1p1s0`·`enP8p1s0`.
- `dvsx11.py`: `GL_LIB = None`이면 시스템 EGL을 쓴다(Jetson의 NVIDIA). E850 보드의 동작은 그대로다.
- `tools/jetson/dvs-jetson`(launcher, E850의 `dvs`와 같은 역할):
  - 패널을 띄운다.
  - 캡처를 시작하기 전마다 NRV 수신기를 그쪽 `run.py stop`으로 멈춘다.
  - 종료 코드별 동작: 0(설정 변경), 1(3초 동안 버퍼 없음)이면 다시 띄운다. 130(q, Esc, 창 닫기)이면 끝낸다. 연속 3번 실패하면 10초 간격으로 다시 시도한다.
- `tools/jetson/deploy.sh`:
  - Mac Docker `ubuntu:24.04` arm64(glibc 2.39, Jetson과 같다)에서 `libdvsview.so`, `dvsgl`, `dvspanel`을 빌드한다.
  - 빌드한 것을 `~/dvs-jetson/`에 옆 디렉터리를 거쳐 rename으로 넣는다. 실행 중인 `.so`를 덮어쓰지 않기 위해서다.
  - `--autostart`를 주면 GNOME 자동 시작을 바꾼다.

**측정(Jetson, 관찰):**
- 창 없이 22초(flush 50 µs, margin 17 = scan 985 µs):
  - 버퍼 39.5/s, 해독 0.6~1.2 ms/버퍼.
  - VI 오류 플래그 0, sequence 손실 0, short 0, fe_missed 0.
  - `ts_missed` 2009가 시작 직후 한 번 생기고 늘지 않았다. RAW2에서 AER로 처음 바꾼 스트림에서만 봤다. 그다음 시작들에서는 0이었다. 원인은 확인하지 않았다(가설: 센서 첫 2초의 시각 점프).
- 창·패널과 함께. 사용자가 패널에서 margin 0(scan 495 µs, 약 2000 fps)과 상하 반전을 골랐고, 캡처가 저절로 다시 시작했다.
  - 프레임당 이벤트 72만~94만, 버퍼 72~75/s, 해독 2.7~3.4 ms/버퍼(한 코어의 약 22%).
  - 그리기 30 fps, 0.5~0.9 ms. 최신 이벤트와의 지연 21~23 ms.
  - 누락은 모두 0이다: ts_missed 0, fe_missed 0, short 0, VI 오류 0.
- `dvsgl`은 `NVIDIA Tegra Orin (nvgpu), OpenGL ES 3.2 NVIDIA 595.78`로 그린다.
- CPU는 `dvs-jetson.py` 프로세스(해독, 필터, 웹 모두) 23~26%, `dvsgl` 4.5%, `dvspanel` 2%.
- RAW2(1600 fps 전체 프레임)와 AER(2000 fps scan, 이벤트별 시각)는 출력이 달라서, 위 CPU 숫자를 같은 일의 비교로 볼 수는 없다.
- 화면을 xwd로 떠서 확인했다. DVS 창에 사람 얼굴 윤곽이 보이고, 패널 그래프가 차고, 웹 주소가 `192.168.0.195:8080`으로 나온다.

**Jetson에 바꾼 것:**
- `~/dvs-jetson/`(새 디렉터리, root 불필요).
- `~/.config/autostart/dvs-jetson.desktop`(새로 넣음). NRV 창의 `nrv-dvs-native.desktop`은 `~/.config/autostart-off/`로 옮겼다. 되돌리려면 다시 옮긴다.
- `~/.config/dvs-jetson/{sensor,display}.json`: 패널이 저장한다.
- 바꾸기 전 백업은 `~/backup-before-e850-20261126/`이다: autostart, user systemd, 서비스 정의, NRV 저장소 HEAD·status·추적 안 되는 파일 tgz.
- NRV 수신기는 그쪽 `run.py stop`으로 멈췄다. 시스템 서비스 `nrv-dvs-streamer`는 **아직 enabled**다(끄려면 sudo). 부팅하면 먼저 뜨지만, `dvs-jetson`이 캡처를 시작할 때마다 멈춘다.
- 지금 도는 인스턴스는 `systemd-run --user --unit=dvs-jetson-now`로 띄웠다. 다음 로그인부터는 자동 시작이 띄운다. 로그인 경로는 아직 시험하지 않았다(Jetson을 재부팅하지 않음).
- NRV 저장소의 파일은 바꾸지 않았다.

### 11.109 Jetson: htop, 패널의 MIPI·DRAM 그래프 (2026-11-26)

사용자: "젯슨에서도 htop 사용하게 해줘, 그리고 mipi 프레임 데이터를 확인 못 하나?"

**htop:**
- Jetson에는 없었다. 필요한 라이브러리(libncursesw6, libnl-3, libnl-genl-3)는 있었다.
- sudo 없이 설치했다: `apt-get download htop`(3.3.0-4build1) → `dpkg -x`로 `~/.local/opt/htop`에 풀고 `~/.local/bin/htop`에 링크.
- `~/.profile`이 `~/.local/bin`을 PATH에 넣으므로 터미널과 ssh에서 `htop`으로 실행된다.
- 시스템 전체에 넣으려면 `sudo apt install htop` 하고, 이 링크를 지운다.

**MIPI 그래프가 비어 있던 까닭(관찰):**
- E850의 패널은 MIPI 프레임·링크 오류를 `csis-irqstat.ko`에서, CRC를 fimc_is sysfs에서, 버퍼 상태를 캡처 루프의 QUERYBUF에서 받는다(11.51). Jetson 앞단에는 셋 다 없었다.
- Jetson VI5 드라이버(`nvidia-oot/.../vi5_fops.c`, NRV 저장소의 `.cache/pristine`)를 읽었다:
  - `vbuf->sequence`는 내준 버퍼마다 1씩 는다. 그래서 VI가 버퍼가 없어 받지 못한 MIPI 프레임은 sequence에 드러나지 않는다. 11.108 앞단의 "lost by sequence"는 늘 0일 수밖에 없다. 이름을 "skipped in sequence"로 바꿨다.
  - 고칠 수 있는 오류(`corr_err`)는 경고와 `err_data`를 `kern.log`에 남긴다(philip은 adm 그룹이라 읽을 수 있다). 그 버퍼는 `VB2_BUF_STATE_ERROR`, 곧 `V4L2_BUF_FLAG_ERROR`로 돌아온다.
  - 고칠 수 없는 오류(`uncorr_err`: 2.5초 동안 응답 없음 등)는 채널 오류가 된다. 이때는 앞단의 "3초 동안 버퍼 없음"이 캡처를 다시 시작한다. 11-08 NRV 시절 `kern.log`에 이 메시지가 있다.
  - 링크 오류 비트(CRC, ECC)의 자세한 값은 `err_data`에만 있다. 카운터로 따로 얻을 수 없다(rtcpu trace는 root 필요).

**바꾼 것(`tools/jetson/dvs-jetson.py`):**
- MIPI 프레임 수: `dvsctl.mipi`를 바꿔, 디코더 링크 통계의 `bufs`를 쓴다(VI가 돌려준 버퍼는 모두 해독한다).
  - 처음에는 캡처 루프의 실시간 카운터를 썼다. 디코더 통계는 2초마다 찍어 두는 값이라, 같은 행에서 131 차이(약 2초 치)가 났다. 그래서 같은 스냅샷의 값으로 바꿨다.
- 링크 오류: 오류 플래그가 붙은 버퍼 수.
- DRAM 그래프: DQBUF마다 다른 버퍼를 QUERYBUF해서 QUEUED(빈 버퍼)와 DONE(처리 대기)을 센다. `frame_bytes`(1 MiB)도 넘긴다. ov5647-cap.py `note_bufs`와 같다.
- CRC 타일은 값이 없어 "기록 없음"으로 남는다.

**측정(관찰, margin 0, flush 50 µs):**
- MIPI 프레임 72.1/s, MIPI 데이터량 75.6 MB/s(= 72 × 1 MiB, VI가 받는 줄 전체), 이벤트 데이터 28.5 MB/s, 프레임 채움 37.7%.
- 빈 버퍼는 16개 중 최소 14~15개, 처리 대기 최대 0~1개, 버퍼 복사(해독) 2~2.9 ms.
- 링크 오류 0, 짧은 버퍼 0, scan 누락 0, frame_end 누락 0.
- 패널을 xwd로 떠서 그래프가 차는 것을 확인했다.

### 11.110 현미경 뷰어 외부 접속: 비밀번호 페이지 + Cloudflare 임시 터널 (보드4, 2026-11-26)

사용자: "모두 깃에 각각 기록하고, 현미경 외부에서 접속 가능하게 해줘".

**방식:**
- 11.106에서는 터널을 띄우려다 권한 검사가 막았다. 뷰어(`scope.py`, :8090)에 인증이 없어서, 링크를 아는 사람은 누구나 노출, LED, 레지스터, 촬영, `/quit`을 쓸 수 있었기 때문이다. 이번에는 비밀번호를 먼저 걸었다.
- nginx 사이트 `tools/board/scope/nginx-scope.conf`:
  - `127.0.0.1:8091`에서만 듣고, Basic 인증(사용자 `scope`)을 거친 요청만 :8090으로 넘긴다.
  - `proxy_buffering off`로 둔다. `/frame.png`의 long poll 응답이 바로 나가게 하기 위해서다.
  - 비밀번호 파일 `/etc/nginx/e850-scope.htpasswd`(apr1, root:www-data 640)는 보드에만 있고 git에는 넣지 않는다. 비밀번호는 사용자에게만 알렸다.
- `tools/board/scope/e850-scope-tunnel.service`:
  - philip 권한으로 `~/bin/cloudflared tunnel --no-autoupdate --url http://127.0.0.1:8091`(11.75의 linux-arm64 바이너리, 2026.11.1)을 돌린다.
  - 계정 없는 trycloudflare 임시 주소라서, 시작할 때마다 주소가 바뀐다. 지금 주소는 `journalctl -u e850-scope-tunnel | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' | tail -1`로 본다.
  - enabled 상태라 재부팅해도 다시 뜬다.
- LAN의 `http://<보드>:8090/`과 데스크톱 아이콘(`e850-open scope`)은 그대로다(인증 없음).

**확인(관찰, Mac에서 외부 주소로):**
- 인증 없음: 401. 틀린 비밀번호: 401(보드 안에서 시험).
- 맞는 비밀번호:
  - 페이지 200(124,345 B, 0.14 s).
  - `/stat` 200(`streaming`, 2.19 fps).
  - `/frame.png?fmt=raw` 200(774,144 B 회색 바이트, X-Region 전달, 0.50 s).
- 터널은 Cloudflare icn06에 등록됐다. ICMP 프록시 경고는 ping 그룹 범위 때문이고, HTTP와는 상관없다.
- 처음 만든 비밀번호 파일은 비어 있었다. `echo 비밀번호 | sudo -S tee`에서 sudo가 표준 입력을 먼저 가져갔기 때문이다. 임시 파일을 만든 뒤 `sudo install`로 다시 만들었다.
- 그때 현미경은 타임랩스(30초마다, 48시간, 시료 "고해상도비교실험…")를 찍고 있었다. 건드리지 않았다.

**보드4에 바꾼 것:**
- `/etc/nginx/sites-available/e850-scope`(+ sites-enabled 링크), `/etc/nginx/e850-scope.htpasswd`.
- `/etc/systemd/system/e850-scope-tunnel.service`(enabled, 지금 실행 중). 끄려면 `sudo systemctl disable --now e850-scope-tunnel`.
- 비밀번호를 바꾸려면 nginx-scope.conf 머리말의 명령으로 파일을 다시 만든다. nginx는 요청마다 파일을 읽으므로 reload하지 않아도 된다.

**메일(사용자: "현미경 접속 주소와, 아이디 패스워드를 메일로 보내"):** 주소, 아이디, 비밀번호, 주소가 바뀌었을 때 확인하는 법, 터널 끄는 법을 Mail.app으로 사용자의 연세 주소에 보냈다. 보낸편지함에서 2026-11-26 00:34:12 KST로 확인했다. 보내기 직전에 터널 주소가 그대로이고 접속이 200인 것도 확인했다.

### 11.111 Jetson Orin Nano와 E850의 DVS 처리 성능 비교 (2026-11-26)

사용자: "제슨오린나노 와 850 dvs 데이타 처리 성능 비교테이블 만들어".

**방법:**
- `tools/linux510/dvsbench.py`(새로 만듦):
  - `dvs_record()` 파일을 127줄 버퍼로 만든다(`dvs_replay()`와 같다). 그것을 각 보드의 `libdvsview.so`에 최대 속도로 넣고, `dvs_ingest()`만 잰다.
  - 필터 스레드(`dvs-filter`)의 CPU 시간은 `/proc/self/task/*/schedstat`에서 읽는다. 워드 10만 개마다 필터가 따라잡을 때까지 기다리므로, 필터가 앞으로 건너뛰는 일(FLAG 2M)은 없다.
  - 이벤트 수는 event group 워드의 두 마스크 popcount로 센다. 두 보드에 같은 값이 쓰인다.
- 녹화 두 개를 두 보드에서 똑같이 돌렸다:
  - E850 녹화: 보드6 `20261124T005912Z-none.gz`. 8 s, 5,955만 이벤트, margin 17, flush 50. 원시 111 MB.
  - Jetson 녹화: `20261125T154001Z-none.gz`. 5 s, 7,745만 이벤트, margin 0, flush 50. 원시 135 MB.
- 재는 동안 각 보드의 실시간 캡처는 멈췄다. CPU1~3을 같이 쓰기 때문이다.
  - Jetson: `systemctl --user stop dvs-jetson-now`, 끝난 뒤 `dvs-jetson-now2`로 다시 띄웠다.
  - 보드6: x-kms.sh가 만든 임시 유닛 `e850-dvs`를 stop했다. 끝난 뒤 같은 `systemd-run --unit=e850-dvs --collect -E DISPLAY=:1 dvs --auto`로 다시 띄웠다. 임시 유닛이라 stop하면 사라지고, 그래서 `systemctl start`는 "not found"였다.
- 해독 CPU의 클럭: E850 2002 MHz(uclamp). Jetson 1344 MHz(전원 모드 25W의 상한, 하드웨어 최대 1728 MHz).
- 빌드: 같은 dvsview.c를 `-O2`로. E850은 Debian 12 gcc, Jetson은 Ubuntu 24.04 gcc.

**같은 녹화, 이벤트 하나당 CPU 시간(관찰, ns, 낮을수록 빠름):**

| 측정 | 녹화 | E850 A55 2.0 GHz | Jetson A78AE 1.34 GHz | Jetson/E850 |
|---|---|---:|---:|---:|
| 해독(필터 끔) | E850 | 15.21 | 10.18 | 1.49배 |
| 해독(필터 끔) | Jetson | 14.17 | 9.40 | 1.51배 |
| 해독(필터 5 ms 켬) | E850 | 24.04 | 10.73 | 2.24배 |
| 해독(필터 5 ms 켬) | Jetson | 20.46 | 9.84 | 2.08배 |
| 잡음 필터 | E850 | 60.92 | 16.71 | 3.65배 |
| 잡음 필터 | Jetson | 59.08 | 15.77 | 3.75배 |

- 코어 하나의 처리량(백만 이벤트/s):
  - 해독: E850 65.7~70.6, Jetson 98.2~106.4.
  - 필터: E850 16.4~16.9, Jetson 59.8~63.4.
- 버퍼(127줄)당 해독: E850 2.93~3.01 ms, Jetson 1.96~1.99 ms.
- 클럭당으로 환산하면(계산): 해독 약 2.2배, 필터 약 5.5배다.

**실시간(관찰). 센서 설정은 같고(scan 495 µs, flush 50~51 µs), 장면과 센서 개체는 다르다:**

| 항목 | E850 보드6 | Jetson |
|---|---:|---:|
| 프레임(33 ms)당 이벤트 | 41.4만 | 51.8만 |
| MIPI 버퍼 | 65.5/s | 72/s |
| 버퍼당 해독(`ingest_ms`) | 4.3 ms | 2.0 ms |
| 최신 이벤트와의 지연 | 30 ms | 23 ms |
| 캡처·해독 주 스레드 CPU | 29.9%(처음 99.9%로 적은 것은 잘못 읽은 값, 11.112) | 16.4% |
| GPU 뷰 `dvsgl` CPU | 22% | 약 4.5% |
| 함께 도는 것 | 행동 분류 `dvs-act` 55%, `dvs-bin` 30%, 얼굴 `dvs-face` 40% | 없음 |
| 시작 뒤 누적 손실 | fe_missed 9, short 3, ts_bad 4 (25.6만 버퍼) | 모두 0 |

**해석:**
- 같은 코드와 같은 데이터로, Jetson 코어 하나가 해독은 약 1.5배, 필터는 약 3.7배 빠르다. 클럭은 67%밖에 안 된다.
- 필터가 특히 크게 차이 나는 까닭은 가설로, 화소마다 시각을 두는 표를 무작위로 읽는 일이 캐시와 메모리에 묶이기 때문으로 본다. A78AE는 L2 256K/코어, L3 2 MB/클러스터다. E850의 캐시 크기는 보드에서 읽지 못했다(`lscpu -C`가 비어 있음).
- E850은 필터를 켜면 해독도 15 → 24 ns(+58%)로 느려진다. Jetson은 +5%다. 가설: 필터 스레드(CPU3)가 같은 클러스터의 캐시와 메모리를 두고 다툰다.
- 용량(계산): E850은 CPU 필터 한계가 코어 하나로 초당 약 1,650만 이벤트다. 바쁜 장면(움직일 때 초당 3,500만, 11.102)에서는 CPU 필터가 뒤처진다. 보드6 데스크톱은 GPU 뷰가 직접 필터하므로 CPU 필터는 쉰다.
- (11.112에서 고침) 보드6 주 스레드의 99.9%는 `top -H -p`가 잘못 보인 값이었다. `/proc/<pid>/task/*/stat`로 30초를 재면 29.9%이고, 그중 해독이 약 23%(3.5 ms × 65.8/s)다.
- 정리: "Jetson이 디코딩을 못 한다"는 느낌은 하드웨어가 아니라 예전 NRV RAW2 파이프라인 탓이었다. 같은 디코더로는 Jetson이 빠르다.
- MAXN_SUPER(1728 MHz)로 바꾸면 클럭만으로 약 29% 더 빨라질 것이다(계산, 재지 않음).

**Jetson에서 같이 고친 것:**
- 페이지의 녹화가 507이었다. `dvsctl.Recorder`의 기본 디렉터리가 정의할 때 값(`/var/tmp/dvsrec`)으로 굳어서, dvs-jetson.py가 바꾼 `REC_DIR`을 보지 않았기 때문이다.
- `dvsctl.start()`가 호출할 때 `REC_DIR`을 넘기도록 고쳤다. E850은 값이 같아 동작이 그대로다. dvs-jetson.py는 `~/dvsrec`을 만든다. `test_dvsctl` 통과.

**보드에 둔 것:**
- Jetson: `~/bench/{e850,jetson}.gz`, `~/dvs-jetson/dvsbench.py`, `~/dvsrec/20261125T154001Z-none.{gz,json}`.
- 보드6: `/var/tmp/bench/`(녹화 둘, dvsbench.py).

### 11.112 보드6 DVS: 캡처 루프·행동 분류·얼굴 검출을 C로 (2026-11-26)

사용자: "비교는 새로운 세션에서 하자, 저장하고, 새로운 세션 가기 전에, 파이썬 캡처 루프, 분류기 얼굴 검출 등을 c로 최적화해".

**먼저 잰 것(관찰).** 보드6, 30초, `/proc/<pid>/task/*/stat`의 utime+stime(`thrcpu.sh`). 프레임당 약 28만 이벤트, 버퍼 65.8/s:

| 스레드 | CPU(코어 하나 = 100%) | 하는 일 |
|---|---:|---|
| 주 스레드(`python3`) | 29.9% | Python 캡처 루프 + `dvs_ingest` 해독(3.5 ms × 65.8/s ≈ 23%) |
| `dvs-act` | 54.3% | Python: numpy 변환, onnxruntime, 판정. 한 단계 33 ms |
| `dvs-face` | 40.7% | Python: numpy 변환, YuNet. 한 번 120~135 ms |
| `dvs-bin` | 23.0% | C 묶기(`dvs_act_bin`) + Python 큐 |
| `dvs-render` | 3.9% | C |
| 합계 | 152.7% | |

- 11.111 표의 "주 스레드 99.9%"는 `top -H -b -p`가 보인 값을 잘못 읽은 것이었다. 위 표가 맞다.
- 보드4에서 모델만 따로 재면(CPU5, 1스레드, ORT 1.31.1 프로파일): 분류 26.8 ms(Conv 55%, FusedConv 30%), 얼굴 110.9 ms(Conv 55%, FusedConv 39%). 시간 대부분이 onnxruntime 합성곱이다. 따라서 C로 옮겨 줄일 수 있는 것은 그 바깥(Python 루프, numpy 변환, GIL 대기)뿐이라고 보고 시작했다.

**바꾼 것(저장소):**
- `tools/linux510/dvsview.c`:
  - `dvs_capture()`: ov5647-cap.py `--live --raw8`의 캡처 루프를 C로 옮겼다. Python은 노드 준비·버퍼 할당·stream on까지 하고, `dvs_cap_node`·`dvs_cap_shot`으로 버퍼를 넘긴 뒤 `dvs_capture(len, 500 ms, …)`를 되풀이해 부른다.
  - C 루프가 하는 일(GIL 없이): VC0 DQBUF, `dvs_ingest`, 다른 버퍼 QUERYBUF 집계(`dvs_buf_note`), QBUF. leader(메타) 버퍼는 shot 필드를 되써서 다시 QBUF한다(예전 `write_shot`).
  - 신호가 오면 `-EINTR`로 돌아가서 Python이 SIGTERM/SIGINT를 처리한다. VC0 버퍼가 `-t`초 없으면 `-ETIMEDOUT`, 2초 창에 짧은 버퍼가 `--stuck`개 이상이면 `-EPIPE`(예전 `check()`)를 돌려준다.
  - `dvs_buf_note`/`dvs_buf_take`: 페이지 DRAM 그래프의 버퍼 수를 C에 둔다. dvsctl의 `buf_note`/`BUFS`를 대신한다. Jetson의 dvs-jetson.py도 이것을 부른다.
  - `pin()`을 CPU 마스크를 받는 `dvs_pin()`으로 내보냈다.
- `tools/linux510/dvsai.c`(새 파일): `-DDVS_AI`로 빌드하면 dvsview.c가 포함한다.
  - onnxruntime C API를 Python 휠의 `libonnxruntime.so.1.31.1`에서 dlopen한다. 그래서 libdvsview.so에 링크 의존성이 생기지 않는다(`dvsact.ort_lib()`가 경로를 찾는다).
  - 스레드는 CPU4~6, 최고 클럭(uclamp), onnxruntime 1스레드로 돈다.
  - `dvs-bin`: 60 ms마다 링을 묶어 큐(8)에 넣는다.
  - `dvs-act`: log1p → 모델(GRU 상태 h) → softmax → `dvs_act_observe`(dvsact.observe와 같은 판정, JSONL 줄, `/act` 상태).
  - `dvs-face`: 200 ms마다 `dvs_surface` 100 ms를 화면 방향으로 뒤집고 0/255로 만들어 YuNet에 넣는다(`dvs_face_detect`, 출력 9개만 받음). 최고 점수 상자에 최근 5개 중앙값 너비를 붙여 `dvs_mark`로 표시한다.
  - 로그 형식은 예전과 같다(`[dvsact] steps …`, `[dvsface] N runs …`).
- `dvsact.py`·`dvsface.py`: `start()`가 C를 부른다. Python 스레드(`_run`, `_bin`)는 지웠다. `Binner`, `Judge`, `observe`, `line`, `detect`, `decode`는 Mac 학습·평가와 비교 시험의 기준으로 남겼다.
- `ov5647-cap.py`: `DvsLive.capture()`가 C 루프를 부른다. `stuck()`은 로그·kmsg·SIGTERM만 한다. OV5647 `Live` 경로는 그대로 Python이다.
- `dvsctl.py`: `/act`는 `dvs_act_status()`를 그대로 보낸다(없는 libdvsview면 off JSON). DRAM 그래프는 `dvs_buf_take()`에서 읽는다.
- `features/host/bundle.sh`: onnxruntime 1.31.1 헤더를 NuGet 패키지에서 받아(sha256 고정, `out/ort/`, git에는 넣지 않음) `-DDVS_AI -I… -ldl`로 빌드한다. GitHub의 microsoft/onnxruntime은 404였다(2026-11-26).
- `tools/jetson/deploy.sh`는 그대로다. dvsai 없이 빌드하므로 Jetson의 `/act`는 off다.

**찾아서 고친 버그(관찰):**
- `dvsface.decode`(Python, 11.99부터): 안쪽 루프가 상자 너비를 `w`에 넣는데, `w`는 입력 너비이기도 했다. 그래서 한 stride에서 후보가 나오면 다음 stride의 격자 너비 `n = w // st`가 틀렸다.
  - 결과: stride 16·32 후보의 위치가 틀렸다. 그 후보가 점수 최고면 얼굴 표시가 엉뚱한 곳에 갔을 수 있다. 보드에서 실제로 그랬는지는 확인하지 않았다.
  - C 판과 비교 시험이 이것을 잡았다(문턱 0에서 Python이 nan/inf). Python 쪽 변수 이름을 고쳤다.

**검증(관찰):**
- `tools/linux510/test_dvsai.py`(새 파일): 시험 4개.
  - 판정: 3000단계 × 2(모든 클래스 / `none sit_down stand_up`). JSONL 줄과 `/act` JSON이 Python과 글자까지 같았다.
  - 모델 한 단계: 40프레임, 중간에 리셋. 확률 차이 최대 1e-5 미만.
  - YuNet: 합성 영상 12장, 문턱 0과 0.4. 상자가 rtol 1e-4 안에서 같았다.
  - 버퍼 통계.
- 다른 시험: `test_dvsact` + `test_dvsctl`(`test_buf_note`는 test_dvsai로 옮김)과 합쳐 27개 통과. Mac Docker는 Debian 12 arm64 + ORT 1.31.1 + numpy 1.24, 보드6은 16개.
- 스레드까지 붙인 전체 경로: Docker에서 `sit_down` 녹화(1,282만 이벤트)를 `dvs_replay`로 실시간으로 넣었다. 같은 링을 Python `CBinner`도 읽어 예전 Python 경로로 돌렸다.
  - 20초(332프레임) 동안 상태 변화 5개가 p 값까지 같았다(appear stand_up 0.79, sit_down 0.77, stand_up 0.81, sit_down 0.86, stand_up 0.71).
- 함께 본 것(관찰, 따로 원인을 보지 않음): 같은 녹화도 라이브러리 경로(`dvs_replay` → `dvs_ingest`)와 시험 도구 `dvsview --replay`(줄마다 `decode`)의 이벤트가 다르다. 34번째 60 ms 프레임에서 87,326 대 88,722로 1.6% 차이였다.
  - Mac 학습 자료(`.bin`)는 시험 도구 경로로 만든다. 그래서 보드 추론과 학습 사이에 작은 차이가 있다는 뜻이다.

**보드6에서 잰 결과(관찰).** 같은 방법으로 30초, 프레임당 약 28만 이벤트, 버퍼 66/s:

| 스레드 | 전(Python) | 후(C) | 차이 |
|---|---:|---:|---:|
| 주 스레드(캡처 + 해독) | 29.9% | 24.9% | −5.0 |
| `dvs-act` | 54.3% | 52.0% | −2.3 |
| `dvs-bin` | 23.0% | 21.3% | −1.7 |
| `dvs-face` | 40.7% | 40.3% | −0.4 |
| `dvs-render` | 3.9% | 3.9% | 0 |
| 합계 | 152.7% | 143.1% | −9.6 |

- 해독(`ingest_ms`)은 3.5~3.6 ms로 그대로다. 주 스레드가 줄어든 5%가 Python 루프 몫이다.
- 손실: 30초 동안 짧은 버퍼, fe_missed, ts_missed, ts_bad 모두 0. 버퍼는 1974개(65.8/s)였다.
- 단계 시간(로그): 분류 34~35 ms, 얼굴 119~129 ms. 모델 계산이 그대로라 거의 같다.
- `/act`, `/history`의 `vc0_n 8, free 6, wait 1`, 상태 변화 JSONL이 나오는 것을 확인했다.
- 처음 배포한 판은 `dvs-act` 스레드에서 `dvs_pin`을 빠뜨렸다. 이름이 `python3`이고, CPU 0~7 아무 데서나 uclamp 없이 58%를 썼다. 고쳐서 다시 넣은 판이 위 표다.
  - 그 판에서는 해독이 2.4 ms로 빨랐다. 까닭은 보지 않았다(가설: 분류기가 클러스터0에서 돌아 클러스터1의 메모리 경합이 줄었다).

**실패하거나 쓰지 않은 실험(관찰, 보드4 CPU5):**
- FP16 모델(onnxconverter-common, GRU·Gemm·Softmax는 FP32로 둠):
  - 분류는 27.0 → 21.3 ms(1.26배)였지만 출력 차이가 최대 0.022였다. 프로파일에서 FP16 Mul 30%, Cast 9%, Transpose 8%가 이득을 먹었다.
  - 얼굴은 MaxPool(opset 11)에 FP16 커널이 없어 세션을 만들지 못했다.
  - 판정이 바뀔 위험에 비해 이득이 작아 쓰지 않는다.
- YuNet 입력 3채널(모두 같은 흑백)을 첫 합성곱에서 1채널로 접는 방법: 첫 Conv가 얼굴 시간의 10.2%라서 줄어드는 몫은 약 7%(코어의 3%)다. 모델 변환 단계를 더할 만하지 않아 하지 않았다.

**정리:**
- Python을 C로 옮겨서 줄어든 것은 합계로 코어 하나의 약 10%(152.7 → 143.1%)다. 남은 분류 52%와 얼굴 40%는 onnxruntime의 FP32 합성곱이다.
- 더 줄이려면 동작을 바꿔야 한다. 예를 들어 얼굴 검출 횟수를 줄이거나(지금 약 3회/s), 이벤트가 적을 때 건너뛰거나, 더 작은 입력을 쓴다. 이는 사용자가 정할 일이다.

**보드에 바꾼 것:**
- 보드6:
  - 설치: `/usr/local/lib/libdvsview.so`(DVS_AI), `/usr/local/bin/ov5647-cap.py`, `/usr/local/share/dvsview/{dvsctl,dvsact,dvsface}.py`.
  - 예전 파일은 `/var/tmp/pre-11112/`에 있다. 시험 파일과 `thrcpu.sh`(저장소 `tools/board/thrcpu.sh`)는 `/var/tmp/`에 있다.
  - `e850-dvs`는 `systemd-run --unit=e850-dvs --collect -E DISPLAY=:1 /usr/local/bin/dvs --auto`로 다시 띄웠다.
- Jetson: `sh tools/jetson/deploy.sh philip@192.168.0.195`로 넣었다. 실행 중인 `dvs-jetson.py`를 SIGTERM하니 런처가 새 판으로 다시 띄웠다(79 MIPI 프레임/s, 오류 0, DRAM 그래프 정상, `/act` off).
- 보드4와 Mac 복제 이미지(`clone-20261124-b6`)에는 아직 예전 판이 들어 있다.


### 11.113 Jetson: 바탕화면 아이콘, USB 카메라 두 대 화면 표시 (2026-11-26)

사용자: "바탕화면에 지금 작동하는거 실행 아이콘 만들어줘, 젯슨", 이어서 "잿슨에 USB 카메라 연결했어 실행시키고 화면에 띄워".

**아이콘(바꾼 것):**
- `tools/jetson/dvs-jetson.desktop`을 만들었다. "DVS (E850 디코더)", 아이콘 `camera-web`, `~/dvs-jetson/dvs-jetson`을 실행한다.
- `deploy.sh`가 이것을 `xdg-user-dir DESKTOP`에 755로 넣고 `gio set … metadata::trusted true`를 한다. DING은 실행 가능하고 trusted인 .desktop만 실행한다.
- 이미 떠 있을 때 누르면 런처가 `notify-send`로 "이미 실행 중"을 알리고 끝난다(flock).
- NRV의 "DVS 디코더" 아이콘(`DVS-Decoder.desktop`, NRV `launch.sh`)은 그대로 두었다.
- 확인(관찰): `desktop-file-validate` 통과, trusted true. 화면 캡처에서는 창에 가려 아이콘이 보이지 않았다.

**NRV 수신 서비스(정리):**
- `nrv-dvs-streamer.service`(User=philip, 부팅 때 `run.py start --no-web --settings …Delta_10_2000FPS_RAW2.txt`)는 협력 측의 예전 경로다. 부팅에서 dvs-jetson이 멈출 때까지 31분 동안 CPU 20분 43초를 썼다(약 0.67 코어, 계산).
- 사용자와 정리해서 끄지 않고 둔다. dvs-jetson이 시작할 때 멈춘다.

**USB 카메라(관찰):**
- 처음에는 커널에 USB 장치가 들어온 기록이 없었다. USB-C 포트는 `usb2-0-role-switch: device`라서 카메라를 받지 않는다. USB-A에 꽂아 달라고 했다.
- 꽂힌 뒤: Generic HD camera(1903:8328) 두 대가 USB 2.0 허브의 1-2.3(`/dev/video1`)과 1-2.2(`/dev/video3`)에 잡혔다. 형식은 YUY2 640×480 30/5 fps와 320×240뿐이다(MJPEG 없음).
- 표시: `systemd-run --user --unit=usbcam -E DISPLAY=:0 -E XAUTHORITY=/run/user/1000/gdm/Xauthority gst-launch-1.0 -e compositor … xvimagesink`. 두 영상을 1280×480 한 창에 나란히 놓았다.
  - 두 대 합쳐 약 37 MB/s를 같은 USB 2.0 허브로 받는다. 오류 없이 돌았다.
  - Jetson 화면 캡처(`ximagesrc`)로 두 영상이 나오는 것을 확인했다. fps는 재지 않았다.
- 끄기: `systemctl --user stop usbcam`.

**카메라 한 대, 아이콘(바꾼 것):** 사용자 "카메라 하나만 뺏어, 다시 나오게 해, 아이콘도 바탕화면에 만들어 주고".
- 흐린 쪽(1-2.3)을 뺐다. 남은 카메라는 `/dev/video3`(1-2.2)이다. 카메라를 빼면 그때 떠 있던 `usbcam` 유닛이 실패로 끝났다(관찰).
- `tools/jetson/usbcam`: USB 카메라를 스스로 찾는다(`vi-output` = DVS는 뺌, index 0만). 한 대면 그대로, 두 대면 나란히 띄운다. 없으면 `notify-send`로 알리고, 이미 떠 있으면 flock으로 알린다.
- `tools/jetson/usbcam.desktop`("USB 카메라", `accessories-camera`)도 만들었다. `deploy.sh`가 두 .desktop을 같은 방식(755 + trusted)으로 넣는다.
- 확인(관찰): `gst-launch-1.0 … v4l2src device=/dev/video3 … xvimagesink`가 떴다. Jetson 화면 캡처에 영상이 나오고, 바탕화면 오른쪽 위에 "USB camera" 아이콘이 보였다. 사용자도 "잘 나와"라고 했다.

### 11.114 보드6: USB 카메라 아이콘, 현미경·DVS·USB 카메라는 연결됐을 때만 (2026-11-26)

사용자는 다음을 요청했다.
- "850에도 같은 카메라를 연결했어 나오게 해"(보드6)
- "현미경이 연결될때만 작동하게해야지, DVS, USB camera도 마찬가지야, 연결될때만 작동하게해"
- "850에서 카메라 잘 나온다, 이것도 아이콘 바탕화면에 올려줘"

**현미경 뷰어가 다른 카메라를 잡던 것(관찰 → 고침):**
- 보드6에는 현미경이 없다. 그런데 꽂은 Generic HD camera(1903:8328, 2-1.3, `/dev/video0`)를 `e850-scope`가 바로 잡았다. `v4l2-ctl -d /dev/video0 … --stream-to=-`가 돌았다.
- 원인: `scope.py`는 USB 쪽 `/dev/video*` 가운데 첫 번째를 현미경으로 본다. 그래서 그 카메라에 SOL 펌웨어 vendor 요청(`sol.diag`, 0xC0 0xD0)도 보낸다.
- 고침: `usb_id()`가 `04b4:00c3`(EEPROM으로 부팅한 SOL 키트, 11.74)인 장치만 받는다.
- 보드6에 넣고 `GET /reload`로 `scope.py`만 다시 띄웠다. 서비스를 재시작하면 `scope-run.sh`가 USB 호스트를 내렸다 올리기 때문이다. 그 뒤로 `v4l2-ctl`이 뜨지 않았다(관찰).
- **보드4(현미경 있는 보드)에는 아직 넣지 않았다.** 거기서는 현미경만 꽂혀 있어 지금 동작이 같다.

**USB 카메라 화면:**
- `e850-open cam`은 현미경이 아닌 USB 카메라(index 0)를 찾아 `mpv --vo=gpu --profile=low-latency --untimed --title="USB 카메라" av://v4l2:/dev/videoN`으로 띄운다.
- `--vo=xv`(GLAMOR Xv)는 `BadShmSeg`로 실패했다. Xv·x11 출력은 MIT-SHM을 쓰는데 이 커널에는 SysV IPC가 없기 때문이다(11.97과 같은 원인). `--vo=gpu`는 세션의 GPU shim(`LD_PRELOAD`)으로 돈다.
- 형식은 장치의 현재 값(640×480 YUYV 30 fps)을 그대로 쓴다.
- mpv는 한 순간 `top`에서 20%였다(한 번 잰 값).

**바탕화면 아이콘(`features/desktop`):**
- `e850-cam.desktop`("USB 카메라", `camera-web`)을 더했다. desktop 기능이 늘 넣는다.
- 보드6 `~/Desktop`에 넣었다. pcmanfm은 위치가 없는 새 아이콘을 DVS 창 뒤(왼쪽 위)에 놓았다. 그래서 사용자 설정에 `x=224 y=860`을 넣고, 세션 환경 그대로 `pcmanfm --desktop`을 다시 띄웠다. DVS, 현미경, USB 카메라 아이콘이 한 줄로 보였다(화면 캡처).
- `e850-open`은 장치가 있을 때만 연다.
  - scope: USB에 `04b4:00c3`이 없으면 띄우지 않는다.
  - cam: 현미경이 아닌 UVC 카메라가 없으면 띄우지 않는다.
  - 장치가 없으면 `e850-say`(python3-gi GTK 안내 창)로 "…연결되어 있지 않습니다"를 보인다. zenity는 webkit까지 끌어와서 쓰지 않았다.
- 확인(관찰, 보드6):
  - cam 아이콘 경로로 창이 떴다. 두 번째 실행은 새 mpv를 띄우지 않고 그 창을 앞으로 가져왔다(mpv 1개).
  - scope는 한글 안내 창을 띄웠다(화면 캡처).

**DVS도 센서가 있을 때만:**
- `dvslive.sh`는 이제 늘 `--need-sensor`로 실행한다. 센서가 없으면 종료 코드 3으로 끝나고, 데모 녹화(11.97)는 더 이상 틀지 않는다.
- `dvslive.service`에 `RestartPreventExitStatus=3`을 넣었다. 센서가 없으면 3초마다 다시 시작하지 않는다.
- `dvs`(아이콘·터미널)는 센서가 없으면 `e850-say`로 "DVS 센서가 연결되어 있지 않습니다 (CN4)"를 보인다. `--auto`(데스크톱 시작)일 때는 로그에만 남긴다.
- 보드6에 넣었다(`/usr/local/bin/dvs`, `dvslive.sh`, `dvslive.service` + daemon-reload). 돌던 DVS(`e850-dvs`)는 그대로 두었다.
- **센서 없는 경로는 시험하지 않았다.** 보드6에는 센서가 있다. 보드4(센서 없음)에는 아직 넣지 않았다.

**CPU7(질문에 답한 것, 관찰):**
- 5초 동안 잰 보드6 코어 사용률은 CPU0 36, CPU1 38, CPU2 30, CPU3 32, CPU4 71, CPU5 52, CPU6 27, **CPU7 1%**였다. DVS + 분류 + 얼굴 + USB 카메라가 돌고 있었다.
- CPU7은 11.30에서 사용자가 요청해 웹 전용으로 비워 둔 코어다("웹 서비스를 위해 하나 할당해서 더 빠른 서비스를 제공해"). DVS 설정 페이지 스레드 `dvs-web`과 Wi-Fi IRQ(`scsc_wlbt`, 11.69)가 여기서 돈다. systemd 유닛은 CPU0~6만 쓴다(`e850-cpu7.conf`).
- 측정할 때 페이지를 연 브라우저가 없어서 1%였다.

**두 보드의 카메라 뷰어를 같게(바꾼 것):**
- 사용자: "왜 다른 뷰어를 사용했어, 같은걸로 해". 비교 시험에 DVS와 USB 카메라를 두 보드에 똑같이 연결하기로 했다.
- 그때까지 Jetson은 `gst-launch … xvimagesink`, 보드6은 mpv였다. 보드마다 먼저 되는 것을 쓴 결과다. 보드6에는 GStreamer가 없고, Xv는 MIT-SHM이 없어서 안 된다.
- 두 보드 모두 `mpv --vo=gpu --profile=low-latency --untimed --no-terminal --title="USB 카메라" av://v4l2:/dev/videoN`으로 맞췄다.
  - Jetson: `tools/jetson/usbcam`을 mpv로 바꿨다. 카메라가 두 대면 `--external-file` + `--lavfi-complex=[vid1][vid2]hstack[vo]`로 나란히 띄운다(두 대일 때는 시험하지 않았다).
  - 보드6: `e850-open cam`.
- mpv는 사용자가 Jetson에 설치했다(`sudo apt install mpv`, 0.37.0, 패키지 19개). 보드6은 0.35.1이다.
- `--no-terminal`을 넣은 이유(관찰): 유닛으로 띄우면 mpv 상태 줄(`V: …`)이 거의 매초 journal에 쌓였다. 그러면 journald 부하가 Jetson에만 더해진다. 두 보드에 같이 넣었다.
- Jetson에서 이전 mpv 유닛을 멈춘 직후에 새로 띄우면 두 번 바로 끝났다(종료 코드 2). 2초 기다린 뒤에는 정상으로 떴다. 원인은 확인하지 않았다(장치 해제가 늦었을 가능성, 가설).
- 확인(관찰): 두 화면 캡처 모두 "USB 카메라" 창에 영상이 나왔다. mpv CPU를 `top`으로 1초씩 한 번 쟀더니 Jetson 11%, 보드6 26%였다. 비교 측정이 아니다. 비교는 다음 세션에서 같은 부하로 한다.

### 11.115 Jetson Orin Nano와 E850: DVS·USB 카메라 실시간 비교, 같은 바이너리로 잰 아키텍처 차이 (2026-11-26)

사용자는 다음을 요청했다.
- "같은 부하로 … 1) DVS 디코더만 2) DVS + USB 카메라 화면 … 스레드별 CPU는 tools/board/thrcpu.sh PID 30으로 재고, mpv의 CPU도 따로 재. DVS 이벤트 손실, DVS·카메라 화면 fps, 지연도 같이 보고, 결과는 표로 11.115에 정리해."
- 세 번째 조건은 물어서 "두 가지로 충분"으로 정했다.
- 이어서: "내가 외출해야 하니, 너가 의미 있는 비교 실험을 해봐, 아키텍처 차원에서 왜 이런 결과가 나오는지도 서로 비교하고."
- 그 뒤: 보드6 sudo 비밀번호를 주었다(저장소·메모리에는 적지 않음, ssh 표준입력으로만 넘김). "8코어라서 장점이 있겠지", "암버전에 차이에 따른 성능 비교평가도 하고". 아래 "보드6 AI 끔", "8코어", "Arm 버전" 절이 그 결과다.

**같은 부하가 되지 못한 점(먼저 적는다):**
- 처음 측정 때는 보드6의 행동 분류·얼굴 검출(AI)을 끄지 못했다.
  - 끄려면 DVS를 `--face= --act=`로 다시 띄워야 하는데, 이 일은 root여야 한다. ssh의 philip에게는 sudo 비밀번호가 없었다.
  - 그 뒤 사용자가 비밀번호를 주어 AI를 끈 측정도 했다(아래 "보드6 AI 끔" 절).
  - 처음 실시간 표의 보드6은 AI가 켜진 채다. AI의 몫은 다음 세 가지로 따로 보였다.
    - AI 스레드를 표에서 따로 적었다.
    - 같은 모델을 두 보드에서 따로 쟀다(아래 aibench).
    - 메모리 경합의 영향을 따로 쟀다(아래 경합 실험).
- DVS 장면이 보드마다 다르다. 프레임당 이벤트가 Jetson 81만~131만, 보드6 32만~37만이었다.
- 두 카메라 모두 실제로는 15 fps였다(아래).

**도구(저장소, 새로 만들거나 바꾼 것):**
- `tools/board/thrcpu.sh`:
  - 실행마다 임시 디렉터리를 따로 쓴다. 그래서 프로세스마다 하나씩 동시에 돌릴 수 있다.
  - 스레드 이름의 공백을 `_`로 바꾼다. gnome-shell의 "JS Helper" 같은 이름에서 join이 틀리던 것을 막는다.
- `tools/board/cmpload.sh SECONDS [MPV_SOCKET]`(새 파일): 한 조건을 잰다.
  - 먼저 SECONDS 동안 다음을 함께 잰다.
    - 캡처 프로세스, dvsgl, mpv, Xorg, gnome-shell 각각의 `thrcpu.sh`.
    - `/proc/stat`의 코어별 사용률(idle·iowait를 뺀 것).
  - 그다음 SECONDS 동안 `viewstat.py`를 돌린다. 측정 도구 자신의 CPU가 앞의 값에 섞이지 않게 하려고 따로 돌린다.
- `tools/board/viewstat.py`(새 파일):
  - 창 갱신 횟수: "DVS" 창과 "USB 카메라" 창의 X Damage 이벤트를 센다. 수준은 NonEmpty이고 바로 subtract하므로, 그린 프레임마다 이벤트 하나다. python3-xlib가 두 보드에 없어서 ctypes(libX11, libXdamage)로 만들었다.
  - 카메라 프레임 나이: mpv IPC의 `time-pos`를 쓴다.
    - mpv를 `--rebase-start-time=no`로 띄우면 `time-pos`가 V4L2 버퍼 시각, 즉 CLOCK_MONOTONIC이다. uvcvideo는 이 시각을 프레임의 첫 USB 패킷에서 잡는다.
    - 나이 = 속성 알림을 받은 순간의 CLOCK_MONOTONIC − `time-pos`.
    - 알림은 초당 약 15번 오고 프레임마다 오지는 않는다.
    - 재는 범위는 카메라의 첫 패킷부터 mpv가 프레임을 출력으로 넘길 때까지다. GPU 그리기, 화면 합성, 스캔아웃은 들어가지 않는다. 빛이 화면에 나오기까지의 지연은 재지 않았다.
- `tools/board/archbench.c`(새 파일): 정적 arm64 바이너리 하나(Mac Docker, Debian 12 gcc 12 `-O2`)를 두 보드에서 그대로 돌린다. root가 없어도 된다.
  - 잴 항목: 의존 덧셈으로 잰 클럭, 정수 발행 폭, FP32 `fmla` 지연과 처리량, 분기 예측 실패, 포인터 추적 지연(4 KB~256 MB), 독립 읽기 겹침(MLP), 코어 하나와 여러 코어의 대역폭.
  - `-m CPUS SECS`로 읽기 부하를 걸어 둘 수 있다.
  - 페이지는 두 보드 모두 4 KB(`MADV_NOHUGEPAGE`)다.
- `tools/board/aibench.py`(새 파일): 보드6이 쓰는 두 모델을 onnxruntime 1스레드, 코어 하나로 돌린다.
- 도구 확인(관찰):
  - viewstat로 센 Jetson DVS 창은 30.0회/s였다. dvsgl의 자체 보고(30.0 frames/s)와 맞다.
  - archbench가 잰 클럭은 1.990 GHz와 1.337 GHz였다. cpufreq 값(2.002, 1.344 GHz)과 맞다.
  - archbench를 두 번 돌렸더니 값이 같았다.

**측정 방법:**
- 모두 philip 권한으로 쟀다. 한 번에 30초씩이다.
- mpv 옵션:
  - 카메라를 재는 동안은 두 보드 모두 원래 옵션에 `--geometry=640x480 --input-ipc-server=… --rebase-start-time=no`만 더해 다시 띄웠다.
  - 보드6의 원래 창은 443×332였다.
  - 보드6 mpv는 GPU shim 환경(`LD_PRELOAD`)이 있어야 돈다. 그래서 lxpanel의 `/proc/PID/environ`을 그대로 써서 띄웠다.
  - 끝난 뒤에는 원래 명령으로 되돌렸다. 보드6은 `e850-open cam`, Jetson은 `usbcam` 유닛이다.
- 보드6의 mpv를 처음에 `kill`로 닫으려 했지만 권한 검사가 막았다. 사용자가 실험을 맡긴 뒤에 창에 xdotool로 `q`를 보내 닫았다.
- Jetson: 전원 모드 25W(CPU 상한 1344 MHz), EMC 3199 MHz(bwmgr), GNOME(mutter 합성), DP 1920×1200 60 Hz. dvs-jetson은 AI 없이 빌드되어 있다.
- 보드6: CPU 2.0 GHz(uclamp), MIF 1539 MHz 고정. X :1은 modesetting + glamor이고 Mali-G52를 panfrost + kbase shim으로 쓴다. HDMI 1080p60이고 KMS 전환은 30 fps가 상한이다(11.85).

**실시간 결과 1: 프로세스와 스레드별 CPU(관찰, 코어 하나 = 100%, 30초).** 값이 둘이면 두 번 잰 것이다.

| 항목 | Jetson 1) DVS만 | Jetson 2) DVS + 카메라 | 보드6 1) DVS(+AI) | 보드6 2) DVS(+AI) + 카메라 |
|---|---:|---:|---:|---:|
| 캡처·해독 주 스레드 | 32.7 / 32.0 | 32.6 / 31.1 | 26.8 | 28.7 / 28.4 |
| `dvs-render`(CPU 렌더러, 웹 화면용) | 2.5 / 2.6 | 2.4 / 2.4 | 4.1 | 4.3 / 4.3 |
| AI: `dvs-act` + `dvs-bin` + `dvs-face` | 없음 | 없음 | 53.3 + 26.0 + 41.7 = 121.0 | 125.4 / 124.8 |
| `dvsgl`(GPU DVS 창) | 6.0 / 5.6 | 5.9 / 7.1 | 22.4 | 22.8 / 22.5 |
| mpv(카메라 화면) | — | 10.6 / 10.8 | — | 25.2 / 24.8 |
| X 서버 | 2.1 / 2.1 | 3.2 / 3.2 | 12.4 | 15.2 / 14.7 |
| gnome-shell(합성기) | 7.0 / 7.5 | 17.0 / 13.1 | 없음 | 없음 |
| 시스템 전체(`/proc/stat`) | 73.2 / 74.4 | 109.8 / 95.3 | 196.9 | 242.6 / 245.1 |
| 전체 − AI(계산) | 73~74 | 95~110 | 약 76 | 약 117~120 |
| 코어 수 | 6 | 6 | 8 | 8 |

- mpv 스레드 나눔(관찰):

  | mpv 스레드 | Jetson | 보드6 | 보드6/Jetson |
  |---|---:|---:|---:|
  | 주 스레드(V4L2 읽기, demux) | 2.3 | 7.0~7.3 | 3.1 |
  | `worker` 셋(YUYV → yuv422p 변환) | 4.5~4.7 | 13.7~13.8 | 3.0 |
  | `vo`(GPU 업로드와 그리기 제출) | 3.1 | 3.8 | 1.2 |
  | 합 | 10.6~10.8 | 24.8~25.2 | 2.3 |

  - 두 보드 모두 mpv가 `yuyv422`를 CPU에서 `yuv422p`로 바꾼다(`video-params`와 `video-out-params`, `hwdec no`). `--vo=gpu`가 packed YUYV를 바로 받지 않아서 생기는 변환이다. 그래서 두 보드가 같은 일을 한다.
- 카메라를 켰을 때 시스템 전체가 늘어난 양(계산):
  - Jetson은 +21~36%다. 그중 mpv가 10.7, gnome-shell이 +6~10, Xorg가 +1이다.
  - 보드6은 +46~48%다. 그중 mpv가 25, Xorg가 +2~3이다. CPU0(장치 IRQ가 모두 가는 코어)이 21%에서 40%로 올랐다. 이 +19%는 USB IRQ와 uvcvideo 처리로 본다(가설, 따로 재지 않음).

**실시간 결과 2: 손실, 화면, 지연(관찰).**

| 항목 | Jetson 1) | Jetson 2) | 보드6 1) | 보드6 2) |
|---|---:|---:|---:|---:|
| 프레임당 이벤트(시작/끝) | 126만~131만, 124만~127만 | 129만 → 81만 | 34.5만~34.6만 | 32.2만~34.8만 |
| MIPI 버퍼/s | 82~83 | 75.5~82.4 | 65.5~66 | 65.3~66 |
| 버퍼당 해독(`ingest_ms`) | 3.6~3.8 ms | 2.9~3.7 ms | 3.6~3.8 ms | 3.6~4.2 ms |
| 최신 이벤트와의 지연(`behind_ms`) | 22 ms | 22~23 ms | 27~28 ms | 26~30 ms |
| 30초 손실(short, fe_missed, ts_missed, ts_bad) | 모두 0 | 모두 0 | 모두 0 | 모두 0 |
| DVS 창 갱신(X Damage) | 30.0회/s | 30.0~30.1회/s | 30.0회/s | 30.0회/s |
| 카메라 창 갱신 | — | 15.1회/s | — | 15.1회/s |
| 카메라 프레임 나이 중앙값 / p95 / 최소 / 최대 | — | 65.9 / 66.5~66.6 / 61.4 / 98.7~108 ms | — | 71.9 / 79.5 / 64.3 / 95.1 ms |
| mpv가 버린 프레임(drop, decoder drop, delayed) | — | 0 | — | 0 |

- 손실을 세션 전체로 보면 다음과 같다.
  - Jetson은 링크 카운터가 처음부터 끝까지 0이었다. 아래 경합 실험 동안도 0이었다(bufs 33만).
  - 보드6은 ts_missed 30, ts_bad 4, ts_restarts 1, act_lost 726만이다. 세션을 시작할 때와 같은 값이라 이번 측정 동안 늘지 않았다.
- 해독을 같은 단위로 맞춘 값(계산, 세션 끝에 한 번 읽음): 보드6 316 kB/버퍼 4.1 ms, Jetson 462 kB/버퍼 3.5 ms.
  - kB당으로는 13.0 대 7.6 µs, 1.7배다.
  - 이벤트당으로는 24 대 8 ns, 3배다. 그러나 Jetson 장면은 바이트당 이벤트가 0.93개로 보드6(0.52개)보다 빽빽해서, 이벤트당 비교는 Jetson 쪽으로 기운다.
  - 같은 녹화로 잰 오프라인 값은 1.5배다(11.111, 아래 경합 실험에서 다시 잼).

**보드6 AI 끔(관찰, 사용자가 비밀번호를 준 뒤).**
- 방법: `sudo systemctl stop e850-dvs` 다음 `systemd-run --unit=e850-dvs --collect -E DISPLAY=:1 -p StandardOutput=append:/var/tmp/cmp/dvs-noai.log … /usr/local/bin/dvs --auto --face= --act=`로 띄웠다. 캡처 프로세스에 `dvs-act`, `dvs-bin`, `dvs-face` 스레드가 없는 것을 확인했다.
- 측정은 Wi-Fi가 끊겨도 이어지게 보드에서 분리 실행했다(`/var/tmp/cmp/noai-runs.sh`, 카메라 없음/있음 × 2).
- 끝난 뒤 원래 명령(`systemd-run --unit=e850-dvs --collect -E DISPLAY=:1 /usr/local/bin/dvs --auto`)과 `e850-open cam`으로 되돌렸다. AI 스레드 셋과 30 fps를 확인했다.
- 처음 sudo 명령을 보낸 ssh가 종료 코드 255로 끊겼다. 보드는 살아 있었고, 유닛은 떠 있었다. ping 최대 158 ms로 Wi-Fi가 잠깐 느렸다.

| 항목 | 보드6 1) DVS만(AI 끔) | 보드6 2) DVS + 카메라(AI 끔) |
|---|---:|---:|
| 캡처·해독 주 스레드 | 29.1 / 29.4 | 30.6 / 30.6 |
| `dvs-render` | 4.9 / 4.8 | 4.8 / 4.6 |
| `dvsgl` | 23.5 / 22.8 | 23.3 / 23.7 |
| mpv | — | 25.0 / 24.8 |
| X 서버 | 12.0 / 11.7 | 14.9 / 14.1 |
| 시스템 전체 | 89.2 / 88.5 | 127.2 / 171.6 |
| 프레임당 이벤트 | 43.1만~43.5만 | 43.1만~43.5만 |
| 버퍼당 해독 | 4.0~4.4 ms | 4.1~4.5 ms |
| 최신 이벤트와의 지연 | 28~30 ms | 25~30 ms |
| 30초 손실 | 모두 0 | 모두 0 |
| DVS 창 / 카메라 창 갱신 | 30.0회/s | 30.0 / 15.1, 2회차 25.0 / 15.1 |
| 카메라 프레임 나이 중앙값 / p95 | — | 72.8 / 79.2, 71.8 / 79.2 ms |
| dvsgl 자체 보고 | 30.0 frames/s, 0 lapped, 프레임당 CPU+GPU 15 ms | 같음 |

- 카메라 2회차의 전체 172%와 DVS 창 25회/s는 같은 때다. 잰 프로세스들의 합은 1회차와 같아서, 재지 않은 다른 프로세스가 그때 돌았던 것으로 본다. 무엇인지는 확인하지 못했다.
- 같은 부하(AI 없음)로 다시 보면(1회차 기준) 다음과 같다.
  - DVS만: 보드6 89%(8코어) 대 Jetson 73~74%(6코어).
  - DVS + 카메라: 127% 대 95~110%.
  - AI를 끄면 보드6의 전체가 약 1.1~1.2코어 줄었다. AI 스레드 합(121~125%)과 맞는다.
- **11.115 처음 해석의 가설 하나가 틀렸다.** "보드6 실시간 해독이 kB당 느린 것은 AI와의 메모리 경합 때문"이라고 했다. 그러나 AI를 꺼도 실시간 해독은 빨라지지 않았다. dvs-noai.log 기준 326~330 kB/버퍼에 4.3~4.5 ms로 kB당 13.4 µs이고, AI를 켰을 때는 13.0 µs였다.
  - 새 가설: 오프라인 bench는 Python이 방금 복사한(캐시에 있는) 버퍼를 해독한다. 실시간은 DMA가 쓴(캐시에 없는) 버퍼를 읽는다. 대역폭과 MLP가 작은 A55가 이 차이에 더 민감하다. 재지 않았다.

**USB 카메라가 15 fps인 것(관찰):**
- 두 보드의 카메라 창 모두 15.1회/s로 바뀐다. Jetson mpv의 `time-pos`를 200 Hz로 읽으면 프레임 간격이 64~68 ms였다. 즉 mpv는 받은 프레임을 다 그리고, 들어오는 것 자체가 15 fps다.
- mpv의 `estimated-vf-fps`는 30을 보였다. 이 경우에는 믿을 수 없는 값이다.
- USB 대역폭 때문은 아니다. descriptor를 보면 VS alt 1 하나가 마이크로프레임당 1000 B × 3 = 24 MB/s를 준다. 30 fps에 필요한 18.4 MB/s보다 많다.
  - 처음에 sysfs `wMaxPacketSize`(0x3e8)만 보고 "8 MB/s라서 30 fps가 안 된다"고 했다. 이것은 곱하기 비트를 빠뜨린 잘못이었다.
- 카메라에는 V4L2 제어 항목이 없다(`v4l2-ctl -l`이 비어 있음). 그래서 노출을 바꿀 수 없다.
- 가설: 카메라가 이 조명에서 자동 노출로 15 fps를 고르고, 한 프레임을 약 60 ms에 걸쳐 보낸다. 두 보드 모두 프레임 나이의 최솟값이 61~64 ms인 것과 맞는다. 밝게 해서 확인하지는 않았다.
- Jetson에서 mpv를 멈춘 직후 다시 띄우면 종료 코드 2로 끝나던 것(11.114): 그 시각마다 커널에 `uvcvideo 1-2.2:1.1: Failed to set UVC probe control : -75`가 있었다. 4~5초 기다리면 됐다.

**아키텍처 측정 1: archbench(관찰, 같은 바이너리).**
- E850은 보드4 CPU6에서 쟀다. 보드6과 같은 SoC이고, 클러스터1이 놀고 있었다.
- Jetson은 CPU5에서 쟀다. 클러스터1(CPU4~5)이 놀고 있었다.
- 각 값은 3번 중 가장 좋은 값이다.

| 항목 | E850 Cortex-A55 | Jetson Cortex-A78AE |
|---|---:|---:|
| 클럭(의존 덧셈 1개 = 1사이클로 잼) | 1.99 GHz | 1.34 GHz(25W 모드 상한, 하드웨어 최대 1.73) |
| 정수 덧셈/사이클(독립 사슬 1/2/4/8) | 1.00 / 1.88 / 1.78 / 1.88 | 1.00 / 2.00 / 2.96 / 3.38 |
| FP32 `fmla.4s` 처리량 | 0.97/사이클 = 7.8 flop/사이클 = 15.5 GFLOPS | 2.00/사이클 = 16 flop/사이클 = 21.4 GFLOPS |
| `fmla` 누적 사슬 지연 | 4사이클 | 2사이클 |
| 분기 예측 실패 | 10.5사이클 = 5.3 ns | 13.9사이클 = 10.4 ns |
| L1D | 32 KB, 2사이클(1.0 ns) | 64 KB, 4사이클(3.0 ns) |
| 그다음 | 96~384 KB 16~19사이클(7.9~9.4 ns), 512 KB부터 늘어남 | L2 256 KB 12~15사이클(8.7~11.4 ns), L3 2 MB/클러스터 16~18사이클(12~13.6 ns) |
| DRAM(64 MB 무작위 추적) | 138 ns(274사이클) | 236 ns(315사이클) |
| 독립 추적 K개 겹침, 192 KB(필터 비트맵 크기) | K=4에서 1.83배가 최대(4.8 ns/읽기) | K=8에서 4.79배(2.3 ns/읽기) |
| 독립 추적 K개 겹침, 64 MB | K=4에서 2.52배가 최대(57 ns/읽기) | K=8에서 7.89배(31 ns/읽기) |
| 코어 하나: 읽기 / memset / memcpy | 5.0 / 9.5 / 3.0 GB/s | 11.1 / 19.1 / 8.5 GB/s |
| 여러 코어 읽기 | 1코어 5.0, 한 클러스터 4코어 8.3, 8코어 14.9 GB/s | 1코어 11.3, 4코어 36.3, 6코어 40.9 GB/s |

- E850의 두 번째 캐시 단계 크기는 지연 곡선으로 본 값이다(약 512 KB). L2인지 클러스터 공유 L3인지는 보드에서 읽지 못했다(`lscpu -C`가 비어 있음). Jetson은 `lscpu -C`가 L1d 64K, L2 256K/코어, L3 2M/클러스터를 보였다.
- "여러 코어 읽기"는 스레드마다 3번 중 가장 좋은 값을 더했다. 그래서 동시 대역폭보다 조금 높게 나올 수 있다.

**아키텍처 측정 2: 같은 녹화를 해독할 때 옆 코어의 메모리 부하(관찰).**
- 조건:
  - `dvsbench.py`로 해독만 쟀다(필터 끔).
  - 녹화는 E850 것(5,955만 이벤트)이고, 두 보드에서 sha1이 같았다.
  - `libdvsview.so`는 같은 Docker gcc 12로 빌드했다(AI 없음).
- 해독 CPU:
  - E850은 보드4 CPU1이다(보드6의 실제 자리).
  - Jetson은 CPU5다. Jetson의 CPU1에서는 실시간 DVS가 돌아서, `DECODE_CPU 5`로 빌드한 판을 썼다.
- 옆 부하는 `archbench -m … 40`으로 걸었다.
  - "다른 클러스터"는 E850 CPU4~6(보드6 AI 자리), Jetson CPU0·2·3이다.
  - "같은 클러스터"는 E850 CPU0·2·3, Jetson CPU4(그 클러스터의 나머지 코어 하나)다.
- 두 차례 번갈아 쟀다.

| 옆 부하 | E850 해독 ns/이벤트 | Jetson 해독 ns/이벤트 |
|---|---:|---:|
| 없음 | 15.73 / 15.47(2.98~3.03 ms/버퍼) | 10.26 / 10.27(1.98 ms/버퍼) |
| 다른 클러스터 3코어 읽기 | 16.39 / 17.45(+4~13%), 부하 7.2 GB/s | 10.84 / 10.45(+2~6%), 부하 18.1 GB/s |
| 같은 클러스터 | 3코어: 18.20 / 18.47(+16~19%), 부하 6.7 GB/s | 1코어: 10.46 / 10.35(+1~2%), 부하 11.1 GB/s |
| 같은 실행의 CPU 렌더러 프레임당 그리기(`draw`) | 7.8~9.2 ms, 부하 때 11.0~14.0 ms | 1.8~2.0 ms, 부하 때 1.9~2.8 ms |

- 11.111 값을 다시 얻었다. E850은 15.21에서 15.5~15.7, Jetson은 10.18에서 10.26이다.
- E850 부하 실행 넷 중 셋에서 CPU1 클럭이 1846 MHz였다(끝날 때 읽은 값).
  - 보드4의 philip은 uclamp를 쓸 수 없다(`uclamp: Operation not permitted`). 그래서 클러스터가 바쁘면 클럭이 내려간 것으로 본다.
  - 사이클로 바꾸면 증가분은 다른 클러스터 +3~5%, 같은 클러스터 +8~9%다(계산).
- Jetson의 `decode_cpu_mhz` 칸은 cpu1의 클럭이다. 해독 CPU(5)의 클럭이 아니다.

**아키텍처 측정 3: 같은 AI 모델(관찰).**
- `aibench.py`로 쟀다. onnxruntime 1.31.1 PyPI 휠을 두 보드에 같은 판으로 썼고, 모델 파일도 sha1이 같다. 1스레드에 코어 하나, `ORT_ENABLE_ALL`이고, 30번 돌렸다.
- Jetson에는 onnxruntime이 없었다. 그래서 휠을 PyPI JSON의 sha256으로 확인하고 `~/cmp/ortpkg`에 풀어 `PYTHONPATH`로만 썼다. 시스템에는 설치하지 않았다.

| 모델 | E850(보드4 CPU6, 2.0 GHz) | Jetson(CPU5, 1.34 GHz) | Jetson이 빠른 배 | 클럭당(계산) |
|---|---:|---:|---:|---:|
| 행동 분류(x 1×2×120×160 + h 1×128) | 중앙값 26.8 ms(최소 26.5) | 7.8 ms(7.7) | 3.4배 | 5.1배 |
| 얼굴 YuNet(1×3×384×480) | 112.1 ms(110.0) | 26.9 ms(26.7) | 4.2배 | 6.2배 |

- E850 값은 11.112(보드4 CPU5: 26.8, 110.9 ms)와 같다.
- Jetson이 보드6과 같은 빈도로 돌린다고 하자(계산). 분류는 60 ms마다, 얼굴은 200 ms 쉬고 한 번이다.
  - Jetson: 모델 계산만 분류 16.7회/s × 7.8 ms ≈ 13%, 얼굴 약 4.4회/s × 26.9 ms ≈ 12%다. 합쳐서 코어의 약 25%다.
  - 보드6(같은 셈): 분류 45%, 얼굴 약 35%다.

**8코어의 장점(관찰).** 사용자: "8코어라서 장점이 있겠지".
- 방법: 모든 코어에 `aibench.py`를 하나씩 동시에 띄웠다(`tools/board/allcore.sh`). 초당 추론 수 = 코어별 1000/중앙값의 합이다.
- 배경 부하: 보드4는 현미경 뷰어와 Chrome(약 1코어)이 돌았다. Jetson은 실시간 DVS(약 0.7코어)가 돌았다.

| 모델 | E850 코어 1개 → 8개 | Jetson 코어 1개 → 6개 | 칩 전체 Jetson/E850 |
|---|---:|---:|---:|
| 행동 분류 | 37 → 207회/s(5.6배) | 128 → 626회/s(4.9배) | 3.0배 |
| 얼굴 | 9.1 → 51회/s(5.6배) | 37 → 175회/s(4.7배) | 3.4배 |

- E850은 전 코어 부하에서 여러 코어가 1846 MHz였다(끝날 때 읽음, 클러스터 전력 한도로 봄, 가설).
- 전력: Jetson `VDD_IN`(INA3221, 사용자 권한으로 읽힘)은 평소(실시간 DVS + 데스크톱 + 카메라) 6.6 W였다. 전 코어 추론 때는 중앙값 8.2 W, 최대 10.2 W이고, `VDD_CPU_GPU_CV`는 중앙값 2.2 W였다. E850은 전력 센서가 없어 N/A다(외부 측정기가 있어야 한다).
- 정리: 8코어는 코어당 차이(3.4~4.2배)를 칩 전체로 3.0~3.4배로 줄인다. 뒤집지는 못한다. 장점은 처리량보다 다음 두 가지다.
  - 일을 코어마다 고정해 서로 방해하지 않게 하는 것. 지금 보드6이 해독 CPU1, 렌더 2, 필터 3, AI 4~6, 웹 7로 나눠 쓴다.
  - (측정하지 못했지만) 작은 코어의 전력이다. 이것은 외부 전력계로 같은 지점을 재야 비교할 수 있다.

**Arm 버전에 따른 비교(관찰).** 사용자: "암버전에 차이에 따른 성능 비교평가도 하고".
- 두 코어 모두 Armv8.2-A 계열이다. 커널이 보이는 기능(`/proc/cpuinfo` Features)은 다르다.

| | E850 Cortex-A55 r0p1(0xd05) | Jetson Cortex-A78AE r0p1(0xd42) |
|---|---|---|
| v8.0 | fp asimd aes pmull sha1 sha2 crc32 | 같음 |
| v8.1 | atomics(LSE) asimdrdm | 같음 |
| v8.2 | fphp asimdhp(FP16) dcpop | 같음 + **asimddp(DotProd)** |
| v8.3 | lrcpc | lrcpc + **paca pacg(포인터 인증)** |
| v8.4 | 없음 | **uscat ilrcpc flagm** |

- 명령별 처리량(`archbench CPU isa`, 같은 바이너리, 관찰):

| 명령(도입 버전) | E850 A55 | Jetson A78AE |
|---|---:|---:|
| FP32 `fmla.4s`(v8.0) | 8 flop/사이클 | 16 flop/사이클 |
| FP16 `fmla.8h`(v8.2 FP16) | 16 flop/사이클(FP32의 2배) | 32 flop/사이클(2배) |
| int8 `sdot`(v8.2 DotProd) | **SIGILL, 이 코어에는 없음** | 64 int8 연산/사이클 |
| 원자 덧셈 `ldadd`(v8.1 LSE), 의존 사슬 | 39.5사이클 | 13.0사이클 |
| 같은 일을 `ldxr/stxr`(v8.0)로 | 15.0사이클 | 15.0사이클 |

  - E850 A55에서는 경쟁 없는 한 스레드 원자 덧셈이 LSE가 옛 방식보다 2.6배 느렸다. 여러 코어가 다툴 때의 비교는 하지 않았다.
- 같은 모델의 정밀도별 시간(코어 하나, onnxruntime 1.31.1, 중앙값 ms, 관찰):
  - 변환은 Mac의 버리는 컨테이너(`python:3.11-slim` arm64 + onnx, onnxconverter-common)에서 `tools/board/onnxvariants.py`로 했다.
  - FP16: `keep_io_types`, GRU·Gemm·Softmax·MaxPool·Resize는 FP32로 둠.
  - int8: QDQ 정적 양자화, Conv·MatMul만, 채널별, 랜덤 입력 8개로 보정. 얼굴 모델은 opset 11이라 채널별 DequantizeLinear를 못 받아서 opset 13으로 올린 뒤 했다.
  - **정확도는 보지 않았다(속도만).**

| 모델 | E850 FP32 / FP16 / int8 | Jetson FP32 / FP16 / int8 |
|---|---|---|
| 행동 분류 | 26.9 / 21.9(1.23배) / 16.6(1.62배) | 7.8 / 7.2(1.08배) / 4.6(1.70배) |
| 얼굴 YuNet | 109.7 / 130.7(0.84배) / 104.0(1.05배) | 26.8 / 34.4(0.78배) / 31.6(0.85배) |

  - int8은 분류에서 두 코어 모두 1.6~1.7배 빨라졌다. Jetson에만 있는 DotProd가 이 모델에서는 큰 차이를 만들지 않았다. 어느 커널이 쓰였는지는 프로파일로 확인하지 않았다.
  - 얼굴 모델은 FP16·int8 모두 FP32로 남긴 연산 앞뒤의 변환(Cast, Q/DQ) 때문에 느리거나 비슷하다(가설).
- 컴파일 대상(같은 dvsview.c, 같은 E850 녹화, `dvsbench.py`, ns/이벤트, 관찰):
  - 대상: v8.0 기본(`-O2`, 실행 때 고르는 outline 원자 함수), v8.2(`-march=armv8.2-a+fp16+rcpc`, LSE 명령 37개를 직접 넣음), 그리고 v8.2에 `-mtune=cortex-a55` 또는 `cortex-a78`.
  - 컴파일러는 이 코드에 DotProd나 포인터 인증 명령을 쓰지 않았다(objdump).

| 대상 | E850 해독 / 필터 켬 때 해독 / 필터 | Jetson 해독 / 필터 켬 때 해독 / 필터 |
|---|---|---|
| v8.0 | 15.39 / 25.18 / 69.7 | 10.31 / 10.58 / 16.6 |
| v8.2 | 15.62 / 24.02 / 67.1 | 10.31 / 10.82 / 16.3 |
| v8.2 + A55 튜닝 | 15.58 / 25.17 / 67.6 | 10.24 / 10.71 / 16.4 |
| v8.2 + A78 튜닝 | 15.34 / 25.41 / 69.3 | 10.27 / 10.74 / 16.2 |

  - 차이는 ±2~4%로 측정 잡음 수준이다.
  - Jetson 라이브러리는 `DECODE_CPU 5`, 필터와 렌더를 CPU4에 둔 판이다.
- 정리:
  - Arm 버전(ISA)이 바꾸는 것은 특정 명령의 유무와 폭이다(FP16 2배, DotProd, LSE).
  - 이 DVS 코드와 이 모델들에서는 그 효과가 작다(컴파일 대상 ±4%, FP16·int8 0.8~1.7배).
  - 두 보드의 차이(1.5~4.2배)는 같은 v8.2 안의 마이크로아키텍처에서 온다: in-order 대 out-of-order, 발행 폭, MLP, 캐시, 대역폭.

**해석: 아키텍처 차원에서 왜 이렇게 나오나(측정과 가설을 나눠 적음).**
1. DVS 해독. 같은 데이터에서 Jetson이 1.5배 빠르고, 클럭당 2.2배다.
   - 측정: A78AE는 out-of-order라 독립 정수 연산을 사이클당 3~3.4개 낸다. A55는 in-order dual-issue라 1.9개다.
   - 해독은 워드마다 비트 연산과 링 저장이 많아서 이 폭 차이가 드러난다.
   - 반대로 분기 예측 실패는 A55가 사이클(10.5 대 13.9)로도 시간(5.3 대 10.4 ns)으로도 싸다.
   - 가설: 데이터에 따라 갈리는 분기가 많아서, 해독 차이(클럭당 2.2배)가 폭 차이만큼 벌어지지 않는다.
2. 잡음 필터. Jetson이 3.7배 빠르고, 클럭당 5.5배다(11.111).
   - 측정: 작업 집합은 비트맵 둘(약 175 KB)이고, 이벤트마다 이웃 열 6곳을 읽는다(`filter()`).
   - A55: 이 집합이 L1(32 KB) 밖이라 다음 단계(8~9 ns)로 간다. 독립 읽기는 1.8배까지만 겹친다.
   - A78AE: 집합이 L2(256 KB) 안에 들고, 4.8배까지 겹친다.
   - 192 KB에서 독립 읽기 한 번의 실효 비용이 4.8 대 2.3 ns다. 여기에 정수 폭 차이가 더해져 3.7배가 된다.
   - 한 번에 하나씩만 쫓는 경우는 E850이 오히려 빠르다(192 KB 8.7 대 11.2 ns, DRAM 138 대 236 ns). 실제 코드처럼 읽기가 서로 독립이면 MLP가 큰 A78AE가 이긴다.
3. AI(onnxruntime FP32 합성곱). Jetson이 3.4~4.2배 빠르고, 클럭당 5~6배다.
   - 측정: FMA 최고치는 A55 8 flop/사이클, A78AE 16 flop/사이클이다. 실제 클럭을 넣으면 15.5 대 21.4 GFLOPS로 1.4배뿐이다.
   - 가설: 차이의 나머지는 A55가 in-order라 합성곱 커널의 load, 주소 계산, FMA를 겹치지 못해 최고치에 한참 못 미치는 데서 온다. 작은 캐시(L1 32 KB, 둘째 단계 ~512 KB)와 낮은 대역폭(5.0 대 11.1 GB/s)도 거든다.
   - 모델의 FLOP을 세지 않았으므로 효율(%)은 계산하지 않았다.
4. 카메라 뷰어. mpv가 2.3배, YUYV 변환이 3.0배 차이다.
   - 두 보드가 같은 CPU 변환(640×480 15 fps)을 한다. 따라서 이 차이는 코어 하나의 SIMD 처리와 메모리 대역폭 차이다(memcpy 3.0 대 8.5 GB/s, 측정).
   - GPU에 넘기는 `vo` 스레드는 3.8 대 3.1%로 비슷하다. 이 mpv에서는 GPU 드라이버의 CPU 비용이 크지 않다.
5. GPU DVS 창(`dvsgl`). 22.5% 대 6%다.
   - 보드6이 이벤트가 Jetson의 약 1/3인데도 CPU를 3.7배 쓴다.
   - 가설: 보드6은 Mesa panfrost + kbase shim이다. 프레임마다 이벤트를 GPU 버퍼로 올리고, shim이 CPU 캐시를 clean한다(11.82, 11.88). 이 업로드와 clean이 대부분이다. 따로 재지 않았다.
   - X 쪽은 보드6 Xorg(glamor) 12~15%, Jetson Xorg 2~3% + gnome-shell 7~17%다. 합치면 비슷하다.
6. 메모리 경합.
   - 측정: E850은 SoC 전체 읽기가 14.9 GB/s(한 클러스터 8.3)다. 옆 코어가 그 반쯤을 쓰면 해독이 4~19% 느려진다(일부는 클럭이 내려간 탓).
   - Jetson은 41 GB/s라서 1~6%다.
   - 보드6 실시간 해독이 kB당 Jetson의 1.7배인 것(오프라인 1.5배)을 처음에는 AI와의 경합 탓으로 보았다. AI를 꺼도 그대로여서 이 가설은 버렸다(위 "보드6 AI 끔").
7. 화면과 지연. 두 보드 모두 목표를 맞춘다(DVS 30, 카메라 15, 손실 0).
   - DVS 지연(`behind_ms`)은 22 대 26~30 ms다.
   - 카메라 프레임 나이는 보드6이 중앙값 6 ms, p95 13 ms 길다.
   - 가설: 보드6은 CPU가 바빠서(8코어 중 2.45코어) mpv 주 스레드와 변환이 늦게 돈다. 카메라 앞 60 ms는 두 보드 공통(카메라)이다.

**정리:**
- 같은 일을 코어 하나로 하면 Jetson(1.34 GHz)이 E850(2.0 GHz)보다 해독 1.5배, 필터 3.7배, AI 3.4~4.2배, 카메라 변환 3배 빠르다.
- 차이는 클럭이 아니라 코어 구조에서 온다: out-of-order와 그 발행 폭, 독립 메모리 읽기 겹침, FMA 처리량, 캐시 크기, 메모리 대역폭.
- 두 보드 모두 지금 실시간 목표는 맞춘다. 남는 여유가 다르다.
  - 보드6은 AI를 빼도 DVS와 카메라에 약 1.2코어(8코어 중)를 쓴다.
  - Jetson은 1.0코어 안팎(6코어 중)을 쓰고, 그중 GNOME 합성기 몫이 크다.
  - AI까지 넣으면 E850은 2.45코어, Jetson은 계산으로 약 1.3코어다.
- Jetson을 MAXN_SUPER(1.73 GHz)로 올리면 CPU 일은 클럭만큼 더 빨라질 것이다(계산, 재지 않음).

**하지 못한 것:**
- (했음) 보드6에서 AI를 끈 실시간 비교는 사용자가 비밀번호를 준 뒤에 했다(위).
- 모델 변형의 정확도, int8 커널 종류(프로파일).
- E850 전력(센서 없음).
- 빛이 화면에 나오기까지의 카메라 지연, 카메라를 밝게 했을 때 30 fps가 되는지.
- PMU 카운터(IPC, 캐시 미스). CLAUDE.md가 E850 PMU 실험을 사용자가 있을 때만 하라고 하므로 하지 않았다.
- Jetson MAXN_SUPER.

**보드에 둔 것(바꾼 것):**
- 보드6:
  - `/var/tmp/cmp/{thrcpu.sh,cmpload.sh,viewstat.py}`.
  - DVS와 AI는 건드리지 않았다.
  - mpv는 측정하는 동안 두 번 다시 띄웠다. 끝에 `e850-open cam`으로 되돌렸다(640×480 창, 새 PID).
- 보드6(추가): `/var/tmp/cmp/{noai-runs.sh,noai-runs.txt,dvs-noai.log}`. DVS를 AI 없이 한 번 다시 띄웠다가 원래 명령으로 되돌렸다.
- 보드4: `/var/tmp/{archbench,aibench.py,dvsbench.py,libdvsview.so,e850.gz(83 MB)}`, `/var/tmp/models/`(모델 변형 6개), `/var/tmp/march/`(라이브러리 변형). 돌던 것은 건드리지 않았다.
- Jetson:
  - `~/cmp/`에 둔 것: 도구, `archbench`, `act.onnx`, `face.onnx`, `libdvsview-cpu5.so`, `e850.gz` 링크, `ortpkg/`, `ort.whl`, `models/`, `march/`.
  - `usbcam` 유닛은 원래 스크립트로 다시 띄웠다.
  - DVS는 건드리지 않았다.
- Mac: 원본 출력, 바이너리, 빌드한 라이브러리는 `~/e850-logs/archbench/`에 있다.
