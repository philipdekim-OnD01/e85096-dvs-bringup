# DVS 스테레오 3D (1단계: Mac) 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 두 보드가 time surface 이벤트 프레임(`/evframe`)을 내주고, Mac의 `dvs3d.py`가 같은 시각의 쌍으로 3D 점구름 페이지를 만든다. 연산량(M6, A55)을 먼저 잰다.

**Architecture:** libdvsview에 C 함수 `dvs_surface`와 display→monotonic 대응을 더하고, dvsctl에 경로 하나를 더한다. Mac 쪽은 파일 하나(`dvs3d.py`)에 시계·짝·파이프라인·녹화·bench·서버가 있고, 페이지는 `dvs3d.html`이다.

**Tech Stack:** C(gcc, zlib), Python 3(보드 3.11 numpy 1.24, Mac 3.12 numpy 2.4 + OpenCV 4.13), Debian `python3-opencv`(보드 bench용), three.js 0.160.0(cdnjs).

**Spec:** `docs/superpowers/specs/2026-11-14-dvs-stereo-3d-design.md`

## Global Constraints

- surface 480×360(2×2 묶음), 값 `1 + 254 × (win − age) / win`, 0 = 이벤트 없음, 극성 안 씀.
- `win` 1000~200000 µs, 기본 20000. 짝 시각 T = Mac 지금 − 80 ms.
- 시각 비교는 `(int32_t)(a - b)`(display clock uint32, 71.6분마다 넘어감).
- 링에서 최신부터 RING/2 안쪽만 읽는다(`dvs_events()` 규칙).
- 보드 웹 스레드는 CPU7. 새 스레드를 만들지 않는다.
- 응답 헤더: `X-Dvs-Newest`, `X-Dvs-Disp2Mono`, `X-Dvs-Mono`, `X-Dvs-Events`, `X-Dvs-Cost-Us`. 오류 425/410/400, 4xx는 `Connection: close`.
- 왕복 최솟값 20 ms 넘으면 "동기 불확실". 점은 최대 20000.
- Mac 페이지 포트 8090. 보정 결과는 `~/e850-logs/dvs3d/calib.npz`(저장소 밖).
- 보드에 설치·재시작할 때마다 이전 파일을 `/root/*.prev-11.56`으로 남긴다. 문서·답변은 한글.

## Review Focus

1. display clock이 넘어가는 순간(71.6분)의 창 → 넘어감을 가로지르는 창도 이벤트를 모두 담는다(Task 1 시험).
2. 한 보드의 dvslive가 다시 시작해 display clock이 0부터 다시 셈 → Mac은 응답마다 최신 `X-Dvs-Disp2Mono`를 쓰고, 410/425 쌍은 버리고 센다(Task 4 시험).
3. 한 보드 응답이 없음·시간 초과 → 예외 없이 그 쌍을 버리고 `dropped`를 센다(Task 4 시험).
4. 이벤트가 없는 창(선풍기 끔) → surface가 모두 0, 점 0개, 페이지가 멈추지 않는다(Task 4 시험).
5. 보정 파일 없음 → 3D 점은 없고 두 화면·시간만 나온다(Task 4 시험).

---

### Task 1: libdvsview — display→monotonic 대응과 `dvs_surface`

**Files:**
- Modify: `tools/linux510/dvsview.c` (`S` 구조체, `dvs_ingest`, 새 함수는 `dvs_cells` 뒤, 자체 시험은 `main`)

**Interfaces:**
- Produces: `int dvs_surface(uint32_t t_end, int win_us, uint8_t *dst, uint32_t *info)` — `dst`는 480×360 바이트(0으로 채운 뒤 씀), `info[0]` newest(display µs, uint32), `info[1]` disp2mono(µs, `(uint32_t)` 하위 32비트; 64비트 값은 `info[1] | info[2] << 32`), `info[3]` cost µs. 반환: 쓴 이벤트 수 ≥ 0, -1 아직 안 옴(`t_end`가 newest보다 늦음), -2 밀려남, -3 인자(`win_us` 범위, `dst` NULL).
- Produces: `int64_t dvs_disp2mono(void)` — 최근 64버퍼의 `now_us() - S.t` 최솟값, 버퍼가 없으면 0.

