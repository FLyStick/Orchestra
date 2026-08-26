import asyncio
import importlib.util
import tempfile
import unittest
from pathlib import Path

from orchestra.config import Settings
from orchestra.contracts.task import TaskInput, TaskStatus
from orchestra.contracts.workflow import TaskExecutionError
from orchestra.store import SQLiteStore
from orchestra.workflow.driver import SqliteWorkflowDriver
from orchestra.workflow.retry import RetryPolicy
from orchestra.workflow.retry_scheduler import RedisZsetRetryScheduler, SqliteRetryScheduler
from orchestra.workflow.state_machine import can_transition, is_terminal


class FakeExecutor:
    """模拟 Executor：按失败次数抛出 TaskExecutionError，成功时落 SUCCEEDED。"""

    def __init__(self, store: SQLiteStore, failures: int = 1) -> None:
        self.store = store
        self.failures = failures
        self.calls = 0
        self.done: asyncio.Event | None = None

    def create_pending_task(self, task_input: TaskInput) -> str:
        return self.store.create_task(task_input)

    async def run(self, task_id: str, *, finalize_failure: bool = True) -> dict:
        self.calls += 1
        if self.calls <= self.failures:
            self.store.update_task(
                task_id,
                status=TaskStatus.RUNNING,
                error=f"boom-{self.calls}",
                duration_ms=1,
            )
            if self.done is not None:
                self.done.set()
            raise TaskExecutionError(f"boom-{self.calls}", stage="execution", duration_ms=1)
        self.store.update_task(task_id, status=TaskStatus.SUCCEEDED, result="ok")
        if self.done is not None:
            self.done.set()
        return self.store.get_task(task_id) or {}


class SqliteWorkflowDriverTest(unittest.TestCase):
    def _driver(self, failures: int, max_attempts: int = 3):
        tmp = tempfile.TemporaryDirectory()
        store = SQLiteStore(Path(tmp.name) / "workflow.db")
        fake = FakeExecutor(store, failures=failures)
        policy = RetryPolicy(max_attempts=max_attempts, base_delay_ms=0, max_delay_ms=0, jitter_ms=0)
        driver = SqliteWorkflowDriver(
            store,
            fake,
            retry_policy=policy,
        )
        return tmp, store, fake, driver

    def test_retry_policy_and_state_machine(self) -> None:
        policy = RetryPolicy(max_attempts=3, base_delay_ms=1000, max_delay_ms=4000, jitter_ms=0)
        self.assertTrue(policy.should_retry(1))
        self.assertFalse(policy.should_retry(3))
        self.assertEqual(policy.delay_for_attempt(1), 1000)
        self.assertEqual(policy.delay_for_attempt(2), 2000)
        self.assertEqual(policy.delay_for_attempt(3), 4000)
        self.assertTrue(can_transition(TaskStatus.PENDING, TaskStatus.ROUTING))
        self.assertTrue(can_transition(TaskStatus.RUNNING, TaskStatus.RETRYING))
        self.assertFalse(can_transition(TaskStatus.SUCCEEDED, TaskStatus.RUNNING))
        self.assertTrue(is_terminal(TaskStatus.FAILED))

    def test_sqlite_driver_retries_then_fails(self) -> None:
        async def scenario() -> None:
            tmp, store, fake, driver = self._driver(failures=3)
            try:
                task_id = await driver.submit(
                    TaskInput(query="风控条款审查", session_id="workflow-1"),
                    start=False,
                )
                first = await driver.execute(task_id)
                self.assertEqual(first["status"], "retrying")
                self.assertEqual(first["attempt_count"], 1)
                self.assertIsNotNone(first["next_retry_at"])

                second = await driver.execute(task_id)
                self.assertEqual(second["status"], "retrying")
                self.assertEqual(second["attempt_count"], 2)

                third = await driver.execute(task_id)
                self.assertEqual(third["status"], "failed")
                self.assertEqual(third["attempt_count"], 3)
                self.assertIn("boom-3", third["error"])

                events = store.list_events(task_id)
                types = [event["event_type"] for event in events]
                self.assertIn("workflow.task_retry_scheduled", types)
                self.assertIn("workflow.retries_exhausted", types)
                self.assertIn("task.failed", types)
            finally:
                await driver.close()
                store.close()
                tmp.cleanup()

        asyncio.run(scenario())

    def test_sqlite_driver_succeeds_after_retry(self) -> None:
        async def scenario() -> None:
            tmp, store, fake, driver = self._driver(failures=1)
            try:
                task_id = await driver.submit(
                    TaskInput(query="简单问题", session_id="workflow-2"),
                    start=False,
                )
                first = await driver.execute(task_id)
                self.assertEqual(first["status"], "retrying")
                second = await driver.execute(task_id)
                self.assertEqual(second["status"], "succeeded")
                self.assertEqual(second["attempt_count"], 1)
                self.assertEqual(fake.calls, 2)
            finally:
                await driver.close()
                store.close()
                tmp.cleanup()

        asyncio.run(scenario())

    def test_sqlite_driver_recover_resumes_crashed_task(self) -> None:
        async def scenario() -> None:
            tmp, store, fake, driver = self._driver(failures=0)
            fake.done = asyncio.Event()
            try:
                task_id = await driver.submit(
                    TaskInput(query="崩溃恢复", session_id="workflow-3"),
                    start=False,
                )
                store.update_task(task_id, status=TaskStatus.ROUTING)
                recovered = await driver.recover()
                self.assertGreaterEqual(recovered, 1)
                await asyncio.wait_for(fake.done.wait(), timeout=2)
                task = store.get_task(task_id)
                self.assertEqual(task["status"], "succeeded")
            finally:
                await driver.close()
                store.close()
                tmp.cleanup()

        asyncio.run(scenario())


