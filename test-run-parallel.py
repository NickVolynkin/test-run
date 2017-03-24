#!/usr/bin/env python2

import re
import sys
import time
import select
import multiprocessing
from multiprocessing.queues import SimpleQueue
import copy


import lib
from lib.colorer import Colorer


color_stdout = Colorer()


class TaskResultListener(object):
    def process_result(self):
        raise ValueError('override me')


class TaskStatistics(TaskResultListener):
    def __init__(self):
        self.stats = dict()

    def process_result(self, worker_name, obj):
        if not isinstance(obj, bool):
            return

        if not worker_name in self.stats.keys():
            self.stats[worker_name] = {
                'pass': 0,
                'othr': 0,
            }
        if obj:
            self.stats[worker_name]['pass'] += 1
        else:
            self.stats[worker_name]['othr'] += 1

    def print_statistics(self):
        color_stdout('Statistics: %s\n' % str(self.stats), schema='test_var')


class TaskOutput(TaskResultListener):
    color_re = re.compile('\033' + r'\[\d(?:;\d\d)?m')

    def __init__(self):
        self.buffer = dict()

    @staticmethod
    def _write(obj):
        sys.stdout.write(obj)

    @staticmethod
    def _decolor(obj):
        return TaskOutput.color_re.sub('', obj)

    def process_result(self, worker_name, obj):
        # worker sent 'done' marker
        if obj is None:
            bufferized = self.buffer.get(worker_name, '')
            if bufferized:
                TaskOutput._write(bufferized)
            return

        if not isinstance(obj, str):
            return

        bufferized = self.buffer.get(worker_name, '')
        if TaskOutput._decolor(obj).endswith('\n'):
            TaskOutput._write(bufferized + obj)
            self.buffer[worker_name] = ''
        else:
            self.buffer[worker_name] = bufferized + obj


def run_worker(gen_worker, task_queue, result_queue, worker_id):
    color_stdout.queue = result_queue
    worker = gen_worker(worker_id)
    worker.run_all(task_queue, result_queue)


def reproduce_baskets(reproduce, all_baskets):
    # check test list and find basket
    found_basket_ids = []
    if not lib.reproduce:
        raise ValueError('[reproduce] Tests list cannot be empty')
    for i, task_id in enumerate(lib.reproduce):
        for basket_id, basket in all_baskets.items():
            if task_id in basket['task_ids']:
                found_basket_ids.append(basket_id)
                break
        if len(found_basket_ids) != i + 1:
            raise ValueError('[reproduce] Cannot find test "%s"' % str(task_id))
    found_basket_ids = list(set(found_basket_ids))
    if len(found_basket_ids) < 1:
        raise ValueError('[reproduce] Cannot find any suite for given tests')
    elif len(found_basket_ids) > 1:
        raise ValueError('[reproduce] Given tests contained by different suites')

    key = found_basket_ids[0]
    basket = copy.deepcopy(all_baskets[key])
    basket['task_ids'] = lib.reproduce
    return { key: basket }


def start_workers(processes, task_queues, result_queues, baskets):
    worker_next_id = 1
    for basket in baskets.values():
        task_ids = basket['task_ids']
        if not task_ids:
            continue
        result_queue = SimpleQueue()
        result_queues.append(result_queue)
        task_queue = SimpleQueue()
        task_queues.append(task_queue)
        for task_id in task_ids:
            task_queue.put(task_id)
        task_queue.put(None)  # 'stop worker' marker
        # It's python-style closure; XXX: prettify
        entry = lambda gen_worker=basket['gen_worker'], \
                task_queue=task_queue, result_queue=result_queue, \
                worker_next_id=worker_next_id: \
            run_worker(gen_worker, task_queue, result_queue, worker_next_id)
        worker_next_id += 1

        process = multiprocessing.Process(target=entry)
        process.start()
        processes.append(process)


def wait_result_queues(processes, task_queues, result_queues):
    inputs = [q._reader for q in result_queues]
    workers_cnt = len(inputs)
    statistics = TaskStatistics()
    listeners = [statistics, TaskOutput()]
    while workers_cnt > 0:
        ready_inputs, _, _ = select.select(inputs, [], [])
        for ready_input in ready_inputs:
            result_queue = result_queues[inputs.index(ready_input)]
            objs = []
            while not result_queue.empty():
                objs.append(result_queue.get())
            for obj in objs:
                worker_name = inputs.index(ready_input) # XXX: tmp
                for listener in listeners:
                    listener.process_result(worker_name, obj)
                if obj is None:
                    workers_cnt -= 1
                    break
    return statistics


def main_loop():
    processes = []
    task_queues = []
    result_queues = []

    color_stdout("Started {0}\n".format(" ".join(sys.argv)), schema='tr_text')

    baskets = lib.task_baskets()
    if lib.reproduce:
        baskets = reproduce_baskets(lib.reproduce, baskets)
        # TODO: when several workers will able to work on one task queue we
        #       need to limit workers count to 1 when reproducing
    start_workers(processes, task_queues, result_queues, baskets)

    if not processes:
        return

    statistics = wait_result_queues(processes, task_queues, result_queues)
    statistics.print_statistics()

    for process in processes:
        process.join()
        processes.remove(process)


def main():
    try:
        main_loop()
    except KeyboardInterrupt as e:
        color_stdout('\n[Main process] Caught keyboard interrupt;' \
            ' waiting for processes for doing its clean up\n', schema='test_var')


if __name__ == "__main__":
    exit(main())
