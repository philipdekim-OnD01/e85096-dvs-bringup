/* GPU demo in an X window without DRI3 (11.82): Mesa panfrost renders a lit torus offscreen
 * (EGL surfaceless + FBO), glReadPixels copies each frame into an XImage, XPutImage shows it.
 * No MIT-SHM: this kernel has no SysV IPC, so shmget fails and XShmAttach gives BadAccess.
 * The fragment shader writes BGRA and the vertex shader flips y, so the bytes land in X's order as is.
 * Usage: gles-x11 [width height seconds]   (seconds 0 = run until killed)
 * Prints per-second fps and the time split render / readback / put; the window title shows fps. */
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES2/gl2.h>
#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

static double now(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec + t.tv_nsec * 1e-9; }

static GLuint prog(const char *vs, const char *fs) {
    GLuint p = glCreateProgram();
    const char *src[2] = { vs, fs }; GLenum ty[2] = { GL_VERTEX_SHADER, GL_FRAGMENT_SHADER };
    for (int i = 0; i < 2; i++) {
        GLuint s = glCreateShader(ty[i]); glShaderSource(s, 1, &src[i], NULL); glCompileShader(s);
        GLint ok; glGetShaderiv(s, GL_COMPILE_STATUS, &ok);
        if (!ok) { char log[512]; glGetShaderInfoLog(s, sizeof log, NULL, log); fprintf(stderr, "shader: %s\n", log); exit(1); }
        glAttachShader(p, s);
    }
    glBindAttribLocation(p, 0, "pos"); glBindAttribLocation(p, 1, "nrm"); glLinkProgram(p);
    return p;
}

/* column-major 4x4: out = a * b */
static void mul(float *out, const float *a, const float *b) {
    float r[16];
    for (int c = 0; c < 4; c++) for (int i = 0; i < 4; i++) {
        r[c * 4 + i] = 0; for (int k = 0; k < 4; k++) r[c * 4 + i] += a[k * 4 + i] * b[c * 4 + k];
    }
    for (int i = 0; i < 16; i++) out[i] = r[i];
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
    glEnable(GL_DEPTH_TEST);

    /* torus: R 1, r 0.4, NU x NV quads */
    enum { NU = 96, NV = 48 };
    static GLfloat v[(NU + 1) * (NV + 1) * 6];
    static GLushort idx[NU * NV * 6];
    int n = 0, m = 0;
    for (int i = 0; i <= NU; i++) for (int j = 0; j <= NV; j++) {
        float u = 2 * M_PI * i / NU, w = 2 * M_PI * j / NV;
        float cx = cosf(u), sx = sinf(u), cw = cosf(w), sw = sinf(w);
        v[n++] = (1 + 0.4f * cw) * cx; v[n++] = (1 + 0.4f * cw) * sx; v[n++] = 0.4f * sw;
        v[n++] = cw * cx; v[n++] = cw * sx; v[n++] = sw;
    }
    for (int i = 0; i < NU; i++) for (int j = 0; j < NV; j++) {
        int a = i * (NV + 1) + j, b = a + NV + 1;
        idx[m++] = a; idx[m++] = b; idx[m++] = a + 1; idx[m++] = a + 1; idx[m++] = b; idx[m++] = b + 1;
    }
    GLuint vbo, ibo;
    glGenBuffers(1, &vbo); glBindBuffer(GL_ARRAY_BUFFER, vbo); glBufferData(GL_ARRAY_BUFFER, sizeof v, v, GL_STATIC_DRAW);
    glGenBuffers(1, &ibo); glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, ibo); glBufferData(GL_ELEMENT_ARRAY_BUFFER, sizeof idx, idx, GL_STATIC_DRAW);
    GLuint p = prog(
        "attribute vec3 pos; attribute vec3 nrm; uniform mat4 mvp; uniform mat4 mv; varying vec3 n; varying vec3 q;"
        "void main(){ n = (mv * vec4(nrm, 0.0)).xyz; q = pos; vec4 c = mvp * vec4(pos, 1.0); gl_Position = vec4(c.x, -c.y, c.z, c.w); }",
        "precision mediump float; varying vec3 n; varying vec3 q; uniform float t;"
        "void main(){ vec3 N = normalize(n); vec3 L = normalize(vec3(0.5, 0.7, 1.0));"
        " float d = max(dot(N, L), 0.0); float s = pow(max(dot(reflect(-L, N), vec3(0.0, 0.0, 1.0)), 0.0), 32.0);"
        " float band = 0.5 + 0.5 * sin(12.0 * atan(q.y, q.x) + 3.0 * t);"
        " vec3 base = mix(vec3(0.95, 0.45, 0.1), vec3(0.1, 0.55, 0.95), band);"
        " vec3 col = base * (0.15 + 0.85 * d) + vec3(s);"
        " gl_FragColor = vec4(col.bgr, 1.0); }");  /* BGRA bytes for X */
    glUseProgram(p);
    GLint umvp = glGetUniformLocation(p, "mvp"), umv = glGetUniformLocation(p, "mv"), ut = glGetUniformLocation(p, "t");
    glBindBuffer(GL_ARRAY_BUFFER, vbo);
    glVertexAttribPointer(0, 3, GL_FLOAT, GL_FALSE, 24, (void *)0);
    glVertexAttribPointer(1, 3, GL_FLOAT, GL_FALSE, 24, (void *)12);
    glEnableVertexAttribArray(0); glEnableVertexAttribArray(1);

    float aspect = (float)W / H, f = 1 / tanf(0.5f * 0.8f), zn = 1, zf = 10;
    float proj[16] = { f / aspect, 0, 0, 0, 0, f, 0, 0, 0, 0, (zf + zn) / (zn - zf), -1, 0, 0, 2 * zf * zn / (zn - zf), 0 };
    double t0 = now(), tsec = t0, tr = 0, trb = 0, tp = 0;
    int frames = 0, total = 0;
    for (;;) {
        double t = now() - t0;
        if (secs > 0 && t > secs) break;
        float a = t * 0.9f, b = t * 0.6f;
        float rx[16] = { 1, 0, 0, 0, 0, cosf(a), sinf(a), 0, 0, -sinf(a), cosf(a), 0, 0, 0, 0, 1 };
        float ry[16] = { cosf(b), 0, -sinf(b), 0, 0, 1, 0, 0, sinf(b), 0, cosf(b), 0, 0, 0, -4, 1 };
        float mv[16], mvp[16];
        mul(mv, ry, rx); mul(mvp, proj, mv);
        double s0 = now();
        glClearColor(0.12f, 0.10f, 0.08f, 1); glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT);
        glUniformMatrix4fv(umvp, 1, GL_FALSE, mvp); glUniformMatrix4fv(umv, 1, GL_FALSE, mv); glUniform1f(ut, t);
        glDrawElements(GL_TRIANGLES, m, GL_UNSIGNED_SHORT, 0);
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
    printf("%d frames, %d triangles each\n", total, m / 3);
    XDestroyImage(img); XCloseDisplay(dpy);
    return 0;
}
