# DVS + USB 카메라 1000 fps 합성 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 보드6의 DVS 이벤트와 USB 카메라 15 fps 프레임을 EDI 방식으로 합성한다. 결과는 1 ms 간격 슬로모션 영상이고, 보드 데모와 Mac 고품질판 두 가지로 만든다.

**Architecture:** 세 부분으로 나눈다.
- 녹화(보드, philip 권한): 기존 `POST /rec`, `v4l2-ctl --verbose`, `/evframe` 영상 몇 장.
- 준비(Python, numpy): 되살리기, 시계 원점 찾기, 카메라 시각, 화소 대응표.
- 합성(C `dvshs`): 흐림 제거와 양쪽 적분, I420을 stdout으로 내고 ffmpeg로 받는다.

**Tech Stack:** C99(libc, libm), Python 3(numpy. OpenCV는 Mac 보정에만), ffmpeg libx264, mpv, Docker debian:12(arm64 빌드).

**Spec:** `docs/superpowers/specs/2026-11-27-dvs-highspeed-fusion-design.md`

## 설계에서 바뀐 것 (2026-11-27 밤 조사 결과, 사용자 외출 중이라 내가 정함)

- **이름:** `dvsfuse`는 11.61의 두 보드 합성(`tools/host/dvsfuse_page.py`)과 겹친다. 그래서 `dvshs`로 바꾼다.
- **root 없음:** 보드6의 philip은 sudo 비밀번호가 필요하고, ACM도 없다. 그래서 실행 중인 `libdvsview.so`와 `dvsctl.py`는 바꾸지 않는다(`/fuse` 경로를 만들지 않는다).
  - 녹화는 기존 `POST /rec {"label":"other","s":1}`로 한다. 3초 카운트다운이 있다.
  - 끝나면 `.gz`와 `.json`을 philip 소유인 `/var/tmp/dvsrec`에서 녹화 폴더로 바로 옮긴다. 분류 학습 자료에 남기지 않기 위해서다.
- **시계 원점:**
  - 녹화 첫 줄은 `rec_state()`가 넣는 실시간 디코더 상태다. 그래서 되살린 시각 0은 녹화 시작 순간의 실시간 `S.t`(= 그때의 `newest`)다.
  - 녹화 중 `/evframe?t_end=&win=10000` 영상 3장과, 되살린 이벤트로 같은 방식(480×360, 가장 늦은 이벤트, `1 + 254·(win−age)/win`)으로 만든 영상을 맞춘다. 원점 o를 1 ms 간격으로 찾은 뒤 50 µs 간격으로 좁힌다.
- **카메라 실측:**
  - 640×480 YUYV를 30 fps로 설정해도 어두워서 15.1 fps(68 ms 간격)로 나온다.
  - `v4l2-ctl --verbose`가 프레임마다 `ts: <s.us> (ts-monotonic, ts-src-soe)`를 찍는다. 이 시각을 노출 시작으로 쓴다.
  - 키프레임은 모든 프레임이다(15 fps). 평가에서만 K=2(7.5 fps 키, 나머지는 정답)를 쓴다.
- **DVS 녹화 실측 (선풍기):** 1 s에 1,965만 이벤트(gz 24 MB), 2 s에 3,438만 이벤트(40 MB). 큐는 넘치지 않았다. 되살린 이벤트는 시각 순서대로였다.
- **좌우 반전 없음:** 카메라와 DVS 원좌표가 같은 방향이다(빨간 원통이 선풍기 위). 카메라가 선풍기를 약간 크게 본다. 그래서 적분은 DVS 격자(960×720)에서 하고, 카메라 화소마다 대응표로 DVS 화소를 읽는다.
- **보정:** 태블릿 바둑판 보정(Mac)은 만들되, 실제 실행은 사용자가 돌아온 뒤에 한다. 오늘 밤에는 선풍기 특징(허브 중심, 날개 원판)에서 손으로 고른 대응점으로 임시 사영 변환을 쓴다. `calib.json`에 `"source": "manual"`로 적는다.
- **C(대비 임계)를 정하는 방법:** 일정하게 도는 선풍기만 있는 장면에서는 C를 키프레임으로 정할 수 없다. 어떤 C로도 번진 원판을 똑같이 설명하기 때문이다.
  - ON/OFF 비 r = C_off/C_on은 정상 회전 영역의 이벤트 수에서 구한다. 한 바퀴에 log 변화 합이 0이므로 C_on·n_on = C_off·n_off다.
  - 크기 C는 기본값 0.2에서 훑는다(0.1~0.4). 보고서에 영상 비교와 함께 적고, 정할 수 없다는 사실도 적는다.
  - 손이나 물체가 움직이는 녹화가 생기면 K=2 평가로 고른다.

