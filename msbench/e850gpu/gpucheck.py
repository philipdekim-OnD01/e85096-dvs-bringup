#!/usr/bin/env python3
"""gpucheck.py MODEL [N] [gl|vk]: is the E850 GPU giving the CPU's answer? Runs the same first N evaluation images
through TFLite on the CPU (ai-edge-litert, XNNPACK) and through TFLite GL at FP32 and FP16 (gl, 11.117, after
`. e850gpu/gpu-env.sh`), or through NCNN on the CPU and NCNN Vulkan on panvk (vk, 11.118, after `. e850gpu/vk-env.sh`), and prints the
delegate's coverage, the median time, and the output difference (max abs, and for classifiers the top-1 agreement).
Run on the board from ~/msbench, with PYTHONPATH=py/3.11."""
import json
import os
import sys
import time

import numpy as np

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, 'benchmark')
from runtimes import NCNN, NCNNVK, NCNNVK16, TFLite, TFLiteGL, TFLiteGL16  # noqa: E402

m, n = sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 8
vk = len(sys.argv) > 3 and sys.argv[3] == 'vk'
man = json.load(open('models/files/manifest.json'))[m]
d, f = os.path.join('models/files', m), man['files']['ncnn' if vk else 'tflite_fp32']
s = man['input']['size']
x = np.load('data/files/%s.npz' % ('coco-eval-%d' % s if m.startswith('yolo') else 'imagenet-eval-224'))['x'][:n]
outs = {}
gpu = [('vk32', NCNNVK), ('vk16', NCNNVK16)] if vk else [('gl32', TFLiteGL), ('gl16', TFLiteGL16)]
for name, cls in [('cpu', NCNN if vk else TFLite)] + gpu:
    rt = cls(d, f, 4)
    xs = [rt.prepare(i) for i in x]
    rt.infer(xs[0])
    t, o = [], []
    for xi in xs:
        a = time.perf_counter()
        o.append(rt.infer(xi))
        t.append((time.perf_counter() - a) * 1e3)
    outs[name] = np.stack(o).reshape(n, -1)
    print('%s: median %.1f ms %s' % (name, np.median(t), {k: v for k, v in rt.info.items() if k in ('delegated',
                                                                                               'max_delegated_partitions')}))
for name, _ in gpu:
    a, b = outs['cpu'], outs[name]
    line = '%s vs cpu: max abs %.4g, mean abs %.4g' % (name, np.abs(a - b).max(), np.abs(a - b).mean())
    if not m.startswith('yolo'):
        line += ', top-1 same %d/%d' % ((a.reshape(n, -1).argmax(1) == b.reshape(n, -1).argmax(1)).sum(), n)
    print(line)
