"""考研背单词 · FastAPI backend (full stage 1-5).

This file only wires the application: lifespan, CORS, validation handler and
static frontend. Business endpoints live in `routers/`, the progress state
machine in `study_state.py`, and the weekly publisher in `scheduler.py`.

Rules that must never be broken (see docs/design-decisions.md):
  - Progress lives ONLY in MySQL. The frontend never keeps a cursor or index.
  - The next item is found by querying pending rows ordered by cycle_no -> round -> id.
  - Progress update and answer_log insertion share ONE transaction.
  - Every user-data endpoint resolves its identity from the session cookie,
    never from anything the browser sends as a user id.
  - Articles and weekly reading never write back to `progress`.
"""

import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import dict_lookup
from auth import router as auth_router
from db import init_schema, transaction
from routers import admin, articles, core, reading, study, tutor
from scheduler import WeeklyPublisher
from seed import ensure_words

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "frontend" / "dist"
publisher = WeeklyPublisher()


@asynccontextmanager
async def lifespan(app):
    init_schema()
    with transaction() as conn:
        ensure_words(conn)
    publisher.start()
    # Warm the ECDICT dictionary in the background so the first "show every
    # word" click is instant instead of paying the ~1-2s CSV parse inline.
    threading.Thread(target=dict_lookup.warm, daemon=True, name="dict-warm").start()
    try:
        yield
    finally:
        publisher.stop()


app = FastAPI(title="考研背单词", version="1.0.0-full", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)
app.include_router(auth_router)
for module in (core, study, articles, reading, admin, tutor):
    app.include_router(module.router)


@app.exception_handler(RequestValidationError)
async def validation_handler(request, exc):
    return JSONResponse(status_code=400, content={"detail": "请求参数不合法"})


# ---------------------------------------------------------------- static frontend

# 管理端是同一份构建产物、另一套外壳：/admin 直接回 index.html，前端按路径前缀
# 决定挂学员端还是管理端。这样不用维护第二个构建，也不会让管理入口出现在底栏。
@app.get("/admin")
def admin_page():
    index = DIST / "index.html"
    if index.is_file():
        return FileResponse(str(index))
    return HTMLResponse("<meta charset='utf-8'><h3>前端还没构建</h3>", status_code=200)


if DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(DIST), html=True), name="frontend")
else:
    @app.get("/")
    def no_frontend():
        return HTMLResponse(
            "<meta charset='utf-8'><h3>前端还没构建</h3>"
            "<p>请先双击运行项目根目录的 setup.bat，或进入 frontend 目录执行 "
            "<code>npm install</code> 与 <code>npm run build</code>。</p>",
            status_code=200,
        )
