# E850-96 + OV5647: Chip ID 판독, CN4 신호 대응, Android 10 백업 — 원시 기록

기록일: 2026-10-31 (KST)  
작업 환경: macOS + ADB, E850-96 Android 10 (Linux `4.14.113-07642-gb9bd6460f267`), CN4에 OV5647 모듈 연결  
목적: [진행 계획 문서](E85096-OV5647-VENDOR510-DEBIAN-PLAN.md)에서 "후속 보고, 원시 로그 없음"으로 남아 있던 항목의 **명령과 실제 출력**을 남겨, 학생이 같은 순서로 재현할 수 있게 한다.

아래 출력은 보드에서 실제로 받은 내용을 그대로 옮긴 것이다. 장치 일련번호는 지웠다.

---

## 1. CN4가 어느 신호에 연결되는가 (회로도)

`E850-96_Circuits_v1.0.pdf`의 CAMERA 시트(CN4, 15핀, BOTTOM CONTACT)와 SoC 핀 시트를 대조한 결과다.

| CN4 핀 | 회로도 넷 | SoC 핀 | Android 10에서 |
| --- | --- | --- | --- |
| 2/3, 5/6, 8/9 | `MIPI_CSI4LB_D0/D1/CLK` | CSI4LB (CSIS1) | — |
| 11 | `CAM_GPIO` | `XGPIO5 / GPG1[5]` | `gpio-96`, 이름 `cam-en`, out **hi** |
| 12 | `CLK2/CSI2_MCLK` | `XCAM_MCLK2 / GPC0[2]` | 핀 미점유(클록 출력 없음) |
| 13 / 14 | `I2C4_SCL / I2C4_SDA` | `XHSI2C2 / GPC1[5], GPC1[4]` | `hsi2c@138C0000` → **`/dev/i2c-8`** |
| 15 | `CAM_VDD_33` | PMIC `VLDO32` (3.3 V / 300 mA) | `vdd_ldo32` **enabled, 3.3 V** |

- I²C, CAM_GPIO, MCLK 선에는 `DMG1012T` 레벨 시프터가 있어 SoC(1.8 V) 쪽과 모듈(3.3 V) 쪽이 나뉜다. **CAM_VDD_33(LDO32)이 꺼져 있으면 모듈 쪽 I²C도 동작하지 않는다.**
- 회로도의 `I2C4`는 Linux `/dev/i2c-4`가 아니다. 이 이미지에서 `/dev/i2c-4`는 `spr5801` 등이 붙은 다른 버스다.
- `/dev/i2c-7`(`hsi2c@138A0000`)에는 레퍼런스 보드용 S5K4EC 드라이버가 등록되어 있지만 CN4와는 관계없다. 카메라 앱을 열면 이 버스에서 `NO ACK`가 나는 것이 정상이다.

### 재현 명령

```sh
# 전원(LDO32)
adb shell 'for r in /sys/class/regulator/regulator.*; do n=$(cat $r/name); case "$n" in *32*) echo "$n $(cat $r/state) $(cat $r/microvolts)";; esac; done'
# CAM_GPIO
adb shell 'grep -B1 -A3 "gpg1:" /sys/kernel/debug/gpio'
# 핀 기능(I2C 모드인지)
adb shell 'grep -E "gpc1-[45]|gpc0-2|gpg1-5" /sys/kernel/debug/pinctrl/139b0000.pinctrl/pinmux-pins'
```

실제 출력:

```text
vdd_ldo32 enabled 3300000

gpiochip23: GPIOs 91-98, parent: platform/139b0000.pinctrl, gpg1:
 gpio-93  (                    |mipi_switch_hdmi    ) out hi
 gpio-96  (                    |cam-en              ) out hi

pin 96 (gpg1-5): (MUX UNCLAIMED) gpg1:96
pin 110 (gpc0-2): (MUX UNCLAIMED) (GPIO UNCLAIMED)
pin 115 (gpc1-4): 138c0000.hsi2c (GPIO UNCLAIMED) function /pinctrl@139b0000/hsi2c2-bus group gpc1-4
pin 116 (gpc1-5): 138c0000.hsi2c (GPIO UNCLAIMED) function /pinctrl@139b0000/hsi2c2-bus group gpc1-5
```

해석: 전원, 전원 켜기 신호, I²C 핀 기능이 모두 준비되어 있다. MCLK2는 나오지 않지만, 시험한 모듈은 자체 클록원(크리스털)을 가진 것으로 보이며 클록 없이도 I²C에 응답했다.

---

## 2. Chip ID `0x5647` 판독

### 2.1 왜 별도 도구가 필요한가

```sh
adb shell 'i2cget --help'
```

