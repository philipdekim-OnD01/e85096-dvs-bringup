/* Set the X root window background to a raw BGRX image (wallpaper.py makes a 1920x1080 one), scaled to the
 * screen's size by the nearest pixel: e850-res can make the screen 2560x1440 or 1280x720 (11.103). The board
 * has no feh/hsetroot. Usage: xbg file.bgrx [width height]   (the file's size, default 1920 1080)
 * Build (tools/gpu image, arm64 Debian 12): gcc -O2 xbg.c -lX11 -o xbg */
#include <X11/Xlib.h>
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>

int main(int argc, char **argv) {
    Display *d = XOpenDisplay(NULL);
    if (argc < 2 || !d) { fprintf(stderr, "usage: xbg file.bgrx (needs DISPLAY)\n"); return 1; }
    int s = DefaultScreen(d), W = DisplayWidth(d, s), H = DisplayHeight(d, s);
    int sw = argc > 3 ? atoi(argv[2]) : 1920, sh = argc > 3 ? atoi(argv[3]) : 1080;
    size_t n = (size_t)sw * sh;
    uint32_t *src = malloc(n * 4), *px = malloc((size_t)W * H * 4);
    FILE *f = fopen(argv[1], "rb");
    if (sw <= 0 || sh <= 0 || !src || !px || !f || fread(src, 4, n, f) != n) {
        fprintf(stderr, "%s: need %dx%d BGRX bytes\n", argv[1], sw, sh);
        return 1;
    }
    for (int y = 0; y < H; y++) {
        const uint32_t *r = src + (size_t)((int64_t)y * sh / H) * sw;
        for (int x = 0; x < W; x++) px[(size_t)y * W + x] = r[(int64_t)x * sw / W];
    }
    XImage *img = XCreateImage(d, DefaultVisual(d, s), DefaultDepth(d, s), ZPixmap, 0, (char *)px, W, H, 32, W * 4);
    if (!img || img->bits_per_pixel != 32) { fprintf(stderr, "need a 32 bpp visual\n"); return 1; }
    Window root = RootWindow(d, s);
    Pixmap p = XCreatePixmap(d, root, W, H, DefaultDepth(d, s));
    XPutImage(d, p, DefaultGC(d, s), img, 0, 0, 0, 0, W, H);
    XSetWindowBackgroundPixmap(d, root, p);
    XFreePixmap(d, p); /* the root window keeps its own reference */
    XClearWindow(d, root);
    XCloseDisplay(d);
    return 0;
}
