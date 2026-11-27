# dvshs(DVS + USB 카메라 → 1000 fps) 선행연구와 논문 가치 검토 (2026-11-28)

대상 실험은 [E85096-LINUX510-BOOT.md](E85096-LINUX510-BOOT.md) 11.121절의 `dvshs`다. 웹 검색으로 찾은 논문 초록, 학회 페이지, 제조사 페이지를 근거로 썼다. 논문 본문을 끝까지 읽은 것은 아니다. 그래서 수치는 초록이나 요약에 나온 것만 옮겼다.

## 1. 결론

- **지금 결과만으로는 논문이 되기 어렵다.** 주요 학회(CVPR, ICCV, ECCV, TPAMI)는 말할 것도 없고, 일반 저널에서도 그렇다. 이유는 세 가지다.
  - 방법(EDI, 2019)이 새롭지 않다.
  - 비동축 배치, 시각 맞춤, CPU 실시간 처리 같은 부분도 2023~2024년에 이미 다뤄졌다.
  - 우리 평가에서 합성이 단순 선형 섞기를 이기지 못했다. '1000 fps'라는 핵심 주장을 수치로 받칠 수 없다.
- **새로울 수 있는 것은 두 가지다.**
  - **센서.** NRV RC1S(960×720, 2 kHz 동기식 이벤트 프레임)를 프레임 카메라와 합성한 공개 연구는 찾지 못했다. 이 센서는 화소마다 µs 시각을 주지 않고, 0.5 ms마다 2비트 프레임을 준다. EDI는 이벤트마다 정확한 시각이 있다고 가정하는데, 이 센서에서는 그 가정이 어떻게 깨지는지가 연구거리다.
  - **관찰.** 11.121에서 ON 이벤트는 1 ms 안에 나오고 OFF 이벤트는 15 ms에 걸쳐 퍼졌다. 이 비대칭은 CVPR 2024 지연 보정 연구(Yang et al.)가 다룬 문제와 같은 종류다. 다만 그 연구는 동기식 프레임 DVS를 다루지 않았다.
- **현실적인 목표는 국내 학회나 응용·시스템 저널이다**(MDPI Sensors, IEEE Access, IEEE Sensors Letters 등). 아래 6장의 실험을 더 해야 한다. 그중 가장 중요한 것은 고속 카메라로 찍은 정답 영상과 비주기 장면이다. 그 결과에서도 합성이 선형 섞기를 못 이기면, 논문은 '왜 안 되는가'를 분석하는 글이 된다.

## 2. 우리 실험에서 실제로 잰 것 (11.121)

- **장치**
  - 보드6(Exynos850, Cortex-A55)에 DVS와 USB 카메라를 따로 단다. 두 장치는 광축이 다르고 하드웨어 동기 신호가 없다.
  - DVS는 NRV RC1S(S5KRC1S)다. 960×720, 2000 fps의 2비트 이벤트 프레임을 낸다.
  - USB 카메라는 640×480 YUYV다. 어두운 방이라 14.7 fps로 나왔다.
- **방법**
  - EDI(Pan et al. CVPR 2019) 흐림 제거에 양방향 적분 보간을 더했다.
  - 두 영상은 사영 변환(H) 하나로 맞췄다. 보정은 날개 원판 타원으로 손으로 맞춘 임시 판이다.
  - 화소별 ON/OFF 비율 지도를 쓴다.
  - DVS 녹화의 시계 원점은 투표로 대강 찾고 ±200 µs 안에서 평균 절대차로 좁혔다. 녹화 5번 모두 score 1.000이었다.
- **평가**
  - 장면은 일정하게 도는 선풍기다. 카메라 프레임을 한 장 걸러 빼 두고 정답으로 삼았다(8장, 768,872 화소).
  - 선형 섞기 37.64 dB, 가장 가까운 키 35.28 dB였다. 합성은 가장 좋은 경우(c 0.02)가 37.42 dB, 데모 값(c 0.3)이 34.88 dB였다. **어떤 설정도 선형 섞기를 넘지 못했다.**
  - 대응표를 ±12 px 옮겨도 PSNR이 0.1 dB 안에서만 변했다. 그래서 이 장면과 이 지표로는 c, 노출 T, 정합을 정할 수 없다.
- **속도**
  - 보드 데모는 1초 분량에 66초가 걸렸다(녹화 8, 준비 18, 합성·인코딩 40). 합성은 4스레드로 돌렸고, 같은 보드에서 DVS 실시간 화면도 함께 돌았다.
  - 흐림 제거 단계만은 9.3초였다.

## 3. 선행연구

### 3.1 EDI 계열 (우리 방법의 뿌리)