## Global Constraints

- DVS 960×720, `struct ev {u32 t; u16 x; u16 y}`, `y & 0x3ff` = 행, `y & 0x8000` = OFF, `y & 0x4000` = 잡음 표시(되살린 녹화에는 없음).
- 카메라 640×480 YUYV(Y0 U Y1 V). 출력은 I420 640×480. 슬로모션 재생은 30 fps(1 ms 프레임 → 33.3배 느리게).
- 모든 시각은 되살린 display µs(녹화 시작 = 0)로 맞춰 C에 넘긴다. CLOCK_MONOTONIC → 되살린 µs 변환은 Python이 한다.
- 보드6에는 root 없이 넣는다: `~/.local/bin`, `~/.local/share/dvshs`, `~/Desktop`. 저장소의 `features/`는 다음 root 설치 때 쓴다.
- 보드 실험 중에도 DVS 서비스(`e850-dvs`)와 카메라 창(mpv)은 끝나면 원래대로 되돌린다. 데모는 카메라 창을 닫고 녹화한 뒤 결과 창만 띄운다.
- 문서는 한글로 쓴다. 관찰, 가설, 실패를 나눠 적는다. 커밋·push는 사용자 확인 뒤에 한다(이번 밤에는 커밋하지 않는다).

## Review Focus

1. **흐림 제거가 수치로 폭주하는 경우**(C가 크거나 이벤트가 많은 화소): `exp` 합이 넘치거나 L이 1을 크게 넘는다. 결과는 클립돼야 하고 NaN이 나오면 안 된다. selftest에 C×5 극단을 넣는다.
- **이벤트가 없는 영역**(대응표 −1, 겹치지 않는 곳): 두 키프레임을 선형으로 섞은 결과와 정확히 같아야 한다. selftest에서 이벤트 0 장면 = 선형 섞기인지 확인한다.
- **원점 찾기 실패**(스냅샷이 링 밖 410, 맞는 점수가 낮음): 경고를 남기고 `newest`의 대략값으로 이어 가야 한다. 멈추면 안 된다. 시험: 스냅샷이 틀린 경우 점수가 낮게 나오는지.
- **카메라 프레임이 녹화 앞뒤를 덮지 못함:** 합성 구간은 [첫 키프레임 기준점, 마지막 키프레임 기준점] ∩ [이벤트 시작, 끝]으로 자른다.
- **데모 중 장치 없음 또는 DVS 꺼짐:** `/evframe` 실패나 카메라 없음이면 안내 창(`e850-say`, 없으면 stderr)을 띄우고 끝낸다. 카메라 창은 녹화한 경우에만 닫는다.

---

### Task 1: 합성 핵심 `tools/linux510/dvshs.c` + selftest

**Files:**
- Create: `tools/linux510/dvshs.c`

**Interfaces:**
- Produces: CLI
  - `dvshs fuse EVENTS CAM TIMES MAP [opts] > out.i420`
    - EVENTS: `struct ev` 배열
    - CAM: YUYV 프레임들
    - TIMES: 줄마다 프레임의 노출 시작(되살린 µs, 정수)
    - MAP: int32 × 640×480, DVS 화소 번호 `y*960+x` 또는 −1
  - opts:
    - `--c 0.2`, `--ratio 1.0`(C_off = c·ratio)
    - `--T 60000`(노출 µs)
    - `--step 1000`
    - `--from US --to US`
    - `--keys 1`
    - `--layout fused|sbs`(sbs = [키프레임 선형 섞기 | 합성], 1280×480)
    - `--eval`(프레임은 내지 않는다. K=2로 빠진 프레임에서 합성을 노출 T로 평균한 것과 실제 Y를 비교해, 합성·가까운 키·선형 섞기의 PSNR을 JSON 한 줄로 stdout에 낸다)
  - stderr: 프레임 수와 단계별 시간.
  - `dvshs --selftest`: 0이면 통과.
