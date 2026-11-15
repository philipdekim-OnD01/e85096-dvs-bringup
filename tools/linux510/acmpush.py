# acmpush2.py LOCAL REMOTE - copy a file to the board over the USB-serial shell: raw tty + head -c
import base64, glob, os, sys, termios, time
port = '/dev/cu.usbmodeme850_961'       # not glob: a phone's ACM port can sort first
fd = os.open(port, os.O_RDWR | os.O_NOCTTY)
a = termios.tcgetattr(fd); a[0]=0; a[1]=0; a[2]=termios.CS8|termios.CREAD|termios.CLOCAL; a[3]=0; termios.tcsetattr(fd, termios.TCSANOW, a)
data = base64.b64encode(open(sys.argv[1], 'rb').read())
def drain(t):
    os.set_blocking(fd, False); end = time.time() + t; out = b''
    while time.time() < end:
        try: out += os.read(fd, 65536)
        except BlockingIOError: time.sleep(0.02)
    os.set_blocking(fd, True); return out
os.write(fd, ('\rstty raw -echo; head -c %d | base64 -d > %s; stty sane; md5sum %s\r' % (len(data), sys.argv[2], sys.argv[2])).encode())
drain(1.0)
for i in range(0, len(data), 16384):
    os.write(fd, data[i:i+16384])           # blocking write: USB flow control paces it
print(drain(3.0).decode(errors='replace')[-200:])
