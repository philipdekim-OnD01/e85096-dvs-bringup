"""Runtime adapters of the benchmark (msbench README): the same calls for PyTorch (TorchScript), onnxruntime, TFLite
(LiteRT) and NCNN on the CPU. Each takes the model directory's file from models/manifest.json, a thread count, and
input RGB uint8 H x W x 3; prepare() makes the runtime's own input (not timed), infer() runs the model once and returns
its first output as numpy (timed). Imports happen in the adapter so a child process loads only its own runtime.
"""
import os

import numpy as np


class Torch:
    def __init__(self, d, f, threads):
        import torch
        torch.set_num_threads(threads)
        try:
            torch.set_num_interop_threads(1)
        except RuntimeError:
            pass
        self.t = torch
        self.m = torch.jit.load(os.path.join(d, f), map_location='cpu').eval()
        self.info = {'torch': torch.__version__, 'threads': torch.get_num_threads(),
                     'cpu_capability': torch.backends.cpu.get_cpu_capability()}

    def prepare(self, x):
        return self.t.from_numpy(np.ascontiguousarray(x[None].transpose(0, 3, 1, 2), np.float32) / 255)

    def infer(self, x):
        with self.t.inference_mode():
            return self.m(x).numpy()


class ORT:
    def __init__(self, d, f, threads):
        import onnxruntime as ort
        o = ort.SessionOptions()
        o.intra_op_num_threads, o.inter_op_num_threads = threads, 1
        o.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        o.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        self.s = ort.InferenceSession(os.path.join(d, f), o, providers=['CPUExecutionProvider'])
        self.i = self.s.get_inputs()[0].name
        self.info = {'onnxruntime': ort.__version__, 'providers': self.s.get_providers()}

    def prepare(self, x):
        return np.ascontiguousarray(x[None].transpose(0, 3, 1, 2), np.float32) / 255

    def infer(self, x):
        return self.s.run(None, {self.i: x})[0]


class TFLite:
    def __init__(self, d, f, threads):
        from ai_edge_litert import __version__ as v
        from ai_edge_litert.interpreter import Interpreter
        self.it = Interpreter(model_path=os.path.join(d, f), num_threads=threads)
        self.it.allocate_tensors()
        self.inp, self.out = self.it.get_input_details()[0], self.it.get_output_details()[0]
        self.info = {'ai_edge_litert': v, 'input_dtype': str(self.inp['dtype'].__name__),
                     'delegate': 'XNNPACK (LiteRT default for CPU)'}

    def prepare(self, x):
        return np.ascontiguousarray(x[None], np.float32) / 255                # NHWC

    def infer(self, x):
        self.it.set_tensor(self.inp['index'], x)
        self.it.invoke()
        return self.it.get_tensor(self.out['index'])


class NCNN:
    FP16 = False        # ncnn's own default on ARMv8.2 (A55, A78AE) is FP16 storage and arithmetic: off = real FP32

    def __init__(self, d, f, threads):
        import ncnn
        self.n = ncnn
        self.net = ncnn.Net()
        o = self.net.opt
        o.use_vulkan_compute = False
        o.num_threads = threads
        o.use_int8_inference = True
        o.use_fp16_packed = o.use_fp16_storage = o.use_fp16_arithmetic = self.FP16
        assert self.net.load_param(os.path.join(d, f[0])) == 0 and self.net.load_model(os.path.join(d, f[1])) == 0
        self.info = {'ncnn': ncnn.__version__, 'threads': threads, 'fp16_storage_arithmetic': self.FP16}

    def prepare(self, x):
        a = np.ascontiguousarray(x.transpose(2, 0, 1), np.float32) / 255
        return self.n.Mat(a), a                 # the Mat points into a without copying: a must stay alive

    def infer(self, x):
        ex = self.net.create_extractor()
        ex.input('in0', x[0])
        return np.array(ex.extract('out0')[1])[None]


class NCNN16(NCNN):
    FP16 = True


