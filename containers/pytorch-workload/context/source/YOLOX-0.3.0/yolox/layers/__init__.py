#!/usr/bin/env python3
# -*- coding:utf-8 -*-
# Copyright (c) Megvii Inc. All rights reserved.


import torch  # noqa

from .jit_ops import FastCOCOEvalOp, JitOp

try:
    from .fast_coco_eval_api import COCOeval_opt
except ImportError:  
    pass
