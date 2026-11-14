"""rootfs-send.py: stream the running root filesystem as a tar to one client through an ssh tunnel, for cloning the
board (docs 11.53, tools/linux510/build-clone.sh). Root, from the ACM console:
  systemd-run --unit=rootfs-send -p Nice=10 python3 -I /var/tmp/rootfs-send.py
Mac: tools/host/rootfs-recv.py <dir> (it opens the tunnel). The stream's md5 and tar's exit code go to /var/tmp/rootfs-send.md5
(tar exit 1 = a file changed while it was read, normal on a live system)."""
import hashlib
import socket
import subprocess

ADDR = ('127.0.0.1', 9100)      # ssh tunnel only: the stream holds /etc/shadow, the ssh host keys and the Wi-Fi password
TAR = ['tar', '-c', '--one-file-system', '--xattrs', '--xattrs-include=*', '--acls', '--numeric-owner', '--sparse',
       '-C', '/', '--exclude=./tmp/*', '.']

s = socket.create_server(ADDR)
s.settimeout(600)
c, peer = s.accept()
s.close()
c.settimeout(60)                # fail instead of hanging if the link stops (11.52)
p = subprocess.Popen(TAR, stdout=subprocess.PIPE)
h, n = hashlib.md5(), 0
while b := p.stdout.read(1 << 20):
    h.update(b)
    n += len(b)
    c.sendall(b)
c.close()
with open('/var/tmp/rootfs-send.md5', 'w') as f:
    f.write('%s %d tar=%d %s\n' % (h.hexdigest(), n, p.wait(), peer[0]))