| 연구 | 내용 | 우리와의 관계 |
|---|---|---|
| Pan et al., CVPR 2019 "Bringing a Blurry Frame Alive…" | EDI. 흐린 한 장과 이벤트로 고속 선명 영상을 만든다. 출력 속도는 원래 프레임의 200배까지 보였다 | 우리 흐림 제거 식 그대로다 |
| Pan et al., TPAMI 2020 "High Frame Rate Video Reconstruction based on an Event Camera"(mEDI) | 여러 프레임을 함께 풀어 깜빡임을 줄이고, 대비 문턱 c를 추정한다 | 우리 양방향 보간과 같은 목적이다. c 추정은 하지 않았다 |
| Lin et al., ICRA 2023 "Fast Event-based Double Integral for Real-time Robotics" | 이벤트가 올 때 바뀐 화소만 다시 더한다. **CPU 단일 코어로 초당 최대 1,300만 이벤트를 실시간 처리한다.** 원래 EDI는 영상 한 장에 1.5초가 걸렸다고 적었다 | 우리는 초당 약 1,700만 이벤트를 4스레드로 처리하는데 실시간의 약 1/40이다. 속도로는 내세울 것이 없다. 이 방식을 쓰면 보드도 빨라질 수 있다 |
| Yang et al., CVPR 2024 "Latency Correction for Event-guided Deblurring and Frame Interpolation" | 이벤트 시각이 늦게 찍히는 것(어두울수록, 빠를수록 심하다)을 매개변수 모델로 보정한다. EDI를 지연에 대해 미분할 수 있게 다시 썼다 | 우리 OFF 이벤트가 15 ms에 걸쳐 퍼지는 현상과 바로 이어진다. 동기식 프레임 DVS에 적용한 사례는 찾지 못했다 |

### 3.2 프레임과 이벤트를 섞는 필터

| 연구 | 내용 | 우리와의 관계 |
|---|---|---|
| Brandli, Muller, Delbruck, ISCAS 2014 | DAVIS(240×180)의 프레임과 이벤트로 2 kHz 영상을 실시간으로 복원했다. 평균 오차는 0.5 DN이다 | '느린 프레임 + 이벤트 = 고속 영상'의 원조다. 같은 칩(동축)이었다 |
| Scheerlinck et al., ACCV 2018 complementary filter | 이벤트마다 log 밝기를 갱신하고, 프레임이 오면 그 값으로 끌어당긴다. 계산이 가볍다 | 우리 비교 기준에 넣어야 할 고전 방법이다(지금은 없다) |
| Wang et al., Asynchronous Kalman Filter(2021), Asynchronous Linear Filter(2023) | 하이브리드 이벤트·프레임 카메라의 비동기 필터다. 화소별 문턱도 프레임으로 보정한다 | 화소별 비율 지도와 같은 문제를 더 체계적으로 푼다 |

### 3.3 학습 기반 이벤트 프레임 보간 (주류)

- **Time Lens(CVPR 2021):** 하드웨어로 동기화한 Prophesee Gen4 720p와 FLIR RGB 카메라로 HS-ERGB 자료를 만들었다. 두 카메라는 스테레오 배치다.
- **Time Lens++(CVPR 2022):** 빔 분할기로 두 카메라를 동축으로 맞춘 BS-ERGB 자료를 만들었다.
- **Revisiting Event-based VFI(IROS 2023):** 실제 이벤트의 잡음과 밀도를 고려하면 정확도가 오른다고 했다.
- **TimeLens-XL(ECCV 2024):** 1280×720에서 27 fps를 냈다. 저자들은 이것을 '첫 실시간'이라고 했는데, RTX 3090 GPU 기준이다.
- **EVDI(CVPR 2022)와 EVDI++(2025):** 흐림 제거와 보간을 함께 하고, EVDI++는 자기 지도 학습이다.
- **EventDiff(2025)와 DiT 기반(2026):** 확산 모델 계열이다.
- **정리:** 이 분야는 큰 학습 모델과 동축 또는 동기화된 장비가 기준이다. 학습 없는 EDI가 선형 섞기보다 못한 결과로는 이 흐름에 낄 수 없다.

### 3.4 광축이 다른(스테레오) 이벤트 + 영상

- **SEVFI-Net(arXiv 2307.08228, IEEE TMM 투고):** SilkyEvCam과 RealSense D455를 8 cm 떨어뜨려 놓고, 시차를 특징 공간에서 맞춰 보간한다. SEID 자료를 공개했다. 저자들은 스테레오 정렬(rectification)만으로는 어긋남을 없애기 어렵다고 썼다.
- **St-EDNet(arXiv 2309.09513, 2023):** 스테레오 이벤트 흐림 제거에서 시차를 학습한다. 기존 방법은 두 영상이 화소 단위로 맞는다고 가정해서, 시차가 크면 artifact가 생긴다고 지적했다.
- **우리와의 관계:** 우리는 평면 사영 변환 하나만 쓴다. 깊이가 다른 물체에서는 맞지 않는다. 11.121에서 관찰한 시차가 바로 이것이다. 이 부분은 새롭지 않고, 기존 연구보다 약하다.

