# S5KRC1S D-PHY PLL 시험 (E850-96)

처음 작성: 2026-11-12. 경과는 [E85096-LINUX510-BOOT.md](E85096-LINUX510-BOOT.md) 11.46에 있다.

배경 자료는 main 브랜치의 [DVS_PLL_MINSU_KARIM_REVIEW_2026-11-10.md](DVS_PLL_MINSU_KARIM_REVIEW_2026-11-10.md)(Minsu·Karim 검토)다. 그 문서의 Jetson 결과는 협력자가 보고한 것이고, E850에서는 재현하지 않았다.

표기는 다음과 같다.

| 표기 | 뜻 |
|---|---|
| **[확인]** | E850에서 측정했거나 파일·소스·데이터시트로 확인했다 |
| **[보고]** | 협력자가 보고했다. E850에서 재현하지 않았다 |
| **[가설]** | 측정하지 않은 추정이다 |
| **[UNKNOWN]** | 판단할 근거 자료가 없다 |

## 0. 요약

**FACT**

- [확인] 보드는 11.29부터 setfile의 0x30A5/0x30A6/0x30A7 = **00/75/00**을 쓴다. 센서에서 다시 읽은 값은 아직 없다. 읽기 코드는 썼지만 빌드하지 못했다(Docker가 꺼져 있음).
- [확인] 녹화 42개(2026-11-07~08, 0x75, margin 17, flush 50 µs, 273초, 이벤트 3.6억 개)를 `tools/host/dvslink.py`로 검사했다.
  - 모르는 워드: 0개
  - sub 타임스탬프 범위 오류: 0개
  - scan 단계 272,857개 중 4,427개(1.62%)가 정확히 scan 2개 간격이다. 즉 scan 1개가 빠졌다. 2개 이상 빠진 곳과 큰 틈은 없다.
  - frame_end 번호 274,561개 중 2,702개가 하나씩 건너뛰었다.
  - 시간이 거꾸로 간 곳 12개는 모두 비트 하나 차이다.
- [확인] 지금 live(0x75, 2026-11-11 15:37 UTC 부팅): CRC 0, 버퍼 34.9~35.0/s, coverage 99~100%다. 관찰 실행(`plltest.py setfile 0 600 --observe`)의 첫 9개 표본이다.

**CAUSE**

- [확인] 11.29의 "PLL M(0x30A6)만 바꿨다"는 틀렸다. 세 바이트를 19비트 한 단어로 보면(1장) 0x30A6의 위 3비트는 P에 들어간다. 0x68~0x7C는 P=3이고 0x80~0x8D는 P=4다.
- [확인] 0x75의 공식값은 24×672/3 = 5376 Mbps다. 데이터시트의 VCO 범위 1250~2500 MHz 밖이다. **실제 lane 속도는 모른다.** 0x75는 검증된 설정이 아니라 **실험값**이다.
- [가설] 범위 밖 단어에서는 공식값이 실제 속도가 아닐 것이다.
  - 11.29에서 같은 수신 설정(1600, settle 8)으로 0x84(공식 768)와 0x72~0x78(공식 4608~6144)이 모두 CRC 0이었다.
  - 공식값이 실제라면 한 수신 설정으로 6~8배 차이 나는 속도를 모두 받기는 어렵다.
- [가설] 1.62% scan 누락은 패킷(줄) 손실이다.
  - 녹화 하나에서 scan이 빠진 구간에는 줄 경계가 6~7개 있었다. 정상 구간은 4~5개다. scan 하나가 통째로 빠진 것이 아니라 줄 2~3개가 빠진 것으로 보인다.
  - 어디서 빠지는지(센서 송신, 링크, CSIS 수신, 버퍼 경계, 녹화 경로)는 모른다. CRC가 0인데도 빠진다.

**EVIDENCE**: 8장 표와 다음 파일.

- 녹화 검사: 보드 `/var/tmp/dvslink-rec.tsv`. 파일당 한 줄로, 녹화 정보와 `dvslink.py --json` 결과가 들어 있다.
- 줄 경계 분석: 녹화 `20261107T074001Z-none.gz` 하나만 했다. next 5,889개의 경계는 3개 60, 4개 3,239, 5개 2,587, 6개 3이었다. miss1 103개의 경계는 3개 1, 6개 12, 7개 90이었다.
- 11.29 sweep 원 기록: BOOT.md 11.29.
- 데이터시트: `S5KRC1SY_DataSheet_REV0.01_Preliminary`, 6.1절과 표 11, 29(3장).

**NEXT**: 7장 순서로 한다. 막힌 것은 두 가지다.

