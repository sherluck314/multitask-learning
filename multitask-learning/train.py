import cityscapes
import torch
import torch.nn as nn
from decoders import Decoders
from encoder import Encoder
from losses import MultiTaskLoss


class MultitaskLearner(nn.Module):
    def __init__(self, num_classes):
        super(MultitaskLearner, self).__init__()
        self.encoder = Encoder()
        self.decoder = Decoders(num_classes)

    def forward(self, x):
        return self.decoder(self.encoder(x))


def main(_run):
    train_loader = cityscapes.get_loader_from_dir(_run.config['root_dir_train'], _run.config)
    validation_loader = cityscapes.get_loader_from_dir(_run.config['root_dir_validation'], _run.config)



    learner = MultitaskLearner(_run.config['num_classes'])


    device = "cuda:0" if _run.config['gpu'] and torch.cuda.is_available() else "cpu"
    learner.to(device)

    criterion = MultiTaskLoss(_run.config['loss_type'], _run.config['loss_weights'])

    initial_learning_rate = 2.5e-3

    optimizer = torch.optim.SGD(learner.parameters(),
                                lr=initial_learning_rate,
                                momentum=0.9,
                                nesterov=True,
                                weight_decay=1e4)
    lr_lambda = lambda x: initial_learning_rate * (1 - x / _run.config['max_iter']) ** 0.9
    lr_scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer, lr_lambda=lr_lambda)

    for epoch in range(_run.config['max_iter']):  # loop over the dataset multiple times

        # polynomial learning rate decay
        lr_scheduler.step()

        running_loss = 0.0

        # training loop
        for i, data in enumerate(train_loader, 0):
            inputs, semantic_labels, instance_centroid, instance_mask = data

            inputs = inputs.to(device)
            semantic_labels  = semantic_labels.to(device)
            instance_centroid = instance_centroid.to(device)
            instance_mask = instance_mask.to(device)  

            # zero the parameter gradients
            optimizer.zero_grad()

            # forward + backward + optimize
            output_semantic, output_instance, output_depth = learner(inputs)
            loss = criterion((output_semantic, output_instance, output_depth),
                             semantic_labels, instance_centroid, instance_mask)
            loss.backward()
            optimizer.step()

            # print statistics
            running_loss += loss.item()
            # if i % 2000 == 1999:    # print every 2000 mini-batches
            print('[%d, %5d] loss: %.3f' %
                  (epoch + 1, i + 1, running_loss))
            running_loss = 0.0

        # validation loop
        for i, data in enumerate(validation_loader, 0):
            inputs, semantic_labels, instance_centroid, instance_mask = data

            # forward + backward + optimize
            output_semantic, output_instance, output_depth = learner(inputs.float())
            val_loss = criterion((output_semantic, output_instance, output_depth),
                                  semantic_labels.long(), instance_centroid, instance_mask)

            # print statistics
            running_loss += val_loss.item()
            # if i % 2000 == 1999:    # print every 2000 mini-batches
            print('[%d, %5d] loss: %.3f' %
                  (epoch + 1, i + 1, running_loss))
            running_loss = 0.0
