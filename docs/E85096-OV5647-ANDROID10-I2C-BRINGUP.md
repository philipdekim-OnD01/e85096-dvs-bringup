# E850-96 + OV5647 5MP: Android 10 ADB/I²C 통신 검증 매뉴얼

문서 기준일: 2026-10-31  
실습 장비: WinLink / 96Boards E850-96 (Samsung Exynos 850, Device Tree의 Exynos3830)  
센서: OV5647 5MP 카메라 모듈, CN4 연결  
호스트: macOS, Android Debug Bridge (ADB)  
범위: 실제 사용자 로그로 확인한 I²C 주소 응답까지. 센서 Chip ID와 MIPI 영상 획득은 아직 검증하지 않았다.

> **업데이트 (2026-10-31):** 이후 작업자의 보고에 따르면 OV5647 **Chip ID `0x5647` 읽기가 완료**되었다. 이 문서 아래의 '미수행/미검증' 표기는 **당시 최초 I²C 주소 ACK 시험 시점의 역사적 기록**이다. Chip ID 판독의 원시 명령과 출력은 현재 미첨부이며, CSI-2 수신/RAW10 캡처는 여전히 미완료다. Linux 전환 A안, Docker 빌드 환경, Android 백업 재시도 현황 및 위험사항은 [후속 개발 상태 문서](E85096-OV5647-VENDOR510-DEBIAN-PLAN.md)에 기록했다.

> 결과 요약: Android 10 / Linux 4.14.113에서 카메라 쪽 HSI2C2는 `/dev/i2c-8`에 대응한다. `i2cdetect -y 8 0x36 0x36`의 결과 `36`이 출력되어, 버스 8의 7-bit 주소 `0x36`에서 I²C 탐지 트랜잭션에 대한 응답(ACK)을 확인했다. 이것만으로 OV5647의 Chip ID, 모든 센서 전원·클록 상태, MIPI/CSI-2 수신 또는 영상 프레임 획득이 입증되는 것은 아니다.

## 1. 실험 목적과 성공 기준

1. Mac에서 ADB를 통해 E850-96 Android 셸에 접속한다.
2. Android 커널에서 I²C 어댑터가 생성됐는지 확인한다.
3. CN4의 센서 제어 신호가 실제 어느 Linux I²C 버스에 대응하는지 Device Tree 경로로 확인한다.
4. OV5647의 예상 7-bit 주소 `0x36`에서 ACK를 확인한다.
5. 다음 시험에서 OV5647 Chip ID `0x5647`을 읽고, 이후 CSI-2 스트리밍을 별도로 검증한다.

이 매뉴얼은 NRV DVS/S5KRC1S 검증과 별개인 OV5647 프레임 카메라 시험이다. DVS의 주소, 제어 레지스터, RESET 신호를 OV5647에 그대로 적용하지 않는다.

## 2. 실습 환경: 사용자 실측 결과

| 항목 | 명령 또는 관측값 | 상태 |
| --- | --- | --- |
| ADB | `adb devices` → 장치가 `device` 상태 | 확인 |
| Android | `adb shell getprop ro.build.version.release` → `10` | 확인 |
| Kernel | `adb shell uname -a` → `Linux localhost 4.14.113-07642-gb9bd6460f267` (aarch64, 2023-01-20 빌드) | 확인 |
| Board model | `Samsung E850-96 board based on EXYNOS3830` | 확인 |
| CPU | AArch64 / ARM Cortex-A55 8개 | 확인 |
| 관리자 권한 | Android 셸 `id` → `uid=0(root)` | 확인 |
| I²C 어댑터 | `0, 1, 2, 3, 4, 7, 8, 9` | 확인 |
| `i2cdetect` | `/system/bin/i2cdetect` | 확인 |
| `i2ctransfer` | 찾을 수 없음 | 미설치 |
| `0x36` 응답 | 버스 8에서 `36` 출력 | 성공 |
| OV5647 Chip ID | `0x5647` 읽기 시험 | 미수행 |
| CSI-2/영상 프레임 | RAW/V4L2 캡처 | 미수행 |

