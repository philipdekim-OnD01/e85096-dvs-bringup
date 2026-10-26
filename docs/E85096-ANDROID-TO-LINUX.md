# E850-96: Android → Linux 전환 및 DVS 개발 환경 구축

> 상태 기준: 2026-10-26. **E850-96 보드 자체의 OS를 Android에서 Linux로 교체**하는 절차입니다. PC의 Windows를 Ubuntu로 바꾸는 안내가 아닙니다.

## 0. 확인된 사실과 미확인 사항

| 항목 | 확인된 정보 | 한계 |
| --- | --- | --- |
| Linux 실행 | NRV 연구진이 E850-96에서 U-Boot/Linux 실행을 보고 | 모든 보드·이미지에서 재현했다고 보장하지 않음 |
| DVS I2C / RESET | Linux 7.2.8에서 `dvs.ko` 및 DTB 적용 후 `0x303B=0x55` 읽기/쓰기 검증 보고 | CHIP_ID 판독, 센서 전체 초기화, 이벤트 수신은 미검증 |
| Linux 설치 패키지 | 2026-10-06 전달된 `e850-linux-20261006-165209.tar.gz` 및 패키지 README 안내가 있음 | **이 문서 작성 시 패키지 내부 README/실행 스크립트는 검토하지 못함** |
| CSI / DMA | 수신 관련 실험이 진행됨 | 이 저장소의 `modules/dvs/`는 CSI/PHY/DMA 드라이버가 아님 |

