__author__ = 'max'

import torch
import torch.nn as nn
import torch.nn.functional as F

from ..masked import DownShiftConv2d, DownRightShiftConv2d
from ..masked import DownShiftConvTranspose2d, DownRightShiftConvTranspose2d
from ..weight_norm import Conv2dWeightNorm, ConvTranspose2dWeightNorm


def concat_elu(x, dim=1):
    """ like concatenated ReLU (http://arxiv.org/abs/1603.05201), but then with ELU """
    return F.elu(torch.cat([x, -x], dim=dim))


def elu(x):
    return F.elu(x)


class GatedResnetBlock(nn.Module):
    def __init__(self, in_channels, h_channels=0, dropout=0.0, activation=concat_elu):
        super(GatedResnetBlock, self).__init__()
        assert activation in [elu, concat_elu]
        gain = 1 if activation == elu else 2
        self.activation = activation
        self.down_conv1 = DownShiftConv2d(
            gain * in_channels, in_channels, kernel_size=(2, 3), bias=True
        )
        self.down_conv2 = DownShiftConv2d(
            gain * in_channels, in_channels, kernel_size=(2, 3), bias=True
        )
        self.down_conv3 = DownShiftConv2d(
            gain * in_channels, 2 * in_channels, kernel_size=(2, 3), bias=True
        )

        self.down_right_conv1 = DownRightShiftConv2d(
            gain * in_channels, in_channels, kernel_size=(2, 2), bias=True
        )
        self.down_right_conv2 = DownRightShiftConv2d(
            gain * in_channels, in_channels, kernel_size=(2, 2), bias=True
        )
        self.nin = Conv2dWeightNorm(gain * in_channels, in_channels, kernel_size=(1, 1))
        self.down_right_conv3 = DownRightShiftConv2d(
            gain * in_channels, 2 * in_channels, kernel_size=(2, 2), bias=True
        )

        if h_channels:
            self.h_conv1 = Conv2dWeightNorm(
                gain * h_channels, in_channels, kernel_size=(3, 3), padding=1
            )
            self.h_conv2 = Conv2dWeightNorm(
                gain * in_channels, 2 * in_channels, kernel_size=(3, 3), padding=1
            )
        else:
            self.h_conv1 = None
            self.h_conv2 = None

        self.dropout = nn.Dropout(p=dropout)

    def initialize(self, x1, x2, h=None, init_scale=1.0):
        if h is not None:
            # h_channels -> 2 * h_channels -> in_channels -> 2 * in_channels -> 2 * in_channels
            hc = self.h_conv1.initialize(self.activation(h), init_scale=init_scale)
            hc = self.h_conv2.initialize(self.activation(hc), init_scale=0.1 * init_scale)
        else:
            hc = 0

        # [batch, 2 * in_channels, H, W]
        c1 = self.down_conv1.initialize(self.activation(x1), init_scale=init_scale)
        c1 = self.down_conv2.initialize(self.activation(c1), init_scale=init_scale)
        # dropout
        c1 = self.dropout(self.activation(c1))
        # [batch, in_channels, H, W]
        a1, b1 = (self.down_conv3.initialize(c1, init_scale=0.1 * init_scale) +
                  hc).chunk(2, 1)
        c1 = a1 * torch.sigmoid(b1) + x1

        # [batch, 2 * in_channels, H, W]
        aux = self.nin.initialize(self.activation(c1), init_scale=init_scale)
        c2 = self.down_right_conv1.initialize(self.activation(x2), init_scale=init_scale)
        c2 = self.down_right_conv2.initialize(self.activation(c2), init_scale=init_scale)
        # dropout
        c2 = self.dropout(self.activation(c2 + aux))
        a2, b2 = (self.down_right_conv3.initialize(c2, init_scale=0.1 * init_scale) +
                  hc).chunk(2, 1)
        c2 = a2 * torch.sigmoid(b2) + x2

        return c1, c2

    def forward(self, x1, x2, h=None):
        if h is not None:
            # h_channels -> 2 * h_channels -> in_channels -> 2 * in_channels -> 2 * in_channels
            hc = self.h_conv1(self.activation(h))
            hc = self.h_conv2(self.activation(hc))
        else:
            hc = 0

        # [batch, 2 * in_channels, H, W]
        c1 = self.down_conv1(self.activation(x1))
        c1 = self.down_conv2(self.activation(c1))
        # dropout
        c1 = self.dropout(self.activation(c1))
        # [batch, in_channels, H, W]
        a1, b1 = (self.down_conv3(c1) + hc).chunk(2, 1)
        c1 = a1 * torch.sigmoid(b1) + x1

        # [batch, 2 * in_channels, H, W]
        aux = self.nin(self.activation(c1))
        c2 = self.down_right_conv1(self.activation(x2))
        c2 = self.down_right_conv2(self.activation(c2))
        # dropout
        c2 = self.dropout(self.activation(c2 + aux))
        a2, b2 = (self.down_right_conv3(c2) + hc).chunk(2, 1)
        c2 = a2 * torch.sigmoid(b2) + x2

        return c1, c2