### 3.5 하드웨어 동기 없는 시각 맞춤

- **EF-Calib(arXiv 2405.17278, 2024):** 이벤트 카메라와 프레임 카메라의 내부·외부 변수와 시각 차이를 연속 시간 B-spline 궤적으로 함께 추정한다.
- **광폭 스테레오 이벤트 카메라 동기화(arXiv 2309.16990)**, **eKalibr-Stereo(2025)**, 그리고 차량·IMU용 운동 상관 방법들이 있다.
- **우리와의 관계:** 우리가 맞춘 것은 '녹화 파일의 시각 0'과 '실시간 디코더 시계'다. 이것은 우리 소프트웨어 구조에 묶인 문제다. 카메라와 DVS 사이의 시각은 USB 카메라 타임스탬프(SOE)와 시스템 단조 시계를 믿었고, 따로 재지 않았다. 그래서 일반적인 공헌으로 내세우기 어렵다.

### 3.6 상용과 센서

- **Prophesee + Qualcomm(2023년 발표, 2024년 양산 준비):** Snapdragon 8 Gen 3에서 프레임 센서와 이벤트 센서를 동기화해 흐림을 없앤다(Metavision Deblur). 기사들은 RGB 카메라 하나와 초점거리가 맞아야 한다는 제약을 적었다. 출시된 폰은 확인하지 못했다.
- **AlpsenTek ALPIX-Eiger + OPPO + Qualcomm(2024):** 8 MP 화소 안에서 영상과 이벤트를 함께 받는 센서다. 목표로 흐림 제거, 해상도 향상, **슬로모션 복원**을 들었다.
- **NRV(NeuroReality Vision) RC1S:** 제조사 문서에 960×720, 2,000 fps 이상, 90 dB 이상이 나온다. CIS + DVS 조합 구성도 소개되어 있다. 독립된 평가 논문은 찾지 못했다.
- **Park, Kim, Ko, "Hybrid Event–Frame Sensing for Human-Perceptual Imaging and Machine Vision"(Sensors, DOI 10.3390/s26165127):**
  - 하이브리드 구조를 여섯 가지로 나눈다. 두 카메라, 광학 정렬, 화소 공유, CIS–DVS 적층, 동종 화소, 이벤트만으로 복원.
  - 적층 CIS–DVS가 가장 균형이 좋다고 결론지었다.
  - 우리는 이 분류의 '두 카메라 시스템'에 해당한다.
  - 저자들이 NRV와 관계가 있는지는 확인하지 않았다. 협력 측이라면 공동 연구 상대가 될 수 있다.

### 3.7 임베디드

- **2025년 조사 논문(arXiv 2509.09971):** 모바일·임베디드용 가벼운 구조를 남은 연구 방향으로 꼽았다.
- **실측 보고:** Jetson 같은 엣지 장치에서 이벤트 보간을 잰 논문은 찾지 못했다.
- **FPGA:** 이벤트 처리 가속기(ESDA, HOMI 등)는 있다. 이벤트로 흐림을 없애는 FPGA 구현은 찾지 못했다.
- **공백과 한계:** 그래서 '휴대폰급 SoC에서 도는 하이브리드 고속 영상'은 아직 비어 있는 자리다. 하지만 Fast EDI가 이미 CPU 단일 코어 실시간을 보였다. 우리가 내세우려면 적어도 그만큼 빨라야 한다.

## 4. 요소별 새로움

| 요소 | 새로운가 | 근거 |
|---|---|---|
| EDI 흐림 제거 + 양방향 보간 | 아니다 | Pan 2019/2020, EVDI 2022 |
| 광축이 다른 두 카메라 | 아니다(더 약하다) | Time Lens HS-ERGB, SEVFI-Net, St-EDNet. 우리는 평면 H 하나다 |
| 하드웨어 동기 없는 시각 맞춤 | 약하다 | EF-Calib, 지연 보정 CVPR 2024. 우리 방법은 우리 소프트웨어 구조에 묶여 있다 |
| 임베디드 CPU에서 돌리기 | 지금은 아니다 | Fast EDI가 CPU 단일 코어 실시간. 우리는 실시간의 약 1/40 |
| **동기식 2 kHz 프레임 DVS(RC1S)와 합성** | **그럴 수 있다** | 공개 연구를 찾지 못했다(검색 범위 안에서) |
| **ON/OFF 시간 응답 비대칭(1 ms 대 15 ms)이 EDI를 깨는 분석** | **그럴 수 있다** | 지연 보정 연구는 있으나 이 센서 형식은 없다 |
| 주기 장면에서 프레임 걸러 빼기 평가가 둔하다는 관찰 | 작은 공헌이다 | 평가 방법 논의의 일부로만 쓸 수 있다 |