- Docker Desktop이 꺼져 있다. `open -a Docker`로 켜지지 않았다. 그래서 모듈과 lib을 빌드하지 못했다.
- 보드 root 경로가 없다. ACM이 응답하지 않고, UDC가 `not attached`다. sudo 비밀번호도 없다. 그래서 설치와 시험을 하지 못했다.

## 1. 레지스터 해석

[보고] Minsu에 따르면 0x30A5~0x30A7은 19비트 한 단어다. 해석은 다음과 같다.

```
word = (A5 << 16) | (A6 << 8) | A7
P = (word >> 13) & 0x3f
M = (word >> 3) & 0x3ff
S = word & 7
```

공식은 둘이다.

- Minsu: `rate = 24 × M / P` Mbps/lane. S=0인 경우만 시험했다.
- Karim: `rate_s = 24 × M / (P × 2^S)`. 실험에서 얻은 식이고, 데이터시트로 확인하지 않았다.

| 이름(`pll` 파라미터) | A5/A6/A7 | P | M | S | 공식값 Mbps | Fref MHz | VCO 범위 안? | 근거 |
|---|---|---:|---:|---:|---:|---:|---|---|
| `setfile`, `experimental_00_75_00` | 00/75/00 | 3 | 672 | 0 | 5376 | 8 | 아니오 | [확인] E850 11.29: CRC 0, 3분 |
| `recommended_00_8b_b0` | 00/8B/B0 | 4 | 374 | 0 | 2244 | 6 | 예 | [보고] Jetson 64.37시간, CRC 0, 잃은 버퍼 0 |
| `alternative_00_8b_f8` | 00/8B/F8 | 4 | 383 | 0 | 2298 | 6 | 예 | [보고] Jetson 약 5시간, CRC 2 |
| `default_00_8d_00` | 00/8D/00 | 4 | 416 | 0 | 2496 | 6 | 예 | [확인] E850 CRC 약 4800/s(11.29) |

- Fref = 24/P다. P=4의 6 MHz는 데이터시트 범위(6~12 MHz)의 아래 끝이다.
- 표의 계산은 모두 MCLK 24 MHz를 가정했다. E850에서 24 MHz는 측정한 값이 아니다(3장 UNKNOWN).

## 2. 11.29 sweep 다시 읽기

11.29에서는 0x30A6만 바꿨다. A5=00, A7=00이고, 수신은 DT 설정(1600, deskew 켬, settle 8)이었다. 결과 열은 11.29 원 기록이고, P·M·공식값 열은 이번에 계산했다.

| 0x30A6 | P | M | 공식값 | VCO 범위 안? | 11.29 결과 |
|---|---:|---:|---:|---|---|
| 0x68 | 3 | 256 | 2048 | 예 | CRC 0, 버퍼 35/s, coverage 100% |
| 0x70 | 3 | 512 | 4096 | 아니오 | 데이터 없음, CRC 1~6/s |
| 0x72, 0x74, 0x75, 0x76, 0x78 | 3 | 576~768 | 4608~6144 | 아니오 | CRC 0, coverage 99~100% |
| 0x7A | 3 | 832 | 6656 | 아니오 | CRC 5074/s, coverage 92% |
| 0x7C | 3 | 896 | 7168 | 아니오 | 데이터 없음 |
| 0x80 | 4 | 0 | 0 | 아니오 | 데이터 없음 |
| 0x84 | 4 | 128 | 768 | 아니오 | CRC 0, coverage 99~100% |
| 0x88 | 4 | 256 | 1536 | 예 | CRC 0, coverage 99~100% |
| 0x8D (vendor) | 4 | 416 | 2496 | 예 | CRC 약 4800/s |

- [확인] 0x8D와 S=1(`30a7=01`, 단어 0x08D01, 수신 800)에서는 CRC가 0이었다. 하지만 첫 프레임만 127줄이 다 왔고, 그 뒤 프레임은 첫 패킷만 들어왔다(11.29 실험 2). Karim의 식으로는 1248이다.
- [확인] 11.29의 "0x75는 원래 값의 83%, 1328 또는 1404 Mbps"라는 추정은 버린다. P가 바뀐 것을 모르고 M만 비례시켜 계산했기 때문이다.
- [가설] 0x88(공식 1536, VCO 범위 안, 수신 설정 1600과 가까움)도 E850 후보가 될 수 있다. 시험 목록에 넣지 않았다. 7장 순서가 끝난 뒤 사용자와 정한다.
- 11.29 측정은 값마다 수십 초~몇 분이었다. 0x75 말고는 다시 재지 않았다.

## 3. 데이터시트(REV0.01 Preliminary)로 확인한 것과 모르는 것