장치 일련번호와 개인 식별 정보는 공개 문서에 기록하지 않는다.

## 3. 통신 경로 개념도

```text
Mac (ADB)
   │ USB 디버깅
   ▼
E850-96 Android 10 (Linux 4.14.113)
   │ /dev/i2c-8 (실제 실습에서 확인된 버스 번호)
   ▼
exynos5-i2c 드라이버
   │ Device Tree: /hsi2c@138C0000
   ▼
Exynos850 HSI2C2 컨트롤러
   │ E850-96 CN4: SDA/SCL
   ▼
0x36 주소에서 응답한 I²C 장치
   │ 센서 ID를 읽기 전까지 모델명 확정 불가
   ▼
OV5647 (연결한 것으로 알려진 목표 센서)
```

중요: 보드 문서의 `I2C4` 표기와 Linux의 `/dev/i2c-4`는 같은 번호를 뜻하지 않는다. E850-96 CN4 신호는 SoC의 `XHSI2C2`에 매핑되고, 이 실습의 Android에서는 `hsi2c@138C0000`가 `i2c-8`로 등록되었다. 다른 커널 또는 DTB에서는 버스 번호가 달라질 수 있다.

## 4. 단계별 실행 명령

아래의 `adb ...` 명령은 Mac 터미널에서 실행한다. `erd850:/ #` 프롬프트 이후 명령은 E850 보드 내부 Android 셸에서 실행한다.

### 4.1 Mac에서 ADB 연결 및 보드 정보 조회

```bash
adb devices
adb shell getprop ro.build.version.release
adb shell uname -a
adb shell 'tr "\000" "\n" < /proc/device-tree/model'
adb shell
```

실측 결과:

```text
Android version: 10
Kernel: Linux localhost 4.14.113-07642-gb9bd6460f267 ... aarch64
Board: Samsung E850-96 board based on EXYNOS3830
```

`adb shell`에 진입하면 아래의 I²C 관련 명령을 실행한다.

### 4.2 I²C 장치·어댑터 목록 조회

```sh
ls -l /dev/i2c*
ls /sys/class/i2c-adapter/
for d in /sys/class/i2c-adapter/i2c-*; do
    echo "$d"
    cat "$d/name"
done
```

실측 요약:

```text
/dev/i2c-0  /dev/i2c-1  /dev/i2c-2  /dev/i2c-3
/dev/i2c-4  /dev/i2c-7  /dev/i2c-8  /dev/i2c-9

i2c-4 : s3c2410-i2c
i2c-7 : exynos5-i2c
i2c-8 : exynos5-i2c
```

장치 노드 존재는 I²C 컨트롤러와 사용자 공간 접근 경로가 있음을 뜻할 뿐, 센서 통신의 성공을 뜻하지 않는다.

### 4.3 Device Tree와 Linux 버스 번호 대응 확인

```sh
for n in 4 7 8; do
    echo "===== i2c-$n ====="
    cat /sys/class/i2c-adapter/i2c-$n/name
    readlink -f /sys/class/i2c-adapter/i2c-$n/device/of_node
done
```

실제 출력:

```text
===== i2c-4 =====
s3c2410-i2c
/sys/firmware/devicetree/base/i2c@13870000

===== i2c-7 =====
exynos5-i2c
/sys/firmware/devicetree/base/hsi2c@138A0000

===== i2c-8 =====
exynos5-i2c
/sys/firmware/devicetree/base/hsi2c@138C0000
```

보드의 CN4 카메라 제어가 HSI2C2에 연결된다는 보드 설명과 위 실행 중 Device Tree 경로를 결합하면, 이 Android 이미지에서는 `/dev/i2c-8`을 우선 시험할 수 있다.

### 4.4 기존 센서 등록 여부·로그 확인

```sh
ls -l /sys/bus/i2c/devices/ | grep 0036
dmesg | grep -iE 'ov5647|hsi2c|camera|sensor' | tail -n 40
```

