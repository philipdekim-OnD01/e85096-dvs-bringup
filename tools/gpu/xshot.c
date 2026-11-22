/* Screenshot of an X screen as binary PPM on stdout (the board has no xwd/import; fb0 does not show
 * the e850-kms buffers, 11.87). Usage: DISPLAY=:1 xshot > shot.ppm
 * Build (tools/gpu image, arm64 Debian 12): gcc -O2 xshot.c -lX11 -o xshot */
#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <stdio.h>

int main(void) {
    Display *d = XOpenDisplay(NULL);
    if (!d) { fprintf(stderr, "xshot: cannot open DISPLAY\n"); return 1; }
    int s = DefaultScreen(d), W = DisplayWidth(d, s), H = DisplayHeight(d, s);
    XImage *img = XGetImage(d, RootWindow(d, s), 0, 0, W, H, AllPlanes, ZPixmap);
    if (!img) { fprintf(stderr, "xshot: XGetImage failed\n"); return 1; }
    printf("P6\n%d %d\n255\n", W, H);
    for (int y = 0; y < H; y++)
        for (int x = 0; x < W; x++) {
            unsigned long p = XGetPixel(img, x, y);
            putchar(p >> 16 & 255); putchar(p >> 8 & 255); putchar(p & 255);
        }
    return 0;
}
