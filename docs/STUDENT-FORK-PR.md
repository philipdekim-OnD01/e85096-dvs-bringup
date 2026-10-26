# 학생용: E850-96 DVS 프로젝트 Fork 및 Pull Request 실습

> 이 저장소는 교수님 공식 원본(Upstream)입니다. 각 학생은 자신의 계정에 **Fork**하고, **개인 개발 브랜치에서 수정 후 PR**을 제출합니다.

## 바로 시작하기

- [공식 원본 저장소](https://github.com/philipdekim-OnD01/e85096-dvs-bringup)
- [Fork 화면으로 이동](https://github.com/philipdekim-OnD01/e85096-dvs-bringup/fork)
- [병합 완료된 I2C/RESET 드라이버 소스](../modules/dvs/src/main.c)
- [Android → Linux 전환 및 설치 주의사항](E85096-ANDROID-TO-LINUX.md)
- [커널 모듈 빌드 안내](../modules/dvs/README.md)
- [Windows/WSL2 설치 및 Android/Linux 드라이버 개발](WINDOWS-WSL2-DRIVER-DEV.md)


## PC 운영체제 안내 — Windows를 지울 필요 없습니다

**학생 PC가 Windows여도 괜찮습니다.** Windows 11에 **WSL2 + Ubuntu**만 설치하면 Linux 개발 도구로 E850-96용 ARM64 드라이버 소스를 분석하고 해당 커널용 교차 빌드를 할 수 있습니다. 실제 보드의 Android를 Linux로 바꾸는 것은 **별도 선택 사항**입니다.

| PC 개발 환경 | 개발 가능한 대상 | 학생 권장 |
| --- | --- | --- |
| Windows 11 + WSL2(Ubuntu) | Linux용 E850 드라이버, Android용 **Linux 커널** 드라이버 | **기본 권장** |
| Windows만 사용 | Git/편집 및 Windows ADB/Fastboot 가능; 커널 빌드는 WSL2 또는 Linux 빌드 머신 권장 | 보조 |
| Ubuntu 네이티브 PC | 두 종류 모두, USB/UART 보드 연결에 유리 | 하드웨어 집중 실습 |

**핵심:** Android 커널도 Linux 커널이지만 **Linux 7.2.8의 모듈을 Android 4.14에 그대로 사용할 수 없습니다.** 실제 보드 OS/커널 버전에 맞는 소스와 툴체인이 필요합니다. 현 드라이버는 Linux 7.2.8에서 I2C/RESET만 시험된 상태입니다.

### Windows 11 → WSL2 Ubuntu 설치 방법 (PowerShell 관리자 권한)

~~~powershell
wsl --install -d Ubuntu
~~~

재부팅 후 Ubuntu를 실행하여 계정을 만들고, PowerShell에서 상태를 확인합니다.

~~~powershell
wsl --list --verbose
~~~

### Ubuntu(WSL2) 터미널에서 개발 도구 설치

~~~bash
sudo apt update
sudo apt install -y git build-essential make bc bison flex \
  libssl-dev libelf-dev device-tree-compiler \
  gcc-aarch64-linux-gnu binutils-aarch64-linux-gnu
~~~

이제 아래 **Fork → Clone → 브랜치 생성** 단계를 진행하면 됩니다. 보드 USB 연결이 필요하면 Windows 네이티브 ADB/Fastboot를 사용하거나 `usbipd-win`으로 WSL2에 전달합니다.

자세한 절차: **[Windows+WSL2 설치, ARM64 빌드, Android 커널 포팅, USB 연결 가이드](WINDOWS-WSL2-DRIVER-DEV.md)**


## GitHub 웹에서 Fork

1. GitHub 로그인 후 공식 저장소에 접속합니다.
2. 화면 오른쪽 상단의 **Fork**를 클릭하고 자신의 GitHub 계정을 선택합니다.
3. 생성된 `https://github.com/YOUR_GITHUB_ID/e85096-dvs-bringup` 저장소를 확인합니다.
4. **Fork 자체는 원본 저장소에 코드를 올리는 행위가 아닙니다.** 코드 공유는 PR로 합니다.

## Linux/WSL2 PC에 복제 및 원본 연결

다음 `YOUR_GITHUB_ID`는 자신의 GitHub 사용자명으로 바꿉니다.

~~~bash
git clone https://github.com/YOUR_GITHUB_ID/e85096-dvs-bringup.git
cd e85096-dvs-bringup
git remote add upstream https://github.com/philipdekim-OnD01/e85096-dvs-bringup.git
git remote -v
git switch -c feature/my-dvs-work
~~~

## 코드 수정 후 제출

~~~bash
git status
git diff
git add .
git commit -m "Describe my DVS improvement"
git push -u origin feature/my-dvs-work
~~~

개인 Fork 저장소의 브랜치 화면에서 **Compare & pull request**를 클릭합니다.
대상은 반드시 `philipdekim-OnD01/e85096-dvs-bringup`의 `main` 브랜치로 설정하고, 아래 내용을 PR 설명에 작성합니다.

- 목적과 변경 파일
- 사용한 호스트·보드·커널 버전
- 빌드 방법과 실행 결과/로그
- 검증한 부분 및 검증하지 못한 부분
- 보안/비공개 자료 포함 여부

## 원본 최신화

원본에 신규 PR이 병합되면, 개인 개발 브랜치를 시작하기 **전에** 자신의 Fork `main`을 최신화합니다.

~~~bash
git switch main
git fetch upstream
git merge --ff-only upstream/main
git push origin main
git switch -c feature/next-task
~~~

다른 작업이 진행 중인 브랜치에는 무작정 병합하지 말고 차이를 확인하세요.

## 2026-10-26 기준 프로젝트 상태

| 작업 영역 | 현재 상태 |
| --- | --- |
| E850-96 Device Tree / HSI2C2 | 공개 코드 구현 |
| I2C + RESET 커널 모듈 | 김민수 연구원 PR #1 병합, Linux 7.2.8 보드 시험 보고 |
| 0x303B 쓰기/읽기 | 시험 성공 보고 (`0x55`) |
| CHIP_ID, 완전한 센서 초기화 | 미검증 |
| MIPI CSI-2 / CSIS DMA 이벤트 수집 | 이 저장소에 완성 구현 없음 |
| 이벤트 디코더 | E850 완전 연동 미검증 |

**권장 분석 순서:** `dts/exynos850-e850-96-dvs.dtsi` → `modules/dvs/src/main.c` → `modules/dvs/README.md` → CSIS/DMA 공개 자료 조사.

### 실험 안전 및 공개 범위

- E850-96 보드의 OS 변경은 [전용 안내](E85096-ANDROID-TO-LINUX.md)를 먼저 읽고, 백업·복구 방법·전압을 확인한 후 담당자의 승인하에 진행합니다.
- **GitHub에 업로드할 수 있는 정보만 제출**합니다. 비공개 센서 레지스터 세부정보, 내부 코드·펌웨어, 키, NDA 자료 및 개인정보는 업로드하지 마세요.
- 이 프로젝트는 학습·연구용 초기 실험 코드이며 완성된 카메라/ISP-less DMA 드라이버로 오인하지 마세요.
