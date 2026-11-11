#!/usr/bin/env python3
"""One controlled S5KRC1S D-PHY PLL link run on the board (docs/DVS-PLL-TESTS.md), as root:

  plltest.py PROFILE RX_MBPS MINUTES [--settle N] [--sample N] [--keep] [--out DIR]

PROFILE is the CIS pll parameter (setfile = the setfile's 00/75/00, experimental_00_75_00, recommended_00_8b_b0,
alternative_00_8b_f8, default_00_8d_00); RX_MBPS is fimc_is.e850_csi_speed, the CSIS lane rate setting (0 = the
DT's 1600; settle follows it unless --settle). Order: the parameters, then dvslive restarted: its stream off puts
the sensor in standby, the next stream's mode change writes the PLL word (sensor still in standby), CSIS starts at
RX_MBPS, then the sensor streams. After WARM s the /stats JSON (CRC count, link counters, V4L2 sequence, PLL
readback) goes to OUT/stats.jsonl every STEP s for MINUTES; OUT/summary.json has the counter deltas and the
pass/fail of each criterion. --sample N: before and after the run, dvslive stopped for N VC0 buffers
(OUT/raw-pre, raw-post, gzipped) and dvslink.py on them. At the end (also on Ctrl-C or SIGTERM) the profile goes
back to setfile and the DT speed and dvslive restarts, unless --keep. Long runs: systemd-run --unit=plltest ...
"""
import argparse
import glob
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request

CIS = '/sys/module/is_cis_s5krc1s/parameters/'
FIMC = '/sys/module/fimc_is/parameters/'
STATS = 'http://127.0.0.1:8080/stats'      # dvslive.sh --ctl 8080
WARM, STEP = 20, 10
# expected readback: 0x30A5 0x30A6 0x30A7 (setfile: is-cis-s5krc1s-setA.h)
WORDS = {'setfile': '00 75 00', 'experimental_00_75_00': '00 75 00', 'recommended_00_8b_b0': '00 8b b0',
         'alternative_00_8b_f8': '00 8b f8', 'default_00_8d_00': '00 8d 00'}
# summed over the run (restarts of dvslive split it into segments); kernel CRC count and the process's counters
LINK = ('bufs', 'short_bufs', 'data_lines', 'unknown', 'sub_fixed', 'fe', 'fe_skips', 'fe_skips_first', 'fe_missed',
        'ts', 'ts_miss', 'ts_miss_first', 'ts_missed', 'ts_fixed', 'ts_bad', 'ts_back', 'ts_restarts')


def rd(p):
    try:
        return open(p).read().strip()
    except OSError:
        return None


def wr(p, v):
    with open(p, 'w') as f:
        f.write(str(v))


def sh(*cmd):
    return subprocess.run(cmd, capture_output=True, text=True).stdout.strip()


def stats():
    try:
        return json.loads(urllib.request.urlopen(STATS, timeout=5).read())
    except (OSError, ValueError):
        return None


def kmsg(msg):
    try:
        wr('/dev/kmsg', 'plltest: %s\n' % msg)
    except OSError:
        pass


def sample(out, n, dvslink):
    """N buffers with dvslive stopped (files: the whole 2x allocation), dvslink.py on them, then gzip"""
    os.makedirs(out, exist_ok=True)
    sh('systemctl', 'stop', 'dvslive')
    log = subprocess.run(['ov5647-cap.py', '--raw8', '-W', '8192', '-H', '127', '-n', str(n), '-b', '4', '-B', '8',
                          '-t', '4', '-o', out + '/buf'], capture_output=True, text=True)
    open(out + '/cap.log', 'w').write(log.stdout + log.stderr)
    sh('systemctl', 'start', 'dvslive')
    files = sorted(glob.glob(out + '/buf_*.raw'), key=lambda f: int(f.rsplit('_', 1)[1][:-4]))
    res = subprocess.run(['python3', '-I', dvslink, '--json'] + files, capture_output=True, text=True) if files else None
    open(out + '/dvslink.jsonl', 'w').write(res.stdout + res.stderr if res else '')
    for f in files:
        sh('gzip', '-1', f)
    rows = [json.loads(x) for x in (res.stdout.splitlines() if res else []) if x.startswith('{')]
    return {'buffers': len(rows), 'short': sum(r['lines']['short'] for r in rows),
            'unknown': sum(r['unknown'] for r in rows), 'ts_miss1': sum(r['ts'].get('miss1', 0) for r in rows),
            'ts_bad': sum(r['ts'].get('back', 0) + r['ts'].get('gap', 0) for r in rows),
            'fe_missed': sum(r['frame_end']['missed'] for r in rows)}


