# OV5647 CIS driver for the Exynos Pablo framework (E850-96 CN4)

삼성 벤더 커널(Linaro `android-exynos-5.10`)의 카메라 프레임워크(Pablo, `fimc-is`)에 OV5647 센서를 추가하는 묶음이다.
S5KRC1S(DVS) 브링업 패치와 같은 구조를 따른다.

| 파일 | 내용 |
| --- | --- |
| `files/.../cis/is-cis-ov5647.c` | CIS 드라이버: probe, 칩 ID 확인, 전역 설정, 모드 변경, 스트림 켜기/끄기 |
| `files/.../cis/is-cis-ov5647.h` | 레지스터 이름(메인라인 `ov5647.c`와 같은 이름) |
| `files/.../cis/is-cis-ov5647-setA.h` | 레지스터 표 — `gen_setfile.py`가 메인라인 v6.6에서 생성 |
| `files/.../camera/module_ov5647.dtsi` | 센서 모듈 DT: 모드 4개(RAW10, 2레인) |
| `files/.../camera/s5e3830-e850-ov5647-camera.dtsi` | 보드 배선: `hsi2c_2`, 주소 `0x36`, sensor1/CSI1, GPG1[5] 전원 |
| `apply.sh` | 커널 트리에 파일 복사, 센서 ID·Kconfig·Makefile·defconfig 추가, 보드 DT include 전환 |
| `gen_setfile.py` | 레지스터 표 생성기 |

## 모드

| 모드 | 해상도 | fps | 레인 속도(Mbps) | 메인라인 표 |
| --- | --- | --- | --- | --- |
| 0 | 640×480 | 60 | 275 | `ov5647_640x480_10bpp` |
| 1 | 1296×972 | 46 | 408 | `ov5647_2x2binned_10bpp` |
| 2 | 1920×1080 | 30 | 408 | `ov5647_1080p30_10bpp` |
| 3 | 2592×1944 | 15 | 437 | `ov5647_2592x1944_10bpp` |

레인 속도는 메인라인 `pixel_rate × 10 bit ÷ 2 lane`로 계산했다. CSIS settle 값 계산에 쓰이며, 실제 측정으로 확인하지 않았다.

## 설계 메모

- **센서 ID 219**: `exynos_is_dt.h`의 "201~255: Other vendor sensors" 구역에서 비어 있는 첫 번호.
- **노출·게인 제어 없음**: 프레임워크는 구현되지 않은 `cis_ops`(NULL)를 건너뛴다(`CALL_CISOPS`). 먼저 RAW 프레임 수신을 확인하는 단계다.
- **probe에 `__init`을 붙이지 않음**: 모듈에서 `-EPROBE_DEFER` 뒤 probe가 다시 불리면 해제된 init 코드를 실행하게 된다(S5KRC1S 드라이버에는 붙어 있다).
- **칩 ID는 init에서 확인만**: init 시점에 전원 순서가 아직 안 돌았을 수 있어, 실패해도 init을 실패시키지 않고 로그만 남긴다.
- **MCLK 없음**: 시험한 모듈은 자체 발진기로 동작했다(MCLK2 미출력 상태에서 칩 ID 판독). 다른 모듈이면 `power_seq`에 `PIN_MCLK`를 추가한다.
- **연속 MIPI 클록**: 스트림 켤 때 `0x4800 = 0x04`(BUS_IDLE만). 메인라인에서는 DT `clock-noncontinuous`일 때만 클록 게이팅을 켠다.
- **CN4는 카메라 하나**: `apply.sh`는 보드 DT의 S5KRC1S include를 OV5647로 바꾼다. DVS로 돌아가려면 그 한 줄을 되돌린다.

## 적용과 빌드

```bash
# 커널 트리(도커 볼륨 e850-kernel)에 적용
docker run --rm -v e850-kernel:/work/kernel -v "$PWD/modules/ov5647-pablo:/m:ro" e850-build-env:1 \
    sh /m/apply.sh /work/kernel

# 모듈과 DTB 빌드(amd64 빌드 이미지, Android prebuilt clang 14)
docker run --rm -v ~/e850-a13-build:/work -v e850-kernel:/work/kernel -v e850-out:/work/out e850-build-env:1 bash -c '
  export PATH=/work/prebuilts/clang/host/linux-x86/clang-r450784e/bin:$PATH
  cd /work/kernel
  MK="make O=/work/out ARCH=arm64 LLVM=1 LLVM_IAS=1 CROSS_COMPILE=aarch64-linux-gnu- -j10"
  scripts/config --file /work/out/.config --module CAMERA_CIS_OV5647_OBJ
  $MK olddefconfig
  $MK drivers/media/platform/exynos/camera/sensor/module_framework/cis/is-cis-ov5647.ko
  $MK dtbs'
```

빌드 결과와 보드 시험 결과는 [docs/E85096-LINUX510-BOOT.md](../../docs/E85096-LINUX510-BOOT.md)에 기록한다.