- [ ] **Step 1: 자체 시험 작성 (`main`, DVSVIEW_TEST)**
  - 링에 이벤트를 직접 넣는다(`S.ring[i] = (struct ev){t, x, y}`, `S.wr`, `S.newest`): (t=1000, x=10, y=20), (t=15000, x=10, y=20), (t=19000, x=101, y=51 | EV_OFF), newest 20000.
  - `dvs_surface(20000, 20000, img, info)` == 3, `img[10 * 480 + 5]` == `1 + 254 * (20000 - 5000) / 20000`(t=15000이 같은 화소의 늦은 값), `img[25 * 480 + 50]` == `1 + 254 * 19000 / 20000`, 나머지 0, `info[0]` == 20000.
  - `dvs_surface(20001, 20000, ...)` == -1. `dvs_surface(20000, 999, ...)` == -3.
  - 넘어감: 이벤트 t = 0xFFFFF000, 0x00000800, newest 0x00001000 → `dvs_surface(0x1000, 20000, ...)` == 2.
  - 밀려남: `S.wr = RING`(링 전체가 한 바퀴), 창 시작이 `S.ring[(S.wr - RING/2) % RING].t`보다 앞 → -2.
- [ ] **Step 2: Docker에서 빌드·실행해 실패 확인** — `docker run --rm -v "$PWD/tools/linux510:/s" debian:12 sh -c '... gcc -O2 -Wall -DDVSVIEW_TEST -o /tmp/t /s/dvsview.c -lpthread -lm -lz && /tmp/t'` → 컴파일 오류(`dvs_surface` 없음).
- [ ] **Step 3: 구현** — newest부터 링을 거꾸로 읽어 `(int32_t)(t_end - e.t) >= win_us`가 되면 멈춘다. `(int32_t)(e.t - t_end) > 0`이면 건너뛴다(창보다 늦은 것). 읽기 하한은 `wr - RING/2`. 다 읽은 뒤 `S.wr`을 다시 읽어 하한이 지나갔으면 -2. 화소 값은 더 늦은 이벤트가 이긴다(거꾸로 읽으므로 처음 쓴 값을 유지). `dvs_ingest`는 decode 뒤 `now_us() - S.t`를 64칸 링에 넣는다.
- [ ] **Step 4: 시험 통과 확인** — 같은 명령, 출력 없이 종료 코드 0. `-shared` 빌드도 경고 없이 된다.
- [ ] **Step 5: 커밋** — `git commit -m "libdvsview: dvs_surface time-surface window and display-to-monotonic offset"`

### Task 2: dvsctl — `GET /evframe`

**Files:**
- Modify: `tools/linux510/dvsctl.py` (docstring 경로 목록, `start()` argtypes, `do_GET`)
- Test: `tools/linux510/test_dvsctl.py`

**Interfaces:**
- Consumes: Task 1의 `dvs_surface`, `dvs_disp2mono`.
- Produces: `evframe_args(query: str) -> tuple[int | None, int]` (t_end 또는 None, win; 틀리면 `ValueError`), `evframe(lib, t_end, win) -> tuple[int, dict, bytes]` (HTTP 코드, 헤더 dict, 본문). `/evframe` 응답은 Global Constraints의 헤더.

- [ ] **Step 1: 시험 작성** — `TestEvframe`:
  - `evframe_args('t_end=123&win=5000') == (123, 5000)`, `evframe_args('') == (None, 20000)`, `win=999`·`win=200001`·`t_end=x`는 `ValueError`, `t_end=4294967295`는 통과, `t_end=4294967296`은 `ValueError`.
  - FakeLib에 `dvs_surface`(dst 첫 바이트에 7을 쓰고 info = [newest 5000, disp2mono 하위 123, 상위 0, cost 40], 반환 1), `dvs_disp2mono`(123) 추가: `evframe(lib, 5000, 20000)` → 코드 200, `zlib.decompress(body)`의 길이 172800, 첫 바이트 7, 헤더 `X-Dvs-Events` '1', `X-Dvs-Newest` '5000', `X-Dvs-Disp2Mono` '123'.
  - FakeLib 반환 -1 → 425, -2 → 410, t_end None → 200이고 본문 빈 바이트, 헤더에 `X-Dvs-Mono`.