def summarize(rows, profile, minutes):
    s = {'samples': len(rows), 'crc': 0, 'seq_skips': 0, 'seq_lost': 0, 'restarts': 0, 'seconds': 0.0}
    s.update({k: 0 for k in LINK})
    for a, b in zip(rows, rows[1:]):
        s['seconds'] += b['t'] - a['t']
        s['crc'] += b['crc'] - a['crc']
        if b['pid'] != a['pid'] or b['st'] is None or a['st'] is None:
            s['restarts'] += b['pid'] != a['pid']
            continue
        for k in LINK:
            s[k] += b['st'].get('link', {}).get(k, 0) - a['st'].get('link', {}).get(k, 0)
        for k in ('seq_skips', 'seq_lost'):
            s[k] += b['st'].get(k, 0) - a['st'].get(k, 0)
    sec, bufs, ts = s['seconds'] or 1, s['bufs'] or 1, s['ts'] or 1
    s['rate'] = {'crc_per_s': s['crc'] / sec, 'bufs_per_s': s['bufs'] / sec, 'scans_missed_pct': 100.0 *
                 s['ts_missed'] / (ts + s['ts_missed']), 'short_bufs_pct': 100.0 * s['short_bufs'] / bufs,
                 'data_lines_per_buf': s['data_lines'] / bufs}
    want = WORDS[profile]
    ok = [r['st'] for r in rows if r['st']]
    pll = [st['pll'] for st in ok if 'pll' in st]
    seqs = {st.get('seq') for st in ok}
    link = bool(ok) and all('link' in st for st in ok)     # None below: not measured (a libdvsview before 11.46)
    s['criteria'] = {
        'readback': all(p.startswith(want) for p in pll) if pll else None,
        'duration': s['seconds'] >= 60 * minutes - 2 * STEP,
        'stream': s['restarts'] == 0 and (s['bufs'] > 0 or not link),
        'crc': s['crc'] == 0,
        'frames_complete': s['short_bufs'] == 0 if link else None,
        'no_buffer_loss': s['seq_lost'] == 0 if len(seqs) > 1 else None,    # None: the sequence is not filled in
        'no_scan_loss': s['ts_missed'] == 0 and s['fe_missed'] == 0 if link else None,
        'ts_continuity': all(s[k] == 0 for k in ('ts_bad', 'ts_back', 'ts_fixed', 'sub_fixed', 'ts_restarts'))
        if link else None,
        'no_unknown_words': s['unknown'] == 0 if link else None}
    # any failed: False; else not judged (None) while one is not measured, but the V4L2 sequence (scan loss covers it)
    c = s['criteria'].values()
    s['pass'] = False if False in c else None if any(v is None and k != 'no_buffer_loss'
                                                      for k, v in s['criteria'].items()) else True
    return s


def selftest():
    def row(t, pid, crc, k, seq):
        return {'t': t, 'pid': pid, 'crc': crc, 'st': {'link': dict.fromkeys(LINK, k) | {'ts': 1000 * k},
                                                       'seq': seq, 'seq_skips': 0, 'seq_lost': 0, 'pll': '00 8b b0 P=4'}}
    rows = [row(0, '1', 5, 0, 10), row(10, '1', 5, 0, 20), row(20, '2', 7, 3, 0), row(30, '2', 7, 4, 30)]
    s = summarize(rows, 'recommended_00_8b_b0', 0.5)
    assert s['crc'] == 2 and s['restarts'] == 1 and s['bufs'] == 1 and s['ts'] == 1000 and s['seconds'] == 30, s
    c = s['criteria']
    assert c['readback'] and not c['crc'] and not c['stream'] and c['no_buffer_loss'] and not c['no_scan_loss'], c
    assert not s['pass'] and summarize(rows[:2], 'setfile', 0)['criteria']['readback'] is False


