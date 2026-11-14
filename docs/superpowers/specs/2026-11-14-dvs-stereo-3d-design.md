# DVS 두 대로 3D 영상 만들기 (설계, 2026-11-14)

사용자와 대화로 정한 설계다(1부 구성, 2부 시각 맞추기·보정, 3부 3D·페이지·시험). 최종 목표는 SLAM이다. 이 문서는 그 첫 과제인 "두 센서로 3D 영상 만들기"만 다룬다.

## 1. 목표와 범위

- 목표: 보드1과 보드2의 DVS(S5KRC1S, 960×720)가 같은 장면을 보고, 별도 웹 페이지에 3D 점구름이 나온다.
- 1단계(이 문서): 계산은 Mac에서 한다. 보드는 이벤트 프레임만 내준다. **연산량을 먼저 잰다**(Mac M6, 보드 A55). 그 결과로 경량화 방안을 세운다.
- 2단계(나중, 별도 계획): 경량화한 계산을 보드1의 dvslive 스레드로 옮긴다. 페이지는 보드1 8080/3d.
- 하지 않는 것: SLAM(과제 2), 렌즈·센서 교체(사용자가 나중에), HDMI 출력, 인증.

## 2. 지금 배치 (2026-11-14 사용자 설명과 화면 측정)

- 두 센서가 미니 선풍기를 본다. 간격 15 cm. 보드1(광학렌즈)은 선풍기 바로 앞, 보드2(기본렌즈)는 선풍기 왼쪽에서 30° 돌려 4 cm 뒤.
- 사용자에 따르면 선풍기 크기는 두 화면에서 비슷하다. 보드1은 선풍기 안쪽에서만 이벤트가 나온다(측정: 이벤트 덩어리 보드1 90×114 px, 보드2 385×387 px). 가설: 광학렌즈 쪽이 어두워 빠른 바깥 날개를 놓친다(확인 안 함). 그 부분의 3D 점은 빠진다. 렌즈·조명은 나중에 바꾼다.
- 보정한 뒤에는 두 센서가 움직이면 안 된다(움직이면 다시 보정).

## 3. 구성

```
보드1 dvslive ── GET /evframe ──┐
보드2 dvslive ── GET /evframe ──┼─ Wi-Fi ─→ Mac tools/host/dvs3d.py
                                │            시계 차이 → 같은 시각 T의 창 요청 → 정류 → SGBM → 3D 점
                                │            http://localhost:8090/ (three.js 점구름, 두 화면)
                                │            받은 쌍 녹화 → bench(같은 코드를 Mac·보드에서 단계별 시간)
```

### 3.1 보드: libdvsview (dvsview.c)

- **display→monotonic 대응.** 이벤트 시각은 보드마다 스트림 시작부터 세는 display clock(µs, `struct ev.t`는 uint32, 71.6분마다 넘어감)이다. `dvs_ingest()`가 버퍼마다 `now_us() - S.t`(CLOCK_MONOTONIC − display)를 최근 64버퍼 링에 넣는다. 그 최솟값이 대응값이다(가장 덜 늦은 버퍼). 두 보드는 하드웨어·설정이 같으므로 일정한 처리 지연은 서로 상쇄된다(가정, 손전등 시험으로 확인).
- **`int dvs_surface(uint32_t t_end, int win_us, uint8_t *dst, uint32_t *info)`**: 링에서 display 시각 `(t_end - win_us, t_end]`의 이벤트로 480×360 이미지(2×2 묶음)를 만든다. 화소 값은 그 화소의 가장 늦은 이벤트로 정한다: `1 + 254 × (win − age) / win`(0 = 이벤트 없음). 극성은 쓰지 않는다. 반환값은 쓴 이벤트 수, `t_end`가 아직 오지 않았으면 -1, 창의 시작이 안전 범위(최신에서 RING/2) 밖이면 -2, 인자가 틀리면 -3. `info`에 newest(display µs), disp2mono(µs), cost(µs)를 넣는다. 시각 비교는 `(int32_t)(a - b)`로 해서 uint32 넘어감에 안전하다.
- 계산은 요청을 받은 웹 스레드(CPU7)에서 한다. 새 스레드는 없다. 링 쓰기와 겹치므로 읽은 다음 범위를 다시 확인한다(`dvs_events()`와 같은 RING/2 규칙).

### 3.2 보드: dvsctl.py

- `GET /evframe?t_end=<display µs>&win=<µs>`: 본문은 480×360 바이트를 zlib(level 1)로 압축한 것이다. 헤더: `X-Dvs-Newest`, `X-Dvs-Disp2Mono`, `X-Dvs-Mono`(응답할 때 보드 CLOCK_MONOTONIC µs), `X-Dvs-Events`, `X-Dvs-Cost-Us`(C 계산 + 압축). `win`은 1000~200000, 기본 20000.
- `t_end`를 빼면 이미지 없이 헤더만 돌려준다(시계 차이 재기, newest 알기).
- 오류: 425 아직 안 옴, 410 링에서 밀려남, 400 인자. 기존 경로처럼 4xx는 `Connection: close`.

### 3.3 Mac: tools/host/dvs3d.py

