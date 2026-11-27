#!/usr/bin/env python3
"""layercost.py: per-layer cost of an NCNN model on the E850's CPU and GPU (11.118), the input a CPU+GPU layer split
needs. Each Convolution / ConvolutionDepthWise / InnerProduct layer of the model is rebuilt as a one-layer net with
its own parameters, the input shape it sees in the model and random weights, then timed on
  cpu: NCNN FP16 storage+arithmetic, THREADS threads (run under taskset, OMP_PROC_BIND=close OMP_PLACES=cores)
  gpu: NCNN Vulkan FP32 on panvk (source e850gpu/vk-env.sh first), winograd off as in runtimes.NCNNVK
A one-layer GPU net also uploads its input and downloads its output. That overhead is measured once on ReLU nets of
several sizes (time against bytes moved, interpolated) and subtracted (gpu_net_ms). The CPU side gets the same
treatment for its FP32<->FP16 packing. The split columns are what an ideal split of each layer between the two would take
(t_cpu * t_gpu / (t_cpu + t_gpu), the GPU share t_cpu / (t_cpu + t_gpu)), with no synchronisation cost.

  python3 e850gpu/layercost.py MODEL [THREADS] [REPS]      (on the board, from ~/msbench; PYTHONPATH=py/3.11)

Results go to results/layercost/e850-MODEL-<time>.json."""
import json
import os
import struct
import sys
import time

import numpy as np
import ncnn

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
KINDS = ('Convolution', 'ConvolutionDepthWise', 'InnerProduct')
TMP = '/var/tmp/layercost'


def parse_param(path):
    lines = open(path).read().split('\n')
    out = []
    for ln in lines[2:]:
        f = ln.split()
        if not f:
            continue
        kind, name, nin, nout = f[0], f[1], int(f[2]), int(f[3])
        ins, outs, kv = f[4:4 + nin], f[4 + nin:4 + nin + nout], f[4 + nin + nout:]
        p = dict(a.split('=', 1) for a in kv)
        out.append(dict(kind=kind, name=name, ins=ins, outs=outs, kv=kv, p=p))
    return out


def opt(net, gpu, threads):
    o = net.opt
    o.use_vulkan_compute = gpu
    o.num_threads = threads
    o.lightmode = False
    if gpu:
        o.use_winograd_convolution = False
        o.use_fp16_packed = o.use_fp16_storage = o.use_fp16_arithmetic = False
    else:
        o.use_fp16_packed = o.use_fp16_storage = o.use_fp16_arithmetic = True


def blob_shapes(d, files, size, names):
    """the (c, h, w) of the blobs NAMES of the model on one input, from a CPU FP32 run"""
    net = ncnn.Net()
    opt(net, False, 4)
    net.opt.use_fp16_packed = net.opt.use_fp16_storage = net.opt.use_fp16_arithmetic = False
    assert net.load_param(os.path.join(d, files[0])) == 0 and net.load_model(os.path.join(d, files[1])) == 0
    ex = net.create_extractor()
    x = np.random.RandomState(0).rand(3, size, size).astype(np.float32)     # the Mat points into x: keep x alive
    ex.input('in0', ncnn.Mat(x))
    shapes = {}
    for b in names:
        r, m = ex.extract(b)
        if r == 0:
            shapes[b] = np.array(m).shape
    return shapes


def one_layer(L, shape, rs):
    """param text and weight bytes of a net Input -> L, weights random, the model file's own layout"""
    c, h, w = (shape + (1, 1))[:3] if len(shape) < 3 else shape
    p = L['p']
    if L['kind'] == 'InnerProduct':
        nw, nb = int(p['2']), int(p['0']) if p.get('1', '0') == '1' else 0
    else:
        nw, nb = int(p['6']), int(p['0']) if p.get('5', '0') == '1' else 0
    param = '7767517\n2 2\nInput in0 0 1 in0 0=%d 1=%d 2=%d\n%s L 1 1 in0 out0 %s\n' % (w, h, c, L['kind'], ' '.join(L['kv']))
    bin_ = struct.pack('<I', 0) + (rs.randn(nw) * 0.05).astype(np.float32).tobytes() + \
        np.zeros(nb, np.float32).tobytes()                      # tag 0 = raw fp32 weights; bias has no tag
    return param, bin_


def build(param, bin_, gpu, threads, key):
    pp, bp = '%s/%s.param' % (TMP, key), '%s/%s.bin' % (TMP, key)
    open(pp, 'w').write(param)
    open(bp, 'wb').write(bin_)
    net = ncnn.Net()
    opt(net, gpu, threads)
    assert net.load_param(pp) == 0 and net.load_model(bp) == 0
    return net


