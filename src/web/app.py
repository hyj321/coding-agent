"""Web UI for CodeAgent — FastAPI + static frontend.

Run: python -m src.web
Then open http://127.0.0.1:7860
"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src.web.runner import (
    get_transcript,
    list_recent_transcripts,
    reset_demo_files,
    run_coding_task,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ASSET_VERSION = "20260829e"

app = FastAPI(title="CodeAgent", version="0.4.0")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

_run_lock = threading.Lock()


class RunRequest(BaseModel):
    task: str = Field(..., min_length=1)
    workdir: str | None = "demos"
    model: str | None = None
    max_steps: int | None = Field(default=20, ge=1, le=60)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "busy": _run_lock.locked(), "asset_version": ASSET_VERSION}


@app.get("/api/meta")
def meta() -> dict[str, Any]:
    demos = PROJECT_ROOT / "demos"
    return {
        "default_workdir": str(demos if demos.is_dir() else PROJECT_ROOT),
        "project_root": str(PROJECT_ROOT),
        "asset_version": ASSET_VERSION,
        "suggestions": [
            {
                "title": "Create a script",
                "desc": "Write hello.py and run it",
                "prompt": "创建一个 hello.py，打印 Hello Agent，并用 python 运行它",
            },
            {
                "title": "Fix greeter tests",
                "desc": "Plan with todos, then repair",
                "prompt": (
                    "阅读 greeter_test.py，修复 greeter.py 使测试全部通过。"
                    "请先用 todo_write 列出计划，再逐步执行并更新 todo 状态，"
                    "最后运行 python greeter_test.py 验证。"
                ),
            },
            {
                "title": "Fix buggy calc",
                "desc": "edit_file + verify",
                "prompt": "修复 buggy_calc.py，使 add(2,3)==5，并运行 python buggy_calc.py 验证",
            },
        ],
    }


@app.get("/api/history")
def history() -> dict[str, Any]:
    return {"items": list_recent_transcripts()}


@app.get("/api/history/{transcript_id}")
def history_detail(transcript_id: str) -> dict[str, Any]:
    data = get_transcript(transcript_id)
    if data is None:
        raise HTTPException(status_code=404, detail="transcript not found")
    return data


@app.post("/api/demos/reset")
def demos_reset() -> dict[str, Any]:
    return reset_demo_files()


@app.post("/api/run")
async def run_stream(body: RunRequest) -> StreamingResponse:
    task = body.task.strip()
    if not task:
        raise HTTPException(status_code=400, detail="task is required")

    if not _run_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Another agent run is already in progress")

    workdir = body.workdir or "demos"
    wd_path = Path(workdir)
    if not wd_path.is_absolute():
        wd_path = (PROJECT_ROOT / wd_path).resolve()
    if not wd_path.is_dir():
        _run_lock.release()
        raise HTTPException(status_code=400, detail=f"workdir not found: {wd_path}")

    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def emit(event: dict[str, Any]) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, event)

    def worker() -> None:
        try:
            emit({"type": "start", "task": task, "workdir": str(wd_path)})

            def on_event(event: dict[str, Any]) -> None:
                emit(event)

            def log(msg: str) -> None:
                # Keep raw logs available but UI prefers structured events.
                emit({"type": "log", "text": msg})

            result, config, transcript_path = run_coding_task(
                task=task,
                workdir=wd_path,
                model=body.model,
                max_steps=body.max_steps,
                approval="auto",
                save_run_transcript=True,
                log=log,
                on_event=on_event,
            )
            emit(
                {
                    "type": "done",
                    "final_text": result.final_text,
                    "steps": result.steps,
                    "stopped_reason": result.stopped_reason,
                    "model": config.model,
                    "workdir": str(config.workdir),
                    "transcript": str(transcript_path) if transcript_path else None,
                    "transcript_id": transcript_path.name if transcript_path else None,
                }
            )
        except Exception as exc:  # noqa: BLE001
            emit({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
        finally:
            try:
                _run_lock.release()
            except RuntimeError:
                pass
            loop.call_soon_threadsafe(queue.put_nowait, None)

    threading.Thread(target=worker, daemon=True).start()

    async def event_gen():
        while True:
            item = await queue.get()
            if item is None:
                break
            yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def main() -> None:
    import uvicorn

    uvicorn.run(
        "src.web.app:app",
        host="127.0.0.1",
        port=7860,
        reload=False,
    )


if __name__ == "__main__":
    main()
