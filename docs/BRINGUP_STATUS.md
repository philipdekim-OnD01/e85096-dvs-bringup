# E850-96 bring-up 현재 상태 (2026-11-26 KST)

이 문서는 새 세션에서 바로 이어서 일할 수 있도록 지금 상태를 정리한 것이다.

**2026-11-26(11.116):** 명세 "E850 vs Jetson Multi-Sensor AI Runtime Benchmark"를 `msbench/`로 진행 중이다. 측정한 것만 적는다.
- Phase 2: 같은 COCO·Imagenette 입력으로 4모델 × 8 CPU 설정 × 스레드 1/4/최대를 두 장비에서 쟀다. HTML 보고서는 `msbench/results/report.html`이다.
- 정확도는 두 장비에서 소수 셋째 자리까지 같다. TFLite INT8 YOLO는 mAP 0.12로 망가진다. 원인은 상자 복원까지 정수화한 것이고, 시험으로 확인했다.
- E850 4스레드(CPU4~7)는 스케줄러가 OpenMP 스레드를 적은 코어에 몰아 놓아(표본 70%) 덜 빨라진다. `OMP_PROC_BIND=close OMP_PLACES=cores`로 1.4~2.65배 빨라진다. 8스레드에서는 묶지 않는다.
- Jetson 첫 판의 1·4스레드는 DVS 디코더(CPU1)와 코어를 같이 써서 0.84코어만 받았다. 그래서 1 → CPU5, 4 → CPU2~5로 바꿔 다시 재는 중이다.
- Phase 7: Jetson에 sudo 없이 TensorRT 11.4.1, PyTorch 2.14.1+cu130, onnxruntime-gpu 1.24.0을 `~/jp`에 넣었다.
  - TensorRT FP16이 가장 빠르다: YOLO11n-640 6.4 ms, 320 3.4 ms, 분류 1.3 ms.
  - TensorRT INT8은 FP16보다 느리고(strongly typed, Q/DQ 배치), ResNet18 INT8은 빌드에 실패한다.
- Phase 3: 보드6 USB 카메라 YOLO11n-320 NCNN FP16은 AI를 켠 평소 상태에서 2.35 fps, AI를 끄면 6.46 fps다(e2e 208 ms). Jetson 실시간은 측정 대기 중이다.
- Phase 4·5(DVS 검출, 융합)는 라벨 데이터가 없어 N/A다. E850 전력은 센서가 없어 N/A다.

**2026-11-26(11.115):** Jetson과 보드6을 같은 방법으로 비교했다. 같은 바이너리로 아키텍처 차이도 쟀다.
- 실시간(30초, `tools/board/cmpload.sh`):
  - 두 보드 모두 DVS 창 30 fps, 카메라 창 15 fps, 손실 0이다.
  - 시스템 CPU(코어 하나 = 100%): Jetson은 DVS만 73%, 카메라를 더하면 95~110%다. 보드6은 AI를 켠 채 197%와 245%이고, AI를 빼면 약 76%와 120%다(계산).
  - mpv는 25% 대 11%, dvsgl은 22.5% 대 6%다. DVS 지연은 26~30 대 22 ms, 카메라 프레임 나이 중앙값은 72 대 66 ms다.
- 보드6 AI를 끄고 다시 쟀다(사용자가 비밀번호를 줌). DVS만 89%, 카메라를 더하면 127%다(Jetson 73%, 95~110%). AI를 꺼도 실시간 해독은 빨라지지 않았다(kB당 13.4 대 13.0 µs). "AI 경합" 가설은 버렸다.
- 8코어 전체 처리량(모델 추론): Jetson이 칩 전체로 3.0~3.4배다(코어당 3.4~4.2배). Jetson 전력은 6.6 W(평소)에서 8.2 W(전 코어)다. E850 전력은 센서가 없어 N/A다.
- Arm 버전: 두 코어 모두 v8.2다. A55에는 DotProd가 없다(SIGILL). A78AE에는 DotProd, 포인터 인증(v8.3), v8.4 일부가 있다. FP16은 두 코어 모두 FP32의 2배 flop이다. 그러나 이 코드와 모델에서는 컴파일 대상 차이가 ±4%, FP16·int8이 0.8~1.7배라 작다. 차이는 마이크로아키텍처에서 온다.
- 두 카메라 모두 실제로는 15 fps다. USB 대역폭(24 MB/s)은 충분하고, 노출 제어가 없다. 카메라 쪽 원인으로 본다(가설).
- 같은 일을 코어 하나로(Jetson 1.34 GHz 대 E850 2.0 GHz): 해독 1.5배, 필터 3.7배, AI 3.4~4.2배(분류 7.8 대 26.8 ms, 얼굴 26.9 대 112 ms), 카메라 YUYV 변환 3배 Jetson이 빠르다.
- 까닭(`tools/board/archbench.c`로 잼):
  - 정수 발행 폭: 3.4 대 1.9/사이클.
  - FP32 FMA: 16 대 8 flop/사이클.
  - 독립 메모리 읽기 겹침: 192 KB에서 4.8 대 1.8배, DRAM에서 7.9 대 2.5배.
  - L1: 64 KB 대 32 KB. L2: 256 KB. E850 둘째 단계는 약 512 KB.
  - 코어 하나 대역폭: 11.1 대 5.0 GB/s. 전체: 41 대 15 GB/s.
  - E850이 나은 것도 있다. DRAM 지연(138 대 236 ns)과 분기 예측 실패(5.3 대 10.4 ns)다.
- 옆 코어가 메모리를 쓰면 E850 해독은 4~19% 느려지고, Jetson은 1~6% 느려진다.

**2026-11-26(11.113~11.114):** USB 카메라, "연결됐을 때만".
- Jetson: 카메라 한 대로 바꿨고, `tools/jetson/usbcam`과 "USB 카메라" 아이콘을 넣었다.
- 보드6:
  - 같은 카메라(1903:8328)를 바탕화면 "USB 카메라" 아이콘(`e850-open cam`, mpv `--vo=gpu`)으로 띄운다. 사용자가 확인했다.
  - 현미경 뷰어 `scope.py`가 아무 USB 카메라나 현미경으로 잡던 것을 고쳤다. 이제 `04b4:00c3`만 받는다.
  - `e850-open`은 장치가 없으면 안내 창(`e850-say`)을 띄우고 끝난다.
  - DVS는 센서가 없으면 데모 녹화를 틀지 않고 끝난다(`--need-sensor`, `RestartPreventExitStatus=3`).
- 보드4에는 아직 넣지 않았다(현미경·DVS 고친 판). 센서 없는 DVS 경로도 시험하지 않았다.
- 두 보드의 USB 카메라 뷰어를 똑같이 `mpv --vo=gpu --profile=low-latency --untimed --no-terminal`으로 맞췄다(Jetson `usbcam`, 보드6 `e850-open cam`). 다음 세션의 비교에서는 DVS와 이 카메라를 두 보드에 함께 건다.

**2026-11-26(11.112):** 보드6 DVS의 Python 캡처 루프, 행동 분류, 얼굴 검출을 C로 옮겼다.
- 캡처 루프는 `dvsview.c`의 `dvs_capture()`, 분류와 얼굴은 새 파일 `dvsai.c`다. onnxruntime C API를 휠의 so에서 dlopen하고, `-DDVS_AI`로 빌드한다(bundle.sh가 NuGet에서 헤더를 받는다).
- 보드6에서 30초 측정: 합계 152.7 → 143.1%(코어의 약 10% 감소). 주 스레드 29.9 → 24.9%, `dvs-act` 54 → 52%, `dvs-bin` 23 → 21%, `dvs-face` 41 → 40%. 손실은 0이다.
- 남은 시간은 onnxruntime의 FP32 합성곱이다. FP16은 분류에서 1.26배였지만 출력이 달라지고, 얼굴 모델은 실행되지 않아 쓰지 않는다.
- 비교 시험 `test_dvsai.py`가 Python의 얼굴 `decode` 버그를 찾았다: 다음 stride의 격자 너비가 틀렸다. 고쳤다.
- 11.111의 "보드6 주 스레드 99.9%"는 잘못 읽은 값이었다. 실제로는 29.9%다(`tools/board/thrcpu.sh`).
- Jetson에도 새 판을 넣었다(`dvs_buf_note`).
- **다음 세션(사용자 지시):** Jetson과 E850 비교를 이어서 한다. 같은 부하로 다시 잰다.

**2026-11-26(11.111):** Jetson과 E850의 DVS 처리 성능을 같은 녹화 두 개로 비교했다(`tools/linux510/dvsbench.py`).
- 해독(ns/이벤트): E850 A55 2.0 GHz 14.2~15.2, Jetson A78AE 1.34 GHz 9.4~10.2(1.5배).
- 잡음 필터: E850 59~61, Jetson 16~17(3.7배).
- E850은 필터를 켜면 해독도 +58% 느려진다(Jetson +5%).
- 실시간 누적 손실은 Jetson 0, 보드6은 적게 있다.

**2026-11-26(11.110):** 보드4 현미경 뷰어를 밖에서 볼 수 있다.
- 경로: Cloudflare 임시 터널(`e850-scope-tunnel.service`, enabled) → nginx `127.0.0.1:8091` Basic 인증(사용자 `scope`, 비밀번호는 보드에만) → :8090.
- 주소는 터널이 시작할 때마다 바뀐다: `sudo journalctl -u e850-scope-tunnel | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' | tail -1`.
- LAN :8090은 그대로다(인증 없음).

**2026-11-26 00시(11.108):** Jetson Orin Nano(192.168.0.195, `ssh philip@192.168.0.195`)의 DVS를 E850의 C 디코더(`libdvsview.so`)·설정 패널(`dvspanel`, 페이지 :8080)·GPU 뷰(`dvsgl`, NVIDIA GLES)로 바꿨다.
- 캡처 앞단은 `tools/jetson/dvs-jetson.py`이고, NRV 드라이버의 `nrv_register_writes`와 VI mode0 8192×128 AER을 쓴다. 배포는 `sh tools/jetson/deploy.sh philip@192.168.0.195 [--autostart]`.
- 측정: 2000 fps scan, 프레임당 이벤트 약 80만에서 누락 0, 한 코어의 약 25%.
- GNOME 자동 시작을 NRV 창 대신 `dvs-jetson`으로 바꿨다.
- htop은 `~/.local/bin/htop`(sudo 없이). 패널의 MIPI·DRAM 그래프를 Jetson VI에서 채운다: MIPI 프레임 72/s, 75.6 MB/s, 프레임 채움 38%, 빈 버퍼 15/16, 링크 오류 0. CRC는 값이 없다(11.109).
- 남은 일:
  - (할 일 아님, 2026-11-26 정리) `nrv-dvs-streamer`는 협력 측의 예전 수신기다(부팅 때 RAW2 `dvs_streamer` + viewer.py, 사용자 philip). 그대로 둔다: `dvs-jetson`이 시작할 때 멈추고, NRV로 되돌릴 때 쓰는 경로다. NRV 창은 `~/.config/autostart-off/nrv-dvs-native.desktop`을 autostart로 되옮기면 돌아온다.
  - 로그인 자동 시작 확인(재부팅 필요).
  - 오래 돌리는 시험.

**2026-11-25 19시 세션 끝 상태(새 세션 시작점, 11.103):**
- 행동 분류의 이벤트 묶기를 C로 옮겼다(`dvs_act_bin`). 스레드는 `dvs-bin`과 `dvs-act` 둘이고 CPU4~6에 있다. 예전에는 이벤트의 약 70%를 버렸는데 이제 0이다. CPU4가 99%였던 까닭은 예전 Python 묶기였다.
- 캡처를 재시작할 때 가끔 나던 exit 134(onnxruntime 종료 충돌)를 `leave()`로 고쳤다. 고친 뒤 10번 모두 exit 0.
- HDMI 신호는 LT8912B 때문에 1080p60이 최대다. 대신 데스크톱 크기를 X 배율로 고르는 앱 `e850-res`(메뉴 "Screen resolution (화면 해상도)")를 보드6·4에 넣었다. 1280x720~2560x1440. 보드4에서 명령줄로 시험했고, HDMI 화면을 눈으로 보지는 않았다.
- 보드4에도 위 DVS 파일을 넣었다. Mac 이미지 overlay도 고쳤다(simg는 다시 만들지 않음).
- DVS 설정 패널(`dvspanel`)을 940x700으로 줄였다(11.104). 왼쪽은 스크롤이 없다. 오른쪽은 "그래프" 탭(12개 두 열, 기간 기본 1분)과 "통계·녹화·레지스터" 탭이다. DVS 창은 왼쪽 끝에 놓인다(`dvsgl` USPosition). 보드6·4에 넣었다.

- SD 카드: 커널은 처음부터 인식했다(dw_mmc, exFAT 내장). desktop 기능에 udisks2, gvfs, exfatprogs, dosfstools와 polkit·udev 규칙을 더해 파일 관리자에 나온다(11.105, 보드6).
- 센서 2000 fps·flush 988 µs와 지금 장면에서는 분류기 묶기(`dvs-bin`)가 한 코어를 다 쓰며 이벤트를 27~49% 잃는다. GPU 창은 24 fps, 프레임당 이벤트 2,097,152개(한도)다(11.105).

