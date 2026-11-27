#!/bin/sh
# Board copy: /var/tmp/msb-rt3.sh. Run as philip on board 6: setsid nohup sh b6-live-gpu.sh; log results/realtime/run4.log
# msbench 11.117 on board 6: live USB camera YOLO11n-320 on the Mali-G52 (TFLite GL, CPU fallback 4 threads CPU4~7)
# in the usual state (DVS live + AI, dvsgl). Closes the USB camera viewer, runs GL FP16 with 1 and 2 GPU partitions,
# GL FP32, and NCNN FP16 bound (the 11.116 best CPU config, same session), then puts the viewer back.
cd ~/msbench; LOG=results/realtime/run4.log
E=$(pgrep -u philip -x lxpanel | head -1)
DISPLAY=$(tr '\0' '\n' < /proc/$E/environ | sed -n 's/^DISPLAY=//p') XAUTHORITY=$(tr '\0' '\n' < /proc/$E/environ | sed -n 's/^XAUTHORITY=//p') \
    xdotool search --name '^USB 카메라$' key q >> $LOG 2>&1
sleep 5
echo "== $(date) mpv: $(pgrep -x mpv) AI threads: $(ps -eLo comm | grep -cE '^dvs-(act|bin|face)')" >> $LOG
for c in "fp16 1" "fp16 2" "fp32 1"; do
    set -- $c
    echo "== yolo11n-320 tflite-gl $1 partitions $2" >> $LOG
    (. e850gpu/gpu-env.sh; MSB_GL_PARTITIONS=$2 python3 camera/realtime_detection.py --model yolo11n-320 --runtime tflite-gl \
        --precision $1 --threads 4 --secs 30 --tag b6-dvslive-gpu-p$2) >> $LOG 2>&1
    sleep 3
done
echo "== yolo11n-320 ncnn fp16 bind" >> $LOG
OMP_PROC_BIND=close OMP_PLACES=cores python3 camera/realtime_detection.py --model yolo11n-320 --runtime ncnn --precision fp16 \
    --threads 4 --secs 30 --tag b6-dvslive-aion-bind2 >> $LOG 2>&1
setsid xargs -0 -a /proc/$E/environ sh -c 'exec env -i "$@" e850-open cam' _ > /dev/null 2>&1 < /dev/null &
sleep 6
echo "== $(date) restored mpv: $(pgrep -x mpv)" >> $LOG
echo done-gpu >> $LOG
