#!/usr/bin/env python3
"""run_benchmark.py: the CPU runtime benchmark on recorded, identical inputs (msbench README, Phase 2).

  python3 benchmark/run_benchmark.py --device auto --sensor camera --runtime all --precision all
        [--models yolo11n-640,yolo11n-320,resnet18,mobilenetv2] [--threads 1,4,max] [--reps 3]
        [--warmup 50] [--iters 500] [--max-secs 25] [--accuracy] [--tag NAME]

Every configuration (model x runtime x precision x threads) runs in its own child process (clean memory peak, a crash
or hang costs one row), pinned to fixed cores (CPUSETS). The child loads the model, warms up (--warmup runs, at most
10 s), then runs --reps blocks of --iters inferences, each block cut at --max-secs (the run count is recorded: a 2 s
inference on one A55 core can not run 500 times in reasonable time). Inputs cycle over the first 16 accuracy images.
Timed: the runtime call only (input tensors are made before; NMS is timed apart for YOLO). Also per block: process CPU
time, busy % of the pinned cores and of the whole machine from /proc/stat (others' load included), clocks and
temperatures before and after, and on a Jetson the INA3221 rails (W) sampled at 10 Hz; a 3 s idle power reading first.
--accuracy: one more pass over all accuracy images at the largest thread count: classifiers top-1/top-5 here, YOLO
detections (conf 0.001, NMS IoU 0.7, max 300) to results/det/ for benchmark/evaluate.py (COCO mAP on the Mac).
--sensor dvs and fusion: not implemented yet (Phases 4 and 5), they stop with the reason.
Writes results/raw/<device>-<host>-<time>[-tag].jsonl (one line a configuration) and the same as .csv.
"""
import argparse
import csv
import glob
import json
import os
import socket
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PYDIR = os.path.join(ROOT, 'py', '%d.%d' % sys.version_info[:2])
if os.path.isdir(PYDIR):
    sys.path.insert(0, PYDIR)
# MSB_PYPATH: extra module directories ahead of PYDIR (Jetson GPU: CUDA torch, onnxruntime-gpu, tensorrt in ~/jp)
sys.path[:0] = [p for p in os.environ.get('MSB_PYPATH', '').split(os.pathsep) if p]
sys.path.insert(0, HERE)
# cores per thread count, off the busy ones: E850 CPU0 takes the device IRQs; on the Jetson CPU0 takes the IRQs and the
# live DVS pins its decoder to CPU1, renderer CPU2, filter CPU3 (the first full run used CPU1 / CPU0-3 and got 0.84 of
# CPU1 at 1 thread, 11.116); measured idle load: CPU4 3.6 %, CPU5 2.6 %
CPUSETS = {'e850': {1: [5], 4: [4, 5, 6, 7], 8: list(range(8))},
           'orin-nano': {1: [5], 4: [2, 3, 4, 5], 6: list(range(6))},
           'agx-orin': {1: [1], 4: [0, 1, 2, 3], 12: list(range(12))}}
MODELS = ['yolo11n-640', 'yolo11n-320', 'resnet18', 'mobilenetv2']


def rd(p, d=None):
    try:
        with open(p) as f:
            return f.read().strip()
    except OSError:
        return d


def device():
    m = (rd('/proc/device-tree/model', '') or '').lower()
    if 'orin nano' in m:
        return 'orin-nano'
    if 'agx orin' in m:
        return 'agx-orin'
    if 'exynos3830' in (rd('/proc/device-tree/compatible', '') or '') or 'exynos3830' in m:
        return 'e850'
    return 'unknown'


def stat():
    """{'cpu': (total, idle), 'cpuN': ...} jiffies"""
    out = {}
    for ln in rd('/proc/stat', '').splitlines():
        if ln.startswith('cpu'):
            v = [int(x) for x in ln.split()[1:9]]
            out[ln.split()[0]] = (sum(v), v[3] + v[4])
    return out


def busy(a, b, keys):
    t = sum(b[k][0] - a[k][0] for k in keys)
    i = sum(b[k][1] - a[k][1] for k in keys)
    return round(100 * (1 - i / t), 1) if t else None


