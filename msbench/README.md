# msbench: E850-Jetson-MultiSensor-AI-Benchmark

E850-96(Exynos 850)과 NVIDIA Jetson에서 AI 영상처리 성능을 비교하는 벤치마크다. 명세는 사용자가 2026-11-26에 준 "E850 vs NVIDIA Jetson 기반 Multi-Sensor AI Runtime Benchmark"(16절, Phase 1~10)이고, 지시는 "아래 실험을 우리 환경에 맞추어 진행해"였다. 이 문서는 Phase 1의 결과물이다. 이미 있는 것과 더 만들어야 하는 것, 이 환경에서 할 수 없는 것(N/A와 원인)을 정리한다. 측정 결과와 경과는 저장소 `docs/E85096-LINUX510-BOOT.md` 11.116 이후 절에 적는다.

## 1. 장비 (2026-11-26, `setup/check_environment.py`로 확인)

| | E850 보드4 (벤치마크용) | E850 보드6 | Jetson Orin Nano | Jetson AGX Orin |
|---|---|---|---|---|
| 보드 | Samsung ERD3830 (exynos3830) | 같음 | Orin Nano Engineering Reference Developer Kit Super (p3767-0005) | **없음 → 모든 항목 N/A** |
| CPU | Cortex-A55 ×8, 546~2002 MHz(schedutil) | 같음. 측정 때 CPU4~7은 1846 MHz | Cortex-A78AE ×6, 729~1344 MHz(nvpmodel 25W) | |
| 메모리 | 3565 MB, zram | 같음 | 7485 MB | |
| OS / 커널 | Debian 12, vendor 5.10.157, Python 3.11.2 | 같음 | Ubuntu 24.04.4, 6.8.12-tegra, L4T R39.2.1, Python 3.12.3 | |
| GPU | Mali-G52 MP1(mali_kbase). OpenCL 없음. GLES 3.1은 Mesa panfrost + kbase shim(`tools/gpu`), Vulkan은 같은 트리의 panvk를 따로 빌드(`e850gpu/build-panvk.sh`, Mesa가 미완성으로 표시). AI는 그 위의 TFLite GL(11.117)과 NCNN Vulkan(11.118) | 같음 | Ampere(nvgpu), 드라이버 595.78, CUDA 드라이버 API 13.2, Vulkan ICD 1개 | |
| CUDA / TensorRT | 해당 없음 | 해당 없음 | 시스템에는 설치 안 됨(apt 후보 JetPack 7.2.2, TensorRT 11.4.1+cuda13.4). sudo 없이 홈 `~/jp`에 풀어 씀(아래, Phase 7) | |
| DLA | 없음 | 없음 | 없음(Orin Nano에는 DLA가 없다) | |
| 전력 측정 | **센서 없음** | 없음 | INA3221: VDD_IN, VDD_CPU_GPU_CV, VDD_SOC(사용자 권한으로 읽힘) | |
| 센서 | CX3 현미경(04b4:00c3), S5KRC1S 드라이버는 올라가 있으나 센서 없음 | S5KRC1S DVS(MIPI CN4), USB 카메라 1903:8328 | NRV DVS(`nrv_dvs`, VI), USB 카메라 1903:8328 | |
| 평소 배경 부하 | 현미경 뷰어 + Chrome, 전체의 약 12% | DVS 실시간(+AI) + dvsgl + mpv, 약 34% | DVS 실시간 + GNOME + mpv, 약 14% | |

벤치마크용 AI 런타임은 시스템 Python을 건드리지 않는다. Mac에서 받은 wheel을 `msbench/py/<X.Y>`에 풀고 `PYTHONPATH`로만 쓴다(`run_benchmark.py`, `check_environment.py`가 스스로 찾는다).

