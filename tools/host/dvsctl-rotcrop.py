"""Drive the dvsctl page in headless Chrome with real CDP pointer input: rotate buttons, drag-crop on the turned
view, reset, reload persistence, ?rot override. (11.55) Usage: python3 tools/host/dvsctl-rotcrop.py http://<board>:8080/ <outdir>; needs Mac Chrome and the websockets module"""
import base64, itertools, json, os, subprocess, sys, time, urllib.request
from websockets.sync.client import connect

URL, OUT = sys.argv[1], sys.argv[2]
os.makedirs(OUT, exist_ok=True)
PORT = 9333
chrome = subprocess.Popen(['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', '--headless=new',
                           f'--remote-debugging-port={PORT}', f'--user-data-dir={OUT}/profile', '--no-first-run',
                           '--window-size=1280,2000', 'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(50):
    try:
        tabs = json.load(urllib.request.urlopen(f'http://127.0.0.1:{PORT}/json'))
        ws_url = next(t['webSocketDebuggerUrl'] for t in tabs if t['type'] == 'page'); break
    except Exception:
        time.sleep(0.2)
ws = connect(ws_url, max_size=None)
ids, errors, fails = itertools.count(1), [], []

def cdp(method, **params):
    i = next(ids)
    ws.send(json.dumps({'id': i, 'method': method, 'params': params}))
    while True:
        m = json.loads(ws.recv())
        if m.get('method') == 'Runtime.exceptionThrown':
            errors.append(m['params']['exceptionDetails'].get('exception', {}).get('description', m['params']))
        if m.get('id') == i:
            if 'error' in m: raise RuntimeError(f'{method}: {m["error"]}')
            return m['result']

def js(expr):
    r = cdp('Runtime.evaluate', expression=expr, returnByValue=True, awaitPromise=True)
    if 'exceptionDetails' in r: raise RuntimeError(f'{expr}: {r["exceptionDetails"]}')
    return r['result'].get('value')

def state():
    return js('''({rot, W, H, crop, cw: view.width, ch: view.height, sw: view.style.width,
                  rect: (({left, top, width, height}) => ({left, top, width, height}))(view.getBoundingClientRect()),
                  info: document.getElementById('crop-info').textContent, ls: localStorage.getItem('dvs-rot')})''')

def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (f'  [{detail}]' if detail else ''))
    if not ok: fails.append(name)

def mouse(kind, x, y, buttons):
    cdp('Input.dispatchMouseEvent', type=kind, x=x, y=y, button='left', buttons=buttons, clickCount=1,
        pointerType='mouse')

def click(sel):
    r = js(f'''(() => {{ const e = document.querySelector({json.dumps(sel)}); e.scrollIntoView({{block: 'center'}});
              const b = e.getBoundingClientRect(); return [b.left + b.width / 2, b.top + b.height / 2]; }})()''')
    mouse('mouseMoved', *r, 0); mouse('mousePressed', *r, 1); mouse('mouseReleased', *r, 0)
    time.sleep(0.3)

def drag(f0, f1):
    """drag on the view from fraction f0 to f1 of its on-screen box; returns the box used"""
    js("view.scrollIntoView({block: 'start'})")
    b = state()['rect']
    p = lambda f: (b['left'] + f[0] * b['width'], b['top'] + f[1] * b['height'])
    (x0, y0), (x1, y1) = p(f0), p(f1)
    mouse('mouseMoved', x0, y0, 0); mouse('mousePressed', x0, y0, 1)
    for t in (0.25, 0.5, 0.75, 1.0):
        mouse('mouseMoved', x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, 1)
    mouse('mouseReleased', x1, y1, 0)
    time.sleep(0.3)
    return b

def shot(name):
    js("window.scrollTo(0, 0)")
    data = cdp('Page.captureScreenshot', format='png', captureBeyondViewport=True)['data']
    open(f'{OUT}/{name}.png', 'wb').write(base64.b64decode(data))

def load(url):
    cdp('Page.navigate', url=url)
    for _ in range(100):           # until a frame has been drawn
        time.sleep(0.2)
        try:
            if js('typeof frame !== "undefined" && frame !== null'): return
        except RuntimeError:
            pass
    raise RuntimeError('no frame drawn')

def rotation_matches(r):
    """the turned bitmap is raw turned r quarter turns clockwise: compare every pixel in one synchronous step"""
    return js(f'''(() => {{
      const w = raw.width, h = raw.height, a = document.createElement('canvas');
      a.width = w; a.height = h; const ax = a.getContext('2d'); ax.drawImage(raw, 0, 0);
      const src = ax.getImageData(0, 0, w, h).data, o = orient(), d = o.getContext('2d').getImageData(0, 0, o.width, o.height).data;
      let bad = 0, lit = 0;
      for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {{
        const [u, v] = [[x, y], [h - 1 - y, x], [w - 1 - x, h - 1 - y], [y, w - 1 - x]][{r}];
        const i = (y * w + x) * 4, j = (v * o.width + u) * 4;
        if (src[i] + src[i + 1] + src[i + 2] > 96) lit++;
        if (src[i] !== d[j] || src[i + 1] !== d[j + 1] || src[i + 2] !== d[j + 2]) bad++;
      }}
      return {{w, h, ow: o.width, oh: o.height, bad, lit}};
    }})()''')

try:
    cdp('Page.enable'); cdp('Runtime.enable')
    cdp('Emulation.setDeviceMetricsOverride', width=1280, height=3000, deviceScaleFactor=1, mobile=False)
    load(URL)
    s = state(); shot('0-start')
    check('fresh profile starts upright', s['rot'] == 0 and (s['cw'], s['ch']) == (960, 720) and s['crop'] is None, s)

    click('#rot-r'); s = state(); shot('1-rot90')
    check('right 90 button: rot 1, canvas 720x960, 75% width, kept', s['rot'] == 1 and (s['cw'], s['ch']) == (720, 960)
          and s['sw'] == '75%' and s['ls'] == '1' and s['info'].startswith('90° 회전 · 전체 720×960'), s)
    check('on-screen box is upright (taller than wide)', s['rect']['height'] > s['rect']['width'], s['rect'])
    m = rotation_matches(1)
    check('90: every pixel = raw turned clockwise', m['bad'] == 0 and (m['ow'], m['oh']) == (m['h'], m['w']), m)

    drag((0.25, 0.25), (0.75, 0.5)); s = state(); shot('2-rot90-crop')
    exp = dict(x=180, y=240, w=360, h=240)
    c = s['crop'] or {}
    check('drag on turned view crops the expected region (+-2 px)',
          all(abs(c.get(k, -99) - v) <= 2 for k, v in exp.items()), f'got {c}, want {exp}, info {s["info"]}')
    check('crop info shows region', '영역 x' in s['info'] and s['info'].startswith('90° 회전'), s['info'])

    # drag inside the crop: the view shows c fitted; fractions map through fit()
    c1 = s['crop']; k = min(720 / c1['w'], 960 / c1['h']); ox = (720 - c1['w'] * k) / 2; oy = (960 - c1['h'] * k) / 2
    f0, f1 = (0.4, 0.45), (0.6, 0.55)
    want = lambda f: (c1['x'] + (f[0] * 720 - ox) / k, c1['y'] + (f[1] * 960 - oy) / k)
    (wx0, wy0), (wx1, wy1) = want(f0), want(f1)
    drag(f0, f1); s = state(); shot('3-rot90-crop2'); c = s['crop'] or {}
    exp2 = dict(x=round(wx0), y=round(wy0), w=round(wx1) - round(wx0), h=round(wy1) - round(wy0))
    check('second drag inside the crop narrows it (+-2 px)', all(abs(c.get(k_, -99) - v) <= 2 for k_, v in exp2.items()),
          f'got {c}, want {exp2}')

    drag((0.5, 0.5), (0.505, 0.505)); s2 = state()
    check('a click on the view keeps the crop', s2['crop'] == s['crop'], s2['crop'])

    click('#crop-reset'); s = state()
    check('whole-view button clears crop', s['crop'] is None and '전체 720×960' in s['info'], s['info'])

    load(URL); s = state()
    check('reload keeps 90 from localStorage', s['rot'] == 1 and (s['cw'], s['ch']) == (720, 960), s)

    click('#rot-l'); click('#rot-l'); s = state(); shot('4-rot270')
    check('left twice from 90: rot 3 (270), 720x960', s['rot'] == 3 and (s['cw'], s['ch']) == (720, 960)
          and s['ls'] == '3' and s['info'].startswith('270° 회전'), s)
    m = rotation_matches(3); check('270: every pixel = raw turned 3 quarter turns', m['bad'] == 0, m)
    drag((0.5, 0.25), (-0.1, -0.1)); c = state()['crop'] or {}
    check('drag past the top-left corner of 270 view clamps to 0,0', all(abs(c.get(k_, -99) - v) <= 2 for k_, v in dict(x=0, y=0, w=360, h=240).items()), c)

    click('#rot-l'); s = state()
    check('left from 270: rot 2 (180), 960x720, full width, crop cleared', s['rot'] == 2 and (s['cw'], s['ch']) == (960, 720)
          and s['sw'] == '' and s['crop'] is None, s)
    m = rotation_matches(2); check('180: every pixel = raw turned twice', m['bad'] == 0, m)

    load(URL + '?rot=0'); s = state(); shot('5-query-rot0')
    check('?rot=0 overrides stored 180 and stores 0', s['rot'] == 0 and s['ls'] == '0' and (s['cw'], s['ch']) == (960, 720), s)

    check('no page exceptions', not errors, errors)
finally:
    ws.close(); chrome.terminate()
print('RESULT', 'FAIL ' + ', '.join(fails) if fails else 'ALL PASS')
