#!/usr/bin/env python3
"""branchsplit.py: a ResNet downsample block split by branch between the E850's CPU and GPU (11.118). The main path
(3x3 stride 2 -> ReLU -> 3x3) runs on the CPU, NCNN FP16 with 4 threads on CPU4-7; the 1x1 stride-2 shortcut runs at
the same time on the GPU, NCNN Vulkan FP32 driven by a Python thread pinned to CPU3, as in splitconv.py. The outputs
are added on the host. Prints the whole block on the CPU, each branch alone and the two branches together (median
of 30), weights random, checked against a float64 reference, and the main path with the same hand-off but an idle
GPU thread (main_cpu_handoff_ms).

  OMP_PROC_BIND=close OMP_PLACES='{4},{5},{6},{7}' taskset -c 3-7 python3 e850gpu/branchsplit.py C HW [C HW ...]
  (on the board from ~/msbench, e850gpu/vk-env.sh sourced, PYTHONPATH=py/3.11)

Results are appended to results/layercost/e850-branch.jsonl."""
import json
import os
import sys
import threading
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from layercost import ROOT, TMP, build      # noqa: E402  (imports ncnn)
from splitconv import conv_net, conv_ref, median_ms, run      # noqa: E402


def conv_line(name, inp, out, w, k, s, relu):
    return 'Convolution %s 1 1 %s %s 0=%d 1=%d 3=%d 4=%d 5=1 6=%d%s\n' % (
        name, inp, out, w.shape[0], k, s, k // 2, w.size, ' 9=1' if relu else '')


def wbytes(w):
    return np.zeros(1, np.uint32).tobytes() + w.tobytes() + np.zeros(w.shape[0], np.float32).tobytes()


def main():
    import ncnn
    os.chdir(ROOT)
    os.makedirs(TMP, exist_ok=True)
    box = {}
    go, done = threading.Barrier(2), threading.Barrier(2)

    def gpu_thread():
        while True:
            go.wait()
            # this thread only, every time: its first NCNN run binds it to OMP_PLACES' first place (CPU4, libgomp), where
            # the CPU part runs; the results before 11.118's last runs had that (gpu thread on CPU4)
            os.sched_setaffinity(0, {3})
            if box.get('stop'):
                return
            t = time.thread_time()
            box['y'] = run(box['net'], box['m']) if box['net'] else 0
            box.setdefault('cpu', []).append((time.thread_time() - t) * 1e3)
            done.wait()

    threading.Thread(target=gpu_thread, daemon=True).start()
    a = [int(v) for v in sys.argv[1:]]
    for c, hw in zip(a[::2], a[1::2]):
        o, h = 2 * c, hw // 2
        rs = np.random.RandomState(0)
        w1, w2, ws = [(rs.randn(*sh) * 0.05).astype(np.float32) for sh in ((o, c, 3, 3), (o, o, 3, 3), (o, c, 1, 1))]
        x = rs.rand(c, hw, hw).astype(np.float32)
        m = box['m'] = ncnn.Mat(x)
        ref = conv_ref(np.maximum(conv_ref(x, w1, 2), 0).astype(np.float32), w2, 1) + conv_ref(x, ws, 2)
        head = '7767517\n%d %d\nInput in0 0 1 in0 0=%d 1=%d 2=%d\n'
        main_p = head % (3, 3, hw, hw, c) + conv_line('a', 'in0', 'a', w1, 3, 2, True) + conv_line('b', 'a', 'out0', w2, 3, 1, False)
        block_p = (head % (6, 7, hw, hw, c) + 'Split sp 1 2 in0 i1 i2\n' + conv_line('a', 'i1', 'a', w1, 3, 2, True) +
                   conv_line('b', 'a', 'b', w2, 3, 1, False) + conv_line('s', 'i2', 's', ws, 1, 2, False) +
                   'BinaryOp add 2 1 b s out0 0=0\n')
        cpu_main = build(main_p, wbytes(w1) + wbytes(w2), False, 4, 'bm')
        cpu_block = build(block_p, wbytes(w1) + wbytes(w2) + wbytes(ws), False, 4, 'bb')
        gpu_short = box['net'] = conv_net(c, hw, ws, 1, 2, True, 1, 'bs')

        def both():
            go.wait()
            y = run(cpu_main, m)
            done.wait()
            return y + box['y']
        r = {'c': c, 'hw': hw, 'o': o, 'driver': {v: os.environ.get(v) for v in ('VK_ICD_FILENAMES', 'PAN_KBASE_UNCACHED')}}
        rel = lambda y: float(np.abs(y - ref).max() / np.abs(ref).max())
        r['block_cpu_rel_err'], r['split_rel_err'] = rel(run(cpu_block, m)), rel(both())
        r['block_cpu_ms'] = round(median_ms(lambda: run(cpu_block, m)), 3)
        r['main_cpu_ms'] = round(median_ms(lambda: run(cpu_main, m)), 3)
        box['cpu'] = []
        r['shortcut_gpu_ms'] = round(median_ms(lambda: (go.wait(), done.wait())), 3)
        r['shortcut_thread_cpu_ms'] = round(float(np.median(box['cpu'])), 3)    # CPU3 time the GPU path itself uses
        r['split_ms'] = round(median_ms(both), 3)
        box['net'] = None               # the same hand-off with the GPU thread doing nothing: what the barriers cost
        r['main_cpu_handoff_ms'] = round(median_ms(both), 3)
        box['net'] = gpu_short
        r['ideal_ms'] = max(r['main_cpu_ms'], r['shortcut_gpu_ms'])
        print(json.dumps(r), flush=True)
        os.makedirs('results/layercost', exist_ok=True)
        with open('results/layercost/e850-branch.jsonl', 'a') as f:
            f.write(json.dumps(dict(r, time=time.strftime('%Y-%m-%dT%H:%M:%S'))) + '\n')
    box['stop'] = True
    go.wait()


if __name__ == '__main__':
    main()
