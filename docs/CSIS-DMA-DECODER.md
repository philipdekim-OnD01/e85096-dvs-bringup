# E850-96 DVS: CSIS → DMA → DRAM 수신 드라이버와 이벤트 디코더

> 기준: 2026-10-27, 브랜치 `feature/csis-dma-event-decoder`. Linux 7.2.8 arm64로 **빌드 검증만** 완료했고, **실보드 수신은 아직 검증하지 않았습니다.** 아래 "보드 검증 절차"로 확인한 결과를 PR에 첨부하세요.

## 1. 전체 데이터 경로

```text
NRV RC1S (S5KRC1S, Delta-01)
  │ I2C: modules/dvs (RESET, 레지스터, NRV 설정 파일 적용, stream on/off)
  │ NRV 22-pin 캐리어 ─ 22→15 pin FPC 어댑터 ─ E850-96 CN4 (15-pin)
  │ MIPI CSI-2: 2 lane(D0/D1), VC0, DT 0x2A(RAW8), long packet 2048 B, FS/FE 사용
  ▼
Exynos850 MIPI-CSIS v1.2 (CSIS2, 2-lane) + D-PHY m0s4s4s2
  ▼  ISP 미사용
CSIS WDMA1 VC0 (packet storage, 1D) → DRAM 버퍼 8개 (링)
  ▼  /dev/e850-csis2  read() = 헤더 + 1 CSI-2 프레임
tools/dvs-decoder: dvs-capture → .dvsc 파일 → dvs-decode → CSV / PGM
```

| 구성 요소 | 파일 | 상태 |
| --- | --- | --- |
| CSIS/D-PHY/WDMA 드라이버 | [modules/csis](../modules/csis/README.md) | 신규, 빌드 검증 |
| CSIS Device Tree | [dts/exynos850-e850-96-dvs-csis.dtsi](../dts/exynos850-e850-96-dvs-csis.dtsi) | 신규, dtc 검증 (CSIS2 2-lane 기본, CSIS0 대체) |
| 센서 설정·스트림 제어 | [modules/dvs](../modules/dvs/README.md) | sysfs 추가, 빌드 검증 |
| 캡처·디코더 | [tools/dvs-decoder](../tools/dvs-decoder) | 신규, 합성 데이터 테스트 통과 |
| 보드 실행 스크립트 | [tools/dvs-session.sh](../tools/dvs-session.sh) | 신규, 문법 검사만 |

## 2. 하드웨어 연결: 22-pin → 15-pin FPC