# GPU (Jetson, Phase 7). infer() is host numpy in -> host numpy out, synchronous (H2D + model + D2H); phases() times
# the three apart, each followed by a synchronize. FP32 is strict everywhere (TF32 off), like trtexec --noTF32.
def _phases(h2d, compute, d2h, sync, n=100):
    import time
    t = {'h2d': [], 'compute': [], 'd2h': []}
    for _ in range(n):
        a = time.perf_counter(); g = h2d(); sync()
        b = time.perf_counter(); y = compute(g); sync()
        c = time.perf_counter(); d2h(y); sync()
        e = time.perf_counter()
        for k, v in zip(t, (b - a, c - b, e - c)):
            t[k].append(v * 1e3)
    return {k: round(float(np.median(v)), 3) for k, v in t.items()}


def _pinned(torch, x):
    return torch.from_numpy(np.ascontiguousarray(x[None].transpose(0, 3, 1, 2), np.float32) / 255).pin_memory()


class TorchCUDA:
    HALF = False

    def __init__(self, d, f, threads):
        import torch
        torch.backends.cudnn.allow_tf32 = torch.backends.cuda.matmul.allow_tf32 = False
        torch.backends.cudnn.benchmark = True
        self.t, self.h = torch, None
        self.dt = torch.float16 if self.HALF else torch.float32
        m = torch.jit.load(os.path.join(d, f), map_location='cuda').eval()
        self.m = m.half() if self.HALF else m
        self.info = {'torch': torch.__version__, 'cuda': torch.version.cuda, 'cudnn': torch.backends.cudnn.version(),
                     'gpu': torch.cuda.get_device_name(), 'dtype': str(self.dt), 'tf32': False}

    def prepare(self, x):
        return _pinned(self.t, x)

    def _run(self, g):
        y = self.m(g).float()
        if self.h is None:
            self.h = self.t.empty(y.shape, pin_memory=True)
        return y

    def infer(self, x):
        with self.t.inference_mode():
            y = self._run(x.to('cuda', self.dt, non_blocking=True))
            self.h.copy_(y)                   # synchronous D2H
        return self.h.numpy().copy()          # the pinned buffer is reused by the next call

    def phases(self, x):
        with self.t.inference_mode():
            return _phases(lambda: x.to('cuda', self.dt, non_blocking=True), self._run, self.h.copy_,
                           self.t.cuda.synchronize)


class TorchCUDA16(TorchCUDA):
    HALF = True


class ORTCUDA:
    def __init__(self, d, f, threads):
        import torch                                   # loads the cuDNN / cuBLAS of the torch wheels for onnxruntime
        import onnxruntime as ort
        if hasattr(ort, 'preload_dlls'):
            ort.preload_dlls()
        o = ort.SessionOptions()
        o.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.ort = ort
        self.s = ort.InferenceSession(os.path.join(d, f), o, providers=[
            ('CUDAExecutionProvider', {'device_id': 0, 'use_tf32': 0}), 'CPUExecutionProvider'])
        assert self.s.get_providers()[0] == 'CUDAExecutionProvider', self.s.get_providers()
        self.i, self.o = self.s.get_inputs()[0].name, self.s.get_outputs()[0].name
        self.info = {'onnxruntime': ort.__version__, 'providers': self.s.get_providers(), 'tf32': False,
                     'io': 'numpy in/out (onnxruntime copies through pageable memory)'}

    def prepare(self, x):
        return np.ascontiguousarray(x[None].transpose(0, 3, 1, 2), np.float32) / 255

    def infer(self, x):
        return self.s.run(None, {self.i: x})[0]

    def phases(self, x):
        b = self.s.io_binding()

        def compute(v):
            b.bind_ortvalue_input(self.i, v)
            b.bind_output(self.o, 'cuda')
            self.s.run_with_iobinding(b)
            return b.get_outputs()[0]
        return _phases(lambda: self.ort.OrtValue.ortvalue_from_numpy(x, 'cuda', 0), compute, lambda y: y.numpy(),
                       lambda: None)


