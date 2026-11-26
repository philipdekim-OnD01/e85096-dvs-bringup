"""TFLite INT8 hypothesis (msbench 11.116): is the YOLO11n TFLite INT8 mAP drop from quantizing the box decode?
Export the head before DFL/decode as two outputs (boxes 64 x A logits, scores 80 x A sigmoid), onnx2tf INT8 with the
same 100 calibration images, decode in numpy float, and score the same 200 images."""
import json, os, shutil, sys, numpy as np, torch
sys.path.insert(0, '/msb/benchmark')
from runtimes import nms, COCO91
from ultralytics import YOLO
from ultralytics.nn.modules.head import Detect
from ultralytics.utils.export.tensorflow import onnx2saved_model
from ai_edge_litert.interpreter import Interpreter
S = int(sys.argv[1]) if len(sys.argv) > 1 else 320
d = '/w/tflraw-%d' % S
os.makedirs(d, exist_ok=True)
m = YOLO('/w/models/yolo11n-%d/yolo11n.pt' % S).model.fuse().eval()
for p in m.parameters(): p.requires_grad = False
head = m.model[-1]; head.export = True; head.format = 'onnx'
Detect._inference = lambda self, x: (x['boxes'], x['scores'].sigmoid())
im = torch.zeros(1, 3, S, S)
with torch.no_grad(): bx, sc = m(im)
print('raw outputs', bx.shape, sc.shape)
f = d + '/raw.onnx'
torch.onnx.export(m, im, f, input_names=['images'], output_names=['boxes', 'scores'], opset_version=18, dynamo=False)
import onnx, onnxslim
onnx.save(onnxslim.slim(onnx.load(f)), f)
cal = np.load('/w/data/coco-calib-%d.npz' % S)['x']
shutil.rmtree(d + '/tfl', ignore_errors=True)
onnx2saved_model(f, d + '/tfl', quantize=8, images=cal)
tf = sorted(os.listdir(d + '/tfl')); print([x for x in tf if x.endswith('.tflite')])
st = np.concatenate([np.full((S // s) ** 2, s, np.float32) for s in (8, 16, 32)])
anc = np.concatenate([np.stack(np.meshgrid(np.arange(S // s) + .5, np.arange(S // s) + .5), -1).reshape(-1, 2) for s in (8, 16, 32)]).astype(np.float32)
A = len(st)
def decode(b, s):
    b = b.reshape(4, 16, A)
    e = np.exp(b - b.max(1, keepdims=True)); e /= e.sum(1, keepdims=True)
    dist = (e * np.arange(16, dtype=np.float32)[None, :, None]).sum(1)          # 4 x A: l t r b
    x1y1, x2y2 = anc.T - dist[:2], anc.T + dist[2:]
    return np.concatenate([(x1y1 + x2y2) / 2, x2y2 - x1y1]) * st                    # 4 x A xywh px
ev = np.load('/w/data/coco-eval-%d.npz' % S)
for tag in ('float32', 'integer_quant'):
    path = d + '/tfl/' + next(x for x in tf if x.endswith('_%s.tflite' % tag) and 'full_' not in x)
    it = Interpreter(model_path=path); it.allocate_tensors()
    inp = it.get_input_details()[0]; outs = it.get_output_details()
    det = []
    for k, x in enumerate(ev['x']):
        it.set_tensor(inp['index'], x[None].astype(np.float32) / 255); it.invoke()
        o = {tuple(sorted(it.get_tensor(q['index']).shape)): it.get_tensor(q['index']) for q in outs}
        raw = [it.get_tensor(q['index'])[0] for q in outs]
        b = next(r for r in raw if 64 in r.shape); s = next(r for r in raw if 80 in r.shape)
        b = b if b.shape[0] == 64 else b.T; s = s if s.shape[0] == 80 else s.T
        out = np.concatenate([decode(b, S), s])
        bx, sc, cl = nms(out, S, False, conf=0.001)
        g, (pl, pt), (h, w) = ev['gain'][k], ev['pad'][k], ev['shape'][k]
        bx = ((bx - [pl, pt, pl, pt]) / g).clip(0, [w, h, w, h])
        det += [{'image_id': int(ev['ids'][k]), 'category_id': COCO91[int(c)], 'bbox': [float(x1), float(y1), float(x2 - x1), float(y2 - y1)], 'score': float(v)} for (x1, y1, x2, y2), v, c in zip(bx, sc, cl)]
    json.dump(det, open('/w/tflraw-%d-%s.json' % (S, tag), 'w'))
    print(tag, len(det), 'detections')
