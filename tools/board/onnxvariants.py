# onnxvariants.py (docs 11.115): FP16 and int8 (QDQ, static, random calibration: speed only, accuracy not checked) variants of
# the two DVS models in /w (act.onnx, face.onnx). Runs in a throwaway python:3.11-slim container with onnx, onnxconverter-common, onnxruntime.
import numpy as np, onnx
from onnxconverter_common import float16
from onnxruntime.quantization import quantize_static, QuantFormat, QuantType, CalibrationDataReader
from onnxruntime.quantization.shape_inference import quant_pre_process
rng = np.random.default_rng(1)
shapes = {'act': {'x': (1, 2, 120, 160), 'h': (1, 128)}, 'face': {'input': (1, 3, 384, 480)}}
class R(CalibrationDataReader):
    def __init__(s, name, m):
        ins = [i.name for i in m.graph.input]
        s.d = iter([{k: (rng.random(shapes[name][k], dtype=np.float32) > .9).astype(np.float32) * (255 if name == 'face' else 1)
                     for k in ins} for _ in range(8)])
    def get_next(s): return next(s.d, None)
for name in ('act', 'face'):
    m = onnx.load('/w/%s.onnx' % name)
    print(name, 'ops', sorted({n.op_type for n in m.graph.node}), 'opset', [o.version for o in m.opset_import])
    block = ['GRU', 'Gemm', 'Softmax', 'MaxPool', 'Resize']
    onnx.save(float16.convert_float_to_float16(m, keep_io_types=True, op_block_list=block), '/w/%s-fp16.onnx' % name)
    src = '/w/%s.onnx' % name
    if name == 'face':      # opset 11: DequantizeLinear has no axis (per-channel) before 13
        from onnx import version_converter
        src = '/w/face13.onnx'
        onnx.save(version_converter.convert_version(m, 13), src)
    quant_pre_process(src, '/w/%s-pre.onnx' % name, skip_symbolic_shape=True)
    quantize_static('/w/%s-pre.onnx' % name, '/w/%s-int8.onnx' % name, R(name, m), quant_format=QuantFormat.QDQ,
                    per_channel=True, activation_type=QuantType.QUInt8, weight_type=QuantType.QInt8,
                    op_types_to_quantize=['Conv', 'MatMul'])
    print(name, 'done')
