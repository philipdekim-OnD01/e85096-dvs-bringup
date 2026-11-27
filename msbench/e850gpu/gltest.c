/* gltest.c: what the E850 GPU (Mali-G52 MP1, Mesa panfrost on the vendor kbase, 11.82) gives a GLES 3.1 compute
 * program (msbench 11.117), to judge how far TFLite GL is from the hardware: peak FMA GFLOPS (highp and mediump),
 * buffer copy GB/s, and the time of one dispatch + glFinish (an empty job round trip) and of a dispatch in a chain of
 * 100 (glMemoryBarrier between them, one glFinish), which is what a model of many layers pays per layer.
 * Headless: EGL_PLATFORM=surfaceless, no config, no surface. Build: build-gltest.sh. Run: . gpu-env.sh; ./gltest
 * ./gltest load fma|copy SECS keeps the GPU busy with one of the two for SECS seconds (11.118: what a busy GPU costs the
 * CPU, ALU work against DRAM traffic) and prints the rate it held. */
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES3/gl31.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static double now(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec + t.tv_nsec * 1e-9; }
static int cmp(const void *a, const void *b) { double x = *(double *)a, y = *(double *)b; return (x > y) - (x < y); }

static GLuint prog(const char *src) {
    GLuint s = glCreateShader(GL_COMPUTE_SHADER), p = glCreateProgram();
    GLint ok; char log[2048];
    glShaderSource(s, 1, &src, NULL); glCompileShader(s); glGetShaderiv(s, GL_COMPILE_STATUS, &ok);
    if (!ok) { glGetShaderInfoLog(s, sizeof log, NULL, log); printf("compile: %s\n", log); exit(1); }
    glAttachShader(p, s); glLinkProgram(p); glGetProgramiv(p, GL_LINK_STATUS, &ok);
    if (!ok) { glGetProgramInfoLog(p, sizeof log, NULL, log); printf("link: %s\n", log); exit(1); }
    return p;
}

#define FMA(P) "#version 310 es\nprecision " P " float;\nlayout(local_size_x = 64) in;\n" \
    "layout(std430, binding = 0) buffer O { highp float o[]; };\nuniform float k;\nvoid main() {\n" \
    "  vec4 x = vec4(float(gl_GlobalInvocationID.x & 7u)), y = x + 1.0, z = x + 2.0, w = x + 3.0;\n" \
    "  for (int i = 0; i < 1024; i++) { x = x * k + y; y = y * k + z; z = z * k + w; w = w * k + x; }\n" \
    "  o[gl_GlobalInvocationID.x] = x.x + y.y + z.z + w.w; }\n"
static const char *COPY = "#version 310 es\nlayout(local_size_x = 256) in;\n"
    "layout(std430, binding = 0) readonly buffer I { vec4 i[]; };\n"
    "layout(std430, binding = 1) writeonly buffer O { vec4 o[]; };\n"
    "void main() { o[gl_GlobalInvocationID.x] = i[gl_GlobalInvocationID.x] * 1.0001; }\n";
static const char *NOP = "#version 310 es\nlayout(local_size_x = 1) in;\n"
    "layout(std430, binding = 2) buffer O { float o[]; };\nvoid main() { o[0] += 1.0; }\n";

static double best(GLuint p, GLuint groups, int reps) {      /* best of reps, seconds per dispatch */
    double b = 1e9;
    glUseProgram(p); glDispatchCompute(groups, 1, 1); glFinish();
    for (int r = 0; r < reps; r++) {
        double t = now(); glDispatchCompute(groups, 1, 1); glFinish(); t = now() - t;
        if (t < b) b = t;
    }
    return b;
}

