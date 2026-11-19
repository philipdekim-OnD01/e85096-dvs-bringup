# 현미경 뷰어 화면 재구성 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**목표:** 뷰어를 상용 제품처럼 바꾼다. [실시간]/[실험]/[결과] 탭, 실험 마법사(프리셋, 최소 크기 비교, 분포 손잡이), 결과 대시보드(핵심 수치, 사진 띠, 재생, PDF 보고서), ⚙ 고급 서랍을 둔다.

**구조:**
- `scope.html` 한 파일을 그 자리에서 재구성한다. 영상 엔진과 그래프 코드는 그대로 옮긴다.
- 서버는 `/analyze/preview`의 인자·JSON과 `/stat`의 `disk`만 더한다.

**기술:** 순수 JS, canvas, CSS(CDN 없음). Python 3.11, OpenCV 4.11.

**설계 문서:** `docs/superpowers/specs/2026-11-19-scope-ui-redesign-design.md`

## 전체 제약

- 화면 글은 모두 한글이다. 외부 파일과 CDN을 쓰지 않는다.
- 기존 HTTP 경로와 cfg 키는 그대로 둔다. 새 키를 만들지 않는다.
- 진행 중인 실험이 있으면 화면이 분석 cfg를 바꾸지 않는다.
- 보드4에서는 실행 중인 스크립트 위에 scp하지 않는다. 설치는 사용자가 sudo로 `install.sh`를 실행한다.
- 커밋과 push는 사용자 확인을 받은 뒤 한다.

## 시험이 놓치기 쉬운 점

1. **물체가 수천 개인 프레임:** JSON은 3000개에서 자르고 `truncated`를 단다. 원 그리기는 화면이 멈추지 않아야 한다.
2. **OpenCV 없음(`cv.have` false):** 물체 표시와 비교는 꺼지고 이유가 보인다. 실시간과 촬영은 동작한다.
3. **실험 중 실시간 프리셋 변경:** 막힌다.
4. **사진 0장인 실험과 수동 촬영 기록:** 결과 탭의 타일이 "—"를 보이고, 오류가 나지 않는다.
5. **폰 폭 390 px:** 가로 넘침이 없다(`scrollWidth == innerWidth`).

---

### 작업 1: 서버 `/analyze/preview` 인자·JSON, `/stat` disk

**파일:** `tools/board/scope/scope.py`

**인터페이스 (만드는 것):**
- `GET /analyze/preview?fmt=json[&dark=0|1&min=&max=&sens=1..5&split=0|1]`
  - 반환: `{count, mean_d_um, total_area_um2, truncated, objs: [[x:int, y:int, d_um:float1], …≤3000]}`
- `GET /analyze/preview?fmt=json&mins=5,10,20`
  - 반환: `{tries: [{min, count, mean_d_um, total_area_um2, truncated, objs}]}`(같은 프레임, 최대 8개)
- `/stat`의 `disk: {free_gb, photos_gb, n}`(30초 캐시)

- [ ] `an_opts(c, q)`를 만든다. cfg 사본에 질의 인자를 덮어쓴다. 범위 자르기는 `/set`과 같다: 크기 0.5~5000, sens 1~5.
- [ ] `photos_bytes()`를 `disk_full`에서 떼어 내 `/stat`과 함께 쓴다.
- [ ] Mac fake 8091에서 확인한다.
  - `curl '/analyze/preview?fmt=json&min=20&max=500&sens=3'`이 count와 objs를 준다.
  - `mins=5,10,20`이 tries 3개를 준다.
  - `/stat`에 disk가 있다.
  - 인자가 없으면 지금처럼 JPEG를 준다.

### 작업 2: `scope.html` 재구성

**파일:** `tools/board/scope/scope.html`(전체 다시 씀)

**옮기는 것(그대로):**
- 노출 막대·LUT·자동 대비
- `fetchFrame`/`process`/`draw`/확대/포인터/측정
- 갤러리·데이터 막대 PNG
- 레지스터
- 기록 그래프 `R`, `SER`, `buildCharts`, `drawOne`, `nearest`, `tip`, `selectIdx`, `drawSizeHist`, `buildTable`

