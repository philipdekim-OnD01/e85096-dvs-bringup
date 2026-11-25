# E850-96 bring-up: Claude Code 작업 안내

WinLink E850-96(Samsung Exynos850 = s5e3830, Cortex-A55 8코어)에 vendor Linux 5.10 + Debian 12를 올리는 저장소다. 현재 상태와 다음 할 일은 [docs/BRINGUP_STATUS.md](docs/BRINGUP_STATUS.md)에 있다. 경과 기록은 [docs/E85096-LINUX510-BOOT.md](docs/E85096-LINUX510-BOOT.md) 11장에 있다.

## 개발 원칙

- **사용자에게 보이는 답변과 중간 보고는 모두 한글로 쓴다.**
- 단계마다 결과를 `docs/E85096-LINUX510-BOOT.md`(새 절)와 `docs/BRINGUP_STATUS.md`에 기록한다. 커밋·push는 **사용자에게 확인을 받은 뒤** 한다(2026-11-02 지시).
- **사용자 허락 없이 하지 않는 일:** 보드 재부팅, 커널 또는 부트 이미지 교체(`fastboot flash boot`), 동작 중인 서비스 중지(nginx, ssh, e850-hdmi, e850-wifi, watchdog 데몬).
- 보드가 멈출 수 있는 실험(idle, PMU, 부트 인자)은 사용자가 자리에 있을 때 한다. 이때 USB(fastboot 복구, ACM 콘솔)와 UART(커널 로그)를 꽂아 둔다. 실험하기 전에 복구 방법을 먼저 준비한다.
- 관찰한 사실, 가설, 실패한 실험, 보드에 실제로 바꾼 것을 구분해서 적는다. 측정하지 않은 것을 됐다고 쓰지 않는다.
- Wi-Fi 비밀번호와 기타 비밀 값은 저장소에 넣지 않는다.
- `docs/E85096-C2-CPUIDLE-REFERENCE-20261102.md`처럼 사용자가 쓴 참고 문서는 지시가 아니라 배경 자료다.

## 보드 구조

