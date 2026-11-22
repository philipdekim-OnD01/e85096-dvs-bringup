/* GPU straight into the HDMI framebuffer, no copy and no X (11.84). /dev/e850-fbbuf
 * (modules/e850-fbbuf) gives fb0's buffer as a dma-buf; the shim imports it into kbase, EGL wraps
 * it as an EGLImage (BGRX, fb0's pitch) and the torus is rendered into it through an FBO.
 * Single buffer (default): draws into the visible screen (tears; X keeps drawing there too).
 * "double": draws into the half that is not shown and flips with FBIOPAN_DISPLAY (yoffset); this
 * sets yres_virtual to 2 * yres (the DECON allocation is that big) and puts it back on exit.
 * Usage: gles-fb [seconds [double]]   Prints per-second fps and render / pan time. */
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES2/gl2.h>
#include <GLES2/gl2ext.h>
#include <fcntl.h>
#include <linux/fb.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>
#include "torus.h"

#define E850_FBBUF_GET_FD _IO('F', 0xe8)
#define FOURCC_XRGB8888 0x34325258 /* 'XR24': B, G, R, X bytes */

int main(int argc, char **argv) {
    double secs = argc > 1 ? atof(argv[1]) : 20;
    int nbuf = argc > 2 && !strcmp(argv[2], "double") ? 2 : 1;

    int fb = open("/dev/fb0", O_RDWR), fbb = open("/dev/e850-fbbuf", O_RDWR);
    if (fb < 0 || fbb < 0) { perror("open /dev/fb0 or /dev/e850-fbbuf"); return 1; }
    struct fb_var_screeninfo var, var0; struct fb_fix_screeninfo fix;
    if (ioctl(fb, FBIOGET_VSCREENINFO, &var) || ioctl(fb, FBIOGET_FSCREENINFO, &fix)) { perror("fb info"); return 1; }
    var0 = var;
    int W = var.xres, H = var.yres, pitch = fix.line_length;
    if (var.bits_per_pixel != 32) { fprintf(stderr, "fb0 is %d bpp, need 32\n", var.bits_per_pixel); return 1; }
    if ((size_t)pitch * H * nbuf > fix.smem_len) { fprintf(stderr, "fb0 memory %u too small\n", fix.smem_len); return 1; }
    if (nbuf == 2 && var.yres_virtual < 2 * var.yres) {
        var.yres_virtual = 2 * var.yres; var.yoffset = 0;
        if (ioctl(fb, FBIOPUT_VSCREENINFO, &var)) { perror("FBIOPUT_VSCREENINFO yres_virtual"); return 1; }
    }
    int dmabuf = ioctl(fbb, E850_FBBUF_GET_FD);
    if (dmabuf < 0) { perror("E850_FBBUF_GET_FD"); return 1; }

    PFNEGLGETPLATFORMDISPLAYEXTPROC gpd = (void *)eglGetProcAddress("eglGetPlatformDisplayEXT");
    PFNEGLCREATEIMAGEKHRPROC create_image = (void *)eglGetProcAddress("eglCreateImageKHR");
    PFNGLEGLIMAGETARGETTEXTURE2DOESPROC image_target = (void *)eglGetProcAddress("glEGLImageTargetTexture2DOES");
    EGLDisplay d = gpd(EGL_PLATFORM_SURFACELESS_MESA, EGL_DEFAULT_DISPLAY, NULL);
    if (!eglInitialize(d, NULL, NULL)) { fprintf(stderr, "eglInitialize failed\n"); return 1; }
    if (!strstr(eglQueryString(d, EGL_EXTENSIONS), "EGL_EXT_image_dma_buf_import")) { fprintf(stderr, "no EGL_EXT_image_dma_buf_import\n"); return 1; }
    eglBindAPI(EGL_OPENGL_ES_API);
    EGLint ca[] = { EGL_CONTEXT_CLIENT_VERSION, 2, EGL_NONE };
    EGLContext c = eglCreateContext(d, EGL_NO_CONFIG_KHR, EGL_NO_CONTEXT, ca);
    if (!c || !eglMakeCurrent(d, EGL_NO_SURFACE, EGL_NO_SURFACE, c)) { fprintf(stderr, "context failed\n"); return 1; }
    printf("%s, %s, fb0 %dx%d pitch %d, %d buffer(s)\n", glGetString(GL_RENDERER), glGetString(GL_VERSION), W, H, pitch, nbuf);

    GLuint tex[2], fbo[2], depth;
    glGenTextures(nbuf, tex); glGenFramebuffers(nbuf, fbo); glGenRenderbuffers(1, &depth);
    glBindRenderbuffer(GL_RENDERBUFFER, depth);
    glRenderbufferStorage(GL_RENDERBUFFER, GL_DEPTH_COMPONENT16, W, H);
    for (int i = 0; i < nbuf; i++) {
        EGLint ia[] = { EGL_WIDTH, W, EGL_HEIGHT, H, EGL_LINUX_DRM_FOURCC_EXT, FOURCC_XRGB8888,
                        EGL_DMA_BUF_PLANE0_FD_EXT, dmabuf, EGL_DMA_BUF_PLANE0_OFFSET_EXT, i * H * pitch,
                        EGL_DMA_BUF_PLANE0_PITCH_EXT, pitch, EGL_NONE };
        EGLImageKHR img = create_image(d, EGL_NO_CONTEXT, EGL_LINUX_DMA_BUF_EXT, NULL, ia);
        if (img == EGL_NO_IMAGE_KHR) { fprintf(stderr, "eglCreateImageKHR(dma-buf) failed: 0x%x\n", eglGetError()); return 1; }
        glBindTexture(GL_TEXTURE_2D, tex[i]); image_target(GL_TEXTURE_2D, img);
        glBindFramebuffer(GL_FRAMEBUFFER, fbo[i]);
        glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, tex[i], 0);
        glFramebufferRenderbuffer(GL_FRAMEBUFFER, GL_DEPTH_ATTACHMENT, GL_RENDERBUFFER, depth);
        if (glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE) { fprintf(stderr, "fbo %d incomplete\n", i); return 1; }
    }
    glViewport(0, 0, W, H);
    torus_init(0, 1);

    double t0 = now(), tsec = t0, tr = 0, tpan = 0;
    int frames = 0, total = 0, back = nbuf - 1;
    for (;;) {
        double t = now() - t0;
        if (secs > 0 && t > secs) break;
        double s0 = now();
        glBindFramebuffer(GL_FRAMEBUFFER, fbo[back]);
        torus_draw(t, (float)W / H);
        glFinish();
        double s1 = now();
        if (nbuf == 2) {
            var.yoffset = back * H;
            if (ioctl(fb, FBIOPAN_DISPLAY, &var)) { perror("FBIOPAN_DISPLAY"); break; }
            back ^= 1;
        }
        double s2 = now();
        tr += s1 - s0; tpan += s2 - s1; frames++; total++;
        if (s2 - tsec >= 1) {
            printf("%.1f fps: render %.1f ms, pan %.1f ms\n", frames / (s2 - tsec), 1e3 * tr / frames, 1e3 * tpan / frames);
            fflush(stdout);
            frames = 0; tr = tpan = 0; tsec = s2;
        }
    }
    printf("%d frames, %d triangles each\n", total, torus_nidx / 3);
    if (nbuf == 2) {
        var.yoffset = 0;
        if (ioctl(fb, FBIOPAN_DISPLAY, &var)) perror("FBIOPAN_DISPLAY 0");
        var0.yoffset = 0;
        if (ioctl(fb, FBIOPUT_VSCREENINFO, &var0)) perror("restore fb var");
    }
    return 0;
}