- 소스: [DVS 커널 모듈 설명](../modules/dvs/README.md), [병합된 PR #1](https://github.com/philipdekim-OnD01/e85096-dvs-bringup/pull/1).
- 외부 공식 안내: [E850-96 U-Boot 보드 문서](https://docs.u-boot.org/en/latest/board/samsung/e850-96.html).
- 제공받은 플래싱 패키지는 공개 GitHub에 포함되어 있지 않습니다. 해당 패키지를 보유한 권한 있는 담당자에게 받아야 합니다. 기밀자료나 배포 권한 없는 바이너리는 이 저장소에 올리지 마세요.

## 1. 위험 및 준비물

**주의: 부트로더와 eMMC 파티션 변경은 기존 Android 데이터 삭제 또는 부팅 불능을 일으킬 수 있습니다.**

1. E850-96 보드, 안정적 전원, 데이터 전송 가능한 USB 케이블과 Ubuntu 64비트 작업 PC를 준비합니다.
2. UART 콘솔과 보드에 맞는 로직 전압을 확인합니다. 보드의 UART 신호에 잘못된 전압을 인가하지 마세요.
3. 원래 Android 이미지와 복구 방법을 확보하고 중요한 데이터를 백업합니다.
4. 정확히 **E850-96용**인 U-Boot, ESP, rootfs 이미지와 DTB인지 확인합니다. 일반 PC용 Ubuntu ISO를 그대로 플래싱하지 않습니다.
5. 공식 문서의 USB 복구(`SW1=1000`, `dltool`) 경로를 검토합니다. 본 저장소 `tools/`의 복구 스크립트는 PR #1 기준 문법 검사만 되었고, 실기기 실행 검증은 명시되지 않았습니다.

## 2. Ubuntu 호스트 준비

2026-10-06 연구원이 안내한 도구 목록을 Ubuntu 환경용으로 정리한 것입니다. 패키지 명칭이나 제공 여부는 사용 중인 Ubuntu 버전에 따라 다를 수 있습니다.

~~~bash
sudo apt update
sudo apt install -y \
  build-essential git cmake python3 bash \
  util-linux coreutils tar gzip flex bison \
  libssl-dev libelf-dev pkg-config \
  libusb-dev libusb-0.1-4 \
  fastboot adb dfu-util dosfstools mtools \
  e2fsprogs openssh-client mmdebstrap \
  qemu-user-static binfmt-support kmod
~~~

## 3. 제공받은 Linux 패키지의 README부터 확인

`e850-linux-20261006-165209.tar.gz`가 PC에 내려받아져 있다는 전제입니다.

~~~bash
tar -tzf e850-linux-20261006-165209.tar.gz | head -80
mkdir -p ~/e850-linux-bundle
tar -xzf e850-linux-20261006-165209.tar.gz -C ~/e850-linux-bundle
find ~/e850-linux-bundle -type f \( -iname 'README*' -o -iname '*.md' \)
~~~

**실제 빌드/플래시 실행은 압축파일 안의 README를 확인한 뒤 진행하세요.** 아래 4~5절은 **공식 U-Boot 일반 절차**이며, 제공 패키지의 스크립트를 대신하는 검증된 1:1 절차가 아닙니다.

## 4. LK(Android) → U-Boot 전환: 공식 문서상 순서

> 이 절의 쓰기 명령은 **파괴적**입니다. E850-96용 `u-boot.bin`의 출처·호환성·복구 준비가 확인된 경우에만 실행합니다.

공식 문서는 기본 LK가 장착된 E850-96에서 다음과 같이 fastboot에 진입하도록 안내합니다.

1. 보드에 전원을 연결합니다.
2. `POWER`(SW2)를 약 1초 누릅니다.
3. `VOL_UP`(SW4)를 약 7초 누릅니다.
4. Ubuntu PC에서 연결을 확인합니다.

~~~bash
fastboot devices
~~~

공식 U-Boot 전환 명령(**이미지 검증·백업·복구 준비 전에는 실행 금지**):

~~~bash
fastboot flash bootloader u-boot.bin
fastboot reboot
~~~

U-Boot 콘솔에서 기본 환경을 재설정하는 방법도 공식 문서에 나와 있습니다. 다만 기존 맞춤 환경이 사라질 수 있으므로 해당 이미지의 README와 비교한 후 적용하세요.

~~~text
env default -f -a
env save
~~~

## 5. U-Boot → Linux 시스템 이미지 기록: 공식 문서상 순서

E850-96용 Linux 파티션은 보통 `esp`(EFI System Partition)와 `rootfs` 두 개입니다. U-Boot가 정상 부팅하고 **맞는 Linux 이미지 두 개가 있는 경우에만** 아래 순서를 적용합니다.

**5.1 U-Boot 콘솔: Linux용 GPT 생성(기존 파티션 삭제 가능)**

~~~text
gpt write mmc 0 $partitions
~~~

**5.2 U-Boot 콘솔: fastboot USB 실행**

~~~text
fastboot usb 0
~~~

**5.3 Ubuntu PC: Linux 이미지 플래시**

~~~bash
fastboot devices
fastboot flash esp esp.img
fastboot flash rootfs rootfs.img
fastboot reboot
~~~

`fastboot oem format`도 공식 문서에 대안으로 있지만, 마찬가지로 파티션 테이블 변경이므로 임의로 실행하지 않습니다. 이미지 파일명·경로는 실제 패키지의 README를 우선합니다.

## 6. Linux 부팅 뒤 기본 검증

아래 명령은 **E850-96 보드에 로그인한 Linux 셸**에서 실행합니다(호스트 PC 아님).

~~~bash
uname -a
cat /etc/os-release
ls /dev/i2c-* 2>/dev/null
dmesg | grep -Ei 'dvs|i2c|csis'
find /sys/firmware/devicetree/base -type d -name 'dvs@10' 2>/dev/null
~~~

DVS `dvs.ko`는 **실행 중인 커널과 일치하는 헤더/빌드 트리**에 맞춰 빌드해야 합니다. `modules/dvs/README.md`의 방법을 참고하세요. Device Tree가 적용된 장치에서 I2C/RESET 체크가 성공했다는 대표 기록은 다음과 같습니다.

~~~text
dvs 0-0010: DVS ready: I2C response verified (0x303B=0x55)
~~~

이는 해당 레지스터의 쓰기/읽기 응답 확인일 뿐 CHIP_ID 또는 CSI-2 데이터 수신 검증이 아닙니다. 실제 I2C 어댑터 번호는 실행 중인 Device Tree/커널에 따라 달라질 수 있으므로 `0-0010`의 `0`을 모든 환경에 고정하지 마세요.

## 7. 장애 대응

- fastboot 연결 실패: USB 케이블, 호스트 권한, 해당 단계의 bootloader/USB 모드부터 확인합니다.
- 부팅 실패: 임의로 다른 보드용 이미지를 재플래시하지 말고 UART 로그와 복구 절차를 확인합니다.
- `dvs.ko` 적재 실패: `uname -r`, 모듈 빌드 대상 커널, DTB의 `compatible = "nrv,rc1s"` 및 RESET GPIO 매핑을 확인합니다.
- DVS 레지스터 응답 실패: CN4 전원/전압/배선/RESET, HSI2C2, 실행 중인 DTB를 점검합니다.
- 정리·시험 로그에는 OS/커널 버전, DTB, 이미지 버전, 수행 명령, 로그, 성공·실패 판정을 기록합니다.

## 8. 학생 개발 및 Fork

[Fork 및 PR 제출 안내](STUDENT-FORK-PR.md)를 참고하세요. 공식 Upstream은 [philipdekim-OnD01/e85096-dvs-bringup](https://github.com/philipdekim-OnD01/e85096-dvs-bringup)입니다.

학생 과제 우선순위: ① I2C/RESET 분석 ② Device Tree 확인 ③ CSIS 관련 공개 코드 분석 ④ DMA 수신 설계 ⑤ DVS 이벤트 디코딩 연계.

공개 저장소에는 공개 가능한 소스·스크립트·마스킹한 로그만 추가합니다. 외부 업체 바이너리, 비공개 센서 레지스터 정보, 인증키, 개인정보, NDA 자료는 게시하지 않습니다.