[확인] 데이터시트에 적힌 내용은 다음과 같다.

| 항목 | 데이터시트 내용 | 위치 |
|---|---|---|
| PLL 식 | `PLL_CLK = MCLK × M / (P × 2^S)` | 6.1, Figure 27 |
| S | "Post Scalar parameter S … is not controllable and equals 0" | 6.1 NOTE |
| Output PLL Fref | 6~12 MHz | 표 11 |
| Output PLL VCO | 1250~2500 MHz | 표 11 |
| Output PLL 출력 | 40~2500 MHz. 최소값은 시험용이다(6 > S ≥ 0) | 표 11 |
| MIPI 송신 최대 effective VCO | 2.5 GHz | 6.1 |
| D-PHY | v1.0, lane당 2.5 Gbps 제한. 1/2/4-lane | 개요, MIPI 절 |
| PLL lock 시간 | 표준 200, 최대 1000. 단위는 추출본에서 깨졌다(µs로 추정). stream 신호가 low→high로 바뀐 때부터 잰다 | 표 29, Figure 56 |
| EXTCLK | 6~64 MHz | 표 11, 29 |

[UNKNOWN] 다음은 근거 자료가 없다.

- **0x30A5~0x30A7의 비트 배치:** 데이터시트 레지스터 표에 이 주소가 없다. 19비트 배치는 Minsu의 보고뿐이다.
- **PLL lock·상태 레지스터:** 데이터시트에서 찾지 못했다. 그래서 다시 읽기는 쓴 값만 확인한다. 실제 PLL 주파수는 확인하지 못한다.
- **S의 동작:** 데이터시트는 "S는 0이고 바꿀 수 없다"고 하면서, 식과 표 11에는 S가 있다. E850 11.29에서 `30a7=01`은 링크 동작을 바꿨다. 데이터시트와 맞지 않는다.
- **MCLK:**
  - E850에서는 SoC가 MCLK를 주지 않는다. CN4 12번을 RSTN으로 쓰기 때문이다(BOOT.md 11.19).
  - 모듈 발진기는 확인하지 않았다. 24 MHz는 데이터시트의 최대 scan rate에서 거꾸로 계산한 값이다(BOOT.md 11.23). 측정한 값이 아니다.
  - MCLK가 다르면 1·2장의 공식값이 모두 같은 비율로 바뀐다.
- **E850 CSIS D-PHY의 최대 수신 속도, 2244/2298에서의 settle·deskew 표:** vendor 커널 소스(`is_hw_find_settle`과 PHY 표)는 Docker 볼륨 `e850-kernel` 안에만 있다. 아직 보지 못했다.
- **실제 lane 속도:** 고속 계측기가 없다. 간접 측정 방법도 아직 없다.

## 4. 수신기(CSIS) 설정

[확인] 현재 설정은 다음과 같다.

| 항목 | 값 | 확인한 곳 |
|---|---|---|
| DT | `mipi_speed` 1600, settle 8, deskew 켬, 2-lane | dmesg `[CSI1] settle(8192x127@30) = 8, speed(1600 Mbps), 2 lane` |
| 런타임 덮어쓰기 | `fimc_is.e850_csi_speed` = 0, `e850_csi_settle` = 0(0이면 DT 값) | `/sys/module/fimc_is/parameters` |
| 적용 시점 | stream on 때. settle을 정하지 않으면 `is_hw_find_settle`이 속도로 고른다 | `csi-speed-param.patch` |
| CRC 계수 | `/sys/module/fimc_is/parameters/e850_csi_crc_count`. dvslive는 `crc_keep`=1, `err_stop`=0 | |

- [보고] Minsu는 Jetson에서 수신 속도를 센서 속도에 맞췄다(DT `pix_clk_hz` 1122 MHz). E850에는 이 값을 그대로 옮기지 않는다.
- E850에서 맞출 값은 `e850_csi_speed`다. 7장 시험에서는 두 가지를 함께 시험한다. 하나는 DT 그대로(0)이고, 다른 하나는 센서 공식값(2244/2298)이다. 공식값은 PHY 표가 그 속도를 지원하는지 확인한 뒤에만 쓴다.

## 5. 코드

순서는 다음과 같다. dvslive를 다시 시작하면 stream off가 되고 센서는 standby(0x3000=0)로 들어간다. 그다음 stream의 mode change가 setfile → lane → `pll` 단어를 쓴다(센서는 아직 standby). 그 뒤 CSIS가 켜지고 stream on이 된다. 흐르는 stream에는 PLL을 쓰지 않는다.