E850-96 CN4는 15-pin FPC 커넥터이므로 NRV 22-pin 캐리어는 **22→15 pin FPC 어댑터**로 연결합니다. 15-pin에는 데이터 레인 **D0/D1 두 개**와 클록 레인만 있어, 캐리어의 D2/D3는 연결되지 않습니다. I2C/RESET 경로는 이 연결로 Minsu 연구원 [PR #1](https://github.com/philipdekim-OnD01/e85096-dvs-bringup/pull/1)에서 실보드 확인되었습니다(`0x303B=0x55`).

따라서
- 센서는 **2-lane 모드**로 출력해야 합니다. NRV 레시피 `0x3915=0x24`는 4-lane이므로 드라이버가 DT `dvs@10`의 `nrv,data-lanes = <2>`에 맞춰 `[1:0]`을 `use_2_lane`으로 바꿔 씁니다(`0x24 → 0x26`, dmesg에 기록).
- 수신은 2-lane D-PHY인 **CSIS2**를 기본으로 합니다. CN4 pin 12(15-pin 규격의 MCLK 자리)가 `CLK2`라는 기존 확인 결과와도 일치합니다. 프레임이 들어오지 않으면 DT에서 CSIS2를 끄고 CSIS0(2-lane 설정) 노드를 켜서 다시 시험합니다.
- 대역폭이 4-lane 대비 절반이므로 처음에는 저속 레시피(DELTA_SDK `calibration/settings/sensor/Delta_01_100FPS.txt`)로 시작하고, 이상 없으면 FPS를 올리세요.

## 3. 근거 자료 (공개 자료만 사용)

- **CSIS v1.2 레지스터·순서**: Linaro `android-exynos-4.14` BSP(GPL-2.0) `is-hw-csi-v1_2.[ch]`, `is-device-csi_v4.c`. Exynos850(=3830)의 IS 체인 `is-v8_20_0`은 `CSIS_V1_2`를 선택합니다.
- **D-PHY**: BSP `phy-exynos-mipi.c`의 `0x0503_0008` D-PHY 설정, `is-hw-settle-8nm-lpp.h` settle 표, PMU 격리 오프셋 0x730, SYSREG ISPPRE 리셋 레지스터 0x14520500.
- **주소/IRQ**: BSP `exynos3830-camera.dtsi` (CSIS0 0x14400000, WDMA0 0x14431000, SPI 141/144). 메인라인과 IRQ 번호 체계가 같음을 SYSMMU_IS0(SPI 164)로 확인했습니다.
- **클록**: 메인라인 7.2.8 `cmu_is`의 `CLK_GOUT_IS_CSIS0_ACLK`, `CLK_GOUT_IS_CSIS_DMA_CLK`, `CLK_GOUT_IS_SYSMMU_IS0_CLK`.
- **센서 설정**: NRV 공개 [DELTA_SDK](https://github.com/nrvcorp/DELTA_SDK) `Delta_01_*.txt` (`20:RRRR=VV` 형식, S5KRC1S).
- **2-bit 이벤트 프레임 형식**: NRV 공개 [embeddedsw](https://github.com/nrvcorp/embeddedsw) `1.Single_DVS/streaming/host` (960×720, 8 B 헤더).

## 4. 드라이버 동작 (modules/csis)

스트림 시작(`E850_DVS_IOC_STREAMON`) 순서는 BSP `csi_stream_on()`을 따릅니다.

1. CSIS 소프트웨어 리셋
2. D-PHY: PMU 격리 해제 → SYSREG 리셋 해제 → 레인별 settle/skew 설정
3. `CSIS_CMN_CTRL` 레인 수, `PHY_CMN_CTRL` 데이터 레인 enable
4. `ISP_CONFIG_CH0`: VC, data type, dual pixel mode / `ISP_RESOL_CH0`: `line-bytes`×`lines-per-frame`
5. 에러 인터럽트만 enable (FS/FE는 WDMA 인터럽트 사용)
6. WDMA: packet storage + 1D, 주소 슬롯 8개, `FCNTSEQ=0xff`, OTF format 3, PRL 경로
7. CSISX2 DMA top(SRAM split 0x1f0, MAX_MO 0x20) → shadow update → PHY clock → `CSI_EN`

WDMA `FRM_START`에서 `ACT_FRAMEPTR`로 기록 중인 슬롯을 기억하고, `FRM_END`에서 그 슬롯을 완료로 표시합니다. 사용자가 읽지 않은 슬롯이 다시 덮이면 `dropped`가 증가하고 다음 프레임에 `E850_DVS_FLAG_DROPPED`가 붙습니다. 복사 중에 같은 슬롯이 덮이면 그 프레임은 버리고 다음 프레임을 반환합니다.

## 5. 센서 제어 (modules/dvs 추가 sysfs)

`/sys/bus/i2c/drivers/dvs/<bus>-0010/` 아래:

| 속성 | 사용 예 | 설명 |
| --- | --- | --- |
| `ready` | `cat ready` | RESET 후 0x303B 쓰기/읽기 확인 완료 여부 |
| `load_settings` | `echo nrv/Delta_01_1000FPS.txt > load_settings` | `/lib/firmware` 기준 NRV 설정 파일 적용 |
| `stream` | `echo 1 > stream` | MODE_SELECT(0x0100) |
| `reg` | `echo 0x3901 > reg; cat reg` / `echo "0x3901 0x2a" > reg` | 단일 레지스터 읽기/쓰기 |

NRV 설정 파일은 마지막에 `0x0100=01`(stream on)을 쓰므로 **CSIS 수신을 먼저 시작한 뒤** 설정을 적용해야 첫 SoT를 놓치지 않습니다. `tools/dvs-session.sh`가 이 순서로 실행합니다.

## 6. 디코더 (tools/dvs-decoder)

```bash
make -C tools/dvs-decoder test          # PC(WSL2/macOS) 또는 보드에서 실행
dvs-decode capture.dvsc                 # 요약 (frame2b 960x720 가정)
dvs-decode -o events.csv --pgm out/ capture.dvsc
dvs-decode -f raw --dump out/ capture.dvsc   # 형식 분석용 원시 덤프
```

- **frame2b** (구현·테스트 완료): 8 B 헤더(타임스탬프 u32 LE, 프레임 번호 u32 LE) + 픽셀당 2 bit, 바이트 상위 비트부터. `0`=없음, `1`=ON, `2`=OFF. CSV 열은 `seq,frame_timestamp,x,y,polarity`.
- 타임스탬프 단위는 센서 값 그대로입니다. 레시피의 `TSTAMP_SUB_UNIT=125`, `TSTAMP_REF_UNIT=999`로 보아 1 µs일 가능성이 높지만 확인되지 않았습니다.
- **MGROUP** (센서 기본 MIPI 출력 형식): **비트 배치가 공개 문서에 없습니다.** 추측으로 디코더를 만들지 않았습니다. `-f raw`로 실제 페이로드를 수집하고 NRV 문서나 DVS_Viewer의 Debug → *Read 1 Buffer / Parsing* 결과와 비교해 `src/dvs_decode.c`에 디코더를 추가하는 것이 다음 과제입니다.

## 7. 보드 검증 절차 (E850-96 Linux에서)

```bash
# 1) DTB: 메인라인 7.2.8 트리에 dts/*.dts{,i}를 복사해 exynos850-e850-96-dvs.dtb 빌드 후 ESP에 적용
# 2) 모듈·도구 빌드 (실행 중인 커널과 같은 트리)
make -C modules/dvs  KDIR=... KERNEL_SOURCE=... CROSS_COMPILE=...
make -C modules/csis KDIR=... KERNEL_SOURCE=... CROSS_COMPILE=...
make -C tools/dvs-decoder CC=aarch64-linux-gnu-gcc
# 3) 보드에서
sudo tools/dvs-session.sh Delta_01_100FPS.txt /tmp/cap.dvsc 300
sudo cat /sys/kernel/debug/e850-csis2/stats
```

| 확인 항목 | 기대 결과 | 실패 시 점검 |
| --- | --- | --- |
| `insmod e850-csis-dma.ko` | `CSIS0 version 0x...` (0이 아님) | 0 또는 행(hang): IS 전원 도메인/클록 |
| `regs`의 `PHY_STATUS` | STOPSTATE 비트 변화 | 레인 매핑, CN4 배선, PMU 격리 |
| `stats`의 `crc_ecc`, `lost_fs` | 0 또는 낮음 | `lane_mbps=`/`settle=` 변경 |
| `frames` 증가 | 설정 FPS와 비슷 | VC/DT, `ISP_CONFIG_CH0`, WDMA 슬롯 |
| `resol_mismatch` | 0 | `nrv,lines-per-frame`를 실제 패킷 수로 |

- 레인 속도는 센서 PLL(레시피 `0x30a5..0x30a7`)로 결정됩니다. 정확한 값을 모르면 `modprobe e850-csis-dma settle=N`으로 N을 바꿔가며 `crc_ecc`가 0이 되는 범위를 찾으세요.
- dmesg에 `0x3915=0x24 -> 0x26 for 2 lane(s)`가 보여야 센서가 2-lane으로 설정된 것입니다.
- CSIS2에서 `frames`가 0이면 CSIS0 노드로 바꿔 시험하고, 결과(어느 포트에서 수신되는지)를 PR에 기록하세요.

## 8. 알려진 한계와 남은 과제

1. CN4 ↔ CSIS 포트 매핑 미확인 (CSIS2 가정, CSIS0 대체 노드 제공). 22→15 어댑터 핀 매핑은 RESET/I2C까지만 실보드 확인.
2. 메인라인에는 IS 전원 도메인 노드가 없어, 부트로더 이후 IS 블록이 켜져 있다고 가정합니다.
3. SYSMMU_IS0는 bypass로 사용합니다(`iommus` 없음, 32-bit DMA 주소).
4. `read()`는 coherent 버퍼를 복사합니다. 2000 FPS 이상이면 mmap/V4L2 vb2 전환이 필요할 수 있습니다.
5. MGROUP 디코더 미구현 (형식 비공개).