class TRT:
    """TensorRT 11 (strongly typed: the ONNX file sets the precision). The engine is built once with trtexec into
    <model>/trt/ (build time in info) and loaded with the tensorrt Python API; device buffers are torch tensors."""

    def __init__(self, d, f, threads):
        import shutil
        import subprocess
        import time
        import tensorrt as trt
        import torch
        eng = os.path.join(d, 'trt', os.path.basename(f).replace('.onnx', '.engine'))
        self.info = {'tensorrt': trt.__version__, 'source': f, 'tf32': False}
        if not os.path.exists(eng):
            os.makedirs(os.path.dirname(eng), exist_ok=True)
            t0 = time.time()
            cmd = [os.environ.get('TRTEXEC') or shutil.which('trtexec'), '--onnx=' + os.path.join(d, f),
                   '--saveEngine=' + eng, '--noTF32', '--skipInference']
            p = subprocess.run(cmd, capture_output=True, text=True)
            if p.returncode or not os.path.exists(eng) or not os.path.getsize(eng):
                if os.path.exists(eng):
                    os.remove(eng)                   # trtexec leaves an empty file behind on a failed build
                raise RuntimeError('trtexec failed: ' + p.stdout[-2000:] + p.stderr[-2000:])
            self.info['engine_build_s'] = round(time.time() - t0, 1)
        self.t = torch
        self.e = trt.Runtime(trt.Logger(trt.Logger.WARNING)).deserialize_cuda_engine(open(eng, 'rb').read())
        self.c = self.e.create_execution_context()
        self.stream = torch.cuda.Stream()
        buf = {}
        for i in range(self.e.num_io_tensors):
            n = self.e.get_tensor_name(i)
            dt = torch.from_numpy(np.empty(0, trt.nptype(self.e.get_tensor_dtype(n)))).dtype
            buf[n] = torch.empty(tuple(self.e.get_tensor_shape(n)), dtype=dt, device='cuda')
            self.c.set_tensor_address(n, buf[n].data_ptr())
            if self.e.get_tensor_mode(n) == trt.TensorIOMode.INPUT:
                self.din = buf[n]
            else:
                self.dout = self.dout if hasattr(self, 'dout') else buf[n]       # the first output, as on the CPU
        self.h = torch.empty(self.dout.shape, dtype=self.dout.dtype, pin_memory=True)
        self.info.update(engine=os.path.relpath(eng, d), engine_bytes=os.path.getsize(eng),
                         io={n: [str(b.dtype), list(b.shape)] for n, b in buf.items()})

    def prepare(self, x):
        return _pinned(self.t, x)

    def _h2d(self, x):
        with self.t.cuda.stream(self.stream):
            self.din.copy_(x, non_blocking=True)

    def _compute(self, _):
        self.c.execute_async_v3(self.stream.cuda_stream)

    def _d2h(self, _):
        with self.t.cuda.stream(self.stream):
            self.h.copy_(self.dout, non_blocking=True)

    def infer(self, x):
        self._h2d(x); self._compute(None); self._d2h(None)
        self.stream.synchronize()
        return self.h.numpy().copy()          # the pinned buffer is reused by the next call

    def phases(self, x):
        return _phases(lambda: self._h2d(x), self._compute, self._d2h, self.stream.synchronize)


# (runtime, precision) -> (adapter, manifest file key)
RUNTIMES = {('pytorch', 'fp32'): (Torch, 'torchscript'), ('onnxruntime', 'fp32'): (ORT, 'onnx'),
            ('onnxruntime', 'int8'): (ORT, 'onnx_int8'), ('tflite', 'fp32'): (TFLite, 'tflite_fp32'),
            ('tflite', 'int8'): (TFLite, 'tflite_int8'), ('ncnn', 'fp32'): (NCNN, 'ncnn'),
            ('ncnn', 'fp16'): (NCNN16, 'ncnn'), ('ncnn', 'int8'): (NCNN, 'ncnn_int8')}
