# allcore.sh DIR CPUS [PY]: aibench.py on each CPU at once (docs 11.115): the board's whole throughput
D=$1; P=${3:-}
for c in $(echo $2 | tr , ' '); do
  (cd $D/models && env $P ACT=act.onnx FACE=face.onnx python3 $D/aibench.py $c 20 2>/dev/null | sed "s/^/cpu$c /") > /tmp/allcore.$c &
done
wait; cat /tmp/allcore.*; rm -f /tmp/allcore.*
