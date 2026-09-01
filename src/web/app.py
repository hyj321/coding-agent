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
from src.web.user_ask import UserAskBridge
from src.agent.steer import SteerInbox
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
from src.agent.capabilities import build_capability_snapshot, load_runtime_policies
from src.agent.styles import (
    delete_style_card,
    format_active_styles_preamble,
    get_style_card,
    list_style_cards,
    save_style_card,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
ASSET_VERSION = "20260831nosuggest1"

app = FastAPI(title="CodeAgent", version="0.5.0")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

_run_lock = threading.Lock()
_active_bridge: ApprovalBridge | None = None
_active_ask_bridge: UserAskBridge | None = None
_active_cancel: threading.Event | None = None
_active_steer: Any | None = None
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
    style_ids: list[str] = Field(
        default_factory=list,
        description="Style card ids to inject as Active style cards preamble.",
    )


class StyleUpsertRequest(BaseModel):
    workdir: str | None = "demos"
    id: str = Field(..., min_length=1, max_length=64)
    title: str | None = None
    description: str = ""
    body: str = Field(..., min_length=1)
    kind: str = "writing"
    overwrite: bool = True


class StyleDeleteRequest(BaseModel):
    workdir: str | None = "demos"
    id: str = Field(..., min_length=1, max_length=64)


class ApproveRequest(BaseModel):
    request_id: str = Field(..., min_length=4, max_length=64)
    allowed: bool


class AskReplyRequest(BaseModel):
    request_id: str = Field(..., min_length=4, max_length=64)
    answer: str = Field(..., min_length=1, max_length=8000)


class SteerRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)


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
    policies = load_runtime_policies()
    return {
        "default_workdir": str(demos if demos.is_dir() else PROJECT_ROOT),
        "project_root": str(PROJECT_ROOT),
        "asset_version": ASSET_VERSION,
        "policies": policies,
        "features": {
            "fs_write": True,
            "fs_delete": True,
            "attach_drop": True,
            "approval": True,
            "stop": True,
            "steer": True,
            "capability_panel": True,
            "ask_user": True,
            "style_cards": True,
        },
        "suggestions": [],
    }


@app.get("/api/capabilities")
def capabilities(workdir: str = Query(default="demos")) -> dict[str, Any]:
    """T1: workdir + registered tools + runtime policies (no API key)."""
    try:
        wd = resolve_workdir(workdir)
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not wd.is_dir():
        raise HTTPException(status_code=400, detail=f"workdir not found: {wd}")
    return build_capability_snapshot(wd)


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


@app.get("/api/styles")
def styles_list(workdir: str = Query(default="demos")) -> dict[str, Any]:
    try:
        wd = resolve_workdir(workdir)
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not wd.is_dir():
        raise HTTPException(status_code=400, detail=f"workdir not found: {wd}")
    cards = [c.to_dict() for c in list_style_cards(wd)]
    return {"workdir": str(wd), "items": cards}


@app.get("/api/styles/{style_id}")
def styles_get(style_id: str, workdir: str = Query(default="demos")) -> dict[str, Any]:
    try:
        wd = resolve_workdir(workdir)
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        card = get_style_card(wd, style_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if card is None:
        raise HTTPException(status_code=404, detail=f"style not found: {style_id}")
    return card.to_dict()


@app.post("/api/styles")
def styles_upsert(body: StyleUpsertRequest) -> dict[str, Any]:
    try:
        wd = resolve_workdir(body.workdir or "demos")
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        card = save_style_card(
            wd,
            style_id=body.id,
            name=(body.title or body.id).strip(),
            description=body.description,
            body=body.body,
            kind=body.kind,
            overwrite=body.overwrite,
        )
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"ok": True, "card": card.to_dict()}


@app.delete("/api/styles/{style_id}")
def styles_delete(style_id: str, workdir: str = Query(default="demos")) -> dict[str, Any]:
    try:
        wd = resolve_workdir(workdir)
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        ok = delete_style_card(wd, style_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=404, detail=f"style not found: {style_id}")
    return {"ok": True, "deleted": style_id}


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


