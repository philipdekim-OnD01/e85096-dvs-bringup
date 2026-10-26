# Windows PC에서 E850-96 Linux / Android 커널 드라이버 개발하기 (WSL2)

> 갱신: 2026-10-26. **학생 PC는 Windows 그대로 두고 WSL2(Ubuntu)로 개발할 수 있습니다.**
> **WSL2의 Ubuntu 설치와 E850-96 보드의 Android → Linux 교체는 서로 다른 작업**입니다.

## 1. 학생에게 권장하는 개발 환경

| 학생 PC | E850-96 보드 OS | 개발 방식 | 권장 |
| --- | --- | --- | --- |
| Windows 11 + WSL2(Ubuntu) | Linux | WSL2에서 ARM64 Linux용 DTS/커널 모듈 교차 빌드 → E850 보드 테스트 | **기본 권장** |
| Windows 11 + WSL2(Ubuntu) | Android | WSL2에서 **해당 Android 커널** 소스/툴체인으로 빌드 → Android E850에서 테스트 | 가능하나 별도 포팅/빌드 필요 |
| Windows 11 (WSL2 없음) | Android 또는 Linux | Git/소스 편집, Windows용 ADB·Fastboot 사용은 가능 | Linux 커널 빌드에는 보통 WSL2 또는 Linux 빌드 서버 권장 |
| Ubuntu 별도 설치 PC | Linux 또는 Android | 네이티브 Linux 개발·빌드, 직접 USB/UART 연결 | 하드웨어 실습이 많을 때 적합 |

- Android도 **Linux 커널**을 사용하므로 Android용 저수준 커널 드라이버는 Linux 커널 소스 트리를 대상으로 만듭니다.
- 그렇다고 Linux 7.2.8에서 빌드한 `dvs.ko`를 Android 4.14에 **그대로 복사하여 사용하는 것은 불가능**합니다. 커널 API, config, ABI, 툴체인 및 서명 요구가 다를 수 있습니다.
- E850-96 보드의 OS를 Linux로 바꿀 필요가 **코드 분석/Fork/WSL2 개발 단계에는 없습니다.** Linux 7.2.8 드라이버를 보드에서 검증하려면 그에 맞는 Linux 시스템이 필요합니다.
- 본 저장소의 `modules/dvs/`는 **I2C/RESET 검증**용입니다. Android 앱/JNI, MIPI CSI-2, CSIS DMA, userspace 이벤트 API는 포함하지 않습니다.

## 2. Windows 11에 WSL2 + Ubuntu 설치 (가장 쉬운 방법)

Windows **PowerShell을 관리자 권한**으로 실행합니다.

~~~powershell
wsl --install -d Ubuntu
~~~

필요 시 Windows를 재부팅하고 시작 메뉴에서 Ubuntu를 처음 실행하여 사용자 이름과 비밀번호를 만듭니다. PowerShell에서 설치 상태를 확인합니다.

~~~powershell
wsl --list --verbose
wsl --update
~~~

설치된 Ubuntu의 VERSION이 `2`인지 확인합니다. WSL1이면 (배포판 이름을 설치 출력에 맞게 변경):

~~~powershell
wsl --set-version Ubuntu 2
~~~

Microsoft 공식 설치 문서: https://learn.microsoft.com/ko-kr/windows/wsl/install

## 3. Ubuntu(WSL2) 내부에서 개발 도구 설치

**아래 명령은 Windows PowerShell이 아닌 Ubuntu 터미널에서 실행**합니다.

~~~bash
sudo apt update
sudo apt install -y \
  git build-essential cmake make \
  bc bison flex libssl-dev libelf-dev \
  device-tree-compiler pkg-config python3 \
  gcc-aarch64-linux-gnu binutils-aarch64-linux-gnu \
  adb fastboot usbutils
~~~

확인:

~~~bash
uname -a
git --version
aarch64-linux-gnu-gcc --version
~~~

**중요:** 위 `uname -a`는 WSL2 **호스트 Linux 커널**을 출력합니다. E850-96의 커널 버전이 아닙니다. E850 보드에서 별도로 `uname -r`을 실행하여 확인하세요.