```text
usage: i2cget [-fy] BUS CHIP ADDR
```

toybox `i2cget`은 8비트 레지스터 주소 하나만 받는다. OV5647의 Chip ID는 16비트 주소 `0x300A`, `0x300B`에 있고 `i2ctransfer`는 설치되어 있지 않다. 그래서 [`tools/ov5647-i2crd`](../tools/ov5647-i2crd/README.md)를 만들었다.

### 2.2 실행

```bash
tools/ov5647-i2crd/build.sh                         # macOS에서 빌드(또는 저장소의 i2crd 사용)
adb push tools/ov5647-i2crd/i2crd /data/local/tmp/i2crd
adb shell chmod 755 /data/local/tmp/i2crd
adb shell '/data/local/tmp/i2crd /dev/i2c-8 0x36 0x300a 2'
```

실제 출력:

```text
56 47
```

`0x300A = 0x56`, `0x300B = 0x47` → **Chip ID `0x5647`, OV5647 확정.**

같은 시각에 주소 탐지도 다시 확인했다.

```sh
adb shell 'i2cdetect -y 8 0x36 0x36; echo ---; /data/local/tmp/i2crd /dev/i2c-8 0x36 0x300a 2'
```

```text
     0  1  2  3  4  5  6  7  8  9  a  b  c  d  e  f
00:
10:
20:
30:                   36
40:
...
---
56 47
```

### 2.3 시행착오 (따라 할 때 주의)

| 증상 | 원인 | 대처 |
| --- | --- | --- |
| `i2cdetect -y -r 7 ...`가 아무것도 출력하지 않음 | toybox는 `-y -r`처럼 옵션을 나눠 쓰면 `Unknown option r`로 실패한다. 뒤에 `| tail` 등을 붙이면 오류가 가려진다 | 옵션을 붙여 쓴다(`-yr`), 오류 출력을 숨기지 않는다 |
| 버스 7 전체 스캔이 2분 넘게 멈춤, dmesg에 `hsi2c 138a0000: tx timeout` | 카메라가 닫혀 있으면 버스 7 핀이 I²C 모드가 아니다 | CN4는 버스 7이 아니다. 버스 8을 쓴다 |
| `i2cset -y 8 0x36 0x30 0x0a` → `mode too long` | toybox `i2cset`은 마지막 인자를 모드(`b`/`w`/`i`)로 요구한다 | `i2cset -y 8 0x36 0x30 0x0a b` (단, 이것으로도 16비트 읽기는 안 된다) |
| `i2crd`가 `ERR 14` | 이 커널에서 `I2C_RDWR` ioctl이 `EFAULT` | 현재 `i2crd`는 자동으로 write+read 방식으로 다시 시도한다 |

---

## 3. 보드 상태 기록 (Android 10)

```sh
adb shell 'zcat /proc/config.gz | grep -E "VIDEO_EXYNOS_PABLO_ISP|CAMERA_CIS_4EC|CAMERA_CIS_3L6"'
adb shell 'for v in /sys/class/video4linux/*; do echo $(basename $v) $(cat $v/name); done | grep ssdevice'
adb shell 'ls /vendor/firmware | grep -iE "setfile|is_"'
```

```text
CONFIG_VIDEO_EXYNOS_PABLO_ISP=y
CONFIG_CAMERA_CIS_4EC_OBJ=y
# CONFIG_CAMERA_CIS_3L6_OBJ is not set

video214 exynos-is-ssdevice_idvc0
video215 exynos-is-ssdevice_idvc1
video216 exynos-is-ssdevice_idvc2
video217 exynos-is-ssdevice_idvc3

is_lib.bin
is_rta.bin
setfile_3l6.bin
setfile_4ec.bin
```

- 카메라 드라이버는 커널에 **내장**(`=y`)되어 있다. OV5647용 CIS 드라이버와 setfile은 없다.
- 이 4.14 커널에 5.10용 `.ko`는 올릴 수 없다. 그래서 [A안(벤더 5.10 + Debian)](E85096-OV5647-VENDOR510-DEBIAN-PLAN.md)으로 간다.

---

## 4. Android 10 백업과 두 가지 장애

백업 위치(Mac): `~/Desktop/E850_Android10_backup_20261031/`  
방법: 파티션마다 `adb exec-out "dd if=/dev/block/mmcblk0pN bs=1M" | gzip -1 > mmcblk0pN.img.gz`. userdata(p24, 54 GB)는 제외.

### 4.1 장애 1 — `mmcblk0boot0` 읽기에서 커널 Oops, 재부팅

