"""Glyph atlas for the web-address banner on HDMI (dvsctl.py, 11.50). The board has no Korean font and no PIL, so
the Mac renders the few characters the banner uses. The output is not committed (a system font's glyphs): run it
on a Mac and copy the file next to dvsctl.py on the board (/usr/local/share/dvsview/dvsfont.json).

  python3 tools/host/hangul-atlas.py out/dvsfont.json [FONT [SIZE]]
"""
import base64
import json
import sys

from PIL import Image, ImageDraw, ImageFont

CHARS = ''.join(map(chr, range(32, 127))) + '웹접속주소무선유연결안됨'     # printable ASCII + dvsctl.BANNER text
font = ImageFont.truetype(sys.argv[2] if len(sys.argv) > 2 else '/System/Library/Fonts/AppleSDGothicNeo.ttc',
                          int(sys.argv[3]) if len(sys.argv) > 3 else 22)
asc, desc = font.getmetrics()
glyphs = {}
for c in CHARS:
    w = max(1, round(font.getlength(c)))
    im = Image.new('L', (w, asc + desc))
    ImageDraw.Draw(im).text((0, 0), c, font=font, fill=255)
    glyphs[c] = [w, base64.b64encode(im.tobytes()).decode()]
assert sum(glyphs[c][0] for c in '192.168.100.200:8080') <= 228, 'an address line would not fit the banner'
with open(sys.argv[1], 'w') as f:
    json.dump({'h': asc + desc, 'glyphs': glyphs}, f, ensure_ascii=False)
print('%s: %d glyphs, %d px high' % (sys.argv[1], len(glyphs), asc + desc))