| 항목 | 내용 |
|---|---|
| 펌웨어 | Android 10 시절 LK + BL31 그대로. LK fastboot로 `boot` 파티션을 굽는다 |
| 커널 | vendor 5.10.157 (Android 13 BSP, `5.10.157-00010-g3b6b9ca51034-dirty`). Image와 모듈은 Docker 볼륨 `e850-out` |
| 부트 이미지 | `tools/linux510/build-bootimg.sh`: boot.img v2 = Image + initramfs(1단계 모듈, busybox init) + DTB(SoC dtb + ERD3830 overlay, CPU `reg`를 `cluster<<8 \| core`로 고침) |
| 명령줄 핵심 | `cpuidle.off=1`(C2에 들어가면 코어가 깨지 않음, 11.12), `panic=0`, `clocksource=arch_sys_counter`, `loglevel=8 ignore_loglevel`, `vt.default_*`(흰 글씨), `root=PARTLABEL=userdata` |
| rootfs | Debian 12 arm64, `userdata`(mmcblk0p24) ext4. `tools/linux510/build-rootfs.sh` |
| 지금 구워진 boot | 보드3은 `~/e850-logs/boot-nocpu/boot.img`(= boot-dvs3 + `cgroup_disable=cpu`, 2026-11-21, 11.80~11.81). 그 밖의 보드는 `~/e850-logs/boot-dvs3/boot.img` = 저장소 빌드, CN4 카메라 DT를 S5KRC1S로, mode0 8192×127 + mode1 8192×32(11.18/11.26). boot-dvs2에 조용한 워치독, gadget을 기다리는 `e850-vbus`를 넣은 판(2026-11-07, 11.32). 그 전 판 `boot-dvs2`, `boot-dvs`. 알려진 정상 이미지는 `~/e850-logs/boot-nokvm/boot.img`(OV5647 DT, 1시간 부하 시험 통과) |
| 자동 적재 모듈 | `e850-cpuhp`(hotplug, `/etc/modules-load.d`), HDMI 콘솔 색은 dpu.ko 패치(`modules/e850-hdmi/apply.sh`, 11.16). `e850-fbpal`은 더 이상 올리지 않는다 |
| 수동 적재 | 카메라 `modprobe fimc-is && modprobe is-cis-ov5647`. `fimc_is`는 내리면 다시 올릴 수 없어 재부팅해야 한다 |
| GPU (11.82) | vendor `mali_kbase` + Mesa panfrost(`tools/gpu/`, Icecream95 `kbase-g52` + 패치, drm-shim을 LD_PRELOAD). 보드3 `~/gpu/`에서 일반 사용자로 화면 없이 GLES 3.1이 된다. AFBC와 IDVS는 이 GPU에서 결과가 틀려 꺼 두었다(IDVS는 아래 LS 캐시 문제와 같은 원인, 11.88). 큰 이미지 BO는 CPU 캐시로 잡고 shim이 clean/invalidate한다(`PAN_KBASE_UNCACHED=1`로 끔). `gles-x11`은 결과를 읽어 X 창에 보인다(720p 42~54 fps). `gles-fb`는 fb0 메모리에 바로 그린다(`modules/e850-fbbuf`를 root로 `load.sh`, 부팅 때는 안 올림, 11.84). 사용자 공간 `S3CFB_WIN_CONFIG`는 fb0 메모리를 풀어 버리니 쓰지 않는다. KMS: `modules/e850-kms`(root로 `load.sh`, 부팅 때는 안 올림)가 `/dev/dri/card0`(이름 `exynos`)를 만든다. fbdev pan으로 DRM 버퍼를 보이고, 전환은 30 fps가 상한이다(11.85). GPU X는 vt8의 두 번째 X다(`sudo sh /var/tmp/e850-kms/x-kms.sh`, modesetting + glamor, 클라이언트는 `kenv`, 11.86). Firefox는 거기서 하드웨어 WebRender로 돈다. Chrome(사용자가 설치)은 래퍼 `/usr/local/bin/google-chrome`(desktop 기능)이 :1에서 `--use-gl=angle --use-angle=gl-egl`로 띄워 GPU 합성·래스터·WebGL을 쓴다(GPU 샌드박스 켬). 11.102 전 shim(`mmap`이 `init_shim`을 부름)으로는 Chrome이 시작하다 돈다(11.102). shim 제출은 비동기다: atom마다 앞 atom에 ORDER 의존, syncobj·fence는 제출 순번, 공유 BO를 쓴 배치는 Mesa `panfrost_flush`에서 기다린다(`PAN_KBASE_SYNC=1`이면 동기, 11.88). YouTube 720p60 약 58~60 fps로 소프트웨어와 같다. 꼭짓점 셰이더가 쓴 위치가 셰이더 코어 LS 캐시에 남아 타일러가 옛 값을 읽던 것(글자·타일 깨짐)은 Mesa가 꼭짓점 job과 타일러 job 사이에 LS clean cache flush job을 넣어 고쳤다(`PAN_E850_FLUSH=0`이면 끔, 11.88). 그리기 검사는 보드 `~/gpu/run-test.sh`(gles-test, gles-subtex, gles-inst). GPU DVFS는 kbase가 사용률을 모으지 않아 377 MHz에 머문다. shim은 Mesa가 CPU 쓰기를 알린 리소스 BO만 clean한다(`PAN_BO_CPU_TRACKED`). `PAN_KBASE_STATS=1`이면 5초마다 제출·대기·clean 통계를 찍는다. X :1 화면은 `DISPLAY=:1 ~/.local/bin/xshot`로 뜬다(fb0 mmap에는 KMS 버퍼가 안 보임) |
| 골라서 넣는 기능 | `features/`(README): 보드에서 `e850-feature install ethernet audio video bt usbhost desktop`. desktop은 바탕화면 아이콘(pcmanfm, `e850-open dvs|scope`; DVS 아이콘은 polkit이 허락한 `e850-dvs-desk.service`, libfm `quick_exec=1`)과 `net-tools`도 넣는다(11.107). Mac에서 `sh features/host/bundle.sh` → `sh features/host/deploy.sh philip@<IP> 기능…`. 모듈은 `/lib/modules/$(uname -r)/extra/`(depmod `search extra built-in`), 펌웨어는 Linaro 고정 커밋에서 받아 git에 넣지 않음(11.76~11.79) |
| CPU 배치 | 장치 IRQ는 Wi-Fi(`scsc_wlbt`, CPU7, `e850-wifi.sh`, 11.69)를 빼고 모두 CPU0. DVS 디코더(캡처 주 스레드) CPU1, 렌더러 `dvs-render` CPU2, 배경 필터 `dvs-filter` CPU3(11.45), 행동 분류 CPU4~6(C 묶기 `dvs-bin` + 추론 `dvs-act`, 11.103), 웹 `dvs-web` CPU7 전용. GPU 데스크톱의 `dvs` 창은 `dvsgl`이 GPU로 그린다(11.102): 그동안 CPU 필터는 쉬고 CPU는 웹용으로 8장에 1장만 그린다. 링은 16M 이벤트(128 MB)이고 `DVS_GL=0`이면 예전 XPutImage 창이다. systemd가 띄우는 것은 모두 CPU0~6(`/etc/systemd/system.conf.d/e850-cpu7.conf`, 11.30) |

