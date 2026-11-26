#!/usr/bin/env python3
"""check_environment.py: what this machine is and what the benchmark can use on it (msbench README, Phase 1).

  python3 setup/check_environment.py [--out results/env]

Standard library only: runs on the E850 (Debian 12, Python 3.11), the Jetson (Ubuntu 24.04, Python 3.12) and the Mac.
Reads, never changes: SoC and board model, CPU cores (MIDR part, features, cpufreq), memory, OS, kernel, GPU (devfreq,
device nodes, Vulkan/OpenCL ICDs, NVIDIA driver, CUDA, TensorRT, DLA), thermal zones, power monitors, AI runtime
packages (each imported in its own process: version, providers), sensors (V4L2 nodes, USB cameras, DVS drivers and
capture processes). Writes <out>/<device>-<host>.json and prints a summary. Python packages in msbench/py/<X.Y>
(the benchmark's own, unpacked wheels) are found as run_benchmark.py finds them.
"""
import argparse
import glob
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PYDIR = os.path.join(ROOT, 'py', '%d.%d' % sys.version_info[:2])
PARTS = {'0xd03': 'Cortex-A53', '0xd05': 'Cortex-A55', '0xd08': 'Cortex-A72', '0xd0b': 'Cortex-A76',
         '0xd41': 'Cortex-A78', '0xd42': 'Cortex-A78AE', '0xd4b': 'Cortex-A78C'}
MODULES = ['numpy', 'onnxruntime', 'torch', 'torchvision', 'ai_edge_litert', 'tflite_runtime', 'tensorflow', 'ncnn',
           'ultralytics', 'onnx', 'cv2', 'PIL', 'tensorrt', 'pycuda', 'cuda']


def rd(path, default=None):
    try:
        with open(path, errors='replace') as f:
            return f.read().strip().strip('\0')
    except OSError:
        return default


