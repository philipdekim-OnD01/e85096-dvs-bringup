#!/usr/bin/env python3
"""generate_report.py [--compare-devices all] [--compare-sensors all] [--raw FILE.jsonl ...] [--out results/report.html]

The HTML report of the measured results (msbench README, Phase 10 first part): reads results/raw/*.jsonl (all runs unless --raw names
files, in name order = device then time, a later run of the same configuration replacing an earlier one; runs tagged
'smoke' are skipped), results/accuracy.json (COCO mAP from
benchmark/evaluate.py), results/manifest.json (model files, parity, INT8 coverage) and results/env/*.json. Writes one
self-contained page (no network) and results/summary.csv. Shows only measured values: a missing combination is
"N/A" with its reason, never filled in.
"""
import argparse
import csv
import glob
import html
import json
import os
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, 'results')
DEV_NAME = {'e850': 'E850 (Cortex-A55 ×8)', 'orin-nano': 'Orin Nano (Cortex-A78AE ×6)', 'agx-orin': 'AGX Orin'}
NA = [('AGX Orin, DLA', '장비 없음. Orin Nano에는 DLA가 없다'),
      ('E850 GPU INT8, 그 밖의 GPU 런타임', 'Mali-G52는 TFLite GL(GLES 3.1 compute, Mesa panfrost)과 NCNN Vulkan(Mesa panvk, 11.118)으로 잰다. GL 백엔드에는 정수 연산이 없고, NCNN의 INT8은 CPU에서만 돈다. OpenCL이 없어 ORT·ArmNN GPU 경로는 없다'),
      ('E850 NCNN Vulkan H2D·계산·D2H', 'NCNN Python에는 GPU 버퍼를 따로 올리고 내리는 API가 없어 나눠 재지 않았다. 전체(입력 numpy→출력 numpy, 동기)만 잰다'),
      ('E850 GPU YOLO 전부', 'GL에 TRANSPOSE·BATCH_MATMUL 셰이더와 상수 텐서 SUB(박스 디코드)가 없어 346개 중 167개(분할 1개)만 GPU, 나머지는 CPU 4스레드'),
      ('E850 전력·에너지', '전력 센서 없음. 외부 전력계가 필요하다'),
      ('Jetson PyTorch·onnxruntime CUDA INT8', '이 모델들에 INT8 CUDA 커널 경로가 없다(QDQ는 CUDA에서 FP32 합성곱으로 풀린다). GPU INT8은 TensorRT만'),
      ('Jetson GPU 클럭 고정', 'jetson_clocks에 sudo 필요: GPU DVFS(306~1020 MHz) 그대로 쟀고 블록 끝 클럭을 기록'),
      ('DVS, CAMERA+DVS 모드', 'DVS 객체 검출 모델과 라벨 데이터 없음, Phase 4·5')]


