# DVS 사람 행동 분류 (나타남 / 움직임 / 넘어짐) 설계

- 날짜: 2026-11-07
- 상태: 승인(2026-11-07). 구현 계획은 `docs/superpowers/plans/2026-11-07-dvs-person-action.md`.
- 상자 크기 측정(11.35)과는 별개 과제다. 경과는 `docs/E85096-LINUX510-BOOT.md` 11.36에 적는다.

## 1. 목표

DVS(S5KRC1S) 이벤트만으로 화면 속 사람 한 명의 행동을 분류해 파일에 기록한다.

- 기록하는 행동은 **나타남**, **움직임**, **넘어짐**이다.
- 인식은 MIPI로 들어와 디코딩한 이벤트에서 한다. HDMI 화면이나 웹 PNG를 다시 분석하지 않는다. HDMI 그리기는 바꾸지 않는다.
- 일반 카메라(OV5647)와 RGB 모델은 쓰지 않는다.

## 2. 정한 요구 사항 (사용자 답)

| 항목 | 내용 |
|---|---|
| 목적 | 사람 기록(파일) |
| 행동 | 나타났다, 움직인다, 넘어진다 |
| 첫 판 장면 | 옆에서 보는 고정 DVS, 한 사람, 사람 말고 크게 움직이는 것 없음 |
| 넘어짐 | 앉기, 천천히 눕기와 구별한다 |
| 방법 | 전이 학습. DVS 선행 연구를 바탕으로 하고, 850에서 현실적인 것 |
| 우리 센서 녹화 | 우선 동작당 5~10번 |
| 시험 | 실제로 넘어지지 않는다. 매트리스에 쓰러지기로 한다 |

## 3. 방법 선택

### 3.1 선행 연구 (논문에 적힌 값, 재현하지 않음)

| 연구 | 내용 | 이 설계에 쓴 것 |
|---|---|---|
| Lee 외 2017, Samsung Research (arXiv 1711.11200) | DVS 640×480, 31명 1,860개 클립(idle, lie, walk, sit, stand, fall, fake fall). 32×32 프레임 10장을 쌓은 TSN, 1.1M 파라미터, 11.4M ops, F1 0.955, Jetson TX1 CPU 31 fps | 작은 CNN이면 임베디드 CPU로 충분하다는 근거 |
| Khacef 외 2025, Sony·Intel (arXiv 2511.22554) | IMX636 1280×720 → 160×160, 60 ms 이벤트 수 프레임, 16 Hz. 14종(falling, sit down, stand up, lying, pick up, walking, no person 등). 넘어짐 F1: CNN+MLP 46%, CNN+S4D 77%, MCUNet+S4D 84%. 1M 파라미터 미만, Loihi 2 90 mW | **구조의 바탕**: 60 ms 프레임, 시간 상태, 16 Hz, "사람 없음" 클래스, 사람 영역을 자르지 않음 |
| Miao 외 2019, PAF (Front. Neurorobot.) | DAVIS346 346×260, 빈 사무실. 넘어짐 데이터 15명 180개(falling 대 bending, slumping-down, tying-shoes). 행동 데이터 10종 450개 | 공개 학습 데이터 |
| Krishnan 2022 (arXiv 2201.12285) | PAF를 25 fps 프레임으로 바꿔 사전 학습 영상 모델을 미세 조정. MViT 0.958, C2D 0.916, X3D 0.625(12종) | 전이 학습이 DVS에서 통한다는 근거. 모델은 850에 너무 크다 |
| DailyDVS-200 (ECCV 2024) | DVXplorer Lite 320×240, 200종, 47명, 22,046개, MIT 라이선스. fall down(109), walk(107), sit down(148), stand up(165), pick up things(100) 등 | 공개 학습 데이터 |
| TTPOINT 2023, EventMamba 2024 | 이벤트를 점 (x, y, t)로 바로 넣음. 0.3~0.9M 파라미터, 샘플당 0.6~1 GFLOPs | 채택하지 않음: 850에서 판정 한 번에 0.2~0.3초(추정). 우리 이벤트는 대부분 잡음이라 점을 뽑기 전에 걸러야 함. Mamba는 ONNX로 옮기기 어려움 |
| EZSR 2025, EventCLIP 2023 | CLIP 기반 zero-shot/few-shot. EZSR은 PAF zero-shot 84.7%, ViT-B/16 | 채택하지 않음: 판정 한 번에 수 초. 녹화 라벨링 보조로만 쓸 수 있음 |
| Rojas 2026 (arXiv 2606.18732) | v2e로 RGB 영상에서 합성 이벤트를 만들어 walking/sitting/falling 학습. 실제 DVS 시험 없음 | 정확도가 부족할 때 붙일 선택지로만 둠 |

