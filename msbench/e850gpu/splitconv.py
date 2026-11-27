#!/usr/bin/env python3
"""splitconv.py: one convolution split by output channels between the E850's CPU and GPU, both parts running at the
same time (11.118). The CPU part is NCNN FP16 with 4 threads on CPU4-7, the GPU part NCNN Vulkan FP32 on panvk driven
by a second Python thread pinned to CPU3 (ncnn's extract() releases the GIL). Each iteration hands the same input
to both, waits for both and concatenates the outputs on the host, as a split layer inside a model would. For every
GPU share it prints the measured time next to the ideal, the slower of the two parts timed alone; the gap is what
running them together costs (shared DRAM, the GPU thread's CPU work, the hand-off).

  OMP_PROC_BIND=close OMP_PLACES='{4},{5},{6},{7}' python3 e850gpu/splitconv.py C HW O K S [SHARE ...]
  (on the board from ~/msbench, e850gpu/vk-env.sh sourced, PYTHONPATH=py/3.11)

Results are appended to results/layercost/e850-split.jsonl."""
import json
import os
import sys
import threading
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from layercost import ROOT, TMP, build      # noqa: E402  (imports ncnn)

REPS = 30


def conv_net(c, hw, w, k, s, gpu, threads, key):
    o = w.shape[0]
    param = '7767517\n2 2\nInput in0 0 1 in0 0=%d 1=%d 2=%d\nConvolution L 1 1 in0 out0 0=%d 1=%d 3=%d 4=%d 5=1 6=%d\n' % (
        hw, hw, c, o, k, s, k // 2, w.size)
    return build(param, np.zeros(1, np.uint32).tobytes() + w.tobytes() + np.zeros(o, np.float32).tobytes(), gpu,
                 threads, key)


def conv_ref(x, w, s):
    """float64 direct convolution, padding k // 2: the reference both parts are checked against"""
    o, c, k, _ = w.shape
    p = k // 2
    xp = np.pad(x.astype(np.float64), ((0, 0), (p, p), (p, p)))
    h = (x.shape[1] + 2 * p - k) // s + 1
    cols = np.stack([xp[:, i:i + s * h:s, j:j + s * h:s] for i in range(k) for j in range(k)], 1)   # c, k*k, h, h
    return np.einsum('ckhw,ock->ohw', cols, w.reshape(o, c, k * k).astype(np.float64))


def run(net, m):
    ex = net.create_extractor()
    ex.input('in0', m)
    r, y = ex.extract('out0')
    assert r == 0
    return np.array(y)


def median_ms(f):
    for _ in range(3):
        f()
    t = []
    for _ in range(REPS):
        a = time.perf_counter()
        f()
        t.append((time.perf_counter() - a) * 1e3)
    return float(np.median(t))


def main():
    import ncnn
    c, hw, o, k, s = [int(v) for v in sys.argv[1:6]]
    shares = [float(v) for v in sys.argv[6:]] or [0, 0.125, 0.25, 0.375, 0.5, 1]
    os.chdir(ROOT)
    os.makedirs(TMP, exist_ok=True)
    rs = np.random.RandomState(0)
    w = (rs.randn(o, c, k, k) * 0.05).astype(np.float32)
    x = rs.rand(c, hw, hw).astype(np.float32)
    m = ncnn.Mat(x)
    ref = conv_ref(x, w, s)
    run(conv_net(c, hw, w, k, s, False, 4, 'ref'), m)            # warms the CPU threads
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
            box['y'] = run(box['net'], m)
            done.wait()

    th = threading.Thread(target=gpu_thread, daemon=True)
    th.start()
    for share in shares:
        og = int(round(o * share / 8)) * 8
        oc = o - og
        cn = conv_net(c, hw, w[:oc], k, s, False, 4, 'c') if oc else None
        gn = conv_net(c, hw, w[oc:], k, s, True, 1, 'g') if og else None
        r = {'c': c, 'hw': hw, 'o': o, 'k': k, 's': s, 'gpu_channels': og,
             'driver': {v: os.environ.get(v) for v in ('VK_ICD_FILENAMES', 'PAN_KBASE_UNCACHED')},
             'mflop': round(2 * c * k * k * o * ref.shape[1] * ref.shape[2] / 1e6, 1)}
        r['cpu_part_ms'] = round(median_ms(lambda: run(cn, m)), 3) if cn else 0
        if gn:
            box['net'] = gn
            r['gpu_part_ms'] = round(median_ms(lambda: (go.wait(), done.wait())), 3)
        else:
            r['gpu_part_ms'] = 0

        def both():
            if gn:
                go.wait()
            yc = run(cn, m) if cn else None
            if gn:
                done.wait()
            return np.concatenate([a for a in (yc, box.get('y') if gn else None) if a is not None])
        y = both()
        r['max_rel_err'] = float(np.abs(y - ref).max() / np.abs(ref).max())
        r['split_ms'] = round(median_ms(both), 3)
        tc, tg = r['cpu_part_ms'], r['gpu_part_ms']
        r['ideal_ms'] = max(tc, tg)             # the two parts overlapping perfectly
        print(json.dumps(r), flush=True)
        os.makedirs('results/layercost', exist_ok=True)
        with open('results/layercost/e850-split.jsonl', 'a') as f:
            f.write(json.dumps(dict(r, time=time.strftime('%Y-%m-%dT%H:%M:%S'))) + '\n')
    box['stop'] = True
    go.wait()


if __name__ == '__main__':
    main()