`mmcblk0boot0`을 `dd`로 읽자 ADB가 끊기고 보드가 fastboot(`Exynos3830 LK Bootloader`)로 넘어갔다. `fastboot reboot` 후 이전 부팅의 커널 로그(`/sys/fs/pstore/dmesg-ramoops-0`)에 다음이 남아 있었다.

```text
<1>[ 1296.404315]  [6:   mmcqd/0boot0: 1763] Unable to handle kernel paging request at virtual address 6b6b6b6b6b6bb5
<0>[ 1296.442608]  [6:   mmcqd/0boot0: 1763] Internal error: Oops: 96000004 [#1] PREEMPT SMP
<4>[ 1297.859054]  [6:   mmcqd/0boot0: 1763] [<000000002629adfc>] blk_rq_map_sg+0x38/0x3e4
<4>[ 1297.866781]  [6:   mmcqd/0boot0: 1763] [<000000003968191d>] mmc_queue_map_sg+0x34/0x3c
<4>[ 1297.874680]  [6:   mmcqd/0boot0: 1763] [<00000000b5f526ba>] mmc_blk_rw_rq_prep+0x1d0/0x344
<4>[ 1297.882925]  [6:   mmcqd/0boot0: 1763] [<00000000470b08e3>] mmc_blk_issue_rw_rq+0xd8/0x4d4
<4>[ 1297.891172]  [6:   mmcqd/0boot0: 1763] [<00000000ec343105>] mmc_blk_issue_rq+0x208/0x588
<4>[ 1297.899245]  [6:   mmcqd/0boot0: 1763] [<0000000008ef6334>] mmc_queue_thread+0x128/0x1a4
<4>[ 1298.633815]  [2:   mmcqd/0boot0: 1763] ---[ end trace 24eaff4301d06b0d ]---
```

해석: `0x6b6b…`는 해제된 메모리에 채워지는 표식(slab poison)이다. `mmcblk0boot0` 요청을 처리하던 MMC 큐 스레드가 이미 해제된 메모리를 따라가다 죽었다(use-after-free). **이 4.14 커널에서는 `mmcblk0boot0/boot1`을 읽지 않는다.** 이 영역(부트로더)은 백업에서 빠졌으므로, 이 백업은 eMMC 전체 이미지가 아니다.

### 4.2 장애 2 — 큰 파티션을 읽는 중 ADB가 반복해서 끊김 (보드 문제 아님)

`super`(p12, 3.6 GB)를 받는 중 ADB가 두 번 끊겼다. 커널 Oops는 새로 남지 않았다. 원인은 Mac 쪽이었다.

```bash
/usr/bin/log show --last 5m --predicate 'eventMessage CONTAINS[c] "exynos"'
```

```text
kernel: (IOUSBHostFamily) ChatGPT@(null): AppleUSBHostUserClient::openGated: failed to open Exynos@01140000:
        provider is already opened for exclusive access by pid 723, Google Chrome
```

Chrome이 Exynos USB 장치를 독점으로 잡고 있었고, ChatGPT 앱도 같은 장치를 계속 열려고 했다. 두 앱을 완전히 종료(⌘Q)하고 `adb kill-server && adb start-server` 후 다시 받자 끊기지 않았다.

> macOS에서 ADB, fastboot, USB 시리얼이 이유 없이 끊기면 먼저 **Chrome(WebUSB/WebADB 탭)과 ChatGPT 앱**을 종료한다. 확인 명령은 위 `log show`다. zsh에서는 `log`가 셸 내장 명령이라 반드시 `/usr/bin/log`로 쓴다.

부수 사항: 부팅 직후(약 230초 시점)에도 Android가 USB 설정을 다시 하면서 `adbd`가 재시작되어 한 번 끊긴다. `adb shell getprop sys.boot_completed`가 `1`이 된 뒤 시작한다.

### 4.3 결과 — 완료, 검증 통과 (2026-10-31 00:43 KST)

Chrome·ChatGPT를 종료한 뒤 `super`부터 다시 받아 끝까지 완료했다.

| 파티션 | 이름 | 원본 크기(B) | 압축해제 크기 일치 |
| --- | --- | ---: | --- |
| p1 | efs | 20,971,520 | OK |
| p5 | dtbo | 1,048,576 | OK |
| p12 | super | 3,774,873,600 | OK |
| p13 | cache | 314,572,800 | OK |
| p14 | modem | 104,857,600 | OK |
| p15 | boot | 104,857,600 | OK |
| p16 | persist | 31,457,280 | OK |
| p17 | recovery | 41,943,040 | OK |
| p18 | misc | 41,943,040 | OK |
| p19 | mnv | 20,971,520 | OK |
| p20 | frp | 524,288 | OK |
| p21 | vbmeta | 65,536 | OK |
| p22 | metadata | 16,777,216 | OK |
| GPT | 앞 34섹터 / 뒤 33섹터 | 17,408 / 16,896 | 두 사본 모두 `EFI PART` 서명 확인 |
| p24 | userdata | 57,950,552,064 | **제외**(사용자 데이터) |
| boot0 / boot1 | 부트로더 | — | **제외**(4.1) |

