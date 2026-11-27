#!/bin/sh
# build-tflite-gl.sh: TFLite C API + the GPU delegate (v2, used with GL_ONLY: its OpenGL ES 3.1 compute backend) for the E850 (11.117), so the
# Mali-G52 runs models through the GLES driver the boards already have (Mesa panfrost on the vendor kbase, 11.82).
# The CMake build of TFLite has only the OpenCL backend (-DCL_DELEGATE_NO_GL), hence Bazel. Both libraries come from
# the same TensorFlow tag, so the TfLiteDelegate/TfLiteContext structs they share match.
# Runs on the Mac:  sh msbench/e850gpu/build-tflite-gl.sh   ->  ~/e850-logs/tflite-gl/{libtensorflowlite_c.so,
# libtensorflowlite_gpu_delegate.so}. Debian 12 arm64 container = the boards' glibc 2.36. Source and Bazel cache stay in
# the Docker volume msbench-tf (not on the Mac disk). minimal_logging_default.cc is built without NDEBUG so the log
# level stays VERBOSE: that is where TFLite says how many layers the delegate took ("Replacing N out of M node(s)").
set -e
TAG=v2.20.0
OUT=$HOME/e850-logs/tflite-gl
mkdir -p "$OUT"
docker run --rm -v msbench-tf:/w -v "$OUT:/out" -v "$(cd "$(dirname "$0")" && pwd)/tflite-gl.patch:/p:ro" debian:12 sh -c "
set -e
apt-get -qq update >/dev/null
apt-get -qq install -y --no-install-recommends git ca-certificates curl gcc g++ python3 python3-numpy patch \
    unzip zip libegl-dev libgles-dev >/dev/null
[ -x /w/bazelisk ] || curl -sL -o /w/bazelisk https://github.com/bazelbuild/bazelisk/releases/download/v1.25.0/bazelisk-linux-arm64
chmod +x /w/bazelisk
[ -d /w/tensorflow ] || git clone -q --depth 1 -b $TAG https://github.com/tensorflow/tensorflow.git /w/tensorflow
cd /w/tensorflow
git config --global --add safe.directory /w/tensorflow
git apply --reverse --check /p 2>/dev/null || git apply /p     # GL: skip ops it has no shader for
export HOME=/w/home USE_BAZEL_VERSION=\$(head -1 .bazelversion)
mkdir -p \$HOME
/w/bazelisk --output_user_root=/w/bazel build -c opt --jobs=8 --local_resources=memory=6000 \
    --repo_env=HERMETIC_PYTHON_VERSION=3.11 --copt=-DEGL_NO_X11 --copt=-DMESA_EGL_NO_X11_HEADERS \
    --linkopt=-lEGL --linkopt=-lGLESv2 --define=xnn_enable_avxvnniint8=false \
    --per_file_copt=tensorflow/lite/minimal_logging_default.cc@-UNDEBUG \
    //tensorflow/lite/c:libtensorflowlite_c.so //tensorflow/lite/delegates/gpu:libtensorflowlite_gpu_delegate.so
cp -f bazel-bin/tensorflow/lite/c/libtensorflowlite_c.so bazel-bin/tensorflow/lite/delegates/gpu/libtensorflowlite_gpu_delegate.so /out/
strip /out/*.so
ls -l /out
"