### 접속

| 경로 | 주소 / 장치 | 용도 |
|---|---|---|
| USB ACM 콘솔 | `/dev/cu.usbmodeme850_961`, root 자동 로그인 | `python3 tools/host/acm.py "<cmd>" <timeout>` |
| UART 디버그 | `/dev/cu.usbserial-AK0O198I`, 115200 | 커널 로그 전용. `tools/host/uartlog.py`가 `~/e850-logs/uart.log`에 계속 기록 |
| USB 네트워크(ECM) | 보드 192.168.7.1, Mac은 DHCP | `EmitRouter=no`라 Mac 인터넷은 Wi-Fi로 유지. 보드→Mac 방향이 멈출 때가 있다(11.52): UDC `soft_connect`를 끊었다 붙이면 복구된다 |
| Wi-Fi | 192.168.0.236 | `ssh philip@192.168.0.236` 또는 `e850@`. 평소 작업은 이걸로 충분하다. 핸드폰 핫스팟 `An`이 켜져 있으면 그쪽에 먼저 붙는다(주소는 DHCP, 11.45). 그때는 `ssh -o HostKeyAlias=192.168.0.236 philip@<주소>` |
| 보드2 (2026-11-14, 11.53) | Wi-Fi 192.168.0.168 (DHCP), UART `/dev/cu.usbserial-A5069RR4`, fastboot `000009f72c9c23e0` | 보드1의 복제본이다(machine-id만 다름, 같은 ssh 호스트 키라 `ssh -o HostKeyAlias=192.168.0.236 philip@192.168.0.168`). 두 보드 모두 USB 주소 192.168.7.1과 ACM 이름 `e850_96`를 쓴다. 그래서 Mac에는 한 번에 한 보드만 USB로 꽂는다. 지금 ACM에 붙은 보드는 `cat /etc/machine-id`로 구별한다(보드1 `4aeede4c…`, 보드2 `6f31c0bd…`) |
| 보드3 (2026-11-15, 11.57) | Wi-Fi 192.168.0.234 (DHCP), fastboot `00000a749c9c23e0`, machine-id `16314a53…`. UART는 보드2에 쓰던 `A5069RR4`를 옮겨 꽂았다 | 보드1의 복제본(11-14 rootfs). **DVS 센서 없음.** 2026-11-23(11.90)부터 데스크톱 보드: 부팅하면 HDMI tty1에 root 프롬프트, USB 호스트가 켜짐(ACM 없음, ssh로만 관리), dvslive 꺼짐. `e850-x`가 GPU 데스크톱(:1)을 띄운다(GPU 1001 MHz·MIF·INT 최대 고정, `e850-x stop`이 풂, Ctrl+Alt+F1). zram 1.8 GB. philip은 netdev·bluetooth 그룹이고 wpa_supplicant 제어 소켓이 netdev에 열려 있다(wpa_gui). 접속 `ssh -o HostKeyAlias=192.168.0.236 philip@192.168.0.234` |
| 보드4 (2026-11-15, 11.57) | Wi-Fi 192.168.0.244 (DHCP), fastboot `00000bf40c9c23e0`, machine-id `606a4996…`(2026-11-24부터), UART 출력 없음 | **2026-11-24 16시부터 보드6 전체 복제본(11.102).** 이미지는 Mac `~/e850-logs/clone-20261124-b6/`(BOARD-CLONE.md 12장, boot-nocpu). 보드6과 같다: 데스크톱 + 현미경 뷰어(`e850-scope`가 부팅 때 USB 호스트라 ACM 없음, `e850-usbhost` disabled) + `bt`·`dvs` 기능 + `e850-wifiwatch` + Chrome GPU + DVS GPU 뷰어. 지금 CN4에 센서 없음. BT 기기는 다시 페어링해야 한다. 굽고 난 첫 부팅은 MFC 경합으로 패닉했다(11.102). 11.101 판의 자료는 Mac `~/e850-logs/board4-backup-20261124/`. 접속 `ssh -o HostKeyAlias=192.168.0.236 philip@192.168.0.244` |
| 보드5 (2026-11-23, 11.92) | Wi-Fi 192.168.0.206 (DHCP), fastboot `00000ae74e9c23e0`, machine-id `6f0d9b38…`, UART 없음 | 보드3 데스크톱 이미지(`clone-20261123`, boot-nocpu)를 구웠다. 보드3처럼 HDMI tty1 root 프롬프트, 부팅 때 USB 호스트(ACM 없음), dvslive 꺼짐, `e850-x`. BT 스피커는 아직 페어링 안 함. 접속 `ssh -o HostKeyAlias=192.168.0.236 philip@192.168.0.206` |
| 보드6 (2026-11-23, 11.93) | Wi-Fi 192.168.0.208 (DHCP), fastboot `00000bf54c9c23e0`, machine-id `f580f9d9…` | 2026-11-24 쇼트로 멈췄다가(11.101) 같은 날 14시에 다시 살아났다(11.102). **DVS 센서 있음.** DVS 데스크톱 + 현미경 뷰어 + Chrome GPU(`google-chrome` 래퍼) + DVS GPU 뷰어(`dvsgl`). 전체 이미지 `~/e850-logs/clone-20261124-b6/`. 접속 `ssh -o HostKeyAlias=192.168.0.236 philip@192.168.0.208` |
| HDMI | LT8912B 브리지, tty1 텍스트 콘솔(16x32 글꼴). 신호는 1080p60 하나(EDID 없음). GPU X(:1)의 데스크톱 크기는 `e850-res`(desktop 기능, xrandr `--scale-from`)로 1280x720~2560x1440 중에 고른다(11.103) | `e850-hdmi.service` |

