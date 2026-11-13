# DVS 사람 행동 분류 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**목표:** S5KRC1S 이벤트로 사람 한 명의 나타남, 움직임, 넘어짐을 16 Hz로 판정해 보드의 JSONL에 기록한다.

**구조:**
- 보드: `libdvsview.so`가 새 이벤트를 넘겨준다(`dvs_events`). `dvsact.py` 스레드(CPU4~6)가 60 ms 프레임을 만들어 ONNX 모델(MobileNetV3-small + GRU)을 돌리고, 판정해서 기록한다.
- 녹화: 같은 웹 서버의 `/rec`이 패킷 원본을 gzip으로 저장한다.
- Mac: 같은 C 디코더로 녹화를 다시 풀고, 같은 `dvsact.py`로 프레임을 만들어 학습한다.

**기술:** C(zlib, pthread), Python 3.11(보드) / 3.12(Mac), numpy, ONNX Runtime 1.31.1, PyTorch(MPS), torchvision, Docker `debian:12`.

**Spec:** `docs/superpowers/specs/2026-11-07-dvs-person-action-design.md`

## Global Constraints

- 사용자에게 보이는 답변과 문서는 한글로 쓴다.
- 과제마다 결과를 `docs/E85096-LINUX510-BOOT.md` 새 절(11.37부터)과 `docs/BRINGUP_STATUS.md`에 적는다. 관찰, 가설, 보드에 바꾼 것을 나눠 적는다.
- 로컬 커밋은 해도 된다. push는 사용자에게 묻는다.
- 접속(2026-11-07 사용자 결정): 사용자가 USB를 꽂아 둔다.
  - ssh, scp, rsync는 USB NCM `philip@192.168.7.1`로 한다. 30 MB 전송이 13.0 MB/s였고, Wi-Fi는 6.5 MB/s였다. USB가 빠져 있으면 Wi-Fi `philip@192.168.0.236`을 쓴다. 아래에서 `$B`는 이 주소다.
  - root가 필요한 일은 `tools/board/dvsact-install.sh` 하나로 모은다. 실행은 ACM root 콘솔로 한다: `python3 tools/host/acm.py "sh /var/tmp/dvsact-stage/dvsact-install.sh" 120`. 결과는 `tail`이나 `cat -v`로 본다(CLAUDE.md의 acm.py 함정).
  - sudo 비밀번호는 쓰지 않는다. 명령 줄, 파일, 저장소, 메모리 어디에도 적지 않는다.
  - 웹 응답 측정(`webbench.py`)은 11.30과 같게 Wi-Fi 주소 `http://192.168.0.236:8080`으로 한다.
- 하지 않는 것: 재부팅, boot 굽기, nginx·ssh·e850-hdmi·e850-wifi·워치독 중지.
- dvslive 재시작은 `curl -s -X POST -d '{}' http://192.168.0.236:8080/sensor`로 한다(기존 경로, 약 5초 뒤 systemd가 다시 띄움).
- 보드는 Python 3.11.2, numpy 1.24.2이고 pip가 없다. `dvsact.py`와 `dvsctl.py`는 numpy 1.24에 있는 API만 쓴다. Mac numpy 2.x에서도 돌아야 한다.
- CPU 배치: 분류 스레드와 ONNX Runtime은 CPU4~6, 웹은 CPU7, 장치 IRQ는 CPU0이다.
- spec 상수:
  - 단계 60 000 µs, 격자 2×120×160(960×720을 6×6으로 묶음), 입력은 `log1p(개수)` float32
  - 클래스 순서: `none, still, walk, sit_down, stand_up, pick_up, lie_down, fall, other`
  - ONNX: `x`[1,2,120,160], `h`[1,128] → `y`[1,9], `h2`[1,128], opset 17
  - 판정 3단계 연속, `fall`은 확률 ≥ 0.6. GRU를 되돌리는 공백 3 s
  - 녹화: 카운트다운 3 s, 기본 6 s, 빈 공간 300 MB 미만이면 거부
- 파일 위치:
  - 보드: `/var/lib/dvsact/`(philip 소유: `model.onnx`, `act.jsonl`, `enable`), `/var/tmp/dvsrec/`(philip 소유)
  - Mac: 녹화 `~/e850-logs/dvsrec/`, 공개 데이터 `~/e850-act-data/`, 학습 결과 `~/e850-act-runs/`. 모두 저장소 밖이다.
- C 빌드와 시험은 `dvsview.c` 머리의 Docker 명령으로 한다. 시험 빌드(`-DDVSVIEW_TEST`)가 `selftest ok`를 내야 한다.
- Mac Python: venv `~/.venvs/dvsact`(torch, torchvision, onnx, onnxruntime, numpy, aedat). 시험은 표준 `unittest`로 한다.

**spec과 달라진 점** (구현 세부, 사용자에게 보고함):
1. `dvs_record(NULL)`은 녹화한 패킷에서 나온 이벤트 수를 돌려준다. 녹화와 재생의 이벤트 수를 비교하는 데 쓴다.
2. `--act`는 `dvslive.service`를 고치지 않고 켠다. `dvslive.sh`가 `/var/lib/dvsact/enable` 파일이 있으면 고정 인자 `--act /var/lib/dvsact/act.jsonl --act-model /var/lib/dvsact/model.onnx`를 붙인다. 그래서 켜고 끌 때 sudo가 필요 없다.
3. `dvs_events`는 처음에 `*cursor == UINT64_MAX`를 받으면 지금 쓰는 위치부터 시작한다. 디코더가 앞질렀는지는 RING/2를 여유로 두고 판정한다.
4. `dvsctl.pin(cpu)`를 `pin(cpus, name)`으로 넓힌다.
5. 단계 시간과 `lost`는 `[dvsview]` 통계 줄에 붙이지 않고 따로 `[dvsact]` 줄(2초마다)로 쓴다.

## Review Focus

