"""Unit tests for the Taodock Media Open WebUI tool.

HTTPX mock transports exercise async requests without network access or a real key.
"""

from __future__ import annotations

import asyncio
import base64
import unittest
from typing import Any

import httpx

import taodock_media
from taodock_media import Tools, _TaodockError


def make_tools(
    responses: dict[tuple[str, str], httpx.Response],
    *,
    api_key: str = "sk-test",
    default_image_model: str = "",
    default_video_model: str = "",
) -> Tools:
    tools = Tools()
    tools.valves.api_key = api_key
    tools.valves.default_image_model = default_image_model
    tools.valves.default_video_model = default_video_model

    def respond(request: httpx.Request) -> httpx.Response:
        path = request.url.raw_path.decode("ascii")
        assert path.startswith("/v1/"), f"API request lost its /v1 prefix: {path}"
        return responses[(request.method, path.removeprefix("/v1/"))]

    def client(key: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url="https://taodock.com/v1",
            headers={"Authorization": f"Bearer {key}"},
            transport=httpx.MockTransport(respond),
        )

    tools._client = client  # type: ignore[method-assign]
    return tools


def run(coro: Any) -> str:
    return asyncio.run(coro)


class TestListModels(unittest.TestCase):
    def test_lists_model_ids(self) -> None:
        tools = make_tools(
            {("GET", "models"): httpx.Response(200, json={"data": [{"id": "chat-model"}, {"id": "image-model"}]})}
        )
        out = run(tools.list_models())
        self.assertIn("chat-model", out)
        self.assertIn("image-model", out)

    def test_empty_model_list(self) -> None:
        tools = make_tools({("GET", "models"): httpx.Response(200, json={"data": []})})
        self.assertIn("No models", run(tools.list_models()))

    def test_missing_api_key(self) -> None:
        tools = make_tools({}, api_key="")
        self.assertIn("not configured", run(tools.list_models()))


class TestGenerateImage(unittest.TestCase):
    def test_inline_image_markdown(self) -> None:
        tools = make_tools(
            {
                ("POST", "images/generations"): httpx.Response(
                    200, json={"data": [{"b64_json": base64.b64encode(b"png-bytes").decode()}]}
                )
            },
            default_image_model="agnes-image-2.5-flash",
        )
        out = run(tools.generate_image("a red fox"))
        self.assertIn("data:image/png;base64,", out)

    def test_url_image_markdown(self) -> None:
        tools = make_tools(
            {("POST", "images/generations"): httpx.Response(200, json={"data": [{"url": "https://cdn.example.com/a.png"}]})},
            default_image_model="agnes-image-2.5-flash",
        )
        out = run(tools.generate_image("a red fox"))
        self.assertIn("https://cdn.example.com/a.png", out)

    def test_api_error_has_status_and_omits_server_details(self) -> None:
        tools = make_tools(
            {("POST", "images/generations"): httpx.Response(503, json={"error": "private upstream details"})},
            default_image_model="agnes-image-2.5-flash",
        )
        with self.assertRaisesRegex(_TaodockError, "HTTP 503") as error:
            run(tools.generate_image("a red fox"))
        self.assertNotIn("private upstream details", str(error.exception))

    def test_requires_model(self) -> None:
        tools = make_tools({})
        out = run(tools.generate_image("a red fox"))
        self.assertIn("No image model", out)


class TestVideoFlow(unittest.TestCase):
    def test_create_video_returns_task_id(self) -> None:
        tools = make_tools(
            {("POST", "videos"): httpx.Response(200, json={"id": "task_123", "status": "queued"})},
            default_video_model="agnes-video-2.5",
        )
        out = run(tools.create_video("a red fox runs"))
        self.assertIn("task_123", out)

    def test_get_video_status_completed(self) -> None:
        tools = make_tools(
            {("GET", "videos/task_123"): httpx.Response(200, json={"id": "task_123", "status": "completed"})}
        )
        out = run(tools.get_video_status("task_123"))
        self.assertIn("completed", out)
        self.assertIn("download_video", out)

    def test_download_video_requires_completion(self) -> None:
        tools = make_tools(
            {("GET", "videos/task_123"): httpx.Response(200, json={"id": "task_123", "status": "in_progress"})}
        )
        out = run(tools.download_video("task_123"))
        self.assertIn("not completed", out)

    def test_download_video_returns_data_link(self) -> None:
        mp4 = b"\x00\x00\x00\x18ftypmp42fake-video"
        tools = make_tools(
            {
                ("GET", "videos/task_123"): httpx.Response(200, json={"id": "task_123", "status": "completed"}),
                ("GET", "videos/task_123/content"): httpx.Response(
                    200, content=mp4, headers={"Content-Type": "video/mp4"},
                ),
            }
        )
        out = run(tools.download_video("task_123"))
        self.assertIn("data:video/mp4;base64,", out)
        self.assertIn(base64.b64encode(mp4).decode(), out)

    def test_download_limits_stream_without_content_length(self) -> None:
        class AsyncChunks(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b"a" * taodock_media._MAX_INLINE_VIDEO_BYTES
                yield b"b"

        tools = make_tools(
            {
                ("GET", "videos/task_123"): httpx.Response(200, json={"status": "completed"}),
                ("GET", "videos/task_123/content"): httpx.Response(
                    200, stream=AsyncChunks(), headers={"Content-Type": "video/mp4"},
                ),
            }
        )
        with self.assertRaisesRegex(_TaodockError, "10 MiB"):
            run(tools.download_video("task_123"))

    def test_download_rejects_a_json_response(self) -> None:
        tools = make_tools(
            {
                ("GET", "videos/task_123"): httpx.Response(200, json={"status": "completed"}),
                ("GET", "videos/task_123/content"): httpx.Response(200, json={"data": "not a video"}),
            }
        )
        with self.assertRaisesRegex(_TaodockError, "did not return an MP4"):
            run(tools.download_video("task_123"))

    def test_task_id_cannot_change_the_api_route(self) -> None:
        tools = make_tools({("GET", "videos/task%3Fpart"): httpx.Response(200, json={"status": "queued"})})
        self.assertIn("queued", run(tools.get_video_status("task?part")))
        with self.assertRaisesRegex(_TaodockError, "task ID returned"):
            run(tools.get_video_status("../models"))


class TestUserValves(unittest.TestCase):
    def test_user_key_overrides_shared_key(self) -> None:
        tools = make_tools({("GET", "models"): httpx.Response(200, json={"data": []})})
        user_valves = tools.UserValves(api_key="sk-user-personal")
        run(tools.list_models(__user__={"valves": user_valves}))
        self.assertEqual(tools._api_key({"valves": user_valves}), "sk-user-personal")
        self.assertEqual(tools._api_key({"valves": tools.UserValves(api_key=" ")}), "sk-test")

    def test_requires_https_and_masks_key_fields(self) -> None:
        tools = Tools()
        tools.valves.api_key = "dummy-key"
        tools.valves.base_url = "http://example.com/v1"
        with self.assertRaisesRegex(_TaodockError, "trusted HTTPS"):
            run(tools.list_models())
        for valve_type in (Tools.Valves, Tools.UserValves):
            self.assertEqual(valve_type.model_json_schema()["properties"]["api_key"]["input"]["type"], "password")


class TestMetadata(unittest.TestCase):
    def test_module_docstring_metadata(self) -> None:
        doc = taodock_media.__doc__ or ""
        self.assertIn("title: Taodock Media", doc)
        self.assertIn("requirements: httpx", doc)
        self.assertIn("licence: MIT", doc)


if __name__ == "__main__":
    unittest.main()