| 파일 | 바꾼 것 | 상태(2026-11-12) |
|---|---|---|
| `modules/s5krc1s-pablo/.../is-cis-s5krc1s.c` | <ul><li>`pll` 파라미터(0644): 이름 4개 중 하나, 비우거나 `setfile`이면 setfile 그대로이고, 모르는 이름이면 `-EINVAL`</li><li>mode change와 stream on 뒤 0x30A5~A7을 읽어 dmesg와 `pll_live`(0444)에 남긴다: `"00 75 00 word 0x07500 P=3 M=672 S=0 rate=5376 rate_s=5376"`, "계산값, 측정 아님"</li></ul> | 작성만 했다. **빌드 안 함** |
| `.../is-cis-s5krc1s-setA.h` | 주석만. 0x75는 실험값이다. 값은 그대로 0x75 | 작성 |
| `tools/linux510/dvsview.c` | <ul><li>`/stats`에 `link` 계수기: 버퍼, 짧은 버퍼(데이터 줄 < 줄), 데이터 줄, 모르는 워드, sub 고침, frame_end 번호 건너뜀, scan 누락(버퍼 첫 단계 따로), 시간 오류·되돌아감·재시작</li><li>자체 시험에 기대 JSON</li></ul> | 작성만 했다. **빌드·자체 시험 안 함** |
| `tools/linux510/dvsctl.py` | `/stats`에 `pll`(`pll_live`)과 호출자가 준 값(V4L2 sequence) | 단위 시험 4개 통과. 보드 설치 안 함 |
| `tools/linux510/ov5647-cap.py` | VC0 버퍼의 V4L2 `sequence`로 잃은 버퍼를 센다(`seq_skips`, `seq_lost`) | 문법 확인, 계수 함수 시험. 보드 설치 안 함 |
| `tools/board/plltest.py` | 시험 한 번 실행기(6장) | 자체 시험 통과. 보드에서 `--observe`로 실행 중 |
| `tools/host/dvslink.py` | 녹화(`.gz`, `.bin`)와 VC0 raw 버퍼 검사(6장) | 자체 시험 통과. 녹화 42개에 실행 |

## 6. 측정 항목과 판정 기준

`plltest.py PROFILE RX_MBPS MINUTES`(보드에서 root로 실행)의 순서는 다음과 같다.

1. `pll`, `e850_csi_speed`, `e850_csi_settle`을 쓰고 `/dev/kmsg`에 표시를 남긴다.
2. dvslive를 다시 시작하고 20초 기다린다.
3. `run.json`을 남긴다. 파라미터, dmesg의 PLL·settle 줄, 모듈 sha256이 들어간다.
4. 10초마다 `/stats`를 `stats.jsonl`에 쓴다.
5. 끝나면(Ctrl-C, SIGTERM 포함) `summary.json`을 쓰고 `setfile`/0/0으로 되돌린 뒤 dvslive를 다시 시작한다.

`--sample N`을 주면 앞뒤로 dvslive를 멈추고 VC0 raw 버퍼 N개를 받는다. 받은 버퍼에 `dvslink.py`를 돌리고 gzip으로 남긴다. 버퍼 경계 그대로이므로 경계 손실 가설을 확인할 수 있다.

판정(`summary.json`의 `criteria`):

| 기준 | 통과 조건 | 재는 것 |
|---|---|---|
| `readback` | `pll_live`가 프로파일의 세 바이트로 시작 | 쓴 값이 들어갔는가(주파수가 아님) |
| `duration` | 정한 시간 − 20초 이상 | |
| `stream` | dvslive 재시작 0, 버퍼 > 0 | |
| `crc` | CRC 증가 0 | 링크 비트 오류 |
| `frames_complete` | 짧은 버퍼 0 | 잘린 프레임(Minsu: 2220 미만에서 CRC 없이 잘림) |
| `no_buffer_loss` | V4L2 sequence 건너뜀 0 | 사용자 공간까지 오지 못한 버퍼 |
| `no_scan_loss` | scan 누락 0, frame_end 누락 0 | CRC 없이 빠진 데이터 |
| `ts_continuity` | 시간 오류·되돌아감·고침·재시작 0 | |
| `no_unknown_words` | 모르는 워드 0 | 깨진 워드 |

- 하나라도 실패하면 `pass`는 false다. 측정하지 못한 기준이 있으면 null이다. 다만 V4L2 sequence는 예외다. 드라이버가 채우지 않으면 null로 두고, scan 누락이 같은 것을 잡는다.
- 비율도 남긴다: CRC/s, 버퍼/s, scan 누락 %, 짧은 버퍼 %, 버퍼당 데이터 줄.
- **기준선 0x75도 `no_scan_loss`에서 실패할 것으로 본다(녹화에서 1.62%).** 그래서 후보는 절대 기준과 함께 같은 날 기준선의 비율과도 비교한다.

