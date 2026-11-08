"""train.py --stage public|ours|mix --data DIR --init imagenet|CKPT --out CKPT [--epochs N] [--holdout 2] [--seed 0]

Spec 7.3: public = PAF + DailyDVS-200 (DailyDVS train split; PAF has no split file) from ImageNet weights;
ours = the board recordings from the public checkpoint, the --holdout latest clips of each class kept out
(<CKPT>.holdout.json, for evaluate.py); mix = public and board clips together from a checkpoint, the public ones under
a window of a board 'none' clip (our sensor's real background, 66k events per 60 ms against PAF's 11k, docs 11.43)
instead of synthetic noise, the board ones held out as in ours. One clip is one sequence, the GRU state from 0; 8 clips a batch, padded;
the loss is cross-entropy on the masked steps (dataset.label_mask), classes weighted by 1 / their masked steps.
A step's memory on MPS grows with its frames (~9 MB each, docs 11.41): a batch is up to 8 clips and FRAMES frames.
CKPT: torch.save({'model': state_dict, ...}) (export.py reads it).
"""
import argparse
import json
import os
import sys
import time

import numpy as np
import torch
import torch.nn.functional as F

from model import HID, NCLS, Step

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '../../linux510'))
from dvsact import CLASSES, GH, GW  # noqa: E402

NOISE = 2.05            # half our sensor's ~4.1 events per cell per 60 ms (spec 4), per unit of r ~ U(0, 2)
BATCH = 8
FRAMES = 256            # ~2.4 GB on MPS; 8 clips of up to 245 steps (~8 GB) swapped a 16 GB Mac to a halt


def augment(c, rng, public, bg=None):
    """one clip uint8 [N, 2, GH, GW] -> uint16; public: our sensor's noise added (bg: a window of one of these board
    clips, after the geometry, which moves the person and not the sensor; else synthetic), then thinned; all: mirror,
    zoom 0.8-1.2 and shift +-10% (nearest, the same for the whole clip)"""
    c = c.astype(np.int64)
    if public and not bg:
        c = c + rng.poisson(rng.uniform(0, 2, 2)[None, :, None, None] * NOISE, c.shape)
    if public:
        c = rng.binomial(c, rng.uniform(0.5, 1))
    if rng.random() < 0.5:
        c = c[..., ::-1]
    s, dx, dy = rng.uniform(0.8, 1.2), rng.uniform(-0.1, 0.1) * GW, rng.uniform(-0.1, 0.1) * GH
    yi = np.round((np.arange(GH) - GH / 2 - dy) / s + GH / 2).astype(int)
    xi = np.round((np.arange(GW) - GW / 2 - dx) / s + GW / 2).astype(int)
    vy, vx = (yi >= 0) & (yi < GH), (xi >= 0) & (xi < GW)
    out = np.zeros(c.shape, np.uint16)
    out[:, :, np.flatnonzero(vy)[:, None], np.flatnonzero(vx)] = np.minimum(c[:, :, yi[vy][:, None], xi[vx]], 65535)
    if public and bg:
        b = bg[rng.integers(len(bg))]
        k = (rng.integers(max(len(b) - len(out), 0) + 1) + np.arange(len(out))) % len(b)   # wraps when shorter
        out = np.minimum(out.astype(np.int64) + b[k], 65535).astype(np.uint16)
    return out


def batches(lengths, order, frames=FRAMES, clips=BATCH):
    """clip indices in order, cut into batches of <= clips clips and <= frames frames (a longer clip goes alone)"""
    b, n = [], 0
    for i in order:
        if b and (len(b) == clips or n + lengths[i] > frames):
            yield b
            b, n = [], 0
        b.append(i)
        n += lengths[i]
    if b:
        yield b


def collate(clips, rng, public, augmenting=True, bg=None):
    """-> x float32 [B, T, 2, GH, GW] (log1p), present [B, T] (a frame, not padding), mask [B, T] (a loss), y [B]"""
    T = max(len(c['counts']) for c in clips)
    x = np.zeros((len(clips), T, 2, GH, GW), np.float32)
    present, mask = np.zeros((len(clips), T), bool), np.zeros((len(clips), T), bool)
    for i, c in enumerate(clips):
        n = len(c['counts'])
        pub = public and c.get('src') != 'ours'     # mix: the board clips have the board's noise already
        x[i, :n] = np.log1p(augment(c['counts'], rng, pub, bg) if augmenting else c['counts'].astype(np.float32))
        present[i, :n], mask[i, :n] = True, c['mask']
    return (torch.from_numpy(x), torch.from_numpy(present), torch.from_numpy(mask),
            torch.tensor([c['cls'] for c in clips]))