1. **표시 시계가 돌아감:** `struct ev.t`는 uint32 µs라 약 71.6분마다 0으로 돈다. live는 며칠씩 돈다. 돌아가는 곳에서도 칸이 끊기지 않고 GRU가 되돌려지지 않아야 한다. → Task 3 `test_binner_wrap`
2. **분류가 늦어 링을 앞질림:** GIL이나 CPU 경쟁으로 늦어지면, 덮인 이벤트를 쓰레기로 읽지 않고 건너뛴 뒤 `act_lost`에 센다. → Task 2 selftest (c)
3. **녹화 중 dvslive 종료:** 녹화 중에 `POST /sensor`로 SIGTERM이 오면 gz를 닫고 `.json`에 `"complete": false`를 남긴다. 학습은 이 클립을 쓰지 않는다. → Task 5 `test_recorder_abort`, Task 7 `test_build_skips_incomplete`
4. **센서 시간 공백:** 버퍼를 잃거나 다시 시작해서 3 s를 넘는 공백이 생기면 reset을 한 번만 낸다. 빈 프레임을 쏟아내지 않는다. 3 s 미만 공백은 건너뛴다. → Task 3 `test_binner_gap`
5. **모델이나 ONNX Runtime이 없음:** `--act`가 켜졌는데 모델 파일이 없거나 import에 실패하면, live와 웹은 그대로 돌고 `[dvsact] off: …` 한 줄만 남긴다. → Task 4 `test_start_missing_model`

---

### Task 1: 공개 데이터 받기와 이벤트 읽기 (Mac)

**Files:**
- Create: `tools/host/act/readers.py`, `tools/host/act/test_readers.py`
- 데이터: `~/e850-act-data/paf/`, `~/e850-act-data/dailydvs/` (저장소 밖)

**Interfaces:**
- Produces: `read_events(path: str) -> Events`. `Events = namedtuple('Events', 't x y p w h')`이다.
  - t: int64 µs, 클립 처음이 0
  - x, y: int16
  - p: uint8, 0 = ON, 1 = OFF (dvsview와 같다)
  - w, h: 센서 크기
- 확장자로 형식을 고른다.
  - `.aedat`: PAF, jAER DAVIS346, 346×260
  - `.aedat4`: DailyDVS-200, `aedat` 패키지, 320×240
  - `.bin`: Task 2 `--replay` 출력, `struct ev` 8 B 리틀 엔디언, 960×720, 행 = `y & 0x3ff`, 극성 = `y >> 15`

- [ ] **Step 1: venv를 만든다**
  Run: `python3 -m venv ~/.venvs/dvsact && ~/.venvs/dvsact/bin/pip install torch torchvision onnx onnxruntime numpy aedat`
  Expected: `~/.venvs/dvsact/bin/python -c "import torch, torchvision, onnx, onnxruntime, aedat"`가 오류 없이 끝난다. `aedat`가 macOS arm64에서 설치되지 않으면 `dv-processing`으로 바꾸고 그 사실을 적는다.

- [ ] **Step 2: 데이터를 받는다**
  - PAF: 저장소의 `toy.aedat`와 `snn.py`를 `~/e850-act-data/paf/`에 받는다. README의 SharePoint 링크 3개(Action Recognition, Fall Detection, 그리고 확인용 Pedestrian)를 `curl -L`로 받아 본다. 로그인 페이지가 나오면 사용자에게 브라우저로 받아 그 디렉터리에 넣어 달라고 한다.
  - DailyDVS-200: Google Drive 폴더의 구조와 파일 이름을 먼저 본다(`gdown --folder` 목록). `C0P3M0S1_…`에서 클래스 번호를 알 수 있는지 `resource/action_description.csv`와 맞춰 본다. 알 수 없고 `train.txt`가 Baidu에만 있으면 사용자에게 묻는다. 그래도 안 되면 DailyDVS-200은 빼고 그 사실을 적는다.
  - spec 7.1의 클래스(109, 107, 148, 93, 165, 100, 99, 97, 80)와, `other`용으로 클래스마다 5개씩만 받는다.

- [ ] **Step 3: 실패하는 시험을 쓴다** (`tools/host/act/test_readers.py`)

```python
def test_paf_toy(self):
    e = read_events(os.path.expanduser('~/e850-act-data/paf/toy.aedat'))
    self.assertEqual((e.w, e.h), (346, 260))
    self.assertTrue(len(e.t) > 1000 and (np.diff(e.t) >= 0).all() and e.t[0] == 0)
    self.assertTrue(0 <= e.x.min() and e.x.max() < 346 and 0 <= e.y.min() and e.y.max() < 260)
    self.assertTrue(set(np.unique(e.p)) <= {0, 1})

def test_replay_bin(self):
    raw = np.array([(1000, 5, 16), (1494, 5, 23 | 1 << 15 | 1 << 14), (2000, 959, 719)],
                   dtype=[('t', '<u4'), ('x', '<u2'), ('y', '<u2')])
    # 임시 파일에 raw.tobytes()를 쓰고 읽는다
    e = read_events(path)
    self.assertEqual(e.t.tolist(), [0, 494, 1000]); self.assertEqual(e.y.tolist(), [16, 23, 719])
    self.assertEqual(e.p.tolist(), [0, 1, 0]); self.assertEqual((e.w, e.h), (960, 720))

def test_aedat4_one(self):   # 받은 DailyDVS 파일이 없으면 skipTest
    e = read_events(first_aedat4)
    self.assertEqual((e.w, e.h), (320, 240)); self.assertTrue(e.x.max() < 320 and e.y.max() < 240)
```

- [ ] **Step 4: 실패를 확인한다**
  Run: `cd tools/host/act && ~/.venvs/dvsact/bin/python -m unittest test_readers -v`
  Expected: `ImportError` 또는 `NameError: read_events`

- [ ] **Step 5: `read_events`를 구현한다**
  - `.aedat`: 주소 비트 배치와 머리글(`#` 줄)은 PAF의 `snn.py`가 읽는 방식을 기준으로 한다.
  - `.aedat4`: `aedat.Decoder`의 polarity 패킷을 이어 붙인다.
  - `.bin`: `np.fromfile`로 읽는다. t는 처음 값과의 uint32 차이로 펼친다.

- [ ] **Step 6: 통과를 확인한다** (같은 명령, `OK`)

- [ ] **Step 7: 기록하고 커밋한다**
  11.37에 받은 데이터셋, 클래스별 클립 수, 못 받은 것과 이유를 적는다.
  `git add tools/host/act/readers.py tools/host/act/test_readers.py docs/ && git commit`

---

### Task 2: `dvsview.c` — `dvs_events`, `dvs_record`, `--replay`

**Files:**
- Modify: `tools/linux510/dvsview.c`
  - `S` 구조체: `rec`, `rec_lock`, `rec_events`, `rec_err`, `act_lost`
  - `decode_lines()`(345), `dvs_stats` snprintf(render 안 `S.stats`), `dvs_stats()` 뒤에 새 export 두 개
  - 시험 `main()`의 처음과 selftest

