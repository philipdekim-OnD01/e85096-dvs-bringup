#!/usr/bin/env python3
"""export.py DATA OUT [MODEL...]: every CPU model file of the runtime benchmark, made once on the Mac (msbench README,
Phase 2), in the export container (models/Dockerfile):

  docker run ... msbench-export:1 python /msb/models/export.py /w/data /w/models

Models: yolo11n-640, yolo11n-320 (Ultralytics yolo11n.pt, COCO), resnet18, mobilenetv2 (torchvision IMAGENET1K_V1).
Every model takes the same input: RGB uint8 / 255, NCHW (TFLite: NHWC); the classifiers carry ImageNet's mean/std as
their first layer so that preprocessing is the same code on every device and runtime.
Per model directory: model.torchscript (PyTorch), model.onnx, model.int8.onnx (onnxruntime QDQ, Conv/MatMul/Gemm,
per channel), tflite/*float32.tflite, tflite/*integer_quant.tflite (onnx2tf static
full integer with float input/output; Ultralytics' *_int8.tflite is onnx2tf's dynamic range quant, not used), ncnn/model.ncnn.{param,bin} (pnnx), ncnn/model.int8.{param,bin} (ncnn2table ACIQ + ncnn2int8).
INT8 calibration: the same 100 images for every runtime (data/prepare.py calib sets).
Writes OUT/manifest.json: files and sizes, parameters, FLOPs, input/output shapes, output parity against the
TorchScript FP32 on 4 calibration images, and how much of each INT8 model is still FP32.
"""
import glob
import json
import os
import shutil
import subprocess
import sys

import numpy as np
import onnx
import torch
import torchvision
from torch import nn

DATA, OUT = sys.argv[1], sys.argv[2]
WANT = sys.argv[3:] or ['yolo11n-640', 'yolo11n-320', 'resnet18', 'mobilenetv2']
os.makedirs(OUT, exist_ok=True)
MAN = os.path.join(OUT, 'manifest.json')
man = json.load(open(MAN)) if os.path.exists(MAN) else {}
torch.set_num_threads(8)


class Norm(nn.Module):
    """ImageNet mean/std as the first layer: the model takes RGB / 255 like YOLO"""
    def __init__(self, net):
        super().__init__()
        self.net = net
        self.register_buffer('mean', torch.tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1))
        self.register_buffer('std', torch.tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1))

    def forward(self, x):
        return self.net((x - self.mean) / self.std)


def calib(name, s):
    f = os.path.join(DATA, 'coco-calib-%d.npz' % s if name.startswith('yolo') else 'imagenet-calib-224.npz')
    return np.load(f)['x']                                                  # N x S x S x 3 uint8


def onnx_int8(src, dst, xs):
    from onnxruntime.quantization import CalibrationDataReader, QuantFormat, QuantType, quantize_static
    from onnxruntime.quantization.shape_inference import quant_pre_process
    pre = dst + '.pre.onnx'
    quant_pre_process(src, pre, skip_symbolic_shape=True)
    name = onnx.load(src).graph.input[0].name

    class R(CalibrationDataReader):
        def __init__(self):
            self.it = iter(xs[i:i + 1].transpose(0, 3, 1, 2).astype(np.float32) / 255 for i in range(len(xs)))

        def get_next(self):
            x = next(self.it, None)
            return None if x is None else {name: x}
    quantize_static(pre, dst, R(), quant_format=QuantFormat.QDQ, per_channel=True, activation_type=QuantType.QUInt8,
                    weight_type=QuantType.QInt8, op_types_to_quantize=['Conv', 'MatMul', 'Gemm'])
    os.remove(pre)


# ACIQ: on YOLO11n-640's calibration images its INT8 output is nearer the FP32 one than KL's (score error 0.15 / 0.27)
NCNN_METHOD = 'aciq'