## 7. 시험 순서

다른 조건은 모두 고정한다. setfile, mode0 8192×127, 2-lane, margin 17, flush 50, 렌즈·장면(가만히 둔 책상), dvslive 설정이 그렇다. 보드는 Wi-Fi ssh로 보고, USB(fastboot 복구)는 꽂아 둔다.

준비:

1. Docker에서 CIS 모듈(`modules/s5krc1s-pablo/build-module.sh`)과 `libdvsview.so`를 빌드한다. `-DDVSVIEW_TEST` 자체 시험을 돌린다.
2. 커널 소스에서 E850 CSIS PHY 표가 2244/2298을 지원하는지, 그때 settle이 얼마인지 확인한다.
3. 보드에 설치한다. 이전 판은 `/root/*.prev-11.46`으로 남긴다. CIS 모듈은 dvslive를 멈추고 rmmod/modprobe로 바꾼다(11.25에서 재부팅 없이 했다).
4. 관찰 실행(`baseline-observe-20261111`)을 멈춘다.

| 순서 | 프로파일 | RX | 시간 | 목적 |
|---|---|---|---|---|
| 0 | `setfile`(00/75/00) | 0 | 10분, `--sample 20` | 기준선. 새 계수기로 0x75를 다시 잰다 |
| 1 | `recommended_00_8b_b0` | 0 | 10분, `--sample 20` | 수신 설정 그대로 |
| 2 | `recommended_00_8b_b0` | 2244 | 10분 | 수신 맞춤(2단계 확인 뒤) |
| 3 | `alternative_00_8b_f8` | 0 / 2298 | 10분씩 | |
| 4 | 0~3 반복 | | 10분씩 | 순서를 바꿔 2번 더. 다시 시작할 때마다 결과가 같은지 본다 |
| 5 | 4까지 통과(또는 기준선보다 나은)한 것 | | 1시간 | |
| 6 | 5 통과 | | 24시간 | `systemd-run --unit=plltest …` |
| 참고 | `default_00_8d_00` | 0 | 2분 | CRC 약 4800/s 재현(선택) |

```sh
# 보드 root. 결과: /var/tmp/plltest/<이름>/{run.json,stats.jsonl,summary.json,raw-*}
python3 /var/tmp/plltest.py setfile 0 10 --sample 20 --out /var/tmp/plltest/0-setfile-rx0-1
python3 /var/tmp/plltest.py recommended_00_8b_b0 0 10 --sample 20 --out /var/tmp/plltest/1-8bb0-rx0-1
# 24시간
systemd-run --unit=plltest python3 /var/tmp/plltest.py recommended_00_8b_b0 2244 1440 --out /var/tmp/plltest/6-8bb0-24h
```

- 위험: 11.29에서 13개 값을 바꿔 봤을 때 보드가 멈춘 적은 없다. 데이터가 없는 값에서는 버퍼가 0이 되고, 끝나면 `setfile`로 돌아간다.
- README와 기본 setfile은 E850 측정 결과가 나온 뒤에만 바꾼다.

## 8. 결과

| 날짜 | 조건 | 시간 | CRC | 버퍼/s | scan 누락 | frame_end 누락 | 짧은 버퍼 | 다시 읽기 | 판정 |
|---|---|---|---|---|---|---|---|---|---|
| 2026-11-07~08 | 0x75, 녹화 42개 | 273초 | 기록 안 됨 | — | 4,427 / 272,857(1.62%) | 2,702 / 274,561 | 녹화로는 모름 | 없음 | 기준 아님(녹화 검사) |
| 2026-11-11 | 0x75, 관찰(이전 lib) | 진행 중, 첫 9 표본 | 0 | 34.9~35.0 | 이전 lib이라 못 잼 | 못 잼 | 못 잼 | 없음 | 판정 못 함 |

## 9. 되돌리기

- `plltest.py`는 끝날 때 `pll`=`setfile`, `e850_csi_speed`=0, `e850_csi_settle`=0으로 되돌리고 dvslive를 다시 시작한다. 파라미터는 재부팅하면 기본값으로 돌아간다.
- 모듈과 lib은 `/root/*.prev-11.46`, `/usr/local/lib/libdvsview.so.prev-11.46`으로 되돌린다.
- 보드가 멈추면 워치독 → fastboot → `~/e850-logs/boot-nokvm/boot.img`(CLAUDE.md 복구)를 따른다.
