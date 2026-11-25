#!/usr/bin/env python3
"""dvsbench: libdvsview's cost per event on this board, from a dvs_record() file fed as fast as it goes (docs 11.111).
The same file on two boards (E850, Jetson) compares their CPUs on the same work.

  dvsbench.py REC.gz [--lib /usr/local/lib/libdvsview.so] [--filter-ms 5] [--mb 400]

Buffers of 127 lines as dvs_replay() makes them. Timed: dvs_ingest() only (decode into the ring, zero and clean the
packets; the copy into the buffer is not), on the decode CPU it pins. The noise filter (dvs-filter thread, its own CPU)
runs behind: its CPU time is read from /proc schedstat, and every ~1M events the feed waits until it has caught up,
so it never skips ahead (FLAG = 2M events in dvsview.c). Stop the live capture first: it pins the same CPUs.
Prints one JSON line.
"""
import argparse
import ctypes
import gzip
import json
import os
import platform
import time

import numpy as np

LINE, LINES = 8192, 127


def thread_ns(name):
    """CPU time in ns of this process's thread called name, None if there is none"""
    for t in os.listdir('/proc/self/task'):
        try:
            if open('/proc/self/task/%s/comm' % t).read().strip() == name:
                return int(open('/proc/self/task/%s/schedstat' % t).read().split()[0])
        except OSError:
            pass
    return None


def wait_idle(name):
    a = thread_ns(name)
    while True:
        time.sleep(0.01)
        b = thread_ns(name)
        if b == a:
            return b
        a = b


def lines_of(words):
    """[(start, end)] word indexes of each line: dvs_record() ends every line with a zero word"""
    z = np.flatnonzero(words == 0)
    starts = np.concatenate(([0], z[:-1] + 1))
    keep = z > starts
    return list(zip(starts[keep].tolist(), z[keep].tolist()))


def events_of(words):
    """events in the event-group words (bit 31 of the big-endian word): popcount of the two 8-pixel masks"""
    b = words.view(np.uint8).reshape(-1, 4)
    g = b[b[:, 0] & 0x80 != 0]
    return int(np.unpackbits(g[:, 2:4]).sum())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('rec')
    ap.add_argument('--lib', default='/usr/local/lib/libdvsview.so')
    ap.add_argument('--filter-ms', type=int, default=0, help='0: filter off (the decoder alone)')
    ap.add_argument('--mb', type=int, default=400, help='at most this many MB of the recording')
    a = ap.parse_args()

    with gzip.open(a.rec, 'rb') as f:
        raw = f.read(a.mb << 20)
    words = np.frombuffer(raw[:len(raw) // 4 * 4], dtype=np.uint32)
    lines = lines_of(words)
    lib = ctypes.CDLL(a.lib)
    lib.dvs_start.argtypes = [ctypes.c_char_p, ctypes.c_int]
    lib.dvs_ingest.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    lib.dvs_set.argtypes = [ctypes.c_char_p, ctypes.c_int]
    if lib.dvs_start(None, 30) < 0:
        raise SystemExit('dvs_start failed')
    lib.dvs_set(b'filter_ms', a.filter_ms)
    buf = (ctypes.c_uint8 * (LINES * LINE))()
    base = ctypes.addressof(buf)
    src = words.ctypes.data
    f0 = wait_idle('dvs-filter') or 0
    ingest_ns, nbuf, since, waited = 0, 0, 0, 0
    for i in range(0, len(lines), LINES):
        part = lines[i:i + LINES]
        for l, (s, e) in enumerate(part):
            ctypes.memmove(base + l * LINE, src + s * 4, min(e - s, LINE // 4) * 4)
        t = time.perf_counter_ns()
        lib.dvs_ingest(base, len(part) * LINE)
        ingest_ns += time.perf_counter_ns() - t
        ctypes.memset(base, 0, len(part) * LINE)
        nbuf += 1
        since += sum(e - s for s, e in part)
        if since > 100_000 and a.filter_ms:     # <= 1.6M events (16 a word): the filter catches up (skips 2M behind)
            wait_idle('dvs-filter')
            waited += 1
            since = 0
    f1 = wait_idle('dvs-filter') or 0
    ev = events_of(words)
    out = {
        'board': platform.node(), 'cpu': open('/proc/cpuinfo').read().count('processor'),
        'decode_cpu_mhz': int(open('/sys/devices/system/cpu/cpu1/cpufreq/scaling_cur_freq').read()) // 1000,
        'rec': os.path.basename(a.rec), 'mb': round(len(raw) / 1e6, 1), 'events': ev, 'buffers': nbuf,
        'lines': len(lines), 'decode_ms': round(ingest_ns / 1e6, 1), 'decode_ns_per_event': round(ingest_ns / ev, 2),
        'decode_mevents_per_s': round(ev / ingest_ns * 1e3, 1), 'decode_ms_per_buffer': round(ingest_ns / nbuf / 1e6, 3),
        'filter_ms_param': a.filter_ms,
    }
    if a.filter_ms:
        out.update(filter_ms=round((f1 - f0) / 1e6, 1), filter_ns_per_event=round((f1 - f0) / ev, 2), filter_waits=waited)
    print(json.dumps(out), flush=True)
    os._exit(0)                 # the render and filter threads never end


if __name__ == '__main__':
    main()