### 3.2 850 실측 (2026-11-07 14:50, live가 도는 상태, 버린 시험)

ONNX Runtime 1.31.1 CPU, 무작위 가중치 MobileNetV3-small + GRU(128), 300번 반복.

| 항목 | CPU5 한 코어 | CPU4~6 세 코어 |
|---|---|---|
| 모델, 입력 2×120×160 | 중앙값 27.2 ms (p95 27.5) | 14.6 ms |
| 모델, 입력 2×60×80 | 14.3 ms (p95 14.7) | 8.2 ms |
| numpy 프레임 만들기, 이벤트 7.2만~12.6만 개(60 ms 분량) | 5.6~7.8 ms | — |

- 16 Hz면 한 단계에 33~35 ms가 걸린다. 예산 62 ms 안이고, 코어 하나의 55~60%다.
- 재는 동안 live는 104 fps, draw 7.3 ms, ingest 2.9~3.9 ms로 평소와 같았다.
- 별도 프로세스로 쟀다. 같은 프로세스 안의 스레드로 돌릴 때의 GIL 영향과 웹 응답 영향은 재지 않았다.
- 보드에는 쓸 수 있는 NPU가 없다(`npu_throughput`은 PM QoS 노드). GPU는 사용자 공간 드라이버가 없다. CPU 플래그에 `asimdhp`는 있고 `asimddp`는 없다. 그래서 int8로 크게 빨라지기를 기대하지 않고 fp32로 돌린다.

## 4. 구조

```
[보드]
MIPI → dvs_ingest() (C, 기존) → 이벤트 링 (기존)
   ├─ render 스레드 → fb0 / HDMI              (바꾸지 않음)
   ├─ dvs_events()  새 이벤트를 복사해 주는 C 함수
   │     └─ 분류 스레드 (dvsact.py, ov5647-cap.py --act, CPU4~6)
   │           센서 시간 60 ms마다: 이벤트 → 2×120×160 프레임 → ONNX(인코더 + GRU)
   │           → 9종 확률 → 상태 판정 → JSONL
   └─ dvs_record()  패킷 원본을 gzip으로 저장 ← dvsctl 페이지 녹화 버튼 (POST /rec)

[Mac]
녹화 .gz → dvsview.c 재생 빌드(Docker debian:12, 보드와 같은 decode()) → 이벤트
PAF .aedat / DailyDVS-200 .aedat4 → 이벤트
   → dvsact.py frames() (보드와 같은 파일) → PyTorch(MPS) 학습 → ONNX → 보드
```

- 입력 표현 코드는 `tools/linux510/dvsact.py` 하나이고, 보드 추론과 Mac 학습이 같이 쓴다.
- 녹화는 디코딩 전의 패킷을 저장한다(초당 약 7 MB). 디코딩한 이벤트로 저장하면 초당 10~17 MB다.

## 5. 입력 표현 (`dvsact.frames()`)