실측 결과: 해당 검색식에서 출력 없음.

해석: `0x36`에 해당하는 커널 I²C client 또는 위 문자열을 포함한 로그가 이 조회에서는 발견되지 않았다. 이 두 명령은 실제 I²C 통신을 실행하는 명령이 아니므로, 출력이 없다는 것만으로 연결 실패라고 판단할 수 없다. Android 커널의 로그 접근 정책, 초기화 시점도 결과에 영향을 줄 수 있다.

### 4.5 도구와 root 권한 확인

```sh
id
command -v i2cdetect
command -v i2ctransfer
```

실측 결과:

```text
uid=0(root) ...
/system/bin/i2cdetect
(i2ctransfer 경로 없음)
```

`su -c id` 실행 시 `su: invalid uid/gid '-c'`가 나왔으나 이미 셸이 root이므로 별도의 `su` 전환은 불필요하다. `i2ctransfer: inaccessible or not found` 역시 센서 실패가 아니라 실행파일 부재를 뜻한다.

### 4.6 주소 0x36 단일 주소 탐지 — 성공 확인

주의: 이 단계는 실제 I²C 트랜잭션을 수행한다. 대상 버스에 다른 활성 드라이버/장치가 있거나 장치 동작에 영향이 우려되면 무작정 반복하거나 `-f`로 강제하지 않는다. 반드시 실제 카메라 버스를 확인한 후 실행한다.

```sh
i2cdetect -F 8
i2cdetect -y 8 0x36 0x36
```

`-F`는 점검용 선택 명령으로, 이번 대화에서의 실측 실행 여부는 확인되지 않았다. 실제 성공이 확인된 명령은 다음 한 줄이다.

```sh
i2cdetect -y 8 0x36 0x36
```

실측 출력에서 중요한 행:

```text
     0  1  2  3  4  5  6  7  8  9  a  b  c  d  e  f
30:                   36
```

판정: 버스 8에서 7-bit 주소 `0x36`의 장치가 I²C 탐지 프로브에 응답했다. 이는 물리 연결/전원/버스의 일부가 동작하고 있다는 강한 근거지만, 동일 주소의 다른 장치 가능성과 OV5647 자체 식별은 구별해야 한다.

탐지 결과 읽는 법:

| 출력 | 의미 |
| --- | --- |
| `36` | 주소 `0x36`에 응답한 장치 발견 |
| `UU` | 커널 드라이버가 해당 주소를 점유한 것으로 보고됨 |
| `--` | 해당 탐지 방식으로 응답 확인 안 됨; 즉시 고장 판정 불가 |
| I/O error | 접근 권한·버스 설정·전원·클록·배선 등을 재확인 |

## 5. 여기까지 확인한 사실과 미완료 항목

| 점검 단계 | 상태 | 근거 |
| --- | --- | --- |
| ADB 연결 | PASS | `adb devices` |
| Android 10 / Kernel 4.14.113 확인 | PASS | `getprop`, `uname` |
| I²C 사용자 공간 장치 생성 | PASS | `/dev/i2c-8` 존재 |
| CN4 카메라 I²C 버스 후보 특정 | PASS (매핑 근거 확보) | `hsi2c@138C0000`, 보드 CN4 신호 |
| 주소 `0x36` ACK | PASS | `i2cdetect -y 8 0x36 0x36` → `36` |
| 응답 장치가 OV5647인지 확인 | NOT TESTED | CHIP_ID 읽기 필요 |
| 센서 스트리밍 초기화 | NOT TESTED | 전원·MCLK·Reset·드라이버 확인 필요 |
| MIPI CSI-2 RAW 데이터 수신 | NOT TESTED | CSIS/V4L2 영상 경로 미검증 |
| 이미지 프레임 저장 | NOT TESTED | 실제 캡처 미실시 |

