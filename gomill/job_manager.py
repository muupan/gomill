"""Job system supporting multiprocessing."""

import sys

from gomill import compact_tracebacks

multiprocessing = None

NoJobAvailable = object()

class JobFailed(StandardError):
    """Error reported by a job."""

class JobSourceError(StandardError):
    """Error from a job source object."""

class JobError(object):
    """Error from a job."""
    def __init__(self, job, msg):
        self.job = job
        self.msg = msg

def _initialise_multiprocessing():
    global multiprocessing
    if multiprocessing is not None:
        return
    try:
        import multiprocessing
    except ImportError:
        multiprocessing = None

class Worker_finish_signal(object):
    pass
worker_finish_signal = Worker_finish_signal()

def worker_run_jobs(job_queue, response_queue):
    try:
        #pid = os.getpid()
        #sys.stderr.write("worker %d starting\n" % pid)
        while True:
            job = job_queue.get()
            #sys.stderr.write("worker %d: %s\n" % (pid, repr(job)))
            if isinstance(job, Worker_finish_signal):
                break
            try:
                response = job.run()
            except JobFailed, e:
                response = JobError(job, str(e))
            except StandardError, e:
                response = JobError(
                    job, compact_tracebacks.format_traceback(skip=1))
            response_queue.put(response)
        #sys.stderr.write("worker %d finishing\n" % pid)
        response_queue.cancel_join_thread()
    # Unfortunately, there will be places in the child that this doesn't cover.
    # But it will avoid the ugly traceback in most cases.
    except KeyboardInterrupt:
        sys.exit(3)

class Multiprocessing_job_manager(object):
    def __init__(self, number_of_workers):
        _initialise_multiprocessing()
        if multiprocessing is None:
            raise StandardError("multiprocessing not available")
        if not 1 <= number_of_workers < 1024:
            raise ValueError
        self.number_of_workers = number_of_workers

    def start_workers(self):
        self.job_queue = multiprocessing.Queue()
        self.response_queue = multiprocessing.Queue()
        self.workers = []
        for i in range(self.number_of_workers):
            worker = multiprocessing.Process(
                target=worker_run_jobs,
                args=(self.job_queue, self.response_queue))
            self.workers.append(worker)
        for worker in self.workers:
            worker.start()

    def run_jobs(self, job_source):
        active_jobs = 0
        while True:
            if active_jobs < self.number_of_workers:
                try:
                    job = job_source.get_job()
                except StandardError:
                    raise JobSourceError(
                        "error from get_job()\n%s" %
                        compact_tracebacks.format_traceback(skip=1))
                if job is not NoJobAvailable:
                    #sys.stderr.write("MGR: sending %s\n" % repr(job))
                    self.job_queue.put(job)
                    active_jobs += 1
                    continue
            if active_jobs == 0:
                break

            response = self.response_queue.get()
            if isinstance(response, JobError):
                job_source.process_error_response(
                    response.job, response.msg)
            else:
                try:
                    job_source.process_response(response)
                except StandardError:
                    raise JobSourceError(
                        "error from process_response()\n%s" %
                        compact_tracebacks.format_traceback(skip=1))
            active_jobs -= 1
            #sys.stderr.write("MGR: received response %s\n" % repr(response))

    def finish(self):
        for _ in range(self.number_of_workers):
            self.job_queue.put(worker_finish_signal)
        for worker in self.workers:
            worker.join()
        self.job_queue = None
        self.response_queue = None

class In_process_job_manager(object):
    def start_workers(self):
        pass

    def run_jobs(self, job_source):
        while True:
            try:
                job = job_source.get_job()
            except StandardError:
                raise JobSourceError(
                    "error from get_job()\n%s" %
                    compact_tracebacks.format_traceback(skip=1))
            if job is NoJobAvailable:
                break
            try:
                response = job.run()
            except StandardError, e:
                job_source.process_error_response(
                    job, compact_tracebacks.format_traceback(skip=1))
            else:
                try:
                    job_source.process_response(response)
                except StandardError:
                    raise JobSourceError(
                        "error from process_response()\n%s" %
                        compact_tracebacks.format_traceback(skip=1))

    def finish(self):
        pass

def run_jobs(job_source, max_workers=None, allow_mp=True):
    if allow_mp:
        _initialise_multiprocessing()
        if multiprocessing is None:
            allow_mp = False
    if allow_mp:
        if max_workers is None:
            max_workers = multiprocessing.cpu_count()
        job_manager = Multiprocessing_job_manager(max_workers)
    else:
        job_manager = In_process_job_manager()
    job_manager.start_workers()
    try:
        job_manager.run_jobs(job_source)
    except StandardError, e:
        try:
            job_manager.finish()
        except StandardError, e2:
            print >>sys.stderr, "Error closing down workers:\n%s" % e2
        raise
    job_manager.finish()