## 5. 지금 상태로 막히는 것

1. **핵심 결과가 부정적이다.** 합성이 선형 섞기를 못 이겼다. 학회 심사자는 첫 표에서 이것을 본다.
2. **정답 영상이 없다.** 15 fps 프레임을 빼 두고 맞히는 평가는 68 ms 간격만 시험한다. 1 ms 영상이 맞는지는 시험하지 못한다.
3. **장면이 하나다.** 그것도 주기적인 선풍기라서 c, T, 정합이 결과를 거의 바꾸지 않았다.
4. **보정이 임시다.** 회전을 0으로 가정했고, 바둑판 보정을 하지 않았다.
5. **비교 대상이 둘뿐이다.** 선형 섞기와 가장 가까운 키만 있다. complementary filter, mEDI, 학습 모델(예: TimeLens-XL)과 비교하지 않았다.
6. **노출 T를 재지 않았다.** 0.95 × 간격으로 가정했을 뿐이다.

## 6. 논문이 되려면 (순서대로)

1. **정답 영상.** 고속 카메라나 휴대폰 슬로모션(240~960 fps)을 같은 장면에 함께 찍는다. 같은 화면에 LED 깜빡임 같은 공통 시각 표식을 넣어 셋을 맞춘다. 그러면 1 ms 영상의 PSNR/SSIM/LPIPS를 직접 잴 수 있다.
2. **장면을 늘린다.** 주기가 없는 움직임(손, 떨어지는 공, 튀는 물), 밝기 몇 단계, 깊이가 다른 물체를 넣는다.
3. **보정을 제대로 한다.** 태블릿 바둑판으로 H를 구하고, 깊이가 다른 장면에서 평면 가정의 오차를 잰다. 노출 T는 UVC로 고정하거나, LED로 잰다.
4. **비교 대상을 늘린다.**
   - 선형 섞기, 가장 가까운 키
   - Scheerlinck complementary filter
   - EDI/mEDI(c 추정 포함)
   - 학습 모델 하나(TimeLens-XL 또는 CBMNet, Mac GPU에서). 그 모델의 입력 형식에 맞게 이벤트를 바꿔 넣는다.
5. **센서 분석(새로운 부분).**
   - RC1S의 ON/OFF 시간 응답을 LED 계단 자극으로 잰다.
   - 0.5 ms 양자화와 2비트 형식이 EDI 오차에 얼마나 기여하는지 합성 자료로 나눠 본다.
   - 지연 보정(CVPR 2024 방식)을 적용해 결과가 바뀌는지 본다.
6. **임베디드 속도와 전력.** Fast EDI 방식의 증분 계산으로 바꾸고, E850과 Jetson Orin Nano에서 실시간 배율과 전력을 잰다. 11.116~11.118의 E850–Jetson 비교 자료와 이을 수 있다.

1~3만 해도 '결과가 맞는지'에 답할 수 있다. 1~5를 하면 새로움이 생긴다.

## 7. 투고처 후보 (마감은 확인하지 않음)

| 수준 | 후보 | 필요한 것 |
|---|---|---|
| 지금 + 조금 | 국내 학회(대한전자공학회, 한국방송·미디어공학회 등) 시스템·데모 발표 | 6장의 1~3 |
| 중간 | MDPI Sensors, IEEE Access, IEEE Sensors Letters(응용·시스템) | 6장의 1~5 |
| 높음 | CVPR Event-based Vision 워크숍(2025년은 6월 Nashville, 2026년 CVPR 판은 확인 못 함), ACCV 2026 AECV 워크숍(2026-12-15 Osaka, 마감은 지났을 가능성이 크다), ECCV 2026 event 멀티모달 워크숍 | 6장 전부 + 센서 분석에서 분명한 새 결과 |
| 주요 학회 | CVPR/ICCV/ECCV 본 학회 | 새 방법이 필요하다(예: 동기식 프레임 DVS용 지연·양자화 모델). 지금 방향으로는 어렵다 |

## 8. 검색으로 확인하지 못한 것

- REFID(CVPR 2023, 흐림 제거와 보간을 함께 하는 연구)는 검색에서 나오지 않았다.
- Samsung DVS Gen4 같은 다른 동기식 이벤트 프레임 센서를 프레임 카메라와 합성한 연구가 있는지는 끝까지 확인하지 못했다. '동기식 프레임 DVS는 새롭다'는 판단은 이 검색 범위 안에서만 맞다. 투고 전에 Samsung SAIT와 NRV 쪽 논문, 특허를 따로 찾아봐야 한다.
- 투고처의 2027년 마감과 범위는 확인하지 않았다.