- 이벤트는 (t µs, x, y, 극성)이다. t는 dvsview의 표시 시계(`struct ev.t`)이고, 좌표는 센서 원본이다. 화면 `flip`은 그리기에만 쓰이므로 입력에 영향이 없다.
- 센서 시간 60 ms 칸마다 극성별로 이벤트를 센다. 격자는 2×120×160이고, 960×720을 6×6 화소씩 묶은 것이다. 셀 값은 `log1p(개수)`, float32다.
- 칸은 센서 시간으로 끊는다. 칸 끝보다 늦은 이벤트가 하나라도 들어오면 그 칸을 닫는다. 데이터가 끊긴 칸은 빈 프레임으로 채우지 않고 건너뛴다.
- 공개 데이터는 좌표를 비례 축소해 같은 격자에 넣는다. DAVIS346은 x×160/346, y×120/260이고, DVXplorer Lite는 ÷2다.
- 우리 센서의 잡음: 화소당 1.9 Hz이므로 셀 하나에 60 ms마다 약 4개가 들어온다(36화소 × 1.9 Hz × 0.06 s). 공개 데이터로 학습할 때 같은 수준(0~2배 무작위)의 Poisson 잡음을 더한다.

## 6. 모델

- 인코더: torchvision `mobilenet_v3_small`, ImageNet 가중치. 첫 conv를 3채널에서 2채널로 바꾸고, RGB 가중치의 채널 평균 × 1.5로 초기화한다. features → 공간 평균 → Linear 576→128.
- 시간 상태: `GRUCell(128, 128)`. 한 단계에 한 프레임. GRU가 부족하면 S4D로 바꿔 본다.
- 출력: Linear 128→9. 클래스는 `none`(사람 없음), `still`(가만히), `walk`, `sit_down`, `stand_up`, `pick_up`, `lie_down`, `fall`, `other`(기타 움직임)다.
- ONNX 입출력: `x` [1,2,120,160], `h` [1,128] → `y` [1,9](logits), `h2` [1,128]. opset 17.
- 약 1.0M 파라미터. 3.2절 실측으로 단계당 27 ms(한 코어)다.

## 7. 학습 데이터

### 7.1 클래스 맞추기

| 클래스 | PAF | DailyDVS-200 | 우리 녹화 |
|---|---|---|---|
| fall | falling | fall down (109) | 매트리스에 넘어지기 |
| walk | walking | walk (107) | 걷기 |
| sit_down | sitting-down, slumping-down | sit down (148), sit on the ground (93) | 의자·바닥에 앉기 |
| stand_up | getting-up | stand up (165) | 일어서기 |
| pick_up | bending, picking-up, tying-shoes | pick up things (100), squat (99), bow (97), tie shoelaces (80) | 숙여 줍기 |
| lie_down | — | — | 천천히 눕기 |
| still | — | — | 가만히 서 있기, 앉아 있기 |
| none | — | — | 빈 방 |
| other | arm-crossing, kicking, jumping, throwing, turning-around, waving | 위에 없는 클래스에서 클래스마다 몇 개씩 | 팔 흔들기 등 |

### 7.2 단계 라벨

- 클립에는 라벨이 하나뿐이다. 클립마다 "활동 곡선"을 구한다. 칸마다 (이벤트 수 − 잡음 기대값)을 0.3초 이동 평균한 값이다.
- 곡선이 가장 높은 시점 + 0.3초부터 클립 끝까지의 단계에만 클립 라벨로 손실을 건다. 그 앞은 손실을 걸지 않는다.
- `none`과 `still` 클립은 1초 이후 모든 단계에 손실을 건다.
- `walk`와 `other` 클립은 움직임이 처음 시작된 시점(곡선이 정점의 30%를 처음 넘는 칸) + 0.3초부터 손실을 건다. 내내 움직이는 동작이라 정점이 클립 끝에 오기 쉽기 때문이다(2026-11-08, 11.41에서 바꿈).
- 넘어짐 뒤 누워 있는 구간도 `fall`로 배운다. 그래서 넘어진 상태가 유지된다.

### 7.3 학습 순서 (Mac, 새 venv `~/.venvs/dvsact`: torch, torchvision, onnx, aedat 읽기)

