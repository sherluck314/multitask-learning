import tempfile

import cityscapes
import torch
import torch.nn as nn
from decoders import Decoders
from encoder import Encoder
from losses import MultiTaskLoss


class MultitaskLearner(nn.Module):
    def __init__(self, num_classes, loss_weights):
        super(MultitaskLearner, self).__init__()
        self.encoder = Encoder()
        self.decoder = Decoders(num_classes)

        self.sem_log_var = nn.Parameter(torch.tensor(loss_weights[0], dtype=torch.float))
        self.inst_log_var = nn.Parameter(torch.tensor(loss_weights[1], dtype=torch.float))
        self.depth_log_var = nn.Parameter(torch.tensor(loss_weights[2], dtype=torch.float))

    def forward(self, x):
        return self.decoder(self.encoder(x))

    def get_loss_params(self):
        return self.sem_log_var, self.inst_log_var, self.depth_log_var


def main(_run):
    train_loader = cityscapes.get_loader_from_dir(_run.config['root_dir_train'], _run.config)
    validation_loader = cityscapes.get_loader_from_dir(_run.config['root_dir_validation'],
                                                       _run.config)

    learner = MultitaskLearner(_run.config['num_classes'], _run.config['loss_weights'])

    device = "cuda:0" if _run.config['gpu'] and torch.cuda.is_available() else "cpu"
    learner.to(device)

    if _run.config['loss_type'] == 'learned':
        loss_weights = learner.get_loss_params()
    elif _run.config['loss_type'] == 'fixed':
        loss_weights = _run.config['loss_weights']
    else:
        raise ValueError(f'Unknown loss_type {_run.config["loss_type"]}')

    criterion = MultiTaskLoss(_run.config['loss_type'], loss_weights, _run.config['enabled_tasks'])

    initial_learning_rate = _run.config['initial_learning_rate']

    optimizer = torch.optim.SGD(learner.parameters(),
                                lr=initial_learning_rate,
                                momentum=0.9,
                                nesterov=True,
                                weight_decay=1e4)
    lr_lambda = lambda x: (1 - x / _run.config['max_iter']) ** 0.9
    lr_scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer, lr_lambda=lr_lambda)

    for epoch in range(_run.config['max_iter']):  # loop over the dataset multiple times

        # polynomial learning rate decay
        lr_scheduler.step()
        # print(f'Learning rate: {lr_scheduler.get_lr()}')

        num_training_batches = 0

        running_loss = 0.0
        training_semantic_loss = 0
        training_instance_loss = 0
        training_depth_loss = 0

        # training loop
        for i, data in enumerate(train_loader, 0):
            inputs, semantic_labels, instance_centroid, instance_mask = data

            # keep count of number of batches
            num_training_batches += 1

            inputs = inputs.to(device)
            semantic_labels = semantic_labels.to(device)
            instance_centroid = instance_centroid.to(device)
            instance_mask = instance_mask.to(device)

            # zero the parameter gradients
            optimizer.zero_grad()

            # forward + backward + optimize
            output_semantic, output_instance, output_depth = learner(inputs)
            loss, task_loss = criterion((output_semantic, output_instance, output_depth),
                                        semantic_labels, instance_centroid, instance_mask)
            loss.backward()
            optimizer.step()

            # print statistics
            running_loss += loss.item()
            # if i % 2000 == 1999:    # print every 2000 mini-batches
            print('[%d, %5d] Training loss: %.3f' %
                  (epoch + 1, i + 1, running_loss))
            running_loss = 0.0

            training_semantic_loss += task_loss[0].item()
            training_instance_loss += task_loss[1].item()
            # may have to add item()
            training_depth_loss += task_loss[2]

        # save statistics to Sacred
        _run.log_scalar('training_semantic_loss',
                        training_semantic_loss / num_training_batches,
                        epoch)
        # print('training_semantic_loss', training_semantic_loss / num_training_batches, epoch)
        _run.log_scalar('training_instance_loss',
                        training_instance_loss / num_training_batches,
                        epoch)
        # print('training_instance_loss', training_instance_loss / num_training_batches, epoch)
        _run.log_scalar('training_depth_loss', training_depth_loss / num_training_batches, epoch)
        # print('training_depth_loss', training_depth_loss / num_training_batches, epoch)

        if _run.config['enable_validation']:
            _validate(
                _run=_run,
                device=device,
                validation_loader=validation_loader,
                learner=learner,
                criterion=criterion,
                epoch=epoch
            )

        if _run.config['model_save_epochs'] != 0 and epoch % _run.config['model_save_epochs'] == 0:
            _save_model(_run, learner, epoch)