def ncnn_int8(d, s):
    p, b = os.path.join(d, 'model.ncnn.param'), os.path.join(d, 'model.ncnn.bin')
    t = os.path.join(d, 'model.table')
    lst = os.path.join(DATA, 'calib-%d' % s, 'list.txt')
    n = '%.8f' % (1 / 255)
    subprocess.run(['ncnn2table', p, b, lst, t, 'mean=[0,0,0]', 'norm=[%s,%s,%s]' % (n, n, n),
                    'shape=[%d,%d,3]' % (s, s), 'pixel=RGB', 'thread=8', 'method=' + NCNN_METHOD], check=True,
                   stdout=subprocess.DEVNULL)
    subprocess.run(['ncnn2int8', p, b, os.path.join(d, 'model.int8.param'), os.path.join(d, 'model.int8.bin'), t],
                   check=True)


def flops(m, s):
    import thop
    import copy
    macs, params = thop.profile(copy.deepcopy(m).float(), inputs=(torch.zeros(1, 3, s, s),), verbose=False)
    return 2 * macs / 1e9


# ---------------------------------------------------------------- parity and FP32-remaining checks
def run_all(d, files, xs):
    """outputs of each file on xs (N x S x S x 3 uint8): {key: [N outputs]}"""
    x = xs.astype(np.float32) / 255
    out = {}
    ts = torch.jit.load(os.path.join(d, files['torchscript']))
    with torch.inference_mode():
        out['torchscript'] = [np.asarray(ts(torch.from_numpy(v[None].transpose(0, 3, 1, 2).copy()))) for v in x]
    import onnxruntime as ort
    for k in ('onnx', 'onnx_int8'):
        s = ort.InferenceSession(os.path.join(d, files[k]), providers=['CPUExecutionProvider'])
        i = s.get_inputs()[0].name
        out[k] = [s.run(None, {i: v[None].transpose(0, 3, 1, 2).copy()})[0] for v in x]
    from ai_edge_litert.interpreter import Interpreter
    for k in ('tflite_fp32', 'tflite_int8'):
        it = Interpreter(model_path=os.path.join(d, files[k]))
        it.allocate_tensors()
        inp, o = it.get_input_details()[0], it.get_output_details()[0]
        res = []
        for v in x:
            it.set_tensor(inp['index'], v[None].astype(inp['dtype']))
            it.invoke()
            res.append(it.get_tensor(o['index']).copy())
        out[k] = res
    import ncnn
    for k, f16 in (('ncnn', False), ('ncnn_fp16', True), ('ncnn_int8', False)):
        net = ncnn.Net()
        net.opt.use_vulkan_compute = False
        net.opt.use_fp16_packed = net.opt.use_fp16_storage = net.opt.use_fp16_arithmetic = f16
        net.load_param(os.path.join(d, files[k.replace('_fp16', '')][0]))
        net.load_model(os.path.join(d, files[k.replace('_fp16', '')][1]))
        res = []
        for v in x:
            ex = net.create_extractor()
            a = np.ascontiguousarray(v.transpose(2, 0, 1))      # the Mat does not copy: keep a alive
            ex.input('in0', ncnn.Mat(a))
            res.append(np.array(ex.extract('out0')[1])[None])
        out[k] = res
    return out


def parity(name, s, out):
    ref = out['torchscript']
    r = {}
    for k, v in out.items():
        if k == 'torchscript':
            continue
        a = np.stack([np.asarray(z, np.float32).reshape(ref[0].shape) for z in v])
        b = np.stack(ref).astype(np.float32)
        if name.startswith('yolo'):
            a, b = a.reshape(len(a), 84, -1).copy(), b.reshape(len(b), 84, -1)  # N x 84 x anchors
            if k.startswith('tflite'):                                          # TFLite: xywh / imgsz (see export_yolo)
                a[:, :4] *= s
            box, cls = np.abs(a[:, :4] - b[:, :4]), np.abs(a[:, 4:] - b[:, 4:])
            top = b[:, 4:].max(1) > 0.25                                        # anchors the reference finds something at
            bt = box.max(1)[top]
            r[k] = {'box_px_max': float(box.max()), 'score_max_abs': float(cls.max()), 'detections': int(top.sum()),
                    'box_px_at_detections_mean': float(bt.mean()), 'box_px_at_detections_max': float(bt.max()),
                    'score_mean_abs_at_detections': float(np.abs(a[:, 4:].max(1) - b[:, 4:].max(1))[top].mean())}
        else:
            r[k] = {'logit_max_abs': float(np.abs(a - b).max()),
                    'top1_agree': float((a.reshape(len(a), -1).argmax(1) == b.reshape(len(b), -1).argmax(1)).mean())}
    return r


