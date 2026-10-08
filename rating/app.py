import asyncio
import csv
import io
import json
import os
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import store
os.environ.setdefault("MPLCONFIGDIR", str(store.ROOT / "matplotlib"))
from .charts import render, summary
from .mortal import MortalBrowser
from .reports import AnalysisError, parse_html_report, parse_json_report, log_matches
from .source import AmbiguousPlayer, PaipuSource, SourceError

WEB = Path(__file__).parent / "static"
QUEUE = asyncio.Queue()
ACTIVE = {"id": None}


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def load(job_id):
    if not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise HTTPException(404, "任务不存在。")
    job = store.read_job(job_id)
    if job is None:
        raise HTTPException(404, "任务不存在。")
    return job


def finish(job):
    job["status"] = "done" if all(g["status"] == "done" for g in job["games"]) else "partial" if any(g["status"] == "done" for g in job["games"]) else "failed"
    job["updated_at"] = timestamp()
    if job["status"] == "done":
        job["error"] = None
    store.save_job(job)


async def run_job(job_id):
    job = load(job_id)
    if job["status"] == "cancelled":
        return
    cancelled = lambda: store.read_job(job_id).get("status") == "cancelled"
    job["status"], job["error"] = "fetching", None
    store.save_job(job)
    try:
        source = PaipuSource()
        if not job["games"]:
            job["player"] = await source.find_player(job["name"], job.get("player_id"))
            job["games"] = await source.recent(job["player"], job["count"], job["hanchan_only"])
        if cancelled():
            return
        if not job["games"]:
            raise SourceError("该用户没有符合条件的四人麻将牌谱。")
        job["status"] = "analyzing"
        if len(job["games"]) < job["count"]:
            job["warning"] = f"牌谱屋仅返回 {len(job['games'])} 场符合条件的记录（请求 {job['count']} 场）。"
        store.save_job(job)
        pending = []
        for game in job["games"]:
            if game["status"] == "done":
                continue
            cache = store.cache_path(game)
            if not job["refresh"] and cache.exists():
                try:
                    cached = json.loads(cache.read_text())
                    # 防止损坏缓存被算成分析成功。
                    assert 0 <= cached["rating"] <= 100 and 0 <= cached["agreement"] <= 100 and cached["total_reviewed"] > 0 and cached["player_id"] == game["seat"]
                    game.update(status="done", analysis=cached, error=None, cached=True)
                    continue
                except (ValueError, KeyError, TypeError, AssertionError):
                    pass
            if game["masked"] or game["mode_id"] in (8, 11, 15):
                game.update(status="failed", error="牌谱 UUID 已隐藏，无法自动分析。" if game["masked"] else "当前 Mortal 引擎仅支持四人南，无法分析此四人东场。")
            else:
                pending.append(game)
        store.save_job(job)
        if pending:
            try:
                async with MortalBrowser() as mortal:
                    for game in pending:
                        if cancelled():
                            return
                        game["status"] = "analyzing"
                        store.save_job(job)
                        try:
                            analysis, html = await mortal.analyze(game, cancelled)
                            if cancelled():
                                return
                            game.update(status="done", analysis=analysis, error=None, cached=False)
                            store.write_json(store.cache_path(game), analysis)
                            (store.ROOT / "reports" / f"{job_id}-{job['games'].index(game)}.html").write_text(html, encoding="utf-8")
                        except AnalysisError as exc:
                            game.update(status="failed", error=str(exc))
                            # 访问受阻时停止该批请求；仍保留其他已成功场次。
                            if any(s in str(exc) for s in ("网络", "代理 CA", "限流", "人机验证", "HTTP 403", "输入框", "提交按钮", "未公开 Rating")):
                                for remaining in pending:
                                    if remaining["status"] == "pending":
                                        remaining.update(status="failed", error=f"批次已暂停：{exc}")
                                job["error"] = str(exc)
                                break
                        if cancelled():
                            return
                        job["updated_at"] = timestamp()
                        store.save_job(job)
            except AnalysisError as exc:
                for game in pending:
                    if game["status"] != "done":
                        game.update(status="failed", error=str(exc))
                job["error"] = str(exc)
        if not cancelled():
            finish(job)
    except AmbiguousPlayer as exc:
        job.update(status="needs_player", candidates=exc.candidates, error=str(exc))
        if not cancelled():
            store.save_job(job)
    except (SourceError, AnalysisError) as exc:
        job.update(status="failed", error=str(exc))
        if not cancelled():
            store.save_job(job)


async def worker():
    while True:
        job_id = await QUEUE.get()
        ACTIVE["id"] = job_id
        try:
            await run_job(job_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            job = load(job_id)
            if job["status"] != "cancelled":
                job.update(status="failed", error="任务发生内部错误，请查看服务器日志并重试。")
                store.save_job(job)
            import logging
            logging.exception("Analysis task failed")
        finally:
            ACTIVE["id"] = None
            QUEUE.task_done()


@asynccontextmanager
async def lifespan(app):
    store.initialize()
    # 浏览器进程不跨重启存活；历史任务仍可查看，手动重试避免意外重复提交。
    for path in (store.ROOT / "jobs").glob("*.json"):
        job = json.loads(path.read_text())
        if job["status"] in ("queued", "fetching", "analyzing"):
            job.update(status="interrupted", error="服务重启中断了此任务，可重试未完成场次。")
            for game in job["games"]:
                if game["status"] == "analyzing":
                    game["status"] = "pending"
            store.save_job(job)
    task = asyncio.create_task(worker())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="雀魂 Rating 笔记", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=WEB), name="static")


