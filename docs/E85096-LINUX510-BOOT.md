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
- [미확인] 11.31의 `slsi_get_qsf_mib_data: Type mismatch`와 `Failed to read regdb freq_ranges`는 부팅할 때 나온다. 교체한 뒤에는 아직 부팅하지 않았다. 다음 부팅 때 `journalctl -k -b | grep -E 'Type mismatch|regdb'`로 본다.
- [가설] 저절로 나던 0x04 Data Abort(약 3.8시간에 1번)는 2019년 펌웨어와 A13 드라이버가 맞지 않거나 펌웨어 자체 버그 때문이다. 새 펌웨어가 그것을 없앤다고 본다. 측정하지 않았다.
- 판정: 절전은 기본값(on)으로 두고 24시간(2026-11-14 00:47 KST까지) 강제 패닉 없이 둔다. 이 동안 `mx_recovery_count`가 23 그대로이면 효과가 있다고 본다(옛 빈도라면 0번일 확률 약 0.2%). 1번이라도 나면 `tools/host/wlbt-panic.py`로 R4 기록을 푼다. 이때 기호와 문자열은 새 펌웨어 것(`~/e850-logs/wifi/fw-a13/mx140/debug/common/`)을 쓴다. 확인 방법:
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
1. 2026-11-14 00:47 KST에 `mx_recovery_count`와 커널 로그를 본다(위 명령).
2. 다음 부팅 때 regdb와 `Type mismatch` 줄을 본다.
3. 새 펌웨어에서도 패닉이 나면: 그 기록을 풀고, 절전 끄기를 다시 시험한다(`wifi-pskeep`). 그다음 대역 고정(2.4/5 GHz)을 시험한다.