Git 저장소는 가급적 WSL2의 Linux 홈 디렉터리(`~/`)에 저장합니다. 소스 편집은 VS Code의 WSL 연동 등을 사용해도 됩니다.

## 4. 학생 Fork → Clone → 개발 브랜치

먼저 다음 GitHub 저장소에서 **Fork**를 누릅니다.

- 공식: https://github.com/philipdekim-OnD01/e85096-dvs-bringup
- 직접 Fork: https://github.com/philipdekim-OnD01/e85096-dvs-bringup/fork

Ubuntu 터미널에서 `YOUR_GITHUB_ID`를 본인 GitHub 사용자명으로 바꿉니다.

~~~bash
cd ~
git clone https://github.com/YOUR_GITHUB_ID/e85096-dvs-bringup.git
cd e85096-dvs-bringup
git remote add upstream https://github.com/philipdekim-OnD01/e85096-dvs-bringup.git
git switch -c feature/my-dvs-work
~~~

분석할 소스:

- `dts/exynos850-e850-96-dvs.dtsi`: HSI2C2, DVS 노드, RESET GPIO.
- `modules/dvs/src/main.c`: I2C/RESET 커널 드라이버.
- `modules/dvs/README.md`: 빌드 전제와 실제 검증 범위.

## 5. Linux 7.2.8을 실행하는 E850-96용으로 빌드

**커널 모듈은 반드시 보드가 실제 실행할 커널과 일치하는 준비된 소스/빌드 트리로 빌드**합니다. Ubuntu/WSL2에 있는 `/lib/modules/$(uname -r)/build`는 E850용이 아니므로 **사용하면 안 됩니다.**

E850 Linux 7.2.8에 대응하는 소스, 보드용 커널 빌드 결과, 구성 파일, `Module.symvers`가 준비된 경우에만 다음 예시를 사용합니다(경로는 각자 환경에 맞게 변경).

~~~bash
cd ~/e85096-dvs-bringup

make -C modules/dvs \
  KDIR=/absolute/path/to/e850-linux-kernel-build \
  KERNEL_SOURCE=/absolute/path/to/e850-linux-kernel-source \
  CROSS_COMPILE=aarch64-linux-gnu-
~~~

이 저장소 `modules/dvs/Makefile`은 대상 커널의 Kbuild 및 `scripts/clang-tools/gen_compile_commands.py`를 요구합니다. 툴체인/커널 빌드 구성이 연구원 제공 환경과 다르면 빌드 방식부터 맞춰야 합니다.

빌드가 성공했다고 하더라도 **보드에서 로드·실행을 검증한 것은 아닙니다**. 실제 센서 리셋 전압/전원, DTB, 대상 커널 `uname -r` 등을 먼저 확인하고 테스트하세요.

## 6. Android를 유지한 E850-96에서 개발하려면?

**Android 앱을 개발하는 것과 Android Linux 커널 드라이버를 개발하는 것은 다른 작업입니다.**

1. E850-96 Android 환경에서 커널 버전과 상태를 확인합니다.

   ~~~bash
   adb devices
   adb shell uname -a
   adb shell getprop ro.build.version.release
   ~~~

2. 현재 설치된 Android BSP와 **정확히 일치하는** Exynos850 커널 소스/defconfig/DTB/툴체인을 준비합니다. 초기 참조 브랜치는 Linaro의 `android-exynos-4.14-linaro`입니다(실제 보드 이미지와 버전이 일치하는지는 별도 확인).

   https://gitlab.com/Linaro/96boards/e850-96/kernel