- 압축 후 전체 크기 663 MB. 파일별 SHA-256은 백업 폴더의 `SHA256SUMS`에 있다.
- 검증 명령:

```bash
cd ~/Desktop/E850_Android10_backup_20261031
awk '$4 ~ /mmcblk0p/ && $4!="mmcblk0p24" {print $4, $3*1024}' partitions.txt | while read p sz; do
  g=$(gzip -l $p.img.gz 2>/dev/null | tail -1 | awk '{print $2}')
  [ "$g" = "$sz" ] && echo "$p OK" || echo "$p MISMATCH $g vs $sz"
done
xxd -s 512 -l 8 gpt_primary.bin            # 00000200: 4546 4920 5041 5254  EFI PART
xxd -s $((32*512)) -l 8 gpt_backup.bin      # 00004000: 4546 4920 5041 5254  EFI PART
shasum -a 256 -c SHA256SUMS
```

- Android 10으로 되돌릴 때(복구 절차): LK fastboot에서 `gunzip -c mmcblk0p15.img.gz > boot.img && fastboot flash boot boot.img`처럼 바꾼 파티션만 다시 쓴다. 바꾸는 파티션은 이후 문서에 매번 기록한다.

--- | --- | --- |
| p1 | efs | 완료, 원본 크기(20,971,520 B)와 압축해제 크기 일치 |
| p5 | dtbo | 완료, 원본 크기(1,048,576 B)와 일치 |
| p12 | super | 받는 중 |
| p13~p22 | cache, modem, boot, persist, recovery, misc, mnv, frp, vbmeta, metadata | 대기 |
| GPT | 앞 34섹터 / 뒤 33섹터 | 대기 |
| boot0 / boot1 | 부트로더 | **제외**(4.1 참고) |

완료 판정은 파티션마다 `gzip -l`의 압축해제 크기와 `/proc/partitions` 크기가 같은지로 한다.

```bash
cd ~/Desktop/E850_Android10_backup_20261031
awk '$4 ~ /mmcblk0p/ && $4!="mmcblk0p24" {print $4, $3*1024}' partitions.txt | while read p sz; do
  g=$(gzip -l $p.img.gz 2>/dev/null | tail -1 | awk '{print $2}')
  [ "$g" = "$sz" ] && echo "$p OK" || echo "$p MISMATCH $g vs $sz"
done
```

---

## 5. 빌드 환경 확인 (Mac Docker)

```bash
docker version --format '{{.Client.Version}} / server: {{.Server.Version}} {{.Server.Arch}}'
docker info --format 'CPUs={{.NCPU}} Mem={{.MemTotal}}'
docker run --rm e850-build-env:1 uname -m
docker run --rm -v e850-out:/o ubuntu:22.04 ls -la /o/arch/arm64/boot/Image
```

```text
28.3.2 / server: 28.3.2 arm64
CPUs=10 Mem=8218034176
x86_64          ← amd64 이미지, Apple Silicon에서 에뮬레이션으로 실행
-rwxr-xr-x 1 root root 46926148 Sep 27 00:31 /o/arch/arm64/boot/Image
```

- `docker image inspect e850-build-env:1`은 이 Docker Desktop에서 `No such image`로 실패했지만 `docker run`은 동작했다. 존재 확인은 `docker run`으로 한다.
- 남아 있는 `Image`(9월 27일)는 **이전 빌드 산출물일 뿐이며 이 보드에서 부팅해 본 적이 없다.**
- 이전 빌드 설정: `CONFIG_CC_VERSION_TEXT="Android (8508608, based on r450784e) clang version 14.0.7"`, `CONFIG_LTO_CLANG=y`. 메모리 8 GB에서 LTO 링크가 부족할 수 있다.

---

## 6. 다음 단계

1. 백업 검증 완료 확인(위 4.3).
2. 벤더 5.10 커널 + DTB를 다시 빌드하고, eMMC를 바꾸지 않는 방법(`fastboot boot` 등)으로 먼저 부팅을 시험한다.
3. Debian/Ubuntu arm64 rootfs 구성, Pablo 카메라 모듈 적재 순서 확인.
4. S5KRC1S 패치 구조를 따라 `is-cis-ov5647` + DT(`hsi2c_2`, CSIS1, `GPG1[5]`) 작성.
5. `ssdevice_idvc` 경로로 RAW10 캡처.