def _has_fakeredis() -> bool:
    return importlib.util.find_spec("fakeredis") is not None


@unittest.skipUnless(_has_fakeredis(), "fakeredis not installed")
class RedisWorkflowDriverTest(unittest.TestCase):
    def test_redis_zset_retry_scheduler(self) -> None:
        async def scenario() -> None:
            import fakeredis.aioredis

            redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
            scheduler = RedisZsetRetryScheduler(redis, "test:retry")
            await scheduler.schedule("task-1", 0)
            await scheduler.schedule("task-2", 10_000)
            due = await scheduler.pop_due()
            self.assertIn("task-1", due)
            self.assertNotIn("task-2", due)
            self.assertTrue(await scheduler.cancel("task-2"))

        asyncio.run(scenario())

    def test_redis_stream_event_bus_replay(self) -> None:
        async def scenario() -> None:
            import fakeredis.aioredis

            from orchestra.workflow.event_bus import RedisStreamEventBus

            redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
            bus = RedisStreamEventBus(redis, "test:events")
            await bus.publish("task-1", "workflow.submitted", {"x": 1})
            events = await bus.replay("task-1")
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["event_type"], "workflow.submitted")
            self.assertEqual(events[0]["payload"], {"x": 1})

        asyncio.run(scenario())

    def test_redis_worker_consumes_submit_command(self) -> None:
        async def scenario() -> None:
            import fakeredis.aioredis

            from orchestra.workflow.redis_driver import RedisStreamWorkflowDriver
            from orchestra.workflow.worker import RedisWorkflowWorker

            with tempfile.TemporaryDirectory() as tmp:
                store = SQLiteStore(Path(tmp) / "redis-workflow.db")
                fake = FakeExecutor(store, failures=0)
                fake.done = asyncio.Event()
                settings = Settings(
                    workflow_driver="redis",
                    redis_stream_prefix="wf-test",
                    redis_consumer_group="wf-group",
                )
                redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
                driver = RedisStreamWorkflowDriver(
                    store,
                    fake,
                    redis,
                    settings,
                    retry_policy=RetryPolicy(max_attempts=2, base_delay_ms=0, max_delay_ms=0, jitter_ms=0),
                )
                await driver.start()
                worker = RedisWorkflowWorker(
                    driver=driver,
                    redis=redis,
                    commands_stream=driver.commands_stream,
                    consumer_group=driver.consumer_group,
                    consumer_name="test-worker",
                    retry_scheduler=driver.retry_scheduler,
                    concurrency=1,
                    poll_interval=0.05,
                    consume_block_ms=100,
                )
                await worker.start()
                try:
                    task_id = await driver.submit(
                        TaskInput(query="Redis 任务", session_id="redis-1")
                    )
                    await asyncio.wait_for(fake.done.wait(), timeout=3)
                    task = store.get_task(task_id)
                    self.assertEqual(task["status"], "succeeded")
                finally:
                    await worker.stop()
                    await driver.close()
                    store.close()

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