**Interfaces:**
- Produces:
  - `int dvs_events(struct ev *dst, int max, uint64_t *cursor)`
    - `*cursor == UINT64_MAX`면 `*cursor = wr`로 두고 0을 돌려준다.
    - `wr - *cursor > RING / 2`면 `act_lost += wr - RING/2 - *cursor`를 하고 `*cursor = wr - RING/2`로 옮긴다.
    - 그다음 `min(max, wr - *cursor)`개를 복사한다(링 끝에서 두 번 memcpy). cursor를 옮기고 개수를 돌려준다.
    - `wr`는 acquire로 읽는다.
  - `int dvs_record(const char *path)`
    - path가 있으면 `gzopen(path, "wb1")`을 하고 0을 돌려준다. 이미 녹화 중이면 `-EBUSY`, 열기에 실패하면 `-errno`다.
    - NULL이면 닫고 녹화 동안 디코딩된 이벤트 수(≥ 0)를 돌려준다. 녹화 중이 아니면 `-EINVAL`, 녹화 중 gzwrite가 실패했으면 `-EIO`다.
  - 녹화 형식: `decode_lines()`가 줄을 0으로 지우기 전에 `rec_lock` 안에서 그 줄의 패킷 단어 n개(버퍼 그대로, 빅 엔디언)와 0 단어 하나를 쓴다. gzwrite가 실패하면 그 자리에서 닫고 `rec_err`를 세운다. 실패가 ingest를 막아서는 안 된다.
  - `dvs_stats()` JSON에 `"act_lost":<누적>`을 더한다.
  - 시험 빌드: `t --replay in.gz out.bin`
    - 0 단어로 나뉜 묶음을 `decode()`에 차례로 넣는다. 부를 때마다 새로 생긴 `struct ev`를 `out.bin`에 덧붙인다.
    - 끝나면 `replay: <N> events`를 찍고 0을 돌려준다. `filter_ms`는 0이다.

- [ ] **Step 1: selftest에 실패하는 시험을 넣는다** (`main()`의 `puts("selftest ok")` 앞)
  - (a) `reset()` 뒤 `c = UINT64_MAX`: `dvs_events(dst, 16, &c) == 0`, `c == nev()`. 이어서 `feed(groups, 3)` → `dvs_events(dst, 16, &c) == 4`이고, `dst[i].x/.y`가 `want[i]`와 같다.
  - (b) 녹화 왕복:
    - `dvs_record(tmp)`를 한다. 두 줄짜리 버퍼를 `decode_lines()`에 넣는다(1줄: `groups` 3단어, 2줄: `ts` 4단어, 빅 엔디언, 나머지 0).
    - `n = dvs_record(NULL)`은 그동안 늘어난 이벤트 수와 같다.
    - `reset()` 뒤 같은 파일을 `replay`로 풀면 같은 (t, x, y)가 나온다.
    - `dvs_record(tmp)`를 두 번 하면 두 번째는 `-EBUSY`다. 녹화 중이 아닐 때 `dvs_record(NULL)`은 `-EINVAL`이다.
  - (c) 앞질림: `c = nev()`에서 `ev_add`로 `RING/2 + 100`개를 넣는다 → `dvs_events(dst, 16, &c) == 16`, `act_lost`가 100 늘고, `c == nev() - RING/2 + 16`이다.
  - 실패하면 `fprintf(stderr, …), 1`로 끝내는 기존 방식을 따른다.

- [ ] **Step 2: 실패를 확인한다**
  Run: `dvsview.c` 머리의 Docker 명령(파일 인자 없이).
  Expected: 컴파일 오류 `dvs_events` / `dvs_record` undeclared

- [ ] **Step 3: 구현한다** (위 Interfaces 그대로. `replay()`는 `main()` 처음의 `alloc_ring()` 바로 뒤에서 `argc == 4 && !strcmp(argv[1], "--replay")`일 때 부른다.)

- [ ] **Step 4: 통과를 확인한다**
  Run: 같은 Docker 명령
  Expected: `selftest ok`. 경고 없이 `-Wall` 빌드된다.

- [ ] **Step 5: 커밋한다** `git add tools/linux510/dvsview.c && git commit` (보드 배포는 Task 4에서 함께 한다)

---

### Task 3: `dvsact.py` — 입력, Binner, Judge, 기록 줄

**Files:**
- Create: `tools/linux510/dvsact.py`, `tools/linux510/test_dvsact.py`

**Interfaces:**
- Produces (모듈 수준):
  - `CLASSES`(Global Constraints의 순서), `MOVE = {'walk','sit_down','stand_up','pick_up','lie_down','other'}`
  - `STEP_US = 60000`, `GAP_US = 3000000`, `HOLD = 3`, `FALL_P = 0.6`, `GW, GH = 160, 120`
  - `EV = np.dtype([('t','<u4'),('x','<u2'),('y','<u2')])`
  - `split(ev: np.ndarray[EV]) -> (t uint32, x, row, pol)`: row = `y & 0x3ff`, pol = `y >> 15`. EV_SUP(bit 14)는 버린다.
  - `counts(x, y, pol, w=960, h=720) -> np.ndarray` uint16 [2,GH,GW]. 칸은 `gx = x*GW//w`, `gy = y*GH//h`, `np.bincount`로 센다.
  - `to_input(c) -> np.ndarray` float32 [1,2,GH,GW] = `log1p(c)`
  - `class Binner(w=960, h=720)`: `push(t, x, y, pol) -> list`. 원소는 `('frame', t0_us: int, counts)` 또는 `('reset', t_us: int, None)`이다.
    - t는 uint32이고, 직전 값과의 uint32 차이로 int64로 펼친다(표시 시계는 뒤로 가지 않는다).
    - 칸 경계는 첫 이벤트에서 STEP_US 간격으로 잡는다. 지금 칸 끝을 넘는 이벤트가 오면 지금 칸을 내보낸다. 사이의 빈 칸은 내보내지 않고, 새 칸은 그 이벤트가 속한 칸이다.
    - 직전 이벤트와 GAP_US 넘게 떨어지면 지금 칸을 버리고 `reset`을 하나 낸 뒤 새로 시작한다.
  - `frames_of(t, x, y, pol, w, h) -> (t0s int64 [N], counts uint16 [N,2,GH,GW])`: 오프라인용. Binner를 그대로 쓰고 reset은 무시한다.
  - `class Judge`: 처음 상태는 `'none'`이다. `step(probs: np.ndarray[9]) -> dict | None`.
    - 후보는 argmax다. 후보가 `fall`인데 확률 < FALL_P면 후보가 없는 것으로 본다.
    - 같은 후보가 HOLD번 이어지고 지금 상태와 다르면 상태를 바꾸고 `{'event','state','prev','p'}`를 돌려준다.
    - event 규칙(순서대로 처음 맞는 것): `fall` → `fall`, prev가 `none` → `appear`, state가 MOVE에 있음 → `move`, 그 밖 → state 이름
  - `line(rec: dict, now_ns: int) -> str`: 키 순서 `t_us, time, event, state, prev, p`. time은 UTC 마이크로초에 `Z`, p는 소수 둘째 자리다.

