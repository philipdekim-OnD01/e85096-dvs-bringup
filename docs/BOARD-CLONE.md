# E850-96 보드 복제 절차

돌고 있는 보드(원본)의 시스템을 다른 E850-96(대상)에 그대로 옮기는 방법이다. 2026-11-14에 보드1(192.168.0.236)을 보드2(출하 상태 Android 10)로 복제할 때 쓴 명령과 측정값을 정리했다. 그날 있었던 일은 [E85096-LINUX510-BOOT.md](E85096-LINUX510-BOOT.md) 11.53절에 있다. 2026-11-15에 보드3으로 한 번 더 했다(11.57, 아래 "보드3에서" 표시).

## 1. 무엇이 옮겨지나

| 대상 | 방법 | 결과 |
|---|---|---|
| `boot` 파티션 | 원본 boot 파티션과 같은 이미지 파일(`~/e850-logs/boot-dvs3/boot.img`)을 굽는다 | 같다 |
| `userdata`(루트 파일시스템) | 원본에서 tar로 받는다 → 같은 크기·라벨·UUID의 ext4로 만든다 → 굽는다 | 같다. machine-id만 새로 만든다 |
| `efs`, `persist`, `dtbo`, bootloader 등 | 건드리지 않는다 | 대상 보드 것 그대로다 |

- **같은 것:** 사용자와 비밀번호, ssh 호스트 키, Wi-Fi 설정(SSID와 비밀번호), 호스트 이름 `e850-96`, 서비스, 모듈, `/var/tmp`의 녹화와 시험 결과.
- **다른 것:**
  - machine-id: 새로 만든다. 그래야 공유기가 다른 IP를 준다.
  - `/var/lib/systemd/random-seed`: 지운다.
  - Wi-Fi MAC: 드라이버가 `00:00:0F` 뒤에 SoC 고유 번호를 붙여 만들므로 저절로 다르다(`drivers/net/wireless/scsc/mgt.c:540`).
- **복제한 뒤에 할 일:** 두 보드는 USB 주소(192.168.7.1)와 ACM 이름(`/dev/cu.usbmodeme850_961`)이 같다. 그래서 **Mac에는 한 번에 한 보드만 USB-C로 꽂는다.** 지금 꽂힌 보드는 `cat /etc/machine-id`로 구별한다.

## 2. 준비

- **원본 보드:** Wi-Fi ssh(`philip@<원본>`)가 되고, USB-C로 ACM root 콘솔(`tools/host/acm.py`)이 된다.
- **대상 보드:** 출하 상태 Android 10이면 된다. USB-C와 UART 어댑터를 Mac에 꽂는다. 카메라를 쓰려면 CN4에 센서를 꽂는다.
- **Mac:**
  - Docker Desktop, `fastboot`(Homebrew), 디스크 여유 약 8 GB.
  - `fbmini`를 빌드해 둔다:
    ```sh
    clang -O2 -o ~/e850-logs/fbmini tools/linux510/fbmini.c \
        -I/opt/homebrew/include/libusb-1.0 -L/opt/homebrew/lib -lusb-1.0
    ```
  - 대상 보드의 UART 로그를 켠다. fastboot에 넣을 때와 굽기 확인에 쓴다:
    ```sh
    nohup python3 tools/host/uartlog.py /dev/cu.usbserial-<대상> ~/e850-logs/uart-new.log >/dev/null 2>&1 &
    ```
    대상 보드를 끄면 어댑터가 잠깐 사라지면서 로거가 죽을 수 있다. 그러면 다시 켠다.
- **작업 디렉터리:** `D=~/e850-logs/clone-$(date +%Y%m%d)`. **비밀 값(`/etc/shadow`, ssh 키, Wi-Fi 비밀번호)이 들어가므로 저장소 밖에 두고, 끝나면 지운다.**

## 3. 원본 확인 (Mac에서, 원본 USB-C 연결)

1. boot 파티션이 어느 이미지인지 확인한다. 두 md5가 같아야 한다:
   ```sh
   N=$(stat -f %z ~/e850-logs/boot-dvs3/boot.img); md5 -q ~/e850-logs/boot-dvs3/boot.img
   python3 tools/host/acm.py "head -c $N /dev/disk/by-partlabel/boot | md5sum" 40
   ```
2. 파일시스템 크기와 UUID를 확인한다. `build-clone.sh`의 기본값(블록 786432, UUID `de77216b-…`)과 다르면 `-e BLOCKS=… -e UUID=…`로 넘긴다:
   ```sh
   python3 tools/host/acm.py "tune2fs -l /dev/mmcblk0p24 | grep -E 'Block count|UUID|features'; df -h /" 15
   ```

