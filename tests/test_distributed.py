import sys
from pathlib import Path
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from vsss_coach.distributed import JobQueue


class DistributedQueueTests(unittest.TestCase):
    def test_workers_lease_only_their_capacity_and_complete_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            queue = JobQueue(Path(directory) / "queue.sqlite3")
            queue.submit([{"id": f"m{i}", "generation": 0, "payload": {"n": i}} for i in range(3)])
            leased = queue.lease("worker-a", 2, 60)
            self.assertEqual(len(leased), 2)
            self.assertTrue(queue.complete(leased[0]["id"], "worker-a", {"score": 1}))
            self.assertFalse(queue.complete(leased[0]["id"], "worker-a", {"score": 2}))
            self.assertEqual(queue.results(0)["counts"]["complete"], 1)

    def test_failed_job_can_return_to_queue(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            queue = JobQueue(Path(directory) / "queue.sqlite3")
            queue.submit([{"id": "m0", "generation": 0, "payload": {}}])
            queue.lease("worker-a", 1, 60)
            self.assertTrue(queue.fail("m0", "worker-a", "temporary", retry=True))
            self.assertEqual(queue.lease("worker-b", 1, 60)[0]["id"], "m0")


if __name__ == "__main__":
    unittest.main()