def main():
    selftest()
    ap = argparse.ArgumentParser()
    ap.add_argument('profile', choices=sorted(WORDS))
    ap.add_argument('rx', type=int, help='fimc_is.e850_csi_speed, Mbps/lane (0 = DT)')
    ap.add_argument('minutes', type=float)
    ap.add_argument('--settle', type=int, default=0, help='fimc_is.e850_csi_settle (0 = from the speed)')
    ap.add_argument('--sample', type=int, default=0, help='VC0 buffers to keep before and after (dvslive stopped)')
    ap.add_argument('--keep', action='store_true', help='leave the profile and speed set at the end')
    ap.add_argument('--out', help='default /var/tmp/plltest/<UTC>-<profile>-<rx>')
    ap.add_argument('--dvslink', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dvslink.py'))
    ap.add_argument('--observe', action='store_true',
                    help='change nothing (no root): log and judge the running stream, PROFILE = what it should be')
    a = ap.parse_args()
    if os.geteuid() and not a.observe:
        sys.exit('root only (or --observe)')
    out = a.out or '/var/tmp/plltest/%s-%s%s-%d' % (time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()),
                                                    'observe-' if a.observe else '', a.profile, a.rx)
    os.makedirs(out, exist_ok=True)
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    run = {'profile': a.profile, 'rx_mbps': a.rx, 'settle': a.settle, 'minutes': a.minutes, 'out': out,
           'kernel': sh('uname', '-r'), 'start': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
           'modules': sh('sh', '-c', 'sha256sum $(modinfo -n is-cis-s5krc1s) $(modinfo -n fimc-is) '
                         '/usr/local/lib/libdvsview.so | cut -c1-16'),
           'observe': a.observe}
    rows = []
    try:
        if not a.observe:
            wr(CIS + 'pll', a.profile)
            wr(FIMC + 'e850_csi_speed', a.rx)
            wr(FIMC + 'e850_csi_settle', a.settle)
            kmsg('start %s rx %d settle %d' % (a.profile, a.rx, a.settle))
            if a.sample:
                run['raw_pre'] = sample(out + '/raw-pre', a.sample, a.dvslink)
            sh('systemctl', 'restart', 'dvslive')
            time.sleep(WARM)
        run['params'] = ({p: rd(CIS + p) for p in ('pll', 'margin', 'flush_us', 'packets', 'regs')} |
                         {p: rd(FIMC + p) for p in ('e850_csi_speed', 'e850_csi_settle', 'e850_csi_crc_keep',
                                                    'e850_csi_err_stop')})
        run['dmesg'] = [x for x in sh('dmesg').splitlines()[-400:]
                        if 'D-PHY PLL' in x or '[CSI1] settle' in x or 'plltest' in x][-8:]
        json.dump(run, open(out + '/run.json', 'w'), indent=1)
        end = time.time() + 60 * a.minutes
        with open(out + '/stats.jsonl', 'w') as f:
            while True:
                r = {'t': time.time(), 'pid': sh('systemctl', 'show', '-p', 'MainPID', '--value', 'dvslive'),
                     'crc': int(rd(FIMC + 'e850_csi_crc_count') or 0), 'st': stats()}
                rows.append(r)
                f.write(json.dumps(r) + '\n')
                f.flush()
                if r['t'] >= end:
                    break
                time.sleep(min(STEP, max(0, end - time.time())))
        if a.sample and not a.observe:
            run['raw_post'] = sample(out + '/raw-post', a.sample, a.dvslink)
    except KeyboardInterrupt:
        run['interrupted'] = True
        print('interrupted', file=sys.stderr)
    finally:
        if len(rows) > 1:               # an interrupted run is judged on what it has (duration fails)
            s = summarize(rows, a.profile, a.minutes)
            s.update({k: run[k] for k in ('raw_pre', 'raw_post', 'interrupted') if k in run})
            json.dump(s, open(out + '/summary.json', 'w'), indent=1)
            print(json.dumps(s, indent=1))
        if not a.observe:
            kmsg('end %s' % a.profile)
        if not a.keep and not a.observe:
            wr(CIS + 'pll', 'setfile')
            wr(FIMC + 'e850_csi_speed', 0)
            wr(FIMC + 'e850_csi_settle', 0)
            sh('systemctl', 'restart', 'dvslive')
        sh('chmod', '-R', 'a+rX', out)


if __name__ == '__main__':
    main()
