"""export.py (--random | CKPT) OUT.onnx: model.Step as ONNX for the board (x, h -> y, h2; opset 17), checked
against onnxruntime on 5 random inputs: 'parity ok max_abs=<v>', exit 1 above 1e-4. CKPT: torch.save of
{'model': state_dict} (train.py)."""
import sys

import numpy as np
import onnxruntime
import torch

from model import HID, Step


def main(src, out):
    m = Step()
    if src != '--random':
        m.load_state_dict(torch.load(src, map_location='cpu')['model'])
    m.eval()
    torch.onnx.export(m, (torch.zeros(1, 2, 120, 160), torch.zeros(1, HID)), out, input_names=['x', 'h'],
                      output_names=['y', 'h2'], opset_version=17, dynamo=False)
    s = onnxruntime.InferenceSession(out, providers=['CPUExecutionProvider'])
    g, worst = torch.Generator().manual_seed(0), 0.0
    for _ in range(5):
        x, h = torch.rand(1, 2, 120, 160, generator=g) * 4, torch.randn(1, HID, generator=g) * 0.5
        with torch.no_grad():
            want = [v.numpy() for v in m(x, h)]
        got = s.run(None, {'x': x.numpy(), 'h': h.numpy()})
        worst = max([worst] + [float(np.abs(a - b).max()) for a, b in zip(got, want)])
    print('parity %s max_abs=%.2e' % ('ok' if worst <= 1e-4 else 'FAILED', worst))
    return worst > 1e-4


if __name__ == '__main__':
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    sys.exit(main(*sys.argv[1:]))
