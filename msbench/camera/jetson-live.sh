#!/bin/sh
# jetson-live.sh — Phase 3 on the Jetson (11.116): live USB camera YOLO with the configurations measured on E850
# board 6 (CPU, 4 threads) plus the GPU runtimes (host 1 thread), 30 s each, the DVS live running as usual. The usbcam
# viewer (mpv) holds the camera, so it is closed first and started again at the end in the desktop session.
cd "$(dirname "$0")/.." || exit 1
log=results/live.log
pkill -x mpv && sleep 2
run() { echo "== $*" >> $log; python3 camera/realtime_detection.py --secs 30 --tag jetson-dvslive "$@" >> $log 2>&1; }
run --model yolo11n-320 --runtime ncnn --precision fp16 --threads 4
run --model yolo11n-640 --runtime ncnn --precision fp16 --threads 4
run --model yolo11n-320 --runtime onnxruntime --precision int8 --threads 4
run --model yolo11n-320 --runtime tflite --precision fp32 --threads 4
. benchmark/jetson-gpu-env.sh
run --model yolo11n-320 --runtime tensorrt --precision fp16 --threads 1
run --model yolo11n-640 --runtime tensorrt --precision fp16 --threads 1
run --model yolo11n-640 --runtime tensorrt --precision int8 --threads 1
run --model yolo11n-640 --runtime pytorch-cuda --precision fp32 --threads 1
U=/run/user/$(id -u)
setsid env DISPLAY=:0 XAUTHORITY=$U/gdm/Xauthority DBUS_SESSION_BUS_ADDRESS=unix:path=$U/bus XDG_RUNTIME_DIR=$U \
    sh "$HOME/dvs-jetson/usbcam" > /dev/null 2>&1 < /dev/null &
echo LIVEDONE >> $log
