# features: 보드에 골라서 넣는 드라이버와 프로그램

E850-96 보드(보드1~4 어느 것이든)에 기본 이미지에 없는 기능을 골라서 넣는 묶음이다. 기능마다 디렉터리가 하나씩 있고, 각 디렉터리의 `feature.sh`가 그 기능의 `install`, `remove`, `status`를 맡는다. 보드에서는 `e850-feature` 명령 하나로 모든 기능을 다룬다. 경과 기록은 `docs/E85096-LINUX510-BOOT.md` 11.76~11.80절에 있다.

| 기능 | 하는 일 | 의존 | 부팅 때 |
|---|---|---|---|
| `usbhost` | USB-A 호스트 모드(키보드, 마우스, LAN9514). `e850-usbhost on\|off`. 부팅 때 켜도 기다리지 않게 `systemd-networkd-wait-online`을 `--any`로 바꾼다(11.92) | | 켜지 않음. 켜면 micro-USB ACM/ECM이 없어진다 |
| `ethernet` | LAN9514 유선랜(`smsc95xx.ko`), 이름 `eth0`, MAC는 machine-id로 고정, DHCP | usbhost | USB 호스트가 켜지면 자동 |
| `audio` | ABOX DSP + AUD3004X 코덱 사운드 카드, 3.5 mm 헤드폰 출력, ALSA 기본 장치(plug → 48 kHz, 한 번에 한 프로그램) | | `e850-audio.service` |
| `video` | MFC 하드웨어 코덱(V4L2 `/dev/video6` 디코더, `/dev/video7` 인코더). 부팅 경합을 고친 `exynos_mfc`(다음 부팅부터) | | `e850-mfc.service` |
| `bt` | Bluetooth: `scsc_bt` → `e850-btbridge.py` → `/dev/vhci` → BlueZ `hci0`. BT 스피커 출력(bluez-alsa, A2DP, SBC XQ, ALSA 변환기 speex: 11.98) | Wi-Fi(e850-wifi) | `e850-bt.service`, `bluealsa.service`(`bluealsa-aplay`는 끔, 11.92) |
| `desktop` | X(fbdev) + Openbox + 패널(lxpanel: 메뉴, 작업 표시줄, 시계) + 파일 관리자(pcmanfm) + 터미널 + Firefox + mpv + 한글 입력(fcitx5-hangul), HDMI tty7. `e850-desktop start\|stop` | usbhost | 켜지 않음(무겁다) |
| `dvs` | DVS(CN4의 S5KRC1S): 모듈(`fimc-is`, `is-cis-s5krc1s`, `csis-irqstat`), 디코더 `libdvsview.so`, X 터미널에서 `dvs` → "DVS" 창에 이벤트 영상 + 설정 페이지 :8080. 센서가 없으면 데모 녹화를 되풀이한다(11.97). 옆에 C 설정 패널 `dvspanel`(웹 설정창과 같은 내용, 11.99), 데스크톱이 뜰 때 센서가 있으면 저절로 뜬다(11.98), 얼굴 가로 길이 표시(YuNet, 11.99) | X(desktop) | `dvslive.service`는 설치만(HDMI로 보려면 `systemctl enable --now dvslive`) |

`desktop`만 빼고 설치하면 X 없이 가벼운 보드가 된다. X가 필요할 때만 `desktop`을 더 설치한다.

## 쓰는 법

Mac(저장소 루트)에서:

```sh
sh features/host/bundle.sh                       # out/e850-features.tgz (Docker로 모듈 빌드, 펌웨어 내려받기)
sh features/host/deploy.sh philip@192.168.0.244 ethernet audio video bt      # X 없이
sh features/host/deploy.sh philip@192.168.0.244 desktop                      # X도
```

보드에서:

```sh
e850-feature list            # 번들에 든 기능
e850-feature status          # 설치 여부와 상태
sudo e850-feature install audio bt
sudo e850-feature remove desktop
e850-desktop start           # X 켜기(dvslive는 그동안 멈춘다), e850-desktop stop
e850-usbhost on              # 키보드/마우스/유선랜 (재부팅하면 꺼짐; 계속 쓰려면 systemctl enable e850-usbhost)
```

## 번들과 보드의 배치

