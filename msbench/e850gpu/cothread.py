#!/usr/bin/env python3
"""cothread.py: corun.py's frame-level CPU+GPU co-run inside one process (11.118), the way an NCNN extension would run
it. The main thread (NCNN FP16, 4 OpenMP threads on CPU4-7) and a GPU thread (NCNN Vulkan FP32, 1 thread, pinned to CPU3
again before every run: libgomp binds it to CPU4 on its first OpenMP region) each infer their model nonstop: each alone
for SECS, then both together for SECS. With 4 threads the GPU side's CPU-fallback team would land on CPU4-7 here.

  OMP_PROC_BIND=close OMP_PLACES='{4},{5},{6},{7}' taskset -c 3-7 python3 e850gpu/cothread.py SECS CPUMODEL:GPUMODEL ...
  (on the board from ~/msbench, e850gpu/vk-env.sh sourced, PYTHONPATH=py/3.11)

Results go to results/corun/e850-thread-<time>.json."""
import json
import os
import sys
import threading
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def load(rt_key, model, threads):
    from runtimes import RUNTIMES
    man = json.load(open('models/files/manifest.json'))[model]
    cls, key = RUNTIMES[rt_key]
    rt = cls(os.path.join('models/files', model), man['files'][key], threads)
    s = man['input']['size']
    x = np.load('data/files/%s.npz' % ('coco-eval-%d' % s if model.startswith('yolo') else 'imagenet-eval-224'))['x'][0]
    return rt, rt.prepare(x)


def loop(rt, xi, pin, secs, start, out):
    for _ in range(3):
        if pin:
            os.sched_setaffinity(0, pin)
        rt.infer(xi)
    start.wait()
    t, c0, w0 = [], time.thread_time(), time.perf_counter()
    while time.perf_counter() - w0 < secs:
        if pin:
            os.sched_setaffinity(0, pin)
        a = time.perf_counter()
        rt.infer(xi)
        t.append((time.perf_counter() - a) * 1e3)
    wall = time.perf_counter() - w0
    out.update(n=len(t), ips=round(len(t) / wall, 3), median_ms=round(float(np.median(t)), 2),
               p90_ms=round(float(np.percentile(t, 90)), 2), thread_cores=round((time.thread_time() - c0) / wall, 3))


def rd(p):
    try:
        return int(open(p).read())
    except OSError:
        return 0


def sample(smp, stop):
    while not stop.is_set():
        smp.append([rd('/sys/devices/system/cpu/cpu4/cpufreq/scaling_cur_freq') / 1e3] +
                   [rd('/sys/class/thermal/thermal_zone%d/temp' % z) / 1e3 for z in (1, 2)])
        time.sleep(0.5)


def run(c, g, secs):
    """the CPU job's and the GPU job's results (either may be None), then CPU4's clock (MHz) and the LITTLE1 and G3D
    temperatures (C), means of 0.5 s samples: a busy GPU heats the SoC and the CPU cluster throttles (11.118).
    The CPU job runs in this, the main thread: in another thread its OpenMP team is a second libgomp pool on CPU5-7 next
    to the main thread's (made while loading the model), and with more OpenMP threads than CPUs libgomp spins only
    briefly before sleeping; NCNN in a new thread was 30% slower (MobileNetV2 21 -> 28 ms)."""
    jobs = [j for j in (c, g) if j]
    start, res = threading.Barrier(len(jobs)), {'c': {}, 'g': {}}
    smp, stop = [], threading.Event()
    th = [threading.Thread(target=sample, args=(smp, stop))]
    if g:
        th.append(threading.Thread(target=loop, args=(*g, secs, start, res['g'])))
    for t in th:
        t.start()
    if c:
        loop(*c, secs, start, res['c'])
    else:
        th[1].join()
    stop.set()
    for t in th:
        t.join()
    m = np.mean(smp, 0) if smp else [0, 0, 0]
    return res['c'], res['g'], {'cpu4_mhz': round(float(m[0])), 'little1_c': round(float(m[1]), 1), 'g3d_c': round(float(m[2]), 1)}


def main():
    os.chdir(ROOT)
    sys.path.insert(0, 'benchmark')
    secs = float(sys.argv[1])
    out = {'secs': secs, 'cpu_worker': 'ncnn fp16, 4 threads, OMP places CPU4-7, the main Python thread',
           'gpu_worker': 'ncnn-vk fp32, 1 thread, Python thread re-pinned to CPU3 before every run',
           'driver': {v: os.environ.get(v) for v in ('VK_ICD_FILENAMES', 'PAN_KBASE_UNCACHED')}, 'cases': []}
    for case in sys.argv[2:]:
        cm, gm = case.split(':')
        c = load(('ncnn', 'fp16'), cm, 4) + (None,)
        g = load(('ncnn-vk', 'fp32'), gm, 1) + ({3},)
        r = {'cpu_model': cm, 'gpu_model': gm}
        r['cpu_alone'], _, r['clock_cpu_alone'] = run(c, None, secs)
        _, r['gpu_alone'], r['clock_gpu_alone'] = run(None, g, secs)
        r['cpu_with'], r['gpu_with'], r['clock_with'] = run(c, g, secs)
        if cm == gm:
            r['sum_over_cpu_alone'] = round((r['cpu_with']['ips'] + r['gpu_with']['ips']) / r['cpu_alone']['ips'] - 1, 4)
        print(json.dumps(r), flush=True)
        out['cases'].append(r)
    os.makedirs('results/corun', exist_ok=True)
    p = 'results/corun/e850-thread-%s.json' % time.strftime('%Y%m%d-%H%M%S')
    json.dump(out, open(p, 'w'), indent=1)
    print('wrote', p)


if __name__ == '__main__':
    main()