def timeit(net, x, reps):
    m = ncnn.Mat(x)
    def run():
        ex = net.create_extractor()
        ex.input('in0', m)
        r, y = ex.extract('out0')
        assert r == 0
        return np.array(y)
    for _ in range(3):
        y = run()
    t = []
    for _ in range(reps):
        a = time.perf_counter()
        run()
        t.append((time.perf_counter() - a) * 1e3)
    return float(np.median(t)), y.shape


def overhead_fit(threads, reps, gpu):
    """[(bytes moved, ms)] of ReLU nets (input and output the same size): a one-layer net's cost without its layer"""
    pts = []
    for c, hw in ((16, 16), (64, 28), (64, 56), (64, 112), (32, 224)):
        net = build('7767517\n2 2\nInput in0 0 1 in0 0=%d 1=%d 2=%d\nReLU L 1 1 in0 out0\n' % (hw, hw, c), b'', gpu,
                    threads, 'relu')
        t, _ = timeit(net, np.ones((c, hw, hw), np.float32), reps)
        pts.append((2 * c * hw * hw * 4, round(t, 3)))
    return pts


def overhead(pts, nbytes):
    x, y = np.array(pts).T
    if nbytes > x[-1]:                          # beyond the largest ReLU: extend its last slope
        return y[-1] + (nbytes - x[-1]) * (y[-1] - y[-2]) / (x[-1] - x[-2])
    return float(np.interp(nbytes, x, y))


def flops(L, cin, out_shape):
    p = L['p']
    if L['kind'] == 'InnerProduct':
        return 2 * int(p['2'])
    k = int(p.get('1', '1')) * int(p.get('11', p.get('1', '1')))
    g = int(p.get('7', '1')) if L['kind'] == 'ConvolutionDepthWise' else 1
    return 2 * (cin // g) * k * int(p['0']) * int(np.prod(out_shape[1:]))


def main():
    model = sys.argv[1]
    threads = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    reps = int(sys.argv[3]) if len(sys.argv) > 3 else 15
    os.chdir(ROOT)
    os.makedirs(TMP, exist_ok=True)
    assert ncnn.get_gpu_count() > 0, 'no Vulkan GPU: source e850gpu/vk-env.sh first'
    man = json.load(open('models/files/manifest.json'))[model]
    d = os.path.join('models/files', model)
    files = man['files']['ncnn']
    layers = [L for L in parse_param(os.path.join(d, files[0])) if L['kind'] in KINDS]
    shapes = blob_shapes(d, files, man['input']['size'], [L['ins'][0] for L in layers])
    fit = {'cpu': overhead_fit(threads, reps, False), 'gpu': overhead_fit(threads, reps, True)}
    rs = np.random.RandomState(1)
    rows = []
    for i, L in enumerate(layers):
        shp = shapes.get(L['ins'][0])
        if shp is None:
            continue
        param, bin_ = one_layer(L, tuple(shp), rs)
        x = rs.rand(*shp).astype(np.float32)
        r = {'layer': L['name'], 'kind': L['kind'], 'in': list(shp)}
        for side, gpu in (('cpu', False), ('gpu', True)):
            net = build(param, bin_, gpu, threads, 'l')
            t, oshape = timeit(net, x, reps)
            r[side + '_ms'] = round(t, 3)
            r[side + '_net_ms'] = round(max(t - overhead(fit[side], 4 * (x.size + int(np.prod(oshape)))), 0.001), 3)
            r['out'] = list(oshape)
        r['mflop'] = round(flops(L, shp[0], r['out']) / 1e6, 2)
        tc, tg = r['cpu_net_ms'], r['gpu_net_ms']
        r.update(gpu_over_cpu=round(tg / tc, 2), split_ms=round(tc * tg / (tc + tg), 3), gpu_share=round(tc / (tc + tg), 3),
                 cpu_gflops=round(r['mflop'] / tc, 1), gpu_gflops=round(r['mflop'] / tg, 1))   # MFLOP / ms
        rows.append(r)
        print(json.dumps(r), flush=True)
    s = {k: round(sum(r[k] for r in rows), 2) for k in ('cpu_net_ms', 'gpu_net_ms', 'split_ms', 'mflop')}
    res = {'model': model, 'threads': threads, 'reps': reps, 'env': {k: os.environ.get(k) for k in ('VK_ICD_FILENAMES', 'PAN_KBASE_UNCACHED')},
           'relu_bytes_ms': fit, 'sum': s,
           'ideal_split_speedup_vs_cpu': round(s['cpu_net_ms'] / s['split_ms'], 3), 'layers': rows}
    print(json.dumps({k: v for k, v in res.items() if k != 'layers'}))
    os.makedirs('results/layercost', exist_ok=True)
    p = 'results/layercost/e850-%s-%s.json' % (model, time.strftime('%Y%m%d-%H%M%S'))
    json.dump(res, open(p, 'w'), indent=1)
    print('wrote', p)


if __name__ == '__main__':
    main()
