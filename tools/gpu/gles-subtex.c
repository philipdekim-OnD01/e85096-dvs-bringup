/* Texture sub-upload check (11.88): glyph caches (glamor, Firefox WebRender) update small rectangles of
 * an 8-bit or RGBA atlas between draws that sample it. Text on the GPU X lost glyphs, so: upload random
 * small rects with glTexSubImage2D, draw the whole texture into an FBO after every upload (as a glyph
 * cache does), read back every `check` uploads and compare with a CPU copy.
 * Usage: gles-subtex [uploads [check]]   (EGL surfaceless, GLES 3.0) */
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES3/gl3.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define S 512

static GLuint prog(const char *fs) {
    const char *vs = "#version 300 es\nin vec2 pos; void main(){ gl_Position = vec4(pos, 0.0, 1.0); }";
    GLuint p = glCreateProgram();
    const char *src[2] = { vs, fs }; GLenum ty[2] = { GL_VERTEX_SHADER, GL_FRAGMENT_SHADER };
    for (int i = 0; i < 2; i++) {
        GLuint s = glCreateShader(ty[i]); glShaderSource(s, 1, &src[i], NULL); glCompileShader(s);
        GLint ok; glGetShaderiv(s, GL_COMPILE_STATUS, &ok);
        if (!ok) { char log[512]; glGetShaderInfoLog(s, sizeof log, NULL, log); fprintf(stderr, "shader: %s\n", log); exit(1); }
        glAttachShader(p, s);
    }
    glBindAttribLocation(p, 0, "pos"); glLinkProgram(p);
    return p;
}

int main(int argc, char **argv) {
    int uploads = argc > 1 ? atoi(argv[1]) : 300, check = argc > 2 ? atoi(argv[2]) : 50;
    PFNEGLGETPLATFORMDISPLAYEXTPROC gpd = (void *)eglGetProcAddress("eglGetPlatformDisplayEXT");
    EGLDisplay d = gpd(EGL_PLATFORM_SURFACELESS_MESA, EGL_DEFAULT_DISPLAY, NULL);
    if (!eglInitialize(d, NULL, NULL)) { fprintf(stderr, "eglInitialize failed\n"); return 1; }
    eglBindAPI(EGL_OPENGL_ES_API);
    EGLint ca[] = { EGL_CONTEXT_CLIENT_VERSION, 3, EGL_NONE };
    EGLContext c = eglCreateContext(d, EGL_NO_CONFIG_KHR, EGL_NO_CONTEXT, ca);
    if (!c || !eglMakeCurrent(d, EGL_NO_SURFACE, EGL_NO_SURFACE, c)) { fprintf(stderr, "context failed\n"); return 1; }

    GLuint rt, fbo;
    glGenTextures(1, &rt); glBindTexture(GL_TEXTURE_2D, rt);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, S, S, 0, GL_RGBA, GL_UNSIGNED_BYTE, NULL);
    glGenFramebuffers(1, &fbo); glBindFramebuffer(GL_FRAMEBUFFER, fbo);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, rt, 0);
    glViewport(0, 0, S, S);
    static const GLfloat quad[] = { -1, -1, 1, -1, -1, 1, 1, 1 };
    glEnableVertexAttribArray(0); glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 0, quad);

    struct { const char *name; GLint ifmt; GLenum fmt; int bpp; const char *swz; } f[] = {
        { "R8", GL_R8, GL_RED, 1, "vec4(t.r, 0.0, 0.0, 1.0)" },
        { "ALPHA", GL_ALPHA, GL_ALPHA, 1, "vec4(t.a, 0.0, 0.0, 1.0)" },
        { "RGBA8", GL_RGBA8, GL_RGBA, 4, "t" },
    };
    int bad_total = 0;
    srand(1);
    for (unsigned k = 0; k < sizeof f / sizeof f[0]; k++) {
        char fs[512];
        snprintf(fs, sizeof fs, "#version 300 es\nprecision mediump float; uniform sampler2D s; out vec4 o;\n"
                 "void main(){ vec4 t = texelFetch(s, ivec2(gl_FragCoord.xy), 0); o = %s; }", f[k].swz);
        GLuint p = prog(fs);
        glUseProgram(p);
        unsigned char *ref = calloc(S * S, f[k].bpp), *rect = malloc(64 * 64 * 4), *img = malloc(S * S * 4);
        GLuint tex; glGenTextures(1, &tex); glActiveTexture(GL_TEXTURE0); glBindTexture(GL_TEXTURE_2D, tex);
        glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
        glTexImage2D(GL_TEXTURE_2D, 0, f[k].ifmt, S, S, 0, f[k].fmt, GL_UNSIGNED_BYTE, ref);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
        int bad_fmt = 0;
        for (int u = 1; u <= uploads; u++) {
            int w = 1 + rand() % 40, h = 1 + rand() % 40, x = rand() % (S - w), y = rand() % (S - h);
            for (int i = 0; i < w * h * f[k].bpp; i++) rect[i] = 1 + rand() % 254;
            glTexSubImage2D(GL_TEXTURE_2D, 0, x, y, w, h, f[k].fmt, GL_UNSIGNED_BYTE, rect);
            for (int r = 0; r < h; r++) memcpy(ref + ((y + r) * S + x) * f[k].bpp, rect + r * w * f[k].bpp, w * f[k].bpp);
            glDrawArrays(GL_TRIANGLE_STRIP, 0, 4); /* a batch that samples the texture before the next upload */
            if (u % check)
                continue;
            glReadPixels(0, 0, S, S, GL_RGBA, GL_UNSIGNED_BYTE, img);
            int bad = 0, bx = -1, by = -1;
            for (int y2 = 0; y2 < S; y2++) for (int x2 = 0; x2 < S; x2++) {
                const unsigned char *q = img + 4 * (y2 * S + x2), *e = ref + f[k].bpp * (y2 * S + x2);
                int ok = f[k].bpp == 1 ? q[0] == e[0] : !memcmp(q, e, 4);
                if (!ok && bad++ == 0) { bx = x2; by = y2; }
            }
            if (bad) printf("%-6s after %4d uploads: %d texels wrong (first at %d,%d)\n", f[k].name, u, bad, bx, by);
            bad_fmt += bad;
        }
        printf("%-6s %d uploads: %s\n", f[k].name, uploads, bad_fmt ? "FAIL" : "ok");
        bad_total += bad_fmt;
        free(ref); free(rect); free(img);
    }
    printf("subtex -> %s\n", bad_total ? "FAIL" : "PASS");
    return bad_total != 0;
}
