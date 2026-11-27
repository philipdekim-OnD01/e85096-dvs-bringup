#!/bin/sh
# build-ncnn.sh: NCNN 20261020 (the boards' pip version) as a static C++ library with Vulkan, plus benchncnn and
# hetero.cpp, for the E850 (Debian 12 arm64). Native arm64 container on the Mac (11.121):
#   git clone --depth 1 --branch 20261020 https://github.com/Tencent/ncnn.git ~/e850-gpu/ncnn
#   (cd ~/e850-gpu/ncnn && git submodule update --init --depth 1 glslang)
#   docker run --rm -v ~/e850-gpu/ncnn:/ncnn -v "$PWD/msbench/e850gpu:/e:ro" -v ~/e850-gpu/out:/out debian:12 \
#       sh /e/build-ncnn.sh
# Output: /out/ncnn-e850/{hetero,benchncnn,libgomp.so.1}. Vulkan is dlopen'ed at run time (simplevk): source vk-env.sh first.
set -e
apt-get update -qq && apt-get install -y -qq --no-install-recommends cmake make g++ >/dev/null
B=/ncnn/build-e850 O=/out/ncnn-e850
mkdir -p $B $O && cd $B
cmake -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=$B/install -DNCNN_VULKAN=ON -DNCNN_SHARED_LIB=OFF \
    -DNCNN_BUILD_TOOLS=OFF -DNCNN_BUILD_EXAMPLES=OFF -DNCNN_BUILD_TESTS=OFF -DNCNN_BUILD_BENCHMARK=ON \
    -DNCNN_PYTHON=OFF .. >/dev/null
make -j"$(nproc)" install >/dev/null
cp benchmark/benchncnn $O/
mkdir -p $B/hetero && cd $B/hetero
cat > CMakeLists.txt <<'EOF'
cmake_minimum_required(VERSION 3.10)
project(hetero CXX)
find_package(ncnn REQUIRED)
add_executable(hetero /e/hetero.cpp)
target_link_libraries(hetero ncnn pthread)
EOF
cmake -DCMAKE_BUILD_TYPE=Release -Dncnn_DIR=$B/install/lib/cmake/ncnn . >/dev/null && make >/dev/null
cp hetero /usr/lib/aarch64-linux-gnu/libgomp.so.1 $O/ && ls -l $O   # libgomp: not every board has it
