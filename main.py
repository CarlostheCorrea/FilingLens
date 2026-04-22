import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from fastapi.responses import HTMLResponse

from routes.scope import router as scope_router
from routes.ingest import router as ingest_router
from routes.answer import router as answer_router
from routes.verify import router as verify_router
from routes.data import router as data_router

app = FastAPI(title="SEC Filing Intelligence Tool", version="1.0.0")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

app.include_router(scope_router)
app.include_router(ingest_router)
app.include_router(answer_router)
app.include_router(verify_router)
app.include_router(data_router)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/health")
async def health():
    return {"status": "ok"}
