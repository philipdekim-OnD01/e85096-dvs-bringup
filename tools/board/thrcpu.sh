#!/bin/sh
# thrcpu.sh PID SECONDS: CPU % of each thread of PID over SECONDS (utime + stime from /proc/PID/task/*/stat; top -H
# misread the capture process's main thread as 99.9 %, docs 11.112), the total, and the DVS page's stats before and
# after (dvsctl :8080 /stats: events per frame, buffers/s, ingest ms; link counter deltas). Board or Jetson, no root.
pid=$1; s=$2; hz=$(getconf CLK_TCK)
snap(){ for t in /proc/$pid/task/*; do echo "$(basename $t) $(awk '{print $14+$15}' $t/stat) $(cat $t/comm)"; done | sort; }
snap > /tmp/thr.a; python3 -c "import urllib.request,sys; sys.stdout.write(urllib.request.urlopen(\"http://127.0.0.1:8080/stats\").read().decode())" > /tmp/st.a; sleep $s; snap > /tmp/thr.b; python3 -c "import urllib.request,sys; sys.stdout.write(urllib.request.urlopen(\"http://127.0.0.1:8080/stats\").read().decode())" > /tmp/st.b
join /tmp/thr.a /tmp/thr.b | awk -v s=$s -v hz=$hz '{d=($4-$2)*100/hz/s; tot+=d; if (d >= 0.5) printf "%-12s %6.1f%%\n", $3, d} END {printf "%-12s %6.1f%%\n", "TOTAL", tot}'
python3 - <<'P'
import json
a, b = json.load(open('/tmp/st.a')), json.load(open('/tmp/st.b'))
print('stats a:', {k: a.get(k) for k in ('fps', 'events', 'buffers', 'ingest_ms', 'behind_ms')})
print('stats b:', {k: b.get(k) for k in ('fps', 'events', 'buffers', 'ingest_ms', 'behind_ms')})
la, lb = a.get('link', {}), b.get('link', {})
print('link delta:', {k: lb.get(k, 0) - la.get(k, 0) for k in ('bufs', 'short_bufs', 'fe_missed', 'ts_missed', 'ts_bad')})
P
