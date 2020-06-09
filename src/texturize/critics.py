# neural-texturize — Copyright (c) 2020, Novelty Factory KG.  See LICENSE for details.

import torch
import torch.nn.functional as F

from .patch import PatchBuilder
from .match import FeatureMatcher


class GramMatrixCritic:
    """A `Critic` evaluates the features of an image to determine how it scores.

    This critic computes a 2D histogram of feature cross-correlations for the specified
    layer (e.g. "1_1") or layer pair (e.g. "1_1:2_1"), and compares it to the target
    gram matrix.
    """

    def __init__(self, layer, offset: float = -1.0):
        self.pair = tuple(layer.split(":"))
        if len(self.pair) == 1:
            self.pair = (self.pair[0], self.pair[0])
        self.offset = offset
        self.gram = None

    def evaluate(self, features):
        current = self._prepare_gram(features)
        yield 1e4 * F.mse_loss(current, self.gram.expand_as(current), reduction="mean")

    def from_features(self, features):
        self.gram = self._prepare_gram(features)

    def get_layers(self):
        return set(self.pair)

    def _gram_matrix(self, column, row):
        (b, ch, h, w) = column.size()
        f_c = column.view(b, ch, w * h)
        (b, ch, h, w) = row.size()
        f_r = row.view(b, ch, w * h)

        gram = (f_c / w).bmm((f_r / h).transpose(1, 2)) / ch
        assert not torch.isnan(gram).any()

        return gram

    def _prepare_gram(self, features):
        lower = features[self.pair[0]] + self.offset
        upper = features[self.pair[1]] + self.offset
        return self._gram_matrix(
            lower, F.interpolate(upper, size=lower.shape[2:], mode="nearest")
        )


class PatchCritic:
    def __init__(self, layer):
        self.layer = layer
        self.patches = None
        self.builder = PatchBuilder(patch_size=2)
        self.matcher = FeatureMatcher(device="cpu")
        self.split_hints = {}

    def get_layers(self):
        return {self.layer}

    def from_features(self, features):
        self.patches = self.prepare(features).detach()
        self.matcher.update_sources(self.patches)
        self.iter = 0

    def prepare(self, features):
        f = features[self.layer]
        return self.builder.extract(f)

    def auto_split(self, function, *arguments, **keywords):
        key = (self.matcher.target.shape, function)
        for i in self.split_hints.get(key, range(16)):
            try:
                result = function(*arguments, split=2**i, **keywords)
                self.split_hints[key] = [i]
                return result
            except RuntimeError as e:
                if "CUDA out of memory." not in str(e):
                    raise

    def evaluate(self, features):
        self.iter += 1

        target = self.prepare(features)
        self.matcher.update_target(target)

        with torch.no_grad():
            if target.flatten(1).shape[1] < 1_048_576:
                self.auto_split(self.matcher.compare_features_matrix)
            else:
                self.auto_split(self.matcher.compare_features_identity)
                self.auto_split(self.matcher.compare_features_random, radius=8)
                self.auto_split(self.matcher.compare_features_nearby, radius=1)

            matched_target = self.matcher.reconstruct_target()

        yield 0.5 * F.mse_loss(target, matched_target)
        del matched_target

        matched_source = self.matcher.reconstruct_source()
        yield 0.5 * F.mse_loss(matched_source, self.patches)
        del matched_source