## 빌드

모든 빌드는 Mac의 Docker에서 한다. Docker Desktop이 꺼져 있으면 `open -a Docker`로 켠다. 사용하는 볼륨과 디렉터리는 다음과 같다.
- 볼륨 `e850-kernel`: 커널 소스
- 볼륨 `e850-out`: 빌드 결과
- 이미지 `e850-build-env:1`
- `~/e850-a13-build`: `/work`로 마운트

```sh
# 외부 모듈(예: e850-cpuhp, e850-fbpal, ov5647-pablo, cpu-pmu-enable): 각 디렉터리의 build-module.sh
docker run --rm -v ~/e850-a13-build:/work -v e850-kernel:/work/kernel -v e850-out:/work/out \
    -v "$PWD/modules/e850-cpuhp:/m:ro" e850-build-env:1 bash /m/build-module.sh
# 결과: e850-out:/ext-<이름>/<이름>.ko. 보드에 보낼 때는 strip:
docker run --rm -v ~/e850-a13-build:/work -v e850-out:/work/out -v ~/e850-logs:/x e850-build-env:1 \
    bash -c '/work/prebuilts/clang/host/linux-x86/clang-r450784e/bin/llvm-strip --strip-debug -o /x/e850-cpuhp.ko /work/out/ext-e850-cpuhp/e850-cpuhp.ko'

# 부트 이미지 (출력: <out>/boot.img, cmdline.txt, merged.dtb)
docker run --rm -v e850-out:/o:ro -v "$PWD/tools/linux510:/t:ro" -v <out>:/out \
    -v ~/Desktop/E850_Android10_backup_20261031:/b:ro ubuntu:22.04 sh /t/build-bootimg.sh root=PARTLABEL=userdata

# rootfs (출력: out/rootfs.simg → fastboot flash userdata). Wi-Fi 펌웨어(Linaro A13 판, 11.47)를 먼저 받아 둔다:
python3 tools/linux510/wifi-fw-linaro.py out/wifi-a13.tgz
docker run --rm --privileged -v e850-out:/o:ro -v "$PWD/tools/linux510:/t:ro" -v "$PWD/out:/out" \
    ubuntu:22.04 sh /t/build-rootfs.sh
```

## 배포

