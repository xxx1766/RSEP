#!/usr/bin/env python3
"""Direct worker calibration: same real inference, shared vs split CPU slice."""
import concurrent.futures
import json
import os
import sys
import time


def worker(args):
    slot, pin, path = args
    cpus = ({"strided": [{2, 4}, {3, 5}, {6, 7}],
             "contiguous": [{2, 3}, {4, 5}, {6, 7}]}[pin][slot]
            if pin != "shared" else set(range(2,8)))
    os.sched_setaffinity(0, cpus)
    sys.path.insert(0, "/app")
    from infer import infer_once
    import cv2
    data = open(path, "rb").read()
    for _ in range(30): infer_once(data)
    before = os.times()
    start = time.perf_counter()
    for _ in range(100): infer_once(data)
    elapsed = time.perf_counter()-start
    after = os.times()
    return {"slot": slot, "cpus": sorted(cpus), "cv2_threads": cv2.getNumThreads(),
            "seconds": elapsed, "cpu_seconds": after.user+after.system-before.user-before.system}


if __name__ == "__main__":
    for pin in ["shared", "strided", "contiguous", "contiguous", "strided", "shared"]:
        with concurrent.futures.ProcessPoolExecutor(max_workers=3) as pool:
            values=list(pool.map(worker, [(i,pin,sys.argv[1]) for i in range(3)]))
        print(json.dumps({"pin": pin, "workers": values}), flush=True)
