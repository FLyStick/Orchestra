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
    def _wait_success(self, client, task_id: str) -> dict:
        task = {}
        for _ in range(50):
            task = client.get(f"/api/v1/tasks/{task_id}").json()
            if task["status"] == "succeeded":
                break
            time.sleep(0.1)
        return task

    def test_health_and_task_flow(self) -> None:
        from fastapi.testclient import TestClient

        from orchestra.api import create_app
        from orchestra.config import Settings

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = Settings(
                db_path=str(root / "api.db"),
                workspace_root=str(root / "workspaces"),
                llm_provider="mock",
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
                task = self._wait_success(client, task_id)
                self.assertEqual(task["status"], "succeeded")
                self.assertIn("[Mock]", task["result"])

                events = client.get(f"/api/v1/tasks/{task_id}/events")
                self.assertEqual(events.status_code, 200)

    def test_react_task_and_workspace_api(self) -> None:
        from fastapi.testclient import TestClient

        from orchestra.api import create_app
        from orchestra.config import Settings

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = Settings(
                db_path=str(root / "api-react.db"),
                workspace_root=str(root / "workspaces"),
                llm_provider="mock",
            )
            with TestClient(create_app(settings)) as client:
                response = client.post(
                    "/api/v1/tasks",
                    json={
                        "query": "请调用rag_search查询报销标准",
                        "session_id": "session-react",
                    },
                )
                self.assertEqual(response.status_code, 202)
                task_id = response.json()["task_id"]
                task = self._wait_success(client, task_id)
                self.assertEqual(task["strategy"], "react")
                self.assertIn("已根据 rag_search", task["result"])

                workspace = client.get("/api/v1/sessions/session-react/workspace")
                self.assertEqual(workspace.status_code, 200)
                files = workspace.json()["files"]
                self.assertIn("answer.md", files)
                self.assertIn("rag/finance/expense-policy.md", files)

                file_response = client.get(
                    "/api/v1/sessions/session-react/workspace/files/answer.md"
                )
                self.assertEqual(file_response.status_code, 200)
                self.assertEqual(file_response.json()["path"], "answer.md")


    def test_scenarios_endpoint_lists_p4_scenarios(self) -> None:
        from fastapi.testclient import TestClient

        from orchestra.api import create_app
        from orchestra.config import Settings

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = Settings(
                db_path=str(root / "api-scenarios.db"),
                workspace_root=str(root / "workspaces"),
                llm_provider="mock",
            )
            with TestClient(create_app(settings)) as client:
                response = client.get("/api/v1/scenarios")
                self.assertEqual(response.status_code, 200)
                scenario_ids = [item["scenario_id"] for item in response.json()]
                self.assertIn("hr_policy_qa", scenario_ids)
                self.assertIn("risk_contract_review", scenario_ids)

if __name__ == "__main__":
    unittest.main()
