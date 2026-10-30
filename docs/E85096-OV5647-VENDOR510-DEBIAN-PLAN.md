# E850-96 OV5647 — 진행 현황 및 벤더 5.10 + Debian 전환 계획

기록일: 2026-10-31 (KST)  
대상: Samsung Exynos850 / WinLink E850-96 / CN4 OV5647 5MP  
개발 목표: I²C 센서 확인 → MIPI CSI-2 프레임 수신 → RAW10 파일 캡처 → 이미지 복원 → 향후 DVS 연구 경로 검증

## 1. 핵심 결정

사용자는 **A안: 삼성 벤더 5.10 커널의 카메라/CSI 코드를 재사용하면서 Debian/Ubuntu ARM64 Linux로 전환**하는 방향을 선택했다.

이는 Android 13으로 업그레이드한다는 뜻이 아니다. Android 13용으로 준비된 벤더 커널 5.10 소스와 Debian/Ubuntu rootfs를 조합할 수 있을지는 **부팅 및 사용자 공간·펌웨어 의존성 검증이 필요하다**. 아직 부팅·영상 캡처 성공이 확인된 것은 아니다.

## 2. OV5647 검증 상태

| 단계 | 상태 | 근거 / 주의 |
| --- | --- | --- |
| 1. I²C 감지 (`/dev/i2c-8`, 7-bit `0x36`) | **DONE: 원시 ADB 출력 확인** | `i2cdetect -y 8 0x36 0x36` → `36` |
| 2. OV5647 Chip ID `0x5647` | **DONE: 원시 출력 확인** | `i2crd /dev/i2c-8 0x36 0x300a 2` → `56 47`. 명령·출력·도구 소스: [원시 기록](E85096-OV5647-CHIPID-RAW-LOG.md#2-chip-id-0x5647-판독) |
| 3. MIPI CSI-2 frame reception | **NOT DONE: 드라이버 경로 개발 필요** | 센서 스트리밍, MIPI PHY, CSIS/카메라 수신 드라이버와 DT 확인 |
| 4. RAW10 파일 저장 + 이미지 복원 | **NOT DONE: 수신 경로 및 처리 검증 필요** | WDMA/V4L2 또는 동등한 캡처 경로, 포맷·Bayer pattern 처리 확인 |

Chip ID 확인은 센서 식별과 I²C 레지스터 접근을 입증하지만 MIPI 영상 수신을 뜻하지 않는다. 특히 3~4단계는 센서 드라이버 하나만 추가하면 자동 완성된다는 보장이 없다. Exynos850 수신 측 드라이버·클록·전원·DMA·펌웨어/HAL 의존성을 확인해야 한다.

기존 ADB 실습 기록: [OV5647 I²C 검증 매뉴얼](E85096-OV5647-ANDROID10-I2C-BRINGUP.md).  
Chip ID 판독, CN4 신호 대응(회로도), 백업 장애 원본 로그: [원시 기록](E85096-OV5647-CHIPID-RAW-LOG.md).

## 3. 커널·빌드 환경

| 구분 | 확인된 내용 |
| --- | --- |
| 현재 보드 | Android 10 / Linux `4.14.113-07642-gb9bd6460f267` / EXYNOS3830 |
| Mac 소스 | 후속 작업자 보고: Android 13 기반 벤더 5.10, `~/e850-a13-build`, `erd3830_t_defconfig` |
| Docker | 후속 작업자 확인: Docker Desktop **28.3.2**, 실행 중, 약 10 CPU 코어 |
| 빌드 이미지 | `e850-build-env:1` (amd64), 존재한다고 보고 |
| 빌드 볼륨 | `e850-out`, 존재한다고 보고 |
| 기존 산출물 | kernel `Image`, `s5e3830.dtb`, 모듈 **139개**가 남아 있다고 보고 |
| 산출물 유효성 | **미검증**: 이미지와 소스 커밋·defconfig·펌웨어·DTB·실제 대상 보드 호환성 추가 확인 필요 |

현재 장비의 커널 4.14에 5.10 대상 `.ko`를 그대로 로드할 수 없다. 기존 커널 산출물이 존재한다는 사실은 부팅 성공의 증거가 아니다.

### Docker 상태 재확인 명령 (Mac)

```bash
docker version
docker info --format 'Server={{.ServerVersion}} Architecture={{.Architecture}}'
docker image inspect e850-build-env:1 --format 'Image={{.Id}} Architecture={{.Architecture}}'
docker volume inspect e850-out --format 'Volume={{.Name}}'
docker run --rm e850-build-env:1 sh -c 'uname -m; command -v clang; clang --version | head -1'
```

실행 중인 Docker 컨테이너가 amd64여도 ARM64용 교차 컴파일 도구를 올바르게 설정하면 대상 커널을 빌드할 수 있다. 사용 중인 크로스컴파일러와 플래그는 별도 확인해야 한다.

## 4. 백업 진행 경과와 장애 — 반드시 보존할 기록

### 4.1 최초 시도 실패

- 후속 작업자가 Android 10 보드의 eMMC 파티션, `mmcblk0boot0/boot1`, GPT 백업을 시작했다.
- 첫 시도에서 ADB 연결이 끊어지고 보드가 fastboot 상태로 전환된 것으로 보고했다.
- `fastboot reboot` 후 Android로 복귀했다고 보고했다.
- `boot0` 백업이 0바이트였고, 후속 작업자는 `mmcblk0boot0` 읽기로 MMC 드라이버 use-after-free가 발생했다고 분석했다.
- **해당 커널 오류의 실제 원인과 재현성은 이 문서에 커널 oops 원본이 없으므로 독립적으로 검증되지 않았다.** 장치 이상이 없다고 단정하거나, 해당 영역을 다시 읽지 않는다.
- 추가(2026-10-31): pstore에 남은 Oops 원본(`blk_rq_map_sg`, `0x6b6b…` slab poison)을 [원시 기록 4.1](E85096-OV5647-CHIPID-RAW-LOG.md#41-장애-1--mmcblk0boot0-읽기에서-커널-oops-재부팅)에 첨부했다. 재현 시험은 하지 않았다.

### 4.2 재시도 — boot0/boot1 제외

후속 작업자는 위험이 제기된 `boot0/boot1`을 제외하고 일반 파티션 백업을 재시작했다고 보고했다.

Mac 백업 위치:

```text
~/Desktop/E850_Android10_backup_20261031/
```

10월 31일 00:28 KST에 확인된 파일 목록에서 다음이 나타났다.

| 파일 | 당시 상황 |
| --- | --- |
| `by-name.txt`, `getprop.txt`, `partitions.txt` | 존재 확인 |
| `mmcblk0p1.img.gz` | 존재, 후속 작업자에 따르면 efs 파티션 백업 |
| `mmcblk0p5.img.gz` | 존재, 후속 작업자에 따르면 dtbo 파티션 백업 |
| `mmcblk0p12.img.gz` | **작성 중**, 후속 작업자에 따르면 super 파티션 |
| `mmcblk0boot0/boot1` 이미지 | **백업 대상에서 제외** |

`mmcblk0p12.img.gz`의 용량이 10초 사이 `83,820,544 → 155,451,392` 바이트로 증가하여 그 시각에는 파일 기록이 진행 중이었다. 후속 작업자는 `super (p12)`의 원본 크기를 약 3.6 GB라고 설명했고, cache 및 기타 작은 파티션이 남아 있다고 보고했다. **이 로그만으로 전체 백업 완료나 검증을 선언할 수 없다.**

주의: 공유된 파일 크기는 파일 생성 중인 특정 시점의 스냅샷이며, 파일 존재만으로 읽기·압축·복원이 성공했다는 의미가 아니다. `boot0/boot1`이 빠진 현재 백업은 *완전한 전체 eMMC 이미지 백업이 아니다*.

## 5. 현재 상태 확인: Mac에서 실행

아래는 **읽기 전용 확인**이다. 실행 중인 백업이 끝나기 전에는 이미지를 옮기거나 이름을 변경하지 않는다.

```bash
adb devices
ls -lh ~/Desktop/E850_Android10_backup_20261031/
pgrep -fl 'adb|gzip|dd|backup'
```

백업을 실행한 셸/작업 관리자의 종료 상태(0 또는 오류), 각 파티션의 원본 크기와 압축해제 크기, 에러 메시지를 함께 기록해야 완료로 판정할 수 있다. 이미지 파일별 gzip 무결성 검사는 파일 작성이 종료된 이후에 수행한다.

```bash
cd ~/Desktop/E850_Android10_backup_20261031
for f in *.img.gz; do
  [ -f "$f" ] || continue
  gzip -t "$f" && echo "OK: $f" || echo "BAD: $f"
done
```

`gzip -t` 통과는 압축 구조 무결성 검사일 뿐, 부트로더 복구 성공이나 파티션 내용을 정확히 덤프했다는 증거는 아니다. 별도로 파티션별 크기·해시·복구 방법을 검증한다.

## 6. 플래싱 전 필수 Gate

1. **백업 완료:** 모든 계획된 일반 파티션의 덤프와 각 파일 무결성 확인. 백업 대상에서 빠진 파티션 명시.
2. **복구 가능성:** Android 복구용 원본 이미지/펌웨어·USB 다운로드 경로·UART 부팅 로그 확보, 복구 절차 검토. `boot0/boot1` 미백업의 위험을 평가.
3. **커널 소스 정합성:** 5.10 소스 커밋, 보드 설정 `erd3830_t_defconfig`, DTB, 모듈, 툴체인, 전원/클록 및 reserved-memory 검증.
4. **Debian 부팅:** rootfs를 먼저 구성하고 위험도가 낮은 방법으로 부팅 경로를 검증한 다음 eMMC 변경 여부 결정.
5. **카메라:** OV5647 I²C/ID 재확인 → 센서 전원/클록/stream-on → CSIS/PHY 및 WDMA/캡처 드라이버 → RAW10 dump → Bayer 복원.

**중단 조건:** 복구 수단이 미검증이거나 eMMC 파티션을 변경해야 하는데 원본 이미지가 불완전할 경우, 플래싱하지 않는다. 'boot0/boot1을 Linux 전환 시 건드리지 않을 것'이라는 제안도 실제 U-Boot/eMMC 부팅 경로와 플래싱 스크립트 확인 전에는 사실로 확정하지 않는다.

## 7. 참고

- [Android 10 OV5647 I²C 실측](E85096-OV5647-ANDROID10-I2C-BRINGUP.md)
- [기존 Android → Linux 전환 매뉴얼](E85096-ANDROID-TO-LINUX.md)
- [E850-96 보드 프로젝트](../README.md)
- [OV5647 Linux 공개 드라이버](https://github.com/torvalds/linux/blob/master/drivers/media/i2c/ov5647.c)
- [E850-96 U-Boot 안내](https://docs.u-boot.org/en/latest/board/samsung/e850-96.html)

기록 원칙: 후속 작업자의 성공 보고를 직접 수집한 raw 로그와 혼동하지 않으며, OS 설치가 완료되기 전까지는 기존 Android 환경의 변경을 최소화한다.