def fp32_left(d, files):
    res = {}
    m = onnx.load(os.path.join(d, files['onnx_int8']))
    prod = {o: n for n in m.graph.node for o in n.output}
    comp = [n for n in m.graph.node if n.op_type in ('Conv', 'MatMul', 'Gemm')]
    q = [n for n in comp if prod.get(n.input[1]) is not None and prod[n.input[1]].op_type == 'DequantizeLinear']
    res['onnx_int8'] = {'compute_ops': len(comp), 'int8_weight_ops': len(q), 'fp32_compute_ratio': 1 - len(q) / len(comp),
                        'op_count': len(m.graph.node)}
    from ai_edge_litert.interpreter import Interpreter, OpResolverType
    it = Interpreter(model_path=os.path.join(d, files['tflite_int8']),      # the model's own ops, before XNNPACK
                     experimental_op_resolver_type=OpResolverType.BUILTIN_WITHOUT_DEFAULT_DELEGATES)
    it.allocate_tensors()
    td = {t['index']: t for t in it.get_tensor_details()}
    ops = it._get_ops_details()
    fl = [o for o in ops if o['inputs'].size and td.get(int(o['inputs'][0]), {}).get('dtype') == np.float32]
    res['tflite_int8'] = {'ops': len(ops), 'fp32_input_ops': len(fl), 'fp32_op_ratio': len(fl) / len(ops),
                          'fp32_op_types': sorted({o['op_name'] for o in fl})}
    conv = [ln for ln in open(os.path.join(d, files['ncnn_int8'][0])).read().splitlines()[2:]
            if ln.split()[:1] and ln.split()[0] in ('Convolution', 'ConvolutionDepthWise', 'InnerProduct', 'Gemm')]
    q8 = [ln for ln in conv if any(t.startswith('8=') and t != '8=0' for t in ln.split())]
    allc = len(open(os.path.join(d, files['ncnn_int8'][0])).read().splitlines()) - 2
    res['ncnn_int8'] = {'conv_like_layers': len(conv), 'int8_layers': len(q8), 'fp32_conv_ratio': 1 - len(q8) / max(1, len(conv)),
                        'layers': allc}
    return res


# ---------------------------------------------------------------- exports
def export_yolo(name):
    from ultralytics import YOLO
    s = int(name.split('-')[1])
    d = os.path.join(OUT, name)
    os.makedirs(d, exist_ok=True)
    pt = os.path.join(d, 'yolo11n.pt')
    if not os.path.exists(pt):
        YOLO('yolo11n.pt')                                                       # downloads into the working directory
        shutil.copy('yolo11n.pt', pt)
    y = YOLO(pt)
    y.export(format='torchscript', imgsz=s)
    y.export(format='onnx', imgsz=s, simplify=True)
    shutil.move(os.path.join(d, 'yolo11n.torchscript'), os.path.join(d, 'model.torchscript'))
    shutil.move(os.path.join(d, 'yolo11n.onnx'), os.path.join(d, 'model.onnx'))
    # TFLite: Ultralytics 8.4.218 exports it through LiteRT, not on Linux ARM64; its earlier path was onnx2tf on an ONNX
    # whose head divides xywh by the image size before joining the scores (one INT8 scale can not hold both 0-640 and
    # 0-1). The same here: that ONNX, then Ultralytics' own onnx2tf call (onnx2saved_model).
    from ultralytics.nn.modules.head import Detect
    from ultralytics.utils.export.tensorflow import onnx2saved_model
    orig = Detect._get_decode_boxes
    Detect._get_decode_boxes = lambda self, x: orig(self, x) / s
    try:
        YOLO(pt).export(format='onnx', imgsz=s, simplify=True)
    finally:
        Detect._get_decode_boxes = orig
    shutil.move(os.path.join(d, 'yolo11n.onnx'), os.path.join(d, 'model.tfl.onnx'))
    shutil.rmtree(os.path.join(d, 'tflite'), ignore_errors=True)
    onnx2saved_model(os.path.join(d, 'model.tfl.onnx'), os.path.join(d, 'tflite'), quantize=8, images=calib(name, s))
    YOLO(pt).export(format='ncnn', imgsz=s)
    shutil.rmtree(os.path.join(d, 'ncnn'), ignore_errors=True)
    shutil.move(os.path.join(d, 'yolo11n_ncnn_model'), os.path.join(d, 'ncnn'))
    m = y.model
    return d, s, m, sum(p.numel() for p in m.parameters())