1. ImageNet 가중치에서 시작한다.
2. PAF와 DailyDVS-200 부분 집합으로 학습한다. 잡음 더하기, 이벤트 솎기(0.5~1배), 좌우 뒤집기, 확대 0.8~1.2배, 이동을 쓴다. 클래스 불균형은 표본 가중치로 맞춘다. 공개 데이터는 각 데이터셋의 피험자 분할을 따른다.
3. 우리 녹화로 미세 조정한다. **클래스마다 2개는 시험용으로 빼 두고** 학습에 쓰지 않는다.
4. ONNX로 내보낸다. 같은 입력에서 PyTorch와 ONNX Runtime 출력의 차이가 1e-4 이하인지 본다.

- 학습 시퀀스는 클립 하나 전체다. 6초 클립이면 100단계다.

## 8. 녹화

- **C:** `int dvs_record(const char *path)`. path가 있으면 gzip 파일(`gzopen`, level 1)을 열고 기록을 시작한다. NULL이면 닫는다. 성공하면 0, 실패하면 −errno를 돌려준다.
  - `decode_lines()`가 줄을 0으로 지우기 전에, 줄마다 패킷 단어(n개)와 끝 표시 0 단어 하나를 쓴다.
  - 버퍼 경계는 남기지 않는다. 0 단어는 패킷 안에 나오지 않는다(`decode()`가 첫 0 단어에서 멈춘다).
- **재생:** `dvsview.c` 시험 빌드(`-DDVSVIEW_TEST`)에 `--replay in.gz out.bin`을 더한다. 0 단어로 나뉜 묶음을 같은 `decode()`에 넣고, 링의 `struct ev`(8 B: t, x, y|극성)를 그대로 `out.bin`에 쓴다.
- **HTTP (`dvsctl.py`):**
  - `POST /rec {"label": "<클래스>", "s": 6}`: 3초 뒤 s초 동안 녹화한다. 이미 녹화 중이면 409, 라벨이 9종 밖이면 400, `/var/tmp` 빈 공간이 300 MB 미만이면 507을 돌려준다.
  - `GET /rec`: `{"state": "idle|countdown|recording", "left_s": .., "last": "<파일>"}`.
- **파일:** `/var/tmp/dvsrec/<UTC 시각>-<라벨>.gz`와 `.json`(라벨, 시작 시각, 길이, `margin`, `flush_us`, 센서 이름)이다.
- **페이지 (`dvsctl.html`):** 라벨 선택, 녹화 버튼, 남은 시간 표시.
- **옮기기:** Mac에서 `rsync`로 `~/e850-logs/dvsrec/`에 받고, 보드에서 지운다. 녹화는 저장소에 넣지 않는다.
- 녹화 목록(첫 회): 7.1절의 "우리 녹화" 열에 있는 동작을 각각 5~10번 한다. 왼쪽·오른쪽·앞·뒤 방향을 섞는다.

## 9. 보드 추론과 판정

- **C:** `int dvs_events(struct ev *dst, int max, uint64_t *cursor)`. 링에서 `*cursor`부터 쓰인 곳까지, 최대 max개를 복사하고 cursor를 옮긴 뒤 개수를 돌려준다. 디코더가 cursor를 한 바퀴 앞질렀으면 남아 있는 가장 오래된 이벤트로 건너뛰고, 건너뛴 수를 `dvs_stats()`의 `act_lost`에 더한다.
- **옵션:** `ov5647-cap.py --act FILE --act-model PATH`. 둘 다 있을 때만 분류 스레드를 띄운다. 없으면 지금과 똑같이 돈다.
- **스레드:** `dvsctl.pin()`으로 CPU4~6에 고정한 뒤, 그 스레드에서 ONNX 세션을 만든다(`intra_op_num_threads=1`). 그래야 ONNX Runtime의 스레드가 같은 코어에 묶인다.
  - 60 ms마다 깨어나 새 이벤트를 받는다. 닫힌 칸마다 모델을 한 단계 돌린다.
  - 센서 시간이 3초 넘게 끊기면 GRU 상태를 0으로 되돌린다.
