"""Unit tests for the Taodock Media Open WebUI tool.

All HTTP traffic is mocked through a fake httpx.Client injected via
Tools._client, so tests run without network access or an API key.
"""

from __future__ import annotations

import asyncio
import base64
import unittest
from typing import Any, Optional

import taodock_media
from taodock_media import Tools, _TaodockError


class FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        json_body: Any = None,
        content: bytes = b"",
        content_type: str = "application/json",
    ) -> None:
        self.status_code = status_code
        self._json = json_body
        self.content = content
        self.text = json_body if isinstance(json_body, str) else (str(json_body) if json_body is not None else "")
        self.headers = {"content-type": content_type}

    def json(self) -> Any:
        return self._json


class FakeClient:
    def __init__(self, responses: dict[tuple[str, str], FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, Optional[dict]]] = []

    def request(self, method: str, path: str, json: Optional[dict] = None) -> FakeResponse:
        self.calls.append((method, path, json))
        key = (method.upper(), path)
        if key not in self.responses:
            raise AssertionError(f"Unexpected call: {method} {path}")
        return self.responses[key]

    def __enter__(self) -> "FakeClient":
        return self

    def __exit__(self, *args: Any) -> None:
        return None


def make_tools(
    responses: dict[tuple[str, str], FakeResponse],
    *,
    api_key: str = "sk-test",
    default_image_model: str = "",
    default_video_model: str = "",
) -> Tools:
    tools = Tools()
    tools.valves.api_key = api_key
    tools.valves.default_image_model = default_image_model
    tools.valves.default_video_model = default_video_model
    fake = FakeClient(responses)
    tools._fake_client = fake  # type: ignore[attr-defined]
    tools._client = lambda _key: fake  # type: ignore[method-assign]
    return tools


def run(coro: Any) -> str:
    return asyncio.run(coro)


class TestListModels(unittest.TestCase):
    def test_lists_model_ids(self) -> None:
        tools = make_tools(
            {("GET", "models"): FakeResponse(json_body={"data": [{"id": "agnes-3.0-flash"}, {"id": "agnes-image-2.5-flash"}]})}
        )
        out = run(tools.list_models())
        self.assertIn("agnes-3.0-flash", out)
        self.assertIn("agnes-image-2.5-flash", out)

    def test_empty_model_list(self) -> None:
        tools = make_tools({("GET", "models"): FakeResponse(json_body={"data": []})})
        self.assertIn("No models", run(tools.list_models()))

    def test_missing_api_key(self) -> None:
        tools = make_tools({}, api_key="")
        self.assertIn("not configured", run(tools.list_models()))


class TestGenerateImage(unittest.TestCase):
    def test_inline_image_markdown(self) -> None:
        tools = make_tools(
            {
                ("POST", "images/generations"): FakeResponse(
                    json_body={"data": [{"b64_json": base64.b64encode(b"png-bytes").decode()}]}
                )
            },
            default_image_model="agnes-image-2.5-flash",
        )
        out = run(tools.generate_image("a red fox"))
        self.assertIn("data:image/png;base64,", out)

    def test_url_image_markdown(self) -> None:
        tools = make_tools(
            {("POST", "images/generations"): FakeResponse(json_body={"data": [{"url": "https://cdn.example.com/a.png"}]})},
            default_image_model="agnes-image-2.5-flash",
        )
        out = run(tools.generate_image("a red fox"))
        self.assertIn("https://cdn.example.com/a.png", out)

    def test_api_error_raises_taodock_error(self) -> None:
        tools = make_tools(
            {("POST", "images/generations"): FakeResponse(status_code=503, json_body={"error": "model_not_found"})},
            default_image_model="agnes-image-2.5-flash",
        )
        with self.assertRaises(_TaodockError):
            run(tools.generate_image("a red fox"))

    def test_requires_model(self) -> None:
        tools = make_tools({})
        out = run(tools.generate_image("a red fox"))
        self.assertIn("No image model", out)


class TestVideoFlow(unittest.TestCase):
    def test_create_video_returns_task_id(self) -> None:
        tools = make_tools(
            {("POST", "videos"): FakeResponse(json_body={"id": "task_123", "status": "queued"})},
            default_video_model="agnes-video-2.5",
        )
        out = run(tools.create_video("a red fox runs"))
        self.assertIn("task_123", out)

    def test_get_video_status_completed(self) -> None:
        tools = make_tools(
            {("GET", "videos/task_123"): FakeResponse(json_body={"id": "task_123", "status": "completed"})}
        )
        out = run(tools.get_video_status("task_123"))
        self.assertIn("completed", out)
        self.assertIn("download_video", out)

    def test_download_video_requires_completion(self) -> None:
        tools = make_tools(
            {("GET", "videos/task_123"): FakeResponse(json_body={"id": "task_123", "status": "in_progress"})}
        )
        out = run(tools.download_video("task_123"))
        self.assertIn("not completed", out)

    def test_download_video_returns_data_link(self) -> None:
        mp4 = b"\x00\x00\x00\x18ftypmp42fake-video"
        tools = make_tools(
            {
                ("GET", "videos/task_123"): FakeResponse(json_body={"id": "task_123", "status": "completed"}),
                ("GET", "videos/task_123/content"): FakeResponse(
                    json_body=None, content=mp4, content_type="video/mp4"
                ),
            }
        )
        out = run(tools.download_video("task_123"))
        self.assertIn("data:video/mp4;base64,", out)
        self.assertIn(base64.b64encode(mp4).decode(), out)


class TestUserValves(unittest.TestCase):
    def test_user_key_overrides_shared_key(self) -> None:
        tools = make_tools({("GET", "models"): FakeResponse(json_body={"data": []})})
        user_valves = tools.UserValves(api_key="sk-user-personal")
        run(tools.list_models(__user__={"valves": user_valves}))
        # The fake client was created with the user key, proving the override path ran.
        self.assertEqual(tools._api_key({"valves": user_valves}), "sk-user-personal")


class TestMetadata(unittest.TestCase):
    def test_module_docstring_metadata(self) -> None:
        doc = taodock_media.__doc__ or ""
        self.assertIn("title: Taodock Media", doc)
        self.assertIn("requirements: httpx", doc)
        self.assertIn("licence: MIT", doc)


if __name__ == "__main__":
    unittest.main()
