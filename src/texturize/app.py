# neural-texturize — Copyright (c) 2020, Novelty Factory KG.  See LICENSE for details.

import os

import torch
import torch.nn.functional as F

from creativeai.image.encoders import models

from .critics import GramMatrixCritic, PatchCritic
from .solvers import SolverLBFGS, MultiCriticObjective
from .io import *


class TextureSynthesizer:
    def __init__(self, device, encoder, lr, precision, max_iter):
        self.device = device
        self.encoder = encoder
        self.lr = lr
        self.precision = precision
        self.max_iter = max_iter

    def prepare(self, critics, image):
        """Extract the features from the source texture and initialize the critics.
        """
        feats = dict(self.encoder.extract(image, [c.get_layers() for c in critics]))
        for critic in critics:
            critic.from_features(feats)

    def run(self, log, seed_img, critics):
        """Run the optimizer on the image according to the loss returned by the critics.
        """
        image = seed_img.to(self.device).requires_grad_(True)

        obj = MultiCriticObjective(self.encoder, critics)
        opt = SolverLBFGS(obj, image, lr=self.lr)

        progress = log.create_progress_bar(self.max_iter)

        try:
            for i, loss in self._iterate(opt):
                # Update the progress bar with the result!
                progress.update(i, loss=loss)
                # Constrain the image to the valid color range.
                image.data.clamp_(0.0, 1.0)
                # Return back to the user...
                yield loss, image

            progress.max_value = i + 1
        finally:
            progress.finish()

    def _iterate(self, opt):
        previous = None
        for i in range(self.max_iter):
            # Perform one step of the optimization.
            loss = opt.step()

            # Return this iteration to the caller...
            yield i, loss

            # See if we can terminate the optimization early.
            if i > 1 and abs(loss - previous) < self.precision:
                assert i > 10, f"Optimization stalled at iteration {i}."
                break

            previous = loss
