# acmpull.py REMOTE LOCAL - fetch a file from the board over the USB-serial shell.
# Large single transfers lose data on the device->host path, so: gzip, 16 KiB chunks, md5 per chunk, retry.
import base64, glob, hashlib, os, re, sys, termios, time, zlib
port = '/dev/cu.usbmodeme850_961'       # not glob: a phone's ACM port can sort first
fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
a = termios.tcgetattr(fd); a[0]=0; a[1]=0; a[2]=termios.CS8|termios.CREAD|termios.CLOCAL; a[3]=0; termios.tcsetattr(fd, termios.TCSANOW, a)
def run(cmd, tmo=20):
    try:
        while os.read(fd, 65536): pass
    except BlockingIOError: pass
    os.write(fd, ('\r' + cmd + '; printf "@@""END@@"\r').encode())
    buf = b''; t = time.time()
    while time.time() - t < tmo and b'@@END@@' not in buf:
        try: buf += os.read(fd, 1 << 20)
        except BlockingIOError: time.sleep(0.01)
    return buf
CH = 16384
size = int(re.search(rb'@@S(\d+)@@', run('gzip -c %s > /tmp/pull.gz; printf "@@S%%s@@" $(stat -c %%s /tmp/pull.gz)' % sys.argv[1])).group(1))
out = b''
for k in range((size + CH - 1) // CH):
    for attempt in range(6):
        r = run('printf "@@M%%s@@@@D%%s@@D" "$(dd if=/tmp/pull.gz bs=%d skip=%d count=1 2>/dev/null | md5sum | cut -c1-32)" '
                '"$(dd if=/tmp/pull.gz bs=%d skip=%d count=1 2>/dev/null | base64 -w0)"' % (CH, k, CH, k))
        m = re.search(rb'@@M([0-9a-f]{32})@@', r); d = re.search(rb'@@D([A-Za-z0-9+/=]+)@@D', r)
        if m and d:
            try:
                chunk = base64.b64decode(d.group(1))
            except Exception:
                continue
            if hashlib.md5(chunk).hexdigest().encode() == m.group(1):
                out += chunk; break
    else:
        sys.exit('chunk %d failed' % k)
open(sys.argv[2], 'wb').write(zlib.decompress(out, 16 + zlib.MAX_WBITS))
print(sys.argv[2], os.path.getsize(sys.argv[2]), 'bytes (gzip %d, %d chunks)' % (size, k + 1))