- 계산(설계 4.3, 이 순서로):
  - Y → 선형 `lin = ((Y−16)/219)^2.2`(0.002로 바닥).
  - 키프레임 k의 기준점 f_k = s_k + T/2.
  - R(t) = Σ_{(s_k,t]} c·(ON ? 1 : −ratio)를 DVS 격자 float에 쌓는다.
  - 노출을 1 ms 단계(가운데 시각)로 걸으며 카메라 화소마다 `S += exp(R[map])`.
  - `logL_k = log(B) + R(f_k)[map] − log(S/M)`.
  - 출력 t ∈ [f_k, f_{k+1}): R'(t) = Σ_{(f_k,t]}, Rtot = R'(f_{k+1}), w = (t−f_k)/(f_{k+1}−f_k).
    - `logL = R'(t) + (1−w)·logL_k + w·(logL_{k+1} − Rtot)`
  - 다시 Y = 16 + 219·clip(L,0,1)^(1/2.2). U·V는 w로 섞는다(4:2:2 → 4:2:0은 두 줄 평균).
  - map −1 화소는 R = 0이다.

- [ ] **Step 1: selftest부터 쓴다.**
  - 장면: 64×48 회전 날개 4개(어두운 날개 0.1, 밝은 바탕 0.8). 정답 L을 0.1 ms마다 만든다.
  - 이벤트는 log 변화가 c_true = 0.25를 넘을 때마다 낸다. ratio는 1이다.
  - 키프레임 4장은 T = 50 ms 동안 평균한 것이고, 간격은 66 ms다.
  - 단언:
    1. 합성 PSNR(정답 대비, 키 사이 1 ms 프레임 전체)이 가까운 키프레임보다 3 dB 넘게, 선형 섞기보다 3 dB 넘게 높다.
    2. 이벤트를 비운 장면의 합성 = 선형 섞기(최대 차 ≤ 1 LSB).
    3. c = 1.25(5배)로 돌려도 NaN이 없고 Y가 [16,235] 안이다.
  - 함수 경계 덕분에 selftest가 내부 함수를 바로 부른다. 640×480 고정이 아니라 W, H를 인자로 받는다.
- [ ] **Step 2: Mac에서 빌드해 selftest를 돌린다.**
  - `cc -O2 -Wall -o /tmp/dvshs tools/linux510/dvshs.c -lm && /tmp/dvshs --selftest`
  - 처음에는 링크 실패나 FAIL.
- [ ] **Step 3: 계산을 구현한다** (`fuse_interval()`, `deblur_key()`, `write_i420()`).
- [ ] **Step 4: selftest를 통과시킨다.** 출력에 세 PSNR이 찍히고 `selftest ok`가 나온다.
- [ ] **Step 5: arm64로 빌드해 보드에서 selftest를 돌린다.**
  - `docker run --rm -v "$PWD/tools/linux510:/s:ro" -v ~/e850-logs/dvsfuse/bin:/o debian:12 sh -c 'apt-get -qq update && apt-get -qq install -y gcc >/dev/null && gcc -O2 -Wall -o /o/dvshs /s/dvshs.c -lm'`
  - scp로 보드6 `~/.local/bin/`에 넣고 `dvshs --selftest`를 돌린다. 걸린 시간도 적는다.

### Task 2: 준비와 녹화 `tools/linux510/dvshs.py` + 시험

**Files:**
- Create: `tools/linux510/dvshs.py`, `tools/linux510/test_dvshs.py`