- **모듈과 작은 파일:** ACM 콘솔로 base64를 보낸다. Mac에서 HTTP 서버를 띄우는 방식은 권한 검사에서 막혔다.
  ```sh
  b=$(gzip -9c X.ko | base64 | tr -d '\n')
  python3 tools/host/acm.py "echo $b | base64 -d | gunzip > /lib/modules/\$(uname -r)/X.ko && depmod" 30
  ```
  큰 파일은 `tools/linux510/acmpush.py`를 쓴다.
- **userdata(768 MB 넘는 sparse) 굽기:** `fastboot flash userdata`는 쓰지 않는다. 먼저 `getvar:is-userspace`를 보내는데, LK가 거기서 멈춘다(11.53). `python3 tools/linux510/resparse.py --split-mib 700 rootfs.simg part.simg` 다음 `fbmini flash userdata part.simg.N`을 차례로 한다. `fbmini`는 `tools/linux510/fbmini.c`이고 빌드법은 7.7절에 있다. 보드를 통째로 복제하는 절차는 [docs/BOARD-CLONE.md](docs/BOARD-CLONE.md)에 있다. 데스크톱 보드(보드3, GPU X·한글·BT 스피커) 이미지는 `~/e850-logs/clone-20261123/`이고 boot는 반드시 `boot-nocpu`다. 대상마다 `build-clone.sh`를 다시 돌린다(machine-id, BOARD-CLONE.md 11장).
- **부트 이미지 굽기(허락받은 뒤):**
  1. 보드에서 `(setsid nohup /usr/local/sbin/to-fastboot >/dev/null 2>&1 </dev/null &)`를 실행한다. 워치독 데몬을 죽이면 약 20초 뒤 리셋되고, LK가 `Abnormal RST_STAT`로 보고 fastboot에 들어간다.
  2. `fastboot devices`로 보드가 보이는지 확인한다.
  3. `fastboot flash boot <img>`를 한다.
  4. `fastboot reboot`를 한다. `usb_read failed`가 떠도 재부팅은 된다.
- **재부팅:** `systemctl reboot`를 하면 바로 부팅하기도 하고 fastboot에 들어가기도 한다. fastboot가 보이면 `fastboot reboot`를 한다.
- **복구:** 보드가 멈추면 워치독이 리셋하고 fastboot로 들어간다. 그때 알려진 정상 이미지 `~/e850-logs/boot-nokvm/boot.img`를 다시 굽는다.
  - 워치독 데몬(initramfs가 켬)이 살아 있는 CPU에서 계속 신호를 보내면 리셋이 몇 분 늦거나 안 될 수 있다.
  - 워치독이 켜지기 전(부팅 약 2초 이내)에 멈추면 전원을 껐다 켜야 한다.

## 자주 걸리는 함정

- **`acm.py`:** 명령 끝에 `&`를 붙이면 `; echo __END__`와 붙어 문법 오류가 난다. 출력에는 명령 줄과 `\e[?2004` 제어 문자가 섞인다. 그래서 `grep '^running'` 같은 판정이 실패하기 쉽다. 결과는 `tail`로 보거나 `cat -v`로 확인한다.
- **macOS 셸:** `timeout`이 없다. BSD grep은 기본 정규식에서 `\|`를 지원하지 않으니 `-E`를 쓴다. `uart.log`를 awk나 sed로 처리할 때는 `LC_ALL=C`를 붙인다.
- **hotplug:** `echo 0 > cpuN/online`은 `e850-cpuhp.ko`가 올라가 있을 때만 한다. 없으면 그 코어는 재부팅할 때까지 돌아오지 않는다.
- **카메라:** `ov5647-cap.py`는 실행하는 동안 콘솔 printk를 끈다. 프레임마다 나오는 커널 메시지가 UART로 나가면 CSIS 인터럽트를 놓치기 때문이다.
- **기능 묶음:** `exynos3830_sound`가 카드 등록에 실패한 같은 부팅에서 다시 올리면 커널 패닉(11.77). `scsc_wlan`을 rmmod 뒤 다시 올려도 패닉(11.78): 바꾼 모듈은 재부팅해야 쓰인다. 오디오는 dmix가 안 된다(SysV IPC 없음), 48 kHz로만 제 속도. BT를 켠 채 쓰려면 고친 `scsc_wlan`(bt 기능이 넣음)이 올라와 있어야 한다. 아니면 WLBT 패닉 뒤 Wi-Fi가 재부팅 전까지 안 돌아온다.
- **`libpam-systemd`를 깔면 커널 패닉(11.80):**
  - 로그인마다 `user@UID.service`가 cpu 컨트롤러를 위임받는다. 그러면 cpu cgroup이 9개를 넘고, vendor `ems.ko`가 9칸 배열 밖을 써서 패닉한다(UBSAN `BRK 0x5512`, `freqboost_can_attach`).
  - 기본 rootfs에는 이 패키지가 없다. desktop 패키지(`dbus-user-session`)가 들인다.
  - 막는 법: 부트 명령줄의 `cgroup_disable=cpu`(`build-bootimg.sh`, `~/e850-logs/boot-nocpu`), 또는 `/etc/systemd/system/user@.service.d/`에 `Delegate=pids memory`(desktop 기능이 넣음).
  - 패키지를 apt로 들이기 전에 `apt-get -s install …`로 `libpam-systemd`가 끼는지 본다.