- [ ] **Step 2: 실패 확인** — `python3 -m unittest discover -s tools/linux510 -p test_dvsctl.py` → ImportError(`evframe_args`).
- [ ] **Step 3: 구현** — `do_GET`에 `/evframe` 분기, 압축은 `zlib.compress(buf, 1)`, `X-Dvs-Mono`는 `time.monotonic_ns() // 1000`(CLOCK_MONOTONIC, C의 `now_us`와 같은 시계), `X-Dvs-Cost-Us`는 C cost + 압축 시간. `lock`(dvs_png와 같은 lock)은 쓰지 않는다: `dvs_surface`는 자기 버퍼만 쓴다.
- [ ] **Step 4: 통과 확인** — 같은 명령, OK.
- [ ] **Step 5: 커밋** — `git commit -m "dvsctl: GET /evframe (time surface of a display-clock window)"`

### Task 3: 보드2에 설치하고 확인

**Files:** 없음(배포). 기록은 Task 8.

- [ ] **Step 1: 빌드** — Task 1 Step 2의 Docker 명령으로 `-shared` `libdvsview.so`(debian:12, 보드와 같은 glibc).
- [ ] **Step 2: 보드2 확인** — ACM `cat /etc/machine-id`가 `6f31c0bd…`인지. dvslive 상태와 `/stats`의 버퍼 속도·`ts_missed`를 적어 둔다(설치 전 기준).
- [ ] **Step 3: 올리기** — `scp`(ssh `-o HostKeyAlias=192.168.0.236 philip@192.168.0.168`)로 `/var/tmp`에. ACM root로 `cp` 이전 판 → `/root/*.prev-11.56`, `install`로 `/usr/local/lib/libdvsview.so`와 dvsctl.py 위치(설치 경로는 보드에서 `systemctl cat dvslive`와 `dvslive.sh`로 확인), `systemctl restart dvslive`.
- [ ] **Step 4: 확인** — `curl -sD- 'http://192.168.0.168:8080/evframe'` 헤더, `t_end=newest-50000`으로 이미지 받기(zlib 풀어서 172800 바이트, 0 아닌 화소 > 0), 410·425·400 각각 한 번. 5분 뒤 `/stats` 버퍼 속도·scan 누락이 설치 전과 같은지.
- [ ] **Step 5: 보드1 준비** — 같은 파일을 보드1 `/var/tmp`에 올리고 설치 명령을 문서에 남긴다(root가 생길 때).

### Task 4: Mac `dvs3d.py` — 시계, 짝, 파이프라인, 녹화, bench

**Files:**
- Create: `tools/host/dvs3d.py`
- Test: `tools/host/test_dvs3d.py`

**Interfaces:**
- Consumes: Task 2의 `/evframe`.
- Produces:
  - `class BoardClock`: `add(t0_us: int, t1_us: int, mono_us: int, disp2mono_us: int) -> None`, `offset() -> int | None`(Mac→보드 monotonic, 최근 30 s 중 왕복 최소 표본), `rtt() -> int | None`, `to_disp(mac_us: int) -> int`(uint32로 접음), `uncertain() -> bool`(rtt > 20000 또는 표본 없음).
  - `class Board`: `__init__(host: str, port=8080)`, `probe() -> None`, `frame(t_end: int, win: int) -> tuple[np.ndarray(360, 480, uint8), dict] | None`(4xx·시간 초과면 None, `self.errors[code]` 증가).
  - `class Pipeline`: `__init__(calib: dict | None, num_disp=64, block=5)`, `run(left: ndarray, right: ndarray) -> tuple[ndarray(N,3) float32, ndarray(N) uint8, dict[str, float]]`(점, 값, 단계별 ms; 보정 없으면 N=0 대신 정류 없이 시차까지만 하고 점 0개).
  - `def pack_points(xyz, val) -> bytes`(헤더 uint32 N, 뒤에 float32×3N, uint8×N), `def thin(n_max: int, *arrays) -> list[ndarray]`(무작위 솎기, 시드 고정).
  - CLI: `dvs3d.py live --left HOST --right HOST [--rec DIR] [--port 8090]`, `dvs3d.py bench DIR [--threads N] [--calib F]`.
