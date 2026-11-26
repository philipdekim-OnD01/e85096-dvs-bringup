# msbench-export: model conversion on the Mac (linux/arm64): PyTorch CPU, Ultralytics (YOLO11n export: ONNX, TorchScript,
# TFLite through onnx2tf, NCNN through pnnx), torchvision (ResNet18, MobileNetV2), onnxruntime (INT8 QDQ), and the ncnn
# tools for NCNN INT8 (ncnn2table, ncnn2int8). docker build -t msbench-export:1 msbench/models
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends git cmake g++ make libgl1 libglib2.0-0 wget unzip \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir ultralytics onnx onnxruntime onnxslim pycocotools
# ncnn quantize tools (not in the pip packages); simpleocv so ncnn2table reads images without OpenCV
RUN git clone --depth 1 https://github.com/Tencent/ncnn /ncnn && cmake -S /ncnn -B /ncnn/build -DCMAKE_BUILD_TYPE=Release \
      -DNCNN_VULKAN=OFF -DNCNN_BUILD_EXAMPLES=OFF -DNCNN_BUILD_BENCHMARK=OFF -DNCNN_BUILD_TESTS=OFF -DNCNN_SIMPLEOCV=ON \
      -DNCNN_BUILD_TOOLS=ON && cmake --build /ncnn/build -j8 --target ncnn2table ncnn2int8 \
    && cp /ncnn/build/tools/quantize/ncnn2table /ncnn/build/tools/quantize/ncnn2int8 /usr/local/bin/ \
    && (cd /ncnn && git rev-parse HEAD > /ncnn-commit) && rm -rf /ncnn/build
WORKDIR /w