@app.post("/api/ask_reply")
def ask_reply(body: AskReplyRequest) -> dict[str, Any]:
    """Resolve a pending ask_user from the Web UI."""
    with _bridge_lock:
        bridge = _active_ask_bridge
    if bridge is None:
        raise HTTPException(status_code=409, detail="no active run awaiting ask_user")
    result = bridge.resolve(body.request_id.strip(), body.answer)
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result.get("error") or "ask_reply failed")
    return result


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


@app.post("/api/stop")
def stop_run() -> dict[str, Any]:
    """Cooperatively cancel the in-flight agent run (Stop button)."""
    with _bridge_lock:
        cancel = _active_cancel
        bridge = _active_bridge
        ask_bridge = _active_ask_bridge
    if cancel is None:
        raise HTTPException(status_code=409, detail="no active run to stop")
    cancel.set()
    if bridge is not None:
        bridge.close()
    if ask_bridge is not None:
        ask_bridge.close()
    return {"ok": True, "stopped": True}


@app.post("/api/steer")
def steer_run(body: SteerRequest) -> dict[str, Any]:
    """Queue a mid-run user correction (injected at the next step boundary)."""
    text = body.message.strip()
    if not text:
        raise HTTPException(status_code=400, detail="message is required")
    with _bridge_lock:
        inbox = _active_steer
    if inbox is None:
        raise HTTPException(
            status_code=409,
            detail="没有进行中的任务可纠偏（可能已结束或页面已刷新）。请重新发送任务。",
        )
    if not inbox.push(text):
        raise HTTPException(status_code=400, detail="empty message")
    return {"ok": True, "queued": True, "pending": inbox.pending_count()}


@app.post("/api/run")
async def run_stream(body: RunRequest) -> StreamingResponse:
    task = body.task.strip()
    if not task:
        raise HTTPException(status_code=400, detail="task is required")

    if not _run_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="上一轮任务仍在执行。请先点红色「停止」，或等本轮结束后再发。",
        )

    workdir = body.workdir or "demos"
    try:
        wd_path = resolve_workdir(workdir)
    except OSError as exc:
        _run_lock.release()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not wd_path.is_dir():
        _run_lock.release()
        raise HTTPException(status_code=400, detail=f"workdir not found: {wd_path}")

    effective_task = task
    if body.style_ids:
        preamble = format_active_styles_preamble(wd_path, list(body.style_ids))
        if preamble:
            effective_task = preamble + task

    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()
    session_id = body.session_id
    ask_min_risk = body.ask_min_risk

    def emit(event: dict[str, Any]) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, event)

    def worker() -> None:
        global _active_bridge, _active_ask_bridge, _active_cancel, _active_steer
        cancel_event = threading.Event()
        steer_inbox = SteerInbox()
        bridge = ApprovalBridge(emit, cancel_event=cancel_event)
        ask_bridge = UserAskBridge(emit, cancel_event=cancel_event)

        def ask_user_fn(question: str, call_id: str | None = None) -> str:
            return ask_bridge.ask(question, call_id=call_id)

        with _bridge_lock:
            _active_bridge = bridge
            _active_ask_bridge = ask_bridge
            _active_cancel = cancel_event
            _active_steer = steer_inbox
        try:
            emit(
                {
                    "type": "start",
                    "task": task,
                    "workdir": str(wd_path),
                    "session_id": session_id,
                    "approval": "ask",
                    "ask_min_risk": ask_min_risk,
                    "style_ids": list(body.style_ids or []),
                }
            )

            def on_event(event: dict[str, Any]) -> None:
                emit(event)

            def log(msg: str) -> None:
                emit({"type": "log", "text": msg})

            result, config, transcript_path = run_coding_task(
                task=effective_task,
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
                cancel_event=cancel_event,
                steer_inbox=steer_inbox,
                ask_user_fn=ask_user_fn,
            )
            cost = None
            if isinstance(result.memory, dict):
                cr = result.memory.get("cost_report")
                if isinstance(cr, dict):
                    cost = cr
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
                    "cost_report": cost,
                }
            )
        except Exception as exc:  # noqa: BLE001
            emit({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
        finally:
            bridge.close()
            ask_bridge.close()
            with _bridge_lock:
                if _active_bridge is bridge:
                    _active_bridge = None
                if _active_ask_bridge is ask_bridge:
                    _active_ask_bridge = None
                if _active_cancel is cancel_event:
                    _active_cancel = None
                if _active_steer is steer_inbox:
                    _active_steer = None
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
