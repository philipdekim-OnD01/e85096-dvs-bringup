/* KMS check for modules/e850-kms (11.85). Needs /dev/dri/card0 and no other DRM master.
 *   gles-kms dumb [seconds]: kernel only. Two dumb buffers with colour bars, the CPU moves a white
 *     square in the back one and page-flips (vsync event) every frame.
 *   gles-kms [seconds]: GPU through GBM (kmscube pattern). Mesa kmsro allocates the surface buffers
 *     as dumb buffers on card0 and imports them into panfrost (kbase shim); the torus is drawn into
 *     an EGL window surface and each frame is page-flipped.
 * When it exits its framebuffers go away, the pipe is disabled and fb0 (console or fbdev X) returns.
 * Prints per-second fps and draw / flip time. */
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES2/gl2.h>
#include <errno.h>
#include <fcntl.h>
#include <gbm.h>
#include <poll.h>
#include <stdint.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#include <xf86drm.h>
#include <xf86drmMode.h>
#include "torus.h"

static int fd, flip_pending, crtc_set;
static uint32_t crtc_id, conn_id;
static drmModeModeInfo mode;

static void flip_done(int f, unsigned int seq, unsigned int s, unsigned int us, void *data) { flip_pending = 0; }

static void find_output(void) {
    drmModeRes *r = drmModeGetResources(fd);
    if (!r || r->count_crtcs < 1) { fprintf(stderr, "no KMS resources (is e850-kms loaded?)\n"); exit(1); }
    for (int i = 0; i < r->count_connectors && !conn_id; i++) {
        drmModeConnector *c = drmModeGetConnector(fd, r->connectors[i]);
        if (c && c->connection == DRM_MODE_CONNECTED && c->count_modes) { conn_id = c->connector_id; mode = c->modes[0]; }
        drmModeFreeConnector(c);
    }
    if (!conn_id) { fprintf(stderr, "no connected connector\n"); exit(1); }
    crtc_id = r->crtcs[0];
    drmModeFreeResources(r);
    printf("connector %u crtc %u mode %s %dx%d@%d\n", conn_id, crtc_id, mode.name, mode.hdisplay, mode.vdisplay, mode.vrefresh);
}

/* show fb: the first time with SetCrtc, then a page flip; returns once the flip is done */
static void show(uint32_t fb) {
    if (!crtc_set) {
        if (drmModeSetCrtc(fd, crtc_id, fb, 0, 0, &conn_id, 1, &mode)) { perror("drmModeSetCrtc"); exit(1); }
        crtc_set = 1;
        return;
    }
    if (drmModePageFlip(fd, crtc_id, fb, DRM_MODE_PAGE_FLIP_EVENT, NULL)) { perror("drmModePageFlip"); exit(1); }
    drmEventContext ev = { .version = 2, .page_flip_handler = flip_done };
    for (flip_pending = 1; flip_pending;) {
        struct pollfd p = { .fd = fd, .events = POLLIN };
        if (poll(&p, 1, 1000) != 1) { fprintf(stderr, "no flip event in 1 s\n"); exit(1); }
        drmHandleEvent(fd, &ev);
    }
}

static void report(double *tsec, int *frames, double *td, double *tf, double s2) {
    if (s2 - *tsec < 1) return;
    printf("%.1f fps: draw %.1f ms, flip %.1f ms\n", *frames / (s2 - *tsec), 1e3 * *td / *frames, 1e3 * *tf / *frames);
    fflush(stdout);
    *frames = 0; *td = *tf = 0; *tsec = s2;
}

static const uint32_t bars[8] = { 0xffffff, 0xffff00, 0x00ffff, 0x00ff00, 0xff00ff, 0xff0000, 0x0000ff, 0x202020 };

static int run_dumb(double secs) {
    int W = mode.hdisplay, H = mode.vdisplay, S = 64;
    uint32_t fb[2], *px[2], pitch = 0;
    int sq[2] = { -1, -1 };
    for (int i = 0; i < 2; i++) {
        struct drm_mode_create_dumb c = { .width = W, .height = H, .bpp = 32 };
        if (drmIoctl(fd, DRM_IOCTL_MODE_CREATE_DUMB, &c)) { perror("CREATE_DUMB"); return 1; }
        struct drm_mode_map_dumb m = { .handle = c.handle };
        if (drmIoctl(fd, DRM_IOCTL_MODE_MAP_DUMB, &m)) { perror("MAP_DUMB"); return 1; }
        px[i] = mmap(NULL, c.size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, m.offset);
        if (px[i] == MAP_FAILED) { perror("mmap dumb"); return 1; }
        pitch = c.pitch;
        for (int y = 0; y < H; y++) for (int x = 0; x < W; x++) px[i][y * (pitch / 4) + x] = bars[x * 8 / W];
        if (drmModeAddFB(fd, W, H, 24, 32, pitch, c.handle, &fb[i])) { perror("drmModeAddFB"); return 1; }
    }
    printf("dumb %dx%d pitch %u\n", W, H, pitch);
    double t0 = now(), tsec = t0, td = 0, tf = 0;
    int frames = 0, total = 0, back = 0;
    for (;;) {
        double s0 = now();
        if (secs > 0 && s0 - t0 > secs) break;
        uint32_t *p = px[back], stride = pitch / 4;
        int y0 = H / 2 - S / 2, x = (int)((s0 - t0) * 400) % (W - S);
        if (sq[back] >= 0)  /* put the bars back where this buffer had its square */
            for (int y = y0; y < y0 + S; y++) for (int i = sq[back]; i < sq[back] + S; i++) p[y * stride + i] = bars[i * 8 / W];
        for (int y = y0; y < y0 + S; y++) for (int i = x; i < x + S; i++) p[y * stride + i] = 0xffffff;
        sq[back] = x;
        double s1 = now();
        show(fb[back]);
        double s2 = now();
        back ^= 1; td += s1 - s0; tf += s2 - s1; frames++; total++;
        report(&tsec, &frames, &td, &tf, s2);
    }
    printf("%d frames\n", total);
    return 0;
}