**Interfaces:**
- Consumes: Task 1의 CLI. 보드의 `dvsview-t --replay`(DVSVIEW_TEST 빌드, `~/.local/bin/dvsview-t`).
- Produces:
  - `parse_v4l2_log(text) -> list[int]`: 프레임마다 CLOCK_MONOTONIC µs. `ts:` 줄 순서 그대로.
  - `surface(ev, t_end, win) -> np.ndarray(360,480,uint8)`: dvs_surface와 같은 규칙. ev는 시각 순서다.
  - `find_origin(ev, snaps, guess, span_us=300000) -> (o, score)`
    - snaps: `[(t_end_live, win, img)]`
    - score: 0~1 일치율(두 영상에서 0이 아닌 화소의 IoU)
  - `cam_times(mono_us, d2m, origin, delta) -> np.ndarray(int64)`: 되살린 µs = mono − d2m − origin + delta.
  - `homography_map(H, cam_wh=(640,480), dvs_wh=(960,720)) -> np.ndarray(int32)`: H는 카메라 → DVS. 범위 밖은 −1.
  - `onoff_ratio(ev, mask=None) -> float`: n_on/n_off. C_off = C_on·n_on/n_off.
  - CLI:
    - `dvshs.py rec DIR [-s 1]`(보드)
    - `dvshs.py prep DIR [--calib calib.json]`
    - `dvshs.py make DIR [--c 0.2] [--layout sbs] [--out slowmo.mp4]`
    - `dvshs.py demo`(보드: rec → prep → make → mpv)
    - `dvshs.py eval DIR --c 0.1,0.2,0.3`(Mac)
    - `dvshs.py calib DIR`(Mac, OpenCV 바둑판)
    - `dvshs.py calib-points OUT x1,y1,u1,v1 ...`(손 대응점 → 사영 변환)

- [ ] **Step 1: 시험을 쓴다** (`test_dvshs.py`, unittest, numpy만).
  - `test_parse_v4l2_log`: 위 실측 줄 2개 → `[175987586238, 175987654258]`.
  - `test_surface_latest_wins`: 같은 화소의 이벤트 두 개에서 늦은 것의 값이 남는다(win 10000, age 0 → 255, age 5000 → 128).
  - `test_find_origin_exact`: 합성 이벤트(무작위 점 20만 개, 1 s)를 o_true = 806940210 + 3217로 밀어 스냅샷 2장을 만든다. guess를 40 ms 틀리게 줘도 |o − o_true| ≤ 100 µs이고 score > 0.9다.
  - `test_find_origin_bad_snap`: 다른 장면 스냅샷이면 score < 0.5다.
  - `test_homography_map_identity`: H = 1.5배 축소 → 카메라 (10,10)이 DVS 화소 15*960+15이고, 범위 밖은 −1이다.
  - `test_onoff_ratio`: ON 300개, OFF 200개 → 1.5.
- [ ] **Step 2: `python3 -m unittest tools/linux510/test_dvshs.py`** — 처음에는 import 실패.
- [ ] **Step 3: 함수와 `prep`, `make`를 구현한다.** 녹화 폴더의 파일:
  - `dvs.gz`, `dvs.json`
  - `cam.yuyv`, `cam.log`
  - `snaps.npz`(t_end, win, img, 그때의 d2m)
  - `rec.json`(newest_before, newest_after, d2m 목록)
  - prep이 만드는 것: `dvs.bin`, `cam.t`, `map.bin`, `prep.json`(o, score, ratio, delta, 구간)
  - make: `dvshs fuse … | ffmpeg -f rawvideo -pix_fmt yuv420p -s WxH -r 30 -i - -c:v libx264 -preset ultrafast -crf 23 OUT`. 왼쪽 위 `t = N ms`는 ffmpeg `drawtext`로 찍는다. 글꼴이 없으면 빼고 계속한다.
- [ ] **Step 4: 시험 통과.** `python3 -m unittest tools/linux510/test_dvshs.py` → OK.
- [ ] **Step 5: `rec`를 구현한다** (보드 전용, urllib, subprocess).
  1. `pgrep -x mpv`로 카메라 창을 닫는다(args에 `/dev/video`).
  2. `v4l2-ctl --set-fmt-video=width=640,height=480,pixelformat=YUYV --set-parm=30 --stream-mmap=4 --stream-count=N --stream-to=cam.yuyv --verbose`를 띄운다. N = (3 + s + 1)·16.
  3. `POST /rec`.
  4. `/rec`와 `/evframe`(헤더만)을 20 ms마다 읽어 `newest`와 d2m을 기록한다.
  5. 상태가 recording이 된 뒤 0.3 s 간격으로 `/evframe?t_end=newest−5000&win=10000`을 3장 받는다.
  6. idle이 되면 `last`의 `.gz`와 `.json`을 옮긴다.
  - 시험은 보드에서 실제로 한 번 돌리는 것이다(Task 4).

