import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from app.core.json_store import atomic_write_json, path_lock, read_json_resilient
from app.core.run_retention import prune_run_history, prune_terminal_mapping, retain_recent_records


class JsonStoreTests(unittest.TestCase):
    def test_concurrent_atomic_writes_never_leave_partial_json(self):
        with tempfile.TemporaryDirectory(prefix="syncanvas-json-store-") as temp:
            target = Path(temp) / "state.json"
            barrier = threading.Barrier(8)

            def writer(index: int) -> None:
                barrier.wait()
                for revision in range(30):
                    atomic_write_json(target, {"writer": index, "revision": revision, "payload": "x" * 4096})
                    parsed = json.loads(target.read_text(encoding="utf-8"))
                    self.assertIn("writer", parsed)

            threads = [threading.Thread(target=writer, args=(index,)) for index in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)
                self.assertFalse(thread.is_alive())

            final = json.loads(target.read_text(encoding="utf-8"))
            self.assertIn(final["writer"], range(8))
            self.assertFalse(list(Path(temp).glob("*.tmp")))

    def test_locked_read_modify_write_preserves_all_updates(self):
        with tempfile.TemporaryDirectory(prefix="syncanvas-json-rmw-") as temp:
            target = Path(temp) / "counter.json"
            atomic_write_json(target, {"value": 0})

            def increment() -> None:
                for _ in range(50):
                    with path_lock(target):
                        state = read_json_resilient(target, {"value": 0})
                        state["value"] += 1
                        atomic_write_json(target, state)

            threads = [threading.Thread(target=increment) for _ in range(6)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)
            self.assertEqual(300, read_json_resilient(target, {})["value"])

    def test_corrupt_json_is_backed_up(self):
        with tempfile.TemporaryDirectory(prefix="syncanvas-json-corrupt-") as temp:
            target = Path(temp) / "canvas.json"
            target.write_text('{"broken":', encoding="utf-8")
            self.assertEqual({"fallback": True}, read_json_resilient(target, {"fallback": True}))
            backups = list(Path(temp).glob("canvas.json.corrupt-*"))
            self.assertEqual(1, len(backups))
            self.assertEqual('{"broken":', backups[0].read_text(encoding="utf-8"))


class RunRetentionTests(unittest.TestCase):
    def test_memory_and_disk_retention_preserve_active_runs(self):
        now_ms = int(time.time() * 1000)
        records = {
            "active": {"run_id": "active", "status": "running", "created_at": now_ms - 10_000},
            **{
                f"done-{index}": {
                    "run_id": f"done-{index}",
                    "status": "succeeded",
                    "completed_at": now_ms - index,
                }
                for index in range(5)
            },
        }
        prune_terminal_mapping(records, {"succeeded"}, memory_limit=2)
        self.assertIn("active", records)
        self.assertEqual(2, sum(row["status"] == "succeeded" for row in records.values()))

        rows = [
            {"id": "new", "timestamp": time.time()},
            {"id": "old", "timestamp": time.time() - 40 * 86400},
            {"id": "legacy"},
        ]
        self.assertEqual(["new", "legacy"], [row["id"] for row in retain_recent_records(rows)])

        with tempfile.TemporaryDirectory(prefix="syncanvas-run-retention-") as temp:
            run_dir = Path(temp)
            disk_records = {}
            for index in range(4):
                run_id = f"run-{index}"
                record = {
                    "run_id": run_id,
                    "status": "succeeded",
                    "completed_at": now_ms - index,
                }
                disk_records[run_id] = record
                atomic_write_json(run_dir / f"{run_id}.json", record)
            prune_run_history(
                run_dir,
                disk_records,
                {"succeeded"},
                memory_limit=2,
                disk_limit=2,
            )
            self.assertEqual(2, len(disk_records))
            self.assertEqual(2, len(list(run_dir.glob("*.json"))))


if __name__ == "__main__":
    unittest.main()
