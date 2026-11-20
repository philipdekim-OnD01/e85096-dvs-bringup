#!/usr/bin/env python3
"""Turn Android mixer_paths.xml into `amixer -s` input (linux510 docs 11.76).

  mixer-path.py mixer_paths.xml [path ...]   -> stdout: cset lines
  with no path: only the top-level defaults.  Paths nest (<path name=x/> inside a path).
  e.g.  mixer-path.py /lib/firmware/mixer_paths.xml media-headphone | amixer -c0 -s -q
"""
import sys
import xml.etree.ElementTree as ET


def ctl_line(c):
    v = c.get('value')
    if v is None:
        return None  # id-indexed ctls are not used on the paths we play
    return "cset name='%s' '%s'" % (c.get('name'), v.replace("'", ''))


def main(argv):
    root = ET.parse(argv[1]).getroot()
    paths = {p.get('name'): p for p in root.findall('path')}  # nested <path/> are references
    out = [ctl_line(c) for c in root.findall('ctl')]

    def walk(name, seen):
        if name in seen:
            return
        seen.add(name)
        for e in paths[name]:
            if e.tag == 'ctl':
                out.append(ctl_line(e))
            elif e.tag == 'path':
                walk(e.get('name'), seen)

    for name in argv[2:]:
        if name not in paths:
            sys.exit('no path %r' % name)
        walk(name, set())
    print('\n'.join(l for l in out if l))


def selftest():
    import tempfile, os
    x = ('<mixer><ctl name="A" value="1"/><path name="p"><ctl name="B" value="x y"/></path>'
         '<path name="q"><path name="p"/><ctl name="C" value="2"/></path></mixer>')
    f = tempfile.NamedTemporaryFile('w', suffix='.xml', delete=False)
    f.write(x); f.close()
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        main(['', f.name, 'q'])
    os.unlink(f.name)
    assert buf.getvalue().split('\n')[:3] == ["cset name='A' '1'", "cset name='B' 'x y'", "cset name='C' '2'"], buf.getvalue()
    print('selftest ok')


if __name__ == '__main__':
    selftest() if sys.argv[1:] == ['--selftest'] else main(sys.argv)