바로 할 일(11.103~11.105에서 더한 것):
- 사용자가 보드6 화면에서 `e850-res`로 2560x1440을 골라 글자 선명도와 DVS 창·Chrome을 확인한다.
- 보드4가 11:33(UTC)에 오류 기록 없이 멈췄다가 12:27에 다시 부팅했다(원인 모름). 현미경은 정상이다(11.105). SD 지원을 넣는다.
- 보드4·6 바탕화면에 DVS·현미경 아이콘(pcmanfm, `e850-open`, DVS는 polkit으로 `e850-dvs-desk.service`만), `ifconfig`(net-tools, 세션 PATH에 sbin)(11.107). 보드6 scan 누락은 사용자가 14:14에 MIPI flush를 1 µs로 바꾼 탓이다(초당 버퍼 330개, scan 7% 누락. 50 µs에서는 0, 11.107).
- 현미경 뷰어 실시간 화면은 회색 바이트 + WebGL2 LUT다(11.106). CPU 257% → 237%. 남은 큰 비용은 Chrome 합성·X(약 0.9코어), USB 수신(0.3), 서버 평균(0.2)이다. 외부 주소는 11.110에서 비밀번호 페이지 뒤에 열었다.
- 이벤트가 많을 때: `dvsgl`을 이벤트마다 한 번만 올리는 누적 방식으로 바꾸고, 분류기 묶기를 GPU로 옮기는 것을 검토한다(11.105).

**2026-11-24 16시 세션 끝 상태:** 보드6(.208)이 다시 살아났다(11.102). 오늘 한 일:
- 보드6 Chrome이 GPU 데스크톱에서 멈추던 것을 고쳤다(drm-shim `mmap` 수정). Chrome은 `--use-gl=angle --use-angle=gl-egl`로 GPU 합성·WebGL을 쓴다.
- DVS 화면을 GPU(`dvsgl`, GLES 3.1 compute)로 그린다. 빠른 움직임(프레임당 약 100만 이벤트)에서 35 fps, 빠지는 프레임 0, 지연 31~35 ms(전 77~118 ms).
- 보드6 전체를 이미지로 Mac `~/e850-logs/clone-20261124-b6/`에 저장했다(BOARD-CLONE.md 12장, 비밀 값 포함, Mac에만 둔다).
- 그 이미지를 보드4(.244)에 구웠다. manifest 42,074개 파일이 모두 같았다.

바로 할 일:
1. **MFC 두 번째 경합을 고친다.** 굽고 난 보드4 첫 부팅이 `mfc_open` → `mfc_alloc_firmware` → `iommu_map_sg` NULL로 패닉했다(trace는 `~/e850-logs/board4-last_kmsg-20261124-b.txt`). 부팅 중 가끔 멈추는 원인이다.
2. 보드4에서 BT 기기를 다시 페어링하고, DVS 센서를 단다(사용자).
3. DVS GPU 화면을 눈으로 확인한다(tearing, 전체 화면 f). 센서 없는 demo 재생은 11.114부터 하지 않는다.
4. 11.100의 남은 일: Wi-Fi 드라이버 근본 수정, 보드3·5 감시, 1 m 얼굴.

git push는 사용자 확인 뒤에 한다. 경과와 측정 결과는 [E85096-LINUX510-BOOT.md](E85096-LINUX510-BOOT.md) 11장에, 대화 요약은 [SESSION-LOG-20261101-02.md](SESSION-LOG-20261101-02.md)에 있다.

## 1. 전체 상태

