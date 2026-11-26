#!/usr/bin/env python3
"""placement.py CPUS [VAR=VALUE ...]: where a runtime's threads run (11.116). Starts one run_benchmark child
(yolo11n-320 pytorch fp32, 4 threads, on CPUS, with the extra environment), waits out loading and warm-up, then samples
/proc/PID/task/*/stat every 20 ms for 5 s: how many of its threads are runnable (state R) and on how many distinct CPUs
those are. Runnable threads on fewer CPUs than their number = the scheduler stacked them (one waits on another's core).
  python3 benchmark/placement.py 4,5,6,7          python3 benchmark/placement.py 4,5,6,7 OMP_WAIT_POLICY=ACTIVE
Another configuration: PROBE_MODEL, PROBE_RT, PROBE_PREC in the environment (e.g. PROBE_RT=tflite)."""
import collections
import json
import os
import subprocess
import sys
import time

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
cpus = [int(c) for c in sys.argv[1].split(',')]
env = dict(os.environ, **dict(a.split('=', 1) for a in sys.argv[2:]))
cfg = {'device': 'probe', 'host': '', 'sensor': 'camera', 'data': 'recorded',
       'model': os.environ.get('PROBE_MODEL', 'yolo11n-320'), 'runtime': os.environ.get('PROBE_RT', 'pytorch'),
       'precision': os.environ.get('PROBE_PREC', 'fp32'), 'threads': len(cpus), 'cpus': cpus, 'reps': 1, 'warmup': 10,
       'iters': 5000, 'max_secs': 40, 'accuracy': False, 'models_dir': 'models/files', 'data_dir': 'data/files',
       'det_dir': '/tmp'}
p = subprocess.Popen([sys.executable, 'benchmark/run_benchmark.py', '--child', json.dumps(cfg)], env=env,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(24)                                          # import + load + warm-up (at most 10 s) on the E850


def sample():
    out = {}
    for t in os.listdir('/proc/%d/task' % p.pid):
        try:
            f = open('/proc/%d/task/%s/stat' % (p.pid, t)).read().rsplit(')', 1)[1].split()
        except OSError:
            continue
        out[t] = (f[0], int(f[36]))                     # state, CPU last run on
    return out


n = stacked = 0
run, dist = [], []
last = collections.defaultdict(collections.Counter)
t0 = time.monotonic()
while time.monotonic() - t0 < 5 and p.poll() is None:
    s = sample()
    r = [c for st, c in s.values() if st == 'R']
    run.append(len(r))
    dist.append(len(set(r)))
    stacked += len(set(r)) < len(r)
    for t, (st, c) in s.items():
        last[t][c] += 1
    n += 1
    time.sleep(0.02)
p.wait()
print('%s %s: cpus %s env %s: %d samples, runnable threads mean %.2f on distinct CPUs mean %.2f, stacked in %.0f%% of samples'
      % (cfg['model'], cfg['runtime'], sys.argv[1], ' '.join(sys.argv[2:]) or '-', n, sum(run) / max(n, 1), sum(dist) / max(n, 1),
         100 * stacked / max(n, 1)))
for t, c in sorted(last.items(), key=lambda x: int(x[0])):
    print('  thread %s: %s' % (t, ' '.join('cpu%d %d%%' % (k, 100 * v / n) for k, v in sorted(c.items()))))
