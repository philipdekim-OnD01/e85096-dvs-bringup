#!/usr/bin/env python3
"""tables.py [--acc results/accuracy.json] RAW.jsonl ...: the results as Markdown tables for the docs (11.116).
Files are read in the order given; a later run of the same device/model/runtime/precision/threads replaces an earlier
one (the Jetson's 1- and 4-thread rerun on quiet cores over the first full run). Prints, per model, the CPU latency
median per thread count on both devices with the E850/Jetson ratio, then the Jetson GPU rows, then accuracy."""
import argparse
import json
import os

RP = [('pytorch', 'fp32'), ('onnxruntime', 'fp32'), ('onnxruntime', 'int8'), ('tflite', 'fp32'), ('tflite', 'int8'),
      ('ncnn', 'fp32'), ('ncnn', 'fp16'), ('ncnn', 'int8')]
GP = [('pytorch-cuda', 'fp32'), ('pytorch-cuda', 'fp16'), ('ort-cuda', 'fp32'), ('ort-cuda', 'fp16'),
      ('tensorrt', 'fp32'), ('tensorrt', 'fp16'), ('tensorrt', 'int8'), ('tensorrt', 'int8fp16')]
MODELS = ['yolo11n-640', 'yolo11n-320', 'resnet18', 'mobilenetv2']


def fmt(v):
    return '—' if v is None else ('%.1f' % v if v >= 10 else '%.2f' % v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--acc')
    ap.add_argument('raw', nargs='+')
    a = ap.parse_args()
    acc = json.load(open(a.acc)) if a.acc else {}
    d, tuned = {}, []
    for f in a.raw:
        for ln in open(f):
            r = json.loads(ln)
            t = 'gpu' if (r['runtime'], r['precision']) in GP else str(r['threads']) if r['threads'] in (1, 4) else 'max'
            if r.get('env'):
                tuned.append(r)
                continue
            d[r['device'], r['model'], r['runtime'], r['precision'], t] = r
    med = lambda *k: (d.get(k) or {}).get('latency_ms', {}).get('median')

    def accs(dev, m, rt, p):
        r = next((v for k, v in d.items() if k[:4] == (dev, m, rt, p) and isinstance(v.get('accuracy'), dict)), None)
        x = (r or {}).get('accuracy') or {}
        if 'top1' in x:
            return 'top-1 %.3f, top-5 %.3f' % (x['top1'], x['top5'])
        y = acc.get(os.path.basename(x.get('detections_file') or ''))
        return 'mAP50-95 %.3f, mAP50 %.3f, P %.2f, R %.2f' % (y['map50_95'], y['map50'], y['precision'],
                                                             y['recall']) if y else '—'
    for m in MODELS:
        print('\n**%s** (중앙값 ms; E850 스레드 1/4/8, Jetson 1/4/6; 비 = E850/Jetson)\n' % m)
        print('| 런타임 | E850 1 | E850 4 | E850 8 | Jetson 1 | Jetson 4 | Jetson 6 | 비 1 | 비 4 | 비 최대 |')
        print('|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|')
        for rt, p in RP:
            x = [med('e850', m, rt, p, t) for t in ('1', '4', 'max')]
            y = [med('orin-nano', m, rt, p, t) for t in ('1', '4', 'max')]
            ratio = ['%.2f×' % (u / v) if u and v else '—' for u, v in zip(x, y)]
            print('| %s %s | %s | %s | %s |' % (rt, p.upper(), ' | '.join(map(fmt, x)), ' | '.join(map(fmt, y)),
                                                ' | '.join(ratio)))
    if any(k[4] == 'gpu' for k in d):
        print('\n**Jetson GPU** (호스트 1스레드 CPU5, 중앙값 ms; H2D·계산·D2H는 따로 잰 100회 중앙값)\n')
        print('| 모델 | 런타임 | 전체 | p95 | H2D | 계산 | D2H | GPU MHz | VDD_IN W | J/추론 | 정확도 |')
        print('|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|')
        for m in MODELS:
            for rt, p in GP:
                r = d.get(('orin-nano', m, rt, p, 'gpu'))
                if not r:
                    continue
                if 'latency_ms' not in r:
                    print('| %s | %s %s | 오류: %s |' % (m, rt, p.upper(), r.get('error', '?')[-120:].replace('\n', ' ')))
                    continue
                g, b = r['gpu_phases_ms'], r['blocks'][-1]
                print('| %s | %s %s | %s | %s | %.2f | %.2f | %.2f | %s | %s | %s | %s |' % (
                    m, rt, p.upper(), fmt(r['latency_ms']['median']), fmt(r['latency_ms']['p95']), g['h2d'],
                    g['compute'], g['d2h'], b.get('gpu_mhz_after'), (b.get('power_w') or {}).get('VDD_IN', '—'),
                    (b.get('energy_j_per_inference') or {}).get('VDD_IN', '—'), accs('orin-nano', m, rt, p)))
    if tuned:
        print('\n**환경 변수를 바꾼 판** (중앙값 ms, 같은 설정의 기본 판과 비교)\n')
        print('| 장비 | 모델 | 런타임 | 스레드 | 환경 | 기본 | 바꾼 판 | 비 |')
        print('|---|---|---|---:|---|---:|---:|---:|')
        for r in tuned:
            t = str(r['threads']) if r['threads'] in (1, 4) else 'max'
            x, y = med(r['device'], r['model'], r['runtime'], r['precision'], t), r.get('latency_ms', {}).get('median')
            print('| %s | %s | %s %s | %d | `%s` | %s | %s | %s |' % (r['device'], r['model'], r['runtime'],
                  r['precision'].upper(), r['threads'], r['env'], fmt(x), fmt(y), '%.2f×' % (x / y) if x and y else '—'))
    print('\n**정확도** (같은 200장, CPU는 최대 스레드에서 한 번)\n')
    print('| 모델 | 런타임 | E850 | Jetson |')
    print('|---|---|---|---|')
    for m in MODELS:
        for rt, p in RP:
            print('| %s | %s %s | %s | %s |' % (m, rt, p.upper(), accs('e850', m, rt, p), accs('orin-nano', m, rt, p)))


if __name__ == '__main__':
    main()