3. 기존 Linux 7.2.8 드라이버의 I2C/RESET 동작을 **참고 자료로** 사용하되, Android 커널의 API·Device Tree·Kbuild 인터페이스로 포팅해야 합니다. **현 소스의 Android 4.14 컴파일/실행은 검증되지 않았습니다.**
4. 대상 커널에 맞는 `dvs.ko` 또는 커널 이미지/DTB를 빌드하고, 해당 BSP의 안전한 배포·플래싱 방식과 복구 절차를 확인합니다.
5. 실제 보드에서 I2C, GPIO, 커널 로그 등을 검증합니다. Android 서비스/앱과 데이터를 주고받는 경우 그 후 Native C/C++ 또는 JNI/앱 구조를 설계합니다. **현재 I2C/RESET 모듈은 이벤트 데이터를 제공하는 사용자 API를 구현하지 않습니다.**

요약: **Windows + WSL2에서도 Android 커널 드라이버를 개발할 수 있지만, Android용 커널 소스/툴체인으로 다시 빌드하고 검증해야 합니다.**

## 7. Windows PC에서 E850-96 USB/ADB/Fastboot 연결

두 가지 접근법 중 **하나를 선택**합니다.

### A. 간편: Windows 네이티브 ADB·Fastboot 사용

Windows용 Android Platform-Tools(ADB/Fastboot)를 설치하고 Windows PowerShell에서:

~~~powershell
adb devices
fastboot devices
~~~

보드가 해당 모드인지, 드라이버/USB 케이블이 정상인지 확인합니다. 이 방법은 **WSL2로 USB를 전달하지 않아도** Windows에서 기기를 다룰 수 있습니다.

### B. WSL2 Ubuntu에서 직접 USB 사용: usbipd-win

WSL2는 USB 장치를 자동으로 직접 제어하지 않습니다. Microsoft 지침을 참고하여 Windows에 `usbipd-win`을 설치한 뒤 전달합니다.

Windows PowerShell(관리자):

~~~powershell
winget install --interactive --exact dorssel.usbipd-win
usbipd list
usbipd bind --busid <BUSID>
~~~

`<BUSID>`는 `usbipd list`에 보이는 **실제** 연결 장치 번호로 바꿉니다. WSL2 Ubuntu 터미널을 켜 둔 상태에서 Windows PowerShell에서:

~~~powershell
usbipd attach --wsl --busid <BUSID>
~~~

WSL2 Ubuntu 터미널:

~~~bash
lsusb
adb devices
fastboot devices
~~~

연결이 끝난 후 Windows PowerShell에서:

~~~powershell
usbipd detach --busid <BUSID>
~~~

**주의:** USB가 WSL2에 연결된 동안은 Windows 쪽에서 같은 USB 장치를 동시에 사용할 수 없습니다. 장치가 Android/bootloader/fastboot 모드로 바뀌면서 USB 장치 목록이 바뀔 수 있으므로 필요 시 재연결해야 합니다. `usbipd` 전달은 모든 보드/모드를 보장하지 않으며 UART/JTAG 장치는 별도 설정이 필요할 수 있습니다.

Microsoft USB 연결 문서: https://learn.microsoft.com/en-us/windows/wsl/connect-usb

## 8. 학생 과제 및 제출 기준

1. **PC 환경 선택**: Windows+WSL2(Ubuntu) 기본 권장, Ubuntu 네이티브도 가능.
2. **보드 OS 목표 명시**: Linux 7.2.8 또는 Android BSP, 빌드 대상 혼동 금지.
3. **Fork와 기능 브랜치 생성**: 원본 `main`을 직접 수정하지 않음.
4. **I2C/RESET 및 DTS 분석**: 검증된 범위와 미검증 범위를 명확히 작성.
5. **실습 결과 제출**: 컴파일 로그, 테스트 커맨드, `uname -r`, 변경 사항.
6. **Pull Request 제출**: 공식 저장소 `main`을 대상으로 PR.

보드의 Android를 Linux로 바꾸는 작업은 **[별도 가이드](E85096-ANDROID-TO-LINUX.md)**에 있으며, 부트로더/eMMC 쓰기는 데이터 삭제와 복구 위험이 있으므로 일반 코드 분석 과제의 필수 단계가 아닙니다.

**공개 범위**: 소스/로그/이미지의 배포 권한을 확인하고 비공개 레지스터 자료, 키, 내부 BSP, NDA 자료, 개인정보를 업로드하지 마세요.
