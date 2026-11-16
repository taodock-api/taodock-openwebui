"""
title: Taodock Media
author: Taodock
author_url: https://taodock.com
git_url: https://github.com/taodock-api/taodock-openwebui
description: Generate images and videos with Taodock AI models. List available models, create images inline in chat, start asynchronous video tasks, check their status, and get a download link when they finish.
required_open_webui_version: 0.6.0
requirements: httpx
version: 0.1.0
licence: MIT
"""

from __future__ import annotations

import base64
from typing import Any, Optional

import httpx
from pydantic import BaseModel, Field


class _TaodockError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class Tools:
    def __init__(self) -> None:
        self.valves = self.Valves()

    class Valves(BaseModel):
        api_key: str = Field(
            "",
            description="Your Taodock API key (sk-...). Create one in the Taodock console at https://taodock.com",
        )
        base_url: str = Field(
            "https://taodock.com/v1",
            description="Taodock OpenAI-compatible base URL.",
        )
        default_image_model: str = Field(
            "",
            description="Fallback image model used when a call does not specify one, e.g. agnes-image-2.5-flash.",
        )
        default_video_model: str = Field(
            "",
            description="Fallback video model used when a call does not specify one, e.g. agnes-video-2.5.",
        )

    class UserValves(BaseModel):
        api_key: str = Field(
            "",
            description="Optional personal Taodock API key. Overrides the shared key when set.",
        )

    # ------------------------------------------------------------------
    # Internal helpers (prefixed with _ so they are NOT exposed as tools)
    # ------------------------------------------------------------------

    def _api_key(self, user: Optional[dict] = None) -> str:
        if user:
            user_valves = user.get("valves")
            if isinstance(user_valves, self.UserValves) and user_valves.api_key:
                return user_valves.api_key.strip()
        return self.valves.api_key.strip()

    def _client(self, api_key: str) -> httpx.Client:
        base = self.valves.base_url.rstrip("/") or "https://taodock.com/v1"
        return httpx.Client(
            base_url=base,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(connect=15.0, read=180.0, write=30.0, pool=15.0),
        )

    def _request(
        self,
        client: httpx.Client,
        method: str,
        path: str,
        payload: Optional[dict] = None,
    ) -> Any:
        try:
            response = client.request(method, path, json=payload)
        except httpx.HTTPError as exc:
            raise _TaodockError(
                f"Could not reach the Taodock API: {exc.__class__.__name__}. Check the base URL in the tool settings."
            ) from exc
        if response.status_code >= 400:
            raise _TaodockError(
                f"Taodock API error (HTTP {response.status_code}): {response.text[:500]}"
            )
        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            return response.json()
        return response.content

    @staticmethod
    def _task_payload(body: Any, fallback_task_id: str = "") -> dict[str, Any]:
        if not isinstance(body, dict):
            raise _TaodockError("Taodock returned an unexpected video task payload.")
        task_id = body.get("id") or body.get("task_id") or fallback_task_id
        if not task_id:
            raise _TaodockError("Taodock returned a video task without an ID.")
        result: dict[str, Any] = {"task_id": task_id, "status": body.get("status") or "queued"}
        if isinstance(body.get("model"), str):
            result["model"] = body["model"]
        if isinstance(body.get("progress"), (int, float)):
            result["progress"] = body["progress"]
        if isinstance(body.get("error"), dict):
            result["error"] = body["error"]
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
        with self._client(api_key) as client:
            body = self._request(client, "GET", "models")
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list) or not data:
            return "No models are available for this API key."
        ids = sorted(str(item.get("id")) for item in data if isinstance(item, dict) and item.get("id"))
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
        with self._client(api_key) as client:
            body = self._request(client, "POST", "images/generations", payload)
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
        with self._client(api_key) as client:
            body = self._request(client, "POST", "videos", payload)
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
        with self._client(api_key) as client:
            body = self._request(client, "GET", f"videos/{task_id}")
        task = self._task_payload(body, fallback_task_id=task_id)
        lines = [f"- Task ID: `{task['task_id']}`", f"- Status: {task['status']}"]
        if "progress" in task:
            lines.append(f"- Progress: {task['progress']}%")
        if task.get("model"):
            lines.append(f"- Model: {task['model']}")
        if task.get("error"):
            lines.append(f"- Error: {task['error']}")
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
        with self._client(api_key) as client:
            task = self._task_payload(
                self._request(client, "GET", f"videos/{task_id}"), fallback_task_id=task_id
            )
            if task["status"] != "completed":
                return (
                    f"Task `{task_id}` is not completed yet (status: {task['status']}). "
                    "Check again later with get_video_status."
                )
            content = self._request(client, "GET", f"videos/{task_id}/content")
        if not isinstance(content, (bytes, bytearray)) or not content:
            return f"No video data was returned for task `{task_id}`."
        encoded = base64.b64encode(bytes(content)).decode("ascii")
        megabytes = len(content) / (1024 * 1024)
        return (
            f"Video for task `{task_id}` ({megabytes:.2f} MB):\n\n"
            f"[Download MP4](data:video/mp4;base64,{encoded})"
        )
