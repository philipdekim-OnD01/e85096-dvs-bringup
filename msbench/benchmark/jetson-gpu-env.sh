# . benchmark/jetson-gpu-env.sh — Jetson GPU runtimes unpacked in the home directory without sudo (msbench Phase 7):
#   ~/jp/py      torch 2.14.1+cu130 and its nvidia-* wheels (pip.pyz install --target)
#   ~/jp/py-ort  onnxruntime-gpu 1.24.0 (pypi.jetson-ai-lab.io sbsa/cu130, --no-deps)
#   ~/jp/root    TensorRT 11.4.1 + cudart 13.2 debs from the L4T r39.2 apt repo (apt-get download; dpkg -x)
# then: python3 benchmark/run_benchmark.py --runtime gpu --threads 4 --accuracy
J=${JP:-$HOME/jp}
export MSB_PYPATH=$J/py-ort:$J/py:$J/root/usr/lib/python3.12/dist-packages
export LD_LIBRARY_PATH=$J/root/usr/lib/aarch64-linux-gnu:$J/root/usr/local/cuda-13.2/targets/sbsa-linux/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}
export TRTEXEC=$J/root/usr/bin/trtexec
