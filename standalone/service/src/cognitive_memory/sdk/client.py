# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Protocol


class MnemosyneClientError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class _Session(Protocol):
    def request(self, method: str, url: str, **kwargs: Any) -> Any: ...

    def close(self) -> None: ...  # optional


class MnemosyneClient:
    """REST 薄封装：认证 → 观察 → 检索 → 反馈 → 失效。不依赖 DSH。

    ``session`` 可注入 FastAPI ``TestClient`` 或 ``httpx.Client``。
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 30.0,
        api_prefix: str = "/api/v1",
        session: Any | None = None,
    ):
        self.base_url = (base_url or "").rstrip("/")
        self.token = token
        self.api_prefix = api_prefix.rstrip("/") or "/api/v1"
        self._owns_session = session is None
        if session is not None:
            self._client = session
        else:
            import httpx

            self._client = httpx.Client(
                base_url=self.base_url or "http://127.0.0.1",
                timeout=timeout,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )

    def close(self) -> None:
        if self._owns_session:
            close = getattr(self._client, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass

    def __enter__(self) -> "MnemosyneClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _url(self, path: str) -> str:
        # TestClient 通常要绝对 path；httpx Client 用相对 path
        return f"{self.api_prefix}{path}"

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        headers = dict(kwargs.pop("headers", {}) or {})
        headers.setdefault("Authorization", f"Bearer {self.token}")
        headers.setdefault("Content-Type", "application/json")
        url = self._url(path)
        resp = self._client.request(method, url, headers=headers, **kwargs)
        status = getattr(resp, "status_code", 0)
        if status >= 400:
            try:
                body = resp.json()
            except Exception:
                body = getattr(resp, "text", None)
            raise MnemosyneClientError(
                f"{method} {path} -> {status}",
                status_code=status,
                body=body,
            )
        if status == 204:
            return {}
        content = getattr(resp, "content", None)
        if content is not None and not content:
            return {}
        return resp.json()

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health")

    def health_probes(
        self,
        *,
        include_write_probe: bool = False,
        write_probe_namespace: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"include_write_probe": include_write_probe}
        if write_probe_namespace:
            params["write_probe_namespace"] = write_probe_namespace
        return self._request("GET", "/cognition/health/probes", params=params)

    def health_explain(self, *, subject: str = "health", ref_id: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"subject": subject}
        if ref_id:
            params["ref_id"] = ref_id
        return self._request("GET", "/cognition/health/explain", params=params)

    def observe(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/observations", json=payload)

    def propose(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/memories", json=payload)

    def search(self, query: str, **extra: Any) -> dict[str, Any]:
        body = {"query": query, **extra}
        return self._request("POST", "/memories/search", json=body)

    def feedback(
        self,
        memory_id: str,
        *,
        useful: bool | None = None,
        incorrect: bool | None = None,
        note: str = "",
        feedback_kind: str = "unclassified",
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/feedback",
            json={
                "memory_id": memory_id,
                "useful": useful,
                "incorrect": incorrect,
                "note": note,
                "producer": "mnemosyne-sdk",
                "feedback_kind": feedback_kind,
            },
        )

    def forget(self, memory_id: str, *, hard: bool = False) -> dict[str, Any]:
        return self._request(
            "DELETE",
            f"/memories/{memory_id}",
            params={"hard": hard},
        )

    def contract(self) -> dict[str, Any]:
        return self._request("GET", "/contract")

    def select_answer(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/cognition/answers/select", json=payload)

    def record_session_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/cognition/sessions/messages", json=payload)

    def record_session_delivery(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/cognition/sessions/deliveries", json=payload)

    def save_session_checkpoint(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/cognition/sessions/checkpoints", json=payload)

    def get_session_checkpoint(self, checkpoint_id: str) -> dict[str, Any]:
        return self._request("GET", f"/cognition/sessions/checkpoints/{checkpoint_id}")

    def resume_session_checkpoint(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/cognition/sessions/checkpoints/resume", json=payload)

    def record_usage_receipt(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/cognition/usage/receipts", json=payload)

    def usage_report(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/cognition/usage/report", json=payload)

    def plan_execution(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/cognition/executions/plan", json=payload)

    def execution_save(self, proposal_id: str) -> dict[str, Any]:
        return self._request("POST", f"/cognition/executions/{proposal_id}/save")

    def execution_adopt(self, proposal_id: str) -> dict[str, Any]:
        return self._request("POST", f"/cognition/executions/{proposal_id}/adopt")

    def execution_confirm(self, proposal_id: str) -> dict[str, Any]:
        return self._request("POST", f"/cognition/executions/{proposal_id}/confirm")

    def execution_observe(self, proposal_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", f"/cognition/executions/{proposal_id}/observe", json=payload)

    def run_cognitive_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/cognition/tasks/run", json=payload)

    def register_validation_suite(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/cognition/validation-suites", json=payload)

    def record_cognitive_result(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", f"/cognition/tasks/{run_id}/feedback", json=payload)

    def propose_learning_update(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/cognition/learning-updates", json=payload)

    def evaluate_learning_update(self, update_id: str) -> dict[str, Any]:
        return self._request("POST", f"/cognition/learning-updates/{update_id}/evaluate")

    def apply_learning_update(self, update_id: str, *, confirm: bool = False) -> dict[str, Any]:
        return self._request("POST", f"/cognition/learning-updates/{update_id}/apply", params={"confirm": confirm})

    def rollback_learning_update(self, update_id: str, *, confirm: bool = False) -> dict[str, Any]:
        return self._request("POST", f"/cognition/learning-updates/{update_id}/rollback", params={"confirm": confirm})

    def record_profile_hypothesis(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/cognition/profile-hypotheses", json=payload)
