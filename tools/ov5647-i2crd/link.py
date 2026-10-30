"""Tiny linker: wrap the .text of a single-section aarch64 ELF object into a static executable."""
import struct, sys
d = open(sys.argv[1], 'rb').read()
shoff, = struct.unpack_from('<Q', d, 0x28); shentsize, shnum, shstrndx = struct.unpack_from('<HHH', d, 0x3a)
sh = [struct.unpack_from('<IIQQQQIIQQ', d, shoff + i * shentsize) for i in range(shnum)]
nm = lambda base, o: d[base + o:d.index(b'\0', base + o)].decode()
ti = [i for i, s in enumerate(sh) if nm(sh[shstrndx][4], s[0]) == '.text'][0]
text = bytearray(d[sh[ti][4]:sh[ti][4] + sh[ti][5]])
symtab = [s for s in sh if s[1] == 2][0]; strs = sh[symtab[6]]
syms = [struct.unpack_from('<IBBHQQ', d, symtab[4] + i * 24) for i in range(symtab[5] // 24)]
start = [s for s in syms if nm(strs[4], s[0]) == '_start'][0][4]
for s in sh:
    if s[1] == 4:
        assert s[7] == ti, 'relocations outside .text'
        for j in range(s[5] // 24):
            off, info, add = struct.unpack_from('<QQq', d, s[4] + j * 24); t = info & 0xffffffff; k = info >> 32
            assert t in (282, 283) and syms[k][3] == ti, (t, k)     # JUMP26/CALL26 within .text only
            delta = (syms[k][4] + add - off) >> 2
            ins, = struct.unpack_from('<I', text, off)
            struct.pack_into('<I', text, off, (ins & 0xfc000000) | (delta & 0x3ffffff))
base, hdr = 0x400000, 64 + 56
eh = b'\x7fELF' + bytes([2, 1, 1, 0]) + bytes(8) + struct.pack('<HHIQQQIHHHHHH', 2, 183, 1, base + hdr + start, 64, 0, 0, 64, 56, 1, 0, 0, 0)
ph = struct.pack('<IIQQQQQQ', 1, 5, 0, base, base, hdr + len(text), hdr + len(text), 0x1000)
open(sys.argv[2], 'wb').write(eh + ph + bytes(text))