| 번들(`e850-features/`) | 보드 |
|---|---|
| 전체 | `/opt/e850-features/` (설치할 때 통째로 복사), `/usr/local/bin/e850-feature` |
| `ko/*.ko` | `/lib/modules/$(uname -r)/extra/` + `/etc/depmod.d/e850-features.conf`(`search extra built-in`: 같은 이름이면 extra가 이긴다) |
| `fw/audio/*.bin`, `fw/video/mfc_fw.bin` | `/lib/firmware/` |
| `*/e850-*.service` | `/etc/systemd/system/` |
| 설치 기록 | `/var/lib/e850-features/<기능>` (번들 버전) |

- **커널 모듈 소스**는 `modules/<이름>/`(`build-module.sh`)에 있고 `bundle.sh`가 빌드한다. dvs의 카메라 모듈 3개는 다시 빌드하지 않고 보드가 쓰는 e850-out 빌드를 strip해서 넣는다. 지금은 `smsc95xx`, `exynos3830-sound`(카드 등록 순서 패치), `scsc-wlan-recovery`(BT를 켠 채 Wi-Fi 복구가 실패하던 문제 패치, `bt`가 넣는다), `exynos-mfc`(udev가 노드를 바로 열면 부팅 중 패닉하던 경합 패치, `video`가 넣는다, 11.80), `e850-hubpwr`.
- **펌웨어**(Samsung 바이너리)는 git에 넣지 않는다. `host/fetch-fw.py`가 Linaro Android 13 E850-96 저장소의 고정 커밋에서 받고, `host/fw.sha256`으로 확인한다.
- 모듈은 커널 릴리스(`KVER`)가 보드의 `uname -r`과 같을 때만 설치한다.

## 새 기능 넣기

1. `features/<이름>/feature.sh`를 만든다. 첫 줄들에 `# about: 한 줄 설명`과 필요하면 `# needs: 다른기능`을 쓴다.
2. 스크립트 안에서 `ROOT=$(cd "$(dirname "$0")/.." && pwd); . "$ROOT/common.sh"`를 하고, `case $1 in install|remove|status`를 쓴다. `common.sh`에는 `ko_install`, `unit_install`, `apt_need` 같은 공통 함수가 있다.
3. 새 커널 모듈이 필요하면 `modules/<이름>/build-module.sh`를 만들고, `host/bundle.sh`의 `MODS`와 strip 목록에 넣는다.

## 알아둘 것

- **audio:** 원래 `exynos3830_sound`는 이 DT에서 카드 등록에 실패한다(fdtoverlay가 sound 자식 노드 순서를 뒤집는다, 11.77). 그래서 패치한 모듈을 쓴다. 등록에 실패한 뒤 같은 부팅에서 다시 올리면 커널 패닉이 났다. 그래서 `audio.sh`는 다시 시도하지 않고 재부팅하라고만 한다.
- **audio 출력:** 헤드폰 경로까지 DAPM이 켜지고 재생이 실제 속도(3초 → 3.19초)로 끝나는 것은 확인했다. 귀로 들어 보지는 못했다. HDMI 소리는 아직 없다.
- **bt:** 칩 안의 BT는 Wi-Fi 펌웨어(WLBT)와 같이 돈다. Wi-Fi 펌웨어가 복구되면 브리지가 끝나고 systemd가 5초 뒤 다시 띄운다.
- **BT 검색이 아무것도 못 찾을 때(11.96):** 커널이 검색 정지 요청의 HCI 명령이 실패하면 STOPPING 상태에 남는다. 그러면 bluetoothd는 검색 중이라고 하는데 실제로는 아무것도 찾지 않고, 새 검색은 Busy로 거절된다. 브리지가 이것을 감시한다. bluetoothd가 검색 중이라는데 검색 명령(Inquiry, 이름 요청, LE 스캔)이 30초 동안 없으면, 가짜 Remote Name Request Complete를 커널에 넣어 STOPPED로 돌린다(연결은 그대로). 그래도 안 되면 어댑터를 껐다 켠다. 로그는 `journalctl -u e850-bt`(`discovery stuck`, `hci command 0x....: status`)다.
- **bt와 Wi-Fi 복구:** 원래 `scsc_wlan`은 BT가 열려 있으면 펌웨어 복구 뒤 Wi-Fi를 되살리지 못한다(11.78). `bt` 설치가 고친 `scsc_wlan.ko`를 `extra/`에 넣지만, 다시 올리면 패닉이 나므로 **한 번 재부팅해야** 쓰인다. 그 전까지 `e850-bt.service`는 켜지지 않는다(`bt-check.sh`).
- **BT 스피커(11.89):** 처음 한 번 페어링한다(스피커는 페어링 모드로). `Pairable: no`인 채로 하면 `No Bonding`으로 요청이 나가 스피커가 `Pairing Not Allowed`로 거절한다.
  ```sh
  sudo bluetoothctl pairable on
  sudo bluetoothctl --timeout 20 scan on          # 스피커 MAC 찾기 (bluetoothctl devices)
  sudo bluetoothctl --agent NoInputNoOutput pair <MAC> && sudo bluetoothctl trust <MAC> && sudo bluetoothctl connect <MAC>
  ```
  출력은 사용자마다 `~/.asoundrc`로 BT에 돌린다(`pcm.!default { type plug; slave.pcm "bluealsa" }`, `ctl.!default { type bluealsa }`). 지우면 헤드폰 잭으로 돌아간다. 스피커가 꺼져 있으면 기본 장치가 열리지 않는다. bluez-alsa 4.0은 섞기를 못 해서 한 번에 한 프로그램만 쓴다. 이미 떠 있던 프로그램은 ALSA 설정을 다시 읽지 않으니 다시 띄운다.