| 런타임 | E850(cp311) | Jetson(cp312) | 비고 |
|---|---|---|---|
| PyTorch | 2.15.0+cpu | 2.15.0+cpu | download.pytorch.org/whl/cpu. Jetson GPU용 PyTorch는 Phase 7 |
| onnxruntime | 1.31.1(시스템, 11.112부터) | 1.31.1(`~/cmp/ort.whl`) | CPUExecutionProvider만 |
| TFLite / LiteRT | ai-edge-litert 1.4.0 | 1.4.0 | XNNPACK 기본 |
| NCNN | 1.0.20261020 | 같음 | Vulkan 끔(E850에는 Vulkan이 없다) |
| numpy | 2.4.6 | 2.5.5 | `msbench/py` 안의 것 |
| PyTorch CUDA | 해당 없음 | 2.14.1+cu130 | `~/jp/py`, pip.pyz `--target`(download.pytorch.org/whl/cu130, cuDNN 9.24 등 nvidia-* wheel 포함). 빌드 목록에 sm_87이 없고 sm_80 커널이 돈다(같은 8.x) |
| onnxruntime GPU | 해당 없음 | onnxruntime-gpu 1.24.0 | `~/jp/py-ort`, pypi.jetson-ai-lab.io sbsa/cu130. CUDA EP는 PyTorch wheel의 cuDNN·cuBLAS를 쓴다 |
| TFLite GL (GPU) | TF v2.20.0 C API + GPU 위임자 v2를 Bazel로 직접 빌드(`e850gpu/`), `GL_ONLY`(GLES 3.1 compute) | 해당 없음 | 보드의 Mesa panfrost + kbase shim을 쓴다(`. e850gpu/gpu-env.sh`). pip wheel·CMake 판에는 GL 백엔드가 없다. FP32·FP16만(INT8은 GL에 정수 연산이 없음) |
| NCNN Vulkan (GPU) | pip NCNN 1.0.20261020(Vulkan 포함) + panvk(`~/gpu/mesa-vk`, `. e850gpu/vk-env.sh`) | 해당 없음 | `--runtime ncnn-vk`. winograd 끔(셰이더 컴파일이 끝나지 않음). panvk에 16비트 저장·연산이 없어 FP16은 버퍼만 FP16(packed), 계산은 FP32 |
| TensorRT | 해당 없음 | 11.4.1 | `~/jp/root`: L4T r39.2 apt 저장소의 deb를 `apt-get download` + `dpkg -x`(cudart 13.2 포함). `trtexec`도 여기 |

Jetson GPU 런타임은 `. benchmark/jetson-gpu-env.sh`가 `MSB_PYPATH`(CPU용 `py/`보다 앞), `LD_LIBRARY_PATH`, `TRTEXEC`를 잡는다.

## 2. 이미 있는 것 (재사용)

