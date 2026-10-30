/* i2crd DEV ADDR REG16 COUNT  -- write 16-bit register address, then read COUNT bytes
 * (one I2C_RDWR transaction with repeated start; falls back to write+read). Freestanding: raw syscalls, no libc. */
typedef unsigned long u64; typedef unsigned short u16; typedef unsigned char u8;
struct i2c_msg { u16 addr, flags, len; u8 *buf; };
struct i2c_rdwr { struct i2c_msg *msgs; unsigned nmsgs; };

static long sc(long n, long a, long b, long c, long d) {
    register long x8 __asm__("x8") = n, x0 __asm__("x0") = a, x1 __asm__("x1") = b,
                  x2 __asm__("x2") = c, x3 __asm__("x3") = d;
    __asm__ volatile("svc 0" : "+r"(x0) : "r"(x8), "r"(x1), "r"(x2), "r"(x3) : "memory");
    return x0;
}
static long num(const char *s) {
    long v = 0, base = 10;
    if (s[0] == '0' && (s[1] == 'x' || s[1] == 'X')) { base = 16; s += 2; }
    for (; *s; s++) {
        int c = *s, d = c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10 :
                        c >= 'A' && c <= 'F' ? c - 'A' + 10 : -1;
        if (d < 0 || d >= base) return -1;
        v = v * base + d;
    }
    return v;
}
static char hx(int v) { return v < 10 ? '0' + v : 'a' + v - 10; }

__attribute__((used)) void cmain(long *sp) {
    long argc = sp[0]; char **argv = (char **)(sp + 1);
    long rc = 1;
    if (argc == 5) {
        long addr = num(argv[2]), reg = num(argv[3]), cnt = num(argv[4]);
        long fd = sc(56, -100, (long)argv[1], 2, 0);            /* openat O_RDWR */
        if (fd >= 0 && cnt > 0 && cnt <= 64) {
            u8 w[2], r[64];
            w[0] = (u8)(reg >> 8); w[1] = (u8)reg;
            struct i2c_msg m[2];
            m[0].addr = (u16)addr; m[0].flags = 0; m[0].len = 2; m[0].buf = w;
            m[1].addr = (u16)addr; m[1].flags = 1; m[1].len = (u16)cnt; m[1].buf = r;
            struct i2c_rdwr x; x.msgs = m; x.nmsgs = 2;
            long e = sc(29, fd, 0x0707, (long)&x, 0);               /* ioctl I2C_RDWR */
            if (e < 0) {                       /* fallback: I2C_SLAVE_FORCE, write address, then read */
                e = sc(29, fd, 0x0706, addr, 0);
                if (e >= 0) e = sc(64, fd, (long)w, 2, 0);
                if (e >= 0) e = sc(63, fd, (long)r, cnt, 0);
            }
            char out[64 * 3 + 1]; long n = 0;
            if (e >= 0) {
                for (long i = 0; i < cnt; i++) { out[n++] = hx(r[i] >> 4); out[n++] = hx(r[i] & 15); out[n++] = ' '; }
                rc = 0;
            } else { out[n++] = 'E'; out[n++] = 'R'; out[n++] = 'R'; out[n++] = ' '; e = -e;
                     out[n++] = hx((e / 10) % 10); out[n++] = hx(e % 10); }
            out[n++] = '\n';
            sc(64, 1, (long)out, n, 0);
        }
    }
    sc(93, rc, 0, 0, 0);
}
__asm__(".globl _start\n_start:\n mov x0, sp\n bl cmain\n");