def clocks(cpus):
    return [int(rd('/sys/devices/system/cpu/cpu%d/cpufreq/scaling_cur_freq' % c, 0)) // 1000 for c in cpus]


def gpu_mhz():
    f = glob.glob('/sys/class/devfreq/*.gpu/cur_freq')
    if f:
        return int(rd(f[0], 0)) // 1000000
    v = rd('/sys/class/misc/mali0/device/clock')            # E850 Mali-G52 (kbase): kHz, 0 while powered down
    return int(v) // 1000 if v and v.isdigit() else None


def temps():
    t = {}
    for z in sorted(glob.glob('/sys/class/thermal/thermal_zone*')):
        v = rd(z + '/temp')
        if v and v.lstrip('-').isdigit():
            t[rd(z + '/type', z)] = int(v) / 1000
    return t


class Power:
    """INA3221 rails (Jetson): W per rail, sampled in a thread; none elsewhere (E850: no sensor, N/A)"""
    def __init__(self):
        self.rails = {}
        for h in glob.glob('/sys/class/hwmon/hwmon*'):
            if rd(h + '/name') == 'ina3221':
                for lf in glob.glob(h + '/in*_label'):
                    n = lf[len(h) + 3:-6]
                    if os.path.exists('%s/curr%s_input' % (h, n)):
                        self.rails[rd(lf)] = ('%s/in%s_input' % (h, n), '%s/curr%s_input' % (h, n))
        self.samples, self.run = [], False

    def read(self):
        return {k: int(rd(v)) * int(rd(c)) / 1e6 for k, (v, c) in self.rails.items()}

    def start(self, period=0.1):
        self.samples, self.run = [], True

        def loop():
            while self.run:
                self.samples.append(self.read())
                time.sleep(period)
        self.th = threading.Thread(target=loop, daemon=True)
        self.th.start()

    def stop(self):
        self.run = False
        self.th.join()
        if not self.samples:
            return None
        return {k: round(sum(s[k] for s in self.samples) / len(self.samples), 3) for k in self.rails}


def pct(v, q):
    v = sorted(v)
    return v[min(len(v) - 1, int(q * len(v)))]


def summary(v):
    import statistics
    return {'n': len(v), 'mean': round(statistics.fmean(v), 3), 'median': round(statistics.median(v), 3),
            'p95': round(pct(v, .95), 3), 'min': round(min(v), 3), 'max': round(max(v), 3),
            'std': round(statistics.pstdev(v), 3)}


def child(cfg):
    """one configuration; prints one JSON line"""
    os.sched_setaffinity(0, cfg['cpus'])
    import numpy as np
    from runtimes import RUNTIMES, COCO91, nms
    man = json.load(open(os.path.join(cfg['models_dir'], 'manifest.json')))[cfg['model']]
    d = os.path.join(cfg['models_dir'], cfg['model'])
    cls, key = RUNTIMES[(cfg['runtime'], cfg['precision'])]
    yolo = cfg['model'].startswith('yolo')
    size = man['input']['size']
    norm = yolo and cfg['runtime'].startswith('tflite') and man['tflite_coords_normalized']
    data = np.load(os.path.join(cfg['data_dir'], 'coco-eval-%d.npz' % size if yolo else 'imagenet-eval-224.npz'))
    xs = data['x']
    r = dict(cfg, file=man['files'][key], file_bytes=man['bytes'][key])
    t0 = time.perf_counter()
    rt = cls(d, man['files'][key], cfg['threads'])
    r['load_ms'] = round((time.perf_counter() - t0) * 1e3, 1)
    r['runtime_info'] = rt.info
    t0 = time.perf_counter()
    inp = [rt.prepare(x) for x in xs[:16]]
    r['prepare_ms_per_input'] = round((time.perf_counter() - t0) * 1e3 / 16, 3)
    pw = Power()
    if pw.rails:
        pw.start()
        time.sleep(3)
        r['power_idle_w'] = pw.stop()
    # warm-up
    t0, n = time.perf_counter(), 0
    while n < cfg['warmup'] and (n < 3 or time.perf_counter() - t0 < 10):
        rt.infer(inp[n % 16])
        n += 1
    r['warmup_runs'] = n
    r['blocks'] = []
    allv = []
    for _ in range(cfg['reps']):
        blk = {'clock_mhz_before': clocks(cfg['cpus']), 'gpu_mhz_before': gpu_mhz(), 'temp_c_before': temps()}
        lat, post = [], []
        s0, c0 = stat(), os.times()
        if pw.rails:
            pw.start()
        t0 = time.perf_counter()
        i = 0
        while i < cfg['iters'] and (i < 10 or time.perf_counter() - t0 < cfg['max_secs']):
            a = time.perf_counter()
            out = rt.infer(inp[i % 16])
            b = time.perf_counter()
            lat.append((b - a) * 1e3)
            if yolo:
                nms(out, size, norm)
                post.append((time.perf_counter() - b) * 1e3)
            i += 1
        wall = time.perf_counter() - t0
        c1, s1 = os.times(), stat()
        if pw.rails:
            blk['power_w'] = pw.stop()
            blk['energy_j_per_inference'] = {k: round(v * wall / i, 4) for k, v in blk['power_w'].items()}
        blk.update(latency_ms=summary(lat), fps=round(i / sum(lat) * 1e3, 2), wall_s=round(wall, 2),
                   process_cpu_cores=round((c1.user + c1.system - c0.user - c0.system) / wall, 2),
                   pinned_busy_pct=busy(s0, s1, ['cpu%d' % c for c in cfg['cpus']]),
                   machine_busy_pct=busy(s0, s1, ['cpu']), clock_mhz_after=clocks(cfg['cpus']), gpu_mhz_after=gpu_mhz(),
                   temp_c_after=temps())
        if post:
            blk['nms_ms'] = summary(post)
        r['blocks'].append(blk)
        allv += lat
    r['latency_ms'] = summary(allv)
    r['fps'] = round(len(allv) / sum(allv) * 1e3, 2)
    if hasattr(rt, 'phases'):
        r['gpu_phases_ms'] = rt.phases(inp[0])                         # medians of 100 runs, synchronize between
    if cfg['accuracy']:
        t0 = time.perf_counter()
        if yolo:
            det = []
            for k, x in enumerate(xs):
                bx, sc, cl = nms(rt.infer(rt.prepare(x)), size, norm, conf=0.001)
                g, (pl, pt), (h, w) = data['gain'][k], data['pad'][k], data['shape'][k]
                bx = (bx - [pl, pt, pl, pt]) / g
                bx = bx.clip(0, [w, h, w, h])
                for (x1, y1, x2, y2), s, c in zip(bx, sc, cl):
                    det.append({'image_id': int(data['ids'][k]), 'category_id': COCO91[int(c)],
                                'bbox': [round(float(x1), 2), round(float(y1), 2), round(float(x2 - x1), 2),
                                         round(float(y2 - y1), 2)], 'score': round(float(s), 5)})
            os.makedirs(cfg['det_dir'], exist_ok=True)
            f = os.path.join(cfg['det_dir'], '%s-%s-%s-%s%s.json' % (cfg['device'], cfg['model'], cfg['runtime'],
                                                                    cfg['precision'],    # --env runs keep their own
                                                                    ''.join('-' + c for c in cfg['env'].replace('=', '').split())))
            json.dump(det, open(f, 'w'))
            r['accuracy'] = {'detections_file': os.path.relpath(f, ROOT), 'images': len(xs), 'detections': len(det)}
        else:
            top = np.stack([np.asarray(rt.infer(rt.prepare(x))).reshape(-1) for x in xs])
            o = np.argsort(-top, 1)
            y = data['y']
            r['accuracy'] = {'images': len(xs), 'top1': round(float((o[:, 0] == y).mean()), 4),
                             'top5': round(float((o[:, :5] == y[:, None]).any(1).mean()), 4)}
        r['accuracy']['secs'] = round(time.perf_counter() - t0, 1)
    st = dict(ln.split(':', 1) for ln in rd('/proc/self/status', '').splitlines() if ':' in ln)
    r['rss_peak_mb'] = round(int(st.get('VmHWM', '0 kB').split()[0]) / 1024, 1)
    print('RESULT ' + json.dumps(r))


def flat(r):
    """one CSV row"""
    b = r.get('blocks') or [{}]
    meds = [x['latency_ms']['median'] for x in b if 'latency_ms' in x]
    row = {k: r.get(k) for k in ('device', 'host', 'sensor', 'model', 'runtime', 'precision', 'threads', 'env',
                                 'file_bytes', 'load_ms', 'rss_peak_mb', 'fps', 'error')}
    row['cpus'] = ' '.join(map(str, r.get('cpus', [])))
    for k in ('mean', 'median', 'p95', 'min', 'n'):
        row['lat_' + k] = r.get('latency_ms', {}).get(k)
    row['block_medians'] = ' '.join('%.2f' % m for m in meds)
    row['nms_median_ms'] = b[0].get('nms_ms', {}).get('median') if b else None
    row['process_cpu_cores'] = b[-1].get('process_cpu_cores')
    row['pinned_busy_pct'] = b[-1].get('pinned_busy_pct')
    row['machine_busy_pct'] = b[-1].get('machine_busy_pct')
    row['clock_mhz_after'] = ' '.join(map(str, b[-1].get('clock_mhz_after', [])))
    row['gpu_mhz_after'] = b[-1].get('gpu_mhz_after')
    for k, v in (r.get('gpu_phases_ms') or {}).items():
        row['gpu_%s_ms' % k] = v
    t = b[-1].get('temp_c_after') or {}
    row['temp_max_c'] = max(t.values()) if t else None
    pw = b[-1].get('power_w') or {}
    row['power_vdd_in_w'] = pw.get('VDD_IN')
    row['energy_vdd_in_j'] = (b[-1].get('energy_j_per_inference') or {}).get('VDD_IN')
    row['power_idle_vdd_in_w'] = (r.get('power_idle_w') or {}).get('VDD_IN')
    acc = r['accuracy'] if isinstance(r.get('accuracy'), dict) else {}     # an errored config keeps cfg's bool
    row['top1'], row['top5'], row['det_file'] = acc.get('top1'), acc.get('top5'), acc.get('detections_file')
    return row


def main():
    ap = argparse.ArgumentParser(description='CPU runtime benchmark on recorded inputs (msbench Phase 2)')
    ap.add_argument('--device', default='auto', choices=['auto', 'e850', 'orin-nano', 'agx-orin'])
    ap.add_argument('--sensor', default='camera', choices=['camera', 'dvs', 'fusion', 'all'])
    ap.add_argument('--runtime', default='all')
    ap.add_argument('--precision', default='all')
    ap.add_argument('--fusion-method', default='all')
    ap.add_argument('--models', default=','.join(MODELS))
    ap.add_argument('--threads', default='1,4,max')
    ap.add_argument('--reps', type=int, default=3)
    ap.add_argument('--warmup', type=int, default=50)
    ap.add_argument('--iters', type=int, default=500)
    ap.add_argument('--max-secs', type=float, default=25)
    ap.add_argument('--accuracy', action='store_true')
    ap.add_argument('--models-dir', default=os.path.join(ROOT, 'models', 'files'))
    ap.add_argument('--data-dir', default=os.path.join(ROOT, 'data', 'files'))
    ap.add_argument('--tag', default='')
    ap.add_argument('--timeout', type=float, default=900)
    ap.add_argument('--env', action='append', default=[], metavar='VAR=VALUE',
                    help='extra environment for the children, kept in each result (e.g. OMP_PROC_BIND=close)')
    ap.add_argument('--child', help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a.child:
        return child(json.loads(a.child))
    dev = device() if a.device == 'auto' else a.device
    if a.sensor in ('dvs', 'fusion'):
        sys.exit('--sensor %s: not implemented yet (Phase %s: needs a DVS-trained YOLO11n and labelled DVS recordings, '
                 'see README)' % (a.sensor, 4 if a.sensor == 'dvs' else 5))
    from runtimes import E850GPU, GPU, RUNTIMES
    # 'all' = the CPU runtimes, 'gpu' = the GPU ones of this device (Jetson CUDA/TensorRT, E850 TFLite GL), or names.
    # E850 ncnn-vk only by name: it needs vk-env.sh, whose LD_PRELOAD differs from gpu-env.sh's (11.118)
    rts = [k for k in RUNTIMES if (k[0] in a.runtime.split(',') or a.runtime == 'all' and k not in GPU
                                   or a.runtime == 'gpu' and k in GPU and (k in E850GPU) == (dev == 'e850')
                                   and k[0] != 'ncnn-vk')
           and (a.precision == 'all' or k[1] in a.precision.split(','))]
    ncpu = os.cpu_count()
    sets = CPUSETS.get(dev, {1: [0], ncpu: list(range(ncpu))})
    threads = [max(sets) if t == 'max' else int(t) for t in a.threads.split(',')]
    host = socket.gethostname()
    stamp = time.strftime('%Y%m%d-%H%M%S')
    os.makedirs(os.path.join(ROOT, 'results', 'raw'), exist_ok=True)
    base = os.path.join(ROOT, 'results', 'raw', '%s-%s-%s%s' % (dev, host, stamp, '-' + a.tag if a.tag else ''))
    rows = []
    env = dict(os.environ)
    if os.path.isdir(PYDIR):
        env['PYTHONPATH'] = PYDIR + os.pathsep + env.get('PYTHONPATH', '')
    env.update(e.split('=', 1) for e in a.env)
    cfgs = [(m, rt, t) for m in a.models.split(',') for rt in rts for t in threads]
    print('%d configurations -> %s.jsonl' % (len(cfgs), base), flush=True)
    for k, (m, (rt, prec), t) in enumerate(cfgs):
        cfg = {'device': dev, 'host': host, 'sensor': 'camera', 'data': 'recorded', 'model': m, 'runtime': rt,
               'precision': prec, 'threads': t, 'cpus': sets.get(t, list(range(t))), 'reps': a.reps,
               'warmup': a.warmup, 'iters': a.iters, 'max_secs': a.max_secs,
               'accuracy': a.accuracy and t == max(threads), 'models_dir': a.models_dir, 'data_dir': a.data_dir,
               'det_dir': os.path.join(ROOT, 'results', 'det'), 'env': ' '.join(a.env),
               'machine_id': rd('/etc/machine-id', '').strip()[:8]}
        t0 = time.time()
        try:
            p = subprocess.run([sys.executable, os.path.abspath(__file__), '--child', json.dumps(cfg)], env=env,
                               capture_output=True, text=True, timeout=a.timeout)
            line = [ln for ln in p.stdout.splitlines() if ln.startswith('RESULT ')]
            r = json.loads(line[-1][7:]) if line else dict(cfg, error='exit %d: %s' % (p.returncode,
                                                                                    p.stderr.strip()[-400:]))
        except subprocess.TimeoutExpired:
            r = dict(cfg, error='timeout %d s' % a.timeout)
        r['time'] = time.strftime('%Y-%m-%dT%H:%M:%S')
        with open(base + '.jsonl', 'a') as f:
            f.write(json.dumps(r) + '\n')
        rows.append(flat(r))
        lm = r.get('latency_ms', {})
        print('[%d/%d] %s %s %s t%d: %s (%.0f s)' % (k + 1, len(cfgs), m, rt, prec, t,
              'median %.2f ms p95 %.2f n %d%s' % (lm['median'], lm['p95'], lm['n'],
                                                   ', acc %s' % {x: r['accuracy'][x] for x in ('top1', 'detections')
                                                                 if x in r['accuracy']} if r.get('accuracy') else '')
              if lm else 'ERROR ' + r.get('error', '?')[-200:], time.time() - t0), flush=True)
        with open(base + '.csv', 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=list(dict.fromkeys(k for x in rows for k in x)))
            w.writeheader()
            w.writerows(rows)
    print('wrote %s.jsonl and .csv' % base)


if __name__ == '__main__':
    main()
