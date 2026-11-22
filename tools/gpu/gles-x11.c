/* GPU demo in an X window without DRI3 (11.82): Mesa panfrost renders a lit torus offscreen
 * (EGL surfaceless + FBO), glReadPixels copies each frame into an XImage, XPutImage shows it.
 * No MIT-SHM: this kernel has no SysV IPC, so shmget fails and XShmAttach gives BadAccess.
 * The fragment shader writes BGRA and the vertex shader flips y, so the bytes land in X's order as is.
 * Usage: gles-x11 [width height seconds [egl]]   (seconds 0 = run until killed)
 * Prints per-second fps and the time split render / readback / put; the window title shows fps.
 * "egl": no copy; EGL draws straight into the window (DRI3 + Present). Needs an X server with a GPU
 * (modesetting + glamor on e850-kms, 11.86); vblank_mode=0 in the environment turns off the vsync wait. */
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES2/gl2.h>
#include <string.h>
#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include "torus.h"

/* torus into the X window through an EGL window surface; prints fps and the time in eglSwapBuffers */
static int run_egl(Display *dpy, Window win, int W, int H, double secs) {
    PFNEGLGETPLATFORMDISPLAYEXTPROC gpd = (void *)eglGetProcAddress("eglGetPlatformDisplayEXT");
    EGLDisplay d = gpd(EGL_PLATFORM_X11_KHR, dpy, NULL);
    if (!eglInitialize(d, NULL, NULL)) { fprintf(stderr, "eglInitialize(x11) failed\n"); return 1; }
    eglBindAPI(EGL_OPENGL_ES_API);
    EGLint ca[] = { EGL_RED_SIZE, 8, EGL_GREEN_SIZE, 8, EGL_BLUE_SIZE, 8, EGL_DEPTH_SIZE, 16,
                    EGL_SURFACE_TYPE, EGL_WINDOW_BIT, EGL_RENDERABLE_TYPE, EGL_OPENGL_ES2_BIT, EGL_NONE };
    EGLConfig cfg; EGLint n = 0;
    if (!eglChooseConfig(d, ca, &cfg, 1, &n) || n < 1) { fprintf(stderr, "no EGL window config\n"); return 1; }
    EGLSurface s = eglCreateWindowSurface(d, cfg, (EGLNativeWindowType)win, NULL);
    EGLint cx[] = { EGL_CONTEXT_CLIENT_VERSION, 2, EGL_NONE };
    EGLContext c = eglCreateContext(d, cfg, EGL_NO_CONTEXT, cx);
    if (s == EGL_NO_SURFACE || !c || !eglMakeCurrent(d, s, s, c)) { fprintf(stderr, "EGL window surface failed 0x%x\n", eglGetError()); return 1; }
    printf("%s, %s, %dx%d, EGL window (%s)\n", glGetString(GL_RENDERER), glGetString(GL_VERSION), W, H, eglQueryString(d, EGL_VENDOR));
    glViewport(0, 0, W, H);
    torus_init(0, 0);
    double t0 = now(), tsec = t0, tr = 0, ts = 0;
    int frames = 0, total = 0;
    for (;;) {
        double s0 = now();
        if (secs > 0 && s0 - t0 > secs) break;
        torus_draw(s0 - t0, (float)W / H);
        double s1 = now();
        eglSwapBuffers(d, s);
        double s2 = now();
        tr += s1 - s0; ts += s2 - s1; frames++; total++;
        if (s2 - tsec >= 1) {
            char title[64];
            snprintf(title, sizeof title, "Mali-G52 (Panfrost, EGL) %.1f fps", frames / (s2 - tsec));
            XStoreName(dpy, win, title);
            printf("%.1f fps: draw %.1f ms, swap %.1f ms\n", frames / (s2 - tsec), 1e3 * tr / frames, 1e3 * ts / frames);
            fflush(stdout);
            frames = 0; tr = ts = 0; tsec = s2;
        }
    }
    printf("%d frames, %d triangles each\n", total, torus_nidx / 3);
    return 0;
}