- [ ] **Step 1: 실패하는 시험을 쓴다** (`unittest`, numpy만 씀)

```python
def test_counts_cells(self):
    c = counts(np.array([0, 5, 959]), np.array([0, 5, 719]), np.array([0, 0, 1]))
    self.assertEqual(c.shape, (2, 120, 160)); self.assertEqual(c[0, 0, 0], 2); self.assertEqual(c[1, 119, 159], 1)
    self.assertEqual(counts(np.array([345]), np.array([259]), np.array([0]), 346, 260)[0, 119, 159], 1)

def test_to_input(self):
    x = to_input(np.full((2, 120, 160), 3, np.uint16))
    self.assertEqual((x.shape, x.dtype), ((1, 2, 120, 160), np.float32)); self.assertAlmostEqual(float(x[0, 0, 0, 0]), np.log1p(3), 6)

def test_binner_bins(self):      # 1 ms마다 하나씩 200 ms
    out = push_ms(Binner(), start=0, n=200)
    self.assertEqual([k for k, *_ in out], ['frame'] * 3)
    self.assertEqual([int(f[2].sum()) for f in out], [60, 60, 60])

def test_binner_wrap(self):      # Review Focus 1: uint32가 0으로 도는 곳을 지남
    out = push_ms(Binner(), start=2**32 - 90000, n=200)
    self.assertEqual([k for k, *_ in out], ['frame'] * 3)
    self.assertEqual([int(f[2].sum()) for f in out], [60, 60, 60])

def test_binner_gap(self):       # Review Focus 4
    b = Binner(); out = push_ms(b, 0, 100) + push_ms(b, 2100000, 100)
    self.assertNotIn('reset', [k for k, *_ in out])
    b = Binner(); out = push_ms(b, 0, 100) + push_ms(b, 5100000, 100)
    self.assertEqual([k for k, *_ in out].count('reset'), 1)
    self.assertTrue(all(f[2].sum() > 0 for f in out if f[0] == 'frame'))

def test_judge(self):
    j, P = Judge(), lambda k, p=0.9: onehot(CLASSES.index(k), p)
    self.assertIsNone([j.step(P('none')) for _ in range(3)][-1])
    r = [j.step(P('walk')) for _ in range(3)]
    self.assertEqual((r[1], r[2]['event'], r[2]['prev']), (None, 'appear', 'none'))
    self.assertTrue(all(j.step(P('fall', 0.55)) is None for _ in range(5)))
    self.assertEqual([j.step(P('fall', 0.7)) for _ in range(3)][-1]['event'], 'fall')
    self.assertEqual([j.step(P('still')) for _ in range(3)][-1]['event'], 'still')
    self.assertEqual([j.step(P('none')) for _ in range(3)][-1]['event'], 'none')
    self.assertEqual([j.step(P('fall')) for _ in range(3)][-1]['event'], 'fall')   # none 다음이라도 fall이 먼저

def test_line(self):
    s = line({'event': 'fall', 'state': 'fall', 'prev': 'walk', 'p': 0.8734}, 1794030632123456000)
    self.assertEqual(s, '{"t_us": 1794030632123456, "time": "2026-11-07T05:50:32.123456Z", '
                        '"event": "fall", "state": "fall", "prev": "walk", "p": 0.87}')
```

`push_ms(b, start, n)`은 `start`(µs)부터 1 ms마다 (x=10, y=10, ON) 이벤트를 `uint32`로 `b.push`에 넣는 시험 도우미다. `onehot`은 그 클래스만 p이고 나머지가 (1−p)/8인 배열이다.

- [ ] **Step 2: 실패를 확인한다**
  Run: `~/.venvs/dvsact/bin/python -m unittest discover -s tools/linux510 -p 'test_dvsact.py' -v`
  Expected: `ModuleNotFoundError: dvsact`

- [ ] **Step 3: 구현한다** (Interfaces 그대로. onnxruntime은 이 과제에서 import하지 않는다.)

- [ ] **Step 4: 통과를 확인한다** (같은 명령, `OK`). 보드 numpy 1.24에서도 확인한다: `scp`로 두 파일을 보드 `/var/tmp/`에 보내고, `ssh $B 'cd /var/tmp && python3 -m unittest test_dvsact'` → `OK`.

- [ ] **Step 5: 커밋한다** `git add tools/linux510/dvsact.py tools/linux510/test_dvsact.py && git commit`

---

### Task 4: 보드 분류 스레드와 무작위 모델로 성능 시험

**Files:**
- Modify: `tools/linux510/dvsact.py`(`start()`), `tools/linux510/dvsctl.py`(`pin` 37~50, `serve` 160), `tools/linux510/ov5647-cap.py`(머리 docstring, `ap.add_argument` 91~94 뒤, `DvsLive.__init__` 282~300), `tools/board/dvslive.sh`(마지막 `exec` 줄)
- Create: `tools/host/act/model.py`, `tools/host/act/export.py`, `tools/host/act/test_model.py`, `tools/board/dvsact-install.sh`

