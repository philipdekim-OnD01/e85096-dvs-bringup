// hetero.cpp: NCNN on the E850's CPU cores and its Mali-G52 together, in C++ (11.121). cothread.py (11.118) without
// Python: the main thread runs the CPU net (NCNN FP16, OpenMP team on CPU4-7 from OMP_PLACES); a plain thread outside
// OpenMP drives the Vulkan net (FP32, winograd off) from CPU3, re-pinned before every run because libgomp binds a thread
// to the first place on its first parallel region (11.118). Each phase runs SECS:
//   cpu   CPU net alone          gpu   GPU net alone          both  both nets at once, each on its own frames
// One JSON line per phase on stdout. Build: build-ncnn.sh. Run on the board (vk-env.sh sourced):
//   OMP_PLACES='{4},{5},{6},{7}' OMP_PROC_BIND=close taskset -c 3-7 ./hetero DIR C H W SECS cpu gpu both ...
#include <net.h>
#include <gpu.h>

#include <pthread.h>
#include <sched.h>
#include <sys/resource.h>
#include <time.h>
#include <unistd.h>

#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <string>
#include <thread>
#include <vector>

static double now()
{
    timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec * 1e-9;
}

static double cputime(clockid_t c)
{
    timespec t;
    clock_gettime(c, &t);
    return t.tv_sec + t.tv_nsec * 1e-9;
}

static double proctime()
{
    rusage r;
    getrusage(RUSAGE_SELF, &r);
    return r.ru_utime.tv_sec + r.ru_stime.tv_sec + (r.ru_utime.tv_usec + r.ru_stime.tv_usec) * 1e-6;
}

static void pin(int cpu)
{
    cpu_set_t s;
    CPU_ZERO(&s);
    CPU_SET(cpu, &s);
    pthread_setaffinity_np(pthread_self(), sizeof s, &s);
}

static void load(ncnn::Net& net, const std::string& dir, bool gpu)
{
    net.opt.use_vulkan_compute = gpu;
    net.opt.num_threads = gpu ? 1 : 4;
    if (gpu)
    {
        // panvk has no 16-bit storage/arithmetic, and the winograd shaders spill for minutes on Bifrost (11.118).
        // The CPU net keeps ncnn's ARMv8.2 default: fp16 storage + arithmetic (the msbench "NCNN FP16" worker).
        net.opt.use_winograd_convolution = false;
        net.opt.use_fp16_packed = net.opt.use_fp16_storage = net.opt.use_fp16_arithmetic = false;
    }
    if (net.load_param((dir + "/model.ncnn.param").c_str()) || net.load_model((dir + "/model.ncnn.bin").c_str()))
    {
        fprintf(stderr, "cannot load %s\n", dir.c_str());
        exit(1);
    }
}

static ncnn::Mat infer(ncnn::Net& net, const ncnn::Mat& in)
{
    ncnn::Extractor ex = net.create_extractor();
    ex.input("in0", in);
    ncnn::Mat out;
    ex.extract("out0", out);
    return out;
}

struct Stat
{
    std::vector<double> ms;
    double thread_s = 0;
};

// Runs until t_end; pin >= 0 re-pins before every inference.
static void loop(ncnn::Net& net, const ncnn::Mat& in, int cpu, std::atomic<int>& ready, std::atomic<double>& t_end, Stat& st)
{
    for (int i = 0; i < 2; i++)
    {
        if (cpu >= 0) pin(cpu);
        infer(net, in);
    }
    ready++;
    while (t_end.load() == 0) std::this_thread::yield();
    double c0 = cputime(CLOCK_THREAD_CPUTIME_ID);
    while (now() < t_end.load())
    {
        if (cpu >= 0) pin(cpu);
        double a = now();
        infer(net, in);
        st.ms.push_back((now() - a) * 1e3);
    }
    st.thread_s = cputime(CLOCK_THREAD_CPUTIME_ID) - c0;
}

static int rd(const std::string& p)
{
    std::ifstream f(p);
    int v = 0;
    f >> v;
    return v;
}

static std::string zone(const char* type)
{
    for (int i = 0; i < 16; i++)
    {
        std::string d = "/sys/class/thermal/thermal_zone" + std::to_string(i);
        std::ifstream f(d + "/type");
        std::string t;
        if (f >> t && t == type) return d + "/temp";
    }
    return "";
}

static std::string stat_json(const char* name, const Stat& s, double wall)
{
    if (s.ms.empty()) return "";
    std::vector<double> v = s.ms;
    std::sort(v.begin(), v.end());
    char b[256];
    snprintf(b, sizeof b, "\"%s\": {\"n\": %zu, \"ips\": %.3f, \"median_ms\": %.2f, \"p90_ms\": %.2f, \"thread_cores\": %.3f}, ",
             name, v.size(), v.size() / wall, v[v.size() / 2], v[v.size() * 9 / 10], s.thread_s / wall);
    return b;
}

