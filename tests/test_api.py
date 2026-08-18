import importlib.util
import tempfile
import time
import unittest
from pathlib import Path


def _has_api_deps() -> bool:
    return all(
        importlib.util.find_spec(name) is not None
        for name in ("fastapi", "httpx")
    )


@unittest.skipUnless(_has_api_deps(), "fastapi/httpx not installed")
class ApiIntegrationTest(unittest.TestCase):
    def test_health_and_task_flow(self) -> None:
        from fastapi.testclient import TestClient

        from orchestra.api import create_app
        from orchestra.config import Settings

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = Settings(
                db_path=str(root / "api.db"),
                workspace_root=str(root / "workspaces"),
            )
            with TestClient(create_app(settings)) as client:
                response = client.get("/healthz")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["status"], "ok")

                response = client.post(
                    "/api/v1/tasks",
                    json={"query": "报销标准是什么", "session_id": "session-1"},
                )
                self.assertEqual(response.status_code, 202)
                task_id = response.json()["task_id"]

                task = {}
                for _ in range(50):
                    task = client.get(f"/api/v1/tasks/{task_id}").json()
                    if task["status"] == "succeeded":
                        break
                    time.sleep(0.1)
                self.assertEqual(task["status"], "succeeded")
                self.assertIn("[Mock]", task["result"])

                events = client.get(f"/api/v1/tasks/{task_id}/events")
                self.assertEqual(events.status_code, 200)


if __name__ == "__main__":
    unittest.main()