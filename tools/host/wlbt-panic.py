#!/usr/bin/env python3
"""wlbt-panic.py JOURNAL.txt MX140.SYM LOG-STRINGS.BIN: decode the WLBT R4 panic records (v2, 53 words) in a
kernel log (journalctl -k ... | grep r4_panic_record) with the firmware's own symbol table and log strings
(board /vendor/etc/wifi/mx140/debug/common/, same build as mx140.bin, 11.47).

Word [2] is the panic's format string, an offset in log-strings.bin ("Data Abort" for the natural 0x04 panics,
"...MXAGENT_FORCED_TERMINATION(0x%08x)" with [3] for mx_panic). The rest of the v2 layout is not in the driver
source; words that fall in firmware code or the heap are printed with their symbol.
"""
import bisect
import re
import sys


def records(lines):
    recs, cur = {}, None
    for l in lines:
        m = re.search(r'\[\s*([\d.]+)\].*r4_panic_record\[(\d+)\] = ([0-9a-f]{8})', l)
        if not m:
            continue
        t, i, v = float(m[1]), int(m[2]), int(m[3], 16)
        if i == 0:
            cur = recs.setdefault(round(t, 1), {})
        if cur is not None:
            cur[i] = v
    return recs


def symbols(lines):
    """objdump -t lines 'addr flags section size name': the sized function and object symbols"""
    syms = []
    for l in lines:
        p = l.split()
        if len(p) >= 4 and re.fullmatch(r'[0-9a-f]{8}', p[0]) and re.fullmatch(r'[0-9a-f]{8}', p[-2]):
            a, sz, fl = int(p[0], 16), int(p[-2], 16), p[1:-3]
            if sz and ('F' in fl or 'O' in fl):
                syms.append((a, sz, p[-1], 'F' in fl))
    syms.sort()
    return syms


def namer(syms):
    addrs = [s[0] for s in syms]

    def name(v):
        if not (0x80000000 <= v < 0x81000000 or 0x1b000000 <= v < 0x1c000000):   # code, heap, TCM only
            return ''
        v &= ~1
        j = bisect.bisect_right(addrs, v) - 1
        if j >= 0 and v < syms[j][0] + syms[j][1]:
            return '%s+0x%x%s' % (syms[j][2], v - syms[j][0], '' if syms[j][3] else ' (data)')
        return ''
    return name


def message(strings, off):
    if off >= len(strings):
        return '?'
    return strings[off:strings.find(b'\0', off)].decode(errors='replace')


def selftest():
    r = records(['[  10.5] wlbt: panic_record_dump: r4_panic_record[0] = 00000002',
                 '[  10.5] wlbt: panic_record_dump: r4_panic_record[2] = 00000004'])
    syms = symbols(['80060140 l     F .text\t00000010 pfree_set_free_list_ptr'])
    assert r == {10.5: {0: 2, 2: 4}}, r
    assert namer(syms)(0x80060147) == 'pfree_set_free_list_ptr+0x6' and namer(syms)(0x146) == ''
    assert message(b'abc\0Data Abort\0', 4) == 'Data Abort'


def main():
    selftest()
    recs = records(open(sys.argv[1], errors='replace'))
    name = namer(symbols(open(sys.argv[2], errors='replace')))
    strings = open(sys.argv[3], 'rb').read()
    for t, r in sorted(recs.items()):
        print('[%.1f] %s  [3]=%08x' % (t, message(strings, r.get(2, 0)), r.get(3, 0)))
        for i in sorted(r):
            n = name(r[i])
            if n and i > 3:
                print('   [%2d] %08x %s' % (i, r[i], n))


if __name__ == '__main__':
    main()