def run(cmd, timeout=20):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip() if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def cpus():
    info = rd('/proc/cpuinfo', '')
    cores, cur = [], {}
    for ln in info.splitlines() + ['']:
        if not ln.strip():
            if cur:
                cores.append(cur)
            cur = {}
            continue
        k, _, v = ln.partition(':')
        cur[k.strip()] = v.strip()
    out = {'count': os.cpu_count(), 'parts': sorted({PARTS.get(c.get('CPU part'), c.get('CPU part', '?')) for c in cores}),
           'features': cores[0].get('Features', '').split() if cores else [], 'policies': []}
    for p in sorted(glob.glob('/sys/devices/system/cpu/cpufreq/policy*')):
        f = lambda n: rd(os.path.join(p, n))
        out['policies'].append({'cpus': f('related_cpus'), 'governor': f('scaling_governor'),
                                'min_mhz': int(f('scaling_min_freq') or 0) // 1000,
                                'max_mhz': int(f('scaling_max_freq') or 0) // 1000,
                                'cur_mhz': int(f('scaling_cur_freq') or 0) // 1000,
                                'hw_max_mhz': int(f('cpuinfo_max_freq') or 0) // 1000})
    if not cores and sys.platform == 'darwin':
        out['parts'] = [run(['sysctl', '-n', 'machdep.cpu.brand_string']) or '?']
    return out


def memory():
    m = {}
    for ln in (rd('/proc/meminfo', '') or '').splitlines():
        k, _, v = ln.partition(':')
        if k in ('MemTotal', 'MemAvailable', 'SwapTotal'):
            m[k + '_mb'] = int(v.split()[0]) // 1024
    m['zram'] = [os.path.basename(z) for z in glob.glob('/sys/block/zram*')]
    if sys.platform == 'darwin':
        m['MemTotal_mb'] = int(run(['sysctl', '-n', 'hw.memsize']) or 0) // 2 ** 20
    return m


def osinfo():
    rel = dict(re.findall(r'^(\w+)="?([^"\n]*)"?$', rd('/etc/os-release', '') or '', re.M))
    return {'os': rel.get('PRETTY_NAME') or platform.platform(), 'kernel': platform.release(),
            'python': platform.python_version(), 'machine': platform.machine()}


def device(model, parts):
    m = (model or '').lower()
    if 'orin nano' in m or 'orin nx' in m:
        return 'orin-nano' if 'nano' in m else 'orin-nx'
    if 'agx orin' in m or ('orin' in m and 'agx' in m):
        return 'agx-orin'
    compat = rd('/proc/device-tree/compatible', '') or ''
    if 's5e3830' in compat or 'exynos3830' in compat or 'exynos850' in compat or ('Cortex-A55' in parts and 'exynos' in m):
        return 'e850'
    return 'host' if sys.platform == 'darwin' else 'unknown'


def gpu():
    g = {'devfreq': [], 'nodes': sorted(glob.glob('/dev/mali*') + glob.glob('/dev/dri/*') + glob.glob('/dev/nvgpu*')
                                         + glob.glob('/dev/nvhost-ctrl-gpu') + glob.glob('/dev/nvidia*'))}
    for d in sorted(glob.glob('/sys/class/devfreq/*')):
        g['devfreq'].append({'name': os.path.basename(d), 'governor': rd(d + '/governor'),
                             'cur_mhz': int(rd(d + '/cur_freq') or 0) // 1000000 or int(rd(d + '/cur_freq') or 0) // 1000,
                             'max_mhz': int(rd(d + '/max_freq') or 0) // 1000000 or int(rd(d + '/max_freq') or 0) // 1000})
    g['vulkan_icd'] = sorted(glob.glob('/usr/share/vulkan/icd.d/*') + glob.glob('/etc/vulkan/icd.d/*'))
    g['opencl_icd'] = sorted(glob.glob('/etc/OpenCL/vendors/*'))
    g['mali_kbase'] = os.path.isdir('/sys/module/mali_kbase')
    # the E850's GLES 3.1 path: Mesa panfrost on the kbase shim, built per user (CLAUDE.md, tools/gpu)
    g['panfrost_shim'] = sorted(glob.glob(os.path.expanduser('~/gpu/*/lib*/libGLESv2.so*'))
                                + glob.glob(os.path.expanduser('~/gpu/lib*/libGLESv2.so*')))[:3]
    smi = run(['nvidia-smi', '--query-gpu=name,driver_version', '--format=csv,noheader'])
    head = run(['nvidia-smi'])
    g['nvidia_smi'] = smi
    if head:
        m = re.search(r'CUDA Version:\s*([\d.]+)', head)
        g['cuda_driver_api'] = m and m[1]
    g['dla_cores'] = len(glob.glob('/dev/nvhost-ctrl-nvdla*')) or len(glob.glob('/sys/devices/platform/*nvdla*'))
    return g


def jetson():
    if not os.path.exists('/etc/nv_tegra_release'):
        return None
    j = {'l4t': (rd('/etc/nv_tegra_release') or '').splitlines()[0]}
    q = lambda pkgs: run(['dpkg-query', '-W', '-f', '${Package} ${Version} ${db:Status-Abbrev}\\n'] + pkgs) or ''
    inst = {}
    for ln in q(['nvidia-l4t-core', 'nvidia-jetpack', 'cuda-toolkit*', 'cuda-cudart*', 'libcudnn*', 'tensorrt',
                 'libnvinfer*', 'python3-libnvinfer']).splitlines():
        p = ln.split()
        if len(p) >= 3 and p[2].startswith('ii'):
            inst[p[0]] = p[1]
    j['installed'] = inst
    pol = run(['apt-cache', 'policy', 'nvidia-jetpack', 'tensorrt', 'cuda-toolkit'], 30) or ''
    j['apt_candidates'] = dict(re.findall(r'^(\S+):\n\s+Installed: .*\n\s+Candidate: (\S+)', pol, re.M))
    j['cuda_dirs'] = sorted(glob.glob('/usr/local/cuda*'))
    j['trtexec'] = shutil.which('trtexec') or next(iter(glob.glob('/usr/src/tensorrt/bin/trtexec')), None)
    j['nvpmodel'] = (run(['nvpmodel', '-q']) or '').splitlines()[:1]
    j['tegrastats'] = shutil.which('tegrastats')
    return j


def thermal():
    return {rd(z + '/type', os.path.basename(z)): int(rd(z + '/temp') or 0) / 1000
            for z in sorted(glob.glob('/sys/class/thermal/thermal_zone*'))}


def power():
    out = []
    for h in sorted(glob.glob('/sys/class/hwmon/hwmon*')):
        rails = {}
        for f in sorted(glob.glob(h + '/in*_label') + glob.glob(h + '/curr*_label') + glob.glob(h + '/power*_label')):
            base = f[:-len('_label')]
            rails[rd(f)] = {os.path.basename(base): rd(base + '_input')}
        chans = [os.path.basename(f) for f in glob.glob(h + '/power*_input') + glob.glob(h + '/curr*_input')]
        if rails or chans:
            out.append({'hwmon': os.path.basename(h), 'name': rd(h + '/name'), 'rails': rails, 'channels': sorted(chans)})
    return out


def pymods():
    env = dict(os.environ)
    if os.path.isdir(PYDIR):
        env['PYTHONPATH'] = PYDIR + os.pathsep + env.get('PYTHONPATH', '')
    code = r'''
import json, sys
m = sys.argv[1]; r = {}
try:
    x = __import__(m); r['version'] = getattr(x, '__version__', '?'); r['path'] = getattr(x, '__file__', None)
    if m == 'onnxruntime': r['providers'] = x.get_available_providers()
    if m == 'torch':
        r['cuda'] = x.cuda.is_available(); r['threads'] = x.get_num_threads()
        r['backends'] = {'mkldnn': x.backends.mkldnn.is_available(), 'xnnpack': hasattr(x.backends, 'xnnpack')}
    if m == 'tensorrt': r['version'] = x.__version__
except Exception as e:
    r = {'error': '%s: %s' % (type(e).__name__, str(e)[:200])}
print(json.dumps(r))
'''
    out = {}
    for m in MODULES:
        try:
            p = subprocess.run([sys.executable, '-c', code, m], capture_output=True, text=True, timeout=120, env=env)
            out[m] = json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else {'error': p.stderr[-200:]}
        except (subprocess.TimeoutExpired, ValueError, IndexError) as e:
            out[m] = {'error': repr(e)[:200]}
        if 'error' in out[m] and 'No module named' in out[m]['error']:
            out[m] = None
    return out


def sensors():
    s = {'v4l2': {}, 'usb': [], 'modules': [], 'processes': []}
    for d in sorted(glob.glob('/sys/class/video4linux/video*')):
        s['v4l2'][os.path.basename(d)] = rd(d + '/name')
    for d in sorted(glob.glob('/sys/bus/usb/devices/*')):
        v, p = rd(d + '/idVendor'), rd(d + '/idProduct')
        if v and p and rd(d + '/bDeviceClass') != '09':           # skip hubs
            s['usb'].append('%s:%s %s' % (v, p, rd(d + '/product') or ''))
    mods = {ln.split()[0] for ln in (rd('/proc/modules', '') or '').splitlines()}
    s['modules'] = sorted(mods & {'fimc_is', 'is_cis_s5krc1s', 'is_cis_ov5647', 'nrv_dvs', 'uvcvideo', 'mali_kbase',
                                  'exynos_mfc', 'nvgpu'})
    for p in glob.glob('/proc/[0-9]*/cmdline'):
        c = (rd(p) or '').replace('\0', ' ')
        if re.search(r'ov5647-cap\.py --live|dvs-jetson\.py|nrv-dvs|scope\.py|\bmpv\b|dvsgl', c):
            s['processes'].append(c[:100])
    s['processes'].sort()
    s['cx3_microscope'] = any(u.startswith('04b4:00c3') for u in s['usb'])
    return s


def load(secs=1.0):
    """whole-machine busy % (of all cores) over secs, the background the benchmark will share the CPU with"""
    def snap():
        v = [int(x) for x in (rd('/proc/stat', 'cpu 0') or 'cpu 0').splitlines()[0].split()[1:9]]
        return sum(v), v[3] + (v[4] if len(v) > 4 else 0)
    a = snap()
    time.sleep(secs)
    b = snap()
    return round(100 * (1 - (b[1] - a[1]) / max(1, b[0] - a[0])), 1)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--out', default=os.path.join(ROOT, 'results', 'env'))
    a = ap.parse_args()
    c = cpus()
    model = rd('/proc/device-tree/model') or (run(['sysctl', '-n', 'hw.model']) if sys.platform == 'darwin' else None)
    env = {'time': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'host': socket.gethostname(),
           'machine_id': (rd('/etc/machine-id') or '')[:8], 'model': model, 'device': device(model, c['parts']),
           'cpu': c, 'memory': memory(), **osinfo(), 'gpu': gpu(), 'jetson': jetson(), 'thermal_c': thermal(),
           'power_monitors': power(), 'python_modules': pymods(), 'sensors': sensors(),
           'background_busy_pct': load(2.0) if os.path.exists('/proc/stat') else None}
    os.makedirs(a.out, exist_ok=True)
    path = os.path.join(a.out, '%s-%s.json' % (env['device'], env['host']))
    with open(path, 'w') as f:
        json.dump(env, f, indent=1, ensure_ascii=False)
    print('device %s (%s), %s, %d cores %s' % (env['device'], model, env['os'], c['count'], '/'.join(c['parts'])))
    for p in c['policies']:
        print('  cpufreq %s: %s %d-%d MHz (now %d)' % (p['cpus'], p['governor'], p['min_mhz'], p['max_mhz'], p['cur_mhz']))
    print('memory %s MB, kernel %s, python %s, background %s%% busy' % (env['memory'].get('MemTotal_mb'), env['kernel'],
                                                                        env['python'], env['background_busy_pct']))
    print('runtimes: ' + ', '.join('%s %s' % (m, v.get('version', 'ERR')) for m, v in env['python_modules'].items() if v))
    print('missing:  ' + ', '.join(m for m, v in env['python_modules'].items() if not v))
    g = env['gpu']
    print('gpu: devfreq %s, mali_kbase %s, vulkan %d, opencl %d, nvidia %s (CUDA driver API %s), DLA %d' % (
        [(d['name'], d['cur_mhz']) for d in g['devfreq'] if 'gpu' in d['name'] or 'mali' in d['name'].lower()],
        g['mali_kbase'], len(g['vulkan_icd']), len(g['opencl_icd']), g['nvidia_smi'], g.get('cuda_driver_api'),
        g['dla_cores']))
    if env['jetson']:
        j = env['jetson']
        print('jetson: %s; installed %s; apt %s; trtexec %s' % (j['l4t'], j['installed'], j['apt_candidates'], j['trtexec']))
    print('power monitors: %s' % ([(p['name'], list(p['rails'])) for p in env['power_monitors']] or 'none'))
    s = env['sensors']
    print('sensors: modules %s, usb %s, cx3 %s' % (s['modules'], s['usb'], s['cx3_microscope']))
    for p in s['processes']:
        print('  running: ' + p)
    print('wrote ' + path)


if __name__ == '__main__':
    main()