**Interfaces:**
- Consumes: Task 2 `dvs_events`, `dvs_stats` `act_lost`. Task 3 `split`, `Binner`, `to_input`, `Judge`, `line`, `CLASSES`.
- Produces:
  - `dvsctl.pin(cpus: set, name: str = 'dvs-web')`. 하는 일은 지금과 같고(uclamp min 1024, affinity), 이름과 CPU 집합만 받는다. `serve()`는 `pin({WEB_CPU})`를 부른다.
  - `dvsact.start(lib, out_path: str, model_path: str, cpus=frozenset({4, 5, 6})) -> threading.Thread | None`
    - `start()` 자신은 `import onnxruntime`과 모델 파일이 있는지만 확인한다. 둘 중 하나라도 실패하면 `[dvsact] off: <이유>`를 쓰고 None을 돌려준다.
    - 세션은 데몬 스레드 안에서 `dvsctl.pin(cpus, 'dvs-act')` 뒤에 만든다(`intra_op_num_threads=1`, `inter_op_num_threads=1`, CPU EP). 그래야 ONNX Runtime 스레드가 같은 코어에 묶인다.
    - 60 ms마다 `dvs_events`로 최대 65 536개씩 받는다(numpy `EV` 버퍼, `ctypes.c_uint64` cursor는 처음에 `UINT64_MAX`).
    - `frame`마다 한 단계를 돌린다(softmax → `Judge.step` → `line`을 append하고 flush). `reset`이면 h를 0으로 둔다.
    - 상태가 바뀌면 stderr에 `[dvsact] <event> <state> p 0.87`을 쓴다. 2초마다 `[dvsact] steps N, step median X ms max Y ms, lost L`을 쓴다.
    - 스레드 안에서 실패하면(세션 생성, 실행) `[dvsact] off: <이유>`를 쓰고 스레드만 끝낸다.
  - `ov5647-cap.py --act FILE --act-model PATH`: 둘 다 있으면 `dvs_start` 뒤에 `sys.path.append('/usr/local/share/dvsview')`를 하고 `dvsact.start(self.lib, a.act, a.act_model)`를 부른다.
  - `dvslive.sh`: `[ -e /var/lib/dvsact/enable ]`이면 `exec` 인자 앞에 `--act /var/lib/dvsact/act.jsonl --act-model /var/lib/dvsact/model.onnx`를 붙인다.
  - `model.Step(nn.Module)`: `forward(x [B,2,120,160], h [B,128]) -> (logits [B,9], h2 [B,128])`. 구성은 spec 6절 그대로다(mobilenet_v3_small features, 공간 평균, Linear 576→128, GRUCell 128, Linear 128→9). `Step.imagenet()`은 ImageNet 가중치를 싣고, 첫 conv를 RGB 평균 × 1.5로 바꾼다.
  - `export.py (--random | CKPT) OUT.onnx`: ONNX(이름 x, h, y, h2, opset 17, `dynamo=False`)를 쓴다. 무작위 입력 5개로 torch와 onnxruntime을 비교해 `parity ok max_abs=<v>`를 찍는다. 1e-4를 넘으면 종료 코드 1이다.
  - `dvsact-install.sh`(root, `/var/tmp/dvsact-stage`에서 실행)
    - 무대에 있는 것만 설치한다: `libdvsview.so`→`/usr/local/lib`, `dvsctl.py`·`dvsctl.html`·`dvsact.py`→`/usr/local/share/dvsview`, `ov5647-cap.py`·`dvslive.sh`→`/usr/local/bin`, `ort/*`→`/usr/local/lib/python3.11/dist-packages`
    - 바뀌는 파일은 `.prev-act`로 한 번만 백업한다.
    - `install -d -o philip -g philip /var/lib/dvsact /var/tmp/dvsrec`
    - 설치한 파일마다 sha256을 찍는다. 다시 실행해도 결과가 같다.

- [ ] **Step 1: 실패하는 시험을 쓴다**
  - `tools/linux510/test_dvsact.py`에 `test_start_missing_model`(Review Focus 5)을 더한다: `start(object(), tmp + '/a.jsonl', '/nonexistent.onnx')`가 None이고, stderr(redirect)에 `[dvsact] off`가 있다.
  - `tools/host/act/test_model.py`: `Step()(torch.zeros(2, 2, 120, 160), torch.zeros(2, 128))`의 모양이 `(2, 9)`, `(2, 128)`이다. `Step.imagenet()`의 첫 conv 가중치 모양이 `(16, 2, 3, 3)`이다.
- [ ] **Step 2: 실패를 확인한다** (두 시험 모두 ImportError 또는 AttributeError)
- [ ] **Step 3: 구현한다** (Interfaces 그대로)
- [ ] **Step 4: 통과를 확인한다**
  Run: 위 두 unittest와 `~/.venvs/dvsact/bin/python tools/host/act/export.py --random ~/e850-act-runs/random.onnx`
  Expected: 시험 `OK`, `parity ok max_abs=…`(≤ 1e-4)
- [ ] **Step 5: 무대를 만들어 보드에 올린다**
  - `libdvsview.so`를 Docker로 빌드한다.
  - ONNX Runtime 1.31.1 cp311 manylinux_2_28_aarch64와 flatbuffers, packaging, protobuf를 `pip download --only-binary=:all: --platform … --python-version 3.11`로 받아 `stage/ort/`에 푼다(11.36 실측과 같은 방법).
  - 바뀐 파일과 `dvsact-install.sh`를 같은 무대에 둔다. `scp -r stage $B:/var/tmp/dvsact-stage`
- [ ] **Step 6: ACM root 콘솔로 설치한다**
  Run: `python3 tools/host/acm.py "sh /var/tmp/dvsact-stage/dvsact-install.sh" 120`
  Expected: sha256 목록이 나오고, `ssh $B python3 -c "import onnxruntime; print(onnxruntime.__version__)"`가 `1.31.1`이다.
- [ ] **Step 7: 켜기 전 기준을 잰다**
  - `python3 tools/host/webbench.py http://192.168.0.236:8080/frame.png 100 10 keep`
  - `/tmp/dvslive.log`의 `[dvsview]` 마지막 10줄
- [ ] **Step 8: 무작위 모델로 켠다**
  - `scp ~/e850-act-runs/random.onnx $B:/var/lib/dvsact/model.onnx`
  - `ssh $B touch /var/lib/dvsact/enable`
  - `POST /sensor {}`로 다시 시작한다.
  Expected: 10초 안에 `/tmp/dvslive.log`에 `[dvsact] steps`가 나온다. `ps -L -o psr,comm -p <MainPID>`(`systemctl show -p MainPID --value dvslive`)에서 `dvs-act` 스레드의 psr이 4~6이다.
