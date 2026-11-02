import unittest

from dsh_retrieval.errors import ServiceError
from dsh_retrieval.refill_auto import AutoRefillCoordinator


class Events:
    def __init__(self):
        self.pending = [{"event_id": "auto-refill:one", "tenant_id": "tenant",
                         "owner_user_id": "owner", "task_id": "task-one",
                         "revision": 1, "generation": 1, "task_version": 4,
                         "result_set_id": "result-one"}]
        self.delivered = []

    def pending_auto_refill_events(self):
        return tuple(self.pending)

    def mark_auto_refill_delivered(self, event_id, result):
        self.delivered.append((event_id, result))
        self.pending = [item for item in self.pending if item["event_id"] != event_id]
        return result


class Service:
    def __init__(self):
        self.calls = []
        self.error = None

    def advance_auto_event(self, actor, event):
        self.calls.append((actor, event))
        if self.error:
            raise self.error
        return {"status": "committed", "reservation_id": "refill-one"}


class AutoRefillCoordinatorTest(unittest.TestCase):
    def test_committed_result_event_is_delivered_once(self):
        events, service = Events(), Service()
        coordinator = AutoRefillCoordinator(events, service, start=False)
        self.addCleanup(coordinator.close)
        coordinator.drain()
        coordinator.drain()
        self.assertEqual(len(service.calls), 1)
        self.assertEqual(events.delivered[0][0], "auto-refill:one")
        self.assertEqual(events.delivered[0][1]["status"], "committed")

    def test_retryable_failure_keeps_durable_event_pending(self):
        events, service = Events(), Service()
        service.error = ServiceError("DEPENDENCY_UNAVAILABLE", "temporary", 503,
                                     retryable=True)
        coordinator = AutoRefillCoordinator(events, service, start=False)
        self.addCleanup(coordinator.close)
        with self.assertRaises(ServiceError):
            coordinator.drain()
        self.assertEqual(len(events.pending), 1)
        self.assertEqual(events.delivered, [])


if __name__ == "__main__":
    unittest.main()
