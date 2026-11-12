"""Record each ready worker, including workers receiving no low-load requests."""
def post_worker_init(worker):
    from request_kernel import record_startup
    record_startup('http')
