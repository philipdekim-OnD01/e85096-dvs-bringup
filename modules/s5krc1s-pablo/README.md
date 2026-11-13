# S5KRC1S (DVS) CIS driver for the Exynos Pablo framework (E850-96 CN4)

OV5647(`modules/ov5647-pablo`)과 같은 경로로 DVS를 받는다. ISP를 거치지 않는다(bypass): CSIS → WDMA → 메모리 → `/dev/video102`(`exynos-is-ss1`). bypass 패치(`modules/ov5647-pablo/bypass.sh`)는 센서와 관계없는 프레임워크 수정이라 그대로 쓴다.

커널 트리에 있던 S5KRC1S 브링업 드라이버를 가져와서 2026-11-06 측정(11.17)과 매뉴얼(`S5KRC1S_센서_user_manual.pdf`)에 맞게 고쳤다.

| 바꾼 것 | 전 | 후 | 근거 |
|---|---|---|---|
| RSTN | GPG1[5] (CN4 11) | **GPC0[2]** (CN4 12) | 11.17: GPC0[2] High일 때만 0x10 응답. 매뉴얼 Table 3: RSTN Low = HW standby |
| MCLK | GPC0[2]로 MCLK 출력 | 출력 안 함 | 내부 클록. RSTN과 같은 핀 |
| CAM_VDD_33 | 켜지 않음 | LDO32 3.3 V on/off | 11.17, OV5647 9.4 |
| `samsung,fast-plus-mode` | 있음 | 뺌 | CCI 최대 400 kHz(매뉴얼 1.2). OV5647 dtsi와 같게 |
| `front_sensor_id` | 없음 | `SENSOR_NAME_S5KRC1S`(42) | S_INPUT이 FRONT 센서를 이것으로 찾음(9.2) |
| VISION 전원 시나리오 | 없음 | 추가 | bypass 캡처는 scenario 1(stand alone) |
| probe `__init` | 있음 | 뺌 | 모듈에서 `-EPROBE_DEFER` 뒤 다시 불림 |
| 등록 | `late_initcall_sync`, exit 없음 | `module_i2c_driver` | rmmod 가능 |
| init | 로그만 | `SEL_MODE`(0x3000) 읽어 응답 확인(실패해도 init은 성공) | 매뉴얼에 칩 ID 레지스터 없음 |

레지스터 표(`setA.h`)는 vendor setfile에서 두 가지만 다르다. 0x0100을 뺐고, D-PHY PLL(0x30A5~A7)을 00/8D/00에서 00/8B/B0으로 바꿨다(11.49). 00/8D/00에서는 줄마다 CRC 오류가 났다(11.28). 11.29~11.48에는 00/75/00(0x30A6만 0x75)이었는데, 공식으로 VCO 범위 밖이라 실험값으로 남겼다(`pll=experimental_00_75_00`). 00/8B/B0은 0x75와의 A/B/A/B 비교와 24시간 시험에서 CRC 0, 누락 0이었다(11.48). 2-lane 설정(0x3915)과 모드(RAW8 8192×127, DT 1600 Mbps/lane)는 그대로다. 실제 레인 속도는 측정하지 않았다. 런타임 param은 `margin`(scan 주기, 11.29), `regs`(시험용 레지스터 쓰기), `flush_us`, `packets`다(`/sys/module/is_cis_s5krc1s/parameters`, 다음 stream on부터 적용).

## 적용과 빌드

```bash
docker run --rm -v e850-kernel:/work/kernel -v "$PWD/modules/s5krc1s-pablo:/m:ro" e850-build-env:1 \
    sh /m/apply.sh /work/kernel
docker run --rm -v ~/e850-a13-build:/work -v e850-kernel:/work/kernel -v e850-out:/work/out \
    -v "$PWD/modules/s5krc1s-pablo:/m:ro" e850-build-env:1 bash /m/build-module.sh   # → ext-s5krc1s/
# DTB: make ... dtbs 후 tools/linux510/build-bootimg.sh 로 boot.img
```