- [ ] **Step 9: 5분 동안 성능을 확인한다**
  기준(spec 10절):
  - `[dvsview]` fps 104, ingest max ≤ 7 ms
  - `[dvsact]` step의 최대가 50 ms 미만이고 lost가 0
  - webbench 중앙값이 Step 7보다 10% 넘게 늘지 않음
  못 미치면 숫자를 적고 멈춘다. spec 11절의 별도 프로세스 안으로 갈지 사용자와 정한다.
- [ ] **Step 10: 끄고 기록하고 커밋한다**
  - `rm /var/lib/dvsact/enable`, `POST /sensor {}`로 끈다. 무작위 모델의 기록은 지운다.
  - 11.38에 설치한 것(sha256)과 성능 숫자를 적는다.
  - `git add tools/ docs/ && git commit`

---

### Task 5: 녹화 `/rec`과 페이지 버튼

**Files:**
- Modify: `tools/linux510/dvsctl.py`(docstring 경로 표, `do_GET`, `do_POST`, `start()`), `tools/linux510/dvsctl.html`(새 `<section>` 녹화)
- Create: `tools/linux510/test_dvsctl.py`

**Interfaces:**
- Consumes: Task 2 `dvs_record`. Task 3 `CLASSES`.
- Produces:
  - `dvsctl.rec_check(label: str, s: int, free_bytes: int, busy: bool) -> int`. 200 / 400(라벨이 CLASSES 밖이거나 s가 1~30 밖) / 409(busy) / 507(free < 300 MB)을 돌려준다.
  - `class Recorder(lib, dirpath='/var/tmp/dvsrec', countdown=3.0)`
    - `start(label, s) -> int`: rec_check를 하고, 200이면 스레드에서 카운트다운 → `dvs_record(<dir>/<UTC %Y%m%dT%H%M%SZ>-<label>.gz)` → s초 → `dvs_record(None)` → `.json`을 쓴다.
    - `status() -> {'state': 'idle'|'countdown'|'recording', 'left_s': float, 'last': str|None}`
    - `close(complete: bool)`: 녹화 중이면 `dvs_record(None)`을 하고 `.json`을 쓴다.
  - `.json` 내용: `label, start`(UTC ISO, 녹화 시작 순간), `s, events`(dvs_record(NULL) 반환값, 음수면 0), `margin, flush_us`(sensor()의 now), `sensor: 's5krc1s', complete`
  - `start()`가 `atexit`와 SIGTERM 경로에서 `close(False)`를 부르게 한다. ov5647-cap.py는 SIGTERM을 KeyboardInterrupt로 바꾸고 `finally` 뒤에 끝나므로 `atexit`가 돈다.
  - HTTP: `POST /rec {"label": .., "s": 6}` → 코드와 `status()`. `GET /rec` → `status()`.
  - 페이지: 라벨 선택(CLASSES, 한글 이름 표시), 녹화 버튼, 1초마다 `GET /rec`으로 상태와 남은 초 표시.

- [ ] **Step 1: 실패하는 시험을 쓴다** (가짜 lib: `dvs_record`가 호출을 기록하고, 닫을 때 1234를 돌려준다)

```python
def test_rec_check(self):
    self.assertEqual(rec_check('fall', 6, 10**9, False), 200)
    self.assertEqual(rec_check('jump', 6, 10**9, False), 400); self.assertEqual(rec_check('fall', 0, 10**9, False), 400)
    self.assertEqual(rec_check('fall', 6, 10**9, True), 409); self.assertEqual(rec_check('fall', 6, 299 * 2**20, False), 507)

def test_recorder_complete(self):
    r = Recorder(fake, tmp, countdown=0); self.assertEqual(r.start('walk', 1), 200); wait_idle(r)
    j = json.load(open(only(tmp, '.json')))
    self.assertEqual((j['label'], j['events'], j['complete']), ('walk', 1234, True)); self.assertTrue(only(tmp, '.gz'))

def test_recorder_abort(self):   # Review Focus 3
    r = Recorder(fake, tmp, countdown=0); r.start('fall', 30); wait_state(r, 'recording'); r.close(False)
    self.assertEqual(fake.calls[-1], None); self.assertFalse(json.load(open(only(tmp, '.json')))['complete'])
```

- [ ] **Step 2: 실패를 확인한다**
  Run: `~/.venvs/dvsact/bin/python -m unittest discover -s tools/linux510 -p 'test_dvsctl.py' -v`
  Expected: ImportError
- [ ] **Step 3: 구현한다**
- [ ] **Step 4: 통과를 확인한다** (같은 명령, `OK`)
- [ ] **Step 5: 보드에 설치한다** (Task 4 Step 5~6처럼 `dvsctl.py`, `dvsctl.html`만 무대에 둔다. ACM root 콘솔로 설치하고 `POST /sensor {}`로 다시 시작한다.)
- [ ] **Step 6: 녹화와 재생을 비교한다**
  - `curl -s -X POST -d '{"label":"none","s":6}' http://192.168.0.236:8080/rec`
  - 10초 뒤 `GET /rec`의 `last`를 확인하고, `rsync -av $B:/var/tmp/dvsrec/ ~/e850-logs/dvsrec/`로 받는다.
  - Docker 시험 빌드로 `t --replay X.gz X.bin`을 돌린다.
  Expected: `replay: N events`의 N이 `.json`의 `events`와 같다. live `[dvsview]` fps는 녹화 중에도 104다.
- [ ] **Step 7: 기록하고 커밋한다** (11.39, `git add tools/linux510/dvsctl.py tools/linux510/dvsctl.html tools/linux510/test_dvsctl.py docs/ && git commit`)

---

### Task 6: 녹화 1회차 (사용자가 함께함)

**Files:**
- Create: `tools/host/act/replay.sh` — `replay.sh DIR`이 DIR의 `*.gz` 중 `.bin`이 없는 것을 Docker 시험 빌드로 풀고, `replay: N` 줄과 `.json`의 events를 나란히 찍는다.

- [ ] **Step 1: 배치를 정한다.** DVS를 옆에서 보게 고정한다. 사람 전신과 매트리스가 화면에 들어오는지 `/frame.png`로 사용자와 확인한다. 거리와 높이는 잰 값으로 적는다.
- [ ] **Step 2: 녹화한다.** 사용자가 휴대전화로 8080 페이지를 열고 spec 7.1 "우리 녹화" 열의 동작을 5~10번씩 찍는다.
  - 방향(왼쪽, 오른쪽, 앞, 뒤)을 섞는다.
  - `none`은 빈 방, `still`은 서 있기와 앉아 있기를 반반, `other`는 팔 흔들기 등이다.
  - 넘어짐은 매트리스 위에서만, 사용자가 판단해서 한다.
