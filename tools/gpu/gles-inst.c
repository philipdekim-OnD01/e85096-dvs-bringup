/* Instanced draw check (11.88): glamor glyphs and Firefox WebRender draw many small quads per call as
 * instances. Draw N quads in a grid (per-instance rect and colour, divisor 1), read back the centre of
 * every quad and count the missing ones, for several N and for strip / indexed draws.
 * Usage: [PASSES=k] [NS=a-b] [INSTID=1] [HIGHP=1] [TRI6=1] [VERBOSE=1] [MAP=1] gles-inst   (EGL surfaceless, GLES 3.0) */
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES3/gl3.h>
#include <stdio.h>
#include <stdlib.h>

#define S 512
#define CELL 8 /* 64 x 64 grid of 8 px cells: up to 4096 instances */

int main(void) {
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

    /* INSTID=1: rect and colour from gl_InstanceID instead of the per-instance attributes */
    const char *vs = getenv("INSTID") ? "#version 300 es\nlayout(location=0) in vec2 corner; out vec4 c;\n"
        "void main(){ int i = gl_InstanceID; c = vec4(float(1 + i % 251), float(1 + (i / 251) % 251), 200.0, 255.0) / 255.0;\n"
        "  vec2 p = vec2(float(i % 64), float(i / 64)) * 8.0 + 1.0 + corner * 6.0; gl_Position = vec4(p / 256.0 - 1.0, 0.0, 1.0); }"
        : "#version 300 es\nlayout(location=0) in vec2 corner; layout(location=1) in vec4 rect;\n"
        "layout(location=2) in vec4 col; out vec4 c;\n"
        "void main(){ c = col; vec2 p = rect.xy + corner * rect.zw; gl_Position = vec4(p / 256.0 - 1.0, 0.0, 1.0); }";
    /* HIGHP=1: fp32 colour varying (16 bytes per vertex) instead of fp16 */
    const char *fs = getenv("HIGHP") ? "#version 300 es\nprecision highp float; in vec4 c; out vec4 o; void main(){ o = c; }"
                                     : "#version 300 es\nprecision mediump float; in vec4 c; out vec4 o; void main(){ o = c; }";
    GLuint p = glCreateProgram();
    const char *src[2] = { vs, fs }; GLenum ty[2] = { GL_VERTEX_SHADER, GL_FRAGMENT_SHADER };
    for (int i = 0; i < 2; i++) {
        GLuint s = glCreateShader(ty[i]); glShaderSource(s, 1, &src[i], NULL); glCompileShader(s);
        GLint ok; glGetShaderiv(s, GL_COMPILE_STATUS, &ok);
        if (!ok) { char log[512]; glGetShaderInfoLog(s, sizeof log, NULL, log); fprintf(stderr, "shader: %s\n", log); return 1; }
        glAttachShader(p, s);
    }
    glLinkProgram(p); glUseProgram(p);

    static const GLfloat corners[] = { 0, 0, 1, 0, 0, 1, 1, 1, 0, 1, 1, 0 }; /* strip 0-3; TRI6=1: 0 1 2 3 4 5 */
    static const GLushort idx[] = { 0, 1, 2, 2, 1, 3 };
    static GLfloat rects[4096][4]; static GLubyte cols[4096][4];
    for (int i = 0; i < 4096; i++) {
        rects[i][0] = (i % 64) * CELL + 1; rects[i][1] = (i / 64) * CELL + 1; rects[i][2] = rects[i][3] = CELL - 2;
        cols[i][0] = 1 + i % 251; cols[i][1] = 1 + (i / 251) % 251; cols[i][2] = 200; cols[i][3] = 255;
    }
    GLuint vb[4];
    glGenBuffers(4, vb);
    glBindBuffer(GL_ARRAY_BUFFER, vb[0]); glBufferData(GL_ARRAY_BUFFER, sizeof corners, corners, GL_STATIC_DRAW);
    glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 0, 0); glEnableVertexAttribArray(0);
    glBindBuffer(GL_ARRAY_BUFFER, vb[1]); glBufferData(GL_ARRAY_BUFFER, sizeof rects, rects, GL_STATIC_DRAW);
    glVertexAttribPointer(1, 4, GL_FLOAT, GL_FALSE, 0, 0); glEnableVertexAttribArray(1); glVertexAttribDivisor(1, 1);
    glBindBuffer(GL_ARRAY_BUFFER, vb[2]); glBufferData(GL_ARRAY_BUFFER, sizeof cols, cols, GL_STATIC_DRAW);
    glVertexAttribPointer(2, 4, GL_UNSIGNED_BYTE, GL_TRUE, 0, 0); glEnableVertexAttribArray(2); glVertexAttribDivisor(2, 1);
    glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, vb[3]); glBufferData(GL_ELEMENT_ARRAY_BUFFER, sizeof idx, idx, GL_STATIC_DRAW);

    static int ns[4096] = { 1, 2, 3, 5, 7, 16, 17, 31, 33, 64, 100, 127, 129, 255, 257, 500, 1000, 1023, 2047, 4096 };
    unsigned nn = 20;
    if (getenv("NS")) { /* NS=a-b: every n from a to b */
        int a = 1, b = 1; sscanf(getenv("NS"), "%d-%d", &a, &b);
        for (nn = 0; a <= b && nn < 4096; a++) ns[nn++] = a;
    }
    static unsigned char img[S * S * 4];
    int verbose = !!getenv("VERBOSE"); /* VERBOSE=1: one line per draw, failing or not */
    int fails = 0, passes = getenv("PASSES") ? atoi(getenv("PASSES")) : 1;
    for (int pass = 0; pass < passes; pass++)
    for (int mode = 0; mode < 2; mode++)
        for (unsigned k = 0; k < nn; k++) {
            int n = ns[k];
            glClearColor(0, 0, 0, 0); glClear(GL_COLOR_BUFFER_BIT);
            if (mode == 0 && getenv("TRI6")) glDrawArraysInstanced(GL_TRIANGLES, 0, 6, n); /* (0,1,2) (3,4,5) */
            else if (mode == 0) glDrawArraysInstanced(GL_TRIANGLE_STRIP, 0, 4, n);
            else glDrawElementsInstanced(GL_TRIANGLES, 6, GL_UNSIGNED_SHORT, 0, n);
            glReadPixels(0, 0, S, S, GL_RGBA, GL_UNSIGNED_BYTE, img);
            int miss = 0, wrong = 0, first = -1;
            for (int i = 0; i < n; i++) {
                int x = (i % 64) * CELL + CELL / 2, y = (i / 64) * CELL + CELL / 2;
                unsigned char *q = img + 4 * (y * S + x);
                if (!q[3]) { miss++; if (first < 0) first = i; }
                else if (q[0] != cols[i][0] || q[1] != cols[i][1]) { wrong++; if (first < 0) first = i; }
            }
            if (miss || wrong) fails++;
            if (first >= 0 && getenv("MAP")) { /* MAP=1: alpha of the first bad cell and its neighbours, '#' drawn */
                int cx = (first % 64) * CELL, cy = (first / 64) * CELL;
                for (int y = cy - 4; y < cy + CELL + 4; y++, putchar('\n'))
                    for (int x = cx - 4; x < cx + CELL + 4; x++)
                        putchar(x < 0 || y < 0 ? ' ' : img[4 * (y * S + x) + 3] ? '#' : '.');
            }
            if (miss || wrong || verbose) { printf("pass %d %s n=%4d: %d missing, %d wrong colour (first %d)\n",
                                               pass, mode ? "elements" : "arrays  ", n, miss, wrong, first); }
        }
    printf("inst -> %s\n", fails ? "FAIL" : "PASS");
    return fails != 0;
}