## 출처

- Pan et al., CVPR 2019: https://openaccess.thecvf.com/content_CVPR_2019/papers/Pan_Bringing_a_Blurry_Frame_Alive_at_High_Frame-Rate_With_an_CVPR_2019_paper.pdf
- Pan et al., mEDI: https://arxiv.org/abs/1903.06531
- Lin et al., Fast EDI, ICRA 2023: https://arxiv.org/abs/2305.05925
- Yang et al., Latency Correction, CVPR 2024: https://openaccess.thecvf.com/content/CVPR2024/html/Yang_Latency_Correction_for_Event-guided_Deblurring_and_Frame_Interpolation_CVPR_2024_paper.html
- Brandli et al., ISCAS 2014: https://ieeexplore.ieee.org/document/6865228
- Scheerlinck et al., ACCV 2018: https://arxiv.org/abs/1811.00386
- Asynchronous Kalman Filter: https://arxiv.org/abs/2012.05590 , Asynchronous Linear Filter: https://arxiv.org/abs/2309.01159
- Time Lens: https://arxiv.org/abs/2106.07286 , Time Lens++: https://arxiv.org/abs/2203.17191
- Revisiting Event-based VFI: https://arxiv.org/abs/2307.12558
- TimeLens-XL, ECCV 2024: https://www.ecva.net/papers/eccv_2024/papers_ECCV/html/11208_ECCV_2024_paper.php
- EVDI, CVPR 2022: https://arxiv.org/abs/2203.12178 , EVDI++: https://arxiv.org/abs/2509.08260
- EventDiff: https://arxiv.org/abs/2505.08235 , DiT 기반 EVFI: https://arxiv.org/abs/2608.10479
- SEVFI-Net: https://arxiv.org/abs/2307.08228
- St-EDNet: https://arxiv.org/abs/2309.09513
- EF-Calib: https://arxiv.org/abs/2405.17278 , 스테레오 이벤트 동기화: https://arxiv.org/abs/2309.16990 , eKalibr-Stereo: https://arxiv.org/abs/2504.04451
- 조사 논문: https://arxiv.org/abs/2509.09971 , https://arxiv.org/abs/2505.05488
- Prophesee + Qualcomm: https://venturebeat.com/ai/prophesee-teams-with-qualcomm-on-faster-event-based-smartphone-cameras , https://www.androidauthority.com/prophesee-3291007/
- AlpsenTek + OPPO + Qualcomm: http://image-sensors-world.blogspot.com/2024/01/oppo-alpsentek-and-qualcomm.html
- NRV RC1S 문서: https://nrvcorp.github.io/docs
- Park, Kim, Ko, Sensors 리뷰: https://doi.org/10.3390/s26165127
- FPGA 이벤트 처리(ESDA, HOMI): 검색 요약에서만 봤고, 논문 주소는 확인하지 않았다
- CVPR 2025 Event-based Vision 워크숍: https://tub-rip.github.io/eventvision2025 , ACCV 2026 AECV: https://app.notion.com/p/Call-for-Papers-AECV-2026-0d7ccea98d4d4b18947048d81ba46937 , ECCV 2026 워크숍: https://eventbasemultimodalvision.github.io/

## 9. 탑저널을 노린다면 (같은 날 추가 검색)

**흐름(검색으로 확인한 것).**
- 업계는 카메라 두 대보다 한 칩 안의 하이브리드로 가고 있다.
  - Sony: 35.6 MP RGB + 이벤트 화소, ISSCC 2023, 최대 10K 이벤트 프레임율.
  - OmniVision: 15 MP CIS + 1 MP EVS, 3층 적층.
  - 위 두 센서는 이벤트를 화소별 µs 시각이 아니라 '이벤트 프레임'으로 낸다. RC1S와 같은 동기식 계열이다.
- 연구도 그쪽으로 갔다.
  - MIPI 2024 HybridEVS 디모자이크 챌린지, MIPI 2025 Hybrid EVS 흐림 제거 챌린지(ICCV 2025 워크숍, Sony 참여).
  - Lu et al. "Hybrid Event-Frame Sensors: Modeling, Calibration, and Simulation"(arXiv 2511.18037, ECCV 2026): 한 칩 하이브리드의 잡음을 모델로 만들고 보정한다. 시뮬레이터 H-ESIM을 함께 낸다.
- 현미경 응용도 활발하다: Inter-event Interval Microscopy(Photonics Research 2025, 800 Hz), 국내 GIST·SNU·KAIST의 생체 뇌 혈관 kHz 영상.