## 4. 루트 파일시스템 받기 (약 12분)

USB 네트워크는 보드→Mac 방향이 멈출 때가 있다(11.52). 그래서 Wi-Fi ssh 터널로 받는다. 원본 보드는 127.0.0.1에서만 연결을 받는다.

```sh
scp tools/board/rootfs-send.py philip@<원본>:/var/tmp/
python3 tools/host/acm.py "rm -f /var/tmp/rootfs-send.md5; systemd-run --unit=rootfs-send -p Nice=10 python3 -I /var/tmp/rootfs-send.py" 10
python3 tools/host/rootfs-recv.py $D philip@<원본>          # 2.6 GB, 3.7 MB/s, 702 s (2026-11-14)
ssh philip@<원본> cat /var/tmp/rootfs-send.md5              # md5와 바이트 수가 위 출력과 같아야 한다
```

- `tar=1`은 정상이다. 돌고 있는 시스템이라 "file changed as we read it"(`./sys`, journal) 경고가 난다. 경고 내용은 원본 ACM에서 `journalctl -u rootfs-send`로 본다(philip은 journal을 못 읽는다).
- 전송하는 동안 원본의 dvslive가 한 번 재시작했다(원인 미확인, 11.53). 원본의 서비스가 잠깐 끊길 수 있다고 보고 한다.
- **원본 USB-C가 안 붙을 때(보드3에서):** 한 번 뺐던 케이블을 다시 꽂으면 원본 UDC가 `not attached`에 머물 수 있다(원인 미확인). 그때는 전에 받아 둔 `rootfs.tar`를 새 디렉터리에 `ln`으로 걸고 5장부터 한다. `build-clone.sh`가 machine-id를 새로 만드므로 옛 `part.simg.*`를 그대로 굽지 않는다(그 안에는 앞 대상의 machine-id가 있다). 그 사이 원본에서 바뀐 파일은 `ssh philip@<원본> 'find /usr /etc /home /opt /var/tmp /var/lib -xdev -type f -newermt "<tar 받은 UTC 시각>"'`로 찾는다. 넣을 파일은 `$D/overlay/<경로>`에 두면 `build-clone.sh`가 이미지에 덮어쓴다(이미 있는 파일은 소유자와 권한이 그대로다, 보드4에서). 그러면 8장 파일 비교에서도 차이가 없다.

## 5. 이미지 만들기 (몇 분)

```sh
docker run --rm --privileged -v "$PWD/tools/linux510:/t:ro" -v $D:/x ubuntu:22.04 sh /t/build-clone.sh > $D/build.log 2>&1
grep -c -i warning $D/build.log                                  # 0이어야 한다
python3 tools/linux510/resparse.py --split-mib 700 $D/rootfs.simg $D/part.simg   # part.simg.0 ~ .3
```

- `build.log`에서 새 machine-id, `df`(3 GiB 중 사용량), e2fsck 결과를 확인한다.
- 조각은 각각 파티션 전체를 덮는 sparse 파일이다. 자기 데이터 앞뒤는 DONT_CARE로 채운다. FILL 조각은 32 MiB 이하로 쪼갠다. LK는 1 GiB FILL에서 멈춘다(7.12).
- `$D/manifest.md5`에는 `usr lib var/tmp root home opt` 아래 모든 파일의 md5가 들어 있다. 대상 보드에서 비교할 때 쓴다(8장).

## 6. 대상 보드를 fastboot에 넣기

- **adb는 쓰지 않는다.** Mac의 Homebrew adb 37.0.1은 서버가 시작하다 죽는다.
- **VOL_UP 키도 쓰지 않는다.** LK가 10번 모두 `Volume up key: 1`(안 눌림)로 읽는다.

**대상이 Android 10일 때:** Android가 UART에 `console` 셸을 띄운다(`ro.debuggable=1`). 부팅이 끝난 뒤(약 60초) UART로 명령을 보낸다. uartlog.py가 포트를 115200으로 맞춰 둔 상태여야 한다:
```sh
python3 -c "import os; fd=os.open('/dev/cu.usbserial-<대상>', os.O_WRONLY|os.O_NOCTTY|os.O_NONBLOCK); os.write(fd, b'reboot bootloader\r')"
fastboot devices                                            # 수 초 뒤 대상 일련번호가 보인다
```
UART 로그에 `Entering fastboot: reboot bootloader command`가 나온다.

**보드3에서:** 전원을 넣자 LK가 `Volume up key is pressed. Entering fastboot mode!`로 바로 fastboot에 들어갔다. 보드마다 다를 수 있으니 먼저 `fastboot devices`를 본다.

