#!/usr/bin/env python3
"""prepare.py OUT: the recorded, same-for-every-device inputs of the CAMERA runtime benchmark (msbench README, Phase 2).

Runs in the export container (models/Dockerfile: Ultralytics' LetterBox, PIL, pycocotools). Downloads only what it uses:
  COCO val2017: instances_val2017.json; of the images with at least one non-crowd box, a fixed shuffle (seed 0) gives
    200 for accuracy and the next 100 for INT8 calibration, each letterboxed as Ultralytics does (no auto, pad 114, centre)
    to 640 and 320: OUT/coco-{eval,calib}-{640,320}.npz (x uint8 N x S x S x 3 RGB, ids, gain, pad, shape),
    OUT/coco-eval-gt.json (the ground truth of those 200 only), OUT/calib-{640,320}/NNN.png + list.txt (ncnn2table).
  Imagenette2-320 (fast.ai, 10 ImageNet classes): 20 val images a class for accuracy, 10 train images a class for
    calibration; resize the short side to 256 (bilinear) and centre-crop 224 as torchvision's ImageNet recipe:
    OUT/imagenet-{eval,calib}-224.npz (x, y = ImageNet class index), OUT/calib-224/NNN.png + list.txt.
Normalisation is left to the runtime adapters (benchmark/runtimes.py), the same code on every device.
"""
import io
import json
import os
import sys
import tarfile
import urllib.request
import zipfile

import numpy as np
from PIL import Image
from ultralytics.data.augment import LetterBox

OUT = sys.argv[1]
RAW = os.path.join(OUT, 'raw')
os.makedirs(RAW, exist_ok=True)
rng = np.random.default_rng(0)


def fetch(url, path):
    if not os.path.exists(path):
        print('get', url, flush=True)
        urllib.request.urlretrieve(url, path + '.part')
        os.rename(path + '.part', path)
    return path


def save_calib(name, xs):
    d = os.path.join(OUT, 'calib-' + name)
    os.makedirs(d, exist_ok=True)
    paths = []
    for i, x in enumerate(xs):
        p = os.path.join(d, '%03d.png' % i)
        Image.fromarray(x).save(p)
        paths.append(p)
    with open(os.path.join(d, 'list.txt'), 'w') as f:
        f.write('\n'.join(paths) + '\n')


# --- COCO ---
ann = os.path.join(RAW, 'instances_val2017.json')
if not os.path.exists(ann):
    z = fetch('http://images.cocodataset.org/annotations/annotations_trainval2017.zip', os.path.join(RAW, 'ann.zip'))
    with zipfile.ZipFile(z) as zf, open(ann, 'wb') as f:
        f.write(zf.read('annotations/instances_val2017.json'))
    os.remove(z)
coco = json.load(open(ann))
has = {a['image_id'] for a in coco['annotations'] if not a['iscrowd']}
imgs = sorted((i for i in coco['images'] if i['id'] in has), key=lambda i: i['id'])
order = rng.permutation(len(imgs))
sel = {'eval': [imgs[k] for k in order[:200]], 'calib': [imgs[k] for k in order[200:300]]}
cdir = os.path.join(RAW, 'coco')
os.makedirs(cdir, exist_ok=True)
for part, lst in sel.items():
    rgb = []
    for im in lst:
        p = fetch('http://images.cocodataset.org/val2017/' + im['file_name'], os.path.join(cdir, im['file_name']))
        rgb.append(np.asarray(Image.open(p).convert('RGB')))
    for s in (640, 320):
        lb = LetterBox((s, s), auto=False)
        xs, gains, pads = [], [], []
        for a in rgb:
            h, w = a.shape[:2]
            g = min(s / h, s / w)
            nw, nh = round(w * g), round(h * g)
            pads.append((round((s - nw) / 2 - 0.1), round((s - nh) / 2 - 0.1)))    # LetterBox's left, top
            gains.append(g)
            xs.append(lb(image=a[:, :, ::-1].copy())[:, :, ::-1])         # LetterBox works on BGR as cv2 reads it
        xs = np.ascontiguousarray(np.stack(xs))
        np.savez(os.path.join(OUT, 'coco-%s-%d.npz' % (part, s)), x=xs, ids=np.array([i['id'] for i in lst]),
                 gain=np.array(gains), pad=np.array(pads), shape=np.array([a.shape[:2] for a in rgb]))
        if part == 'calib':
            save_calib(str(s), xs)
        print(part, s, xs.shape, flush=True)
ids = {i['id'] for i in sel['eval']}
json.dump({'images': sel['eval'], 'categories': coco['categories'],
           'annotations': [a for a in coco['annotations'] if a['image_id'] in ids]},
          open(os.path.join(OUT, 'coco-eval-gt.json'), 'w'))

# --- Imagenette (ImageNet classes) ---
WNID = {'n01440764': 0, 'n02102040': 217, 'n02979186': 482, 'n03000684': 491, 'n03028079': 497, 'n03394916': 566,
        'n03417042': 569, 'n03425413': 571, 'n03445777': 574, 'n03888257': 701}
tgz = fetch('https://s3.amazonaws.com/fast-ai-imageclas/imagenette2-320.tgz', os.path.join(RAW, 'imagenette2-320.tgz'))
with tarfile.open(tgz) as tf:
    names = sorted(n for n in tf.getnames() if n.endswith('.JPEG'))
    pick = {'eval': [], 'calib': []}
    for part, split, k in (('eval', 'val', 20), ('calib', 'train', 10)):
        for wnid in sorted(WNID):
            c = [n for n in names if '/%s/%s/' % (split, wnid) in n]
            pick[part] += [(c[j], WNID[wnid]) for j in sorted(rng.choice(len(c), k, replace=False))]
    for part, lst in pick.items():
        xs = []
        for n, _ in lst:
            im = Image.open(io.BytesIO(tf.extractfile(n).read())).convert('RGB')
            w, h = im.size
            g = 256 / min(w, h)
            im = im.resize((round(w * g), round(h * g)), Image.BILINEAR)
            w, h = im.size
            l, t = (w - 224) // 2, (h - 224) // 2
            xs.append(np.asarray(im.crop((l, t, l + 224, t + 224))))
        xs = np.stack(xs)
        np.savez(os.path.join(OUT, 'imagenet-%s-224.npz' % part), x=xs, y=np.array([y for _, y in lst]),
                 names=np.array([n for n, _ in lst]))
        if part == 'calib':
            save_calib('224', xs)
        print('imagenet', part, xs.shape, flush=True)
os.remove(tgz)
print('done')