class TopShitBlock(nn.Module):
    def __init__(self, in_channels, out_channels):
        super(TopShitBlock, self).__init__()
        self.down_conv1 = DownShiftConv2d(
            in_channels, out_channels, kernel_size=(2, 3), bias=True
        )

        self.down_conv2 = DownShiftConv2d(
            in_channels, out_channels, kernel_size=(1, 3), bias=True
        )
        self.down_right_conv = DownRightShiftConv2d(
            in_channels, out_channels, kernel_size=(2, 1), bias=True
        )

    @staticmethod
    def down_shift(input):
        batch_size, channels, H, W = input.size()
        return torch.cat(
            [input.new_zeros(batch_size, channels, 1, W), input[:, :, :H - 1, :]], dim=2
        )

    @staticmethod
    def right_shift(input):
        batch_size, channels, H, W = input.size()
        return torch.cat(
            [input.new_zeros(batch_size, channels, H, 1), input[:, :, :, :W - 1]], dim=3
        )

    def initialize(self, x, init_scale=1.0):
        x1 = TopShitBlock.down_shift(self.down_conv1.initialize(x, init_scale=init_scale))
        x2 = TopShitBlock.down_shift(self.down_conv2.initialize(x, init_scale=init_scale))
        x2 = x2 + TopShitBlock.right_shift(
            self.down_right_conv.initialize(x, init_scale=init_scale)
        )
        return x1, x2

    def forward(self, input):
        x1 = TopShitBlock.down_shift(self.down_conv1(input))
        x2 = TopShitBlock.down_shift(self.down_conv2(input))
        x2 = x2 + TopShitBlock.right_shift(self.down_right_conv(input))
        return x1, x2


class DownSamplingBlock(nn.Module):
    def __init__(self, num_filters):
        super(DownSamplingBlock, self).__init__()
        self.down_conv = DownShiftConv2d(
            num_filters, num_filters, kernel_size=(2, 3), stride=(2, 2), bias=True
        )
        self.down_right_conv = DownRightShiftConv2d(
            num_filters, num_filters, kernel_size=(2, 2), stride=(2, 2), bias=True
        )

    def initialize(self, x1, x2, h=None, init_scale=1.0):
        x1 = self.down_conv.initialize(x1, init_scale=init_scale)
        x2 = self.down_right_conv.initialize(x2, init_scale=init_scale)
        return x1, x2

    def forward(self, x1, x2, h=None):
        x1 = self.down_conv(x1)
        x2 = self.down_right_conv(x2)
        return x1, x2


class UpSamplingBlock(nn.Module):
    def __init__(self, num_filters):
        super(UpSamplingBlock, self).__init__()
        self.down_deconv = DownShiftConvTranspose2d(
            num_filters, num_filters, kernel_size=(2, 3), stride=(2, 2), bias=True
        )
        self.down_right_deconv = DownRightShiftConvTranspose2d(
            num_filters, num_filters, kernel_size=(2, 2), stride=(2, 2), bias=True
        )

    def initialize(self, x1, x2, h=None, init_scale=1.0):
        x1 = self.down_deconv.initialize(x1, init_scale=init_scale)
        x2 = self.down_right_deconv.initialize(x2, init_scale=init_scale)
        return x1, x2

    def forward(self, x1, x2, h=None):
        x1 = self.down_deconv(x1)
        x2 = self.down_right_deconv(x2)
        return x1, x2


class NINBlock(nn.Module):
    def __init__(self, num_filters, activation=concat_elu):
        super(NINBlock, self).__init__()
        assert activation in [elu, concat_elu]
        gain = 1 if activation == elu else 2
        self.activation = activation
        self.nin = Conv2dWeightNorm(gain * num_filters, num_filters, kernel_size=(1, 1))

    def initialize(self, x, residual, init_scale=1.0):
        residual = self.nin.initialize(self.activation(residual), init_scale=init_scale)
        return x + residual

    def forward(self, x, residual):
        residual = self.nin(self.activation(residual))
        return x + residual


class Identity(nn.Module):
    def __init__(self):
        super(Identity, self).__init__()

    def initialize(self, x, init_scale=1.0):
        return x

    def forward(self, input):
        return input


class DownSampling(nn.Module):
    def __init__(self, num_filters):
        super(DownSampling, self).__init__()
        self.conv = Conv2dWeightNorm(
            num_filters, num_filters * 2, kernel_size=(3, 3), stride=(2, 2), padding=1
        )

    def initialize(self, x, init_scale=1.0):
        return self.conv.initialize(x, init_scale=init_scale)

    def forward(self, x):
        return self.conv(x)