int main(int argc, char **argv) {
    int W = argc > 2 ? atoi(argv[1]) : 1280, H = argc > 2 ? atoi(argv[2]) : 720;
    double secs = argc > 3 ? atof(argv[3]) : 20;

    Display *dpy = XOpenDisplay(NULL);
    if (!dpy) { fprintf(stderr, "no X display\n"); return 1; }
    int scr = DefaultScreen(dpy);
    Window win = XCreateSimpleWindow(dpy, RootWindow(dpy, scr), 0, 0, W, H, 0, 0, 0);
    XStoreName(dpy, win, "Mali-G52 (Panfrost)");
    XSelectInput(dpy, win, StructureNotifyMask);
    XMapWindow(dpy, win);
    for (XEvent e;;) { XNextEvent(dpy, &e); if (e.type == MapNotify) break; }
    if (argc > 4 && !strcmp(argv[4], "egl")) return run_egl(dpy, win, W, H, secs);
    XImage *img = XCreateImage(dpy, DefaultVisual(dpy, scr), DefaultDepth(dpy, scr), ZPixmap, 0, malloc(W * H * 4), W, H, 32, W * 4);
    if (!img || img->bits_per_pixel != 32) { fprintf(stderr, "need a 32 bpp visual\n"); return 1; }
    GC gc = DefaultGC(dpy, scr);

    PFNEGLGETPLATFORMDISPLAYEXTPROC gpd = (void *)eglGetProcAddress("eglGetPlatformDisplayEXT");
    EGLDisplay d = gpd(EGL_PLATFORM_SURFACELESS_MESA, EGL_DEFAULT_DISPLAY, NULL);
    if (!eglInitialize(d, NULL, NULL)) { fprintf(stderr, "eglInitialize failed\n"); return 1; }
    eglBindAPI(EGL_OPENGL_ES_API);
    EGLint ca[] = { EGL_CONTEXT_CLIENT_VERSION, 2, EGL_NONE };
    EGLContext c = eglCreateContext(d, EGL_NO_CONFIG_KHR, EGL_NO_CONTEXT, ca);
    if (!c || !eglMakeCurrent(d, EGL_NO_SURFACE, EGL_NO_SURFACE, c)) { fprintf(stderr, "context failed\n"); return 1; }
    printf("%s, %s, %dx%d\n", glGetString(GL_RENDERER), glGetString(GL_VERSION), W, H);

    GLuint tex, depth, fbo;
    glGenTextures(1, &tex); glBindTexture(GL_TEXTURE_2D, tex);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, W, H, 0, GL_RGBA, GL_UNSIGNED_BYTE, NULL);
    glGenRenderbuffers(1, &depth); glBindRenderbuffer(GL_RENDERBUFFER, depth);
    glRenderbufferStorage(GL_RENDERBUFFER, GL_DEPTH_COMPONENT16, W, H);
    glGenFramebuffers(1, &fbo); glBindFramebuffer(GL_FRAMEBUFFER, fbo);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, tex, 0);
    glFramebufferRenderbuffer(GL_FRAMEBUFFER, GL_DEPTH_ATTACHMENT, GL_RENDERBUFFER, depth);
    if (glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE) { fprintf(stderr, "fbo incomplete\n"); return 1; }
    glViewport(0, 0, W, H);
    torus_init(1, 1); /* BGRA bytes for X */

    double t0 = now(), tsec = t0, tr = 0, trb = 0, tp = 0;
    int frames = 0, total = 0;
    for (;;) {
        double t = now() - t0;
        if (secs > 0 && t > secs) break;
        double s0 = now();
        torus_draw(t, (float)W / H);
        glFinish();
        double s1 = now();
        glReadPixels(0, 0, W, H, GL_RGBA, GL_UNSIGNED_BYTE, img->data);
        double s2 = now();
        XPutImage(dpy, win, gc, img, 0, 0, 0, 0, W, H);
        XSync(dpy, False);
        double s3 = now();
        tr += s1 - s0; trb += s2 - s1; tp += s3 - s2; frames++; total++;
        if (s3 - tsec >= 1) {
            char title[64];
            snprintf(title, sizeof title, "Mali-G52 (Panfrost) %.1f fps", frames / (s3 - tsec));
            XStoreName(dpy, win, title);
            printf("%.1f fps: render %.1f ms, readback %.1f ms, put %.1f ms\n", frames / (s3 - tsec),
                   1e3 * tr / frames, 1e3 * trb / frames, 1e3 * tp / frames);
            fflush(stdout);
            frames = 0; tr = trb = tp = 0; tsec = s3;
        }
    }
    printf("%d frames, %d triangles each\n", total, torus_nidx / 3);
    XDestroyImage(img); XCloseDisplay(dpy);
    return 0;
}
