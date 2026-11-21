# features: 보드에 골라서 넣는 드라이버와 프로그램

E850-96 보드(보드1~4 어느 것이든)에 기본 이미지에 없는 기능을 골라서 넣는 묶음이다. 기능마다 디렉터리가 하나씩 있고, 각 디렉터리의 `feature.sh`가 그 기능의 `install`, `remove`, `status`를 맡는다. 보드에서는 `e850-feature` 명령 하나로 모든 기능을 다룬다. 경과 기록은 `docs/E85096-LINUX510-BOOT.md` 11.76~11.80절에 있다.

| 기능 | 하는 일 | 의존 | 부팅 때 |
|---|---|---|---|
| `usbhost` | USB-A 호스트 모드(키보드, 마우스, LAN9514). `e850-usbhost on\|off` | | 켜지 않음. 켜면 micro-USB ACM/ECM이 없어진다 |
| `ethernet` | LAN9514 유선랜(`smsc95xx.ko`), 이름 `eth0`, MAC는 machine-id로 고정, DHCP | usbhost | USB 호스트가 켜지면 자동 |
| `audio` | ABOX DSP + AUD3004X 코덱 사운드 카드, 3.5 mm 헤드폰 출력, ALSA 기본 장치(plug → 48 kHz, 한 번에 한 프로그램) | | `e850-audio.service` |
| `video` | MFC 하드웨어 코덱(V4L2 `/dev/video6` 디코더, `/dev/video7` 인코더) | | `e850-mfc.service` |
| `bt` | Bluetooth: `scsc_bt` → `e850-btbridge.py` → `/dev/vhci` → BlueZ `hci0` | Wi-Fi(e850-wifi) | `e850-bt.service` |
| `desktop` | X(fbdev) + Openbox + 터미널 + Firefox + mpv, HDMI tty7. `e850-desktop start\|stop` | usbhost | 켜지 않음(무겁다) |

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

- **커널 모듈 소스**는 `modules/<이름>/`(`build-module.sh`)에 있고 `bundle.sh`가 빌드한다. 지금은 `smsc95xx`, `exynos3830-sound`(카드 등록 순서 패치), `scsc-wlan-recovery`(BT를 켠 채 Wi-Fi 복구가 실패하던 문제 패치, `bt`가 넣는다), `e850-hubpwr`.
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
- **bt와 Wi-Fi 복구:** 원래 `scsc_wlan`은 BT가 열려 있으면 펌웨어 복구 뒤 Wi-Fi를 되살리지 못한다(11.78). `bt` 설치가 고친 `scsc_wlan.ko`를 `extra/`에 넣지만, 다시 올리면 패닉이 나므로 **한 번 재부팅해야** 쓰인다. 그 전까지 `e850-bt.service`는 켜지지 않는다(`bt-check.sh`).
- **dmix 없음:** 이 커널에는 SysV IPC가 없어 ALSA dmix가 안 된다. 44.1 kHz를 그대로 내보내면 약 15% 빨라서 `asound.conf`가 48 kHz로 바꾼다.
- **desktop:** GPU 사용자 공간 드라이버가 없어(Mali 라이브러리는 Android용뿐) CPU로 그린다. 그래서 영상은 480p 정도가 현실적이다(11.80의 측정 참고).