| 명세 항목 | 저장소에 있는 것 | 상태 |
|---|---|---|
| E850 CIS 드라이버 | OV5647 Pablo RAW10 캡처 `tools/linux510/ov5647-cap.py`, `modules/ov5647-pablo`(ISP 없음) | 동작. 일반 영상 검출에는 USB 카메라가 현실적이다 |
| 8M / 16M / 50M CIS | CX3 UVC 현미경 키트(Hi-846 8M, 16M, Hi-5031Q 50M 모듈을 바꿔 끼움), `tools/board/scope/` | 동작. **렌즈 없는 현미경 영상이라 COCO 객체 검출 대상이 아니다.** 명세의 "8MP CIS 기본"은 USB 카메라 1903:8328(640×480, 실측 15 fps, 11.115)로 대신한다 |
| E850 DVS 드라이버 | `modules/s5krc1s-pablo`(is-cis-s5krc1s), CN4 DT 8192×127 RAW8(11.18, 11.26) | 동작, **바꾸지 않는다** |
| MIPI CSI-2 수신 | fimc-is CSIS VC0 → `ov5647-cap.py --live --raw8`, 캡처 루프 C(`dvs_capture`, 11.112) | 동작 |
| D-PHY PLL·CRC 설정 | 11.18~11.26의 DT와 setfile. CRC가 모든 줄에 나면 케이블을 다시 꽂는다(11.53) | 검증됨, **바꾸지 않는다** |
| DVS 디코더·타임스탬프 | `tools/linux510/dvsview.c`: AER 패킷 → (t, x, y, 극성) 링, 32비트 표시 시계, 센서 시계와 CLOCK_MONOTONIC 대응(`d2m`, 11.56), 손실·CRC·지연 통계 | 동작 |
| 이벤트 표현 | 이벤트 프레임(렌더러), 시간 표면 `dvs_surface` 480×360(11.56), 60 ms 개수 프레임 2×120×160(`dvs_act_bin`, 11.37) | 있음. **복셀 그리드는 없다** |
| 노이즈 필터 | `dvs-filter`(CPU3), GPU판 `dvsgl.c` | 동작 |
| 뷰어 | `dvsgl`(GPU), `dvsx11.py`, 웹 `dvsctl.py`(CPU7), USB 카메라 mpv(`e850-open cam`), Jetson `tools/jetson/dvs-jetson.py`, `usbcam` | 동작 |
| AI 추론 | `dvsai.c`(onnxruntime C API, 행동 분류 MobileNetV3-small+GRU, YuNet 얼굴), 학습 `tools/host/act/`(PyTorch, Mac MPS) | 동작. **객체 검출(YOLO)은 없다** |
| Jetson DVS | NRV DVS(`nrv_dvs`, 협력 측 드라이버)에 E850 디코더를 그대로 올림(11.108) | 동작. 명세의 "Jetson DVS 센서 포팅"은 이미 되어 있다 |
| 공간 정렬 | DVS 두 대 3D 합성: 태블릿 바둑판 깜빡임 페이지와 호모그래피(`tools/host/dvs3d.py`, `dvsfuse_page.py`, 11.56~11.62) | CIS+DVS 정렬에 같은 방법을 쓸 수 있다(USB 카메라도 바둑판을 본다) |
| 성능 측정 도구 | `tools/board/thrcpu.sh`, `cmpload.sh`, `viewstat.py`, `archbench.c`, `aibench.py`, `allcore.sh`, `onnxvariants.py`, `tools/linux510/dvsbench.py` | 11.111, 11.115에서 씀 |

## 3. 새로 만든 것 (Phase 1~2)

