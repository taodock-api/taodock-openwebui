"""
title: Taodock Media
author: Taodock
author_url: https://taodock.com
git_url: https://github.com/taodock-api/taodock-openwebui
description: Generate images and videos with Taodock AI models. List available models, create images inline in chat, start asynchronous video tasks, check their status, and get a download link when they finish.
required_open_webui_version: 0.6.0
requirements: httpx
version: 0.2.0
licence: MIT
"""

from __future__ import annotations

import base64
from typing import Any, Optional
from urllib.parse import quote, urlsplit

import httpx
from pydantic import BaseModel, Field


_DEFAULT_BASE_URL = "https://taodock.com/v1"
_MAX_INLINE_VIDEO_BYTES = 10 * 1024 * 1024
_VIDEO_TOO_LARGE = "The video exceeds the 10 MiB inline download limit. Download it from Taodock using the task ID."


class _TaodockError(Exception):
    pass


class Tools:
    def __init__(self) -> None:
        self.valves = self.Valves()

    class Valves(BaseModel):
        api_key: str = Field(
            "",
            description="Create a Taodock API key at https://taodock.com/keys.",
            json_schema_extra={"input": {"type": "password"}},
            repr=False,
        )
        base_url: str = Field(
            _DEFAULT_BASE_URL,
            description="Trusted HTTPS base URL, including /v1 for Taodock.",
        )
        default_image_model: str = Field(
            "",
            description="Exact authorized image model ID to use when the call omits one.",
        )
        default_video_model: str = Field(
            "",
            description="Exact authorized video model ID to use when the call omits one.",
        )

    class UserValves(BaseModel):
        api_key: str = Field(
            "",
            description="Optional personal Taodock API key. Overrides the shared key when set.",
            json_schema_extra={"input": {"type": "password"}},
            repr=False,
        )

    # ------------------------------------------------------------------
    # Internal helpers (prefixed with _ so they are NOT exposed as tools)
    # ------------------------------------------------------------------

    def _api_key(self, user: Optional[dict] = None) -> str:
        if user:
            user_valves = user.get("valves")
            if isinstance(user_valves, self.UserValves) and user_valves.api_key.strip():
                return user_valves.api_key.strip()
        return self.valves.api_key.strip()

    def _client(self, api_key: str) -> httpx.AsyncClient:
        base = self.valves.base_url.strip().rstrip("/") or _DEFAULT_BASE_URL
        try:
            parsed = urlsplit(base)
            if (
                parsed.scheme == "https"
                and parsed.hostname
                and parsed.username is None
                and "?" not in base
                and "#" not in base
            ):
                return httpx.AsyncClient(
                    base_url=base,
                    headers={"Authorization": f"Bearer {api_key}"},
                    follow_redirects=False,
                    timeout=httpx.Timeout(connect=15.0, read=180.0, write=30.0, pool=15.0),
                )
        except (ValueError, httpx.InvalidURL):
            pass
        raise _TaodockError("Set a trusted HTTPS base URL without credentials, query parameters, or a fragment.")

    @staticmethod
    def _check_response(response: httpx.Response) -> None:
        if not response.is_success:
            status = response.status_code
            if status in (401, 403):
                raise _TaodockError(
                    f"Taodock denied the request (HTTP {status}). Check your API key, model permissions, and quota."
                )
            raise _TaodockError(f"Taodock could not complete the request (HTTP {status}). Check the Taodock request logs.")

    async def _request_json(
        self,
        client: httpx.AsyncClient,
        method: str,
        path: str,
        payload: Optional[dict] = None,
    ) -> Any:
        try:
            response = await client.request(method, path, json=payload)
        except httpx.HTTPError:
            raise _TaodockError("Could not reach the Taodock API. Check the network and base URL.") from None
        self._check_response(response)
        try:
            return response.json()
        except ValueError:
            raise _TaodockError("Taodock returned invalid JSON. Check the base URL and Taodock request logs.") from None

    async def _video_content(self, client: httpx.AsyncClient, task_path: str) -> bytes:
        content = bytearray()
        try:
            async with client.stream("GET", f"{task_path}/content") as response:
                self._check_response(response)
                content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if content_type not in {"video/mp4", "application/octet-stream"}:
                    raise _TaodockError("Taodock did not return an MP4 video for this task.")
                length = response.headers.get("content-length", "")
                if length.isdecimal() and int(length) > _MAX_INLINE_VIDEO_BYTES:
                    raise _TaodockError(_VIDEO_TOO_LARGE)
                async for chunk in response.aiter_bytes():
                    if len(content) + len(chunk) > _MAX_INLINE_VIDEO_BYTES:
                        raise _TaodockError(_VIDEO_TOO_LARGE)
                    content.extend(chunk)
        except httpx.HTTPError:
            raise _TaodockError("Could not download the Taodock video. Check the network and try again.") from None
        if content_type == "application/octet-stream" and content[4:8] != b"ftyp":
            raise _TaodockError("Taodock did not return an MP4 video for this task.")
        return bytes(content)

    @staticmethod
    def _task_path(task_id: str) -> str:
        if task_id in {".", ".."} or "/" in task_id or "\\" in task_id or len(task_id) > 256:
            raise _TaodockError("Use the task ID returned by create_video.")
        return f"videos/{quote(task_id, safe='')}"

    @staticmethod
    def _task_payload(body: Any, fallback_task_id: str = "") -> dict[str, Any]:
        if not isinstance(body, dict):
            raise _TaodockError("Taodock returned an unexpected video task payload.")
        task_id = body.get("id") or body.get("task_id") or fallback_task_id
        if not isinstance(task_id, str) or not task_id:
            raise _TaodockError("Taodock returned a video task without an ID.")
        result: dict[str, Any] = {"task_id": task_id, "status": body.get("status") or "queued"}
        if isinstance(body.get("model"), str):
            result["model"] = body["model"]
        if isinstance(body.get("progress"), (int, float)):
            result["progress"] = body["progress"]
        return result

    # ------------------------------------------------------------------
    # Tool methods exposed to the model
    # ------------------------------------------------------------------

    async def list_models(self, __user__: Optional[dict] = None) -> str:
        """List the Taodock models available to the configured API key.

        :return: A markdown table with the available model IDs.
        """
        api_key = self._api_key(__user__)
        if not api_key:
            return "Taodock API key is not configured. Add it in the tool settings (Valves) or in your personal user valves."
        async with self._client(api_key) as client:
            body = await self._request_json(client, "GET", "models")
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list) or not data:
            return "No models are available for this API key."
        ids = sorted(str(item.get("id")) for item in data if isinstance(item, dict) and item.get("id"))
        if not ids:
            return "No models are available for this API key."
        lines = ["| Model ID |", "| --- |"]
        lines.extend(f"| {model_id} |" for model_id in ids)
        return "\n".join(lines)

    async def generate_image(
        self,
        prompt: str,
        model: str = "",
        size: str = "",
        aspect_ratio: str = "",
        number_of_images: int = 1,
        __user__: Optional[dict] = None,
    ) -> str:
        """Generate one or more images from a text prompt with a Taodock image model and display them inline in the chat.

        :param prompt: Description of the image to generate.
        :param model: Image model ID (optional; falls back to the tool's default image model). Use list_models to discover IDs.
        :param size: Optional output size, e.g. 1024x1024. Leave empty for the model default.
        :param aspect_ratio: Optional aspect ratio such as 16:9. Leave empty for the model default.
        :param number_of_images: Number of images to generate (1-4). Defaults to 1.
        :return: Markdown that renders the generated images.
        """
        api_key = self._api_key(__user__)
        if not api_key:
            return "Taodock API key is not configured. Add it in the tool settings (Valves) or in your personal user valves."
        model_id = model.strip() or self.valves.default_image_model.strip()
        if not model_id:
            return "No image model specified and no default image model is configured. Run list_models to pick one."
        payload: dict[str, Any] = {
            "model": model_id,
            "prompt": prompt,
            "response_format": "b64_json",
        }
        if size.strip():
            payload["size"] = size.strip()
        if aspect_ratio.strip():
            payload["aspect_ratio"] = aspect_ratio.strip()
        if number_of_images and number_of_images > 1:
            payload["n"] = min(number_of_images, 4)
        async with self._client(api_key) as client:
            body = await self._request_json(client, "POST", "images/generations", payload)
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list) or not data:
            return f"{model_id} returned no image data."
        images: list[str] = []
        for item in data:
            if isinstance(item, dict) and isinstance(item.get("b64_json"), str) and item["b64_json"]:
                images.append(f"![Generated image](data:image/png;base64,{item['b64_json']})")
            elif isinstance(item, dict) and isinstance(item.get("url"), str) and item["url"]:
                images.append(f"![Generated image]({item['url']})")
        if not images:
            return f"{model_id} returned no usable image payload."
        return "\n\n".join(images)

    async def create_video(
        self,
        prompt: str,
        model: str = "",
        duration: int = 0,
        resolution: str = "",
        aspect_ratio: str = "",
        negative_prompt: str = "",
        __user__: Optional[dict] = None,
    ) -> str:
        """Start an asynchronous Taodock video generation task and return its task ID. Use get_video_status with the returned task ID to check progress, and download_video when it completes.

        :param prompt: Description of the video to generate.
        :param model: Video model ID (optional; falls back to the tool's default video model).
        :param duration: Optional video length in seconds. 0 uses the model default.
        :param resolution: Optional resolution such as 1080p.
        :param aspect_ratio: Optional aspect ratio such as 16:9.
        :param negative_prompt: Optional description of what to avoid.
        :return: The created task ID and initial status.
        """
        api_key = self._api_key(__user__)
        if not api_key:
            return "Taodock API key is not configured. Add it in the tool settings (Valves) or in your personal user valves."
        model_id = model.strip() or self.valves.default_video_model.strip()
        if not model_id:
            return "No video model specified and no default video model is configured. Run list_models to pick one."
        payload: dict[str, Any] = {"model": model_id, "prompt": prompt}
        if duration and duration > 0:
            payload["duration"] = duration
        if resolution.strip():
            payload["resolution"] = resolution.strip()
        if aspect_ratio.strip():
            payload["aspect_ratio"] = aspect_ratio.strip()
        if negative_prompt.strip():
            payload["negative_prompt"] = negative_prompt.strip()
        async with self._client(api_key) as client:
            body = await self._request_json(client, "POST", "videos", payload)
        task = self._task_payload(body)
        return (
            f"Video task created.\n\n"
            f"- Task ID: `{task['task_id']}`\n"
            f"- Status: {task['status']}\n"
            f"- Model: {task.get('model', model_id)}\n\n"
            f"Call get_video_status with task ID `{task['task_id']}` to check progress. Video generation is asynchronous and may take a few minutes."
        )

    async def get_video_status(self, task_id: str, __user__: Optional[dict] = None) -> str:
        """Check the status of a Taodock video generation task.

        :param task_id: The task ID returned by create_video.
        :return: Current status (queued, in_progress, completed or failed) and progress when available.
        """
        api_key = self._api_key(__user__)
        if not api_key:
            return "Taodock API key is not configured. Add it in the tool settings (Valves) or in your personal user valves."
        task_id = task_id.strip()
        if not task_id:
            return "Task ID is required."
        task_path = self._task_path(task_id)
        async with self._client(api_key) as client:
            body = await self._request_json(client, "GET", task_path)
        task = self._task_payload(body, fallback_task_id=task_id)
        lines = [f"- Task ID: `{task['task_id']}`", f"- Status: {task['status']}"]
        if "progress" in task:
            lines.append(f"- Progress: {task['progress']}%")
        if task.get("model"):
            lines.append(f"- Model: {task['model']}")
        if task["status"] == "failed":
            lines.append("Check the Taodock request logs for details.")
        if task["status"] == "completed":
            lines.append(
                "\nThe video is ready. Call download_video with this task ID to get a download link."
            )
        return "\n".join(lines)

    async def download_video(self, task_id: str, __user__: Optional[dict] = None) -> str:
        """Download a completed Taodock video and return a markdown download link.

        :param task_id: The task ID returned by create_video. The task must be completed.
        :return: A markdown link to download the MP4 file.
        """
        api_key = self._api_key(__user__)
        if not api_key:
            return "Taodock API key is not configured. Add it in the tool settings (Valves) or in your personal user valves."
        task_id = task_id.strip()
        if not task_id:
            return "Task ID is required."
        task_path = self._task_path(task_id)
        async with self._client(api_key) as client:
            task = self._task_payload(
                await self._request_json(client, "GET", task_path), fallback_task_id=task_id
            )
            if task["status"] != "completed":
                return (
                    f"Task `{task_id}` is not completed yet (status: {task['status']}). "
                    "Check again later with get_video_status."
                )
            content = await self._video_content(client, task_path)
        if not content:
            return f"No video data was returned for task `{task_id}`."
        encoded = base64.b64encode(content).decode("ascii")
        megabytes = len(content) / (1024 * 1024)
        return (
            f"Video for task `{task_id}` ({megabytes:.2f} MB):\n\n"
            f"[Download MP4](data:video/mp4;base64,{encoded})"
        )