- **MFC(`exynos_mfc`, 11.81):**
  - 내리지 않는다. 열려 있는 동안 `rmmod`하면 패닉한다. 내린 뒤 다시 올리면 코어가 붙지 않는다.
  - vendor 판은 udev `v4l_id`와의 부팅 경합으로 패닉한다. `features/video`가 고친 판을 넣는다.
  - 고친 판에도 두 번째 경합이 남아 있다: `mfc_core_probe`가 끝나기 전에 `v4l_id`가 열면 `mfc_alloc_firmware` → `iommu_map_sg`에서 NULL 패닉(23.8 s, 보드4, 11.102). 아직 고치지 않았다. 부팅 중 가끔 멈추면 이것부터 의심한다.
  - ffmpeg `*_v4l2m2m`은 MMAP 버퍼만 쓰는데, 이 드라이버는 USERPTR/DMABUF만 받는다. 그래서 디코딩이 되지 않는다.
- **usbhost를 켜면 ACM root 셸이 끊긴다.** ssh의 philip은 sudo 비밀번호가 필요하고, UART는 입력을 받지 않는다(sysrq도 안 됨, 11.81). root가 다시 필요할 일은 usbhost를 켜기 전에 끝낸다.
- **`fastboot boot`(RAM 부팅):** 이 LK에서는 `fb: boot`를 찍고 멈춘다(7.3, 11.80). 전원을 다시 넣어야 하니 쓰지 않는다.
- **USB 호스트:** 평소에는 꺼져 있어서 키보드·마우스가 없다. 허브 전원(LDO24)을 `e850-hubpwr`로 켜고 OTG `id`를 0으로 바꾸면 USB-A가 동작한다. 이때 micro-USB 케이블은 빼야 한다(11.64). 데스크톱(X)은 CPU로 그린다. GPU 결과는 읽어서 X 창에 보내는 방식으로만 보인다(`gles-x11`, 11.82).
- **포트:** nginx가 80번 포트를 쓴다. Apache를 올리기 전에 사용자와 포트를 정한다.
- **Wi-Fi:** 펌웨어가 패닉하면(`WLBT FW PANIC`) 드라이버가 펌웨어를 다시 올리고 약 6초 뒤 다시 연결한다(11.31, `e850-wifi.sh`의 `disable_recovery_handling=0`). 복구 횟수는 `/proc/driver/mxman_ctrl0/mx_recovery_count`로 본다. `mx_panic`은 읽기만 해도 펌웨어가 패닉하니 `cat`하지 않는다(11.33부터 root만 읽고 쓸 수 있다, 0600). 강제 패닉 반복 시험은 `tools/board/wifi-panicloop.sh N`(root, 보드 `/var/tmp/wifi-panicloop.sh`). 2026-11-13부터 펌웨어는 Linaro Android 13 판(`R4-1_Y23W13A`, 11.47)이다. 옛 2019년 판은 3.8시간마다 Data Abort가 났다. 되돌리기는 root로 `sh /var/tmp/wifi-fwswap.sh a10`. **`wpa_cli reassociate`는 쓰지 않는다:** 연결된 채 같은 AP로 다시 붙으면(`vifStatus=2`) 드라이버 송신 큐가 막혀 DHCP를 못 받는다(11.100). 다시 붙일 때는 `disconnect` 다음 `reconnect`. 주소가 3분 없으면 `e850-wifiwatch`가 이렇게 한다(보드6).
- **Android 10 보드를 fastboot에 넣기:** Mac의 Homebrew adb 37.0.1은 서버가 시작하다 죽는다. VOL_UP 키도 LK가 읽지 못한다. 대신 UART(Android `console` 셸)에 `reboot bootloader`를 보낸다(11.53). Linux에서는 `rebootarg bootloader`가 통하지 않으니 `to-fastboot`을 쓴다.
- **MIPI CRC가 모든 줄에 날 때:** 먼저 CN4 카메라 케이블을 다시 꽂는다. 보드2는 그것으로 CRC 0이 됐다(11.53). CRC가 계속 나는 상태로 두면 dvslive가 재시작을 되풀이하다 정지 경로에서 보드가 멈춘다.
- **재부팅 뒤 ACM이 안 보일 때:** UDC가 `not attached`이고 UART에 `dwc3_otg_start_gadget does not have any gadget`이 있으면 `e850-vbus` 경합이다(11.31). 11.32부터 `e850-vbus`가 gadget을 기다린다(`dmesg`에 `VBUS on … after N ms`). 그래도 안 보이면 ssh에서 `sudo sh -c "rmmod e850_vbus; modprobe e850-vbus"`를 한다(비밀번호는 사용자에게 묻는다). 정상 상태에서는 ACM이 3초 안에 돌아왔다(11.32). 경합 상태에서는 시험하지 못했다. 안 되면 재부팅한다.
- **ACM 셸에서 USB를 끊는 명령:** ACM 셸은 `serial-getty@ttyGS0.service` cgroup 안에 있다. ACM이 끊기면 systemd가 `setsid`로 띄운 자식까지 죽인다(11.32). 그런 명령은 ssh에서 하거나 `systemd-run`으로 띄운다.
- **보드 `/tmp`:** 부팅 때 비워진다. 남길 파일은 `/var/tmp`에 둔다.
- **YouTube 자동 시험:** Marionette로 띄운 Firefox에서는 재생 약 50초에 YouTube가 "Something went wrong"으로 재생을 끊는다. GPU·소프트웨어 경로 모두 같다(11.88). 측정은 30초 이내로 하고, 긴 시험은 로컬 클립(보드 `/var/tmp/loop.html`)으로 한다.
- **`pkill -f`/`pgrep -f`를 ssh로 보낼 때:** 패턴과 같은 글자가 그 ssh 명령 줄 어디에 있어도 ssh 자신이 잡힌다(`x-kms.sh` 안의 `pkill -f "Xorg :1 vt8"` 포함, 11.88). pkill은 따로 ssh 한 번으로 보낸다.
- **Firefox를 :0과 :1에 함께 띄울 때:** 같은 프로필로 두 번째 Firefox를 띄우면 떠 있는 Firefox에 창을 넘기고 바로 끝난다. 창은 다른 화면에 생긴다(11.89). 그래서 `/usr/local/bin/firefox`(desktop 기능)가 :1에서 `~/.config/mozilla/firefox/gpu` 프로필을 쓴다. 메뉴, 패널, 터미널의 `firefox`가 모두 이것이다. `firefox-esr`를 직접 치면 이 우회가 안 된다.
- **ALSA 출력 바꾸기(`~/.asoundrc`):** 이미 떠 있는 프로그램은 설정을 다시 읽지 않는다. 다시 띄워야 한다(11.89). BT 스피커는 bt 기능의 bluez-alsa로 쓴다(README).
- **DVS 시각(11.99):** 링의 이벤트 시각은 32비트 표시 시계다. 64비트로 되돌릴 때 기준은 `S.newest`(표시 시계)를 쓴다. `now_us()`(단조 시계)와는 2^31 µs 넘게 다를 수 있다.
- **`pgrep -f 'ov5647-cap.py --live'`:** ssh로 보낸 명령 줄 자신도 잡는다. live PID는 `systemctl show -p MainPID --value dvslive`로 얻는다.

## 로그와 백업 위치 (Mac)

- `~/e850-logs/`: `uart.log`, 부트 이미지 디렉터리(`boot-nokvm`, `boot-mpidr`, `boot-c2t` 등), strip한 모듈, Android 10 DT(`a10dtb/`)
- `~/Desktop/E850_Android10_backup_20261031/`: Android 10 파티션 백업. `build-bootimg.sh`가 restore용으로 사용한다
- `~/claude-transcript-backup/`: Claude 대화 기록. 5분마다 rsync하며, Mac을 재부팅하면 다시 켜야 한다