**우리만 가진 것.** RC1S(동기식 2 kHz 2비트 프레임)와 제조사(NRV) 접근, 그리고 ON 1 ms / OFF 15 ms 비대칭 관찰. 둘째는 E850(Mali GPU)과 Jetson의 엣지 실측 환경. 셋째는 CX3 현미경 모듈.

**방향 후보.**

| 방향 | 내용 | 투고처 | 위험 |
|---|---|---|---|
| A. 동기식 이벤트 프레임 센서의 물리 모델 + 복원(추천) | RC1S의 시간 응답(ON/OFF 지연·잔향, 0.5 ms 양자화, 화소별 문턱)을 LED·CIS로 재는 보정법을 만든다. 그 모델을 넣은 EDI 또는 시뮬레이터로 학습한 망을 만든다. 정답은 고속 카메라 영상이다. 같은 센서 계열(Samsung식, Sony 이벤트 프레임)에도 일반화된다는 것을 보인다 | TPAMI, IJCV, TIP, TCI | H-ESIM(ECCV 2026)과 겹친다. 그쪽은 잡음 모델이고, 우리는 시간 응답과 동기식 읽기로 갈라야 한다. 센서 내부 자료가 없으면 약해진다 |
| B. 엣지 실시간 하이브리드 고속 영상 | 모바일 GPU(E850 Mali, Jetson)에서 실시간·전력 측정을 한다. Fast EDI(CPU 한 코어 실시간)와 TimeLens-XL(RTX 3090 27 fps) 사이의 빈자리다 | TCSVT, TMC급(탑보다 한 단계 아래) | 방법이 고전적이면 새로움이 약하다 |
| C. DVS + CIS 고속 현미경 | 미세유체, 세포 운동, 혈류 같은 생물 현상을 kHz로 보고 새 생물학적 결과를 낸다 | Light: Sci. Appl., Optica, Nat. Commun. | 광학 개조, 생물 협력자, 긴 기간이 필요하다 |

**하지 말 것.**
- HS-ERGB·BS-ERGB 위에서 학습 보간 SOTA 경쟁을 하지 않는다. 이미 붐비고 큰 GPU가 필요하다.
- '카메라 두 대 구성'을 새로움으로 내세우지 않는다.

**A의 첫 시험(1~2주, 싸다).**
1. LED 계단 자극으로 RC1S의 ON/OFF 지연과 퍼짐을 밝기별로 잰다.
2. 그 응답을 EDI 적분에 넣는다.
3. 비주기 장면을 휴대폰 슬로모션 정답과 함께 찍고, 선형 섞기를 이기는지 본다. 이기면 A로 간다. 못 이기면 B나 C로 돌린다.

추가 출처:
- Hybrid Event-Frame Sensors (H-ESIM): https://arxiv.org/abs/2511.18037 , https://yunfanlu.github.io/HESIM
- MIPI 2024 HybridEVS 디모자이크: https://arxiv.org/abs/2405.04867 , MIPI 2025 Hybrid EVS 흐림 제거: https://openaccess.thecvf.com/content/ICCV2025W/MIPI/papers/Wu_MIPI_2025_Challenge_on_Deblurring_for_Hybrid_EVS_Camera_Methods_ICCVW_2025_paper.pdf
- Sony·OmniVision ISSCC 2023 하이브리드: https://image-sensors-world.blogspot.com/2022/12/2023-international-solid-state-circuits.html , https://image-sensors-world.blogspot.com/2023/12/omnivision-15mp1mp-hybrid-rgbevent.html
- Samsung 1280×960 DVS: https://www.researchgate.net/publication/349293363_A_1280960_Dynamic_Vision_Sensor_with_a_495-mm_Pixel_Pitch_and_Motion_Artifact_Minimization
- Inter-event Interval Microscopy: https://arxiv.org/abs/2504.04924 , 생체 뇌 혈관 kHz 영상(보도자료): https://www.eurekalert.org/news-releases/1124490

## 10. 온디바이스 AI를 차별점으로 (같은 날 추가 검색)

### 10.1 선행연구