| 파일 | 하는 일 |
|---|---|
| `setup/check_environment.py` | 장비·OS·CPU 클럭·GPU·CUDA/TensorRT/DLA·온도·전력 센서·런타임·센서를 읽어 `results/env/*.json`에 남긴다. 표준 라이브러리만 쓴다 |
| `models/Dockerfile`, `models/export.py` | Mac Docker(linux/arm64)에서 모든 모델 파일을 한 번 만든다: TorchScript, ONNX FP32, ONNX INT8(QDQ), TFLite FP32·INT8(onnx2tf 정적 정수 양자화), NCNN FP32(pnnx)·INT8(ncnn2table aciq). 같은 보정 이미지 100장. `manifest.json`에 크기, 파라미터, FLOPs, 출력 모양, TorchScript 대비 출력 차이, INT8 모델에 FP32로 남은 비율 |
| `data/prepare.py` | 같은 입력: COCO val2017 200장(정확도) + 100장(보정), Ultralytics LetterBox 640·320. Imagenette(ImageNet 10개 클래스) 200장 + 100장, 224 중앙 크롭 |
| `benchmark/runtimes.py` | CPU 어댑터(PyTorch·onnxruntime·TFLite·NCNN FP32/FP16/INT8), Jetson GPU 어댑터(PyTorch CUDA FP32/FP16, onnxruntime CUDA FP32/FP16, TensorRT FP32/FP16/INT8: 호스트 numpy→numpy를 동기로 재고, H2D·계산·D2H를 따로도 잰다), YOLO NMS(클래스별, 자체 시험 포함) |
| `models/fp16_onnx.py`, `models/trt_int8.py` | GPU용 ONNX: FP16(입출력 float32), TensorRT용 대칭 INT8 QDQ(TensorRT 11은 strongly typed라 정밀도를 ONNX가 정한다). ResNet18은 첫 합성곱을 float로 둔다(INT8로 하면 Orin용 구현이 없어 빌드 실패) |
| `benchmark/jetson-gpu-env.sh` | Jetson GPU 런타임 경로 |
| `e850gpu/` | E850 GPU(11.117): `build-tflite-gl.sh`(Mac Docker, debian:12 arm64 + Bazel, `tflite-gl.patch`: GL에 셰이더가 없는 TRANSPOSE·BATCH_MATMUL을 GPU에서 뺌), `gpu-env.sh`(Mesa 경로), `gpucheck.py`(CPU와 출력 비교), `gltest.c`·`build-gltest.sh`(GLES compute 최고 GFLOPS·대역폭·dispatch 지연), `b6-live-gpu.sh`(보드6 실시간 카메라 GPU 시험). 어댑터는 `runtimes.py`의 `TFLiteGL`(`--runtime gpu`가 E850에서 고름, `MSB_GL_PARTITIONS`로 GPU 분할 수, 기본 1). 11.118: `build-panvk.sh`·`vk-env.sh`(NCNN Vulkan, 어댑터 `NCNNVK`, `--runtime ncnn-vk`로만 고름: `LD_PRELOAD`가 GL과 다름), `corun.py`(CPU NCNN과 GPU 워커를 함께 돌려 처리량·간섭), `layercost.py`(모델의 합성곱마다 CPU·GPU 시간, 이상적 층 분할), `splitconv.py`(합성곱 하나를 출력 채널로 나눠 CPU·GPU가 동시에), `branchsplit.py`(ResNet 다운샘플 블록: CPU가 본 경로, GPU가 1×1 shortcut), `cothread.py`(corun을 한 프로세스의 두 스레드로, CPU 클럭·온도 표본 포함). `gltest load fma|copy SECS`는 GPU에 계산만 또는 DRAM 복사만 계속 준다(GPU가 CPU를 얼마나 깎는지). 둘 다 GPU 스레드를 실행마다 CPU3에 다시 고정한다: `OMP_PROC_BIND` 아래에서 libgomp가 첫 OpenMP 영역에서 CPU4로 묶는다 |
| `benchmark/threadprobe.sh`, `benchmark/placement.py` | 스레드 진단: 코어 집합·환경 변수만 바꿔 설정 하나를 돌림, 런타임 스레드가 어느 코어에 몇 개 몰리는지 표본 |
| `camera/realtime_detection.py`, `camera/jetson-live.sh` | 실시간 USB 카메라 YOLO(Phase 3): V4L2 mmap, YUYV→RGB, 레터박스, 어댑터, NMS, 카메라→검출 지연 |
| `dashboard/generate_report.py`, `dashboard/tables.py` | `results/report.html`(한 파일, 네트워크 없음)과 `summary.csv`, 문서용 Markdown 표. 장비마다 모든 원자료를 이름순으로 읽고 같은 설정은 나중 판이 덮는다 |
| `benchmark/run_benchmark.py` | 설정마다 자식 프로세스 하나: 고정 코어, 워밍업, 반복 블록 3회, 지연 분포, NMS 시간, CPU 사용, 클럭, 온도, Jetson 전력. `--accuracy`면 정확도 패스 |
| `benchmark/evaluate.py` | COCO mAP50-95, mAP50(pycocotools), conf 0.25·IoU 0.5에서 P, R, F1, 누락률 |

명세의 실행 명령에 맞춘 것:

```sh
python3 setup/check_environment.py
python3 benchmark/run_benchmark.py --device e850 --sensor camera --runtime all --precision all --accuracy
python3 benchmark/run_benchmark.py --device orin-nano --sensor camera --runtime all --precision all --accuracy
```

## 4. 명세를 이 환경에 맞춘 점

