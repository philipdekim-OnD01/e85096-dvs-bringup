#!/bin/sh
# dvspanel-test.sh: build dvspanel and run its selftest against a stub of the page's API under Xvfb (docs 11.98).
#   docker run --rm --platform linux/arm64 -v "$PWD/tools/linux510:/s:ro" debian:12 sh /s/dvspanel-test.sh
# SHOT=/out/panel.png (with -v <dir>:/out): also a screenshot of the panel running against the stub.
set -e
export DEBIAN_FRONTEND=noninteractive
P="gcc libc6-dev pkg-config libgtk-3-dev libcjson-dev xvfb python3"
dpkg -s $P >/dev/null 2>&1 || { apt-get -qq update && apt-get -qq install -y $P >/dev/null; }
cd /tmp
gcc -O2 -Wall -Werror -o dvspanel /s/dvspanel.c $(pkg-config --cflags --libs gtk+-3.0 libcjson) -lm
cat > stub.py <<'X'
import http.server, json
got = []
KEYS = ['t', 'fps', 'draw_ms', 'events', 'buffers', 'ingest_ms', 'kb', 'behind_ms', 'coverage', 'dips', 'scan_us',
        'crc', 'bufs', 'short_bufs', 'ts_missed', 'fe_missed', 'ts_bad', 'unknown', 'mipi_fs', 'mipi_fe', 'mipi_err',
        'vc0_n', 'vc0_free', 'vc0_wait']
T0 = 1795478000
def row(i):  # 2 s apart; crc +1 a row (10 over the 11 rows), a null for dips
    v = dict(t=T0 + 2 * i, fps=57, draw_ms=2.2, events=1000, buffers=50, ingest_ms=0.3, kb=12, behind_ms=30,
             coverage=99, dips=None, scan_us=985, crc=i, bufs=100 * i, short_bufs=0, ts_missed=0, fe_missed=0,
             ts_bad=0, unknown=0, mipi_fs=100 * i, mipi_fe=100 * i, mipi_err=0, vc0_n=16, vc0_free=12, vc0_wait=1)
    return [v[k] for k in KEYS]
PARAMS = {'fps': {'v': 60, 'def': 30, 'min': 1, 'max': 120}, 'fade': {'v': 0, 'def': 0, 'min': 0, 'max': 250},
          'color': {'v': 1, 'def': 0, 'min': 0, 'max': 4}}
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def reply(self, d, code=200):
        b = json.dumps(d).encode()
        self.send_response(code); self.send_header('Content-Length', str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        path, _, q = self.path.partition('?')
        if path == '/history':
            since = float(q.split('=')[1])
            return self.reply({'keys': KEYS, 'rows': [row(i) for i in range(11) if T0 + 2 * i > since],
                               'frame_bytes': 8192 * 127})
        if path == '/reg':
            return self.reply({'a': 12829, 'n': 2, 'bus': 7, 'v': [0, 17]} if q == 'a=321D&n=2' else {}, 
                              200 if q == 'a=321D&n=2' else 400)
        self.reply({'/params': PARAMS,
                    '/sensor': {'margin': {'v': 17, 'now': 17, 'min': 0, 'max': 140, 'def': 17},
                                'flush_us': {'v': 50, 'now': 50, 'min': 1, 'max': 1023, 'def': 50}},
                    '/stats': {'fps': 57, 'draw_ms': 2.2, 'events': 1000, 'buffers': 50, 'ingest_ms': 0.3, 'kb': 12,
                               'behind_ms': 30, 'coverage': 99, 'dips': 0, 'scan_us': 985, 'crc': 5,
                               'link': {'short_bufs': 1, 'ts_missed': 2, 'fe_missed': 3, 'ts_bad': 4},
                               'addrs': [['무선 Wi-Fi', '10.0.0.2:8099'], ['유선 USB', None]],
                               'pll': '00 8b b0 word 0x08bb0 P=4 M=374 S=0 rate=2244 rate_s=2244'},
                    '/act': {'on': True, 'state': 'stand_up', 'top': 'sit_down', 'p': 0.75,
                             'recent': [{'t_us': 1795478312081726, 'event': 'move', 'state': 'sit_down',
                                         'prev': 'stand_up', 'p': 0.55}]},
                    '/rec': {'state': 'idle', 'left_s': 0, 'last': None}}[path])
    def do_POST(self):
        got.append((self.path, json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
        open('/tmp/posts', 'w').write(json.dumps(got))
        self.reply({'/params': {'rejected': [], 'params': PARAMS},
                    '/rec': {'state': 'countdown', 'left_s': 3.0, 'last': None}}[self.path])
http.server.HTTPServer(('127.0.0.1', 8099), H).serve_forever()
X
python3 stub.py & S=$!
Xvfb :7 -screen 0 1920x1080x24 >/dev/null 2>&1 & X=$!
sleep 2
rc=0
DISPLAY=:7 NO_AT_BRIDGE=1 ./dvspanel --selftest 8099 || rc=1
cat /tmp/posts; echo
grep -q '\["/params", {"fade": 100}\]' /tmp/posts && echo "post ok" || rc=1
grep -q '\["/rec", {"label": "sit_down", "s": 6}\]' /tmp/posts && echo "rec ok" || rc=1
if [ -n "${SHOT:-}" ]; then
    DISPLAY=:7 NO_AT_BRIDGE=1 ./dvspanel 8099 & D=$!
    sleep 4
    xwd -root -display :7 | convert xwd:- -crop 960x1080+960+0 "$SHOT"
    kill $D
fi
kill $S $X
exit $rc