| 연구 | 내용 | 우리와 다른 점 |
|---|---|---|
| Gehrig & Scaramuzza, Nature 2024 "Low-latency automotive vision with event cameras"(DAGr) | 20 fps RGB + 이벤트 카메라로 5,000 fps 카메라만큼의 지연을 45 fps 대역폭으로 얻었다. 정확도는 떨어지지 않았다. CNN(영상)과 비동기 GNN(이벤트)을 함께 쓴다 | **DVS+CIS 하이브리드가 Nature에 실린 선례다.** 다만 GPU에서 돌렸고, 비동기 µs 이벤트를 썼다 |
| Lenz et al. 2022 "A Framework for Event-based Computer Vision on a Mobile Device"(arXiv 2205.06836) | Android 폰에서 이벤트 처리와 TFLite 복원을 하고, 이벤트 발생부터 결과까지 지연을 쟀다 | 폰에서 이벤트 AI를 돌린 선례다. 하이브리드도 아니고, GPU 실측이나 열 제한 분석도 없다 |
| Ev-Edge(arXiv 2403.15717, 2024) | 이벤트 신경망을 Jetson 같은 상용 엣지 장치에서 효율적으로 돌린다. 이벤트 프레임이 아주 희소하고, 희소 텐서 변환 비용이 크다고 지적했다 | Jetson급 엣지다. 우리는 그보다 아래인 NPU 없는 휴대폰 SoC다 |
| Park, Kim, Ko, Chang, Sensors 2026 "Event-Based Machine Vision for Edge AI Computing"(DOI 10.3390/s26030935) | DVS로 가정용 사람 검출, 자세, 손 모양을 엣지에서 처리한다. 사생활 보호, 상시 동작, 검출·자세 11배 빠름 | 3.6절 리뷰와 같은 저자군이다. 협력 측일 수 있으나 확인하지 않았다. CIS 융합이나 SoC 실측은 아니다 |
| 이벤트로 깨우는 RGB 노드(arXiv 2608.23192, 2026), sub-mW 상시 감시 노드(arXiv 1705.00221) | 이벤트 센서가 지켜보다가 움직임이 있을 때만 RGB와 검출기를 켠다. 센서 전력이 최저 277 µW였다 | 'DVS가 CIS를 깨운다'는 생각 자체는 이미 있다. 마이크로컨트롤러급이고 고속 영상은 없다 |
| 뉴로모픽 칩(Loihi, SpiNNaker2, PULP Kraken 4.7 mW 제스처 등) | DVS 제스처 인식을 mW급으로 한다 | 전용 칩이다. 우리는 이미 쓰이는 범용 SoC다 |
| Wang et al., ACM CSUR "Event Camera Meets Mobile Embodied Perception"(arXiv 2503.22943) | 모바일 이벤트 카메라 조사. 잡음, 데이터량, 자원 제약을 과제로 꼽았다 | 열 제한이나 공유 메모리를 다루는지는 본문을 확인하지 못했다 |
| MobiCom 2023 드론 회피, 2024 EventTracker(이벤트+깊이), 2025 HawkEye(이벤트+LiDAR) | 이벤트 카메라 시스템 논문이 MobiCom 같은 모바일 최상위 학회에 해마다 실린다 | 투고처가 열려 있다는 근거다 |

**검색으로 본 빈자리.**
- 휴대폰 GPU에서 이벤트 신경망의 시간과 전력을 잰 공개 연구는 찾지 못했다.
- 동기식 이벤트 프레임(RC1S, Sony 이벤트 프레임)이 GPU·SIMD에 잘 맞는다는 점을 실측으로 보인 연구도 없었다. 문헌은 "조밀한 프레임은 GPU에 맞고, 비동기 이벤트를 프레임으로 바꾸면 희소성 이득을 잃는다"고만 말한다(Ev-Edge, AEStream).

### 10.2 우리 환경에서 이미 잰 것 (논문의 '플랫폼' 절이 된다)

| 자산 | 실측 | 절 |
|---|---|---|
| NPU 없는 보급형 폰 SoC(Exynos850, A55×8, Mali-G52 MP1) | 오픈 GPU 스택(panfrost GLES 3.1 + panvk Vulkan, kbase shim) 위에서 TFLite GL·NCNN Vulkan이 CPU FP32와 같은 정확도로 돈다. GPU 최고 38 GFLOPS | 11.117, 11.118 |
| 2 kHz DVS 실시간 수신·해독 | A55 코어 하나가 초당 6,570만~7,060만 이벤트를 해독한다. 잡음 필터는 1,640만~1,690만이다 | 11.111 |
| DVS 온디바이스 AI | 보드6이 DVS 실시간 화면을 띄운 채 행동 분류(34 ms)와 얼굴 검출(YuNet 약 120 ms)을 CPU4~6에서 함께 돌린다. 단계마다 코어를 나눠 묶었다 | 11.112 |
| 같은 DVS·같은 바이너리를 Jetson Orin Nano에서 | 이벤트 하나당 E850 대비 Jetson이 해독 1.5배, 필터 3.7배 빠르다. AI 런타임 4모델 × 8설정 표가 있다 | 11.111, 11.115, 11.116 |
| 모바일 SoC의 현실 | GPU만 돌려도 CPU4~7 클럭이 −17%(91°C)다. DRAM 복사는 CPU 추론을 25~35% 늦춘다. CPU·GPU 동시 실행 순이득은 대략 "GPU÷CPU 처리량 − 6~12%"다. 층 나누기는 늘 손해였다 | 11.118 |
| CIS | USB UVC 카메라, CX3 현미경(8/16/50 MP), OV5647(MIPI) | 11.121, 메모리 |
| 사람이 볼 출력 | 온디바이스 EDI 합성으로 1 ms 슬로모션(1 s 분량에 66 s) | 11.121 |
| 재현성 | 보드 9대, SD 카드로 같은 시스템 설치(약 4분) | 11.120 |