**대상이 이미 우리 Linux일 때:** ACM에서 `to-fastboot`을 실행한다. 워치독을 죽여 약 30초 뒤 `Abnormal RST_STAT`로 들어간다. `rebootarg bootloader`는 LK가 무시한다.

## 7. 굽기 (약 12분)

```sh
fastboot -s <대상> flash boot ~/e850-logs/boot-dvs3/boot.img      # boot는 fastboot로 된다(2.4 s)
for i in 0 1 2 3; do ~/e850-logs/fbmini flash userdata $D/part.simg.$i; done
~/e850-logs/fbmini reboot
```

- **`fastboot flash userdata`는 쓰지 않는다.** 굽기 전에 `getvar:is-userspace`를 보내는데, LK가 거기서 멈춘다. 멈추면 전원을 다시 넣어야 한다.
- **조각마다 결과를 보고 다음으로 간다. 하나라도 실패하면 `reboot`를 보내지 않는다(보드4에서).** 전송 중 `LIBUSB_ERROR_IO`로 끊기면 LK는 나머지 데이터를 기다리며 명령에 답하지 않는다. 대상 전원을 다시 넣는다. Linux가 initramfs 셸로 뜨면(`error loading journal`) 거기서 `to-fastboot`을 그냥 실행한다(`setsid nohup … &`는 initramfs에서 듣지 않았다). 그다음 성공하지 못한 조각부터 다시 굽는다. 조각마다 파티션 전체를 덮으므로 이미 성공한 조각은 다시 굽지 않아도 된다.
- 대상 USB-C는 Mac 본체 포트에 꽂는다. 보드4는 USB 허브 두 단 뒤에서 두 번 끊겼고, 본체 포트로 옮긴 뒤 됐다(원인 확인은 안 됨).
- 굽는 동안 `fastboot devices`를 되풀이해 부르지 않는다(같은 USB 장치를 연다. 방해하는지는 확인하지 않았다).
- 조각마다 걸린 시간은 5분 56초, 76초, 116초, 86초였다(보드3에서는 250 s, 31 s, 53 s, 32 s). 첫 조각(18,465청크)이 느린 것은 LK가 청크마다 UART에 한 줄을 찍기 때문으로 보인다.
- UART에서 각 조각의 위치를 확인할 수 있다. 첫 줄은 `CHUNK TYPE NONE (lba: <userdata 시작>, sct: …)`이고, 다음 RAW의 lba가 앞 조각의 끝과 이어지면 된다.

## 8. 확인

부팅은 약 40초 걸린다. 아래는 모두 2026-11-14에 확인한 항목이다.

```sh
python3 tools/host/acm.py "cat /etc/machine-id; systemctl is-system-running; systemctl --failed --no-legend; ip -br addr show wlan0" 20
```
- machine-id가 `build.log`와 같고, `running`이고, 실패한 유닛이 없고, wlan0에 IP가 있으면 된다.
- ssh는 호스트 키가 원본과 같으니 `ssh -o HostKeyAlias=<원본 주소> philip@<대상 주소>`로 접속한다.
- 파일 비교: 대상에서 root로 목록을 만들고 Mac에서 비교한다. 다른 파일은 실행 중에 바뀌는 캐시(`root/.cache/…/onnxruntime.db*`)뿐이어야 한다.
  ```sh
  python3 tools/host/acm.py "cd / && find usr lib var/tmp root home opt -xdev -type f -print0 | LC_ALL=C sort -z | xargs -0 md5sum > /run/m.md5; chmod 644 /run/m.md5" 300
  ssh -o HostKeyAlias=<원본 주소> philip@<대상 주소> cat /run/m.md5 | LC_ALL=C sort -k2 > /tmp/b.s
  LC_ALL=C sort -k2 $D/manifest.md5 | diff - /tmp/b.s
  ```
- **센서가 없는 대상(보드3에서):** dvslive가 웹 페이지만 띄우고 화면에 "DVS 센서가 연결되어 있지 않습니다"를 보인다(11.57). 그 판보다 옛 `ov5647-cap.py`이면 dvslive가 몇 초마다 재시작하며 커널 경고를 내니 바로 `systemctl stop dvslive`를 한다.
- 웹과 카메라: `http://<대상>:8080/`이 200을 돌려주는지 본다. `/stats`의 `crc`, `link.short_bufs`, `coverage`는 원본과 같은 수준이어야 한다(CRC 0, 데이터 100%).

## 9. 막혔던 곳과 해결

