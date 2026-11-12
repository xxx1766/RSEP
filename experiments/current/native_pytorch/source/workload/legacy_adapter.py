"""Execute the user's unchanged calculation functions; Tide SDK is not exercised."""
from pathlib import Path
import hashlib
import importlib.util
import json
import os
import sys
import threading
import time
import types

ROOT=Path(os.environ.get('LEGACY_ROOT','/evidence'))


class RegistrationOnly:
    @staticmethod
    def fn():return lambda function:function
    @staticmethod
    def main():return lambda function:function
    @staticmethod
    def init():return lambda function:function


class LegacyKernel:
    def __init__(self,threads=1):
        if threads not in [1,2,4]:raise ValueError('threads must be one of 1,2,4')
        self.threads=threads
        os.chdir(ROOT/'workdir')
        sys.path.insert(0,str(ROOT/'source/YOLOX-0.3.0'))
        shim=types.ModuleType('tide');shim.tide=RegistrationOnly();sys.modules['tide']=shim
        spec=importlib.util.spec_from_file_location('user_yolox',ROOT/'source/workload/user_program.py')
        self.module=importlib.util.module_from_spec(spec)
        started=time.perf_counter_ns();spec.loader.exec_module(self.module)
        self.module.init();self.init_ms=(time.perf_counter_ns()-started)/1e6
        self.module.torch.set_num_threads(threads)
        self.original_image=self.module.cv2.imread(str(ROOT/'workdir/assets/dog.jpg'))
        assert self.original_image is not None
        self.context=threading.local();self.context.trace=None
        self.module.predictor.model.register_forward_pre_hook(self.before_forward)
        self.module.predictor.model.register_forward_hook(self.after_forward)
        self.postprocess=self.module.postprocess;self.visualize=self.module.vis
        self.module.postprocess=self.postprocess_hook;self.module.vis=self.visualize_hook

    def before_forward(self,module,args):
        self.context.trace['forward_start']=time.perf_counter_ns()

    def after_forward(self,module,args,result):
        trace=self.context.trace;trace['forward_calls']+=1
        trace['forward_ms']+=(time.perf_counter_ns()-trace.pop('forward_start'))/1e6

    def postprocess_hook(self,*args,**kwargs):
        started=time.perf_counter_ns();result=self.postprocess(*args,**kwargs)
        trace=self.context.trace;trace['postprocess_ms']+=(time.perf_counter_ns()-started)/1e6
        trace['postprocess_calls']+=1;trace['outputs']=result
        return result

    def visualize_hook(self,*args,**kwargs):
        started=time.perf_counter_ns();result=self.visualize(*args,**kwargs)
        trace=self.context.trace;trace['draw_ms']+=(time.perf_counter_ns()-started)/1e6
        trace['draw_calls']+=1;return result

    def identity(self):
        import torch,torchvision,cv2,numpy
        return dict(pid=os.getpid(),backend='PyTorch CPU',torch=torch.__version__,torchvision=torchvision.__version__,
            opencv=cv2.__version__,numpy=numpy.__version__,torch_intraop=torch.get_num_threads(),
            torch_interop=torch.get_num_interop_threads(),opencv_threads=cv2.getNumThreads(),
            initialization_ms=self.init_ms,model='yolox-nano',input_size=list(self.module.predictor.test_size),
            checkpoint_sha256=hashlib.sha256((ROOT/'downloads/yolox_nano.pth').read_bytes()).hexdigest(),
            user_program_sha256=hashlib.sha256((ROOT/'source/workload/user_program.py').read_bytes()).hexdigest(),
            affinity=sorted(os.sched_getaffinity(0)),tide_sdk='registration-only shim; computation diagnostic, not native Tide')

    def run_request(self,request_id):
        image=self.original_image.copy()
        trace=dict(forward_calls=0,postprocess_calls=0,draw_calls=0,forward_ms=0.,postprocess_ms=0.,draw_ms=0.)
        self.context.trace=trace;cpu_start=time.process_time_ns();start=time.monotonic_ns()
        result=self.module.proce_img(image)
        finish=time.monotonic_ns();cpu_finish=time.process_time_ns()
        detections=trace.pop('outputs')[0]
        count=0 if detections is None else int(detections.shape[0])
        classes=[] if detections is None else [int(v) for v in detections[:,6].tolist()]
        assert trace['forward_calls']==trace['postprocess_calls']==1 and count>0 and trace['draw_calls']==1
        assert self.module.torch.get_num_threads()==self.threads
        assert result.dtype.name=='uint8' and result.shape==self.original_image.shape
        event=dict(request_id=request_id,pid=os.getpid(),tid=threading.get_native_id(),success=True,
            execution_ms=(finish-start)/1e6,process_cpu_ms=(cpu_finish-cpu_start)/1e6,
            execution_start_ns=start,execution_finish_ns=finish,**trace,detections=count,classes=classes,
            class_names=[self.module.COCO_CLASSES[i] for i in classes],
            output_shape=list(result.shape),output_sha256=hashlib.sha256(result.tobytes()).hexdigest(),
            torch_intraop_threads=self.threads,test_size=list(self.module.predictor.test_size),
            affinity=sorted(os.sched_getaffinity(0)))
        return result,event
