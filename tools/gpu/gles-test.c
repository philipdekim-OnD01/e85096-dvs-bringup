/* EGL surfaceless + GLES2 check: render a triangle into an FBO, read pixels back, then time full-screen fills. */
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES2/gl2.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
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
    glBindAttribLocation(p, 0, "pos"); glLinkProgram(p);
    GLint ok; glGetProgramiv(p, GL_LINK_STATUS, &ok); if (!ok) { fprintf(stderr, "link failed\n"); exit(1); }
    return p;
}

int main(int argc, char **argv) {
    int W = 1920, H = 1080, frames = argc > 1 ? atoi(argv[1]) : 60;
    PFNEGLGETPLATFORMDISPLAYEXTPROC gpd = (void *)eglGetProcAddress("eglGetPlatformDisplayEXT");
    EGLDisplay d = gpd(EGL_PLATFORM_SURFACELESS_MESA, EGL_DEFAULT_DISPLAY, NULL);
    EGLint maj, min;
    if (!eglInitialize(d, &maj, &min)) { fprintf(stderr, "eglInitialize failed 0x%x\n", eglGetError()); return 1; }
    eglBindAPI(EGL_OPENGL_ES_API);
    EGLint ca[] = { EGL_CONTEXT_CLIENT_VERSION, 2, EGL_NONE };
    EGLContext c = eglCreateContext(d, EGL_NO_CONFIG_KHR, EGL_NO_CONTEXT, ca);
    if (!c || !eglMakeCurrent(d, EGL_NO_SURFACE, EGL_NO_SURFACE, c)) { fprintf(stderr, "context failed 0x%x\n", eglGetError()); return 1; }
    printf("EGL %d.%d  GL_RENDERER %s\nGL_VERSION %s\n", maj, min, glGetString(GL_RENDERER), glGetString(GL_VERSION));

    GLuint tex, fbo;
    glGenTextures(1, &tex); glBindTexture(GL_TEXTURE_2D, tex);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, W, H, 0, GL_RGBA, GL_UNSIGNED_BYTE, NULL);
    glGenFramebuffers(1, &fbo); glBindFramebuffer(GL_FRAMEBUFFER, fbo);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, tex, 0);
    if (glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE) { fprintf(stderr, "fbo incomplete\n"); return 1; }
    glViewport(0, 0, W, H);

    /* 1. correctness: clear (64,128,191,255), then several draws over the centre, each read back.
     * Shows whether only the first draw, constant-colour shaders or triangles go missing. */
    const char *vs_c = "attribute vec2 pos; void main(){ gl_Position = vec4(pos, 0.0, 1.0); }";
    const char *fs_c = "precision mediump float; void main(){ gl_FragColor = vec4(1.0, 0.6, 0.2, 0.8); }";
    const char *vs_v = "attribute vec2 pos; varying vec2 uv; void main(){ uv = pos; gl_Position = vec4(pos, 0.0, 1.0); }";
    const char *fs_v = "precision mediump float; varying vec2 uv; void main(){ gl_FragColor = vec4(1.0, 0.6 + uv.x, 0.2 + uv.y, 0.8); }";
    GLuint pc = prog(vs_c, fs_c), pv = prog(vs_v, fs_v);
    static const GLfloat tri[] = { -0.5f, -0.5f, 0.5f, -0.5f, 0.0f, 0.5f };
    static const GLfloat quad0[] = { -1, -1, 1, -1, -1, 1, 1, 1 };
    struct { const char *name; GLuint p; const GLfloat *v; GLenum mode; int n; } t[] = {
        { "1 triangle, constant colour", pc, tri, GL_TRIANGLES, 3 },
        { "2 same again", pc, tri, GL_TRIANGLES, 3 },
        { "3 triangle, varying colour", pv, tri, GL_TRIANGLES, 3 },
        { "4 quad, constant colour", pc, quad0, GL_TRIANGLE_STRIP, 4 },
        { "5 quad, varying colour", pv, quad0, GL_TRIANGLE_STRIP, 4 },
        { "6 quad, constant again", pc, quad0, GL_TRIANGLE_STRIP, 4 },
        { "7 triangle, constant again", pc, tri, GL_TRIANGLES, 3 },
    };
    unsigned char mid[4], corner[4];
    int ok = 1;
    glEnableVertexAttribArray(0);
    for (unsigned i = 0; i < sizeof t / sizeof t[0]; i++) {
        glClearColor(0.25f, 0.5f, 0.75f, 1.0f); glClear(GL_COLOR_BUFFER_BIT);
        glUseProgram(t[i].p); glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 0, t[i].v);
        glDrawArrays(t[i].mode, 0, t[i].n);
        if (getenv("FULL_READ")) {
            /* Where did this draw land?  Bounding box and count of pixels that differ from the clear colour. */
            static unsigned char img[1920 * 1080 * 4];
            glReadPixels(0, 0, W, H, GL_RGBA, GL_UNSIGNED_BYTE, img);
            int n = 0, x0 = W, y0 = H, x1 = -1, y1 = -1;
            for (int y = 0; y < H; y++) for (int x = 0; x < W; x++) {
                unsigned char *q = img + 4 * (y * W + x);
                if (q[0] != 64 || q[1] != 128 || q[2] != 191) { n++; if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y; }
            }
            printf("    drawn %d px, box x %d..%d y %d..%d\n", n, x0, x1, y0, y1);
        }
        glReadPixels(W / 2, H / 2, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, mid);
        glReadPixels(5, 5, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, corner);
        /* want 255,153,51,204 at the centre (uv = 0 there); corner keeps the clear colour unless a quad covers it */
        int good = mid[0] > 250 && mid[1] > 148 && mid[1] < 158 && mid[2] > 46 && mid[2] < 56 && mid[3] > 199 && mid[3] < 209;
        ok &= good;
        printf("%-30s centre %3d,%3d,%3d,%3d corner %3d,%3d,%3d,%3d %s\n", t[i].name,
               mid[0], mid[1], mid[2], mid[3], corner[0], corner[1], corner[2], corner[3], good ? "ok" : "BAD");
    }
    printf("correctness -> %s\n", ok ? "PASS" : "FAIL");
    if (getenv("ONLY_CHECK")) return !ok;

    /* 2. speed: full-screen quads with a per-pixel shader (sin per pixel) */
    GLuint p2 = prog("attribute vec2 pos; varying vec2 uv; void main(){ uv = pos; gl_Position = vec4(pos, 0.0, 1.0); }",
                     "precision mediump float; varying vec2 uv; uniform float t;"
                     "void main(){ float v = sin(uv.x * 20.0 + t) * cos(uv.y * 20.0 - t);"
                     " gl_FragColor = vec4(0.5 + 0.5 * v, uv * 0.5 + 0.5, 1.0); }");
    static const GLfloat quad[] = { -1, -1, 1, -1, -1, 1, 1, 1 };
    glUseProgram(p2); glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 0, quad);
    GLint ut = glGetUniformLocation(p2, "t");
    glFinish();
    double t0 = now();
    for (int i = 0; i < frames; i++) { glUniform1f(ut, i * 0.1f); glDrawArrays(GL_TRIANGLE_STRIP, 0, 4); glFinish(); }
    double dt = now() - t0;
    printf("%d frames %dx%d in %.3f s = %.1f fps, %.0f Mpix/s\n", frames, W, H, dt, frames / dt, frames * (double)W * H / dt / 1e6);
    glReadPixels(W / 2, H / 2, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, mid);
    printf("last frame centre %d,%d,%d (GL error 0x%x)\n", mid[0], mid[1], mid[2], glGetError());
    return !ok;
}
