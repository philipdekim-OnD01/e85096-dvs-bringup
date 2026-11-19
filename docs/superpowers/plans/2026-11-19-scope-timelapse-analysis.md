# 현미경 자동 촬영 · 분석 · 기록 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**목표:** 보드4 현미경이 일정 간격으로 찍는다. 찍을 때마다 물체의 개수와 크기를 SQLite에 기록하고, 웹 뷰어에서 사진과 함께 추세 그래프를 본다.

**구조:**
- 분석·DB·추적은 새 모듈 `scope_rec.py`가 맡는다.
- `scope.py`는 촬영 스레드와 HTTP 경로만 더한다.
- `scope.html`은 옵션 묶음과 전체 화면 "기록" 패널을 더한다.

**기술:** Python 3.11, numpy 1.24, OpenCV 4.11.0.86 headless wheel(`/opt/e850-scope/py`), sqlite3, canvas.

**설계 문서:** `docs/superpowers/specs/2026-11-19-scope-timelapse-analysis-design.md`

## 전체 제약

- 시스템 패키지는 설치하지 않는다. wheel 하나만 `py/`에 푼다.
- cv2가 없어도 뷰어는 뜬다. 분석만 꺼지고 이유가 화면에 보인다.
- 데이터를 자동으로 지우지 않는다. 보관 한도에 닿거나 여유 공간이 2 GB 아래로 내려가면 자동 촬영을 멈춘다.
- 화면 글은 모두 한글이다. CDN을 쓰지 않는다.
- 커밋과 push는 사용자 확인을 받은 뒤 한다(CLAUDE.md).
- 서비스에 설치할 때는 사용자가 sudo로 `install.sh`를 실행한다.

## 시험이 놓치기 쉬운 점

1. **물체가 0개인 사진:** count 0, 평균 크기 null. 그래프는 null 점을 건너뛴다.
2. **임계값이 잡음에 걸려 덩어리가 수만 개 나올 때:** 물체 5000개에서 자르고 `truncated`로 표시한다. DB와 화면이 멈추지 않아야 한다.
3. **촬영 중 카메라가 빠질 때:** 그 회차만 건너뛰고 로그를 남긴다. run은 계속한다.
4. **실행 중 서비스가 다시 시작될 때:** 같은 run을 잇는다. 끝나는 때가 지났으면 run을 닫는다.
5. **실행 중 분석 조건(pixel_um, 크기 범위)을 바꿀 때:** 사진마다 쓰인 조건(opts)을 기록한다.

---

### 작업 1: OpenCV wheel 확인

- [ ] Mac에서 PyPI JSON의 URL로 wheel을 받는다. sha256이 PyPI 값과 같은지 확인한다.
- [ ] 보드4의 `/var/tmp/e850-scope/py`에 푼다(`python3 -m zipfile -e`). 그다음 `python3 -c "import sys; sys.path.insert(0,'/var/tmp/e850-scope/py'); import cv2; print(cv2.__version__)"`을 실행한다. 기대 출력: `4.11.0`.

### 작업 2: `scope_rec.py` (분석, 추적, DB, 윤곽, CSV)

**인터페이스 (만드는 것):**
- `HAVE_CV: bool`, `CV_ERR: str`
- `analyze(img, pixel_um, dark=True, min_um=5, max_um=500, split=True, k_mad=3) -> dict`
  - 반환 키: `objs` [(x, y, area_px)], `contours`, `count`, `mean_d_um` (float 또는 None), `total_area_um2`, `truncated`
- `overlay_jpg(img, res) -> bytes`
- `Rec(path)`의 메서드:
  - `new_run(sample, every_s, opts) -> int`
  - `end_run(run_id)`
  - `add_photo(run_id, name, meta, res, pixel_um, track) -> int`
  - `runs()`, `photos(run_id)`, `objects(photo_id)`, `tracks(run_id, k=8)`, `csv(run_id, what)`

- [ ] `--selftest`를 먼저 쓴다. 2016×1536 회색 바탕에 LED 얼룩(가장자리가 어두운 그러데이션)을 넣고, 어두운 원판 3종을 그린다.
  - 원판: 지름 10 µm 5개, 40 µm 5개, 서로 붙은 40 µm 2개. pixel_um은 2.24.
  - 단언 1: `count == 12`(나누기 켬), 11(나누기 끔).
  - 단언 2: 40 µm 원판의 등가 지름이 ±15 % 안에 든다.
  - 단언 3: 범위 20~500 µm에서 count는 7이다.
  - 단언 4: 빈 영상에서 count 0, `mean_d_um is None`.
  - 단언 5: 원판을 3 px 옮긴 두 번째 사진에서 track ID가 같다.
  - 단언 6: 6000개를 넣으면 count는 6000, 기록은 5000개, `truncated`다. 잡음 3배 빈 영상에서는 기본 민감도로 ≤ 1개, k = 5로 0개다.