def seq_loss(m, x, present, mask, y, weights):
    """Step.forward over each sequence: the encoder on all real frames at once, then the GRU step by step"""
    B, T = present.shape
    f = torch.zeros(B, T, HID, device=x.device)
    f[present] = m.proj(m.enc(x[present]).mean((2, 3)))
    h, logits = torch.zeros(B, HID, device=x.device), []
    for t in range(T):
        h = m.gru(f[:, t], h)
        logits.append(m.head(h))
    logits = torch.stack(logits, 1)
    return F.cross_entropy(logits[mask], y[:, None].expand(B, T)[mask], weight=weights)


def load(data, stage, holdout, out):
    man = [json.loads(line) for line in open(os.path.join(data, 'manifest.jsonl'))]
    public = [m for m in man if m['src'] in ('paf', 'daily') and m['split'] in (None, 'train')]
    if stage == 'public':
        man = public
    else:
        man = [m for m in man if m['src'] == 'ours']
        kept = []
        for cls in CLASSES:     # board file names start with the UTC time: the latest ones are held out
            mine = sorted((m for m in man if m['cls'] == cls), key=lambda m: os.path.basename(m['path']))
            kept += mine[len(mine) - holdout:] if holdout else []
        json.dump([m['file'] for m in kept], open(out + '.holdout.json', 'w'))
        man = [m for m in man if m not in kept] + (public if stage == 'mix' else [])
    clips = []
    for m in man:
        z = np.load(os.path.join(data, m['file']))
        clips.append({'counts': z['counts'], 'mask': z['mask'], 'cls': int(z['cls']), 'src': m['src']})
    return clips


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--stage', choices=('public', 'ours', 'mix'), required=True)
    ap.add_argument('--data', default=os.path.expanduser('~/e850-act-runs/data'))
    ap.add_argument('--init', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--epochs', type=int, default=10)
    ap.add_argument('--holdout', type=int, default=2)
    ap.add_argument('--seed', type=int, default=0)
    a = ap.parse_args()
    torch.manual_seed(a.seed)
    rng = np.random.default_rng(a.seed)
    dev = torch.device('mps' if torch.backends.mps.is_available() else 'cpu')
    m = Step.imagenet() if a.init == 'imagenet' else Step()
    if a.init != 'imagenet':
        m.load_state_dict(torch.load(a.init, map_location='cpu')['model'])
    m.to(dev).train()
    clips = load(a.data, a.stage, a.holdout, a.out)
    steps = np.bincount([c['cls'] for c in clips for _ in range(int(c['mask'].sum()))], minlength=NCLS)
    w = np.where(steps > 0, 1 / np.maximum(steps, 1), 0)
    w = torch.tensor(w / w.sum(), dtype=torch.float32, device=dev)
    bg = [c['counts'] for c in clips if c['src'] == 'ours' and CLASSES[c['cls']] == 'none'] if a.stage == 'mix' else None
    print('%d clips, masked steps per class: %s' % (len(clips), dict(zip(CLASSES, steps.tolist()))))
    opt = torch.optim.AdamW(m.parameters(), lr=1e-3 if a.stage == 'public' else 3e-4)
    t0 = time.time()
    for e in range(a.epochs):
        total, n = 0.0, 0
        for b in batches([len(c['counts']) for c in clips], rng.permutation(len(clips))):
            x, present, mask, y = collate([clips[k] for k in b], rng, a.stage != 'ours', bg=bg)
            if not mask.any():
                continue
            loss = seq_loss(m, x.to(dev), present.to(dev), mask.to(dev), y.to(dev), w)
            opt.zero_grad()
            loss.backward()
            opt.step()
            total, n = total + loss.item(), n + 1
            if dev.type == 'mps':
                torch.mps.empty_cache()     # otherwise its cache keeps a block per batch shape and grows
        print('epoch %d loss %.4f (%.0f s)' % (e + 1, total / max(n, 1), time.time() - t0), flush=True)
    torch.save({'model': m.cpu().state_dict(), 'stage': a.stage, 'epochs': a.epochs, 'classes': CLASSES}, a.out)


if __name__ == '__main__':
    main()