- **판정:** 한 클래스가 3단계(180 ms) 연속 1등이면 현재 상태가 된다. `fall`은 확률 ≥ 0.6이 3단계 연속이어야 한다. 문턱 값은 `dvsact.py`의 상수이고, 시험하면서 맞춘다.
- **기록:** 상태가 바뀔 때 한 줄을 쓰고 바로 flush한다.
  ```json
  {"t_us": 1794030632123456, "time": "2026-11-07T05:50:32.123456Z", "event": "fall", "state": "fall", "prev": "walk", "p": 0.87}
  ```
  - `event`는 위에서부터 처음 맞는 것으로 정한다. 나타나자마자 넘어져도 `fall`이 남는다.
    1. `state`가 `fall`이면 `fall`
    2. `prev`가 `none`이면 `appear`
    3. `state`가 `walk`, `sit_down`, `stand_up`, `pick_up`, `lie_down`, `other` 중 하나면 `move`
    4. 그 밖에는 `state`(`still`, `none`) 그대로
- **로그:** stderr(`/tmp/dvslive.log`)에 상태가 바뀔 때마다 `[dvsact] <event> <state> p 0.87`을 쓰고, 2초마다 단계 시간 중앙값·최댓값과 `act_lost`를 쓴다.
- **보드에 설치하는 것:** ONNX Runtime 1.31.1 aarch64 wheel과 flatbuffers, packaging, protobuf를 `/usr/local/lib/python3.11/dist-packages`에 푼다. 보드에 pip가 없어서이고, 3.2절 실측에서 이 방법으로 불러와졌다. 모델은 `/var/lib/dvsact/model.onnx`, 기록은 `/var/lib/dvsact/act.jsonl`이다.
- `dvslive.service`에 `--act`를 넣는 것은 10절 live 받아들임 시험을 통과한 뒤에 한다.

## 10. 시험과 받아들임 기준

| 단계 | 내용 | 기준 |
|---|---|---|
| 단위 (Mac) | 합성 이벤트로 `frames()`의 셀 개수와 칸 닫기를 확인 | 기대한 값과 같음 |
| 단위 (Docker) | `dvs_record()` → `--replay` 왕복, 기존 `DVSVIEW_TEST` 시험 | 이벤트가 같음, 기존 시험 통과 |
| 녹화·재생 | 한 클립을 보드 live 디코더 수(`dvs_stats`)와 Mac 재생 수로 비교 | 같은 수 |
| ONNX | PyTorch와 ONNX Runtime 출력 비교 | 차이 ≤ 1e-4 |
| 보드 성능 | 무작위 모델로 `--act`를 켠 live, 5분 | fps 104 유지, ingest max ≤ 7 ms, 단계 p95 < 50 ms, `act_lost` 0. 웹 `/frame.png`(`webbench.py`)는 켜기 전보다 10% 안쪽 |
| 오프라인 정확도 | 빼 둔 우리 클립(클래스마다 2개)의 혼동 행렬 | 숫자 그대로 보고. 표본이 작아 참고용 |
| live 받아들임 (첫 판) | 사용자가 매트리스 넘어짐 10번, 앉기·눕기·줍기 각 10번, 화면 밖에서 들어오기 10번 | 넘어짐을 1초 안에 기록한 것 8/10 이상. 넘어짐이 아닌 30번 중 `fall` 오판 3번 이하. `appear` 8/10 이상 |

못 미치면 녹화를 동작당 20번으로 늘린다. 그래도 안 되면 v2e 합성 데이터를 붙인다.

## 11. 위험과 미정

