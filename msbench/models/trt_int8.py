#!/usr/bin/env python3
"""trt_int8.py DATA_DIR MODELS_DIR [MODEL ...]: model.int8s.onnx beside each model.onnx for TensorRT INT8 (msbench Phase 7).
TensorRT 11 takes only symmetric INT8 (zero point 0, signed) from explicit Q/DQ, so this is model.int8.onnx's recipe
(same 100 calibration images, MinMax, per-channel weights, Conv/MatMul/Gemm only) with signed symmetric activations
instead of QUInt8, biases left float. Adds files['onnx_int8_sym'] to manifest.json. Export container.
ResNet18 keeps its first convolution in float: TensorRT 11.4 on Orin (sm_87) has no implementation for the INT8
conv1 (7x7, 3 channels) fused with ReLU and MaxPool, and the build fails (11.116)."""
import json, os, sys

import numpy as np
import onnx
from onnxruntime.quantization import CalibrationDataReader, QuantFormat, QuantType, quantize_static
from onnxruntime.quantization.shape_inference import quant_pre_process

DATA, d = sys.argv[1], sys.argv[2]
EXCLUDE = {'resnet18': ['/net/conv1/Conv']}
man = json.load(open(os.path.join(d, 'manifest.json')))
for k, v in man.items():
    if k.startswith('_') or sys.argv[3:] and k not in sys.argv[3:]:
        continue
    s = v['input']['size']
    xs = np.load(os.path.join(DATA, 'coco-calib-%d.npz' % s if k.startswith('yolo') else 'imagenet-calib-224.npz'))['x']
    src, dst = os.path.join(d, k, 'model.onnx'), os.path.join(d, k, 'model.int8s.onnx')
    pre = dst + '.pre.onnx'
    quant_pre_process(src, pre, skip_symbolic_shape=True)
    name = onnx.load(src).graph.input[0].name

    class R(CalibrationDataReader):
        def __init__(self):
            self.it = iter(xs[i:i + 1].transpose(0, 3, 1, 2).astype(np.float32) / 255 for i in range(len(xs)))

        def get_next(self):
            x = next(self.it, None)
            return None if x is None else {name: x}
    quantize_static(pre, dst, R(), quant_format=QuantFormat.QDQ, per_channel=True, activation_type=QuantType.QInt8,
                    weight_type=QuantType.QInt8, op_types_to_quantize=['Conv', 'MatMul', 'Gemm'],
                    nodes_to_exclude=EXCLUDE.get(k, []),
                    extra_options={'ActivationSymmetric': True, 'WeightSymmetric': True,
                                   'QuantizeBias': False})                    # TensorRT: no Int32 bias DQ
    os.remove(pre)
    zp = [t for t in onnx.load(dst).graph.initializer if t.name.endswith('_zero_point')]
    assert zp and all(not onnx.numpy_helper.to_array(t).any() for t in zp), 'non-zero zero point left'
    v['files']['onnx_int8_sym'] = 'model.int8s.onnx'
    v['bytes']['onnx_int8_sym'] = os.path.getsize(dst)
    print(k, v['bytes']['onnx_int8_sym'], 'zero points', len(zp), flush=True)
json.dump(man, open(os.path.join(d, 'manifest.json'), 'w'), indent=1)
