"""The model for the MNIST variant of the multitask experiment."""

import torch
import torch.nn.functional as F
from torch import Tensor, nn


def assert_shape(x: Tensor, shape: (int, int)):
    """Raises an exception if the Tensor doesn't have the given final two dimensions."""
    assert tuple(x.shape[-2:]) == tuple(shape), f'Expected shape ending {shape}, got {x.shape}'


class Encoder(nn.Module):
    def __init__(self):
        super().__init__()
        self._encoder = nn.Sequential(nn.Conv2d(1, 16, 3, stride=3, padding=1),  # [batch x 16 x 10 x 10]
            nn.ReLU(True), nn.MaxPool2d(2, stride=2),  # [batch x 16 x 5 x 5]
            nn.Conv2d(16, 8, 3, stride=2, padding=1),  # [batch x 8 x 3 x 3]
            nn.ReLU(True), nn.MaxPool2d(2, stride=1)  # [batch x 8 x 2 x 2]
        )

    def forward(self, x):
        assert_shape(x, (28, 28))
        return self._encoder(x)

    @staticmethod
    def get_out_features():
        return 8


class Encoder2(nn.Module):
    """A larger encoder."""
    def __init__(self):
        super().__init__()
        self._conv1 = nn.Conv2d(1, 32, 5, stride=2, padding=4)
        self._conv2 = nn.Conv2d(32, 16, 3, stride=2, padding=1)

    def forward(self, x):
        assert_shape(x, (28, 28))

        x = F.relu(self._conv1(x))
        x = F.max_pool2d(x, 2, stride=2)

        x = F.relu(self._conv2(x))
        x = F.max_pool2d(x, 2, stride=2)

        return x

    @staticmethod
    def get_out_features():
        return 16


class Classifier(nn.Module):
    def __init__(self, num_classes: int, in_features: int):
        super().__init__()
        self._fc1 = nn.Linear(in_features=2 * 2 * in_features, out_features=128)
        self._fc2 = nn.Linear(in_features=128, out_features=num_classes)

    def forward(self, x):
        assert_shape(x, (2, 2))

        x = x.view(x.shape[0], -1)
        x = F.relu(self._fc1(x))
        x = self._fc2(x)
        return x


class Reconstructor(nn.Module):
    def __init__(self, in_features: int):
        super().__init__()
        self._decoder = nn.Sequential(nn.ConvTranspose2d(in_features, 16, 3, stride=2),  # b, 16, 5, 5
                                      nn.ReLU(True),
                                      nn.ConvTranspose2d(16, 8, 5, stride=3, padding=1),  # b, 8, 15, 15
                                      nn.ReLU(True),
                                      nn.ConvTranspose2d(8, 1, 2, stride=2, padding=1),  # b, 1, 28, 28
                                      nn.Tanh())

    def forward(self, x):
        x = self._decoder(x)
        assert_shape(x, (28, 28))
        return x


class MultitaskMnistModel(nn.Module):
    def __init__(self, initial_ses: [float], model_version: int):
        super().__init__()

        if model_version == 1:
            self._encoder = Encoder()
        elif model_version == 2:
            self._encoder = Encoder2()
        else:
            raise ValueError(f'Unknown model version {model_version}')

        self._classifier1 = Classifier(num_classes=3, in_features=self._encoder.get_out_features())
        self._classifier2 = Classifier(num_classes=10, in_features=self._encoder.get_out_features())
        self._reconstructor = Reconstructor(in_features=self._encoder.get_out_features())

        assert len(initial_ses) == 3
        self._weight1 = nn.Parameter(torch.tensor([initial_ses[0]]))
        self._weight2 = nn.Parameter(torch.tensor([initial_ses[1]]))
        self._weight3 = nn.Parameter(torch.tensor([initial_ses[2]]))

    def forward(self, x):
        assert_shape(x, (28, 28))

        x = self._encoder(x)
        x1 = self._classifier1(x)
        x2 = self._classifier2(x)
        x3 = self._reconstructor(x)
        return x1, x2, x3

    def get_loss_weights(self) -> (nn.Parameter, nn.Parameter, nn.Parameter):
        """Returns the loss weight parameters (s in the paper)."""
        return self._weight1, self._weight2, self._weight3