| 증상 | 원인 / 해결 |
|---|---|
| `fastboot flash userdata`가 끝나지 않고 LK가 응답하지 않음 | `getvar:is-userspace`. 전원을 다시 넣는다. 그러면 새 boot로 Linux가 뜨는데, userdata가 아직 Android f2fs라 init이 ACM에 셸을 띄운다. 거기서 `to-fastboot`을 실행하고 7장의 fbmini로 굽는다 |
| 대상에서 MIPI CRC가 모든 줄에 나고 dvslive가 수십 초마다 재시작함 | CN4 카메라 케이블 접촉 문제였다. dvslive를 멈추고 → `systemctl poweroff` → 케이블을 다시 꽂고 → 전원을 넣었더니 CRC 0이 됐다. 그대로 두면 정지 경로에서 보드가 멈추고 워치독이 리셋한다 |
| 대상 UDC가 `not attached`(ACM 없음) | USB-C가 덜 꽂혔다. 다시 꽂으니 `configured`가 됐다. e850-vbus 경합(11.31)과는 다르다. 경합이면 dmesg에 `does not have any gadget`이 나온다 |
| ACL 경고(`Cannot acl_from_text`) | tar는 ACL을 사용자 이름으로 기록한다. `build-clone.sh`가 원본의 `/etc/passwd`, `/etc/group`을 컨테이너에 먼저 넣어 해결했다 |
| ACM 도구가 엉뚱한 장치로 보냄 | 휴대폰 같은 다른 ACM 장치가 꽂혀 있으면 `glob('/dev/cu.usbmodem*')[0]`이 그쪽을 골랐다. `acmpush.py`, `acmpull.py`는 이제 `/dev/cu.usbmodeme850_961`만 쓴다(11.57) |
| acmpush가 끝났는데 md5가 옛 값 | `fs.protected_regular = 2`: `/var/tmp`에 philip의 같은 이름 파일이 있으면 root도 덮어쓰지 못한다. 먼저 `rm -f`한다(11.57) |
| USB 네트워크로 받을 때 연결 시간 초과 | USB 네트워크의 보드→Mac 방향이 멈춰 있었다(11.52). Wi-Fi ssh 터널로 받는다(4장) |

## 10. 다음에 하면 좋을 것

- 파일시스템은 3 GiB로 만들어지고, 파티션은 54 GiB다. 복제한 뒤 대상에서 `sudo resize2fs /dev/mmcblk0p24`를 한다. 마운트된 채로 늘어나서 재부팅은 필요 없다. 2026-11-19에 보드4와 보드1에서 했다. 둘 다 54 GB가 됐고 49 GB가 남았다(E85096-LINUX510-BOOT.md 11.67). 보드4는 늘리기 전에 100% 차 있었다. 보드2와 보드3은 늘리지 않았다.
- 두 보드를 동시에 USB로 쓰려면 한쪽의 gadget 주소와 일련번호를 바꿔야 한다. 아직 하지 않았다.

## 11. 데스크톱 보드 이미지 (보드3, 2026-11-23)

보드3을 원본으로 받은 이미지다. GPU 데스크톱(`e850-x`), 패널·메뉴·파일 관리자, 한글 입력, BT 스피커 출력, Wi-Fi·BT 설정 창, zram이 들어 있다(E85096-LINUX510-BOOT.md 11.89~11.91). 방법은 위 4~8장과 같고, 여기에는 다른 점만 적는다.

| 항목 | 값 |
|---|---|
| 이미지 디렉터리 | `~/e850-logs/clone-20261123/`(Mac). `rootfs.tar`(원본 그대로), `rootfs.simg`, `part.simg.*`, `manifest.md5`, `build.log`. **비밀 값(사용자 비밀번호 해시, ssh 키, Wi-Fi 비밀번호, BT 링크 키)이 들어 있다.** 저장소나 외부로 옮기지 않는다 |
| boot | **`~/e850-logs/boot-nocpu/boot.img`**(md5 `25643a9a3f6b62b18db07cd895a7c410`, 명령줄에 `cgroup_disable=cpu`). 이 rootfs에는 `libpam-systemd`가 있어, 다른 boot로는 로그인할 때 커널 패닉이 난다(11.80) |
| 파일시스템 | 8 GiB(`BLOCKS=2097152`), 라벨 `rootfs`, UUID `de77216b-a7d7-4c77-9fec-51c801b1fb77`(원본과 같음). 원본 사용량 약 5.2 GB(apt 캐시는 비우고 받음) |

**구운 보드: 보드5(11.92), 보드6(11.93, 현미경 뷰어를 더 넣음, 11-24 고장), 보드4(11.101, 보드6 대신).** 지금 `part.simg.*`의 machine-id(`606a4996…`)는 보드4의 것이다(보드6 판은 `board6-old/`). 보드6과 같은 상태로 만들 때는 `~/e850-logs/scope-board4/board4-setup.sh`를 root로 돌린다(11.101: 현미경 저장소 판, Wi-Fi 감시, DVS 자동 실행, `bt dvs` 기능). 현미경 보드로 쓸 때는 구운 뒤 `~/e850-logs/scope-board6/board6-setup.sh`(11.92의 두 수정, `e850-usbhost` 끄기, `tools/board/scope/install.sh`, OpenCV)를 root로 돌린다(11.93).

