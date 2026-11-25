#!/usr/bin/env python3
"""aibench.py CPU [N]: the DVS models' inference time on one core with one onnxruntime thread, as dvsai.c runs them
(docs 11.112, 11.115): the action classifier (x 1x2x120x160 and the GRU state h 1x128) and YuNet on the 480x360
event image (1x3x384x480, 0/255). The same script and onnxruntime 1.31.1 on the E850 and the Jetson; ACT, FACE: the
model files. Prints the median and the fastest of N runs (after 5 warm-up runs) and the cpufreq clock of CPU.
"""
import os
import sys
import time

import numpy as np
import onnxruntime as ort

cpu = int(sys.argv[1])
n = int(sys.argv[2]) if len(sys.argv) > 2 else 30
os.sched_setaffinity(0, {cpu})
rng = np.random.default_rng(1)
for name, path in (('act', os.environ.get('ACT', '/var/lib/dvsact/model.onnx')),
                   ('face', os.environ.get('FACE', '/var/lib/dvsview/face.onnx'))):
    o = ort.SessionOptions()
    o.intra_op_num_threads = o.inter_op_num_threads = 1
    o.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    s = ort.InferenceSession(path, o, providers=['CPUExecutionProvider'])
    feed = {}
    for i in s.get_inputs():
        shape = [1, 3, 384, 480] if name == 'face' else [1, 2, 120, 160] if len(i.shape) == 4 else [1, 128]
        x = rng.random(shape, dtype=np.float32)
        feed[i.name] = (x > .9) * np.float32(255) if name == 'face' else np.log1p(x * 4).astype(np.float32)
    for _ in range(5):
        s.run(None, feed)
    t = []
    for _ in range(n):
        t0 = time.perf_counter()
        s.run(None, feed)
        t.append((time.perf_counter() - t0) * 1e3)
    t.sort()
    mhz = int(open('/sys/devices/system/cpu/cpu%d/cpufreq/scaling_cur_freq' % cpu).read()) // 1000
    print('%s: median %.1f ms, fastest %.1f ms (%d runs, CPU%d at %d MHz, inputs %s)' %
          (name, t[n // 2], t[0], n, cpu, mhz, {k: v.shape for k, v in feed.items()}))
