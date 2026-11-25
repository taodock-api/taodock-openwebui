# Taodock Media — Open WebUI Tool

An Open WebUI Tool for generating images and managing asynchronous Taodock video tasks from a chat. It uses the models available to the configured Taodock API key. [Open WebUI Community](https://openwebui.com) · [Taodock Open WebUI guide](https://taodock.com/integrations/open-webui)

## Features

| Method | What it does |
|---|---|
| `list_models` | Lists the models available to your Taodock API key. |
| `generate_image` | Generates images from a prompt and returns Markdown image links for the chat. |
| `create_video` | Starts an async video task and returns its task ID. |
| `get_video_status` | Reports task status/progress (queued → in_progress → completed/failed). |
| `download_video` | Returns a markdown download link for a completed video. |

## Install

1. Download `taodock_media.py` from the [latest release](https://github.com/taodock-api/taodock-openwebui/releases/latest), or open the [source file](taodock_media.py) in this repository.
2. In Open WebUI, go to **Workspace → Tools → + (Create)**. Paste the full source and save. Your workspace role needs permission to create Tools.
3. Open the tool's **Valves** and set:
   - `api_key` — an API key from the [Taodock console](https://taodock.com/keys).
   - `base_url` — defaults to `https://taodock.com/v1`. Custom gateways require a trusted HTTPS URL.
   - `default_image_model` / `default_video_model` — optional fallbacks. Run `list_models` and use exact model IDs with the appropriate media capabilities.
4. Enable the tool for a model (model settings → Tools) or per chat via the **+** menu.

Users can also set a personal key under **User Valves**, which overrides the shared key for their own requests.

## Requirements

- Open WebUI 0.6.0 or newer.
- The tool declares `requirements: httpx`; Open WebUI installs it automatically on save.

## Notes and limits

- Video generation is asynchronous: `create_video` returns a task ID immediately; poll with `get_video_status` and call `download_video` once completed.
- `download_video` embeds MP4 videos up to 10 MiB in a Markdown data link. Use the Taodock console and the task ID for larger downloads.
- The tool sends the API key, model ID, media prompt, and task parameters to the configured HTTPS API. It uses masked key fields where the Open WebUI version supports them. HTTP errors show the response status and direct you to the Taodock request logs for details.
- Model and video capabilities depend on the key's permissions and the selected model. Generation can consume Taodock quota. [Current model catalog](https://taodock.com/models) · [Taodock API guide](https://docs.taodock.com/api/overview.html)

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install httpx pydantic pytest
PYTHONPATH=. .venv/bin/python -m pytest tests/ -q
```

## License

MIT — see [LICENSE](LICENSE).