**대상마다 이미지를 새로 만든다.** `build-clone.sh`가 만들 때마다 machine-id를 새로 넣기 때문이다. 같은 `part.simg.*`를 두 보드에 구우면 machine-id가 같아져 DHCP 주소가 겹친다. `rootfs.tar`는 다시 받을 필요가 없다.

```sh
D=~/e850-logs/clone-20261123
rm -f $D/rootfs.simg $D/part.simg.*
docker run --rm --privileged -e BLOCKS=2097152 -v "$PWD/tools/linux510:/t:ro" -v $D:/x ubuntu:22.04 sh /t/build-clone.sh > $D/build.log 2>&1
grep -c -i warning $D/build.log                       # 0
python3 tools/linux510/resparse.py --split-mib 700 $D/rootfs.simg $D/part.simg
fastboot -s <대상> flash boot ~/e850-logs/boot-nocpu/boot.img
for f in $D/part.simg.*; do ~/e850-logs/fbmini flash userdata $f || break; done   # 하나라도 실패하면 reboot하지 않는다(7장)
~/e850-logs/fbmini reboot
```

**구운 뒤 원본(보드3)과 다른 점과 할 일.**
- **ACM 콘솔이 없다.** `e850-usbhost`가 부팅 때 켜져서 micro-USB는 gadget이 아니다. 확인은 Wi-Fi ssh(`ssh -o HostKeyAlias=192.168.0.236 philip@<대상 주소>`, sudo 비밀번호는 원본과 같다)나 HDMI root 프롬프트(USB 키보드)로 한다. 8장의 `acm.py` 명령은 ssh에서 `sudo`로 바꿔 한다. ACM이 필요하면 `sudo e850-usbhost off`(다음 부팅까지) 또는 `sudo systemctl disable e850-usbhost`. fastboot(LK)는 이와 상관없이 micro-USB로 된다.
- 파일시스템을 늘린다: `sudo resize2fs /dev/mmcblk0p24`(10장).
- **이미지에 없는 두 수정을 넣는다(11.92).** 넣지 않으면 부팅이 2분 동안 `systemd-networkd-wait-online`을 기다리다 `degraded`가 된다(usb0가 DOWN). `bluealsa-aplay`도 failed로 남는다.
  ```sh
  # Mac에서 (저장소 루트)
  scp -o HostKeyAlias=192.168.0.236 features/usbhost/wait-online-any.conf philip@<대상 주소>:/var/tmp/
  # 대상 보드에서 (sudo)
  install -D -m 644 /var/tmp/wait-online-any.conf /etc/systemd/system/systemd-networkd-wait-online.service.d/e850-any.conf
  systemctl daemon-reload; systemctl reset-failed; systemctl restart systemd-networkd-wait-online
  systemctl disable --now bluealsa-aplay
  systemctl is-system-running        # running
  ```
- **DVS 센서가 있는 보드:** dvslive가 꺼져 있다. `sudo systemctl enable --now dvslive`로 켠다. 그러면 HDMI는 DVS 화면이 차지하고, `e850-x`를 치면 dvslive를 멈추고 데스크톱을 띄운다.
- **BT 스피커는 다시 페어링한다.** 페어링 정보는 원본 어댑터 주소(`/var/lib/bluetooth/9C:23:E0:0A:74:9C/`) 아래에 있어 새 보드에서는 쓰이지 않는다. 절차는 `features/README.md`의 "BT 스피커"다. 그 전에는 `~/.asoundrc`(philip, 기본 출력 = BT) 때문에 소리가 나지 않는다. 헤드폰 잭으로 쓰려면 그 파일을 지운다.
- 그대로 오는 것: philip의 `netdev`·`bluetooth` 그룹, wpa_supplicant 제어 소켓(netdev) 설정, zram(1.8 GB, lz4), GPU(`~/gpu/mesa-kbase`, `/var/tmp/e850-kms`의 `e850-kms.ko`), `/usr/local/bin/e850-x`, `/etc/motd`의 안내 한 줄, Firefox 프로필(사용자 기본 프로필과 `~/.config/mozilla/firefox/gpu`, 시험 프로필 `/var/tmp/ffprof*`), `/var/tmp`의 녹화와 시험 파일(약 2 GB).
- 보드5에서 본 굽기: boot 2.4초, userdata 8조각 합계 577초. 첫 시도는 boot에서 LK가 멈춰 전원을 다시 넣었다. 그때 보드는 UART와 같은 허브 뒤에 있었고, 다른 세션 스크립트가 5초마다 `fastboot devices`를 부르고 있었다(11.92). 굽는 동안에는 다른 fastboot 호출을 멈춘다.
- 확인 순서: HDMI에 root 프롬프트 → USB 키보드로 `e850-x` → 패널·메뉴·한/영 → `ssh`로 `systemctl --failed`, `cat /proc/swaps`, `id philip`.

