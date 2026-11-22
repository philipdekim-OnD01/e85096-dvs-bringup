/* The demo scene shared by gles-x11.c and gles-fb.c: a lit torus with moving colour bands,
 * drawn with GLES2 into the bound framebuffer (with a depth buffer). For an FBO the vertex shader
 * flips y so that row 0 of the target memory is the top of the image (glReadPixels order and
 * scanout order); an EGL window surface (gles-kms) is already stored top row first. */
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

static GLint torus_umvp, torus_umv, torus_ut;
static int torus_nidx;

/* bgr: write the colour as BGRA bytes (an RGBA texture read back for X); 0 for a BGRX target.
 * flipy: 1 for an FBO, 0 for a window surface. */
static void torus_init(int bgr, int flipy) {
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
    torus_nidx = m;
    GLuint vbo, ibo;
    glGenBuffers(1, &vbo); glBindBuffer(GL_ARRAY_BUFFER, vbo); glBufferData(GL_ARRAY_BUFFER, sizeof v, v, GL_STATIC_DRAW);
    glGenBuffers(1, &ibo); glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, ibo); glBufferData(GL_ELEMENT_ARRAY_BUFFER, sizeof idx, idx, GL_STATIC_DRAW);
    char fs[1024];
    snprintf(fs, sizeof fs,
        "precision mediump float; varying vec3 n; varying vec3 q; uniform float t;"
        "void main(){ vec3 N = normalize(n); vec3 L = normalize(vec3(0.5, 0.7, 1.0));"
        " float d = max(dot(N, L), 0.0); float s = pow(max(dot(reflect(-L, N), vec3(0.0, 0.0, 1.0)), 0.0), 32.0);"
        " float band = 0.5 + 0.5 * sin(12.0 * atan(q.y, q.x) + 3.0 * t);"
        " vec3 base = mix(vec3(0.95, 0.45, 0.1), vec3(0.1, 0.55, 0.95), band);"
        " vec3 col = base * (0.15 + 0.85 * d) + vec3(s);"
        " gl_FragColor = vec4(col.%s, 1.0); }", bgr ? "bgr" : "rgb");
    char vs[512];
    snprintf(vs, sizeof vs,
        "attribute vec3 pos; attribute vec3 nrm; uniform mat4 mvp; uniform mat4 mv; varying vec3 n; varying vec3 q;"
        "void main(){ n = (mv * vec4(nrm, 0.0)).xyz; q = pos; vec4 c = mvp * vec4(pos, 1.0); gl_Position = vec4(c.x, %sc.y, c.z, c.w); }",
        flipy ? "-" : "");
    GLuint p = prog(vs, fs);
    glUseProgram(p);
    torus_umvp = glGetUniformLocation(p, "mvp"); torus_umv = glGetUniformLocation(p, "mv"); torus_ut = glGetUniformLocation(p, "t");
    glBindBuffer(GL_ARRAY_BUFFER, vbo);
    glVertexAttribPointer(0, 3, GL_FLOAT, GL_FALSE, 24, (void *)0);
    glVertexAttribPointer(1, 3, GL_FLOAT, GL_FALSE, 24, (void *)12);
    glEnableVertexAttribArray(0); glEnableVertexAttribArray(1);
    glEnable(GL_DEPTH_TEST);
}

/* clear and draw the frame for time t (seconds) */
static void torus_draw(float t, float aspect) {
    float f = 1 / tanf(0.5f * 0.8f), zn = 1, zf = 10;
    float proj[16] = { f / aspect, 0, 0, 0, 0, f, 0, 0, 0, 0, (zf + zn) / (zn - zf), -1, 0, 0, 2 * zf * zn / (zn - zf), 0 };
    float a = t * 0.9f, b = t * 0.6f;
    float rx[16] = { 1, 0, 0, 0, 0, cosf(a), sinf(a), 0, 0, -sinf(a), cosf(a), 0, 0, 0, 0, 1 };
    float ry[16] = { cosf(b), 0, -sinf(b), 0, 0, 1, 0, 0, sinf(b), 0, cosf(b), 0, 0, 0, -4, 1 };
    float mv[16], mvp[16];
    mul(mv, ry, rx); mul(mvp, proj, mv);
    glClearColor(0.12f, 0.10f, 0.08f, 1); glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT);
    glUniformMatrix4fv(torus_umvp, 1, GL_FALSE, mvp); glUniformMatrix4fv(torus_umv, 1, GL_FALSE, mv); glUniform1f(torus_ut, t);
    glDrawElements(GL_TRIANGLES, torus_nidx, GL_UNSIGNED_SHORT, 0);
}