# PyTorch and onnxruntime-CUDA INT8 are N/A: neither has an INT8 CUDA kernel path for these models (QDQ on the CUDA
# provider dequantizes and runs FP32 convolutions), so INT8 on the GPU is TensorRT only.
GPU = {('pytorch-cuda', 'fp32'): (TorchCUDA, 'torchscript'), ('pytorch-cuda', 'fp16'): (TorchCUDA16, 'torchscript'),
       ('ort-cuda', 'fp32'): (ORTCUDA, 'onnx'), ('ort-cuda', 'fp16'): (ORTCUDA, 'onnx_fp16'),
       ('tensorrt', 'fp32'): (TRT, 'onnx'), ('tensorrt', 'fp16'): (TRT, 'onnx_fp16'),
       ('tensorrt', 'int8'): (TRT, 'onnx_int8_sym'), ('tensorrt', 'int8fp16'): (TRT, 'onnx_int8_fp16')}
RUNTIMES.update(GPU)

COCO91 = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 27, 28, 31, 32, 33, 34,
          35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61, 62, 63,
          64, 65, 67, 70, 72, 73, 74, 75, 76, 77, 78, 79, 80, 81, 82, 84, 85, 86, 87, 88, 89, 90]


def nms(out, size, coords_normalized, conf=0.25, iou=0.7, max_det=300, max_nms=30000):
    """YOLO11 head output (84 x anchors: cx cy w h, 80 class scores) -> boxes x1 y1 x2 y2 (letterbox px), score, class;
    class-aware greedy NMS as Ultralytics' (one label per box)"""
    p = np.asarray(out, np.float32).reshape(84, -1).T
    sc = p[:, 4:]
    cls = sc.argmax(1)
    s = sc[np.arange(len(cls)), cls]
    k = s > conf
    p, s, cls = p[k], s[k], cls[k]
    if coords_normalized:
        p = p.copy()
        p[:, :4] *= size
    o = s.argsort()[::-1][:max_nms]
    p, s, cls = p[o], s[o], cls[o]
    b = np.stack([p[:, 0] - p[:, 2] / 2, p[:, 1] - p[:, 3] / 2, p[:, 0] + p[:, 2] / 2, p[:, 1] + p[:, 3] / 2], 1)
    bo = b + cls[:, None] * 7680.0                                                # boxes of different classes never meet
    area = (bo[:, 2] - bo[:, 0]) * (bo[:, 3] - bo[:, 1])
    keep, idx = [], np.arange(len(bo))
    while idx.size and len(keep) < max_det:
        i = idx[0]
        keep.append(i)
        r = idx[1:]
        w = np.clip(np.minimum(bo[i, 2], bo[r, 2]) - np.maximum(bo[i, 0], bo[r, 0]), 0, None)
        h = np.clip(np.minimum(bo[i, 3], bo[r, 3]) - np.maximum(bo[i, 1], bo[r, 1]), 0, None)
        inter = w * h
        idx = r[inter / (area[i] + area[r] - inter + 1e-9) <= iou]
    return b[keep], s[keep], cls[keep]


def _selftest():
    out = np.zeros((84, 4), np.float32)
    out[:4, 0] = [100, 100, 50, 50]; out[4, 0] = .9                               # class 0
    out[:4, 1] = [102, 101, 50, 50]; out[4, 1] = .8                               # same class, overlaps: removed
    out[:4, 2] = [102, 101, 50, 50]; out[5, 2] = .7                               # other class: kept
    out[:4, 3] = [300, 300, 20, 20]; out[4, 3] = .1                               # under conf
    b, s, c = nms(out, 640, False)
    assert list(c) == [0, 1] and np.allclose(s, [.9, .7]) and np.allclose(b[0], [75, 75, 125, 125]), (b, s, c)
    b2, _, _ = nms(out / np.array([640] * 4 + [1] * 80, np.float32)[:, None], 640, True)
    assert np.allclose(b, b2, atol=1e-3)
    print('runtimes selftest ok')


if __name__ == '__main__':
    _selftest()