class Query(BaseModel):
    query: str = Field(min_length=1, max_length=100)
    player_id: int | None = Field(default=None, ge=1)
    hanchan_only: bool = True
    refresh: bool = False


@app.get("/")
async def index():
    return FileResponse(WEB / "index.html")


@app.get("/api/health")
async def health():
    return {"status": "ok", "active_job": ACTIVE["id"], "queued": QUEUE.qsize()}


@app.get("/api/jobs")
async def jobs():
    paths = sorted((store.ROOT / "jobs").glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:30]
    return [{k: job.get(k) for k in ("id", "name", "count", "status", "created_at")} for job in [json.loads(p.read_text()) for p in paths]]


@app.post("/api/jobs", status_code=202)
async def create(query: Query):
    parts = query.query.strip().rsplit(None, 1)
    if len(parts) != 2 or not re.fullmatch(r"[0-9]{1,3}", parts[1]) or not 1 <= int(parts[1]) <= 200:
        raise HTTPException(422, "请输入“用户名 场数”，例如 fioq421 20；场数为 1–200。")
    if QUEUE.qsize() >= 20:
        raise HTTPException(429, "等待任务较多，请稍后再提交。")
    job = {"id": uuid.uuid4().hex, "name": parts[0], "count": int(parts[1]), "player_id": query.player_id, "hanchan_only": True, "refresh": query.refresh, "created_at": timestamp(), "updated_at": timestamp(), "status": "queued", "player": None, "games": [], "error": None, "warning": None}
    store.save_job(job)
    await QUEUE.put(job["id"])
    return job


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    job = load(job_id)
    return {**job, "summary": summary(job)}


@app.post("/api/jobs/{job_id}/cancel")
async def cancel(job_id: str):
    job = load(job_id)
    if job["status"] in ("queued", "fetching", "analyzing"):
        job["status"] = "cancelled"
        for game in job["games"]:
            if game["status"] in ("pending", "analyzing"):
                game["status"] = "cancelled"
        store.save_job(job)
    return job


@app.post("/api/jobs/{job_id}/retry", status_code=202)
async def retry(job_id: str):
    job = load(job_id)
    if job["status"] in ("queued", "fetching", "analyzing") or ACTIVE["id"] == job_id:
        raise HTTPException(409, "任务仍在执行，请等待结束。")
    for game in job["games"]:
        if game["status"] != "done":
            game.update(status="pending", error=None)
    job.update(status="queued", error=None)
    store.save_job(job)
    await QUEUE.put(job_id)
    return job


@app.post("/api/jobs/{job_id}/games/{index}/import")
async def import_report(job_id: str, index: int, request: Request):
    job = load(job_id)
    if job["status"] in ("queued", "fetching", "analyzing") or ACTIVE["id"] == job_id:
        raise HTTPException(409, "请等待当前任务结束后导入报告。")
    if not 0 <= index < len(job["games"]):
        raise HTTPException(404, "比赛不存在。")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 10 * 1024 * 1024:
            raise HTTPException(413, "报告不能超过 10 MB。")
    game = job["games"][index]
    try:
        text = body.decode("utf-8-sig")
        analysis = parse_json_report(json.loads(text), game["seat"]) if text.lstrip().startswith("{") else parse_html_report(text, game["seat"])
        if not log_matches(game.get("uuid"), analysis.get("log_id")):
            raise AnalysisError("报告牌谱 ID 与此场不匹配或无法验证；请保留 mjai-reviewer 报告中的 log_id 元数据。")
    except (AnalysisError, ValueError, TypeError, UnicodeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    game.update(status="done", analysis=analysis, error=None, cached=False)
    # 导入报告只归属此任务；不将未经模型选择确认的报告写入网站模型缓存。
    finish(job)
    return {"status": "ok"}


@app.get("/api/jobs/{job_id}/chart.{fmt}")
async def chart(job_id: str, fmt: str):
    if fmt not in ("png", "svg"):
        raise HTTPException(404)
    job = load(job_id)
    if not job["games"]:
        raise HTTPException(409, "还没有比赛记录。")
    data = await asyncio.to_thread(render, job, fmt)
    return Response(data, media_type="image/png" if fmt == "png" else "image/svg+xml", headers={"Cache-Control": "no-store"})


@app.get("/api/jobs/{job_id}/export.{fmt}")
async def export(job_id: str, fmt: str):
    job = load(job_id)
    if fmt == "json":
        data = json.dumps({**job, "summary": summary(job)}, ensure_ascii=False, indent=2, allow_nan=False)
        media = "application/json"
    elif fmt == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["game_id", "time_utc", "mode", "seat", "rank", "pt", "status", "rating", "agreement", "bad10", "bad5", "model_tag", "error"])
        for g in job["games"]:
            a = g.get("analysis") or {}
            values = [g["game_id"], datetime.fromtimestamp(g["start_time"], timezone.utc).isoformat(), g["mode"], g["seat"], g["rank"], g.get("pt"), g["status"], *[a.get(k) for k in ("rating", "agreement", "bad10", "bad5", "model_tag")], g.get("error")]
            # 防止错误信息、模型名或上游文本被电子表格当公式执行。
            writer.writerow(["'" + v if isinstance(v, str) and v.startswith(("=", "+", "-", "@")) else v for v in values])
        data = "\ufeff" + output.getvalue()
        media = "text/csv"
    else:
        raise HTTPException(404)
    return Response(data, media_type=media, headers={"Content-Disposition": f'attachment; filename="rating-{job_id}.{fmt}"'})