**새로 만드는 것:**
- `showTab(name)`: `"live" | "exp" | "results"`. 실시간 탭이 아니면 `fetchFrame`이 500 ms 뒤에 다시 본다(대역 절약).
- 물체 표시: `st.ov = {on, busy, res}`. `draw()`가 `res.objs`를 원으로 그린다.
- `PRESETS = {particle: {an_dark: 1, an_min_um: 20, an_max_um: 1000, an_sens: 3, an_split: 1, an_track: 0}, cell: {…8, 80, 4, 1, 0}, colony: {…100, 5000, 3, 1, 1}}`. 설계 문서의 표를 따른다.
- 마법사:
  - 초안 `D`(cfg에서 복사), 단계 1~4
  - `testDraft()`: `mins=5,10,20,50,100,200`. 결과로 그림 6장과 큰 미리보기, 분포를 그린다.
  - 분포 손잡이를 끌면 `D.an_min_um`/`an_max_um`이 바뀌고, 놓으면 `testDraft(single)`를 한다.
  - 예상 용량: `n = hours ? floor(hours*3600/every)+1 : null`, `mb = disk.n ? photos_gb*1000/n : W*H*0.75e-6`
  - `startExp()`: `setCfg(D)` 다음 `setCfg({tl_on:1})`, 그다음 결과 탭으로 간다.
- 결과: 타일 4개, 사진 띠(`loading=lazy` 썸네일, 새 사진만 덧붙인다), `play()`(img onload 뒤 다음, 0.4초 간격), `printReport()`(`beforeprint`/`afterprint`로 `--` 변수를 밝게 바꾸고 다시 그린다).
- 고급 서랍: 평탄화, 히스토그램, 레지스터, 화소, 단축키, OpenCV 상태.
- 단축키: 1/2/3 탭(측정 1:1 키는 실시간 탭에서만 `1`. 탭 이동은 Alt+1/2/3), R 결과, Space, C, X, M, F, 0, +/−, Esc.

- [ ] Mac fake 8091에서 CDP 시험 스크립트(`scratchpad/x/cdpui.py`)를 돌린다. 기대 결과:
  - 예외와 console error가 0개다.
  - 탭 3개와 서랍의 스크린숏
  - 마법사: 비교 그림 6장의 개수가 숫자로 보인다. 손잡이를 끌면 `D.an_min_um`이 바뀐다.
  - 10초 간격으로 실험을 시작하고 40초 뒤 결과 탭에 사진이 3장 이상 있고, 타일 개수가 숫자다.
  - 실험 중 실시간 프리셋 선택이 `disabled`다.
  - 재생하면 `R.sel`이 늘어난다.
  - `emulateMedia print` 스크린숏의 배경이 밝다.
  - 390 px에서 `scrollWidth == innerWidth`다.
- [ ] `cv.have` false 경로: Mac에서 `scope_rec.HAVE_CV`를 끈 실행(`python3 -c` 래퍼)으로 오류 없이 이유가 보이는지 확인한다.

### 작업 3: 보드4 시험, 사용자 시안 확인, 설치, 기록

- [ ] 보드4 `/var/tmp/scope-ui/`에 복사하고 fake 8091(`--ctl /var/tmp/scope-ui/ctl --data /var/tmp/scope-dev`)로 띄운다. 같은 CDP 시험을 돌리고 분석 시간을 기록한다.
- [ ] 사용자에게 `http://192.168.0.244:8091/`을 주고 의견을 받는다. 고친다.
- [ ] `/var/tmp/e850-scope`에 복사한다. 사용자가 `sudo sh /var/tmp/e850-scope/install.sh`를 실행한다. 확인할 것:
  - 8090의 `/stat`에서 tl_on과 run #1이 이어진다.
  - 다음 촬영 뒤 사진 수가 늘어난다.
- [ ] BOOT 문서 11.71과 BRINGUP_STATUS를 쓴다. 커밋은 사용자 확인 뒤에 한다.
