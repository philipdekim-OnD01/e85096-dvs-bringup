# E850-96 보드 복제 절차

돌고 있는 보드(원본)의 시스템을 다른 E850-96(대상)에 그대로 옮기는 방법이다. 2026-11-14에 보드1(192.168.0.236)을 보드2(출하 상태 Android 10)로 복제할 때 쓴 명령과 측정값을 정리했다. 그날 있었던 일은 [E85096-LINUX510-BOOT.md](E85096-LINUX510-BOOT.md) 11.53절에 있다.

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

**대상이 이미 우리 Linux일 때:** ACM에서 `to-fastboot`을 실행한다. 워치독을 죽여 약 30초 뒤 `Abnormal RST_STAT`로 들어간다. `rebootarg bootloader`는 LK가 무시한다.

## 7. 굽기 (약 12분)

```sh
fastboot -s <대상> flash boot ~/e850-logs/boot-dvs3/boot.img      # boot는 fastboot로 된다(2.4 s)
for i in 0 1 2 3; do ~/e850-logs/fbmini flash userdata $D/part.simg.$i; done
~/e850-logs/fbmini reboot
```

- **`fastboot flash userdata`는 쓰지 않는다.** 굽기 전에 `getvar:is-userspace`를 보내는데, LK가 거기서 멈춘다. 멈추면 전원을 다시 넣어야 한다.
- 조각마다 걸린 시간은 5분 56초, 76초, 116초, 86초였다. 첫 조각(18,465청크)이 느린 것은 LK가 청크마다 UART에 한 줄을 찍기 때문으로 보인다.
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
- 웹과 카메라: `http://<대상>:8080/`이 200을 돌려주는지 본다. `/stats`의 `crc`, `link.short_bufs`, `coverage`는 원본과 같은 수준이어야 한다(CRC 0, 데이터 100%).

## 9. 막혔던 곳과 해결

| 증상 | 원인 / 해결 |
|---|---|
| `fastboot flash userdata`가 끝나지 않고 LK가 응답하지 않음 | `getvar:is-userspace`. 전원을 다시 넣는다. 그러면 새 boot로 Linux가 뜨는데, userdata가 아직 Android f2fs라 init이 ACM에 셸을 띄운다. 거기서 `to-fastboot`을 실행하고 7장의 fbmini로 굽는다 |
| 대상에서 MIPI CRC가 모든 줄에 나고 dvslive가 수십 초마다 재시작함 | CN4 카메라 케이블 접촉 문제였다. dvslive를 멈추고 → `systemctl poweroff` → 케이블을 다시 꽂고 → 전원을 넣었더니 CRC 0이 됐다. 그대로 두면 정지 경로에서 보드가 멈추고 워치독이 리셋한다 |
| 대상 UDC가 `not attached`(ACM 없음) | USB-C가 덜 꽂혔다. 다시 꽂으니 `configured`가 됐다. e850-vbus 경합(11.31)과는 다르다. 경합이면 dmesg에 `does not have any gadget`이 나온다 |
| ACL 경고(`Cannot acl_from_text`) | tar는 ACL을 사용자 이름으로 기록한다. `build-clone.sh`가 원본의 `/etc/passwd`, `/etc/group`을 컨테이너에 먼저 넣어 해결했다 |
| USB 네트워크로 받을 때 연결 시간 초과 | USB 네트워크의 보드→Mac 방향이 멈춰 있었다(11.52). Wi-Fi ssh 터널로 받는다(4장) |

## 10. 다음에 하면 좋을 것

- 파일시스템이 3 GiB이고 90%를 쓰고 있다. 파티션은 54 GiB다. 대상에서 `resize2fs /dev/mmcblk0p24`로 온라인 확장할 수 있다. 시험은 아직 안 했다.
- 두 보드를 동시에 USB로 쓰려면 한쪽의 gadget 주소와 일련번호를 바꿔야 한다. 아직 하지 않았다.
