import os
from loguru import logger
import time
import sys
import json
from typing import Dict, Any
import cv2
import torch

import threading
from queue import Queue

cpu_num = 1
os.environ['OMP_NUM_THREADS'] = str(cpu_num)
os.environ['OPENBLAS_NUM_THREADS'] = str(cpu_num)
os.environ['MKL_NUM_THREADS'] = str(cpu_num)
os.environ['VECLIB_MAXIMUM_THREADS'] = str(cpu_num)
os.environ['NUMEXPR_NUM_THREADS'] = str(cpu_num)
torch.set_num_threads(cpu_num)

def sink_serializer(msg):
    record = msg.record
    ns = record['extra'].pop('ns') if 'ns' in record['extra'] else None
    obj = {
        'time': ns if ns is not None else int(record['time'].timestamp()*1e9),
        'msg': record['message'],
        'level': record['level'].name,
    }
    obj.update(record['extra'])
    s = json.dumps(obj)
    print(s, file=sys.stderr)
logger.remove()
logger.add(sink_serializer, level='DEBUG')
logger = logger.opt(lazy=True)

from yolox.data.data_augment import ValTransform
from yolox.data.datasets import COCO_CLASSES
from yolox.exp import get_exp
from yolox.utils import fuse_model, get_model_info, postprocess, vis

from tide import tide

predictor = None


class Predictor(object):
    def __init__(
        self,
        model,
        exp,
        cls_names=COCO_CLASSES,
        device="cpu",
        fp16=False,
        legacy=False,
    ):
        self.model = model
        self.cls_names = cls_names
        self.num_classes = exp.num_classes
        self.confthre = exp.test_conf
        self.nmsthre = exp.nmsthre
        self.test_size = exp.test_size
        self.device = device
        self.fp16 = fp16
        self.preproc = ValTransform(legacy=legacy)

    def inference(self, img):
        img_info: Dict[str, Any]  = {"id": 0}
        img_info["file_name"] = None

        height, width = img.shape[:2]
        img_info["height"] = height
        img_info["width"] = width
        img_info["raw_img"] = img

        ratio = min(self.test_size[0] / img.shape[0], self.test_size[1] / img.shape[1])
        img_info["ratio"] = ratio

        img, _ = self.preproc(img, None, self.test_size)
        img = torch.from_numpy(img).unsqueeze(0)
        img = img.float()
        if self.device == "gpu":
            img = img.cuda()
            if self.fp16:
                img = img.half()

        with torch.no_grad():
            t0 = time.time()
            outputs = self.model(img)
            outputs = postprocess(
                outputs, self.num_classes, self.confthre,
                self.nmsthre, class_agnostic=True
            )
        return outputs, img_info

    def visual(self, output, img_info, cls_conf=0.35):
        ratio = img_info["ratio"]
        img = img_info["raw_img"]
        if output is None:
            return img
        output = output.cpu()

        bboxes = output[:, 0:4]

        bboxes /= ratio

        cls = output[:, 6]
        scores = output[:, 4] * output[:, 5]

        vis_res = vis(img, bboxes, scores, cls, cls_conf, self.cls_names)
        return vis_res


@tide.fn()
def proce_img(img):
    global predictor
    assert predictor is not None
    outputs, img_info = predictor.inference(img)
    result_image = predictor.visual(outputs[0], img_info, predictor.confthre)
    return result_image


_sentinel = object()
FPS = 20

def wait_thread(que: Queue):
    while True:
        rtn = que.get()
        if rtn is _sentinel:
            que.put(rtn)
            break

        t0 = time.time_ns()
        tide.wait(rtn)
        t1 = time.time_ns()
        logger.bind(RoutineId=rtn.id, ns=t0).debug('TideCtr: start waitting')
        logger.bind(RoutineId=rtn.id, ns=t1).debug('TideCtr: finish waitting')


@tide.main()
def main():
    que = Queue()
    thr = threading.Thread(target=wait_thread, args=(que,))
    thr.start()

    img_path = './assets/dog.jpg'
    img = cv2.imread(img_path)
    rtn = tide.offload(proce_img, img)
    tide.wait(rtn)

    for _ in range(1000):
        time.sleep(1./FPS)
        t0 = time.time_ns()
        rtn = tide.offload(proce_img, img)
        que.put(rtn)
        t1 = time.time_ns()

        logger.bind(RoutineId=rtn.id, ns=t0).debug('TideCtr: start offloading')
        logger.bind(RoutineId=rtn.id, ns=t1).debug('TideCtr: finish offloading')
    que.put(_sentinel)
    thr.join()


@tide.init()
def init():
    args = {
        'exp_file': None,
        'name': 'yolox-nano',
        'conf': 0.25,
        'nms': 0.45,
        'tsize': 640,
        'ckpt': './models/yolox_nano.pth',
        'device': 'cpu',
    }
    exp = get_exp(args['exp_file'], args['name'])
    exp.test_conf = args['conf']
    exp.nmsthre = args['nms']
    exp.test_size = (args['tsize'], args['tsize'])

    model = exp.get_model()
    model.eval()

    ckpt_file = args['ckpt']
    ckpt = torch.load(ckpt_file, map_location="cpu")
    model.load_state_dict(ckpt["model"])

    global predictor
    predictor = Predictor(model, exp, COCO_CLASSES, args['device'])
    logger.info("TideCtr: stop initializing")


if __name__ == "__main__":
    logger.info("TideCtr: start the application")
    tide.run()