## 12. 보드6 전체 이미지 (DVS 데스크톱 + 현미경, 2026-11-24)

보드6을 원본으로 받은 이미지다(E85096-LINUX510-BOOT.md 11.102). 11장 데스크톱 이미지 위에 보드6에서 더한 것이 모두 들어 있다.
- 현미경 뷰어 `e850-scope`: 부팅 때 USB 호스트를 맡는다. `e850-usbhost`는 disabled다.
- `bt`·`dvs` 기능, Wi-Fi 감시 `e850-wifiwatch`.
- GPU 데스크톱(`e850-x`)을 띄우면 `dvs --auto`가 DVS 창과 설정 패널을 띄운다. 센서가 없으면 조용히 끝난다.
- Chrome GPU 판: 사용자가 깐 `google-chrome-stable`, 래퍼 `/usr/local/bin/google-chrome`, 고친 shim.
- DVS GPU 뷰어 `dvsgl`.

방법은 4~8장과 같고, 다른 점만 적는다.

| 항목 | 값 |
|---|---|
| 이미지 디렉터리 | `~/e850-logs/clone-20261124-b6/`(Mac, 권한 700). 들어 있는 것: `rootfs.tar`(원본, md5 `7b2c7466…`, 보드6 `rootfs-send` 값과 같음), `overlay/`(tar 뒤에 바뀐 6개 파일), `boot.img`(= boot-nocpu), `rootfs.simg`·`part.simg.0~10`(보드4용), `manifest.md5`, `MD5SUMS`, `README.txt`. **비밀 값(비밀번호 해시, ssh 키, Wi-Fi 비밀번호, BT 링크 키)이 들어 있다.** 저장소나 외부로 옮기지 않는다 |
| 받은 때 | 2026-11-24 14:17~15:07 KST(`rootfs-send`, 약 2.4 MB/s). 그 뒤 바뀐 파일은 `find / -xdev -newermt` 결과에서 골라 `overlay/`에 넣었다 |
| boot | `boot.img` = `~/e850-logs/boot-nocpu/boot.img`(md5 `25643a9a…`). `libpam-systemd`가 있어서 반드시 이 boot를 쓴다(11.80) |
| 파일시스템 | 10 GiB(`BLOCKS=2621440`), 라벨 `rootfs`, UUID `de77216b-…`. 사용량 7.0 GB. 대상 파티션은 57.9 GB이니 첫 부팅 뒤 `resize2fs`로 늘린다 |

**대상마다 다시 만든다.** 같은 보드에 다시 구울 때는 `MACHINE_ID`에 그 보드의 id를 준다. 그래야 DHCP 주소가 유지된다. 새 보드라면 `MACHINE_ID`를 빼고 새 id를 쓴다.

```sh
D=~/e850-logs/clone-20261124-b6
rm -f $D/rootfs.simg $D/part.simg.*
docker run --rm --privileged -e BLOCKS=2621440 -e MACHINE_ID=<대상 id 또는 이 줄에서 빼기> \
    -v "$PWD/tools/linux510:/t:ro" -v $D:/x ubuntu:22.04 sh /t/build-clone.sh > $D/build.log 2>&1
python3 tools/linux510/resparse.py --split-mib 700 $D/rootfs.simg $D/part.simg
fastboot -s <대상> flash boot $D/boot.img            # 대상 boot가 이미 boot-nocpu면 생략
for f in $(ls $D/part.simg.* | sort -t. -k3 -n); do ~/e850-logs/fbmini flash userdata $f || break; done   # 하나라도 실패하면 reboot하지 않는다
~/e850-logs/fbmini reboot
```

조각은 11개(0~10)다. 조각마다 자기 데이터 앞뒤가 DONT_CARE인 파티션 전체 이미지라(`resparse.py`) 원리상 순서와 상관없다. 그래도 실패한 조각을 찾기 쉽게 숫자 순으로 굽는다(글자 순이면 `.10`이 `.1` 뒤에 온다).

