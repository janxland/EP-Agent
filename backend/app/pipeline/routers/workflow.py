"""
工作流模板 API (v2.0)
"""
from __future__ import annotations
import asyncio
import json as _json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.pipeline import db as _db

router = APIRouter()


def _deserialize_template(t: dict) -> dict:
    """反序列化 steps/variables 字段"""
    for key in ("steps", "variables"):
        raw = t.get(key, "[]")
        if isinstance(raw, str):
            try: t[key] = _json.loads(raw)
            except Exception: t[key] = []
    for step in t.get("steps", []):
        at = step.get("args_template")
        if isinstance(at, str):
            try: step["args_template"] = _json.loads(at)
            except Exception: step["args_template"] = {}
    return t


@router.post("/traces/{trace_id}/extract-workflow")
async def extract_workflow(trace_id: str, use_llm: bool = True):
    try:
        from app.agentcore.workflow_extractor import WorkflowExtractor
        result = await WorkflowExtractor().extract(trace_id=trace_id, use_llm=use_llm)
        if result.get("error"):
            raise HTTPException(400, result["error"])
        return {"ok": True, **result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/workflows")
async def list_workflows(domain: str = "", limit: int = 50):
    try:
        templates = _db.list_workflow_templates(domain=domain, limit=limit)
        return {"ok": True, "templates": [_deserialize_template(t) for t in templates]}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/workflows/stats/summary")
async def workflow_stats():
    try:
        templates = _db.list_workflow_templates(limit=1000)
        counts    = _db.get_workflow_run_counts()
        return {"ok": True, "total_templates": len(templates),
                "total_runs": counts["total"], "succeeded_runs": counts["succeeded"],
                "failed_runs": counts["failed"], "avg_duration_ms": counts["avg_duration_ms"]}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/workflows/{template_id}")
async def get_workflow(template_id: str):
    try:
        t = _db.get_workflow_template(template_id)
        if not t:
            raise HTTPException(404, f"workflow template {template_id} not found")
        return {"ok": True, "template": _deserialize_template(t)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


class RunWorkflowRequest(BaseModel):
    session_id: str  = ""
    variables:  dict = {}
    dry_run:    bool = False


@router.post("/workflows/{template_id}/run")
async def run_workflow(template_id: str, req: RunWorkflowRequest = RunWorkflowRequest()):
    try:
        from app.pipeline import service as _svc
        from app.agentcore.workflow_runner import WorkflowRunner

        session_id = req.session_id
        if not session_id:
            new_sess   = _svc.create_session(title=f"[工作流] {template_id[:12]}")
            session_id = new_sess.id

        async def event_generator():
            queue: asyncio.Queue = asyncio.Queue()

            async def publish(evt_type: str, payload: dict):
                await queue.put((evt_type, payload))

            async def run_and_signal():
                try:
                    await WorkflowRunner().run(
                        template_id=template_id, session_id=session_id,
                        variables=req.variables, publish=publish, dry_run=req.dry_run,
                    )
                except asyncio.CancelledError:
                    await queue.put(("workflow.cancelled", {"message": "任务已取消"}))
                except Exception as e:
                    await queue.put(("workflow.error", {"error": str(e)}))
                finally:
                    await queue.put(None)

            task = asyncio.create_task(run_and_signal())
            try:
                while True:
                    item = await queue.get()
                    if item is None:
                        break
                    evt_type, payload = item
                    yield f"data: {_json.dumps({'type': evt_type, **payload}, ensure_ascii=False)}\n\n"
                yield 'data: {"type": "done"}\n\n'
            finally:
                if not task.done():
                    task.cancel()
                    try: await task
                    except (asyncio.CancelledError, Exception): pass

        return StreamingResponse(event_generator(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/workflows/{template_id}/runs")
async def list_workflow_runs(template_id: str, limit: int = 20):
    try:
        return {"ok": True, "runs": _db.list_workflow_runs(template_id=template_id, limit=limit)}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/workflow-runs/{run_id}")
async def get_workflow_run_detail(run_id: str):
    try:
        run = _db.get_workflow_run(run_id)
        if not run:
            raise HTTPException(404, f"workflow run {run_id} not found")
        return {"ok": True, "run": run, "step_logs": _db.get_workflow_step_logs(run_id)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/workflow-runs/{run_id}/cancel")
async def cancel_workflow_run(run_id: str):
    try:
        run = _db.get_workflow_run(run_id)
        if not run:
            raise HTTPException(404, f"workflow run {run_id} not found")
        if run.get("status") == "running":
            import datetime
            _db.update_workflow_run(run_id, {"status": "cancelled",
                                             "ended_at": datetime.datetime.now().isoformat()})
        return {"ok": True, "run_id": run_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/workflows/{template_id}/deprecate")
async def deprecate_workflow(template_id: str):
    try:
        t = _db.get_workflow_template(template_id)
        if not t:
            raise HTTPException(404, f"workflow template {template_id} not found")
        _db.update_workflow_template_status(template_id, "deprecated")
        return {"ok": True, "template_id": template_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))