- [ ] **Step 3: Mac으로 옮긴다.** `rsync -av $B:/var/tmp/dvsrec/ ~/e850-logs/dvsrec/`를 하고, 크기가 같은지 확인한 뒤 보드에서 지운다.
- [ ] **Step 4: 푼다.**
  Run: `sh tools/host/act/replay.sh ~/e850-logs/dvsrec`
  Expected: 모든 클립에서 N이 events와 같다. `complete: false` 클립은 따로 센다.
- [ ] **Step 5: 기록하고 커밋한다.** 11.40에 클래스별 클립 수, 길이, 이벤트 수, 배치를 적는다. `git add tools/host/act/replay.sh docs/ && git commit`

---

### Task 7: 학습 데이터 만들기 (Mac)

**Files:**
- Create: `tools/host/act/dataset.py`, `tools/host/act/test_dataset.py`

**Interfaces:**
- Consumes: Task 1 `read_events`. Task 3 `frames_of`, `CLASSES`.
- Produces:
  - `PAF_MAP: dict[str, str]`, `DAILY_MAP: dict[int, str]`: spec 7.1 표 그대로다. DailyDVS에서 표에 없는 번호는 `other`이고, 클래스마다 `OTHER_PER_CLASS = 5`개만 쓴다.
  - `noise(c: np.ndarray[N,2,GH,GW]) -> np.ndarray[2]`: 극성별 셀 값의 중앙값(배경 추정)
  - `activity(c) -> np.ndarray[N]`: 칸마다 `sum(max(c − noise, 0))`를 가운데 정렬 5칸(0.3 s) 이동 평균한 값(`np.convolve(…, np.ones(5) / 5, 'same')`)
  - `label_mask(cls: str, act: np.ndarray[N]) -> np.ndarray[N] bool`: `none`, `still`은 17번째 칸(1 s)부터 true, 나머지는 `argmax(act) + 5`부터 true
  - `build(items: list[tuple[str, str, str]], out_dir: str)`: items는 (경로, 클래스, 출처)다. 클립마다 `<out>/<출처>/<이름>.npz`에 `t0s, counts`(uint8, 255에서 자름), `mask, cls`(int)를 쓰고, `<out>/manifest.jsonl`에 한 줄을 더한다. 우리 녹화는 옆 `.json`이 `complete: true`인 것만 쓴다.
  - CLI: `dataset.py --paf DIR --daily DIR --ours DIR --out ~/e850-act-runs/data`

- [ ] **Step 1: 실패하는 시험을 쓴다**

```python
def test_maps(self):
    self.assertEqual((PAF_MAP['falling'], PAF_MAP['slumping-down'], PAF_MAP['getting-up']), ('fall', 'sit_down', 'stand_up'))
    self.assertEqual((DAILY_MAP[109], DAILY_MAP[148], DAILY_MAP[100]), ('fall', 'sit_down', 'pick_up'))
    self.assertTrue(set(PAF_MAP.values()) | set(DAILY_MAP.values()) <= set(CLASSES))

def test_label_mask_peak(self):     # 40번째 칸에 움직임이 몰림
    c = np.full((100, 2, 120, 160), 2, np.uint16); c[38:43, :, 50:60, 70:80] += 30
    m = label_mask('fall', activity(c))
    self.assertFalse(m[:45].any()); self.assertTrue(m[45:].all())

def test_label_mask_still(self):
    m = label_mask('still', activity(np.full((100, 2, 120, 160), 2, np.uint16)))
    self.assertFalse(m[:17].any()); self.assertTrue(m[17:].all())

def test_build_skips_incomplete(self):   # Review Focus 3
    # 합성 .bin 두 개(6 s, 1 ms마다 이벤트), 하나는 .json complete false
    build([(a, 'walk', 'ours'), (b, 'walk', 'ours')], out)
    self.assertEqual(len(open(out + '/manifest.jsonl').readlines()), 1)
    z = np.load(only(out + '/ours', '.npz'))
    self.assertEqual((z['counts'].dtype, z['counts'].shape[1:]), (np.uint8, (2, 120, 160)))
```

- [ ] **Step 2: 실패를 확인한다** (`~/.venvs/dvsact/bin/python -m unittest discover -s tools/host/act -p 'test_dataset.py' -v`, ImportError)
- [ ] **Step 3: 구현한다**
- [ ] **Step 4: 통과를 확인한다** (`OK`)
- [ ] **Step 5: 실제 데이터를 만든다.** CLI를 돌린다. Expected: `manifest.jsonl`의 클래스별 개수를 찍는다. `lie_down`, `still`, `none`이 우리 녹화에서만 나오는지 확인한다.
- [ ] **Step 6: 커밋한다** (`git add tools/host/act/dataset.py tools/host/act/test_dataset.py && git commit`)

---

### Task 8: 학습, 내보내기, 오프라인 평가 (Mac)

**Files:**
- Create: `tools/host/act/train.py`, `tools/host/act/evaluate.py`, `tools/host/act/test_train.py`

**Interfaces:**
- Consumes: Task 4 `model.Step`, `export.py`. Task 7 npz와 manifest. Task 3 `Judge`, `to_input`.
- Produces:
  - `train.augment(c: np.ndarray uint8 [N,2,GH,GW], rng, public: bool) -> np.ndarray uint16`
    - public일 때만: 극성마다 Poisson(r × 2.05)을 더한다(r ~ U(0, 2), 우리 센서 잡음 약 4.1개/셀/칸의 절반씩). 이어서 이항 솎기를 한다(keep ~ U(0.5, 1)).
    - 모두: 좌우 뒤집기(p = 0.5), 확대 U(0.8, 1.2)와 이동 ±10%(nearest, 클립 전체에 같은 값)
  - `train.py --stage public|ours --data DIR --init imagenet|CKPT --out CKPT [--epochs N] [--holdout 2] [--seed 0]`
    - 배치는 클립 8개다. 길이를 맞춰 패딩하고, 패딩 칸은 mask를 false로 둔다. 시퀀스마다 h를 0에서 시작한다.
    - 손실은 mask된 칸의 CE이고, 클래스 가중치는 mask된 칸 수의 역수(합 1)다.
    - AdamW를 쓰고 lr은 public 1e-3, ours 3e-4다. 장치는 MPS다.
    - `ours`: 클래스마다 시각이 가장 늦은 `--holdout`개 클립을 빼고 `<CKPT>.holdout.json`에 쓴다.
    - 에폭마다 `epoch E loss L`을 찍는다.
  - `evaluate.py --ckpt CKPT --data DIR --holdout FILE`
    - 클립마다 mask된 칸의 softmax 평균의 argmax로 9×9 혼동 행렬을 찍는다.
    - 같은 확률을 `Judge`에 넣어 클립마다 `fall` 기록 수를 찍는다.