def export_cls(name):
    from ultralytics.utils.export.ncnn import torch2ncnn
    from ultralytics.utils.export.tensorflow import onnx2saved_model
    s = 224
    d = os.path.join(OUT, name)
    os.makedirs(d, exist_ok=True)
    net = {'resnet18': lambda: torchvision.models.resnet18(weights='IMAGENET1K_V1'),
           'mobilenetv2': lambda: torchvision.models.mobilenet_v2(weights='IMAGENET1K_V1')}[name]()
    m = Norm(net).eval()
    x = torch.zeros(1, 3, s, s)
    torch.jit.trace(m, x).save(os.path.join(d, 'model.torchscript'))
    torch.onnx.export(m, x, os.path.join(d, 'model.onnx'), input_names=['images'], output_names=['output0'],
                      opset_version=17, dynamo=False)
    shutil.rmtree(os.path.join(d, 'tflite'), ignore_errors=True)
    onnx2saved_model(os.path.join(d, 'model.onnx'), os.path.join(d, 'tflite'), quantize=8, images=calib(name, s))
    torch2ncnn(m, x, os.path.join(d, 'ncnn'))
    return d, s, m, sum(p.numel() for p in net.parameters())


for name in WANT:
    print('=====', name, flush=True)
    d, s, m, params = (export_yolo if name.startswith('yolo') else export_cls)(name)
    xs = calib(name, s)
    onnx_int8(os.path.join(d, 'model.onnx'), os.path.join(d, 'model.int8.onnx'), xs)
    ncnn_int8(os.path.join(d, 'ncnn'), s)
    tfl = sorted(glob.glob(os.path.join(d, 'tflite', '*.tflite')))
    print('tflite files:', [os.path.basename(f) for f in tfl], flush=True)
    pick = lambda tag: os.path.relpath(next(f for f in tfl if f.endswith(tag) and 'full_integer' not in f), d)
    files = {'torchscript': 'model.torchscript', 'onnx': 'model.onnx', 'onnx_int8': 'model.int8.onnx',
             'tflite_fp32': pick('_float32.tflite'), 'tflite_int8': pick('_integer_quant.tflite'),
             'ncnn': ['ncnn/model.ncnn.param', 'ncnn/model.ncnn.bin'],
             'ncnn_int8': ['ncnn/model.int8.param', 'ncnn/model.int8.bin']}
    size = {k: sum(os.path.getsize(os.path.join(d, f)) for f in (v if isinstance(v, list) else [v]))
            for k, v in files.items()}
    out = run_all(d, files, xs[:4])
    g = onnx.load(os.path.join(d, 'model.onnx')).graph
    man[name] = {'input': {'name': g.input[0].name, 'size': s, 'scale': '1/255', 'layout': 'NCHW (TFLite NHWC)'},
                 'output_shape': list(np.asarray(out['torchscript'][0]).shape),
                 'tflite_coords_normalized': name.startswith('yolo'), 'params': int(params),
                 'gflops': round(flops(m, s), 3), 'files': files, 'bytes': size,
                 'parity_vs_torchscript': parity(name, s, out), 'int8_fp32_left': fp32_left(d, files),
                 'onnx_opset': [o.version for o in onnx.load(os.path.join(d, 'model.onnx')).opset_import if o.domain in ('', 'ai.onnx')]}
    json.dump(man, open(MAN, 'w'), indent=1)
    print(json.dumps(man[name], indent=1), flush=True)

import importlib.metadata as md
man['_tools'] = {p: md.version(p) for p in ('torch', 'torchvision', 'ultralytics', 'onnx', 'onnxruntime', 'onnx2tf',
                                            'tensorflow', 'ai-edge-litert', 'ncnn', 'pnnx') if p}
man['_tools']['ncnn_tools_commit'] = open('/ncnn-commit').read().strip()
json.dump(man, open(MAN, 'w'), indent=1)
print('done', man['_tools'])