class UpSampling(nn.Module):
    def __init__(self, num_filters):
        super(UpSampling, self).__init__()
        self.deconv = ConvTranspose2dWeightNorm(
            num_filters,
            num_filters // 2,
            kernel_size=(3, 3),
            stride=(2, 2),
            padding=1,
            output_padding=1
        )

    def initialize(self, x, init_scale=1.0):
        return self.deconv.initialize(x, init_scale=init_scale)

    def forward(self, x):
        return self.deconv(x)


class PixelCNNPP(nn.Module):
    def __init__(
        self,
        levels,
        in_channels,
        out_channels,
        num_resnets,
        h_channels=0,
        dropout=0.0,
        activation='concat_elu'
    ):
        """

        Args:
            levels: the levels of the network (number of gated resnet blocks in up or down pass)
            in_channels: the input channels
            out_channels: the output and hidden channels
            num_resnets: number of resnet layers in each gated resnet block
            h_channels: the channel of conditional input h (default = 0)
            dropout: dropout rate
        """
        super(PixelCNNPP, self).__init__()
        up_layers = []
        nins1 = []
        nins2 = []
        up_hs = []

        assert activation in ['elu', 'concat_elu']
        activation = elu if activation == 'elu' else concat_elu

        ori_h_channels = h_channels
        # ////////// up pass //////////
        for l in range(levels):
            if l == 0:
                up_layers.append(TopShitBlock(in_channels, out_channels))
                up_hs.append(Identity())
            else:
                up_layers.append(DownSamplingBlock(out_channels))
                nins1.append(NINBlock(out_channels, activation))
                nins2.append(NINBlock(out_channels, activation))
                if h_channels:
                    up_hs.append(DownSampling(h_channels))
                    h_channels = h_channels * 2
                else:
                    up_hs.append(Identity())

            for rep in range(num_resnets):
                up_layers.append(
                    GatedResnetBlock(out_channels, h_channels, dropout, activation)
                )
                nins1.append(NINBlock(out_channels, activation))
                nins2.append(NINBlock(out_channels, activation))
                up_hs.append(Identity())

        # ////////// down pass //////////
        down_layers = []
        down_hs = []
        for l in range(levels):
            for rep in range(num_resnets):
                down_layers.append(
                    GatedResnetBlock(out_channels, h_channels, dropout, activation)
                )
                down_hs.append(Identity())

            if l < levels - 1:
                down_layers.append(UpSamplingBlock(out_channels))
                if h_channels:
                    down_hs.append(UpSampling(h_channels))
                    h_channels = h_channels // 2
                else:
                    down_hs.append(Identity())

        assert len(nins1) == len(down_layers)
        self.up_layers = nn.ModuleList(up_layers)
        self.down_layers = nn.ModuleList(down_layers)
        self.nins1 = nn.ModuleList(nins1)
        self.nins2 = nn.ModuleList(nins2)

        assert ori_h_channels == h_channels
        assert len(up_hs) == len(up_layers)
        assert len(down_hs) == len(down_layers)
        self.up_hs = nn.ModuleList(up_hs)
        self.down_hs = nn.ModuleList(down_hs)

    def initialize(self, x, h=None, init_scale=1.0):
        up_pass = []
        x1 = None
        x2 = None
        for l, (layer, up_h) in enumerate(zip(self.up_layers, self.up_hs)):
            if l == 0:
                x1, x2 = layer.initialize(x, init_scale=init_scale)
            else:
                x1, x2 = layer.initialize(x1, x2, h=h, init_scale=init_scale)
                up_pass.append((x1, x2))
            h = up_h.initialize(h, init_scale=init_scale)

        for l, (layer, down_h, nin1, nin2) in enumerate(
            zip(self.down_layers, self.down_hs, self.nins1, self.nins2)
        ):
            u1, u2 = up_pass.pop()
            if l == 0:
                x1 = u1
                x2 = u2
            else:
                x1 = nin1.initialize(x1, u1, init_scale=init_scale)
                x2 = nin2.initialize(x2, u2, init_scale=init_scale)
            x1, x2 = layer.initialize(x1, x2, h, init_scale=init_scale)
            h = down_h.initialize(h, init_scale=init_scale)
        assert len(up_pass) == 0
        return x2

    def forward(self, input, h=None):
        up_pass = []
        x1 = None
        x2 = None
        for l, (layer, up_h) in enumerate(zip(self.up_layers, self.up_hs)):
            if l == 0:
                x1, x2 = layer(input)
            else:
                x1, x2 = layer(x1, x2, h=h)
                up_pass.append((x1, x2))
            h = up_h(h)

        for l, (layer, down_h, nin1, nin2) in enumerate(
            zip(self.down_layers, self.down_hs, self.nins1, self.nins2)
        ):
            u1, u2 = up_pass.pop()
            if l == 0:
                x1 = u1
                x2 = u2
            else:
                x1 = nin1(x1, u1)
                x2 = nin2(x2, u2)
            x1, x2 = layer(x1, x2, h)
            h = down_h(h)
        assert len(up_pass) == 0
        return x2