- [ ] 구현한다. 알고리즘은 설계 문서의 "분석 순서"를 따른다.
  - 배경 시그마는 max(최대 지름 px × 2, 15)이다.
  - watershed 표지는 거리 변환의 국소 극대점(창 = 최소 반지름)에서 값이 최소 반지름의 절반 이상인 곳이다.
  - DB는 WAL 모드로 연다. 연결은 스레드마다 따로 연다(`sqlite3.connect(path, timeout=10)`).
- [ ] 보드4에서 `python3 scope_rec.py --selftest`를 실행한다. 기대 출력: `selftest ok`.

### 작업 3: `scope.py` 자동 촬영과 HTTP

**인터페이스 (쓰는 것):** 작업 2의 이름 전부.

- [ ] `sys.path`에 `HERE/py`를 넣는다. `import scope_rec`.
- [ ] cfg 기본값을 둔다(설계 문서의 키): `tl_every` 300 s, `tl_hours` 24, `an_min_um` 5, `an_max_um` 500, `rec_gb` 30. 체크박스 기본값은 설계 스케치와 같다.
- [ ] `Cam.feed`가 raw 프레임을 `cam.raw`에 둔다.
  - `grab(n, flat) -> uint8`: 새 raw n장을 평균한다.
  - `auto_exposure()`: 평균 118 ± 10을 목표로 최대 6번 조정하고, 매번 새 프레임 3장을 기다린다.
- [ ] `save_photo(img, extra_meta, run_id) -> name`: 기존 `capture()`를 넓힌 것이다.
  - PNG를 쓴다(`rec_png`가 꺼져 있으면 썸네일만 쓴다).
  - json, 분석, DB, 윤곽 JPEG(`rec_ov`)을 쓴다.
  - `/capture`가 이 함수를 쓴다(run 없음).
- [ ] 자동 촬영 스레드: 다음 촬영 시각 = 지난 시각 + 간격이다. 늦어서 놓친 회차는 건너뛴다.
  - 촬영 앞뒤로 LED와 노출을 저장했다가 되돌린다.
  - 보관 한도 검사를 한다. 끝나는 때가 지났으면 run을 닫고 `tl_on=False`로 둔다.
- [ ] `/set`에 새 키를 받는다. 숫자는 범위로 자른다(간격 10~86400 s, 크기 0.5~5000 µm, 한도 1~45 GB). `tl_on`이 0→1이 되면 `new_run`, 1→0이 되면 `end_run`.
- [ ] HTTP 경로를 더한다.
  - `/rec/runs`, `/rec/photos?run=`, `/rec/objects?photo=`, `/rec/tracks?run=`
  - `/rec/csv?run=&what=photos|objects`(첨부 파일)
  - `/analyze/preview`(지금 프레임 윤곽 JPEG, `X-Count` 헤더)
  - `/stat`에 `tl`(다음 촬영 시각, 찍은 수, 상태 글)과 `cv`(HAVE_CV, CV_ERR)를 넣는다.

### 작업 4: `scope.html`

- [ ] 옆 칸에 "자동 촬영 · 분석" 묶음을 넣는다. 설계 스케치의 체크박스와 선택 상자, "분석 미리보기", 상태 줄(다음 촬영, 찍은 수, 경고)이 들어간다.
- [ ] 머리줄에 "기록" 버튼을 둔다. 누르면 전체 화면 패널이 뜬다.
  - run 선택(수동 촬영 포함)
  - 계열 체크박스: 개수, 평균 크기, 총면적, 크기 분포, 개별 물체
  - canvas 작은 그래프 여러 개(공통 시간축)
  - 점을 누르면 사진(윤곽 JPEG, 없으면 원본)과 값, 크기 분포 막대가 나온다.
  - CSV 링크 2개
- [ ] 보드4에서 `--fake`를 포트 8091로 띄운다(실제 사진으로 만든 YUYV, `--data /var/tmp/scope-dev`). 헤드리스 Chrome(CDP)으로 열어 콘솔 오류가 없는지 본다. 간격 10 s로 몇 장 찍고 그래프 점이 늘어나는지, 점을 누르면 사진이 열리는지 확인한다.

### 작업 5: 설치와 실제 시험, 기록

- [ ] `install.sh`가 `scope_rec.py`를 복사하게 한다. `S/py`가 있으면 `D/py`로 복사한다.
- [ ] 사용자가 `sudo sh /var/tmp/e850-scope/install.sh`를 실행한다. 그 뒤 `/stat`의 `cv.have`가 true인지 본다.
- [ ] 실제 키트로 간격 30 s 자동 촬영을 몇 분 돌린다. DB 행 수, 사진, 그래프를 확인한다. 끝나면 끈다.
- [ ] `docs/E85096-LINUX510-BOOT.md` 11.70, `docs/BRINGUP_STATUS.md`, 메모리를 적는다. 커밋은 사용자 확인 뒤에 한다.