- **센서 차이:** 공개 데이터(DAVIS346, DVXplorer)는 잡음, 감도, 해상도가 우리 센서와 다르다. 우리 센서는 아래쪽 잡음 줄(11.28)도 있다. 잡음 더하기와 미세 조정으로 얼마나 줄어들지는 재기 전에 모른다. 필요하면 잡음 줄을 가린다.
- **데이터가 적음:** 동작당 5~10개는 과적합하기 쉽다. 시험용 2개로는 정확도를 믿을 만하게 말할 수 없다.
- **가만히 있는 사람:** DVS에 거의 보이지 않는다. 오래 가만히 있으면 `none`으로 바뀌고, 다시 움직일 때 `appear`가 찍힐 수 있다.
- **GIL:** 분류 스레드가 캡처 스레드나 웹 스레드를 밀 수 있다. 10절 보드 성능 시험에서 잡히면 분류를 별도 프로세스로 옮긴다.
- **데이터 받기:** DailyDVS-200의 분할·라벨 파일(`train.txt`)은 Baidu에 있다. Google Drive 파일 이름(`C0P3M0S1_…`)에서 클래스를 알 수 없으면 PAF와 우리 녹화만 쓴다. PAF는 Tongji SharePoint에 있고, 로그인 없이 받을 수 있는지 모른다. PAF 데이터의 라이선스는 적혀 있지 않다(저장소 코드는 Apache-2.0).
- **안전:** 넘어짐 녹화와 시험은 매트리스 위에서, 사용자가 판단해서 한다.
- **시야:** 렌즈 화각을 모른다. 방 크기에 맞는 거리와 높이는 첫 녹화 때 정한다.

## 12. 넣지 않는 것

HDMI 결과 표시, 여러 사람 추적, 사람 영역 자르기, MySQL, v2e 합성 데이터(10절 조건 전까지), int8 양자화, 상자 크기 측정과 동시 실행.

## 13. 구현 순서

1. 공개 데이터 받기 확인(PAF, DailyDVS-200 라벨) → 쓸 데이터셋을 정한다.
2. C: `dvs_events()`, `dvs_record()`, `--replay`와 그 시험(Docker).
3. `dvsact.py` `frames()`와 단위 시험. `--act`를 무작위 모델로 켜서 보드 성능 시험(10절).
4. 녹화: `/rec`, 페이지 버튼 → 사용자 녹화 1회차.
5. Mac: aedat 읽기, 데이터 만들기, 학습, ONNX 내보내기, 오프라인 정확도.
6. 보드 배포, live 받아들임 시험. 통과하면 `dvslive.service`에 `--act`를 넣는다.

단계마다 결과를 `docs/E85096-LINUX510-BOOT.md` 새 절과 `docs/BRINGUP_STATUS.md`에 적는다.

## 참고

- Lee 외, "Embedded Real-Time Fall Detection Using Deep Learning For Elderly Care", arXiv 1711.11200, 2017
- Khacef 외, "Privacy-preserving fall detection at the edge using Sony IMX636 event-based vision sensor and Intel Loihi 2 neuromorphic processor", arXiv 2511.22554, 2025
- Miao 외, "Neuromorphic Vision Datasets for Pedestrian Detection, Action Recognition, and Fall Detection", Front. Neurorobot. 13:38, 2019, https://github.com/MSZTY/PAFBenchmark
- Krishnan, Krishnan, "Benchmarking Conventional Vision Models on Neuromorphic Fall Detection and Action Recognition Dataset", arXiv 2201.12285, 2022
- Wang 외, "DailyDVS-200", ECCV 2024, https://github.com/QiWang233/DailyDVS-200
- "TTPOINT: A Tensorized Point Cloud Network for Lightweight Action Recognition with Event Cameras", ACM MM 2023 (arXiv 2308.09993); "EventMamba: Rethinking Efficient and Effective Point-based Networks for Event Camera Classification and Regression", arXiv 2405.06116, https://github.com/rhwxmx/EventMamba
- Yang 외, "EZSR: Event-based Zero-Shot Recognition", CVPR 2025 (arXiv 2407.21616); Wu 외, EventCLIP, arXiv 2306.06354
- Rojas 외, "Low-Cost Neuromorphic Fall Detection Using Synthetic Event Data and Hybrid SNNs", arXiv 2606.18732, 2026