def _validate(_run, device, validation_loader, learner, criterion, epoch):
    val_semantic_loss = 0
    val_instance_loss = 0
    val_depth_loss = 0
    val_iou = 0

    num_val_batches = 0

    # validation loop
    with torch.no_grad():  # exclude gradients
        for i, data in enumerate(validation_loader, 0):
            inputs, semantic_labels, instance_centroid, instance_mask = data

            inputs = inputs.to(device)
            semantic_labels = semantic_labels.to(device)
            instance_centroid = instance_centroid.to(device)
            instance_mask = instance_mask.to(device)

            # keep count of number of batches
            num_val_batches += 1

            # forward + backward + optimize
            output_semantic, output_instance, output_depth = learner(inputs.float())
            val_loss, val_task_loss = criterion(
                (output_semantic, output_instance, output_depth),
                semantic_labels.long(), instance_centroid, instance_mask)

            # calculate accuracy measures

            # segmentation IoU
            batch_iou = 0

            # TODO: this batch size might break
            batch_size = semantic_labels.shape[0]
            for image_index in range(batch_size):
                batch_iou += _compute_image_iou(
                    semantic_labels[image_index],
                    output_semantic[image_index],
                    _run.config['num_classes'])

            # instance mean error
            instance_error = val_task_loss[1].item()

            # inverse depth mean error
            depth_error = val_task_loss[2]

            # print('Batch iou %', batch_iou * 100)
            # print('Batch instance_error', instance_error)
            # print('Batch depth_error', depth_error)

            # Print every 2000 mini-batches
            # if i % 2000 == 1999:
            print('[%d, %5d] Validation loss: %.3f' %
                  (epoch + 1, i + 1, val_loss.item()))

            val_semantic_loss += val_task_loss[0].item()
            val_instance_loss += val_task_loss[1].item()
            # may have to add item()
            val_depth_loss += val_task_loss[2]
            val_iou += batch_iou / batch_size

    # save statistics to Sacred
    _run.log_scalar('val_semantic_loss', val_semantic_loss / num_val_batches, epoch)
    # _run.run_logger.debug('val_semantic_loss', val_semantic_loss / num_val_batches)
    _run.log_scalar('val_instance_loss', val_instance_loss / num_val_batches, epoch)
    # _run.run_logger.debug('val_instance_loss', val_instance_loss / num_val_batches, epoch)
    _run.log_scalar('val_depth_loss', val_depth_loss / num_val_batches, epoch)
    # _run.run_logger.debug('val_depth_loss', val_depth_loss / num_val_batches, epoch)

    _run.log_scalar('val_iou', val_iou / num_val_batches, epoch)
    # _run.run_logger.debug('val_iou', val_iou / num_val_batches, epoch)

    if _run.config['loss_type'] == 'learned':
        _run.log_scalar('weight_semantic_loss', learner.get_loss_params()[0].item(), epoch)
        print('Weight: semantic loss', learner.get_loss_params()[0].item(), epoch)
        _run.log_scalar('weight_instance_loss', learner.get_loss_params()[1].item(), epoch)
        print('Weight: instance loss', learner.get_loss_params()[1].item(), epoch)
        _run.log_scalar('weight_depth_loss', learner.get_loss_params()[2].item(), epoch)
        print('Weight: depth loss', learner.get_loss_params()[2].item(), epoch)


def _compute_image_iou(truth, output_softmax, num_classes: int):
    # Convert the softmax to the id of the class.
    output_classes = torch.argmax(output_softmax, dim=0)

    iou = 0.0
    for c in range(num_classes):
        # Create tensors with 1 for every pixel labelled with this class, and 0 otherwise. We then
        # add these tensors. The result has 2 for the intersection, and 1 or 2 for the union.

        truth_for_class = torch.where(
            truth == c,
            torch.ones_like(truth, dtype=torch.int),
            torch.zeros_like(truth, dtype=torch.int))

        output_for_class = torch.where(
            output_classes == c,
            torch.ones_like(output_classes, dtype=torch.int),
            torch.zeros_like(output_classes, dtype=torch.int))

        result = truth_for_class + output_for_class
        # View in 1D as bincount only supports 1D.
        # We expect values 0, 1, 2 for no object, one object and both objects respectively.
        counts = torch.bincount(result.view(-1), minlength=3)

        assert counts.size(0) == 3, f'Wrong number of bins: {counts}'

        intersection = counts[2].item()
        union = counts[1].item() + counts[2].item()

        if union > 0:
            iou += intersection / union

    return iou / num_classes


def _save_model(_run, model, epoch: int):
    with tempfile.NamedTemporaryFile() as file:
        torch.save(model.state_dict(), file.name)
        _run.add_artifact(file.name, f'model_epoch_{epoch}')
        _run.run_logger.info(f'Saved model to sacred.')
