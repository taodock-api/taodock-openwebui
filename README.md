# Taodock Media — Open WebUI Tool

An [Open WebUI](https://openwebui.com) Tool that adds Taodock AI media generation to any chat: generate images inline, start asynchronous video tasks, check their status, and download finished videos.

## Features

| Method | What it does |
|---|---|
| `list_models` | Lists the models available to your Taodock API key. |
| `generate_image` | Generates images from a prompt and renders them inline in the chat. |
| `create_video` | Starts an async video task and returns its task ID. |
| `get_video_status` | Reports task status/progress (queued → in_progress → completed/failed). |
| `download_video` | Returns a markdown download link for a completed video. |

## Install

1. In Open WebUI, go to **Workspace → Tools → + (Create)**.
2. Paste the full contents of [`taodock_media.py`](taodock_media.py) and save.
3. Open the tool's **Valves** and set:
   - `api_key` — your Taodock API key (`sk-...`, from the [Taodock console](https://taodock.com));
   - `base_url` — keep the default `https://taodock.com/v1`;
   - `default_image_model` / `default_video_model` — optional fallbacks used when the model doesn't specify one.
4. Enable the tool for a model (model settings → Tools) or per chat via the **+** menu.

Users can also set a personal key under **User Valves**, which overrides the shared key for their own requests.

## Requirements

- Open WebUI 0.6.0 or newer.
- The tool declares `requirements: httpx`; Open WebUI installs it automatically on save.

## Notes and limits

- Video generation is asynchronous: `create_video` returns a task ID immediately; poll with `get_video_status` and call `download_video` once completed.
- `download_video` embeds the MP4 as a `data:` URI link, which is convenient for short clips. Very large videos may not be practical to embed; consider downloading via the Taodock console for large results.
- The API key is only sent to the configured `base_url` and is never logged or included in tool output.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install httpx pydantic pytest
PYTHONPATH=. .venv/bin/python -m pytest tests/ -q
```

## License

MIT — see [LICENSE](LICENSE).