**없는 것.**
- **전력 실측.** GPU 전력은 N/A였다. 탑급 온디바이스 논문에서는 필수다. DC 12 V 전원선에 전력계(INA226 같은)를 꽂아야 한다.
- 정답 라벨이 있는 과제와 데이터셋.
- 장시간 열 평형 시험.

### 10.3 추천하는 논문 틀

**제목 방향:** "NPU 없는 보급형 폰 SoC에서 동기식 이벤트 프레임 DVS + CIS 하이브리드 지각: 센서부터 판단까지"

| 가설 | 시험 | 근거로 쓸 우리 자산 |
|---|---|---|
| H1. 동기식 2비트 이벤트 프레임은 비동기 이벤트와 달리 조밀한 SIMD·GPU 커널에 바로 맞는다. 그래서 보급형 SoC가 2 kHz를 낮은 전력으로 처리한다 | 비트 묶음 NEON·GLES 커널 대 이벤트 목록 처리를 비교한다. 이벤트당 ns와 J, 열 평형 상태에서 잰다 | 11.111 해독기, GPU 스택 |
| H2. DVS가 CIS와 DNN을 언제·어디를 돌릴지 정하면, 같은 정확도에서 에너지와 지연이 줄어든다(DAGr의 온디바이스판) | 프레임만 쓰는 경우(5/15/30 fps)와 DVS로 깨우기·ROI를 비교한다. 지표는 판단 지연(빛에서 결과까지), 에너지/판단, 정확도 | 행동 분류·얼굴 파이프라인, USB 카메라, 11.116 런타임 표 |
| H3. 열과 DRAM 경합을 아는 배치(A55 클러스터 둘 + Mali)가 단순 배치보다 지속 처리량이 높다 | 30분 이상 장시간으로 잰다 | 11.118의 열·경합 실측, 코어 묶기 |
| (부) 같은 센서쌍으로 사람이 볼 1 ms 슬로모션도 만든다 | 정답 고속 영상과 비교한다 | dvshs |
| 일반화 | 같은 코드로 Jetson Orin Nano(NRV DVS)에서 다시 잰다 | 11.108~11.116 |

**과제(라벨이 필요하다):**
- 고속 물체(공, 손) 검출·추적의 지연.
- 집 안 행동 분류(이미 `tools/host/act`가 있다). Park et al. 2026과 겹치니 하이브리드와 SoC 실측으로 갈라야 한다.

**투고처:**
- **모바일 시스템 최상위:** ACM MobiSys, MobiCom, SenSys, IMWUT. 이벤트 카메라 논문이 해마다 실린다.
- **저널:** IEEE TMC, IEEE IoT Journal.
- 숫자가 아주 크면 Nature Electronics, Nature Communications도 볼 수 있다. DAGr가 Nature에 실렸으니 하이브리드 주제 자체는 받아들여진다.

**약점과 반론 준비.**
- "Exynos850은 2020년 칩이고 NPU가 없다." 답: 보급형 SoC가 출하량 대부분이다. NPU가 없으니 CPU+GPU 배치 문제가 생긴다. Jetson과도 비교한다.
- "센서가 한 회사 것이다." 답: Sony·OmniVision 하이브리드도 이벤트 프레임으로 낸다. 일반화 논의를 넣는다.
- "DVS가 CIS를 깨우는 것은 이미 있다." 답: 그 연구는 마이크로컨트롤러와 상시 감시다. 우리는 kHz 고속 판단과 폰 SoC 실측이다.

추가 출처:
- DAGr, Nature 2024: https://www.nature.com/articles/s41586-024-07409-w , 코드 https://github.com/uzh-rpg/dagr
- Lenz et al. 2022: https://arxiv.org/abs/2205.06836
- Ev-Edge: https://arxiv.org/abs/2403.15717
- Park et al., Sensors 2026: https://www.mdpi.com/1424-8220/26/3/935
- 이벤트로 깨우는 RGB 노드: https://arxiv.org/abs/2608.23192 , sub-mW 노드: https://arxiv.org/abs/1705.00221
- CSUR 조사: https://arxiv.org/abs/2503.22943
- MobiCom 2023 드론: https://dl.acm.org/doi/abs/10.1145/3570361.3613269
- FPGA GCN(ZCU104, 초당 1,330만 이벤트): https://arxiv.org/abs/2406.07318
- AEStream: https://arxiv.org/abs/2212.10719