static uint32_t fb_for_bo(struct gbm_bo *bo) {
    uint32_t *id = gbm_bo_get_user_data(bo);
    if (id) return *id;
    id = malloc(sizeof *id);
    if (drmModeAddFB(fd, gbm_bo_get_width(bo), gbm_bo_get_height(bo), 24, 32, gbm_bo_get_stride(bo),
                     gbm_bo_get_handle(bo).u32, id)) { perror("drmModeAddFB(gbm bo)"); exit(1); }
    gbm_bo_set_user_data(bo, id, NULL);
    return *id;
}

static int run_gpu(double secs) {
    int W = mode.hdisplay, H = mode.vdisplay;
    struct gbm_device *gbm = gbm_create_device(fd);
    if (!gbm) { fprintf(stderr, "gbm_create_device failed\n"); return 1; }
    struct gbm_surface *gs = gbm_surface_create(gbm, W, H, GBM_FORMAT_XRGB8888, GBM_BO_USE_SCANOUT | GBM_BO_USE_RENDERING);
    if (!gs) { fprintf(stderr, "gbm_surface_create failed\n"); return 1; }
    PFNEGLGETPLATFORMDISPLAYEXTPROC gpd = (void *)eglGetProcAddress("eglGetPlatformDisplayEXT");
    EGLDisplay d = gpd(EGL_PLATFORM_GBM_KHR, gbm, NULL);
    if (!eglInitialize(d, NULL, NULL)) { fprintf(stderr, "eglInitialize(gbm) failed\n"); return 1; }
    eglBindAPI(EGL_OPENGL_ES_API);
    EGLint ca[] = { EGL_RED_SIZE, 8, EGL_GREEN_SIZE, 8, EGL_BLUE_SIZE, 8, EGL_DEPTH_SIZE, 16,
                    EGL_SURFACE_TYPE, EGL_WINDOW_BIT, EGL_RENDERABLE_TYPE, EGL_OPENGL_ES2_BIT, EGL_NONE };
    EGLConfig cfgs[64], cfg = NULL;
    EGLint n = 0;
    eglChooseConfig(d, ca, cfgs, 64, &n);
    for (int i = 0; i < n && !cfg; i++) {
        EGLint vid; eglGetConfigAttrib(d, cfgs[i], EGL_NATIVE_VISUAL_ID, &vid);
        if (vid == GBM_FORMAT_XRGB8888) cfg = cfgs[i];
    }
    if (!cfg) { fprintf(stderr, "no XRGB8888 EGL config\n"); return 1; }
    EGLSurface s = eglCreateWindowSurface(d, cfg, (EGLNativeWindowType)gs, NULL);
    EGLint cx[] = { EGL_CONTEXT_CLIENT_VERSION, 2, EGL_NONE };
    EGLContext c = eglCreateContext(d, cfg, EGL_NO_CONTEXT, cx);
    if (s == EGL_NO_SURFACE || !c || !eglMakeCurrent(d, s, s, c)) { fprintf(stderr, "EGL surface/context failed 0x%x\n", eglGetError()); return 1; }
    printf("%s, %s, gbm %s\n", glGetString(GL_RENDERER), glGetString(GL_VERSION), gbm_device_get_backend_name(gbm));
    glViewport(0, 0, W, H);
    torus_init(0, 0);
    double t0 = now(), tsec = t0, td = 0, tf = 0;
    int frames = 0, total = 0;
    struct gbm_bo *prev = NULL;
    for (;;) {
        double s0 = now();
        if (secs > 0 && s0 - t0 > secs) break;
        torus_draw(s0 - t0, (float)W / H);
        eglSwapBuffers(d, s);
        struct gbm_bo *bo = gbm_surface_lock_front_buffer(gs);
        if (!bo) { fprintf(stderr, "lock_front_buffer failed\n"); return 1; }
        double s1 = now();
        show(fb_for_bo(bo));
        double s2 = now();
        if (prev) gbm_surface_release_buffer(gs, prev);
        prev = bo; td += s1 - s0; tf += s2 - s1; frames++; total++;
        report(&tsec, &frames, &td, &tf, s2);
    }
    printf("%d frames, %d triangles each\n", total, torus_nidx / 3);
    return 0;
}

int main(int argc, char **argv) {
    int dumb = argc > 1 && !strcmp(argv[1], "dumb");
    double secs = argc > 1 + dumb ? atof(argv[1 + dumb]) : 10;
    fd = open("/dev/dri/card0", O_RDWR | O_CLOEXEC);
    if (fd < 0) { perror("/dev/dri/card0"); return 1; }
    find_output();
    return dumb ? run_dumb(secs) : run_gpu(secs);
}
