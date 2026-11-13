"""The action model (spec 6): one 60 ms frame per step through MobileNetV3-small features (ImageNet), spatial
mean, Linear 576 -> 128, GRUCell 128, Linear 128 -> 9 logits (dvsact.CLASSES)."""
import torch
from torch import nn
from torchvision.models import MobileNet_V3_Small_Weights, mobilenet_v3_small

HID, NCLS = 128, 9


def _two_channel(conv, w=None):
    """the stem conv for (ON, OFF) counts instead of RGB; w: its weights"""
    c = nn.Conv2d(2, conv.out_channels, conv.kernel_size, conv.stride, conv.padding, bias=False)
    if w is not None:
        with torch.no_grad():
            c.weight.copy_(w)
    return c


class Step(nn.Module):
    """forward(x [B, 2, 120, 160] log1p counts, h [B, 128]) -> (logits [B, 9], h2 [B, 128])"""

    def __init__(self, enc=None):
        super().__init__()
        if enc is None:
            enc = mobilenet_v3_small().features
            enc[0][0] = _two_channel(enc[0][0])
        self.enc = enc
        self.proj = nn.Linear(576, HID)
        self.gru = nn.GRUCell(HID, HID)
        self.head = nn.Linear(HID, NCLS)

    def forward(self, x, h):
        h2 = self.gru(self.proj(self.enc(x).mean((2, 3))), h)
        return self.head(h2), h2

    @classmethod
    def imagenet(cls):
        """ImageNet features; each of the 2 stem channels gets the RGB mean x 1.5 (the sum of the 3 x 2/3)"""
        enc = mobilenet_v3_small(weights=MobileNet_V3_Small_Weights.IMAGENET1K_V1).features
        w = enc[0][0].weight
        enc[0][0] = _two_channel(enc[0][0], w.mean(1, keepdim=True).repeat(1, 2, 1, 1) * 1.5)
        return cls(enc)