- **BoardClock:** 보드마다 keep-alive 연결 하나. 요청 보낸 Mac 시각 t0, 받은 시각 t1, 보드 `X-Dvs-Mono`로 `offset = mono − (t0 + t1)/2`를 구한다. 최근 30초 표본 중 왕복이 가장 짧은 것을 쓴다. 왕복 최솟값이 20 ms를 넘으면 "동기 불확실"이다.
- **짝:** Mac 시각 T = 지금 − 80 ms를 각 보드 display 시각으로 바꾼다(`T + offset − disp2mono`). 두 보드에 동시에(스레드 2개) 요청한다. 한쪽이라도 4xx·시간 초과면 그 쌍은 버리고 횟수를 센다.
- **파이프라인:** zlib 풀기 → 정류(`cv2.remap`, 보정 파일이 없으면 그대로) → `StereoSGBM`(좌우 검사 `disp12MaxDiff`, 반점 제거) → 왼쪽(보드1) 이벤트 화소만 남김 → `reprojectImageTo3D`(보정이 있을 때) → 최대 2만 점(무작위 솎기) → float32 x,y,z + uint8 값. 단계마다 시간을 잰다.
- **페이지:** `http://localhost:8090/`(`tools/host/dvs3d.html`). three.js(cdnjs, 고정 판)로 점구름(거리에 따라 색, 마우스 회전), 두 정류 화면(PNG), 단계별 시간, 시계 상태, 버린 쌍 수. 점은 `GET /points`(바이너리), 상태는 `GET /stats`(JSON).
- **녹화:** `--rec DIR`이면 쌍마다 `np.savez_compressed`(두 이미지, 두 display 시각, Mac 시각) 파일 하나.
- **bench:** `dvs3d.py bench DIR`이 녹화를 같은 파이프라인으로 돌려 단계별 중간값·p90(ms)을 출력한다. Mac과 보드(`python3-opencv` 필요)에서 같은 코드로 돈다. `--threads N`으로 OpenCV 스레드 수를 정한다.

### 3.4 보정: tools/host/dvs3d-calib.py (사용자가 있을 때 실행)

- Mac 화면(또는 태블릿)에 바둑판을 띄우고 초당 10번 흑백을 뒤집는다(`tools/host/dvs3d-checker.html`, 브라우저 전체 화면). 두 보드에서 같은 T의 100 ms 창을 받아 `cv2.findChessboardCornersSB`로 모서리를 찾는다.
- 두 센서가 함께 보는 공간에서 15~20가지 자세. 렌즈마다 `calibrateCamera`, 다음 `stereoCalibrate`(내부 값 고정), `stereoRectify`.
- 결과: `~/e850-logs/dvs3d/calib.npz`(이 배치에만 맞으므로 저장소에 넣지 않는다).
- 판정: 재투영 오차 1 px 미만, 두 초점거리 비 2배 이하(넘으면 렌즈를 다시 논의).

## 4. 오류 처리

- 한 보드 응답이 없거나 늦음 → 페이지에 어느 보드인지 표시하고 3D는 멈춤. 다시 붙으면 이어 감.
- 410(밀려남)이 계속되면 T의 지연(80 ms)을 늘린다는 안내를 `stats`에 남긴다. 425는 T를 그만큼 뒤로.
- 보정 파일이 없으면 3D 점은 내지 않고 두 화면과 시간만 보인다.

## 5. 구현 순서

1. libdvsview `dvs_surface` + display→monotonic, C 자체 시험(DVSVIEW_TEST).
2. dvsctl `/evframe` + unittest(FakeLib).
3. 보드2에 설치(ACM root), dvslive 재시작, `/evframe` 확인. 보드1은 root가 생길 때 같은 파일로 설치.
4. dvs3d.py: BoardClock, 짝, 파이프라인, 녹화, bench + unittest(순수 함수).
5. **연산량:** Mac에서 녹화·bench. 보드2에 `python3-opencv`를 설치하고 bench(1코어, 3코어). 보드1에 아직 `/evframe`이 없으면 보드2를 두 번 써서(같은 이미지 쌍) 잰다. SGBM 비용은 내용과 거의 무관하고, 같은 이미지는 유효 시차가 가장 많아 3D 변환이 가장 무거운 경우다.
6. 페이지(dvs3d.html).
7. 보정 도구(사용자가 있을 때 실행), 손전등 시각 시험, 3D 정확도 시험.
8. 경량화 방안 정리(측정 결과 → 2단계 계획의 입력).

## 6. 판정 기준

| 항목 | 기준 |
|---|---|
| 연산량 | 단계별 ms를 M6과 A55에서 잰다. 보드 이식 판단: A55 합계 100 ms 미만(10 Hz) |
| 보드 비용 | `/evframe` 한 번의 C 계산 + 압축 시간(`X-Dvs-Cost-Us`), live 계수기(버퍼 속도, scan 누락)가 나빠지지 않을 것 |
| 전송량 | 보드 하나당 B/s |
| 시각 맞추기 | 손전등 깜빡임 시작 시각 차이 2 ms 미만 |
| 보정 | 재투영 오차 1 px 미만, 초점거리 비 2배 이하 |
| 3D 정확도 | 선풍기 원판 점에 평면·원을 맞춰 거리 오차 5 cm, 지름 오차 2 cm 안(자로 잰 값과 비교) |
| 시험 코드 | C 자체 시험, `test_dvsctl.py`, `test_dvs3d.py` |

## 7. 위험과 모르는 것

- time surface는 경계만 있는 영상이라 SGBM이 엉뚱한 짝을 낼 수 있다(가장 큰 불확실성).
- Wi-Fi 왕복 방식으로 2 ms 정확도가 나오는지 모른다. 안 되면 손전등(공통 시각 사건)이나 GPIO 선으로 바꾼다.
- 보드1 바깥 날개 이벤트가 없다(2장).
- 보드1 설치에는 root가 필요하다(사용자 sudo 또는 USB-C ACM).