- [ ] **Step 1: 시험 작성** (`test_dvs3d.py`, numpy·cv2 필요):
  - BoardClock: 표본 (0, 10000, 1_000_000, 500) 과 (100000, 104000, 1_100_000, 500)이면 `offset() == 1_100_000 - 102000`, `rtt() == 4000`, `uncertain()` False. 30 s 넘은 표본은 버려진다. 표본 rtt 25000뿐이면 `uncertain()` True. `to_disp`는 `(mac + offset - disp2mono) & 0xffffffff`(음수·넘어감 포함 한 사례씩).
  - Pipeline(None).run(zeros, zeros) → 점 0개, 시간 dict 키가 `rectify`, `sgbm`, `mask`, `reproject`.
  - Pipeline(calib)에서 calib는 같은 카메라 둘을 x로 0.15 m 옮긴 합성값(K f=600, cx=240, cy=180, 왜곡 0, `cv2.stereoRectify`로 Q 계산): 왼쪽 surface에 60×60 무작위 무늬(시드 0, 값 1~255), 오른쪽은 같은 무늬를 x −20 px → 점 개수 > 0, 중앙 z 중간값이 `600 × 0.15 / 20 = 4.5` m의 ±10% 안.
  - `pack_points` 길이 = 4 + 13N, 첫 uint32 = N. `thin(5, a)`는 5개, `thin(5, b)`(3개)는 그대로.
  - Board.frame: 로컬 `http.server`로 425·410 응답 → None, `errors` {425:1, 410:1}; 응답 없는 포트(닫힌 소켓) → None, 예외 없음.
- [ ] **Step 2: 실패 확인** — `python3 -m unittest discover -s tools/host -p test_dvs3d.py` → ImportError.
- [ ] **Step 3: 구현** — SGBM 인자: `minDisparity 0, numDisparities num_disp, blockSize block, P1 8*block², P2 32*block², disp12MaxDiff 1, uniquenessRatio 10, speckleWindowSize 50, speckleRange 2`. 마스크는 왼쪽 정류 surface > 0 그리고 시차 > 0. bench는 녹화 파일마다 `run`을 돌려 단계별 중간값·p90을 표로 출력(`cv2.setNumThreads(threads)`). live는 10 Hz 목표 루프 + Task 6의 서버 시작 함수 자리(`serve(state, port)`, Task 6에서 채움). 녹화는 `np.savez_compressed(DIR/%06d.npz, left, right, t_left, t_right, mac_us)`.
- [ ] **Step 4: 통과 확인** — 같은 명령, OK.
- [ ] **Step 5: 커밋** — `git commit -m "dvs3d: board clock, pairing, SGBM pipeline, recording and bench"`

### Task 5: 연산량 측정

**Files:** 기록은 Task 8. 결과 원자료는 `~/e850-logs/dvs3d/bench-*`.

- [ ] **Step 1: 녹화** — 보드1에 `/evframe`이 없으면 `dvs3d.py live --left 192.168.0.168 --right 192.168.0.168 --rec ~/e850-logs/dvs3d/rec-b2b2` 3분(같은 이미지 쌍, spec 5장 5번). 받은 쌍 수, 버린 쌍 수, 보드당 B/s, 시계 rtt를 적는다.
- [ ] **Step 2: Mac bench** — `dvs3d.py bench <rec> --threads 1`과 기본 스레드. 합성 보정(Task 4 시험과 같은 calib)을 `--calib`로 줘서 3D 변환까지 잰다.
- [ ] **Step 3: 보드2 bench** — 보드2 공간 확인(`df -h /`), `apt-get install -y --no-install-recommends python3-opencv`(ACM root), 녹화 일부(300쌍)를 `/var/tmp`에. `taskset -c 4` `--threads 1`, `taskset -c 4-6` `--threads 3`. 행동 분류가 켜져 있으면(`/var/lib/dvsact/enable`) 재는 동안 끄고 다시 켠다.
- [ ] **Step 4: 보드 비용** — 10 Hz로 `/evframe`을 받는 동안 `X-Dvs-Cost-Us` 분포와 `/stats` 버퍼 속도·scan 누락.