def runs(names):
    return names or [f for f in sorted(glob.glob(os.path.join(RES, 'raw', '*.jsonl'))) if 'smoke' not in os.path.basename(f)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--compare-devices', default='all')
    ap.add_argument('--compare-sensors', default='all')
    ap.add_argument('--raw', nargs='*')
    ap.add_argument('--out', default=os.path.join(RES, 'report.html'))
    a = ap.parse_args()
    files, seen = runs(a.raw), {}
    for f in files:
        for ln in open(f):
            if ln.strip():
                r = dict(json.loads(ln), run=os.path.basename(f)[:-6])
                seen[r['device'], r['model'], r['runtime'], r['precision'], r['threads'], r.get('env') or ''] = r
    rows = list(seen.values())
    if a.compare_devices != 'all':
        rows = [r for r in rows if r['device'] in a.compare_devices.split(',')]
    acc = json.load(open(os.path.join(RES, 'accuracy.json'))) if os.path.exists(os.path.join(RES, 'accuracy.json')) else {}
    man = json.load(open(os.path.join(RES, 'manifest.json'))) if os.path.exists(os.path.join(RES, 'manifest.json')) else {}
    env, mids = {}, {r.get('machine_id') for r in rows}
    for f in sorted(glob.glob(os.path.join(RES, 'env', '*.json'))):
        e = json.load(open(f))
        # shortcut: rows from before machine_id was recorded take the device's first file by name (e850-board4 = the
        # benchmark board); a file whose machine id matches a row wins
        if e['device'] not in env or e.get('machine_id') in mids:
            env[e['device']] = e
    data = []
    for r in rows:
        b = r.get('blocks') or []
        d = {k: r.get(k) for k in ('device', 'model', 'runtime', 'precision', 'threads', 'rss_peak_mb', 'load_ms',
                                   'file_bytes', 'error', 'fps', 'warmup_runs', 'run', 'env')}
        d['gpu'] = r.get('gpu_phases_ms') or ({'h2d': None, 'compute': None, 'd2h': None}
                                              if r.get('runtime') == 'ncnn-vk' else None)   # GPU row without phases
        d['gpu_mhz'] = b[-1].get('gpu_mhz_after') if b else None
        d['cpus'] = r.get('cpus')
        lm = r.get('latency_ms') or {}
        d.update({'median': lm.get('median'), 'p95': lm.get('p95'), 'mean': lm.get('mean'), 'n': lm.get('n')})
        d['blocks'] = [x['latency_ms']['median'] for x in b]
        d['nms'] = b[0]['nms_ms']['median'] if b and 'nms_ms' in b[0] else None
        d['cores'] = b[-1].get('process_cpu_cores') if b else None
        d['pinned_busy'] = b[-1].get('pinned_busy_pct') if b else None
        d['machine_busy'] = b[-1].get('machine_busy_pct') if b else None
        d['clock'] = b[-1].get('clock_mhz_after') if b else None
        t = b[-1].get('temp_c_after') if b else None
        d['temp'] = max(t.values()) if t else None
        d['power'] = (b[-1].get('power_w') or {}).get('VDD_IN') if b else None
        d['energy'] = (b[-1].get('energy_j_per_inference') or {}).get('VDD_IN') if b else None
        d['idle_power'] = (r.get('power_idle_w') or {}).get('VDD_IN')
        ac = r['accuracy'] if isinstance(r.get('accuracy'), dict) else {}  # an errored config keeps cfg's bool
        d['top1'], d['top5'] = ac.get('top1'), ac.get('top5')
        det = os.path.basename(ac.get('detections_file') or '')
        if det in acc:
            d.update({k: acc[det].get(k) for k in ('map50_95', 'map50', 'recall', 'f1', 'miss_rate')})
            d['precision_det'] = acc[det].get('precision')            # 'precision' is FP32/INT8 here
        data.append(d)
    keys = sorted({k for d in data for k in d if k not in ('blocks', 'cpus', 'clock', 'gpu')})
    with open(os.path.join(RES, 'summary.csv'), 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction='ignore')
        w.writeheader()
        w.writerows(data)
    envs = {k: {'model': v.get('model'), 'os': v.get('os'), 'kernel': v.get('kernel'), 'python': v.get('python'),
                'cpu': '/'.join(v['cpu']['parts']) + ' ×%d' % v['cpu']['count'],
                'clock': ', '.join('%s: %d-%d MHz' % (p['cpus'], p['min_mhz'], p['max_mhz']) for p in v['cpu']['policies']),
                'mem': v['memory'].get('MemTotal_mb'), 'bg': v.get('background_busy_pct'),
                'runtimes': {m: x.get('version') for m, x in v['python_modules'].items() if x and 'version' in x}}
            for k, v in env.items() if k in {d['device'] for d in data}}
    mf = {k: {'params': v['params'], 'gflops': v['gflops'], 'bytes': v['bytes'], 'parity': v['parity_vs_torchscript'],
              'int8': v['int8_fp32_left']} for k, v in man.items() if not k.startswith('_')}
    page = PAGE.replace('/*DATA*/', json.dumps({'rows': data, 'env': envs, 'manifest': mf, 'tools': man.get('_tools'),
                                                 'names': DEV_NAME, 'na': NA, 'files': [os.path.basename(f) for f in files],
                                                 'made': time.strftime('%Y-%m-%d %H:%M')}, ensure_ascii=False))
    with open(a.out, 'w') as f:
        f.write(page)
    print('wrote %s (%d rows from %s) and summary.csv' % (a.out, len(data), ', '.join(os.path.basename(f) for f in files)))


PAGE = r'''<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Edge AI Runtime Benchmark</title>
<style>
:root{color-scheme:light;--surface:#fcfcfb;--panel:#ffffff;--text:#0b0b0b;--text2:#52514e;--muted:#7a7974;--grid:#e6e5e1;
--s1:#2a78d6;--s2:#eb6834;--line:#d9d8d3}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])){color-scheme:dark;--surface:#1a1a19;--panel:#222220;
--text:#f0efec;--text2:#c3c2b7;--muted:#9a998f;--grid:#33332f;--s1:#3987e5;--s2:#d95926;--line:#3a3a36}}
:root[data-theme="dark"]{color-scheme:dark;--surface:#1a1a19;--panel:#222220;--text:#f0efec;--text2:#c3c2b7;--muted:#9a998f;
--grid:#33332f;--s1:#3987e5;--s2:#d95926;--line:#3a3a36}
body{margin:0;background:var(--surface);color:var(--text);font:14px/1.5 -apple-system,"Apple SD Gothic Neo","Noto Sans KR",sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:32px 0 8px}p.sub{color:var(--text2);margin:0 0 16px}
h1,h2,p.sub,#envs,#na{overflow-wrap:anywhere}main>*{min-width:0}
.filters{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:8px 0 12px}
.filters label{color:var(--text2);font-size:13px}
select,button{font:inherit;color:var(--text);background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:4px 8px}
.legend{display:flex;gap:16px;font-size:13px;color:var(--text2)}.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;vertical-align:-1px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:8px 0}
.wrap{overflow-x:auto}table{border-collapse:collapse;font-size:12.5px;width:100%}
th,td{border-bottom:1px solid var(--grid);padding:4px 6px;text-align:right;white-space:nowrap}
th{color:var(--text2);font-weight:600;position:sticky;top:0;background:var(--panel)}td.l,th.l{text-align:left}
.tip{position:fixed;pointer-events:none;background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:6px 8px;
font-size:12px;box-shadow:0 2px 8px rgba(0,0,0,.15);display:none;z-index:9}
svg text{fill:var(--text2);font-size:11.5px}.na{color:var(--muted)}
</style></head><body><main>
<h1>Edge AI Runtime Benchmark: E850 vs Jetson</h1>
<p class="sub" id="sub"></p>
<div class="card" id="envs"></div>
<h2>CPU 런타임별 추론 지연 (중앙값, ms, 낮을수록 빠름)</h2>
<div class="filters"><label>모델 <select id="fm"></select></label><label>스레드 <select id="ft"></select></label>
<div class="legend" id="lg"></div></div>
<div class="card"><svg id="chart" width="100%" role="img" aria-label="런타임별 지연 막대그래프"></svg></div>
<h2>장비별 가장 빠른 설정 (FP32보다 정확도가 0.02 넘게 떨어진 설정은 뺌, 환경 변수를 바꾼 판 포함)</h2><div class="card wrap" id="best"></div>
<h2>GPU (Jetson은 호스트 1스레드, E850 TFLite GL·NCNN Vulkan은 CPU 폴백 4스레드; 입력 numpy→출력 numpy, 동기)</h2><div class="card wrap" id="gpu"></div>
<h2>Jetson / E850 속도 비 (같은 모델·런타임·정밀도·스레드)</h2><div class="card wrap" id="ratio"></div>
<h2>정확도 (같은 200장, 스레드 최대에서 한 번)</h2><div class="card wrap" id="acc"></div>
<h2>모델 변환 검증 (TorchScript FP32 대비, 보정 이미지 4장)</h2><div class="card wrap" id="par"></div>
<h2>전체 결과 표</h2><div class="card wrap" id="all" style="max-height:520px;overflow:auto"></div>
<h2>N/A (재지 못한 것과 원인)</h2><div class="card" id="na"></div>
<div class="tip" id="tip"></div>
<script>
const D=/*DATA*/;
const $=s=>document.querySelector(s), esc=s=>String(s??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const devs=[...new Set(D.rows.map(r=>r.device))].sort(), color=d=>devs.indexOf(d)?'var(--s2)':'var(--s1)';
const models=[...new Set(D.rows.map(r=>r.model))], RP=[['pytorch','fp32'],['onnxruntime','fp32'],['onnxruntime','int8'],
 ['tflite','fp32'],['tflite','int8'],['ncnn','fp32'],['ncnn','fp16'],['ncnn','int8']];
const tlabel=t=>t==1?'1':t==4?'4':'최대';
const tkey=(r)=>r.threads==1?'1':r.threads==4?'4':'max';
$('#sub').textContent=`측정한 값만 싣는다. 기록된 같은 입력(COCO val2017 200장, Imagenette 200장)으로 CPU·GPU 런타임을 비교. 생성 ${D.made}, 원자료 ${D.files.join(', ')}`;
$('#envs').innerHTML=devs.map(d=>{const e=D.env[d]||{};return `<b>${esc(D.names[d]||d)}</b>: ${esc(e.model)}; ${esc(e.os)}, 커널 ${esc(e.kernel)}, Python ${esc(e.python)}; ${esc(e.clock)}; 메모리 ${esc(e.mem)} MB; 배경 부하 ${esc(e.bg)}%; ${esc(Object.entries(e.runtimes||{}).map(([k,v])=>k+' '+v).join(', '))}`}).join('<br>');
$('#lg').innerHTML=devs.map(d=>`<span><i style="background:${color(d)}"></i>${esc(D.names[d]||d)}</span>`).join('');
$('#fm').innerHTML=models.map(m=>`<option>${m}</option>`).join('');
$('#ft').innerHTML=['1','4','max'].map(t=>`<option value="${t}">${tlabel(t)}</option>`).join('');
const get=(d,m,rt,p,t)=>D.rows.find(r=>!r.env&&r.device==d&&r.model==m&&r.runtime==rt&&r.precision==p&&tkey(r)==t);
const tip=$('#tip');
function show(e,h){tip.innerHTML=h;tip.style.display='block';tip.style.left=Math.min(e.clientX+12,innerWidth-260)+'px';tip.style.top=(e.clientY+12)+'px'}
function draw(){
 const m=$('#fm').value,t=$('#ft').value,svg=$('#chart'),W=svg.clientWidth||900,L=150,R=70,bh=10,gap=2,gh=devs.length*(bh+gap)+12;
 const vals=RP.flatMap(([rt,p])=>devs.map(d=>get(d,m,rt,p,t)?.median).filter(v=>v!=null)),mx=Math.max(...vals,1);
 const H=RP.length*gh+28,x=v=>L+(W-L-R)*v/mx;let s='';
 const ticks=5;for(let i=0;i<=ticks;i++){const v=mx*i/ticks,X=x(v);s+=`<line x1="${X}" x2="${X}" y1="0" y2="${H-20}" stroke="var(--grid)"/><text x="${X}" y="${H-6}" text-anchor="middle">${v<10?v.toFixed(1):Math.round(v)}</text>`}
 RP.forEach(([rt,p],i)=>{const y0=i*gh+4;s+=`<text x="${L-8}" y="${y0+gh/2}" text-anchor="end" dominant-baseline="middle">${rt} ${p.toUpperCase()}</text>`;
  devs.forEach((d,j)=>{const r=get(d,m,rt,p,t),y=y0+j*(bh+gap);
   if(!r||r.median==null){s+=`<text x="${L+4}" y="${y+bh/2}" dominant-baseline="middle" class="na">N/A${r&&r.error?' (오류)':''}</text>`;return}
   const w=Math.max(2,x(r.median)-L);
   s+=`<path d="M${L},${y}h${w-4}a4,4 0 0 1 4,4v${bh-8}a4,4 0 0 1-4,4h-${w-4}z" fill="${color(d)}"/>`+
      `<rect x="${L}" y="${y-1}" width="${W-L}" height="${bh+2}" fill="transparent" data-i="${D.rows.indexOf(r)}"/>`+
      `<text x="${L+w+4}" y="${y+bh/2}" dominant-baseline="middle">${r.median<10?r.median.toFixed(2):r.median.toFixed(1)}</text>`})});
 svg.setAttribute('height',H);svg.setAttribute('viewBox',`0 0 ${W} ${H}`);svg.innerHTML=s;
 svg.querySelectorAll('rect[data-i]').forEach(el=>{el.onmousemove=e=>{const r=D.rows[+el.dataset.i];
  show(e,`<b>${esc(D.names[r.device])}</b><br>${r.model} ${r.runtime} ${r.precision.toUpperCase()}, 스레드 ${r.threads} (CPU ${r.cpus})<br>중앙값 ${r.median} ms, p95 ${r.p95} ms, ${r.n}회<br>블록 중앙값 ${r.blocks.join(' / ')}<br>클럭 ${r.clock} MHz, 고정 코어 바쁨 ${r.pinned_busy}%`+(r.energy?`<br>VDD_IN ${r.power} W, ${r.energy} J/추론`:'')+(r.nms!=null?`<br>NMS ${r.nms} ms`:''))};el.onmouseleave=()=>tip.style.display='none'})}
$('#fm').onchange=$('#ft').onchange=draw;addEventListener('resize',draw);draw();
function table(el,head,body){el.innerHTML=`<table><thead><tr>${head.map((h,i)=>`<th class="${i<4?'l':''}">${h}</th>`).join('')}</tr></thead><tbody>${body.map(r=>`<tr>${r.map((c,i)=>`<td class="${i<4?'l':''}">${c??'<span class=na>N/A</span>'}</td>`).join('')}</tr>`).join('')}</tbody></table>`}
const f2=v=>v==null?null:(+v).toFixed(v<10?2:1);
if(devs.length>1){const a=devs.find(d=>d=='e850')||devs[0],b=devs.find(d=>d!=a);const body=[];
 models.forEach(m=>RP.forEach(([rt,p])=>{const row=[m,rt,p.toUpperCase(),''];['1','4','max'].forEach(t=>{const x=get(a,m,rt,p,t),y=get(b,m,rt,p,t);row.push(x&&y&&x.median&&y.median?(x.median/y.median).toFixed(2)+'×':null)});body.push(row)}));
 table($('#ratio'),['모델','런타임','정밀도','','스레드 1','스레드 4','스레드 최대'],body)} else $('#ratio').textContent='장비가 하나뿐이다';
{table($('#acc'),['모델','런타임','정밀도','장비','top-1','top-5','mAP50-95','mAP50','P','R','F1','누락률'],
  D.rows.filter(r=>r.top1!=null||r.map50_95!=null||r.recall!=null).map(r=>[r.model,r.runtime,r.precision.toUpperCase(),D.names[r.device],r.top1,r.top5,r.map50_95,r.map50,r.precision_det,r.recall,r.f1,r.miss_rate]))}
{const body=[];Object.entries(D.manifest).forEach(([m,v])=>Object.entries(v.parity).forEach(([k,p])=>body.push([m,k,(v.bytes[k.replace('_fp16','')]/1e6).toFixed(2)+' MB',v.params? (v.params/1e6).toFixed(2)+'M':'',v.gflops,
 p.top1_agree??null,p.logit_max_abs!=null?f2(p.logit_max_abs):null,p.box_px_at_detections_mean!=null?f2(p.box_px_at_detections_mean):null,p.score_mean_abs_at_detections!=null?p.score_mean_abs_at_detections.toFixed(4):null,
 k.endsWith('int8')?(()=>{const q=v.int8[k];return q?(100*(q.fp32_compute_ratio??q.fp32_op_ratio??q.fp32_conv_ratio)).toFixed(1)+'%':null})():''])));
 table($('#par'),['모델','형식','파일','파라미터','GFLOPs','top-1 일치','로짓 최대차','상자 평균차(px)','점수 평균차','INT8의 FP32 비율'],body)}
table($('#all'),['장비','모델','런타임','정밀도','스레드','환경','중앙값','p95','평균','횟수','NMS','RSS MB','프로세스 코어','고정 코어 바쁨%','전체 바쁨%','클럭','최고 온도','VDD_IN W','J/추론','오류'],
 D.rows.map(r=>[D.names[r.device],r.model,r.runtime,r.precision.toUpperCase(),r.threads,esc(r.env||''),f2(r.median),f2(r.p95),f2(r.mean),r.n,r.nms!=null?f2(r.nms):'',r.rss_peak_mb,r.cores,r.pinned_busy,r.machine_busy,(r.clock||[]).join('/'),r.temp,r.power,r.energy,r.error?esc(r.error).slice(0,80):'']));
const accv=r=>{const x=D.rows.find(q=>q.device==r.device&&q.model==r.model&&q.runtime==r.runtime&&q.precision==r.precision&&(q.top1!=null||q.map50_95!=null));return x?(x.top1??x.map50_95):null};
const accs=r=>{const v=accv(r);return v==null?'정확도 N/A':(r.model.startsWith('yolo')?'mAP50-95 ':'top-1 ')+v.toFixed(3)};
const ok=r=>{const ref=Math.max(...D.rows.filter(q=>q.device==r.device&&q.model==r.model&&q.precision=='fp32').map(accv).filter(v=>v!=null));const v=accv(r);return v!=null&&isFinite(ref)&&v>=ref-0.02};
const best=(d,m,gpu)=>D.rows.filter(r=>r.device==d&&r.model==m&&!!r.gpu==gpu&&r.median!=null&&ok(r)).sort((x,y)=>x.median-y.median)[0];
const bs=r=>r?`${f2(r.median)} ms <span class=na>${r.runtime} ${r.precision.toUpperCase()}${r.gpu?'':' t'+r.threads}${r.env?' + '+esc(r.env):''}, ${accs(r)}</span>`:null;
{const body=models.map(m=>{const e=best('e850',m,false),j=best('orin-nano',m,false),g=best('orin-nano',m,true),eg=best('e850',m,true);
 return [m,'','','',bs(e),bs(eg),bs(j),bs(g),e&&j?(e.median/j.median).toFixed(1)+'×':null,e&&g?(e.median/g.median).toFixed(0)+'×':null]});
 table($('#best'),['모델','','','','E850 CPU','E850 GPU','Jetson CPU','Jetson GPU','E850/Jetson CPU','E850 CPU/Jetson GPU'],body)}
{const G=D.rows.filter(r=>r.gpu);if(G.length)table($('#gpu'),['모델','런타임','정밀도','장비','중앙값','p95','H2D','계산','D2H','GPU MHz','VDD_IN W','J/추론','정확도'],
 G.map(r=>[r.model,r.runtime+(r.env?' + '+esc(r.env):''),r.precision.toUpperCase(),D.names[r.device],f2(r.median),f2(r.p95),r.gpu.h2d,r.gpu.compute,r.gpu.d2h,r.gpu_mhz,r.power,r.energy,accs(r)]));else $('#gpu').textContent='GPU 결과 없음'}
$('#na').innerHTML=D.na.map(([a,b])=>`<div><b>${esc(a)}</b>: ${esc(b)}</div>`).join('');
</script></main></body></html>'''

if __name__ == '__main__':
    main()
