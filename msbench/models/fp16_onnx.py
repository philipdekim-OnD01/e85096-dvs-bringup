#!/usr/bin/env python3
"""fp16_onnx.py MODELS_DIR: model.fp16.onnx beside each model.onnx (float16 inside, float32 input/output) for
onnxruntime CUDA and TensorRT at FP16 (msbench Phase 7), files['onnx_fp16'] in manifest.json. Where trt_int8.py made
model.int8s.onnx, also model.int8s16.onnx (files['onnx_int8_fp16']): the same Q/DQ with float16 scales (opset 21) and
float16 everywhere else, so TensorRT 11 (strongly typed) runs INT8 convolutions with FP16 between them instead of FP32.
Export container."""
import json, os, sys
import onnx
from onnx import version_converter
from onnxconverter_common import float16

d = sys.argv[1]
man = json.load(open(os.path.join(d, 'manifest.json')))
for k, v in man.items():
    if k.startswith('_'):
        continue
    src, dst = os.path.join(d, k, 'model.onnx'), os.path.join(d, k, 'model.fp16.onnx')
    m = onnx.load(src)
    del m.graph.value_info[:]        # onnxslim's float32 value_info left on YOLO's Resize breaks the converted types
    onnx.save(float16.convert_float_to_float16(m, keep_io_types=True), dst)
    v['files']['onnx_fp16'] = 'model.fp16.onnx'
    v['bytes']['onnx_fp16'] = os.path.getsize(dst)
    print(k, v['bytes']['onnx_fp16'])
    q = os.path.join(d, k, 'model.int8s.onnx')
    if os.path.exists(q):
        m = onnx.load(q)
        del m.graph.value_info[:]
        m = float16.convert_float_to_float16(version_converter.convert_version(m, 21), keep_io_types=True)
        onnx.save(m, os.path.join(d, k, 'model.int8s16.onnx'))
        v['files']['onnx_int8_fp16'] = 'model.int8s16.onnx'
        v['bytes']['onnx_int8_fp16'] = os.path.getsize(os.path.join(d, k, 'model.int8s16.onnx'))
        print(k, 'int8+fp16', v['bytes']['onnx_int8_fp16'])
json.dump(man, open(os.path.join(d, 'manifest.json'), 'w'), indent=1)