int main(int argc, char** argv)
{
    if (argc < 7)
    {
        fprintf(stderr, "usage: hetero DIR C H W SECS cpu|gpu|both ...\n");
        return 2;
    }
    std::string dir = argv[1];
    int c = atoi(argv[2]), h = atoi(argv[3]), w = atoi(argv[4]);
    double secs = atof(argv[5]);
    const int GPU_CPU = 3;

    ncnn::Mat in(w, h, c);
    unsigned x = 12345;
    for (int i = 0; i < (int)in.total(); i++)
    {
        x = x * 1103515245 + 12345;
        ((float*)in.data)[i] = (x >> 8) / 16777216.f;   // [0, 1): the models normalize inside (MemoryData mean/std)
    }

    if (ncnn::get_gpu_count() < 1)
    {
        fprintf(stderr, "ncnn sees no Vulkan GPU (source vk-env.sh first)\n");
        return 1;
    }
    ncnn::Net cpu_net, gpu_net;
    load(cpu_net, dir, false);
    load(gpu_net, dir, true);

    // The two nets must agree before their speeds mean anything.
    ncnn::Mat a = infer(cpu_net, in), b = infer(gpu_net, in);
    a = a.reshape(a.w * a.h * a.d * a.c), b = b.reshape(b.w * b.h * b.d * b.c);
    double d = 0, m = 0;
    for (int i = 0; i < (int)a.total(); i++)
    {
        d = std::max(d, (double)fabsf(a[i] - b[i]));
        m = std::max(m, (double)fabsf(a[i]));
    }
    printf("{\"model\": \"%s\", \"check\": {\"outputs\": %zu, \"max_abs_diff\": %.4g, \"max_abs\": %.4g}}\n", dir.c_str(),
           a.total(), d, m);
    fflush(stdout);

    std::string tz[3] = {zone("LITTLE0"), zone("LITTLE1"), zone("G3D")};
    for (int k = 6; k < argc; k++)
    {
        std::string ph = argv[k];
        bool use_cpu = ph == "cpu" || ph == "both", use_gpu = ph == "gpu" || ph == "both";
        if (!use_cpu && !use_gpu)
        {
            fprintf(stderr, "bad phase %s\n", ph.c_str());
            return 2;
        }
        Stat sc, sg;
        std::atomic<int> ready(0);
        std::atomic<double> t_end(0);
        std::thread g;
        if (use_gpu) g = std::thread(loop, std::ref(gpu_net), std::cref(in), GPU_CPU, std::ref(ready), std::ref(t_end), std::ref(sg));
        while (ready.load() < (use_gpu ? 1 : 0)) std::this_thread::yield();

        // Sampler: CPU4 clock and temperatures every 0.5 s while the phase runs.
        std::atomic<bool> stop(false);
        double mhz = 0, t0 = 0, t1 = 0, tg = 0;
        int ns = 0;
        std::thread smp([&] {
            pin(0);
            while (!stop)
            {
                mhz += rd("/sys/devices/system/cpu/cpu4/cpufreq/scaling_cur_freq") / 1e3;
                t0 += rd(tz[0]) / 1e3, t1 += rd(tz[1]) / 1e3, tg += rd(tz[2]) / 1e3;
                ns++;
                for (int i = 0; i < 50 && !stop; i++) usleep(10000);
            }
        });

        double p0 = proctime(), w0 = now();
        if (use_cpu)
        {
            for (int i = 0; i < 2; i++) infer(cpu_net, in);
            p0 = proctime(), w0 = now();
            t_end = w0 + secs;
            loop(cpu_net, in, -1, ready, t_end, sc);
        }
        else
        {
            t_end = w0 + secs;
        }
        if (use_gpu) g.join();
        double wall = now() - w0, pc = (proctime() - p0) / wall;
        stop = true;
        smp.join();

        double tot = sc.ms.size() / wall + sg.ms.size() / wall;
        printf("{\"phase\": \"%s\", \"secs\": %.1f, %s%s\"total_ips\": %.3f, \"process_cores\": %.2f, "
               "\"cpu4_mhz\": %.0f, \"little0_c\": %.1f, \"little1_c\": %.1f, \"g3d_c\": %.1f}\n",
               ph.c_str(), wall, stat_json("cpu", sc, wall).c_str(), stat_json("gpu", sg, wall).c_str(), tot, pc,
               mhz / ns, t0 / ns, t1 / ns, tg / ns);
        fflush(stdout);
    }
    return 0;
}
