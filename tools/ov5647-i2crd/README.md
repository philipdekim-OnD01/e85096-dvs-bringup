# i2crd — 16비트 레지스터 주소 I²C 읽기 도구

OV5647처럼 **레지스터 주소가 16비트**인 센서를 Android 셸에서 읽기 위한 작은 정적 바이너리다.
Android 10 보드의 toybox `i2cget`은 8비트 레지스터 주소만 받기 때문에 OV5647의 `0x300A` 같은 주소를 읽을 수 없고,
`i2ctransfer`도 설치되어 있지 않다.

```text
i2crd DEV ADDR REG16 COUNT
  예) i2crd /dev/i2c-8 0x36 0x300a 2   →   56 47
```

- `I2C_RDWR`(repeated start)로 [REG 상위, REG 하위]를 쓰고 COUNT 바이트를 읽는다.
- `I2C_RDWR`가 실패하면 `I2C_SLAVE_FORCE` + `write()` + `read()`로 다시 시도한다.
  E850-96 Android 10(4.14)에서는 `I2C_RDWR`가 `EFAULT(14)`로 실패해 이 대체 경로로 읽혔다.
- 오류 시 `ERR <errno>`를 출력하고 종료 코드 1을 돌려준다.
- 레지스터 **쓰기 기능은 없다.** 읽기만 한다.

## 빌드 (macOS, Apple clang만 사용)

크로스 컴파일러나 ELF 링커가 없어도 된다. clang으로 aarch64 ELF 오브젝트를 만들고,
`link.py`가 `.text`를 정적 실행 파일로 감싼다(libc 없이 시스템 콜을 직접 호출).

```bash
tools/ov5647-i2crd/build.sh
# i2crd: ELF 64-bit LSB executable, ARM aarch64, ... statically linked
```

Linux/WSL2에서는 `aarch64-linux-gnu-gcc -static -nostdlib -ffreestanding -O2 i2crd.c -o i2crd`로도 빌드할 수 있다(미시험).

## 보드에 올려 실행

```bash
adb push tools/ov5647-i2crd/i2crd /data/local/tmp/i2crd
adb shell chmod 755 /data/local/tmp/i2crd
adb shell /data/local/tmp/i2crd /dev/i2c-8 0x36 0x300a 2
```

저장소에 들어 있는 `i2crd`는 위 `build.sh`로 만든 것과 바이트 단위로 같다.
