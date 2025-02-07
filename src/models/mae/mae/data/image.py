import numpy as np

import torch
from torchvision import datasets, transforms


def load_datasets(dataset, data_path=None):
    if dataset == 'omniglot':
        return load_omniglot()
    elif dataset == 'mnist':
        return load_mnist()
    elif dataset.startswith('lsun'):
        category = dataset[5:]
        return load_lsun(data_path, category)
    elif dataset == 'cifar10':
        return load_cifar10()
    else:
        raise ValueError('unknown data set %s' % dataset)


def load_omniglot():
    def reshape_data(data):
        return data.T.reshape((-1, 1, 28, 28))

    train_data, train_label = torch.load('data/Omniglot/processed/training.pt')
    test_data, test_label = torch.load('data/Omniglot/processed/test.pt')
    train_data = torch.cat(
        list(
            map(
                lambda x: torch.nn.functional.interpolate(
                    x.unsqueeze(dim=0), size=(28, 28), mode='bicubic'
                ).clamp(0., 255.).div(255).squeeze(0),
                train_data.float().unsqueeze(1)
            )
        )
    )
    test_data = torch.cat(
        list(
            map(
                lambda x: torch.nn.functional.interpolate(
                    x.unsqueeze(dim=0), size=(28, 28), mode='bicubic'
                ).clamp(0., 255.).div(255).squeeze(0),
                test_data.float().unsqueeze(1)
            )
        )
    )
    train = list(zip(train_data.unsqueeze(1), train_label))
    test = list(zip(test_data.unsqueeze(1), test_label))
    return train, test, 2345


def load_mnist():
    train_data, train_label = torch.load('data/MNIST/processed/training.pt')
    test_data, test_label = torch.load('data/MNIST/processed/test.pt')

    train_data = train_data.float().div(255).unsqueeze(1)
    test_data = test_data.float().div(255).unsqueeze(1)

    return [(train_data[i], train_label[i]) for i in range(len(train_data))], [
        (test_data[i], test_label[i]) for i in range(len(test_data))
    ], 2000


def load_lsun(data_path, category):
    imageSize = 64
    train_data = datasets.LSUN(
        data_path,
        classes=[category + '_train'],
        transform=transforms.Compose(
            [
                transforms.Resize(96),
                transforms.RandomCrop(imageSize),
                transforms.ToTensor(),
                transforms.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5)),
            ]
        )
    )

    val_data = datasets.LSUN(
        data_path,
        classes=[category + '_val'],
        transform=transforms.Compose(
            [
                transforms.Resize(96),
                transforms.RandomCrop(imageSize),
                transforms.ToTensor(),
                transforms.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5)),
            ]
        )
    )

    return train_data, val_data, 4000


def load_cifar10():
    train_data = datasets.CIFAR10(
        'data/cifar10',
        train=True,
        download=True,
        transform=transforms.Compose(
            [
                transforms.ToTensor(),
                transforms.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5))
            ]
        )
    )
    test_data = datasets.CIFAR10(
        'data/cifar10',
        train=False,
        transform=transforms.Compose(
            [
                transforms.ToTensor(),
                transforms.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5))
            ]
        )
    )
    return train_data, test_data, 2000


def get_batch(data, indices):
    imgs = []
    labels = []
    for index in indices:
        img, label = data[index]
        imgs.append(img)
        labels.append(label)
    return torch.stack(imgs, dim=0), torch.LongTensor(labels)


def iterate_minibatches(data, indices, batch_size, shuffle):
    if shuffle:
        np.random.shuffle(indices)

    for start_idx in range(0, len(indices), batch_size):
        excerpt = indices[start_idx:start_idx + batch_size]
        yield get_batch(data, excerpt)


def binarize_image(img):
    return torch.rand(img.size()).type_as(img).le(img).float()


def binarize_data(data):
    return [(binarize_image(img), label) for img, label in data]