이 결과는 I²C 단일 주소 응답 성공에 한정한다. `/sys/class/video4linux`에 여러 `video*` 노드가 존재해도 어떤 노드가 카메라인지 확인하기 전에는 영상 수신 성공이라고 판단하지 않는다.

## 6. 다음 검증: OV5647 Chip ID

공개 Linux OV5647 센서 드라이버는 센서 식별 레지스터를 `0x300A`부터의 16-bit 값으로 읽고, `0x5647`인지 비교한다.

- 레지스터 `0x300A` (상위 바이트): 예상 `0x56`
- 레지스터 `0x300B` (하위 바이트): 예상 `0x47`
- 합친 값: `0x5647`

`i2ctransfer` 실행 파일이 준비되고, 다른 드라이버가 해당 장치를 사용하지 않음을 확인한 뒤 예컨대 다음과 같이 읽는다.

```sh
# 아직 이 보드에서 실행/검증되지 않은 예시 명령
i2ctransfer -y 8 w2@0x36 0x30 0x0a r1
i2ctransfer -y 8 w2@0x36 0x30 0x0b r1
```

주의: `i2ctransfer`는 현재 Android 이미지에 설치되어 있지 않다. 따라서 위 두 명령은 검증된 성공 로그가 아니라 다음 시험을 위한 절차다. 센서 활성화 시퀀스와 드라이버 점유 상태를 확인하고 실행한다. 가능하면 ARM64용 작은 `I2C_RDWR` read-only 테스트 프로그램을 사용해 동일한 combined transaction을 구현할 수 있다.

Chip ID가 확인되더라도 영상 출력은 별도 단계다. 영상 시험에서는 센서 전원·MCLK·RESET/PWDN, CSI-2 2-lane 설정, CSI receiver, DMA, V4L2/RAW capture를 각각 점검한다.

## 7. 문제 해결 우선순위

1. `/dev/i2c-8`이 없어지면 먼저 실행 중인 Device Tree에서 HSI2C2 대응 버스 번호를 다시 확인한다.
2. 주소 `0x36`이 응답하지 않으면 카메라 커넥터 방향/핀 배열, 센서 전원, MCLK, RESET/PWDN 및 센서 초기화 여부를 점검한다.
3. I²C 주소는 응답하지만 ID가 다르면 실제 장치 모델, 주소 충돌, 레지스터 접근 방식 및 전원 상태를 점검한다.
4. Chip ID가 맞아도 카메라 프레임이 없으면 CSI-2/CSIS/ISP 또는 RAW 경로 문제로 분리한다.
5. 초기 I²C 검증만을 위해 Android를 지우거나 eMMC 부트로더를 교체하지 않는다. Linux 전환은 복구 도구와 펌웨어 백업을 확보한 뒤 별도 계획으로 수행한다.

## 8. 참고 자료

- [E850-96 프로젝트 README](../README.md)
- [기존 CN4 DVS I²C 상태 문서](e85096-cn4-i2c-status.md): DVS 대상이며 본 OV5647 시험과 별개
- [Android → Linux 전환 안내](E85096-ANDROID-TO-LINUX.md)
- [E850-96 공식 하드웨어 문서](https://www.96boards.org/documentation/consumer/e850-96b/hardware-docs/)
- [Linux 메인라인 OV5647 센서 드라이버](https://github.com/torvalds/linux/blob/master/drivers/media/i2c/ov5647.c)

## 9. 실험 기록 요약 (5줄)

1. Mac의 ADB로 E850-96 Android 10, Linux 4.14.113 시스템에 접속했다.
2. Linux I²C 어댑터와 `/dev/i2c-*` 장치 목록을 확인했다.
3. CN4의 HSI2C2와 Device Tree `hsi2c@138C0000`을 대조하여 `i2c-8`로 매핑했다.
4. `i2cdetect -y 8 0x36 0x36`을 실행해 화면에 `36`이 나오는 것을 관찰했다.
5. 주소 `0x36`의 ACK는 확인되었고, OV5647 Chip ID·MIPI 데이터 수신·영상 캡처는 다음 실험으로 남겼다.
