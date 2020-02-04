import datetime
import time

import dataset
import numpy as np
import torch
import torch.backends.cudnn as cudnn
import torch.nn as nn
import utils
from torch.utils.data import DataLoader


def WGAN_trainer(opt):
    # ----------------------------------------
    #      Initialize training parameters
    # ----------------------------------------

    # cudnn benchmark accelerates the network
    if opt.cudnn_benchmark == True:
        cudnn.benchmark = True
    else:
        cudnn.benchmark = False

    # Build networks
    generator = utils.create_generator(opt)
    discriminator = utils.create_discriminator(opt)
    perceptualnet = utils.create_perceptualnet()

    # To device
    if opt.multi_gpu == True:
        generator = nn.DataParallel(generator)
        discriminator = nn.DataParallel(discriminator)
        perceptualnet = nn.DataParallel(perceptualnet)
        generator = generator.cuda()
        discriminator = discriminator.cuda()
        perceptualnet = perceptualnet.cuda()
    else:
        generator = generator.cuda()
        discriminator = discriminator.cuda()
        perceptualnet = perceptualnet.cuda()

    # Loss functions
    L1Loss = nn.L1Loss()
    # FeatureMatchingLoss = FML1Loss(opt.fm_param)

    # Optimizers
    optimizer_g = torch.optim.Adam(generator.parameters(), lr=opt.lr_g, betas=(opt.b1, opt.b2),
                                   weight_decay=opt.weight_decay)
    optimizer_d = torch.optim.Adam(discriminator.parameters(), lr=opt.lr_d, betas=(opt.b1, opt.b2),
                                   weight_decay=opt.weight_decay)

    # Learning rate decrease
    def adjust_learning_rate(lr_in, optimizer, epoch, opt):
        """Set the learning rate to the initial LR decayed by "lr_decrease_factor" every "lr_decrease_epoch" epochs"""
        lr = lr_in * (opt.lr_decrease_factor ** (epoch // opt.lr_decrease_epoch))
        for param_group in optimizer.param_groups:
            param_group['lr'] = lr

    # Save the model if pre_train == True
    def save_model(net, epoch, opt):
        """Save the model at "checkpoint_interval" and its multiple"""
        if opt.multi_gpu == True:
            if epoch % opt.checkpoint_interval == 0:
                torch.save(net.module, 'deepfillNet_epoch%d_batchsize%d.pth' % (epoch, opt.batch_size))
                print('The trained model is successfully saved at epoch %d' % (epoch))
        else:
            if epoch % opt.checkpoint_interval == 0:
                torch.save(net, 'deepfillNet_epoch%d_batchsize%d.pth' % (epoch, opt.batch_size))
                print('The trained model is successfully saved at epoch %d' % (epoch))

    # ----------------------------------------
    #       Initialize training dataset
    # ----------------------------------------

    # Define the dataset
    trainset = dataset.InpaintDataset(opt)
    print('The overall number of images equals to %d' % len(trainset))

    # Define the dataloader
    dataloader = DataLoader(trainset, batch_size=opt.batch_size, shuffle=True, num_workers=opt.num_workers,
                            pin_memory=True)

    # ----------------------------------------
    #            Training and Testing
    # ----------------------------------------

    # Initialize start time
    prev_time = time.time()

    # Training loop
    for epoch in range(opt.epochs):
        for batch_idx, (img, mask) in enumerate(dataloader):
            # Load mask (shape: [B, 1, H, W]), masked_img (shape: [B, 3, H, W]), img (shape: [B, 3, H, W]) and put it to cuda
            img = img.cuda()
            mask = mask.cuda()

            ### Train Discriminator
            optimizer_d.zero_grad()

            # Generator output
            first_out, second_out = generator(img, mask)

            # forward propagation
            first_out_wholeimg = img * (1 - mask) + first_out * mask  # in range [-1, 1]
            second_out_wholeimg = img * (1 - mask) + second_out * mask  # in range [-1, 1]

            # Fake samples
            fake_scalar = discriminator(second_out_wholeimg.detach(), mask)
            # True samples
            true_scalar = discriminator(img, mask)

            # Overall Loss and optimize
            loss_D = - torch.mean(true_scalar) + torch.mean(fake_scalar)
            loss_D.backward()

            ### Train Generator
            optimizer_g.zero_grad()

            # Mask L1 Loss
            first_MaskL1Loss = L1Loss(first_out_wholeimg, img)
            second_MaskL1Loss = L1Loss(second_out_wholeimg, img)

            # GAN Loss
            fake_scalar = discriminator(second_out_wholeimg, mask)
            GAN_Loss = - torch.mean(fake_scalar)

            # Get the deep semantic feature maps, and compute Perceptual Loss
            img = (img + 1) / 2  # in range [0, 1]
            img = utils.normalize_ImageNet_stats(img)  # in range of ImageNet
            img_featuremaps = perceptualnet(img)  # feature maps
            second_out_wholeimg = (second_out_wholeimg + 1) / 2  # in range [0, 1]
            second_out_wholeimg = utils.normalize_ImageNet_stats(second_out_wholeimg)
            second_out_wholeimg_featuremaps = perceptualnet(second_out_wholeimg)
            second_PerceptualLoss = L1Loss(second_out_wholeimg_featuremaps, img_featuremaps)

            # Compute losses
            loss = first_MaskL1Loss + second_MaskL1Loss + opt.perceptual_param * second_PerceptualLoss + opt.gan_param * GAN_Loss
            loss.backward()
            optimizer_g.step()

            # Determine approximate time left
            batches_done = epoch * len(dataloader) + batch_idx
            batches_left = opt.epochs * len(dataloader) - batches_done
            time_left = datetime.timedelta(seconds=batches_left * (time.time() - prev_time))
            prev_time = time.time()

            # Print log
            print("\r[Epoch %d/%d] [Batch %d/%d] [first Mask L1 Loss: %.5f] [second Mask L1 Loss: %.5f]" %
                  ((epoch + 1), opt.epochs, batch_idx, len(dataloader), first_MaskL1Loss.item(),
                   second_MaskL1Loss.item()))
            print("\r[D Loss: %.5f] [G Loss: %.5f] [Perceptual Loss: %.5f] time_left: %s" %
                  (loss_D.item(), GAN_Loss.item(), second_PerceptualLoss.item(), time_left))

        # Learning rate decrease
        adjust_learning_rate(opt.lr_g, optimizer_g, (epoch + 1), opt)
        adjust_learning_rate(opt.lr_d, optimizer_d, (epoch + 1), opt)

        # Save the model
        save_model(generator, (epoch + 1), opt)


def LSGAN_trainer(opt):
    # ----------------------------------------
    #      Initialize training parameters
    # ----------------------------------------

    # cudnn benchmark accelerates the network
    if opt.cudnn_benchmark == True:
        cudnn.benchmark = True
    else:
        cudnn.benchmark = False

    # Build networks
    generator = utils.create_generator(opt)
    discriminator = utils.create_discriminator(opt)
    perceptualnet = utils.create_perceptualnet()

    # To device
    if opt.multi_gpu == True:
        generator = nn.DataParallel(generator)
        discriminator = nn.DataParallel(discriminator)
        perceptualnet = nn.DataParallel(perceptualnet)
        generator = generator.cuda()
        discriminator = discriminator.cuda()
        perceptualnet = perceptualnet.cuda()
    else:
        generator = generator.cuda()
        discriminator = discriminator.cuda()
        perceptualnet = perceptualnet.cuda()

    # Loss functions
    L1Loss = nn.L1Loss()
    MSELoss = nn.MSELoss()
    # FeatureMatchingLoss = FML1Loss(opt.fm_param)

    # Optimizers
    optimizer_g = torch.optim.Adam(generator.parameters(), lr=opt.lr_g, betas=(opt.b1, opt.b2),
                                   weight_decay=opt.weight_decay)
    optimizer_d = torch.optim.Adam(generator.parameters(), lr=opt.lr_d, betas=(opt.b1, opt.b2),
                                   weight_decay=opt.weight_decay)

    # Learning rate decrease
    def adjust_learning_rate(lr_in, optimizer, epoch, opt):
        """Set the learning rate to the initial LR decayed by "lr_decrease_factor" every "lr_decrease_epoch" epochs"""
        lr = lr_in * (opt.lr_decrease_factor ** (epoch // opt.lr_decrease_epoch))
        for param_group in optimizer.param_groups:
            param_group['lr'] = lr

    # Save the model if pre_train == True
    def save_model(net, epoch, opt):
        """Save the model at "checkpoint_interval" and its multiple"""
        if opt.multi_gpu == True:
            if epoch % opt.checkpoint_interval == 0:
                torch.save(net.module, 'deepfillNet_epoch%d_batchsize%d.pth' % (epoch, opt.batch_size))
                print('The trained model is successfully saved at epoch %d' % (epoch))
        else:
            if epoch % opt.checkpoint_interval == 0:
                torch.save(net, 'deepfillNet_epoch%d_batchsize%d.pth' % (epoch, opt.batch_size))
                print('The trained model is successfully saved at epoch %d' % (epoch))

    # ----------------------------------------
    #       Initialize training dataset
    # ----------------------------------------

    # Define the dataset
    trainset = dataset.InpaintDataset(opt)
    print('The overall number of images equals to %d' % len(trainset))

    # Define the dataloader
    dataloader = DataLoader(trainset, batch_size=opt.batch_size, shuffle=True, num_workers=opt.num_workers,
                            pin_memory=True)

    # ----------------------------------------
    #            Training and Testing
    # ----------------------------------------

    # Initialize start time
    prev_time = time.time()

    # Tensor type
    Tensor = torch.cuda.FloatTensor

    # Training loop
    for epoch in range(opt.epochs):
        for batch_idx, (img, mask) in enumerate(dataloader):
            # Load mask (shape: [B, 1, H, W]), masked_img (shape: [B, 3, H, W]), img (shape: [B, 3, H, W]) and put it to cuda
            img = img.cuda()
            mask = mask.cuda()

            # LSGAN vectors
            valid = Tensor(np.ones((img.shape[0], 1, 8, 8)))
            fake = Tensor(np.zeros((img.shape[0], 1, 8, 8)))

            ### Train Discriminator
            optimizer_d.zero_grad()

            # Generator output
            first_out, second_out = generator(img, mask)

            # forward propagation
            first_out_wholeimg = img * (1 - mask) + first_out * mask  # in range [-1, 1]
            second_out_wholeimg = img * (1 - mask) + second_out * mask  # in range [-1, 1]

            # Fake samples
            fake_scalar = discriminator(second_out_wholeimg.detach(), mask)
            # True samples
            true_scalar = discriminator(img, mask)

            # Overall Loss and optimize
            loss_fake = MSELoss(fake_scalar, fake)
            loss_true = MSELoss(true_scalar, valid)
            # Overall Loss and optimize
            loss_D = 0.5 * (loss_fake + loss_true)
            loss_D.backward()

            ### Train Generator
            optimizer_g.zero_grad()

            # Mask L1 Loss
            first_MaskL1Loss = L1Loss(first_out_wholeimg, img)
            second_MaskL1Loss = L1Loss(second_out_wholeimg, img)

            # GAN Loss
            fake_scalar = discriminator(second_out_wholeimg, mask)
            GAN_Loss = MSELoss(fake_scalar, valid)

            # Get the deep semantic feature maps, and compute Perceptual Loss
            img = (img + 1) / 2  # in range [0, 1]
            img = utils.normalize_ImageNet_stats(img)  # in range of ImageNet
            img_featuremaps = perceptualnet(img)  # feature maps
            second_out_wholeimg = (second_out_wholeimg + 1) / 2  # in range [0, 1]
            second_out_wholeimg = utils.normalize_ImageNet_stats(second_out_wholeimg)
            second_out_wholeimg_featuremaps = perceptualnet(second_out_wholeimg)
            second_PerceptualLoss = L1Loss(second_out_wholeimg_featuremaps, img_featuremaps)

            # Compute losses
            loss = first_MaskL1Loss + second_MaskL1Loss + opt.perceptual_param * second_PerceptualLoss + opt.gan_param * GAN_Loss
            loss.backward()
            optimizer_g.step()

            # Determine approximate time left
            batches_done = epoch * len(dataloader) + batch_idx
            batches_left = opt.epochs * len(dataloader) - batches_done
            time_left = datetime.timedelta(seconds=batches_left * (time.time() - prev_time))
            prev_time = time.time()

            # Print log
            print("\r[Epoch %d/%d] [Batch %d/%d] [first Mask L1 Loss: %.5f] [second Mask L1 Loss: %.5f]" %
                  ((epoch + 1), opt.epochs, batch_idx, len(dataloader), first_MaskL1Loss.item(),
                   second_MaskL1Loss.item()))
            print("\r[D Loss: %.5f] [G Loss: %.5f] [Perceptual Loss: %.5f] time_left: %s" %
                  (loss_D.item(), GAN_Loss.item(), second_PerceptualLoss.item(), time_left))

        # Learning rate decrease
        adjust_learning_rate(opt.lr_g, optimizer_g, (epoch + 1), opt)
        adjust_learning_rate(opt.lr_d, optimizer_d, (epoch + 1), opt)

        # Save the model
        save_model(generator, (epoch + 1), opt)
