"""Web UI for CodeAgent — FastAPI + static frontend.

Run: python -m src.web
Then open http://127.0.0.1:7860
"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src.web.approval import ApprovalBridge
from src.web.runner import (
    build_tree,
    delete_workdir_file,
    get_transcript,
    list_directory,
    list_recent_transcripts,
    read_workdir_file,
    reset_demo_files,
    resolve_workdir,
    run_coding_task,
    write_workdir_file,
    PROJECT_ROOT,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
ASSET_VERSION = "20260830k"

app = FastAPI(title="CodeAgent", version="0.5.0")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

_run_lock = threading.Lock()
_active_bridge: ApprovalBridge | None = None
_bridge_lock = threading.Lock()


class RunRequest(BaseModel):
    task: str = Field(..., min_length=1)
    workdir: str | None = "demos"
    model: str | None = None
    max_steps: int | None = Field(default=30, ge=1, le=60)
    session_id: str | None = Field(
        default=None,
        description="Stable id for one conversation (= one history / memory unit).",
    )
    ask_min_risk: Literal["low", "medium", "high"] = Field(
        default="medium",
        description="Risk level at which Web asks for Allow/Deny (default: medium+).",
    )


class ApproveRequest(BaseModel):
    request_id: str = Field(..., min_length=4, max_length=64)
    allowed: bool


class WriteFileRequest(BaseModel):
    workdir: str | None = "demos"
    path: str = Field(..., min_length=1)
    content: str = ""


class DeleteFileRequest(BaseModel):
    workdir: str | None = "demos"
    path: str = Field(..., min_length=1)


class WorkdirRequest(BaseModel):
    path: str = Field(..., min_length=1)


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
        "features": {
            "fs_write": True,
            "fs_delete": True,
            "attach_drop": True,
            "approval": True,
        },
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
                    "用 todo_write 写 3～5 条阶段计划（可与读文件同轮），"
                    "阶段完成再更新 todo；最后运行 python greeter_test.py 验证。"
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


@app.post("/api/workdir")
def set_workdir(body: WorkdirRequest) -> dict[str, Any]:
    try:
        wd = resolve_workdir(body.path)
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not wd.is_dir():
        raise HTTPException(status_code=400, detail=f"workdir not found: {wd}")
    listing = list_directory(wd)
    return {"workdir": str(wd), "listing": listing}


@app.get("/api/fs/list")
def fs_list(path: str | None = Query(default=None)) -> dict[str, Any]:
    try:
        target = resolve_workdir(path) if path else PROJECT_ROOT
        return list_directory(target)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except NotADirectoryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/fs/tree")
def fs_tree(workdir: str = Query(default="demos")) -> dict[str, Any]:
    try:
        wd = resolve_workdir(workdir)
        if not wd.is_dir():
            raise HTTPException(status_code=400, detail=f"workdir not found: {wd}")
        return build_tree(wd)
    except HTTPException:
        raise
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/fs/file")
def fs_file(
    workdir: str = Query(default="demos"),
    path: str = Query(..., min_length=1),
) -> dict[str, Any]:
    try:
        wd = resolve_workdir(workdir)
        return read_workdir_file(wd, path)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/fs/write")
def fs_write(body: WriteFileRequest) -> dict[str, Any]:
    """Write a workdir file (diff Undo/Redo)."""
    try:
        wd = resolve_workdir(body.workdir or "demos")
        if not wd.is_dir():
            raise HTTPException(status_code=400, detail=f"workdir not found: {wd}")
        return write_workdir_file(wd, body.path, body.content)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/fs/delete")
def fs_delete(body: DeleteFileRequest) -> dict[str, Any]:
    """Delete a workdir file (undo a newly created file)."""
    try:
        wd = resolve_workdir(body.workdir or "demos")
        if not wd.is_dir():
            raise HTTPException(status_code=400, detail=f"workdir not found: {wd}")
        return delete_workdir_file(wd, body.path)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/fs/view")
def fs_view(
    workdir: str = Query(default="demos"),
    path: str = Query(..., min_length=1),
) -> HTMLResponse:
    """Simple standalone window to display a file."""
    try:
        wd = resolve_workdir(workdir)
        data = read_workdir_file(wd, path)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    safe_path = (
        str(data["path"])
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="UTF-8"/><title>{safe_path}</title>
<style>
html,body{{margin:0;height:100%;background:#1e1e1e;overflow:hidden;font-family:Consolas,monospace}}
.bar{{display:flex;align-items:center;gap:10px;padding:8px 12px;background:#3c3c3c;color:#ddd;font-size:13px}}
#ed{{position:absolute;inset:36px 0 22px 0}}
.st{{position:absolute;left:0;right:0;bottom:0;padding:4px 12px;background:#007acc;color:#fff;font-size:12px}}
</style>
<script src="https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min/vs/loader.js"></script>
</head>
<body>
<div class="bar">{safe_path}</div>
<div id="ed"></div>
<div class="st">UTF-8 · Monaco · VS Code engine</div>
<script>
const CONTENT = {json.dumps(data["content"])};
require.config({{ paths: {{ vs: "https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min/vs" }} }});
require(["vs/editor/editor.main"], function () {{
  const ext = {json.dumps(str(data["path"]).rsplit(".", 1)[-1].lower() if "." in str(data["path"]) else "")};
  const langMap = {{py:"python",js:"javascript",ts:"typescript",json:"json",md:"markdown",css:"css",html:"html",htm:"html"}};
  monaco.editor.create(document.getElementById("ed"), {{
    value: CONTENT,
    language: langMap[ext] || "plaintext",
    theme: "vs-dark",
    readOnly: true,
    automaticLayout: true,
    minimap: {{ enabled: true }},
    fontSize: 13,
    fontFamily: "Consolas, monospace",
  }});
}});
</script>
</body></html>"""
    return HTMLResponse(html)