- **Wi-Fi·BT 설정(11.90):** desktop 기능이 `wpagui`와 `blueman`을 넣고, 세션이 트레이에 아이콘을 띄운다. wpa_gui가 일반 사용자로 Wi-Fi를 다루려면 두 가지가 더 필요하다. 사용자를 `netdev`·`bluetooth` 그룹에 넣고(`usermod -aG netdev,bluetooth 사용자`), `wpa_supplicant-wlan0.conf`에 `ctrl_interface=DIR=/run/wpa_supplicant GROUP=netdev`와 `update_config=1`을 넣은 뒤 `wpa_supplicant@wlan0`를 재시작한다. Wi-Fi가 잠깐 끊기니 ssh로 할 때는 되돌리기 장치를 걸고 한다(11.90). Wi-Fi 드라이버는 `/proc/net/wireless`에 신호를 주지 않아 lxpanel `netstatus`는 쓰지 않는다.
- **desktop 세션(11.89):** `session.sh`가 세션마다 따로 D-Bus를 띄운다(`dbus-run-session`). fcitx5는 버스마다 하나만 돌기 때문이다. 한/영은 Ctrl+Space, 한/영 키, 오른쪽 Alt다. GPU X(:1, `modules/e850-kms/x-kms.sh`)도 같은 세션을 GPU Mesa 환경으로 띄운다. `/usr/local/bin/firefox`는 :1에서 별도 프로필(`~/.config/mozilla/firefox/gpu`)을 쓴다. 같은 기본 프로필이 :0에 열려 있으면 새 창이 :0에 생기기 때문이다.
- **dmix 없음:** 이 커널에는 SysV IPC가 없어 ALSA dmix가 안 된다. 44.1 kHz를 그대로 내보내면 약 15% 빨라서 `asound.conf`가 48 kHz로 바꾼다.
- **dvs(11.97):** `dvs`는 X 터미널에서 실행한다(sudo 비밀번호를 묻는다). 실행하는 동안 `dvslive.service`를 멈추고 끝나면 되살린다. 창은 메모리에 그린 960×720 페이지를 XPutImage로 올린다(SysV IPC가 없어 MIT-SHM 없음). fb0 주소 배너는 X에서 끈다(X도 fb0를 쓴다). 창을 닫거나 q, Esc, Ctrl+C로 끝낸다. 설정 페이지에서 센서 값을 바꾸면 캡처가 끝나고 `dvs`가 2초 뒤 다시 띄운다(11.98). 센서가 없을 때 `fimc-is`를 한 번 올리는데, 이 모듈은 재부팅 전까지 내리지 않는다. 데모 녹화는 번들에 `out/dvs-demo.gz`가 있을 때만 들어간다(git 밖, `dvs_record()` 형식, 7초 stand_up).
- **desktop:** X는 CPU로 그린다. GPU는 `tools/gpu/`(Mesa panfrost on kbase, 11.82)로 화면 밖 렌더링만 되고, X에 붙이려면 KMS 장치와 dma-buf 공유가 더 필요하다. 그래서 영상은 480p 정도가 현실적이다(11.80의 측정 참고).
