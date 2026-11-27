#!/usr/bin/env python3
"""corun.py: the E850's CPU and GPU running at the same time (11.118). How much does a GPU worker (TFLite GL) add to a
CPU worker (NCNN), and how much do the two slow each other down (they share the DRAM and the GPU worker's CPU fallback
and driver run on CPU cores)? For each case it runs the CPU worker alone, the GPU worker alone, then both together,
each for SECS seconds of back-to-back inferences on one prepared evaluation image, and prints per worker the
inferences per second, the median ms and the CPU cores its process used.

  python3 e850gpu/corun.py [SECS] [CASE ...]     (on the board, from ~/msbench; PYTHONPATH=py/3.11)

A CASE is CPUMODEL:GPUMODEL[:PARTITIONS[:GPURUNTIME]], e.g. resnet18:resnet18 or yolo11n-320:resnet18 (detection
on the CPU, classification on the GPU, the multi-sensor split). The CPU worker is NCNN FP16 with 4 threads on CPU4-7
(OMP_PROC_BIND=close OMP_PLACES=cores, 11.116). The GPU worker is GPURUNTIME: tflite-gl (FP16, the GLES environment
of gpu-env.sh, default) or ncnn-vk (FP32, panvk, vk-env.sh, 11.118), its CPU fallback threads on CPU0-3. Each worker
is its own process. Results go to results/corun/e850-<time>.json.
Worker mode (internal): corun.py worker RUNTIME PRECISION MODEL THREADS PARTITIONS SECS START_AT"""
import json
import os
import resource
import subprocess
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def worker(rt_name, prec, model, threads, parts, secs, start_at):
    os.chdir(ROOT)
    sys.path.insert(0, 'benchmark')
    os.environ['MSB_GL_PARTITIONS'] = parts
    from runtimes import RUNTIMES
    man = json.load(open('models/files/manifest.json'))[model]
    cls, key = RUNTIMES[(rt_name, prec)]
    rt = cls(os.path.join('models/files', model), man['files'][key], int(threads))
    s = man['input']['size']
    x = np.load('data/files/%s.npz' % ('coco-eval-%d' % s if model.startswith('yolo') else 'imagenet-eval-224'))['x'][0]
    xi = rt.prepare(x)
    for _ in range(3):
        rt.infer(xi)
    while time.time() < float(start_at):      # both workers start their timed loop together
        time.sleep(0.005)
    t, r0, w0 = [], resource.getrusage(resource.RUSAGE_SELF), time.perf_counter()
    while time.perf_counter() - w0 < float(secs):
        a = time.perf_counter()
        rt.infer(xi)
        t.append((time.perf_counter() - a) * 1e3)
    wall, r1 = time.perf_counter() - w0, resource.getrusage(resource.RUSAGE_SELF)
    cpu = r1.ru_utime + r1.ru_stime - r0.ru_utime - r0.ru_stime
    print(json.dumps({'runtime': rt_name, 'precision': prec, 'model': model, 'n': len(t), 'ips': round(len(t) / wall, 3),
                      'median_ms': round(float(np.median(t)), 2), 'p90_ms': round(float(np.percentile(t, 90)), 2),
                      'cores': round(cpu / wall, 2), 'delegated': rt.info.get('delegated')}))


def gpu_env(rt):
    """the driver environment of gpu-env.sh (tflite-gl) or vk-env.sh (ncnn-vk)"""
    e = dict(os.environ)
    if rt == 'ncnn-vk':
        m = os.path.expanduser('~/gpu/mesa-vk')
        e.update(VK_ICD_FILENAMES=m + '/icd.json', LD_PRELOAD=m + '/lib/libpanfrost_kbase_drm_shim.so',
                 PAN_I_WANT_A_BROKEN_VULKAN_DRIVER='1', PAN_KBASE_UNCACHED='1')
        return e
    m = os.path.expanduser('~/gpu/mesa-kbase/lib')
    e.update(LD_LIBRARY_PATH=m + (':' + e['LD_LIBRARY_PATH'] if e.get('LD_LIBRARY_PATH') else ''),
             LIBGL_DRIVERS_PATH=m + '/dri', LD_PRELOAD=m + '/libpanfrost_kbase_drm_shim.so', EGL_PLATFORM='surfaceless')
    return e


def run(jobs, secs):
    """jobs: [(cpus, env, args)] started together; returns their JSON lines"""
    start = time.time() + 45                  # model load + GL/Vulkan shader compile fit in this
    ps = [subprocess.Popen(['taskset', '-c', cpus, sys.executable, os.path.abspath(__file__), 'worker'] + args +
                           [str(secs), str(start)], env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           text=True) for cpus, env, args in jobs]
    out = []
    for p in ps:
        o, _ = p.communicate()
        line = [s for s in o.splitlines() if s.startswith('{')]
        out.append(json.loads(line[-1]) if line else {'error': 'worker exit %d' % p.returncode})
    return out


def main():
    a = sys.argv[1:]
    secs = int(a.pop(0)) if a and a[0].isdigit() else 30
    cases = a or ['resnet18:resnet18', 'mobilenetv2:mobilenetv2', 'yolo11n-320:yolo11n-320:2', 'yolo11n-320:resnet18']
    res = {'secs': secs, 'cpu_worker': 'ncnn fp16, 4 threads, CPU4-7, OMP_PROC_BIND=close OMP_PLACES=cores',
           'gpu_worker': 'per case: tflite-gl fp16 or ncnn-vk fp32, CPU0-3',
           'gpu_lock_khz': open('/sys/class/misc/mali0/device/dvfs_min_lock_status').read().strip(), 'cases': []}
    for c in cases:
        cm, gm, parts, grt = (c.split(':') + ['1', 'tflite-gl'][len(c.split(':')) - 2:])[:4]
        cj = ('4-7', dict(os.environ, OMP_PROC_BIND='close', OMP_PLACES='cores'), ['ncnn', 'fp16', cm, '4', '1'])
        gj = ('0-3', gpu_env(grt), [grt, 'fp32' if grt == 'ncnn-vk' else 'fp16', gm, '4', parts])
        r = {'case': c, 'cpu_alone': run([cj], secs)[0], 'gpu_alone': run([gj], secs)[0]}
        r['cpu_with_gpu'], r['gpu_with_cpu'] = run([cj, gj], secs)
        res['cases'].append(r)
        print(json.dumps(r), flush=True)
    os.makedirs(os.path.join(ROOT, 'results/corun'), exist_ok=True)
    p = os.path.join(ROOT, 'results/corun', 'e850-%s.json' % time.strftime('%Y%m%d-%H%M%S'))
    json.dump(res, open(p, 'w'), indent=1)
    print('wrote', p)


if __name__ == '__main__':
    if sys.argv[1:2] == ['worker']:
        worker(*sys.argv[2:9])
    else:
        main()