### Task 3: 보정 (임시 손 대응점 + 바둑판 도구)

**Files:**
- Modify: `tools/linux510/dvshs.py`(calib, calib-points)
- Test: `tools/linux510/test_dvshs.py`

- [ ] **Step 1: 시험을 쓴다.**
  - `test_calib_points_roundtrip`: 알려진 H로 만든 점 6쌍 → 구한 H가 점을 0.5 px 안에 옮긴다.
  - `test_calib_checker_synthetic`(cv2가 있을 때만):
    - 바둑판 영상을 H로 휘어 카메라 영상을 만든다.
    - 깜박임을 흉내 낸 DVS 누적 영상(칸 경계에 이벤트)을 만든다.
    - 모서리를 찾아 구한 H의 오차가 2 px 이내여야 한다.
- [ ] **Step 2: 구현한다.**
  - `calib-points`: DLT, numpy만 쓴다.
  - `calib`: DVS 쪽은 녹화 전체 이벤트 수 영상을 σ2로 흐린다. 카메라 쪽은 프레임 평균 Y다. 둘 다 `cv2.findChessboardCorners`(9×6 안쪽 모서리)를 쓰고 `cv2.findHomography`를 한다.
- [ ] **Step 3: 시험 통과.**
- [ ] **Step 4: 실제 녹화로 임시 H를 만든다.**
  - 허브 중심, 날개 원판 위·아래·왼쪽·오른쪽 끝, 빨간 원통 모서리 2개를 두 영상에서 눈으로 읽는다(확대 PNG).
  - 결과는 `~/e850-logs/dvsfuse/calib-manual.json`이다.
  - 대응 확인: 카메라 프레임 위에 DVS 이벤트 수 영상을 반투명으로 겹친 PNG를 눈으로 본다.

### Task 4: 실제 녹화로 끝까지 (Mac 고품질 + 보드 데모)

**Files:**
- Create: `features/desktop/e850-hs.desktop`
- Modify: `features/desktop/e850-open`(`hs` 항목), `features/desktop/feature.sh`(파일 설치), `features/host/bundle.sh`(dvshs, dvsview-t 빌드)

- [ ] **Step 1: 보드6에서 `dvshs.py rec`(1 s)를 돌린다.**
  - 확인: 큐 넘침 없음, 원점 score > 0.9, 카메라 프레임 ≥ 60장, 카메라 창 다시 띄움.
- [ ] **Step 2: Mac에서 prep → make를 돌린다.**
  - c 0.1/0.2/0.3과 sbs 레이아웃으로 만들고, 프레임 몇 장을 PNG로 뽑아 날개가 보이는지 본다.
  - K=2 평가 수치는 기록만 한다. 선풍기 정상 회전 장면에서는 C를 구별하지 못할 수 있다.
- [ ] **Step 3: 보드에서 `dvshs.py demo`를 돌린다.**
  - 시간을 단계별로 잰다: 녹화, 되살리기, 원점, 합성, 인코딩.
  - 결과 창을 xshot으로 찍는다.
  - 60초를 넘으면 원인 단계를 적고, 줄일 수 있는 것(프레임 범위, 해상도)을 적용한다.
- [ ] **Step 4: 데스크톱 아이콘을 만든다.**
  - 보드6 `~/Desktop/e850-hs.desktop`은 `Exec=python3 ~/.local/share/dvshs/dvshs.py demo`다.
  - 저장소 `features/desktop`에는 `e850-open hs` 경로를 넣는다. root 설치는 다음에 한다.
  - 아이콘으로 띄우는 것은 사용자가 확인할 일로 남긴다. 밤에는 같은 명령을 세션 환경으로 실행한다.

### Task 5: 기록

- [ ] `docs/E85096-LINUX510-BOOT.md` 11.121(새 절):
  - 사용자 요청
  - 설계와 바뀐 점
  - 측정(녹화, 원점 score, 단계 시간, PSNR)
  - 관찰, 가설, 실패
  - 보드6에 바꾼 것
- [ ] `docs/BRINGUP_STATUS.md`와 CLAUDE.md "골라서 넣는 기능" 줄에 dvshs를 더한다.
- [ ] 아침 보고: 결과 영상 위치, 사용자가 할 일(태블릿 보정, 아이콘 클릭, 커밋 허락).
