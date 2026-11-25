from collections import Counter
import threading
import unittest

from dsh_retrieval.models import Actor
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.search_scheduling import FairSearchQueue, SearchLimits, SearchRetry


class FairSearchTest(unittest.TestCase):
    def test_fifo_users_rotate_and_long_task_yields(self):
        a, b = Actor('t', 'a'), Actor('t', 'b')
        order, done = [], threading.Event()
        def callback(actor, task):
            order.append(task)
            if len(order) == 4:
                done.set()
            return task == 'a1' and order.count(task) == 1
        queue = FairSearchQueue(callback, SearchLimits(workers=1), start=False)
        for actor, task in ((a, 'a1'), (a, 'a2'), (b, 'b1'), (a, 'a1')):
            queue.enqueue(actor, task)
        queue.workers[0].start()
        try:
            self.assertTrue(done.wait(3))
        finally:
            queue.close()
        self.assertEqual(order, ['a1', 'b1', 'a2', 'a1'])

    def test_parallel_users_bound_and_active_duplicate_is_single_followup(self):
        a, b = Actor('t', 'a'), Actor('t', 'b')
        entered, release, finished = threading.Barrier(3), threading.Event(), threading.Event()
        calls = Counter()
        def callback(actor, task):
            calls[task] += 1
            if task in ('a1', 'b1') and calls[task] == 1:
                entered.wait(3)
                release.wait(3)
            if sum(calls.values()) >= 4:
                finished.set()
            return False
        queue = FairSearchQueue(callback, SearchLimits(workers=2), start=False)
        for actor, task in ((a, 'a1'), (a, 'a2'), (b, 'b1')):
            queue.enqueue(actor, task)
        for worker in queue.workers:
            worker.start()
        try:
            entered.wait(3)
            self.assertEqual(calls, {'a1': 1, 'b1': 1})
            queue.enqueue(a, 'a1');queue.enqueue(a, 'a1')
            release.set()
            self.assertTrue(finished.wait(3))
        finally:
            release.set();queue.close()
        self.assertEqual(calls, {'a1': 2, 'a2': 1, 'b1': 1})

    def test_backpressure_shutdown_and_cooldown_does_not_block_other_user(self):
        a, b = Actor('t', 'a'), Actor('t', 'b')
        done, order = threading.Event(), []
        def callback(actor, task):
            order.append(task)
            if len(order) == 3:
                done.set()
            return SearchRetry(.03) if task == 'a' and len(order) == 1 else False
        queue = FairSearchQueue(callback, SearchLimits(workers=1, max_pending=2), start=False)
        queue.enqueue(a, 'a');queue.enqueue(b, 'b')
        with self.assertRaises(ServiceError) as caught:
            queue.enqueue(a, 'overflow')
        self.assertEqual(caught.exception.code, 'SEARCH_QUEUE_FULL')
        queue.workers[0].start()
        try:
            self.assertTrue(done.wait(3))
        finally:
            queue.close()
        self.assertEqual(order, ['a', 'b', 'a'])
        with self.assertRaises(ServiceError):
            queue.enqueue(a, 'closed')

    def test_invalid_configuration(self):
        for values in ({'workers': True}, {'workers': 17}, {'max_calls': 0}, {'max_hit_slots': 0}, {'max_pending': 1}):
            with self.assertRaises(ValueError):
                SearchLimits(**values)

    def test_recovery_waits_for_capacity_and_does_not_drop_approved_work(self):
        a, b = Actor('t', 'a'), Actor('t', 'b')
        started, release, recovered, done = (threading.Event() for _ in range(4))
        order = []
        def callback(actor, task):
            order.append(task)
            if task == 'active':
                started.set();release.wait(3)
            else:
                done.set()
            return False
        queue = FairSearchQueue(callback, SearchLimits(workers=1, max_pending=1))
        queue.enqueue(a, 'active')
        def producer():
            queue.enqueue(b, 'recovered', wait=True)
            recovered.set()
        producer_thread = threading.Thread(target=producer)
        try:
            self.assertTrue(started.wait(3))
            producer_thread.start()
            self.assertFalse(recovered.wait(.02))
            release.set()
            self.assertTrue(recovered.wait(3));self.assertTrue(done.wait(3))
        finally:
            release.set();queue.close()
            if producer_thread.ident is not None:
                producer_thread.join()
        self.assertEqual(order, ['active', 'recovered'])

    def test_failure_is_visible_bounded_and_does_not_stop_other_users(self):
        a, b = Actor('t', 'a'), Actor('t', 'b')
        done = threading.Event()
        def callback(actor, task):
            if task == 'bad':
                raise ServiceError('ACCESS_DENIED', 'private credential detail', 403)
            done.set()
            return False
        queue = FairSearchQueue(callback, SearchLimits(workers=1), start=False)
        queue.enqueue(a, 'bad');queue.enqueue(b, 'good');queue.workers[0].start()
        try:
            self.assertTrue(done.wait(3))
        finally:
            queue.close()
        self.assertEqual(queue.status(a, 'bad')['state'], 'failed')
        self.assertEqual(queue.status(a, 'bad')['error_code'], 'ACCESS_DENIED')
        self.assertNotIn('private', str(queue.status(a, 'bad')))

    def test_one_user_cannot_fill_all_admission_slots_and_recovery_interleaves_users(self):
        a, b = Actor('t', 'a'), Actor('t', 'b')
        queue = FairSearchQueue(lambda *_: False, SearchLimits(workers=1, max_pending=4, max_pending_per_user=2), start=False)
        queue.enqueue(a, 'a1');queue.enqueue(a, 'a2')
        with self.assertRaises(ServiceError) as caught:
            queue.enqueue(a, 'a3')
        self.assertEqual(caught.exception.code, 'SEARCH_USER_QUEUE_FULL')
        queue.enqueue(b, 'b1')
        self.assertEqual(queue.status(b, 'b1')['state'], 'queued')
        queue.close()
        recovered = []
        other = FairSearchQueue(lambda *_: False, SearchLimits(workers=1), start=False)
        other.enqueue = lambda actor, item, **_: recovered.append((actor, item))
        other.recover(((a, 'a1'), (a, 'a2'), (b, 'b1'), (b, 'b2')))
        self.assertEqual(recovered, [(a, 'a1'), (b, 'b1'), (a, 'a2'), (b, 'b2')])
        other.close()
