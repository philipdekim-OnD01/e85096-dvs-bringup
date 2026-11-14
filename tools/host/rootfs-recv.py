"""rootfs-recv.py DIR [SSH-HOST]: receive the board's rootfs tar from tools/board/rootfs-send.py into DIR/rootfs.tar
through an ssh tunnel (default philip@192.168.0.236, Wi-Fi: the USB network stalls, 11.52) and print its md5, to compare
with the board's /var/tmp/rootfs-send.md5 (docs 11.53). DIR holds secrets: keep it out of git."""
import hashlib
import os
import socket
import subprocess
import sys
import time

d = sys.argv[1]
os.makedirs(d, mode=0o700, exist_ok=True)
host = sys.argv[2] if len(sys.argv) > 2 else 'philip@192.168.0.236'
tun = subprocess.Popen(['ssh', '-N', '-o', 'ExitOnForwardFailure=yes', '-L', '9100:127.0.0.1:9100', host])
time.sleep(3)
c = socket.create_connection(('127.0.0.1', 9100), timeout=60)
h, n, t0 = hashlib.md5(), 0, time.time()
with open(os.path.join(d, 'rootfs.tar'), 'wb') as f:
    while b := c.recv(1 << 20):
        h.update(b)
        f.write(b)
        n += len(b)
        if n >> 28 != (n - len(b)) >> 28:
            print('%d MB, %.1f MB/s' % (n >> 20, n / 1e6 / (time.time() - t0)), flush=True)
tun.terminate()
print('%s %d (%.0f s)' % (h.hexdigest(), n, time.time() - t0))
