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
| 지금 구워진 boot | `~/e850-logs/boot-dvs3/boot.img` = 저장소 빌드, CN4 카메라 DT를 S5KRC1S로, mode0 8192×127 + mode1 8192×32(11.18/11.26). boot-dvs2에 조용한 워치독, gadget을 기다리는 `e850-vbus`를 넣은 판(2026-11-07, 11.32). 그 전 판 `boot-dvs2`, `boot-dvs`. 알려진 정상 이미지는 `~/e850-logs/boot-nokvm/boot.img`(OV5647 DT, 1시간 부하 시험 통과) |
| 자동 적재 모듈 | `e850-cpuhp`(hotplug, `/etc/modules-load.d`), HDMI 콘솔 색은 dpu.ko 패치(`modules/e850-hdmi/apply.sh`, 11.16). `e850-fbpal`은 더 이상 올리지 않는다 |
| 수동 적재 | 카메라 `modprobe fimc-is && modprobe is-cis-ov5647`. `fimc_is`는 내리면 다시 올릴 수 없어 재부팅해야 한다 |
| CPU 배치 | 장치 IRQ는 모두 CPU0. DVS 디코더(캡처 주 스레드) CPU1, 렌더러 `dvs-render` CPU2, 배경 필터 `dvs-filter` CPU3(11.45), 행동 분류 `dvs-act` CPU4~6, 웹 `dvs-web` CPU7 전용. systemd가 띄우는 것은 모두 CPU0~6(`/etc/systemd/system.conf.d/e850-cpu7.conf`, 11.30) |

### 접속

| 경로 | 주소 / 장치 | 용도 |
|---|---|---|
| USB ACM 콘솔 | `/dev/cu.usbmodeme850_961`, root 자동 로그인 | `python3 tools/host/acm.py "<cmd>" <timeout>` |
| UART 디버그 | `/dev/cu.usbserial-AK0O198I`, 115200 | 커널 로그 전용. `tools/host/uartlog.py`가 `~/e850-logs/uart.log`에 계속 기록 |
| USB 네트워크(NCM) | 보드 192.168.7.1, Mac은 DHCP | `EmitRouter=no`라 Mac 인터넷은 Wi-Fi로 유지 |
| Wi-Fi | 192.168.0.236 | `ssh philip@192.168.0.236` 또는 `e850@`. 평소 작업은 이걸로 충분하다. 핸드폰 핫스팟 `An`이 켜져 있으면 그쪽에 먼저 붙는다(주소는 DHCP, 11.45). 그때는 `ssh -o HostKeyAlias=192.168.0.236 philip@<주소>` |
| HDMI | LT8912B 브리지, tty1 텍스트 콘솔(16x32 글꼴) | `e850-hdmi.service` |

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
- **USB 호스트:** 꺼져 있어서 키보드·마우스가 없다. 데스크톱 환경은 보류했다(GPU 사용자 공간 드라이버 없음).
- **포트:** nginx가 80번 포트를 쓴다. Apache를 올리기 전에 사용자와 포트를 정한다.
- **Wi-Fi:** 펌웨어가 패닉하면(`WLBT FW PANIC`) 드라이버가 펌웨어를 다시 올리고 약 6초 뒤 다시 연결한다(11.31, `e850-wifi.sh`의 `disable_recovery_handling=0`). 복구 횟수는 `/proc/driver/mxman_ctrl0/mx_recovery_count`로 본다. `mx_panic`은 읽기만 해도 펌웨어가 패닉하니 `cat`하지 않는다(11.33부터 root만 읽고 쓸 수 있다, 0600). 강제 패닉 반복 시험은 `tools/board/wifi-panicloop.sh N`(root, 보드 `/var/tmp/wifi-panicloop.sh`).
- **재부팅 뒤 ACM이 안 보일 때:** UDC가 `not attached`이고 UART에 `dwc3_otg_start_gadget does not have any gadget`이 있으면 `e850-vbus` 경합이다(11.31). 11.32부터 `e850-vbus`가 gadget을 기다린다(`dmesg`에 `VBUS on … after N ms`). 그래도 안 보이면 ssh에서 `sudo sh -c "rmmod e850_vbus; modprobe e850-vbus"`를 한다(비밀번호는 사용자에게 묻는다). 정상 상태에서는 ACM이 3초 안에 돌아왔다(11.32). 경합 상태에서는 시험하지 못했다. 안 되면 재부팅한다.
- **ACM 셸에서 USB를 끊는 명령:** ACM 셸은 `serial-getty@ttyGS0.service` cgroup 안에 있다. ACM이 끊기면 systemd가 `setsid`로 띄운 자식까지 죽인다(11.32). 그런 명령은 ssh에서 하거나 `systemd-run`으로 띄운다.
- **보드 `/tmp`:** 부팅 때 비워진다. 남길 파일은 `/var/tmp`에 둔다.
- **`pgrep -f 'ov5647-cap.py --live'`:** ssh로 보낸 명령 줄 자신도 잡는다. live PID는 `systemctl show -p MainPID --value dvslive`로 얻는다.

## 로그와 백업 위치 (Mac)

- `~/e850-logs/`: `uart.log`, 부트 이미지 디렉터리(`boot-nokvm`, `boot-mpidr`, `boot-c2t` 등), strip한 모듈, Android 10 DT(`a10dtb/`)
- `~/Desktop/E850_Android10_backup_20261031/`: Android 10 파티션 백업. `build-bootimg.sh`가 restore용으로 사용한다
- `~/claude-transcript-backup/`: Claude 대화 기록. 5분마다 rsync하며, Mac을 재부팅하면 다시 켜야 한다