**대상을 fastboot에 넣기.** 이 이미지가 올라간 보드는 부팅 때 `e850-scope`가 USB 호스트를 맡는다. 그래서 Mac에는 ACM도 fastboot도 보이지 않는다.
- ssh에서 `sudo systemctl stop e850-scope`를 한다. 스크립트가 gadget으로 되돌리고, Mac에 ACM(`/dev/cu.usbmodeme850_961`)이 생긴다. 이때 케이블이 Mac에 꽂혀 있는지도 확인된다.
- 그다음 `sudo sh -c '(setsid nohup /usr/local/sbin/to-fastboot >/dev/null 2>&1 </dev/null &)'`를 한다. 보드4는 43초 뒤 fastboot에 보였다.

**구운 뒤 할 일.**
- `sudo resize2fs /dev/mmcblk0p24`.
- BT 기기를 다시 페어링한다. 페어링 정보는 보드6 어댑터(`/var/lib/bluetooth/9C:23:E0:0B:F5:4C/`) 아래에 있어서 다른 보드에서는 쓰이지 않는다.
- 11장의 두 수정(`wait-online` any, `bluealsa-aplay` 끔)은 이미 들어 있다(보드6에서 `systemctl is-system-running` = running).
- DVS 센서가 CN4에 있어야 DVS 창이 뜬다. 센서는 전원을 끈 채 옮긴다.

**보드4에 구운 기록(2026-11-24).**
- 11조각 모두 OKAY였다(합계 1,704초).
- 첫 부팅은 23.84 s에 MFC 경합으로 패닉했다(11.102). 다음 부팅은 eMMC 시간 초과로 initramfs에 떨어졌다. 워치독이 fastboot로 보냈고, `fastboot reboot` 뒤 정상이었다.
- 굽고 나서 바로 Wi-Fi에 나오지 않으면 Mac의 ACM(`/dev/cu.usbmodeme850_961`)을 본다. 이때 ACM이 보이면 initramfs 셸이다(Debian이면 `e850-scope`가 USB를 가져간다). 셸에서 `dmesg`를 보고, 워치독 리셋을 기다리거나 다시 부팅한다.
- 확인: manifest 42,074개 파일이 모두 같았다. 서비스 상태가 원본과 같고, failed 유닛은 0이었다.

**보드3에 구운 기록(2026-11-27, E85096-LINUX510-BOOT.md 11.119).**
- 보드6에서 새로 받은 판이다: `~/e850-logs/clone-20261127-b6/`(rootfs.tar md5 `bd019b52…`, 8.2 GB, 3.2 MB/s). 사용량이 7.7 GB라 `BLOCKS=3145728`(12 GiB)로 만들었고, 조각은 12개다.
- 보드3은 전원을 넣었을 때 eMMC 읽기 오류로 initramfs 셸에 떨어져 있었다. 거기서 `to-fastboot`을 실행했다. 보드6 `rootfs-send`는 사용자가 sudo로 띄웠다. 받는 43분 동안 보드3은 fastboot에서 기다렸다.
- 12조각 모두 OKAY였다(합계 1,884초). 45초 뒤 Wi-Fi에 나왔다. running, failed 0, philip이 읽을 수 있는 파일 44,019개가 manifest와 같았다.

## 13. SD 카드로 설치하기 (설치용 boot 이미지, 2026-11-27)

여러 대에 같은 시스템을 넣을 때 쓴다(E85096-LINUX510-BOOT.md 11.120). 보드마다 USB로 굽는 것은 설치용 boot 이미지(64 MB) 하나다. userdata는 보드가 SD 카드에서 eMMC로 직접 쓴다. 대상마다 이미지를 다시 만들 일도, 굽고 나서 sudo로 할 일(`resize2fs`, machine-id)도 없다.

**준비 (Mac, 한 번).**
1. userdata 이미지: 4~5장처럼 `rootfs.tar`를 받아 `build-clone.sh`로 만든다. `MACHINE_ID`는 주지 않는다(설치가 보드마다 정한다). Mac 디스크가 모자라면 이미지를 표준 출력으로 받아 카드에 바로 쓴다:
   ```sh
   docker run --rm --privileged -e BLOCKS=3145728 -e TAR=/src/rootfs.tar -e OUT=/dev/fd/3 \
       -v <dir>/rootfs.tar:/src/rootfs.tar:ro -v "$PWD/tools/linux510:/t:ro" -v <dir>:/x \
       ubuntu:22.04 sh -c 'sh /t/build-clone.sh 3>&1 1>&2' 2><dir>/build.log > "/Volumes/<카드>/e850-install/userdata.simg"
   ```
   카드(`/Volumes/...`)를 Docker에 `-v`로 붙이지 않는다. 컨테이너가 시작하지 못하고, 그 뒤의 `docker run`까지 모두 멈췄다(2026-11-27).
