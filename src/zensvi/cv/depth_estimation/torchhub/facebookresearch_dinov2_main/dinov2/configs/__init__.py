# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the license found in the
# LICENSE file in the root directory of this source tree.

import pathlib

from omegaconf import OmegaConf


def load_config(config_name: str):
    """

    Args:
      config_name: str:
      config_name: str:

    Returns:

    """
    config_filename = config_name + ".yaml"
    return OmegaConf.load(pathlib.Path(__file__).parent.resolve() / config_filename)


dinov2_default_config = load_config("ssl_default_config")


def load_and_merge_config(config_name: str):
    """

    Args:
      config_name: str:
      config_name: str:

    Returns:

    """
    default_config = OmegaConf.create(dinov2_default_config)
    loaded_config = load_config(config_name)
    return OmegaConf.merge(default_config, loaded_config)