- [ ] **Step 1: 실패하는 시험을 쓴다**

```python
def test_augment(self):
    c = np.random.default_rng(0).integers(0, 5, (10, 2, 120, 160)).astype(np.uint8)
    a = augment(c, np.random.default_rng(1), public=True)
    self.assertEqual(a.shape, c.shape); self.assertTrue((a >= 0).all())
    self.assertTrue(np.array_equal(augment(c, np.random.default_rng(1), True), a))   # 같은 seed면 같은 결과

def test_overfit_tiny(self):   # 두 클래스, 다른 자리에 움직임이 있는 합성 클립 4개, CPU, 30걸음
    first, last = run_tiny(steps=30)
    self.assertLess(last, 0.5 * first)
```

- [ ] **Step 2: 실패를 확인한다** (`~/.venvs/dvsact/bin/python -m unittest discover -s tools/host/act -p 'test_train.py' -v`, ImportError)
- [ ] **Step 3: 구현한다**
- [ ] **Step 4: 통과를 확인한다** (`OK`)
- [ ] **Step 5: 공개 데이터로 학습한다**
  Run: `train.py --stage public --init imagenet --out ~/e850-act-runs/public.pt`
  Expected: loss가 줄어든다. 에폭 수와 시간을 적는다.
- [ ] **Step 6: 우리 녹화로 미세 조정한다**
  Run: `train.py --stage ours --init ~/e850-act-runs/public.pt --out ~/e850-act-runs/ours.pt --holdout 2`
- [ ] **Step 7: 내보낸다**
  Run: `export.py ~/e850-act-runs/ours.pt ~/e850-act-runs/model.onnx`
  Expected: `parity ok max_abs≤1e-4`
- [ ] **Step 8: 오프라인으로 평가한다**
  Run: `evaluate.py --ckpt ~/e850-act-runs/ours.pt --data ~/e850-act-runs/data --holdout ~/e850-act-runs/ours.pt.holdout.json`
  Expected: 혼동 행렬. 숫자를 그대로 11.41에 적는다. 표본이 작아 참고용이라고 함께 적는다.
- [ ] **Step 9: 커밋한다** (`git add tools/host/act/train.py tools/host/act/evaluate.py tools/host/act/test_train.py docs/ && git commit`)

---

### Task 9: 보드 배포와 live 받아들임 시험 (사용자가 함께함)

**Files:**
- Create: `tools/host/act/accept.py`, `tools/host/act/test_accept.py`

**Interfaces:**
- Consumes: Task 5 녹화 `.json`(label, start, s). Task 6 `replay.sh`. Task 7 `activity`. 보드 `act.jsonl`.
- Produces: `accept.py --rec DIR --act act.jsonl`
  - 시험 하나는 녹화 하나다. 녹화를 켠 채로 동작한다.
  - 시험 구간 `[start, start + s + 2 s]` 안의 `act.jsonl` 줄을 모은다.
  - 넘어짐 지연 = 첫 `fall` 줄의 time − (start + `activity` 정점의 칸 시각). live 재생 지연 때문에 약 ±0.3 s 오차가 있다.
  - 표를 찍는다: 라벨, `fall` 있음, 지연, `appear` 있음
  - 요약을 찍는다:
    - `fall` 라벨 중 지연 ≤ 1 s인 수 / 시험 수
    - `sit_down`·`lie_down`·`pick_up` 라벨 중 `fall`이 찍힌 수 / 시험 수
    - `walk` 라벨(화면 밖에서 들어오기) 중 `appear`가 찍힌 수 / 시험 수
  - 기준을 넘으면 종료 코드 0, 아니면 1이다.

- [ ] **Step 1: 실패하는 시험을 쓴다** (`test_accept.py`: 합성 녹화 `.json` 3개와 `act.jsonl` 줄들)
  - fall 시험에서 정점 +0.6 s에 `fall` 줄 → 지연 0.6, 통과로 센다
  - sit_down 시험에 `fall` 줄 → 오판 1
  - walk 시험에 `appear` 줄 → 나타남 1
  - 기준 계산이 각각 8/10, 3/30, 8/10을 쓰는지 본다(시험 수를 바꿔 경계값 8과 7을 확인한다)
- [ ] **Step 2: 실패를 확인한다** (ImportError)
- [ ] **Step 3: 구현한다**
- [ ] **Step 4: 통과를 확인한다** (`OK`)
- [ ] **Step 5: 배포한다**
  - `scp ~/e850-act-runs/model.onnx $B:/var/lib/dvsact/model.onnx`
  - `ssh $B 'rm -f /var/lib/dvsact/act.jsonl; touch /var/lib/dvsact/enable'`
  - `POST /sensor {}`로 다시 시작한다.
  Expected: `[dvsact] steps`, lost 0, fps 104
- [ ] **Step 6: 받아들임 시험을 한다**
  사용자가 페이지에서 녹화를 켜고 동작한다.
  - `fall` 10번(매트리스)
  - `sit_down`, `lie_down`, `pick_up` 각 10번
  - `walk` 10번(화면 밖에서 시작해 들어오기)
  끝나면 녹화와 `act.jsonl`을 Mac으로 옮기고, `replay.sh`로 푼 뒤 `accept.py`를 돌린다.
- [ ] **Step 7: 결과에 따라 정한다**
  - 통과: `enable`을 그대로 둔다. 부팅 때부터 분류가 켜진다.
  - 실패: `enable`을 지운다. 녹화를 동작당 20번으로 늘려 Task 6~8을 다시 한다. 시험 녹화도 학습 데이터에 넣는다.
  - 어느 쪽이든 숫자를 11.42와 BRINGUP_STATUS에 적는다.
- [ ] **Step 8: 커밋한다** (`git add tools/host/act/accept.py tools/host/act/test_accept.py docs/ && git commit`). push할지 사용자에게 묻는다.