- **기록된 같은 데이터로 하는 런타임 비교(Phase 2)와 실시간 센서 실험(Phase 3~5)을 나눈다.** Phase 2의 CAMERA 입력은 COCO 이미지를 미리 레터박스한 uint8 배열이다. 모든 장비와 런타임이 같은 배열을 받는다.
- 모든 모델의 입력은 RGB/255다. 분류 모델은 ImageNet mean/std를 첫 층으로 넣었다. 그래서 전처리 코드가 장비와 런타임마다 같다.
- **반복 횟수:** 명세는 워밍업 50회, 반복 500회 이상, 3회 반복이다. A55 코어 하나로 YOLO11n 640은 한 번에 수 초가 걸릴 수 있다. 그래서 워밍업은 최대 10초, 반복 블록은 500회 또는 25초 중 먼저 오는 쪽(최소 10회)으로 끊고, 실제 횟수를 결과에 남긴다.
- **스레드와 코어:** 1, 4, 최대(E850 8, Orin Nano 6). 코어를 고정한다. E850은 1 → CPU5, 4 → CPU4~7, 8 → 전부. Jetson은 1 → CPU5, 4 → CPU2~5, 6 → 전부(첫 전체 측정은 1 → CPU1, 4 → CPU0~3이었는데, CPU1에는 DVS 디코더가 고정되어 있어 1스레드가 코어의 0.84만 받았다. 11.116). 다른 일(현미경, Chrome, 실시간 DVS)은 끄지 않았다. 그 부하는 블록마다 `/proc/stat`으로 함께 적는다.
- **INT8:** 세 런타임 모두 같은 100장으로 정적 보정한다. ONNX는 Conv·MatMul·Gemm만 양자화한다(YOLO 출력의 좌표 0~640과 점수 0~1이 한 텐서로 이어지므로, 출력을 텐서 하나로 양자화하면 점수가 망가진다). TFLite는 Ultralytics처럼 좌표를 0~1로 나눈 머리로 전 연산을 정수화한다. NCNN은 합성곱·전결합 층만 양자화한다. 런타임마다 양자화 범위가 다르다. 그래서 FP32로 남은 비율을 manifest에 함께 적는다.
- **FP16(CPU):** 명세의 E850 실험 목록에는 없지만 NCNN은 ARMv8.2 CPU에서 FP16 저장·연산이 기본이라 `ncnn fp16`으로 따로 잰다(`ncnn fp32`는 이를 끈 진짜 FP32).
- **GPU 정밀도:** FP32는 모든 경로에서 TF32를 끈다(PyTorch `allow_tf32=False`, ORT `use_tf32=0`, trtexec `--noTF32`). GPU 측정은 호스트 1스레드(Jetson CPU5)로 한다. GPU 클럭은 고정하지 못했다(`jetson_clocks`에 sudo 필요). 블록 끝의 GPU 클럭을 남긴다.

## 5. N/A와 원인

