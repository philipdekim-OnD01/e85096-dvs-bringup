/* Set the X root window background to a raw BGRX image of the screen's size (wallpaper.py makes
 * one). The board has no feh/hsetroot. Usage: xbg file.bgrx
 * Build (tools/gpu image, arm64 Debian 12): gcc -O2 xbg.c -lX11 -o xbg */
#include <X11/Xlib.h>
#include <stdio.h>
#include <stdlib.h>

int main(int argc, char **argv) {
    Display *d = XOpenDisplay(NULL);
    if (argc < 2 || !d) { fprintf(stderr, "usage: xbg file.bgrx (needs DISPLAY)\n"); return 1; }
    int s = DefaultScreen(d), W = DisplayWidth(d, s), H = DisplayHeight(d, s);
    size_t n = (size_t)W * H;
    char *px = malloc(n * 4);
    FILE *f = fopen(argv[1], "rb");
    if (!f || fread(px, 4, n, f) != n) { fprintf(stderr, "%s: need %dx%d BGRX bytes\n", argv[1], W, H); return 1; }
    XImage *img = XCreateImage(d, DefaultVisual(d, s), DefaultDepth(d, s), ZPixmap, 0, px, W, H, 32, W * 4);
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