@app.post("/api/approve")
def approve_tool(body: ApproveRequest) -> dict[str, Any]:
    """Resolve a pending High/Medium tool approval from the Web UI."""
    with _bridge_lock:
        bridge = _active_bridge
    if bridge is None:
        raise HTTPException(status_code=409, detail="no active run awaiting approval")
    result = bridge.resolve(body.request_id.strip(), body.allowed)
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result.get("error") or "approve failed")
    return result


@app.post("/api/run")
async def run_stream(body: RunRequest) -> StreamingResponse:
    task = body.task.strip()
    if not task:
        raise HTTPException(status_code=400, detail="task is required")

    if not _run_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Another agent run is already in progress")

    workdir = body.workdir or "demos"
    try:
        wd_path = resolve_workdir(workdir)
    except OSError as exc:
        _run_lock.release()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not wd_path.is_dir():
        _run_lock.release()
        raise HTTPException(status_code=400, detail=f"workdir not found: {wd_path}")

    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()
    session_id = body.session_id
    ask_min_risk = body.ask_min_risk

    def emit(event: dict[str, Any]) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, event)

    def worker() -> None:
        global _active_bridge
        bridge = ApprovalBridge(emit)
        with _bridge_lock:
            _active_bridge = bridge
        try:
            emit(
                {
                    "type": "start",
                    "task": task,
                    "workdir": str(wd_path),
                    "session_id": session_id,
                    "approval": "ask",
                    "ask_min_risk": ask_min_risk,
                }
            )

            def on_event(event: dict[str, Any]) -> None:
                emit(event)

            def log(msg: str) -> None:
                emit({"type": "log", "text": msg})

            result, config, transcript_path = run_coding_task(
                task=task,
                workdir=wd_path,
                model=body.model,
                max_steps=body.max_steps,
                approval="ask",
                save_run_transcript=True,
                log=log,
                on_event=on_event,
                session_id=session_id,
                ask_fn=bridge.ask,
                ask_min_risk=ask_min_risk,
                deny_high=False,
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
                    "session_id": session_id,
                }
            )
        except Exception as exc:  # noqa: BLE001
            emit({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
        finally:
            bridge.close()
            with _bridge_lock:
                if _active_bridge is bridge:
                    _active_bridge = None
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