| 항목 | 상태 | 문서 |
|---|---|---|
| 8코어 SMP | 동작한다. DT의 CPU `reg`를 `cluster<<8 \| core`로 고쳤다 | 11.6~11.8 |
| 1시간 8코어 부하 | 통과. 오류 0, 최고 87℃ | 11.11 |
| CPU hotplug | `e850-cpuhp` 모듈로 해결. 30회 + 무작위 300회 시험에서 실패 0 | 11.13 |
| C2 cpuidle | **보류.** 코어가 C2에서 깨어나지 않는다. 펌웨어 문제로 본다. `cpuidle.off=1` 유지 | 11.12 |
| 코어 배치 | 장치 IRQ는 모두 CPU0. 단 Wi-Fi IRQ는 2026-11-19부터 CPU7(11.69: CPU0에 있으면 송신 중 USB 호스트가 늦어져 현미경 프레임이 절반 빠졌다). 디코더 CPU1, `dvs-render` CPU2, `dvs-filter` CPU3, `dvs-act` CPU4~6(11.45; 11.103부터 C 묶기 `dvs-bin`과 추론 `dvs-act` 두 스레드). 2026-11-16 08:27 측정(11.63): `dvs-act` 54%, 그리기 21~36%, 디코더·필터 4~6%. 11.30 당시 live: 그리기 약 0.75코어, 캡처·디코드 0.13~0.19. **CPU7은 웹 페이지 전용**(`dvs-web` 스레드, uclamp.min 1024로 돌 때만 최고 클럭). systemd 전체는 CPU0~6(`e850-cpu7.conf`). 측정: `tools/board/cpumap-cap.sh` + `tools/host/cpumap.py` | 11.30, 11.45, 11.63 |
| 카메라 OV5647 | 55~63 fps(VTS 수정, 캡처 중 printk 끔). **2026-11-06 사용자가 떼어 내고 DVS 센서로 교체했다** | 11.10 |
| DVS 센서 S5KRC1S | I2C 확인: `i2c-7`(HSI2C2) 주소 0x10, 0x303B 쓰기/읽기 OK. 전원은 `modules/dvs-power`(LDO32 + RSTN GPC0[2]). 매뉴얼에 칩 ID 레지스터 없음, 0x36도 응답(정체 모름). Pablo CIS 드라이버 `modules/s5krc1s-pablo`(OV5647과 같은 ISP 우회) 빌드, boot-dvs로 부팅. 드라이버로 전원·I2C·stream on까지 동작. Delta_10_2000FPS setfile로 **CSIS가 MIPI 프레임을 받는다**(F38/3 s). 하지만 DMA done이 없어 VC0 프레임은 사용자 공간에 0개, stop 시 VRESOL_MISMATCH·OVERFLOW. overflow 패닉은 껐다. 스트림 도중 I2C 쓰기나 insmod를 한 시험에서 soft lockup이 두 번 났다(전원 재투입 필요). 1000 FPS 시도(`to_scnt1`=1999)는 아직 효과를 측정하지 못했다. IRQ 폭주 원인은 CSIS v1.2가 VRESOL 오류 상태를 지우지 않은 것이었다(패치로 수정). devfreq를 최대로 하면 **DVS 이벤트가 사용자 공간에 온다**(`tools/host/dvsdecode.py`로 AER 패킷 확인). 남은 문제: CRC 오류(1499 Mbps 수신 설정에서 줄지만 남음), 매뉴얼에 없는 패킷 type 000000/000101(손상인지 미확정). 캡처는 `dvscap.sh`. 타임스탬프로 잰 실제 프레임 간격은 494 µs(약 2024 fps)라서 1000 FPS 설정은 효과가 없다. 오류 10프레임이면 CSIS를 끄는 vendor 동작을 `fimc_is.e850_csi_err_stop=0`으로 풀었고, **`ov5647-cap.py --live --raw8`로 HDMI에 이벤트 영상(ON 초록/OFF 빨강)을 계속 띄운다**(`is_cis_s5krc1s.flush_us=250`이면 초당 약 11번 갱신, 11.25. to_scnt1은 프레임 주기가 아니라 MIPI flush 타임아웃이라 999로 되돌림) 32줄 모드(mode1)는 System MMU fault 패닉으로 보류(11.26). 디코딩·렌더를 C(`libdvsview.so`)로 옮겨 HDMI **30 fps**, 디코딩 5.2 ms/MB(numpy의 약 45배). 하지만 버퍼에 센서 시간의 약 14%만 들어온다(CRC 시 DMA abort로 추정, 11.27). **11.28: 화면 깜빡임의 세 원인을 고쳤다.** ① CRC 비트가 `error_id`에 쌓여 프레임마다 오류 flush(fimc-is 패치) ② 링크가 워드의 종류 비트 31~26을 지워 SUB 타임스탬프가 256 ms씩 사라짐(디코더가 복구) ③ 커널 볼륨에 남아 있던 EMERG 디버그 printk가 5초마다 CPU0 인터럽트 안에서 UART로 나가 프레임 손실(지움). 지금 live는 초당 68버퍼, 2초 커버리지 93~100%, 지연 21~24 ms, 어두운 프레임 0장(3분). 화면 조정 앱 `http://192.168.0.236:8080/`(인증 없음). CRC 오류는 초당 약 8700개로 그대로다(링크 문제로 추정). **11.29: 0x30A6을 0x8D에서 0x75로 바꾸자 CRC가 없어졌다.** (**11.46 정정:** 0x30A5~A7은 19비트 PLL 단어이고 0x30A6은 M만이 아니라 P에도 들어간다. 0x75는 공식값 5376 Mbps로 VCO 범위 밖이다. 실제 속도를 모르는 **실험값**이다. 0x75 녹화 42개에서 CRC 없이 scan 1.62%가 빠졌다. 2026-11-12 A/B 10분씩: 0x75, 00/8B/B0, 00/8B/F8 모두 CRC 0. **버퍼 사이 scan 누락(1~1.6%)은 PLL이 아니라 디코더가 128번째 줄을 버린 탓이었다**(센서 128줄, DT 127줄). `W × (H + 1)`로 고친 뒤 00/8B/B0 10분 모든 판정 통과. 수신 설정을 맞춰도 같다. 시험 계획과 결과: [DVS-PLL-TESTS.md](DVS-PLL-TESTS.md))  vendor 0x8D는 줄마다 CRC였고, 0x72~0x78에서는 CRC 0, 깨진 워드 0이다. setfile을 0x75로 바꾸고 재부팅한 뒤 3분 동안 CRC 0개였다(0x70·0x7C·0x80은 데이터 없음, 0x7A는 CRC, 좁은 구간이라 장시간 안정성은 미측정). scan 속도는 `is_cis_s5krc1s.margin`(DTAG_FRM_MARGIN)으로 정한다. 주기는 430 + 32.4 × margin µs이고, 기본 17은 985 µs(1015 fps)다. 디코더가 scan 주기를 직접 잰다. **웹 서비스 `dvslive.service`(부팅 때 시작): `http://192.168.0.236:8080/`**, 미리보기 기본 10 fps(PNG는 C로 만들어 보드 안에서 34 ms), 센서 scan 속도·MIPI flush를 바꾸면 스트림을 다시 시작, 인증 없음. **11.30: 웹을 CPU7 전용으로 하고 keep-alive·TCP_NODELAY를 켜서 Wi-Fi 응답이 104~113 → 28~40 ms**(링크 상태에 따라), 미리보기 20/30 fps 추가 | 11.17~11.30 |
| DVS 사람 행동 분류 | **구현 중(3장 1번). 과제 1~5 끝, 6~8은 도구까지(11.40):** 보드 분류 스레드(무작위 모델로 16 Hz, live·웹 영향 없음, 11.38), 페이지에서 동작 녹화(11.39), 녹화 확인·데이터·학습·평가 도구(11.40). 공개 데이터 PAF 411개, DailyDVS-200 1,021개를 학습 입력으로 만들었다(11.41). 공개 데이터로 10에폭 학습(69분, loss 1.73 → 0.72). DailyDVS 미사용 323개 참고 평가 67%, 넘어짐 82%. 다만 앉기의 21%가 넘어짐으로 나왔다(정면 데이터, 미세조정 전 기준선, 11.43). 보드 live 첫 시험에서 공개 모델은 앉기를 한 번도 맞히지 못했고, 빈 화면 2분에 오경보 14번을 냈다. 보드 빈 화면 21개를 배경으로 섞어 다시 학습한 `mix` 모델은 빈 화면 5분에 0번이다(11.44). 옆에서 녹화한 앉기 13·일어서기 10(동작마다 자름)을 더해 학습한 `sitstand` 모델은 평가용 앉기·일어서기 4/4를 맞혔다. 다만 빈 화면 0/2다. 보드는 앉음·서 있음만 판정하도록 바꿨고, 상태는 웹 페이지 맨 위에 나온다(`http://<보드>/`, 11.45). **남은 것: 넘어짐 녹화, 빈 화면 오경보 확인.** 나타남·움직임·넘어짐을 DVS 이벤트로 분류해 JSONL에 기록한다. 방법은 60 ms 프레임 → MobileNetV3-small(ImageNet) → GRU, 16 Hz. A55 한 코어에서 무작위 가중치 모델 27.2 ms(실측) | 11.36~11.45 |
| DVS 스테레오 3D | **1단계(Mac 계산) 구현, 보드 설치 전(11.56).** 보드 `/evframe`(time surface 480×360), Mac `tools/host/dvs3d.py`(시계·짝·SGBM·3D·페이지 http://localhost:8090/·bench·calib). Mac M6 연산량 쌍당 15.9 ms(SGBM 93%). A55 수치 없음(보드 OpenCV 설치 결정 대기). 보정·실제 3D는 사용자가 있을 때. 최종 목표 SLAM | 11.56 |
| 상자 크기 측정 | **사람 행동 분류와 별개 과제(3장 2번).** 목표는 컨베이어 위 상자를 1.5 m에서 보고 가로·세로·높이를 재는 것이다. 1차 시험에서 손에 든 상자는 재지 못했다. hull이 손과 팔까지 묶었다(11.35). **2026-11-15 방식 결정(사용자 제안): DVS 두 대, 위 보드가 가로·세로·위치, 옆 보드가 높이, Wi-Fi로 기록을 합친다.** 별도 비공개 저장소 [e850-dvs-boxdim](https://github.com/philipdekim-OnD01/e850-dvs-boxdim)에 제안서와 기준 코드를 올렸다. PC 테스트 14개는 보드1에서도 통과했다. 실제 센서로는 아직 재지 않았다. 다음은 M1: `dvs_boxes()` 기록에 상자 중심 `cx_px`, `cy_px` 추가(11.58) | 11.35, 11.58 |
| 현미경 모드(SOL CX3 UVC) | **뷰어 동작(보드4, 2026-11-19부터 부팅 서비스).** USB 호스트가 안 되던 원인은 허브 전원 LDO24(ERD DT가 SD `vmmc`로 씀)였다. `modules/e850-hubpwr`로 켰다. micro-USB를 빼야 허브가 붙는다. 웹 `http://192.168.0.244:8090/`(전자현미경 스타일, 확대·측정·평탄화·평균·레지스터·촬영). 실물 뷰어 수신 6.9 fps, 누적 96.3%(dvslive와 같이 돌 때. 센서 7.2~7.4, 온전 98.4%). 확대 버튼·기준점·더블탭·핀치·1:1(11.65). **11.67: 50M 키트(Hi-5031Q, 2016×1536, 4.37 fps)용으로 뷰어가 크기와 센서를 카메라에서 읽는다. 보드4 가짜 모드로만 확인했다. 실물 키트를 E850에 꽂아 본 적은 없다.** **11.68: LED 켜기·끄기 버튼을 넣었다. 조명 LED는 TLC59108 OUT3였다. 이전 펌웨어는 OUT1에 써서 LED가 늘 꺼져 있었다.** **11.69: 보드4는 부팅 때 `e850-scope.service`로 현미경을 띄운다(재부팅 확인 전). Wi-Fi IRQ를 CPU7로 옮겨 프레임 수신이 50% → 96~98%가 됐다.** **11.70: 자동 촬영·물체 개수/크기 분석(OpenCV 4.11 wheel을 `/opt/e850-scope/py`에)·SQLite 기록·추세 그래프를 서비스에 설치했다. 실험 #1(5분 간격, 최소 5 µm라 개수 0)은 11-19 18:30에 `/set tl_on=0`으로 끝났다(124장, 11.72).** **11.71: 화면을 상용 제품식으로 재구성했다(실시간·실험 마법사·결과 대시보드·PDF 보고서). 11.72: 인쇄 여백과 멈춤 이유 표시를 고쳐 보드4 `/var/tmp/e850-scope`에 올려 두었다. 설치(`sudo sh /var/tmp/e850-scope/install.sh`)는 사용자가 한다. 제품 소개·사용 설명서 `docs/SCOPE-MANUAL-20261120.html`(28장, 11.74에서 센서 모듈 장 추가, 메일로 보냄). **11.75: 보드4 뷰어를 Cloudflare 임시 터널로 외부에 열었다(보호 없음, 사용자 결정). 재부팅하면 꺼지고 주소가 바뀐다.**** **11.73: 몸체에 8M·16M·50M 모듈을 바꿔 끼운다(사용자). 16M(Hi-1634) 펌웨어 `SENSOR=hi1634`(2304×1752, 3.37 fps, MIPI 720 Mbps)가 Mac에서 RAM으로 동작한다(60프레임 온전). EEPROM은 아직 50M 펌웨어다. 키트를 뽑았다 꽂으면 뷰어가 멈추던 버그를 고쳐 `/var/tmp/e850-scope`에 올렸다.** **11.74: 칩 ID로 센서를 고르는 통합 펌웨어(129,380 B)를 키트2 EEPROM에 썼다. Mac RAM 시험에서 16M·50M이 온전했다. EEPROM 부팅으로 보드4에서 16M 2304×1752 3.36 fps(193프레임 온전, MIPI 오류 0)를 받았다. 8M은 모듈이 없어 시험하지 못했다.** **11.94(보드6): 고해상도 사진 4032×3072(키트 형식 2 GREY, 센서 프레임 3장에 나눠 보냄, RAM 적재). 뷰어에 해상도·색 선택. 사진 받기 평균 1.8초. 같은 날 저장 먼저·분석은 뒤에서로 바꿔 응답 3.2초, 물체 수는 18.5초 뒤 갤러리에 채워짐. 50M 모듈은 2×2 자리 평균이 두 조명에서 같아 색 필터가 없는 것으로 보고 흑백만(색 있는 물체로 확인 전). EEPROM에 고해상도 펌웨어를 썼다(다시 읽어 일치). ③ NORMAL에서 키트를 리셋해 EEPROM 부팅을 확인했다(2026-11-23 10:12, 325프레임 온전, 4.37 fps). 서비스 재시작만으로는 키트 전원이 끊기지 않는다.** 남은 것: 새 화면 설치(16M 화소 크기 포함)와 실물 키트 확인, 보드4에서 50M, 8M 모듈 시험, HDMI, 부팅 모드 전환, 보드3 설치, 화소 크기 확인 | 11.64, 11.65, 11.67~11.75, 11.94 |
| GPU(Mali-G52, 11.82) | **2026-11-22 보드3: vendor `mali_kbase`(r38p1) 위에서 오픈소스 Mesa panfrost가 그린다.** Icecream95 `kbase-g52` 브랜치(drm-shim이 panfrost ioctl을 kbase로 바꿈)에 패치(stat 가로채기, 성능 카운터, CPU 비캐시 메모리 + 큰 이미지 BO만 캐시하고 shim이 직접 clean/invalidate, AFBC·IDVS 끔). 화면 없는 GLES2 시험 7가지 PASS, 1080p 전체 화면 셰이더 103 fps(캐시 정리 비용, `PAN_KBASE_UNCACHED=1`이면 322 fps). **HDMI에 보이기(1번 방법):** `gles-x11`이 GPU로 그린 토러스를 `glReadPixels`로 읽어 X 창에 `XPutImage`로 보낸다. 1280×720에서 42~54 fps(처음 7.7 fps). 남은 병목은 X로 보내기 10~12 ms(SysV IPC가 없어 MIT-SHM 불가). root 불필요(`/dev/mali0` 0666), 시스템 파일 변경 없음(`~/gpu/`). `tools/gpu/build.sh`(Mac Docker), `run-test.sh`·`gles-x11`(보드). X·Firefox 자체의 GPU 가속(2번 방법)은 아직: DPU KMS 장치, dma-buf 공유, DRI3가 필요하다. **11.83: dpu는 fbdev(DRM 아님)지만 `S3CFB_WIN_CONFIG`가 dma-buf를 바로 화면에 낸다. 커널에 DRM·CMA 헬퍼가 EXPORT되어 있어 외부 모듈로 KMS를 만들 수 있다. 설계안 A~E, 사용자는 B 다음 A를 골랐다.** **11.84(B): GPU가 fb0 메모리에 바로 그린다(`modules/e850-fbbuf`가 fb0 dma-buf fd를 줌, 손으로 insmod). shim에 dma-buf 가져오기·BO 해제·참조 수 수정. `gles-fb` 1080p 한 장 116~164 fps, pan 두 장 30 fps(pan이 vsync를 기다림). 사용자 공간 `S3CFB_WIN_CONFIG`는 fb0 메모리를 풀어 버려 쓰지 않는다.** **11.85(A 1단계): KMS 장치 `modules/e850-kms`(`/dev/dri/card0`, 이름 `exynos`, fbdev pan으로 DRM 버퍼를 보임, 손으로 insmod)와 Mesa kmsro. `gles-kms dumb`과 GPU(GBM) 페이지 전환 모두 HDMI에 나옴, 30 fps 상한(pan이 두 프레임 주기를 씀).** **11.86(A 2단계): vt8의 두 번째 X(`modules/e850-kms/x-kms.sh`, modesetting + glamor on Mali, DRI3)가 동작한다. GPU가 X 창에 바로 그리기 720p 78~92 fps(복사 방식 42). Firefox는 하드웨어 WebRender·WebGL(Panfrost GLES 3.1). 그러나 YouTube 720p60은 39~46 fps(드롭 25~37%)로 소프트웨어 경로(58 fps)보다 나쁘다.** **11.87: 원인은 shim이다. (1) 제출마다 목록의 캐시 BO를 모두 clean(5초에 4~32 GB). Mesa가 CPU로 쓴 리소스 BO만 알리게 고쳐 48~52 fps(드롭 14~26%). (2) 동기 제출이 남았다. Renderer가 GPU 작업마다 기다려 시간의 약 55%를 쓰고, GPU는 377 MHz에 머문다. 화면을 떠 보니 11.86 판부터 페이지 글자·타일이 깨진다(캐시와 무관).** **11.88: shim 비동기 제출(atom마다 앞 atom에 ORDER 의존, syncobj와 fence를 순번으로 구현, 공유 BO를 쓴 배치는 Mesa `panfrost_flush`에서 기다림, `PAN_KBASE_SYNC=1`이면 동기). X를 새 Mesa로 다시 띄웠다. YouTube 720p60 약 58~60 fps(드롭 2~6%, 새 Firefox 첫 회는 더 낮을 때가 있음), 소프트웨어 경로와 같다. 렌더링 깨짐의 원인을 찾았다: 꼭짓점 셰이더가 쓴 위치가 셰이더 코어 LS 캐시에 남아 타일러가 앞 배치의 값을 읽는다(`gles-inst`로 재현, IDVS가 틀리던 것도 같은 원인). Mesa가 꼭짓점 job과 타일러 job 사이에 LS clean cache flush job을 넣어 고쳤다(`PAN_E850_FLUSH=0`이면 끔). 터미널·Firefox 글자가 다 보이고, 잰 범위에서 비용은 없다. 로컬 AV1 720p60 클립 30분: 평균 59.9 fps, 드롭 0.27%, 메모리 늘지 않음(YouTube는 자동 시험 재생을 약 50초에서 끊어 긴 시험에 못 씀). GPU DVFS는 kbase가 사용률을 모으지 않아(busy·idle 모두 0) 377 MHz에 머문다.** 다음: GPU 사용률(DVFS), compute→다음 job 경로 시험, KMS vblank | 11.82~11.88 |
| 골라서 넣는 기능(`features/`) | **2026-11-21(11.76~11.81).** **11.80: desktop 패키지가 `libpam-systemd`를 들이면 로그인마다 `user@UID.service`가 cpu 컨트롤러를 위임받아 cpu cgroup이 9개를 넘고, vendor `ems.ko`(9칸 배열을 `css->id - 1`로 씀)가 커널 패닉을 낸다(보드3에서 X 시작 때, 그 뒤 부팅마다). 수정: 부트 명령줄 `cgroup_disable=cpu`(`build-bootimg.sh`, 지금 이미지에 바로 쓸 판 `~/e850-logs/boot-nocpu/boot.img`), desktop 기능이 apt 전에 `user@.service.d/e850-no-cpu-delegate.conf`. drop-in은 Debian 12 컨테이너에서 cpu css 13 → 1 확인, 보드에서는 아직. 보드3은 `fastboot boot`(이 LK에서 안 됨, 7.3) 시도로 LK가 멈춰 전원 재투입 대기. 보드4 사고도 이것으로 추정(미확인).** 보드에서 `e850-feature install 이름…`. Mac에서 `features/host/bundle.sh` → `deploy.sh`. 유선랜(`smsc95xx`, eth0 MAC 고정, 케이블 없어 링크 미확인), 오디오(패치한 `exynos3830_sound`, 헤드폰 경로, 재생 속도 확인, 귀로 미확인, dmix 불가: SysV IPC 없음), MFC(`/dev/video6`·`7`, 디코딩 미시험), BT(`scsc_bt` → `/dev/vhci` 브리지, hci0·스캔 확인), USB 호스트(`e850-usbhost on`), X(`desktop`, 따로). **BT를 켠 채 WLBT 패닉이 나면 Wi-Fi 복구가 실패하던 vendor 경합을 `modules/scsc-wlan-recovery`로 고쳤다(보드3에서 8.1초 복구 확인). bt 설치가 함께 넣고 다음 부팅부터 쓰인다.** 보드3: ethernet·audio·video·bt 설치, 재부팅 후 모두 정상. **11.81(보드3, boot-nocpu):** 패닉 0회, cpu 컨트롤러 없음, 로그인 세션이 있어도 정상. **vendor `exynos_mfc`가 udev `v4l_id`와 부팅 경합으로 패닉하던 것을 `modules/exynos-mfc`로 고쳤다(부팅 세 번 중 두 번 경합 발생, 고친 판은 견딤). video 기능이 넣는다.** MFC는 MMAP 버퍼를 받지 않아 ffmpeg v4l2m2m 디코딩은 안 된다. X가 뜨지 않던 원인은 desktop 기능에 `kbd`(chvt)·`xserver-xorg-legacy`가 빠진 것과 스크립트 실행 비트(644)였다(고침, 2b5a74e). **보드3에서 X+Openbox+Firefox가 HDMI에 뜬다. X 안 YouTube: 480p 29.9 fps 드롭 1%(CPU 31%), 720p60 55.9 fps 드롭 8%(55%).** headless Firefox YouTube: 480p 30 fps 드롭 0(CPU 35%), 720p60 61 fps 드롭 0.5%(46%). **보드4: desktop 설치 직후 Wi-Fi에서 사라졌고, 전원 재투입 후에도 무선이 안 된다(2026-11-21 08:20). 11.80의 패닉 또는 MFC 경합으로 추정, micro-USB로 boot-nocpu를 굽고 `last_kmsg`로 확인해야 한다.** **11.89(보드3): desktop에 패널(lxpanel 메뉴·작업 표시줄)·파일 관리자(pcmanfm)·한글 입력(fcitx5-hangul, Ctrl+Space/오른쪽 Alt), 세션마다 D-Bus. GPU X(:1)도 같은 세션을 GPU Mesa 환경으로 띄우고, `/usr/local/bin/firefox`가 :1에서 별도 프로필을 쓴다. bt에 bluez-alsa(A2DP, `PrivateUsers=no` 필요: USER_NS 없음). JBL Flip 6 페어링·연결(`pairable on` 필요), `aplay` 실제 속도, 귀로는 사용자 확인 대기. 한 번에 한 프로그램.** **11.90(보드3): 부팅하면 HDMI root 프롬프트 + USB 키보드(usbhost 부팅 때 켬, ACM 없음), dvslive 끔, `e850-x`가 MIF·INT를 최대로 고정하고 GPU 데스크톱을 띄운다(11.82~11.89 측정은 dvslive가 건 이 고정 상태였다). `e850-x`가 GPU도 1001 MHz로 고정(`e850-x stop`이 풂). zram 1.8 GB(lz4). root 콘솔에서 띄우면 메뉴가 비던 것(root `XDG_RUNTIME_DIR`가 넘어감)을 `x-kms.sh`의 `env -i`로 고침. 한글 입력 xdotool 확인. Wi-Fi(wpa_gui, 트레이 신호 아이콘)·BT(blueman) 설정, 음량 아이콘(BT 스피커 믹서). 재부팅 확인 전.** **11.91: 보드3 데스크톱 이미지 `~/e850-logs/clone-20261123/`(boot-nocpu + 8 GiB rootfs 8조각, 절차 BOARD-CLONE.md 11장). 11.92: 보드5(192.168.0.206)에 구워 부팅, 파일 37,094개 md5 일치. `features/usbhost`가 `systemd-networkd-wait-online --any` drop-in을 넣는다(usb0 DOWN이라 2분 대기 뒤 degraded였음). `features/bt`가 쓰지 않는 `bluealsa-aplay`를 끈다. 보드5 HDMI·`e850-x`·BT 재페어링 확인 전. 11.93: 보드6(192.168.0.208)에 같은 이미지 + 현미경 뷰어(`e850-scope`, OpenCV, 호스트 모드는 scope가 맡음) + Cloudflare 임시 터널. 16M 모듈 3.36 fps, 외부 링크 200.** | 11.76~11.81, 11.89~11.91 |
| 강의 자료(2026-11-23, 11.95) | 16:9 편집 가능 HTML 두 개. `docs/E850-BRINGUP-LECTURE-20261123.html`(26장: 부팅 구조, 드라이버 올린 순서 18단계, X, 최적화, GPU, 복제, 함정)과 `docs/SCOPE-LAB-LECTURE-20261123.html`(21장: 키트 펌웨어, 모듈 3종, USB 호스트, 웹 뷰어, 분석·실험, 고해상도 사진, MIPI 직결). PDF와 함께 메일로 보냈다. 현미경 자료의 시료 사진(08:54) 확대 두 장은 샘플 이미지라 공개해도 된다고 사용자가 확인했다 | 11.95 |
| BT 검색 고착(2026-11-23, 11.96) | 보드6에서 blueman이 아무것도 못 찾았다. 커널 검색 상태가 STOPPING에 갇혀 있었다(Stop Rejected, Start Busy). 정지 요청의 HCI 명령이 오류로 끝나면 생긴다(커널 소스, 결함 주입으로 재현). `e850-btbridge.py`에 감시 스레드를 넣었다: 검색 중인데 검색 명령이 30초 없으면 가짜 Remote Name Request Complete → 그래도면 어댑터 껐다 켬. **보드에서 감시 동작은 아직 확인 못 함.** **11.100: 보드6 밤샘 끊김의 원인을 찾았다.** 연결된 채 같은 AP로 다시 연결하면(`slsi_connect vifStatus=2`, `wpa_cli reassociate`로 재현 3번) 드라이버 송신 큐가 프레임을 거부해(`no fcq`) DHCP를 못 받는다. 감시 `e850-wifiwatch`(보드6만)가 disconnect+reconnect로 되살린다(1번 시험, 36초). 새 브리지는 2026-11-24 01:01 `e850-bt` 재시작으로 쓰이기 시작했다(JBL 30초 뒤 다시 붙음) | 11.96, 11.98, 11.100 |
| `dvs` 기능(2026-11-23, 11.97) | `features/dvs`: X 터미널에서 `dvs` → "DVS" 창(960×720, XPutImage) + 웹 :8080. 센서가 없으면 데모 녹화(stand_up 7초)를 되풀이했다(`dvs_replay`). 11.114부터는 센서가 없으면 "연결되어 있지 않습니다"를 보이고 끝난다. Mac Docker(Xvfb)에서 창과 웹 모두 사람 모양을 확인했다. 보드6에 설치해 확인했다(11.98) | 11.97 |
| 보드6 DVS 데스크톱(2026-11-24, 11.98) | 보드6 CN4에 DVS가 있다(현미경은 사용자가 뗐다, `e850-scope`는 그대로 기다림). `dvs`는 캡처가 끝나면 다시 띄운다(130 = 사용자 종료, 3 = 센서 없음, 바로 실패 3번이면 포기). `x-kms.sh`가 데스크톱 시작 때 `dvs --auto`를 `e850-dvs` 유닛으로 띄운다. C 설정 패널 `dvspanel`(GTK 3)이 DVS 창 오른쪽에 뜬다. BT: SBC XQ(452 kbps), ALSA 변환기를 speex로. **귀로 나아졌는지는 미확인** | 11.98 |
| DVS 패널·어두운 프레임·얼굴(2026-11-24, 11.99) | 패널을 웹 설정창과 같은 내용으로(통계, 행동, 그래프, 녹화, 레지스터 읽기. 레지스터 이름은 비공개 자료라 뺌). 빠른 동작 때 프레임 최대 65%가 비던 것: 잡음 필터를 켜면 지연이 필터 시간을 빼고 계산됐다. 지연을 필터 스레드에서 재고 프레임을 필터가 넘긴 데까지로 해서 고쳤다(13.8만 이벤트/프레임에서 0장/2초, GPU 아님). 얼굴: YuNet CNN(입력 크기를 풀어 134 ms/회), DVS 창에 노란 상자와 `W n cm`(px × 17/960 × 19/3, 1 m 기준). **보드에서 얼굴 검출은 아직 0건(얼굴이 화면 밖이었음)** | 11.99 |
| HDMI 콘솔 색 | 흰 글씨. dpu.ko 패치로 근본 수정했다(`e850-fbpal`과 rebind는 쓰지 않음). 전원을 껐다 켠 뒤에도 사용자가 화면에서 확인했다 | 11.14~11.16 |
| HDMI 출력 꺼짐 | 해결. `decon_release()`가 fb0를 닫을 때마다 DECON을 끄던 것을 open 수 세기로 고쳤다(dpu.ko 재빌드) | 11.15 |
| 두 번째 보드 | **2026-11-14 11.53: 복제 완료(boot-dvs3 + A의 rootfs, machine-id만 새로).** Wi-Fi 192.168.0.168, 웹 8080 동작. 처음에는 MIPI CRC가 모든 줄에 났다(dvslive가 수십 초마다 재시작하다 한 번은 보드가 멈춤). **CN4 케이블을 다시 꽂자 CRC 0, 데이터 100%로 A와 같아졌다.** USB-C는 다시 확인해야 한다 | 11.53 |
| 네트워크 | Wi-Fi 192.168.0.236(ssh), USB 192.168.7.1(gadget 함수는 `ecm.usb0`, 즉 ECM. 문서의 "NCM"은 ECM이다). **11.52: USB에서 보드→Mac 방향만 멈춘 적이 있다(원인 모름). UDC `soft_connect`를 끊었다 붙여 재부팅 없이 복구했다.** **Wi-Fi 펌웨어 패닉 2번**(2026-11-07, 부팅 후 2.6시간과 6분, `WLBT FW PANIC: 0x04`, 원인 모름·재현 못 함). **11.31부터 자동 복구를 켰다**(`e850-wifi.sh`가 `disable_recovery_handling=0`). 강제 패닉 4번(런타임 설정)과 1번(부팅 설정) 모두 복구, `wlan0` 재연결 5.6~6.5초, 8080 응답 5.6~7.3초. 복구 횟수는 `/proc/driver/mxman_ctrl0/mx_recovery_count`. **11.32: journal을 초당 130줄 → 분당 38줄로 줄였다**(카메라 bypass 경고, 워치독 keepalive). journal은 약 2주 남는다(추정). 11.33: vendor 버그 2개 수정(`mx_panic` 0600, system error log WARN). **11.47: 저절로 나는 0x04 패닉(약 3.8시간에 1번)은 펌웨어 Data Abort다. 2026-11-13 00:47 KST에 펌웨어를 Linaro Android 13 판 `S612_E3830_R4-1_Y23W13A`(2023-03)로 바꿨다**(옛 판 2019-12). 다시 붙는 데 8초 걸렸다. **24시간 시험(11-13 00:47~11-14 00:47 KST)에서 패닉 0번**(11.48) | 11.5, 11.30, 11.31, 11.32, 11.33, 11.47, 11.48, 11.52 |
| 서비스 | nginx(80), ssh, e850-hdmi, e850-wifi, systemd-timesyncd, 워치독 데몬 | |
| 사용자 계획 | MySQL + Apache로 출입 기록, YOLO nano 영상 처리 | |

## 2. HDMI / DECON / fbcon 문제

### 2.1 확인된 사실 (소스와 측정으로 확인함)

- **fb visual:**
  - DPU fb(`/dev/fb0`, 32 bpp, RGB 8:8:8)는 부팅 직후 `fix.visual = FB_VISUAL_MONO01`이다.
  - vendor dpu 드라이버에서 `fix.visual`을 바꾸는 곳은 `decon_set_par()`뿐이다.
  - `decon_set_par()`는 DECON 상태가 OFF나 INIT이면 visual을 설정하기 전에 return한다.
  - fbcon이 붙는 시점에 DECON은 아직 켜지지 않았으므로, fbcon은 흑백(MONO) 모드로 붙는다.
- **팔레트:** `decon_setcolreg()`는 DECON이 OFF나 HIBER일 때 쓰기를 버리고 성공을 돌려준다. 그래서 `vt.default_*`나 `ESC ] P`로 팔레트를 바꿔도 반영되지 않는다.
- **색 순서:** fbcon의 pseudo-palette 인덱스는 VGA 순서(1=파랑, 4=빨강)이고, `default_red/grn/blu` 배열은 ANSI 순서(1=빨강, 4=파랑)다. 변환하지 않고 넣으면 빨강과 파랑이 바뀐다.
- **`decon_release()`(11.15):** vendor 드라이버는 fb0가 닫힐 때마다 DECON, DSIM, 패널을 껐다. fbcon unbind도 포함이다. DECON이 꺼져 있을 때 한 pan은 버려진다(`UNBLANK missed`). 그래서 11.14 이후 HDMI 출력이 꺼져 있었다.
- **현재 해결책(11.16, `modules/e850-hdmi/apply.sh`의 dpu.ko 패치):**
  1. 창을 만들 때 `fix.visual = TRUECOLOR`로 설정한다. 그래서 fbcon이 부팅 때부터 색 모드로 붙는다.
  2. `decon_setcolreg()`는 DECON 상태와 관계없이 pseudo-palette에 저장한다. 그래서 fbcon이 `vt.default_*`를 넣는다(VGA/ANSI 변환은 fbcon이 한다). vendor 디버그 `printk("@@@@")`도 뺐다.
  3. `decon_release()`는 마지막 사용자가 닫을 때만 DECON을 끈다(11.15).
  4. `fb-hold.py`가 unblank하고 pan을 한 뒤 fd를 계속 쥐고 있다(11.15).
  - `e850-fbpal`과 fbcon rebind는 서비스에서 뺐다. 모듈 소스(`modules/e850-fbpal`)와 보드의 `.ko`는 남겨 두었다.
- **측정(`tools/board/fbhist.py`, fb0 메모리의 픽셀 히스토그램. HDMI 출력이 아니다):**
  - 배경 0x000000, 글자 0xffffff
  - `\033[31m` → 0xaa0000, `\033[32m` → 0x00aa00, `\033[34m` → 0x0000aa
  - 재부팅한 뒤에도 같다.
- **HDMI 화면:** 2026-11-02 07시(11.15), 재부팅 뒤 사용자가 흰 글씨를 눈으로 확인했다. 그 전(11.14)에는 출력이 꺼져 있었다. 11.16 모듈로 바꾼 뒤 전원을 껐다 켰을 때도(2026-11-06) 확인했다.
- **fb0 열고 닫기:** `fbhist.py`를 두 번 실행해도 `decon_disable`이 찍히지 않는다.

### 2.2 가설 (아직 확인하지 않음)

- DECON이 HIBER(화면 절전)에 들어갔다 나와도 색이 유지될 것으로 본다. `decon_set_par()`는 32bpp에서 visual을 TRUECOLOR로 설정하고, 팔레트는 메모리에만 있다. 측정은 하지 않았다.
- HDMI 케이블을 다시 꽂거나, `ov5647-cap.py --live`가 fb를 직접 쓰다가 끝난 뒤에도 화면과 색이 유지되는지는 모른다.
- LT8912B HPD 레지스터(`0xc1` bit7)는 화면이 나올 때도 `not connected`로 읽혔다. 판정에 쓰지 않는다.

### 2.3 실패한 실험

| 시도 | 결과 | 원인 |
|---|---|---|
| 부트 인자 `vt.default_red/grn/blu`만 설정 | 글자가 그대로 파랑 | fb가 MONO01이라 fbcon이 팔레트를 쓰지 않음 |
| `ESC ] R` / `ESC ] P`로 팔레트 재설정 | 변화 없음 | `decon_setcolreg()`가 OFF/HIBER에서 쓰기를 버림 |
| `e850-fbpal` 첫 버전(visual은 두고 팔레트만 채움) | 적재 실패 `-ENODEV` | visual 검사에 걸림. TRUECOLOR를 강제하는 것으로 고침 |
| `e850-fbpal` 둘째 버전(ANSI 순서 그대로 채움) | 빨강과 파랑이 바뀜 | VGA/ANSI 인덱스 차이. `vga_to_ansi[]` 표로 고침 |
| fbcon rebind를 `fb-hold.py` 뒤에 실행(11.14 서비스) | HDMI 출력 꺼짐. fb0 메모리만 정상이라 놓침 | rebind의 `decon_release()`가 DECON을 끔 |
| rebind 뒤 `fb0/blank`로 unblank만 함 | 검은 화면 | pan이 없어 fb0가 화면에 나가지 않음 |
| 다른 프로세스에서 pan | 흰 글씨가 잠깐 보였다가 사라짐 | 그 프로세스가 fd를 닫을 때 다시 꺼짐 |

### 2.4 보드에 실제로 바꾼 것 (이번 세션)

| 경로 | 변경 |
|---|---|
| `boot` 파티션 | **2026-11-07 08:27 KST부터 `~/e850-logs/boot-dvs3/boot.img`**(boot-dvs2 + 조용한 `s3c2410_wdt.ko`, gadget을 기다리는 `e850-vbus.ko`, 11.32). 그 전 판은 `boot-dvs2`(DT mode1 8192×32, 11.26), `boot-dvs`. 복구용 정상 이미지는 `boot-nokvm` |
| `/lib/modules/.../is-cis-s5krc1s.ko` | **2026-11-07 margin·regs param + 0x30A6 0x75(실험값, 11.46)(0b35f571…, 11.29)**. 이전: `/root/is-cis-s5krc1s.ko.prev-margin`(1b5c7d77), `.prev-11.28`(01dd3c98, 모드별 줄 수 127/32 판, 11.26), `.mode0only` |
| `/lib/modules/$(uname -r)/e850-cpuhp.ko` | 추가. `/etc/modules-load.d/e850-cpuhp.conf`로 자동 적재 |
| `/lib/modules/$(uname -r)/e850-fbpal.ko` | 추가. 11.16부터는 올리지 않는다(파일만 남음) |
| `/lib/modules/$(uname -r)/is-cis-ov5647.ko` | VTS 수정판으로 교체. 원본은 `/root/is-cis-ov5647.ko.orig` |
| `/etc/systemd/system/e850-hdmi.service` | fbpal과 fbcon rebind를 뺌(11.16). 저장소와 같은 파일(md5 `d92fce11…`). 이전 판은 `/root/e850-hdmi.service.fbpal`(11.15), `/root/e850-hdmi.service.bak`(11.14) |
| `/usr/local/bin/fb-hold.py` | unblank 뒤 pan 추가(11.15). 이전 판은 `/root/fb-hold.py.bak` |
| `/lib/modules/$(uname -r)/dpu.ko` | 11.16 패치판(md5 `4512e825…`). 11.15판은 `/root/dpu.ko.opencnt`, 원본은 `/root/dpu.ko.orig` |
| `/home/philip/.ssh/authorized_keys` | 사용자가 Mac 키를 등록(`ssh-copy-id`). `ssh philip@192.168.0.236` 키 접속 가능. sudo는 비밀번호 필요 |
| `/usr/local/bin/ov5647-cap.py` | 캡처 중 printk 끄기, u32 LUT. 저장소와 md5 같음 |
| `/lib/modules/$(uname -r)/is-cis-s5krc1s.ko` | 11.18 판으로 교체. 원본은 `/root/is-cis-s5krc1s.ko.orig` |
| `/usr/local/bin/ov5647-cap.py` | `--raw8` 추가(11.19). 이전 판 `/root/ov5647-cap.py.bak` |
| `/root/csis-peek.ko`, `/root/dvsreg.sh` | CSIS 레지스터 읽기 모듈, 스트리밍 중 센서 레지스터 스냅샷 |
| `/lib/modules/.../is-cis-s5krc1s.ko` | 2026-11-06 `to_scnt1`=1999로 바꾼 빌드(e296fa2f…, 11.21). 이전 모듈: `/root/is-cis-s5krc1s.ko.2000fps` |
| `/lib/modules/.../fimc-is.ko` | 2026-11-06 CSIS 오류 clear 패치 + speed/settle 파라미터(7f47a2a8…, 11.22~11.23). 이전: `/root/fimc-is.ko.nopanic` |
| `/lib/modules/.../fimc-is.ko` | 2026-11-06 `e850_csi_err_stop` 파라미터 추가(a2149bb7…, 11.24). 이전: `/root/fimc-is.ko.prev-11.23` |
| `/usr/local/bin/ov5647-cap.py` | `--live --raw8`(DvsLive), SIGTERM으로 정상 종료(11.24), 버퍼 모아 디코딩(11.25), libdvsview.so 호출 + `--fps`(11.27). 저장소와 같은 파일 |
| `/usr/local/lib/libdvsview.so` | 추가(11.27). `tools/linux510/dvsview.c` 빌드 |
| `/lib/modules/.../fimc-is.ko` | 2026-11-06 crc_keep(CRC를 `error_id`에서 뺌) + EMERG 디버그 printk 없음(099c323b…, 11.28). 이전: `/root/fimc-is.ko.prev-bypassdbg`(113b4d93), `.prev-crcflush`(908e4ee0), `.prev-11.27` |
| `/usr/local/lib/libdvsview.so` | 2026-11-06 SUB 복구·캐시 write-back·1비트 필터·설정 API(42e1bb22…, 11.28). 이전: `.prev-subfix`(bab245d3), `.prev-11.28`(877162f9) |
| `/usr/local/bin/ov5647-cap.py` | `--ctl`, `--boxes`, cached heap, SYNC ioctl 뺌(ae0fab5c…, 11.28). 저장소와 같은 파일 |
| `/usr/local/share/dvsview/dvsctl.py`, `dvsctl.html` | 추가(11.28). 화면 조정 앱. 설정은 `/var/lib/dvsview/display.json` |
| `/usr/local/bin/dvslive.sh` | 추가(11.28). live 시작 스크립트(`tools/board/dvslive.sh`). 2026-11-07 foreground로 바꾸고 `sensor.json`의 margin(기본 17)·flush_us를 씀(38b7d03d…, 11.29) |
| `/etc/systemd/system/dvslive.service` | 추가·enable(11.29, a1252e90…). 부팅 때 live + 8080 페이지, 끝나면 3초 뒤 다시 시작. 센서 설정은 `/var/lib/dvsview/sensor.json` |
| `/usr/local/lib/libdvsview.so` | 2026-11-07 scan 주기 측정 + `dvs_png`(17f1368f…, 11.29). 이전: `.prev-scan`(42e1bb22) |
| `/usr/local/share/dvsview/dvsctl.py`, `dvsctl.html` | 2026-11-07 CPU7 고정(`dvs-web`, uclamp), keep-alive, TCP_NODELAY, 20/30 fps(b53fd0b9…, 5171b486…, 11.30). 그 전: 센서 설정, CRC/s, canvas 미리보기(01bebea2…, 275b6bbe…, 11.29) |
| `/etc/systemd/system.conf.d/e850-cpu7.conf` | 추가(11.30, 39e8c1a4…). `CPUAffinity=0-6`: systemd가 띄우는 모든 프로세스를 CPU0~6에 둔다. CPU7은 웹 스레드만 |
| `/usr/local/bin/e850-wifi.sh` | 2026-11-07 `modprobe scsc_mx disable_recovery_handling=0`(47489536…, 11.31): Wi-Fi 펌웨어 패닉 뒤 자동 복구. 이전: `/root/e850-wifi.sh.prev-recovery`(7d4af31c) |
| `/lib/modules/.../is-cis-s5krc1s.ko` | 2026-11-06 to_scnt1 999 복원 + `packets`/`flush_us` param(54365f52…, 11.25). 이전: `/root/is-cis-s5krc1s.ko.1999` |
| `/lib/modules/.../fimc-is.ko` | 2026-11-07 bypass 경고 로그 끔(f1e7bbf3…, 11.32), querycap WARN 수정(ca6c8863…, 11.34, taint W 없어짐). 이전: `/root/fimc-is.ko.prev-11.34`(f1e7bbf3), `.prev-11.31`(099c323b) |
| `/lib/modules/.../s3c2410_wdt.ko`, `e850-vbus.ko` | 2026-11-07 boot-dvs3와 같은 판(dfd6bae7…, 0990ffa3…, 11.32). 이전: `/root/*.ko.prev-11.32` |
| `/lib/modules/.../scsc_mx.ko`, `scsc_wlan.ko` | 2026-11-07 `mx_panic` 0600, system error log 크기 버그 수정(ee2b5944…, a02d408e…, 11.33). 이전: `/root/scsc_*.ko.prev-11.33` |
| `/var/tmp/boxbench/boxes.jsonl` | 추가(11.35). 상자 측정 1차 시험 기록. 지워도 된다 |
| `/lib/modules/.../is-cis-s5krc1s.ko`, `/usr/local/lib/libdvsview.so`, `/usr/local/share/dvsview/dvsctl.py` | **2026-11-12 11.46판**: CIS `pll` 파라미터와 `pll_live` 다시 읽기(182f06f0…), `/stats`의 `link` 계수기(db2b51bd…), `pll`(5ec64db2…). 이전: `/root/is-cis-s5krc1s.ko.prev-11.46`, `/usr/local/lib/libdvsview.so.prev-11.46`, `/root/dvsctl.py.prev-11.46` |
| `/usr/local/bin/ov5647-cap.py` | **2026-11-12 23:43 KST**: 이상 상태가 되면 스트림을 다시 켠다(`--stuck`, md5 bcfde139, 11.46). 이전 판은 `/root/ov5647-cap.py.prev-stuck`(md5 03fdbff4)이다 |
| `/usr/local/lib/libdvsview.so` | **2026-11-12 23:04 KST**: 22비트 기준값 한 바퀴를 처리한다(md5 c10b0382, 11.46). 이전 판은 `.prev-wrap`(md5 db798ea1, 11.46판)이다. `/var/tmp`에 `wifistuck.sh`, `stuckscan.py`, `stuck2/`(이상 상태 dmesg·녹화)를 추가했다 |
| `/var/tmp/plltest.py`, `dvslink.py`, `pll-ab.sh`, `plltest/` | 추가(11.46). PLL 시험 실행기, 기록 검사기, A/B 묶음, 결과(Mac `~/e850-logs/plltest/`에 사본) |
| `/usr/local/bin/ov5647-cap.py` | **2026-11-12 128줄 디코딩**(`dvs_ingest(buf, W × (H + 1))`, md5 `03fdbff4…`, 11.46). 이전: `/root/ov5647-cap.py.prev-11.46`(7d82fa17) |
| `/lib/modules/.../is-cis-s5krc1s.ko`, `/var/tmp/plltest.py` | **2026-11-14 08:20 KST 11.49판: 기본 setfile PLL 00/8B/B0**(모듈 md5 f96703b7, plltest 6787f7a4). 이전: `/root/is-cis-s5krc1s.ko.prev-11.49`(0x75, 0fe3ba23), `/var/tmp/plltest.py.prev-11.49` |
| `/usr/local/bin/ov5647-cap.py`, `dvslive.sh`, `/lib/modules/.../csis-irqstat.ko` | **2026-11-14 11.51판**: DQBUF마다 VC0 버퍼 상태(QUERYBUF, 345 µs), dvslive가 csis-irqstat 적재(MIPI FS/FE·링크 오류 계수, `/sys/module/csis_irqstat/parameters`). 이전: `/root/ov5647-cap.py.prev-11.51`, `/root/dvslive.sh.prev-11.51` |
| `/usr/local/share/dvsview/dvsctl.py`, `dvsctl.html`, `dvsfont.json` | **2026-11-14 11.50판**: 웹 크롭, 센서 레지스터 읽기(`/reg`, 읽기만), 1시간 통계 그래프(`/history`, 11.51에서 MIPI·DRAM 묶음과 개수 타일), HDMI 왼쪽 아래 한글 접속 주소. 이전: `/root/dvsctl.py.prev-11.50`, `dvsctl.html.prev-11.50`. `dvsfont.json`은 `tools/host/hangul-atlas.py`로 만든다(저장소에 없음). 11.52에서 배너가 fb0를 한 번만 열도록 고쳤다(`decon_release` 로그가 5초마다 찍히던 것, 40cc3f46). **11.54: 웹 미리보기 90° 회전 버튼**(웹 화면만, `dvsctl.html` 6df1e198, 이전 `/root/dvsctl.html.prev-11.54`) |
| `/vendor/etc/wifi` | **2026-11-13 00:47 KST**: Linaro A13 WLBT 펌웨어(`mx140.bin` md5 792565d8, `S612_E3830_R4-1_Y23W13A`, 규제 DB `regulatory_db_2_7`, 11.47). 이전(Android 10) 판은 `/var/tmp/wifi-a10-vendor`에 있다. 되돌리기는 root `sh /var/tmp/wifi-fwswap.sh a10`이다. 받기는 `tools/linux510/wifi-fw-linaro.py`다. 01:15 KST부터는 `build-rootfs.sh`가 `out/wifi-a13.tgz`를 넣고, Wi-Fi 패키지와 `e850-wifi`도 넣는다(11.47) |
| `/usr/local/bin/e850-wifi.sh` | 2026-11-13 펌웨어 출처 주석만 고침(22c43270, 11.47). 이전: `/root/e850-wifi.sh.prev-11.47`(47489536) |
| `/etc/sysctl.d/90-e850-autoreboot.conf` | 추가(11.22). `softlockup_panic=1`, `panic=5` |
| `/root/csis-irqstat.ko` | 추가(11.22). kretprobe IRQ 계수기, 캡처 전에 insmod하고 끝나면 rmmod |
| `/usr/local/bin/dvscap.sh` | 추가(11.23). devfreq 최대 + 캡처 + IRQ 계수 |
| `/root/dvs-power.ko` | 추가(11.17). 수동 insmod로만 DVS 전원을 켬. 지금은 내려 둠 |
| `/root/` | 시험용 스크립트 `ovreg.py`, `hp.sh`, `fbhist.py`, `stress1h.sh`, `.ko` 사본 |
| 계정 / Wi-Fi | `philip` 계정. Wi-Fi는 PSK 해시만 보드에 있음(저장소에는 없음) |

## 3. 다음에 할 실험

0. **웹 서비스(dvsctl) 이어서 할 일 (2026-11-14, 브랜치 `dvs-web-tools`, PR #7 병합 전)**
   - 지금 상태: 웹 페이지 `tools/linux510/dvsctl.html`과 서버 `dvsctl.py`. 서버는 캡처 프로세스(dvslive) 안에서 CPU7로 돈다. 페이지는 요청마다 파일에서 읽으므로 HTML만 바꿀 때는 dvslive를 다시 시작하지 않는다. 기능과 경과는 11.50(크롭, 레지스터 읽기, 그래프, HDMI 주소 배너), 11.51(MIPI·DRAM 그래프), 11.52(배너 fb0), 11.54(90° 회전)에 있다.
   - 보드: 두 보드 모두 회전 페이지가 설치되어 있다(`dvsctl.html` md5 6df1e198, 보드1은 16:42 KST에 설치됨, 11.55).
   - (끝남, 11.55) 회전 버튼과 돌린 화면의 크롭을 실제 마우스 입력(headless Chrome + CDP)으로 확인했다. 두 보드 모두 17개 통과. 시험: `python3 tools/host/dvsctl-rotcrop.py http://<보드>:8080/ <출력 디렉터리>`.
   - 확인하지 못한 것: 실제 모니터의 HDMI 주소 배너. 크롭 정보 줄이 1줄↔2줄을 오가며 스크롤바가 생겼다 없어지는 브라우저에서는 미리보기가 7.5 px 흔들린다(페이지는 고치지 않음, 11.55).
   - 보드에 배포하는 순서: Mac에서 고친다 → `python3 -m unittest discover -s tools/linux510 -p test_dvsctl.py`를 돌린다 → 페이지를 확인한다(`dvsctl-rotcrop.py`처럼 보드 주소에서 바로 열거나, 로컬 사본에 `<base href="http://<보드>:8080/">`를 넣고 `--disable-web-security`로 연다. 11.54) → `scp`로 `/var/tmp`에 올린다 → root로 `install`한다. `dvsctl.py`를 바꿨으면 root로 `systemctl restart dvslive`를 한다.
   - 보드를 USB로 동시에 Mac에 꽂지 않는다(같은 192.168.7.1, 같은 ACM 이름). ACM에 붙은 보드는 `cat /etc/machine-id`로 구별한다(보드1 `4aeede4c…`, 보드2 `6f31c0bd…`, 보드3 `16314a53…`, 보드4 `e0c18890…`).
   - 센서 없는 보드(2026-11-15, 11.57): 센서 스트림 켜기가 실패하면 dvslive가 웹만 띄우고 빨간 안내를 보인다. 보드3·4에 설치했다. 보드1·2에는 `ov5647-cap.py`, `dvsctl.py`, `dvsctl.html`을 아직 설치하지 않았다. 설치할 때 `dvsctl.py`는 HEAD가 아니라 보드 판 + 이번 변경으로 만든다(HEAD의 `/evframe`은 새 `libdvsview.so`가 있어야 한다).
   - 보드1 USB-C가 다시 꽂아도 `not attached`이다(11.57). 보드1에서 root가 필요하면 사용자가 ssh에서 sudo로 하거나, `rmmod e850_vbus; modprobe e850-vbus`(시험 안 함)나 재부팅으로 되살린다.
   - 같이 볼 것: 보드1 dvslive가 2026-11-14에 저절로 3번 재시작했다(`NRestarts=3`). 한 번은 rootfs 전송 중이었다. 원인은 root journal로 확인해야 한다.


0-1. **DVS 스테레오 3D (2026-11-14, 브랜치 `dvs-stereo-3d`, 11.56)**
   - 설계 `docs/superpowers/specs/2026-11-14-dvs-stereo-3d-design.md`, 계획 `docs/superpowers/plans/2026-11-14-dvs-stereo-3d.md`. 1단계는 Mac에서 계산, 2단계는 경량화해서 보드1로 옮긴다. 최종 목표는 SLAM이다.
   - 배치: 미니 선풍기 앞, 보드1(광학렌즈) 정면, 보드2(기본렌즈) 왼쪽 30° 4 cm 뒤, 간격 15 cm. 보정한 뒤에는 두 센서를 움직이지 않는다.
   - **보드 설치(root 필요, 아직 안 함):** 파일은 두 보드 `/var/tmp`에 있다(`libdvsview.so` 0397eb69, `dvsctl.py` f62b7d7c). 보드마다 ssh에서 사용자가 실행한다(또는 USB-C ACM root 콘솔에서 `sudo` 없이):
     `sudo sh -c 'cp /usr/local/lib/libdvsview.so /root/libdvsview.so.prev-11.56; cp /usr/local/share/dvsview/dvsctl.py /root/dvsctl.py.prev-11.56; install -m 755 /var/tmp/libdvsview.so /usr/local/lib/libdvsview.so; install -m 644 /var/tmp/dvsctl.py /usr/local/share/dvsview/dvsctl.py; systemctl restart dvslive'`
     확인: `curl -sD- http://<보드>:8080/evframe`에 `X-Dvs-Newest`가 있고, `/stats`의 버퍼 속도(약 38~40/s)와 scan 누락이 설치 전과 같다. 되돌리기는 `/root/*.prev-11.56`을 다시 `install`하고 `systemctl restart dvslive`.
   - 실행: `python3 tools/host/dvs3d.py live`(두 보드에 `/evframe`이 있을 때). 없으면 `--png`(HDMI 화면, 시각 없음). 페이지 http://localhost:8090/.
   - 보정(사용자가 있을 때): 바둑판을 Mac에서는 http://localhost:8090/checker 로, 태블릿에서는 메일로 받은 `tools/host/dvs3d-checker.html` 파일로 띄운다(태블릿은 Mac의 localhost에 닿지 않는다. 2026-11-15에 사용자 메일로 보냄. 깜빡임은 CSS라 메일 미리보기처럼 JavaScript가 꺼져 있어도 된다). 한 칸 크기를 자로 잰다(m). `python3 tools/host/dvs3d.py calib --square <m>`을 실행하고, 두 센서가 함께 보는 곳에서 화면을 20가지 위치·기울기로 움직인다. 결과는 `~/e850-logs/dvs3d/calib.npz`이고, 판정은 rms 1 px 미만, 초점거리 비 2배 이하다.
   - 다음 할 일: ① 보드 설치와 `/evframe` 비용 측정 ② 손전등 깜빡임으로 두 보드 시각 차이 측정(2 ms 미만 목표) ③ 보정 ④ 선풍기 원판으로 3D 정확도(거리 ±5 cm, 지름 ±2 cm). 센서에서 선풍기까지 거리를 자로 재서 `--zrange 가까운,먼`(m, 기본 0.3,3)에 넣는다. 시차 범위가 이 값으로 정해진다 ⑤ A55 bench(4장 결정 뒤) ⑥ 경량화 방안(240×180·시차 32, 또는 이벤트 화소만 C로 맞추기) 결정.
   - 보드1 선풍기 바깥 날개가 안 보이는 문제(조명·조리개·초점)는 그대로다.
   - 2026-11-15(11.59): 같은 렌즈로 바꿨지만 흐려서 바둑판 모서리가 잡히지 않는다(0/30). 그 사이 시험은 대략 보정으로 한다: `python3 tools/host/dvs3d.py rough --png --baseline <간격 m>` 다음 `live --png --calib ~/e850-logs/dvs3d/calib-rough.npz --zrange 0.2,2`. 초점을 맞추면 진짜 보정(`calib`)으로 바꾼다. 보드1 `filter_ms`를 4로 하면 화면이 비는 문제는 원인을 모른다(0으로 둠). 보고서: `docs/DVS-STEREO-FEASIBILITY-20261115.html`. 목표를 "두 영상 합성으로 더 또렷한 영상"으로 정정한 뒤의 시험(보드2를 보드1 시점으로 ECC 자동 정렬, 보드2 영역 안의 보드1 화소, 쌍당 1 ms): `docs/DVS-FUSION-20261115.html`. 다음은 합성 영상 실시간 표시, 보드2 초점, 움직이는 물체. 정답 바둑판 점수(11.60, `~/e850-logs/dvs3d/eval-20261115/eval.py`): 지금은 깨끗한 보드2 혼자(0.688)가 합성(0.600)보다 높다. 가장 큰 손실은 자동 정렬(정답 정렬과 10.6 px 차이, 정답 정렬이면 합성 0.663)이고, 그다음은 보드1 검은 칸 잡음이다. AI 합성 준비(11.61): 정답 그림 페이지 `tools/host/dvsfuse_page.py`(160장 × 3초, SVG, v2를 메일로 보냄), 렌즈 왜곡 항은 효과 없음. 2026-11-15 저녁(11.62, 사용자 외출 중):
     - 정답 그림을 두 번 녹화했다. 기준 설정 8,130쌍, 최적 설정 12,207쌍이다.
     - DVS 설정을 최적화했다. root 없이 바꿀 수 있는 것만 바꿨고, 지금 보드에 남아 있다. 두 보드 모두 scan 500/s(margin 48), filter 2이고, 보드1만 window 80이다. 원래 값은 `~/e850-logs/dvs3d/rec-truth-20261115/settings-before.txt`에 있다.
     - 두 보드 U-Net 합성을 학습했다(`~/e850-logs/dvs3d/fusenet-20261115/`). 학습에 쓰지 않은 그림에서 한 쌍 기준 정답 상관이 보드2 혼자 0.30, 모델 0.65로 약 2배다. 한쪽을 빼거나 망가뜨려도 좋은 쪽 이상이다.
     - 보고서: `docs/DVS-FUSION-AI-20261115.html`.
     - 종합 보고서(11.56·11.59~11.62, 그림 포함, PDF 19쪽): `docs/DVS-BINOCULAR-REPORT-20261115.html`. 2026-11-15 21:17에 메일로 보냈다.
     - 강의 자료(16:9, 36장, 편집 가능 HTML): `docs/DVS-BINOCULAR-LECTURE-20261116.html`. 원본과 스크립트는 `~/e850-logs/dvs3d/lecture-20261116/`에 있다. 그림 30장 단위 통계에서 두 보드의 몫은 녹화 2에서 +0.075 [0.042, 0.097]이고, 녹화 1에서는 0과 구별되지 않는다(11.62). 2026-11-16 07:19에 메일로 보냈다.
     - 재현 백서(16:9, 57장, 편집 가능 HTML, 영어 용어 + 쪽마다 각주, 11.63): `docs/E850-DVS-WHITEPAPER-20261116.html`. OS·kernel, 개발 환경, core별 역할·사용률·지정 코드, U-Net 학습 코드와 학습 자료 설계 근거, 재현 checklist. 원본과 PDF, 학습 코드 zip은 `~/e850-logs/dvs3d/whitepaper-20261116/`. 2026-11-16 08:45에 메일로 보냈다. **학습 코드(`rec.py`, `build.py`, `train.py`, `sweep.py`, `eval.py`, `stats.py`)는 아직 저장소 밖이다.** 저장소로 옮길지 사용자 확인 대기.
     - U-Net 강의 자료(16:9, 58장, 편집 가능 HTML, 11.66): `docs/UNET-LECTURE-20261118.html`. 유래, 문헌 비교와 IPA(UNeXt Table 1 값), 기초 개념, 구조, 층별 C01~C15, 학습을 다룬다. 층 수를 실제 모델로 세었다. Conv는 15층(사용자가 말한 28층은 어떤 방식으로도 나오지 않음), 모듈 43, 연산 55, 가중치 1,097,449. 원본·PDF는 `~/e850-logs/dvs3d/unet-lecture-20261118/`. 2026-11-18 07:01에 메일로 보냈다(첨부 2).
     - 2026-11-18 07:00 확인: 보드1·보드2는 Wi-Fi 응답, **보드3(.234)·보드4(.244)는 ping·ssh 무응답**. ARP에도 없다. Mac에 ACM·fastboot 장치가 없다. 보드3 UART 로그는 11-15 이후 새 줄이 없다(11.66).
     - 다음: 아날로그 바이어스(sudo), `/evframe`(sudo), 그림 종류 넓히기, 보드 이식.
     - **2026-11-16 08:31쯤부터 보드1(192.168.0.236)이 Wi-Fi에서 응답하지 않는다**(ping 실패, 08:27 측정 때는 정상). Mac에 USB·UART가 없어 원인을 보지 못했다. 보드2는 정상(11.63). **21:57쯤 사용자가 전원을 다시 넣어 보드1·보드4는 정상으로 올라왔다**(멈춘 원인 모름). 보드3(.234)은 네트워크에 없고 UART도 조용하다(11.63).

1. **DVS 사람 행동 분류: 나타남 / 움직임 / 넘어짐 (구현 중)**
   - 설계: `docs/superpowers/specs/2026-11-07-dvs-person-action-design.md`. 조사와 실측: 11.36.
   - 방법(A′): 60 ms 이벤트 수 프레임(2×120×160) → ImageNet 사전 학습 MobileNetV3-small → GRU, 16 Hz, CPU4~6, 상태가 바뀔 때 JSONL 한 줄. Sony·Intel 2025 넘어짐 연구를 따른다. 공개 데이터 PAF·DailyDVS-200으로 학습하고 우리 센서 녹화(동작당 5~10번)로 미세 조정한다.
   - 실측: 무작위 가중치 모델이 A55 한 코어에서 27.2 ms, 프레임 만들기 5.6~7.8 ms. 16 Hz면 코어 하나의 55~60%다.
   - 할 일:
     1. 구현 계획 `docs/superpowers/plans/2026-11-07-dvs-person-action.md`(과제 9개)를 `dvs-person-action` 브랜치에서 실행 중이다. 과제 1(데이터 읽기, 11.37)과 2~4(녹화·이벤트 읽기 C 함수, 프레임·판정, 보드 분류 스레드와 설치, 11.38)는 끝났다. 분류는 `/var/lib/dvsact/enable`이 있을 때만 돈다(지금 꺼짐, 모델 없음). 과제 5(페이지 "행동 녹화", `POST /rec`, 11.39)도 끝났다. 녹화는 `/var/tmp/dvsrec`에 쌓이니 묶음마다 Mac `~/e850-logs/dvsrec/`로 옮기고 지운다. 파일 전송은 USB NCM(192.168.7.1, 13 MB/s), root 설치는 ACM 콘솔로 한다.
     2. 공개 데이터는 사용자가 브라우저로 받는다. PAF는 Tongji SharePoint, DailyDVS-200은 Google Drive(`action_<번호>` 폴더)에 있다. 받을 곳은 `~/e850-act-data/paf`, `~/e850-act-data/dailydvs`다.
2. **상자 크기 측정 (사람 행동 분류와 별개 과제. 위·옆 DVS 두 대 방식, 별도 저장소 `e850-dvs-boxdim`, 11.58)**
   - 요구 사항, 높이 설계 구상, 1차 시험 결과는 11.35에 있다. 요구 사항은 1.5 m, 위에서 수직, ±1~2 cm, 파일 + HDMI + 웹, 시험 상자 19×15×5 cm다.
   - 1차 시험: 손에 든 상자를 사람 앞에서 옮겼다. 기존 `dvs_boxes()`가 상자와 손·팔을 한 덩어리로 묶었다. 상자를 옮기기 전에도 사람의 움직임을 상자로 기록했다.
   - 2026-11-15: 사용자 제안으로 방식을 정했다. 위 보드가 가로·세로·위치를, 옆 보드가 높이를 재고, 위 보드가 Wi-Fi로 옆 보드 기록을 가져와 합친다. 제안서와 기준 코드(Python 표준 라이브러리)는 비공개 저장소 <https://github.com/philipdekim-OnD01/e850-dvs-boxdim>에 있다. 다른 사람들도 개발할 수 있게 하려는 것이다.
   - 할 일(그 저장소 `docs/PROPOSAL.md` 9장):
     1. M1: 이 저장소 `dvsview.c` `box_end()`의 JSON에 `cx_px`, `cy_px`(표본 중심의 중앙값)를 추가한다. `synth()` 자체 시험에서도 확인한다. 그 전에는 `boxdim`이 실제 기록을 받지 못한다.
     2. M2: 한 상자 시험. 거리를 줄이고, 막대로 밀고, 옆 카메라 렌즈는 벨트 면 높이에 둔다. 건너편에는 가림판을 세운다. 보정은 제안서 7장대로 한다. 위 카메라 `mm_per_px`는 납작한 물체(A4)로 잰다.
     3. M3: `dvs_boxes()`를 여러 상자용으로 바꾼다. 지금은 화면에 상자 하나만 있다고 가정한다.
   - 미해결: 상자가 벨트 폭 방향으로 나란히 지나가는지(사용자 답 "모름"), 벨트 폭과 속도, 렌즈 화각(60° 가정), 보드2의 NTP.
   - 시험 띄우기와 되돌리기 명령은 11.35 "시험 방법"에 있다. `dvslive`를 멈추고 `systemd-run`으로 띄운다. 두 보드 모두 `--mm-per-px 1`(화소)로 띄운다.
3. **HDMI 색 유지 시험 (위험 낮음, 재부팅 없음)**
   - `restart e850-hdmi`와 fb0 열고 닫기는 11.15에서 확인했다. 나머지가 남았다.
   - 할 일: `fbhist.py`로 색을 측정하면서 다음 상황을 차례로 만든다.
     - `systemctl restart e850-hdmi`
     - HDMI 케이블 뽑았다 꽂기
     - 콘솔 blank 시간이 지난 뒤(DECON HIBER) 깨우기
     - `ov5647-cap.py --live`를 실행했다 끝내기
   - 판정: 매번 배경 0x000000, 글자 0xffffff인지 본다.
   - 2.2의 가설 1·2를 확인하는 실험이다.
4. **Wi-Fi 펌웨어 패닉 원인 (자동 복구는 11.31에서 켰다)**
   - **11.47(2026-11-13):** 저절로 나는 0x04 패닉 6번은 모두 펌웨어 `Data Abort`였다(vendor의 `mx140.sym`·`log-strings.bin`으로 R4 기록 [2]를 풀었다). abort 자리는 늘 힙 할당·수신 버퍼 풀 함수였다(`pfree…`, `pmalloc_debug_validate_free_list`, `dplane_dpif_resource_topup_all_rx_pools` 등). 펌웨어 힙 손상으로 본다(가설). 해석기는 `tools/host/wlbt-panic.py`다.
   - **끝남: 새 펌웨어 24시간 시험(2026-11-13 00:47 KST ~ 11-14 00:47 KST), 패닉 0번(11.48).**
     - [확인] 지금 부팅(06:13 KST부터 18.6시간): `mx_recovery_count` 0. 커널 로그에 `WLBT FW PANIC`이 없고, 감시가 읽은 454번이 모두 0이었다.
     - [확인, 근거 약함] 그 전 5.4시간(교체 뒤 44분 + 01:31 부팅): Mac 감시가 5분마다 0을 읽었다. 그 부팅의 커널 journal은 01:32에서 끊겨 있어(전원 차단) 로그로는 확인하지 못했다.
     - 판정: 정한 기준(값이 늘지 않음)을 넘었다. 옛 빈도(3.8시간에 1번)라면 24시간 동안 약 6번 났어야 하고, 0번일 확률은 약 0.2%다(지금 부팅 18.6시간만 쳐도 약 0.8%).
     - 남은 변수: 지금 부팅은 확인할 때마다 2.4 GHz(2437 MHz)였다. 앞의 5.4시간은 5 GHz(5745 MHz)였다. 옛 펌웨어 때는 두 대역을 오갔다(11.30). 대역의 영향은 따로 가르지 않았다.
     - `/vendor/etc/wifi`를 Linaro Android 13 BSP 판으로 바꿨다(`S612_E3830_R4-1_Y23W13A`, 2023-03-27, 드라이버와 같은 세대). 출처는 GitLab `Linaro/96boards/e850-96/platform/vendor/samsung_slsi` 커밋 11e9c19a다.
     - [확인] 강제 패닉 한 번으로 올렸고, 8초 뒤 다시 연결됐다. 처리량은 61.9 Mbit/s였다.
     - 절전은 기본값(on)이다. 절전 끄기 시험(`wifi-pskeep`)은 바꾸기 전에 멈췄다.
     - 판정: 이 동안 강제 패닉을 내지 않는다. 01:31 KST에 재부팅해서 `mx_recovery_count`는 0부터 센다(그 전 44분은 0번). 이 값이 늘지 않으면 효과가 있다고 본다(옛 빈도라면 0번일 확률 약 0.2%). 1번이라도 나면 `wlbt-panic.py`로 푼다. 기호는 `~/e850-logs/wifi/fw-a13/mx140/debug/common/`에 있다.
     - 또 재부팅하면 `journalctl -k -b -1`도 본다.
     - 06:13 KST에 전원을 껐다 켰다(사용자). 콜드 부팅에서도 새 펌웨어가 올라왔다. 지금까지 새 펌웨어로 모두 약 5.4시간 돌았고 패닉은 0번이다(아직 판정 전). HDMI 화면이 안 나왔다(사용자). 보드 쪽은 정상으로 보인다(e850-hdmi active, LT8912B 1920x1080@60 설정됨, fb0 unblank, 렌더러 57 fps). HPD `not connected`는 화면이 나올 때도 그렇게 읽히므로(2.2) 원인 근거가 아니다. 원인 모름(11.47).
     - [확인] 재부팅(01:31 KST)해 보니 부팅 때도 새 펌웨어가 올라와 다시 붙었다. 규제 DB를 읽게 됐다(`Regulatory Version: 2.7`, 예전에는 `Failed to read regdb`). `Type mismatch`는 그대로인데, HE·TWT·WPA3 MIB가 없다는 뜻이고 Android 기능 문자열에만 쓰여 해가 없다(11.47).
   - 11.31에서 한 것: 자동 복구 켬(강제 패닉 4번 모두 복구, 5.6~6.5초). 큰 송수신 약 1.1 GB, 재결합, roaming 11분 동안 재현 0번. 0x04는 펌웨어 내부 번호라 호스트 소스에 뜻이 없다.
   - 할 일:
     - 패닉이 났는지는 `mx_recovery_count`로 본다. 났으면 `journalctl -g 'WLBT FW PANIC|panic_record'`로 기록을 꺼낸다. 11.32부터 journal이 약 2주 남는다(추정).
     - 펌웨어 로그(DRAM `mxl` 링)를 패닉 때 꺼내는 방법을 찾는다(wlbtd 없음).
     - `e850-vbus` 경합(11.31)은 11.32에서 고쳤다. 다만 기다리는 경로가 실제로 돈 부팅(`after N ms`, N > 0)은 아직 없다. 부팅할 때마다 `dmesg | grep e850-vbus`를 본다.
     - vendor 버그 `vsnprintf` WARN(`scsc_wlan`)과 `mx_panic` 0666 권한(`scsc_mx`)은 11.33에서 고쳤다. 강제 패닉 9번 모두 복구, WARN 0줄.
   - (전 2번 "dpu 드라이버 근본 수정"은 11.15, 11.16에서 끝났다.)
5. **YOLO nano CPU 추론 벤치마크 (보드 변경은 패키지 설치뿐)**
   - 할 일: ncnn 또는 ONNX Runtime으로 YOLOv8n/YOLO11n(모델은 사용자 확인 필요)을 8코어에서 돌린다. 320과 640 입력에서 fps와 온도를 잰다.
   - 이 결과로 카메라 → 추론 → HDMI 파이프라인을 어떻게 짤지 정한다.
   - 참고: 11.36에서 ONNX Runtime 1.31.1을 보드에서 돌렸다(pip 없이 wheel을 풀어 씀). MobileNetV3-small 2×120×160 입력이 한 코어 27.2 ms, 세 코어 14.6 ms였다.

선택 실험: C2 idle에서 GICC_CTLR를 끄는 시험. 보드가 멈출 수 있어서 사용자가 있을 때만 한다.

6. **DVS D-PHY PLL 시험 (11.46~11.49, 24시간 통과, 기본 setfile을 00/8B/B0으로 바꿈)**
   - 계획, 판정 기준, 지금까지의 결과는 [DVS-PLL-TESTS.md](DVS-PLL-TESTS.md)에 있다. 배경은 main의 `DVS_PLL_MINSU_KARIM_REVIEW_2026-11-10.md`이다.
   - 0x75는 검증된 설정이 아니다. 2026-11-14에 E850 측정(A/B/A/B, 24시간)을 근거로 기본 setfile을 00/8B/B0으로 바꿨다(11.49). 0x75는 `pll=experimental_00_75_00`으로만 쓴다.
   - 할 일:
     1. (끝남, 11.46) 빌드·설치, PHY 설정 확인, 첫 A/B(각 10분). 버퍼 경계 누락의 원인은 디코더가 128번째 줄을 버린 것이었다(고침).
     2. (끝남, 2026-11-12 07:51~17:51 KST) 00/8B/B0, 수신 1600, 10시간: CRC 0. Wi-Fi 패닉 직후 이상 구간 382초를 빼면 짧은 버퍼·scan 누락·frame_end 누락 0이었다. 타임스탬프 오류는 22비트 기준값 한 바퀴(약 4208초마다) 8번 × 64뿐이었다. `pass: false`는 이 두 가지 때문이다.
     3. (보드에 넣음, 23:04 KST) 기준값 한 바퀴를 처리하는 `libdvsview.so`(c10b0382). 이전 판은 `/usr/local/lib/libdvsview.so.prev-wrap`(db798ea1)이다. [확인] **보드 live에서 한 바퀴를 넘겼다(2026-11-13).** 재부팅 뒤 dvslive가 01:31:58 KST부터 같은 프로세스로 78분 돌았다. 22비트 ms 기준값의 주기는 69.9분이라 이 구간에 한 바퀴가 적어도 한 번 있다. 02:50:17 KST 계수기는 버퍼 158241, ts_bad 4, ts_restarts 1, ts_fixed 0, ts_back 0이다. 모두 스트림 시작 때 값 그대로다. 옛 판이었다면 한 바퀴마다 ts_bad +64, ts_restarts +1이 늘었을 것이다.
     4. **(끝남) Wi-Fi 패닉 재현 시험.** 강제 Wi-Fi 패닉 6번(프로파일마다 3번) 가운데 2번이 패닉 2초 안에 이상 상태가 됐다. 00/8B/B0에서 1/3(2회째, 23:12:49부터 시험이 끝날 때까지 120초), setfile(0x75)에서 1/3(1회째, 23:08:23부터 끝날 때까지 120초)이다. setfile 2회째는 패닉 직후 10초 표본 하나에 짧은 버퍼 1, 오류 4, 재시작 1만 있었다(이상 상태 아님). 패닉 전에는 6번 모두 이상이 없었다. 6번 모두 CRC 0이었다. **그래서 Wi-Fi 패닉이 이상 상태를 일으킨다(재현됨, 6번 중 2번). 0x75에서도 난다. PLL 설정 탓이 아니다.** 앞의 "00/8B/B0 2/2, 0x75 0/3"은 우연으로 본다. 원 자료: 보드 `/var/tmp/plltest/8-wifi-*`, Mac `~/e850-logs/plltest/8-wifi-*`. `mx_recovery_count` 6 → 12. 시험 방법: 2026-11-12 23:04 KST 시작, 약 23:35 끝. root `systemd-run --unit=wifistuck sh /var/tmp/wifistuck.sh 3`. 00/8B/B0과 setfile을 번갈아 4분씩 3회 돌리고, 매번 140초째에 Wi-Fi 패닉을 낸다. 지금까지 패닉 5번 가운데 00/8B/B0에서는 2/2가 이상 상태가 됐고, 0x75에서는 0/3이었다(11.46).
     4a. (끝남, 2026-11-12 23:56 KST) live가 이상 상태를 알아채 스트림을 다시 켠다(`ov5647-cap.py --stuck 10`, 보드 설치). 강제 패닉 10번 중 2번이 이상 상태가 됐고, 둘 다 10~14초 만에 정상으로 돌아왔다. 첫 재시작의 종료는 SIGABRT였다(원인 모름, 11.46).
     5. (끝남) 이상 상태 자동 재시작(`ov5647-cap.py --stuck`)과 22비트 한 바퀴 수정을 보드에서 확인했다(위 0장, 2026-11-13).
     6. **(끝남, 2026-11-13 07:25~08:09 KST) A/B/A/B 비교:** 0x75(`experimental_00_75_00`)와 00/8B/B0을 번갈아 10분씩 2회씩 돌렸다(수신 1600, 128줄 디코딩, `tools/board/pll-abab.sh`).
        - **4회 모두 통과했다.** CRC 0, 짧은 버퍼·scan·frame_end 누락 0, 타임스탬프 오류 0, raw 표본 깨끗, readback 일치.
        - 차이는 버퍼 속도뿐이다(0x75 35.2/s, 00/8B/B0 37.8/s, 두 번 모두 같음). 센서 scan 주기는 둘 다 985 µs다.
        - 결과 디렉터리: `/var/tmp/plltest/10-abab-*`.
     7. **(끝남, 통과) 00/8B/B0 24시간 시험**(`/var/tmp/plltest/11-8bb0-24h`, 2026-11-13 08:10 ~ 11-14 08:10 KST, 11.48).
        - `pass: true`, 9개 기준 모두 통과. CRC 0, 재시작 0, 버퍼 3,272,178개(37.87/s, 모두 128줄).
        - 짧은 버퍼, scan 누락, frame_end 누락, 타임스탬프 오류, 모르는 워드가 모두 0이다. raw 표본도 깨끗했다.
        - 같은 시간 Wi-Fi 패닉 0번이라 이상 구간이 없었다(`stuckscan.py`는 쓸 일이 없었다).
     8. **사용자 결정(2026-11-13):** 24시간 시험으로 안정성이 확인되면 다음 두 가지를 승인했다.
        - (끝남, 11.49) 기본 setfile을 00/8B/B0으로 바꾼다. `is-cis-s5krc1s-setA.h`의 0x30A5~A7을 바꾸고 모듈을 다시 빌드해 보드에 넣었다(이전 모듈 `/root/is-cis-s5krc1s.ko.prev-11.49`). `plltest.py setfile` 10분 확인도 통과했다(readback `00 8b b0`).
        - (끝남, 2026-11-14) `dvs-pll-diag`를 main에 병합했다(PR #6, 병합 커밋 e514a95).

7. **현미경 모드 (사용자 승인한 계획, 11.64~11.65)**
   - 지금(2026-11-19, 11.69): 보드4는 부팅 때 `e850-scope.service`(`/opt/e850-scope`)가 뜬다. 끄기 `curl http://192.168.0.244:8090/quit`(다음 부팅까지). 고치기: 파일을 `/var/tmp/e850-scope`에 scp한 뒤 root로 `sh /var/tmp/e850-scope/install.sh`. 끄기 `curl http://192.168.0.244:8090/quit`. 고치기 `scp` 뒤 `/reload`. 보드4의 `/var/tmp/e850-scope`에는 11.67 판이 있다(옛 판 `*.bak-1116`).
   - 키트 두 개(11.67): Hi-846 8M(1632×1224, EEPROM에 새 펌웨어), Hi-5031Q 50M(2016×1536, EEPROM에 고해상도 펌웨어, EEPROM 부팅 확인 2026-11-23). 펌웨어와 기밀 자료는 비공개 `sol-cx3-uvc`에 있다.
   - 할 일:
     1. 실물에서 확인: 촬영 저장, 평탄화, 자동 노출, 레지스터 쓰기, 데이터 막대 PNG, `/quit` 뒤 gadget 복귀.
     2. 화소 크기 확인(Hi-846 화소 1.12 µm × 2 = 2.24 µm로 가정). 눈금자나 크기를 아는 시료로 잰다.
     3. 프레임 손실: dvslive를 끈 상태에서 다시 잰다. 지금은 같이 돌 때 누적 96.3%를 받는다. 웹 요청이 몰리면 더 잃는다(10초 7.0 중 6.2장).
     8. 확대·축소를 실제 터치 기기와 트랙패드로 확인한다.
     4. HDMI 출력: 현미경 모드에서는 dvslive 대신 fb0에 그린다.
     5. (일부 끝남, 11.69: 보드4는 항상 현미경도 띄운다) 부팅 모드 전환: `/etc/e850-mode`(dvs|scope)와 "다음 부팅 한 번만" 옵션, 부팅 유닛, `e850-hubpwr`를 `/lib/modules`에 설치, 파일은 root 소유 디렉터리로.
     6. 보드3에 설치(지금 네트워크에 없음).
     7. 816×612 캡처 하나가 모두 255였던 것, 1632 영상이 Mac보다 어두웠던 것(11.64).

## 4. 사용자 결정 대기

- Apache 포트: nginx를 끄고 Apache를 80번에 올릴지, Apache를 8080에서 따로 띄울지. **DVS 웹 서비스(`dvslive.service`, 11.29)가 부팅 때부터 8080을 쓴다(인증 없음).** Apache를 8080에 올리면 앱은 페이지 없이 live만 돈다. `dvslive.sh`의 `CTL_PORT`로 앱 포트를 바꿀 수 있다
- 사람 행동 분류(3장 1번): 공개 데이터(PAF, DailyDVS-200) 브라우저 내려받기, 그리고 보드 앞 동작 녹화 일정(배치: DVS를 옆에 고정, 전신과 매트리스가 화면에 들어오게)
- 상자 측정(11.58): 방식은 위·옆 DVS 두 대로 정했다. 남은 결정은 세 가지다. ① 상자가 벨트 폭 방향으로 나란히 지나가는지 ② `e850-dvs-boxdim`에 초대할 사람들의 GitHub 아이디 ③ M1(`dvsview.c`에 상자 중심 추가)을 누가 언제 할지. MySQL 저장은 계속 보류다
- DVS 3D(11.56): A55 연산량을 재려면 보드에 OpenCV가 있어야 한다. `python3-opencv`(Debian, 의존성이 크다)나 `opencv-python-headless` aarch64 wheel(크기 미확인, `/tmp`에 풀어 쓰면 rootfs를 쓰지 않는다) 중에서 고르고, 다운로드를 허락받아야 한다. rootfs는 보드2 198 MB가 남았다(11.53 무렵). 보드1은 2026-11-19에 54 GB로 늘려 49 GB가 남는다(11.67)
- DVS 3D: 보드 두 대에 `/evframe` 설치(root, 3장 0-1의 명령). 렌즈·조명(보드1 바깥 날개)은 나중에
- YOLO 모델 종류와 입력 크기
