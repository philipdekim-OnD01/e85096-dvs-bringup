#!/bin/sh
# threadprobe.sh MODEL RUNTIME PRECISION THREADS "CPUSET;ENV" ... — one run_benchmark child per case, for thread
# scaling questions (11.116: E850 PyTorch at 4 threads on CPU4-7). Each case is a cpu list and optional env, e.g.
#   sh benchmark/threadprobe.sh yolo11n-320 pytorch fp32 4 "4,5,6,7;" "0,1,2,3;" "4,5,6,7;OMP_WAIT_POLICY=ACTIVE"
# prints: cpus env median p95 process_cores pinned_busy clocks
cd "$(dirname "$0")/.." || exit 1
m=$1 rt=$2 p=$3 t=$4
shift 4
for c in "$@"; do
    cpus=${c%%;*} envs=${c#*;}
    cfg=$(printf '{"device":"probe","host":"","sensor":"camera","data":"recorded","model":"%s","runtime":"%s","precision":"%s","threads":%s,"cpus":[%s],"reps":2,"warmup":10,"iters":40,"max_secs":15,"accuracy":false,"models_dir":"models/files","data_dir":"data/files","det_dir":"/tmp"}' "$m" "$rt" "$p" "$t" "$cpus")
    env $envs python3 benchmark/run_benchmark.py --child "$cfg" | sed -n 's/^RESULT //p' | python3 -c "
import json, sys
r = json.load(sys.stdin); b = r['blocks'][-1]
print('cpus %-9s env %-24s median %8.1f p95 %8.1f cores %.2f busy %5.1f MHz %s' % ('$cpus', '$envs' or '-',
      r['latency_ms']['median'], r['latency_ms']['p95'], b['process_cpu_cores'], b['pinned_busy_pct'],
      b['clock_mhz_after']))"
done