| 항목 | 원인 | 풀려면 |
|---|---|---|
| AGX Orin 전부, DLA FP16·INT8 | 장비가 없다. Orin Nano에는 DLA가 없다 | AGX Orin 장비 |
| E850 GPU의 INT8, ORT·ArmNN GPU | TFLite GL(11.117)과 NCNN Vulkan(11.118)을 잰다. GL 백엔드에는 정수 연산이 없고(양자화 모델은 float로 푼다), NCNN의 INT8은 CPU에서만 돈다. OpenCL 드라이버가 없어 ORT·ArmNN GPU 경로가 없다 | 이 커널(kbase r38p1)에 맞는 Mali OpenCL 사용자 드라이버. 다른 SoC용 libmali는 쓰지 않기로 했다(11.117) |
| E850 NCNN Vulkan의 H2D·계산·D2H | NCNN Python에 GPU 버퍼를 따로 올리고 내리는 API가 없다. 전체(numpy → numpy, 동기)만 잰다 | NCNN C++ API |
| E850 GPU의 YOLO 전부 | GL에 TRANSPOSE·BATCH_MATMUL 셰이더와, 둘째 입력이 상수 텐서인 SUB(박스 디코드)가 없다. 346개 연산 중 GPU는 167개(분할 1개) 또는 301개(분할 2개)이고 나머지는 CPU | GL 셰이더를 더 쓰거나, 모델에서 C2PSA 주의 블록·디코드를 떼어 CPU로 |
| E850 전력·에너지 | 전력 센서가 없다 | 외부 전력계. 명세대로 두 보드를 같은 지점(DC 입력)에서 같은 장비로 잰다. Jetson의 INA3221 `VDD_IN`은 모듈 입력이라 E850 DC 입력과 직접 비교하지 않는다 |
| Jetson PyTorch·onnxruntime CUDA INT8 | 이 모델들에 INT8 CUDA 커널 경로가 없다(QDQ는 CUDA EP에서 역양자화 뒤 FP32 합성곱) | GPU INT8은 TensorRT로만 잰다 |
| Jetson GPU 클럭 고정 | `jetson_clocks`·nvpmodel 변경에 sudo가 필요하다 | GPU DVFS(306~1020 MHz)를 그대로 두고 블록 끝 클럭을 기록 |
| DVS 객체 검출(YOLO11n-DVS), mAP | 라벨 있는 DVS 검출 데이터가 없다. 지금 DVS 모델은 행동 분류와 얼굴뿐이다 | 보드6 녹화 + 라벨, 또는 공개 이벤트 검출 데이터(센서가 다름)로 미세조정(Phase 4) |
| CAMERA+DVS 융합 정확도 | 두 센서를 함께 찍은 정답 데이터와 두 광학계의 정렬(보정)이 없다 | 같은 장면을 동시 녹화하고 11.61의 바둑판 방법으로 호모그래피를 맞춘다(Phase 5). 보드6은 MIPI DVS와 USB 카메라를 동시에 받는다 |
| 연산자별 시간 | Phase 2 범위 밖 | ORT 프로파일러, TFLite 벤치마크 도구의 op 프로파일, ncnn `benchmark`로 Phase 2 뒤에 |

## 6. 단계별 상태

| Phase | 내용 | 상태 |
|---|---|---|
| 1 | 기존 코드 분석, 환경 확인 | 이 문서, `results/env/` |
| 2 | 같은 데이터로 CPU 런타임 비교 | 됨(11.116): 두 장비 × 4모델 × 8설정 × 스레드 3, 정확도 |
| 3 | E850 CIS 실시간 YOLO | 됨(11.116): 보드6 USB 카메라, DVS AI 켬·끔, 코어 고정. Jetson도 같은 카메라로 CPU·GPU 8설정(`camera/jetson-live.sh`) |
| 4 | E850 DVS 실시간 YOLO | 아직(위 N/A) |
| 5 | CAMERA+DVS 융합(A·B·C) | 아직 |
| 6 | Jetson 센서·영상처리 | DVS·USB 카메라는 이미 동작(11.108, 11.113) |
| 7 | Jetson CUDA·TensorRT | 됨(11.116): 홈에 설치, PyTorch CUDA·ORT CUDA·TensorRT FP32/FP16/INT8. E850 GPU(Mali-G52) TFLite GL FP32/FP16도 됨(11.117), NCNN Vulkan FP32/FP16(packed)도 됨(11.118) |
| 8 | 세 장비 같은 조건 | AGX Orin 없음 → 두 장비 |
| 9 | 전력·에너지 | Jetson만(INA3221 VDD_IN, 결과의 `power_w`·`energy_j_per_inference`), E850은 외부 측정기 필요 |
| 10 | 통합 뷰어, HTML 보고서 | HTML 보고서 `results/report.html`, 문서용 표 `dashboard/tables.py`. 실시간 통합 뷰어는 아직 |

RQ 답(RQ1~RQ3 실측, RQ4 일부, RQ5~RQ8 N/A와 원인)은 저장소 `docs/E85096-LINUX510-BOOT.md` 11.116 끝에 있다.

큰 파일(모델, 데이터, wheel, `py/`)은 저장소에 넣지 않는다(`.gitignore`). 결과(`results/`의 JSON·CSV)는 넣는다.
