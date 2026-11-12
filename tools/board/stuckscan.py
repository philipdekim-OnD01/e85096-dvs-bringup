#!/usr/bin/env python3
"""stuckscan.py STATS.JSONL...: stuck episodes and 22-bit reference wraps in plltest.py runs (11.46).

A sample with short buffers or timestamp bit fixes is stuck; consecutive ones are one episode. A sample with exactly
ts_bad +64 and ts_restarts +1 and nothing else is a reference wrap (old lib) . A <dir>.panic file beside the run
(wifistuck.sh) gives the forced Wi-Fi panic time; the report says if an episode began within 30 s of it.
"""
import json
import os
import sys
import time

K = ('short_bufs', 'ts_bad', 'ts_restarts', 'ts_fixed', 'ts_missed', 'ts_miss', 'fe_missed')


def scan(rows):
    eps, wraps, cur = [], [], None
    for a, b in zip(rows, rows[1:]):
        d = {k: b['st']['link'][k] - a['st']['link'][k] for k in K}
        d['crc'] = b['crc'] - a['crc']
        if d['short_bufs'] > 0 or d['ts_fixed'] > 0:
            if cur is None:
                cur = {'start': a['t'], 'end': b['t'], **dict.fromkeys(d, 0)}
            cur['end'] = b['t']
            for k in d:
                cur[k] += d[k]
            continue
        if cur:
            eps.append(cur)
            cur = None
        if d['ts_bad'] == 64 and d['ts_restarts'] == 1 and sum(d.values()) == 65:
            wraps.append(b['t'])
    if cur:
        eps.append(cur)
    return eps, wraps


def selftest():
    def row(t, short=0, bad=0, rs=0, fix=0):
        return {'t': t, 'crc': 0, 'st': {'link': dict(dict.fromkeys(K, 0), short_bufs=short, ts_bad=bad,
                                                       ts_restarts=rs, ts_fixed=fix)}}
    rows = [row(0), row(10, bad=64, rs=1), row(20, 5, 64 + 30, 2, 9), row(30, 9, 64 + 60, 3, 20), row(40, 9, 124, 3, 20)]
    eps, wraps = scan(rows)
    assert wraps == [10] and len(eps) == 1 and eps[0]['start'] == 10 and eps[0]['end'] == 30, (eps, wraps)
    assert eps[0]['short_bufs'] == 9 and eps[0]['ts_fixed'] == 20, eps


def main():
    selftest()
    kst = lambda t: time.strftime('%H:%M:%S', time.gmtime(t + 9 * 3600))
    for p in sys.argv[1:]:
        rows = [json.loads(l) for l in open(p)]
        eps, wraps = scan(rows)
        pf = os.path.dirname(os.path.abspath(p)) + '.panic'
        panic = float(open(pf).read()) if os.path.exists(pf) else None
        crc = rows[-1]['crc'] - rows[0]['crc']
        print('%s: %.2f h, crc %d, wraps %d%s' % (p, (rows[-1]['t'] - rows[0]['t']) / 3600, crc, len(wraps),
                                                  ', panic %s KST' % kst(panic) if panic else ''))
        for e in eps:
            near = panic is not None and -10 <= e['start'] - panic <= 30
            print('  stuck %s-%s KST (%.0f s)%s %s' % (kst(e['start']), kst(e['end']), e['end'] - e['start'],
                                                   ' AFTER PANIC' if near else '',
                                                   {k: v for k, v in e.items() if k not in ('start', 'end') and v}))
        if panic is not None and not any(-10 <= e['start'] - panic <= 30 for e in eps):
            print('  no stuck state after the panic')


if __name__ == '__main__':
    main()