int main(int argc, char **argv) {
    EGLDisplay d = eglGetDisplay(EGL_DEFAULT_DISPLAY);
    if (!eglInitialize(d, NULL, NULL)) { printf("eglInitialize failed 0x%x\n", eglGetError()); return 1; }
    eglBindAPI(EGL_OPENGL_ES_API);
    EGLint ca[] = { EGL_CONTEXT_MAJOR_VERSION, 3, EGL_CONTEXT_MINOR_VERSION, 1, EGL_NONE };
    EGLContext c = eglCreateContext(d, EGL_NO_CONFIG_KHR, EGL_NO_CONTEXT, ca);
    if (!c || !eglMakeCurrent(d, EGL_NO_SURFACE, EGL_NO_SURFACE, c)) { printf("context failed 0x%x\n", eglGetError()); return 1; }
    printf("renderer: %s | %s\n", glGetString(GL_RENDERER), glGetString(GL_VERSION));

    enum { G = 64 * 1024, NB = 4 << 20 };             /* FMA invocations; copy: 4M vec4 = 64 MB each way */
    GLuint b[3]; glGenBuffers(3, b);   /* groups stay under 65535 (the GLES minimum of GL_MAX_COMPUTE_WORK_GROUP_COUNT) */
    glBindBuffer(GL_SHADER_STORAGE_BUFFER, b[0]); glBufferData(GL_SHADER_STORAGE_BUFFER, NB * 16, NULL, GL_DYNAMIC_COPY);
    glBindBuffer(GL_SHADER_STORAGE_BUFFER, b[1]); glBufferData(GL_SHADER_STORAGE_BUFFER, NB * 16, NULL, GL_DYNAMIC_COPY);
    float zero = 0;
    glBindBuffer(GL_SHADER_STORAGE_BUFFER, b[2]); glBufferData(GL_SHADER_STORAGE_BUFFER, 4, &zero, GL_DYNAMIC_COPY);
    for (int i = 0; i < 3; i++) glBindBufferBase(GL_SHADER_STORAGE_BUFFER, i, b[i]);

    GLuint f32 = prog(FMA("highp")), f16 = prog(FMA("mediump"));
    glUseProgram(f32); glUniform1f(glGetUniformLocation(f32, "k"), 0.999f);
    glUseProgram(f16); glUniform1f(glGetUniformLocation(f16, "k"), 0.999f);
    double fl = (double)G * 1024 * 4 * 4 * 2;           /* 4 fma of vec4 per loop, 2 flop each */
    printf("fma highp   %.1f GFLOPS\n", fl / best(f32, G / 64, 5) / 1e9);
    printf("fma mediump %.1f GFLOPS\n", fl / best(f16, G / 64, 5) / 1e9);
    GLuint copy = prog(COPY);
    if (argc == 4 && !strcmp(argv[1], "load")) {
        int fma = !strcmp(argv[2], "fma"); GLuint p = fma ? f32 : copy; long n = 0;
        double t0 = now(), end = t0 + atof(argv[3]);
        glUseProgram(p);
        while (now() < end) { glDispatchCompute(fma ? G / 64 : NB / 256, 1, 1); glFinish(); n++; }
        double s = now() - t0;
        if (fma) printf("load fma %.1f GFLOPS for %.1f s\n", fl * n / s / 1e9, s);
        else printf("load copy %.2f GB/s for %.1f s\n", 2.0 * NB * 16 * n / s / 1e9, s);
        return 0;
    }
    printf("copy        %.2f GB/s (read + write)\n", 2.0 * NB * 16 / best(copy, NB / 256, 5) / 1e9);

    GLuint nop = prog(NOP); double t[200];
    glUseProgram(nop); glDispatchCompute(1, 1, 1); glFinish();
    for (int i = 0; i < 200; i++) { t[i] = now(); glDispatchCompute(1, 1, 1); glFinish(); t[i] = now() - t[i]; }
    qsort(t, 200, sizeof *t, cmp);
    printf("dispatch + glFinish: median %.3f ms, min %.3f ms\n", t[100] * 1e3, t[0] * 1e3);
    for (int r = 0; r < 20; r++) {
        t[r] = now();
        for (int i = 0; i < 100; i++) { glDispatchCompute(1, 1, 1); glMemoryBarrier(GL_SHADER_STORAGE_BARRIER_BIT); }
        glFinish(); t[r] = (now() - t[r]) / 100;
    }
    qsort(t, 20, sizeof *t, cmp);
    printf("dispatch in a chain of 100: median %.3f ms each\n", t[10] * 1e3);
    float v; glBindBuffer(GL_SHADER_STORAGE_BUFFER, b[2]);
    float *m = glMapBufferRange(GL_SHADER_STORAGE_BUFFER, 0, 4, GL_MAP_READ_BIT); v = m ? *m : -1;
    glUnmapBuffer(GL_SHADER_STORAGE_BUFFER);
    printf("counter %.0f (expect %d), GL error 0x%x\n", v, 1 + 200 + 20 * 100, glGetError());
    printf(v == 1 + 200 + 20 * 100 ? "RESULT OK\n" : "RESULT FAIL\n");
    return v != 1 + 200 + 20 * 100;
}