### Task 6: 페이지 `dvs3d.html`과 서버

**Files:**
- Create: `tools/host/dvs3d.html`
- Modify: `tools/host/dvs3d.py` (`serve(state, port)`)

**Interfaces:**
- Consumes: Task 4의 `pack_points`, 파이프라인 결과 상태(`state`: 최신 점 bytes, 두 화면 PNG bytes, stats dict).
- Produces: `GET /`(페이지), `GET /points`(application/octet-stream), `GET /views.png`(정류 두 화면 가로로), `GET /stats`(JSON: `timing_ms`, `clock`(보드별 rtt, uncertain), `pairs`, `dropped`, `errors`, `calib` 유무).

- [ ] **Step 1: 시험** — `test_dvs3d.py`에 서버를 임시 포트로 띄워 `/stats` JSON 키, `/points` 길이 = 4 + 13N, `/`가 200이고 three.js 스크립트 태그가 `cdnjs.cloudflare.com/ajax/libs/three.js/0.160.0/`인지.
- [ ] **Step 2: 실패 확인 → 구현 → 통과 확인** — 페이지: 점구름(three.js `Points`, z에 따라 색, 마우스 드래그 회전·휠 확대는 직접 짠 간단한 궤도 조작), 두 화면 이미지, 상태 표. 5 Hz로 `/points`·`/stats`를 받는다.
- [ ] **Step 3: 화면 확인** — headless Chrome으로 `http://localhost:8090/`을 찍어 점·화면·상태가 나오는지(합성 점 상태로).
- [ ] **Step 4: 커밋** — `git commit -m "dvs3d: three.js point cloud page"`

### Task 7: 보정 도구 (실행은 사용자가 있을 때)

**Files:**
- Create: `tools/host/dvs3d-calib.py`, `tools/host/dvs3d-checker.html`
- Test: `tools/host/test_dvs3d.py`

**Interfaces:**
- Consumes: Task 4의 `Board`, `BoardClock`.
- Produces: `find_corners(img: ndarray, pattern=(9, 6)) -> ndarray | None`, `calibrate(views: list[tuple[left_corners, right_corners]], square_m: float, size=(480, 360)) -> dict`(K1, D1, K2, D2, R, T, R1, R2, P1, P2, Q, rms, f_ratio), 결과는 `np.savez(~/e850-logs/dvs3d/calib.npz)`. `Pipeline`이 읽는 calib dict 키와 같다.
- [ ] **Step 1: 시험** — 합성: 바둑판 3D 점을 두 가상 카메라(f 600, 0.15 m)로 15가지 자세 투영 → `calibrate` rms < 0.5, |T| 0.15 ± 0.01 m, f_ratio 1.0 ± 0.05. 합성 바둑판 이미지(가장자리만 값 200)에서 `find_corners`가 54점.
- [ ] **Step 2: 실패 확인 → 구현 → 통과 확인** — checker 페이지는 9×6 안쪽 모서리 바둑판을 100 ms마다 흑백 뒤집기, 전체 화면 버튼.
- [ ] **Step 3: 커밋** — `git commit -m "dvs3d: stereo calibration with a blinking checkerboard"`

### Task 8: 기록

- [ ] **Step 1:** `docs/E85096-LINUX510-BOOT.md` 11.56(설계 요약, 보드2 설치와 확인, 연산량 표, 경량화 방안, 확인하지 못한 것), `BRINGUP_STATUS.md`(1장 표에 3D 줄, 3장에 새 항목과 보드1 설치 명령, 보정 절차).
- [ ] **Step 2: 커밋** — `git commit -m "docs 11.56: DVS stereo 3D stage 1 (Mac), compute measured"`