2. 설치용 boot: `build-bootimg.sh "root=PARTLABEL=userdata e850.install"` → `~/e850-logs/boot-install/boot.img`. 명령줄 말고는 보통 boot와 같다. 카드가 없으면 보통 부팅을 한다.
3. 카드(exFAT. FAT32는 4 GB 넘는 파일을 못 담는다): `sh tools/host/make-install-sd.sh "/Volumes/<카드>" ~/e850-logs/boot-nocpu/boot.img <dir>/userdata.simg`. 카드에 `e850-install/{boot.img,userdata.simg,MD5SUMS}`가 생긴다. MD5SUMS는 원본에서 잰다. **카드에는 이미지의 비밀 값이 들어 있다.** 쓴 뒤 지우거나 갖고 다닌다.

**보드마다.**
1. fastboot에 넣는다(6장).
2. `~/e850-logs/fbmini flash boot ~/e850-logs/boot-install/boot.img`(Mac에 fastboot 보드는 하나만 꽂는다. fbmini는 처음 찾은 장치를 쓴다). stock `fastboot flash boot`은 보드7에서 2분 넘게 끝나지 않았고, 그 뒤 LK가 답하지 않아 전원을 다시 넣어야 했다(2026-11-27).
3. 카드를 끝까지(딸깍) 꽂고 `~/e850-logs/fbmini reboot`. USB-C는 이때 뽑아도 된다.
4. 기다린다. 설치하는 동안 HDMI는 까맣다. 끝나면 보드가 혼자 다시 부팅하고, 새 시스템의 tty1이 HDMI에 뜬다. 그러면 카드를 빼서 다음 보드로 옮긴다.
5. 실패하면 HDMI가 까만 채로 있다. 카드의 `e850-install/log/<SoC 일련번호>.txt`에 이유가 있다. boot는 여전히 설치용이라 원인을 고친 카드로 다시 켜면 처음부터 다시 설치한다. USB-C가 꽂혀 있으면 ACM 셸에서 `dmesg | grep install`로도 볼 수 있다.

**설치가 하는 일(`tools/linux510/e850-install`, initramfs).**
- 모든 모듈을 올린 뒤 SD 카드에서 `e850-install/`을 찾는다. 10초 안에 없으면 보통 부팅을 한다.
- 검사한다(하나라도 틀리면 아무것도 쓰지 않는다):
  - boot.img가 `ANDROID!`로 시작하는지, 설치용 이미지(`e850.install`)가 아닌지, boot 파티션보다 크지 않은지, md5가 맞는지 본다.
  - 쓸 곳은 이름이 `userdata`와 `boot`인 파티션이다. 둘 다 eMMC(`device/type` = MMC) 위에 하나씩만 있어야 하고 같은 디스크여야 한다. SD가 먼저 잡혀 eMMC 번호가 바뀌어도 맞는 곳에 쓴다.
  - userdata.simg가 파티션보다 크면 쓰기 전에 멈춘다(`simg-write`).
- 지금 userdata에 우리 Linux가 있으면 그 machine-id를 남긴다. DHCP 주소가 그대로다. 없으면 새로 만든다.
- userdata.simg를 쓴다(`simg-write`). 쓰는 동안 md5를 재고, MD5SUMS와 다르면 boot를 바꾸지 않고 멈춘다.
- 새 파일시스템에 machine-id를 넣고 파티션 전체(54 GiB)로 늘린다.
- boot.img를 쓰고 다시 읽어 md5를 확인한 뒤 재부팅한다.

**보드7에 처음 써 본 기록(2026-11-27, 11.120).**
- 새 보드다. 전원을 넣자 fastboot에 들어갔다. `fbmini flash boot` 2.4 s, 설치 906 s(그때 판: `wc -c` 136 s, 파이프 때문에 쓰기 752 s). 지금 판은 같은 이미지를 195 s에 쓴다(보드에서 파일로 잼).
- 카드가 덜 꽂혀 있으면 감지되지 않는다(감지 핀 `hi`). 딸깍 소리가 날 때까지 넣는다.
- 설치 뒤 첫 부팅이 eMMC `Input/output error`로 initramfs에 남았다. 리셋하면 fastboot로 가니 `fbmini reboot`을 한다. 그다음은 정상이었다(78 s에 Wi-Fi). HDMI가 1~2분 넘게 까맣고 Wi-Fi에 안 나오면 이것을 의심한다.
- 고친 판으로 새 보드8·9에도 넣었다. 둘 다 전원을 넣으면 fastboot에 들어갔고, `fbmini flash boot`부터 Wi-Fi까지 약 4분이었다. 첫 부팅 정상(E85096-LINUX510-BOOT.md 11.120 표).
