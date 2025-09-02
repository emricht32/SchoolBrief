
import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from dotenv import load_dotenv
load_dotenv()

from .db import init_db
from .session import add_session_middleware
from .auth import router as auth_router
from .views import router as views_router
from .billing import router as billing_router
from .scheduler import start_scheduler



app = FastAPI()
add_session_middleware(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(auth_router, prefix="/auth", tags=["auth"])
app.include_router(views_router, tags=["views"])
app.include_router(billing_router, prefix="/billing", tags=["billing"])

@app.on_event("startup")
def on_startup():
    init_db()
    start_scheduler()

@app.get("/healthz")
def healthz():
    return {"ok": True}

@app.get("/favicon.ico")
def favicon():
    return RedirectResponse("/static/favicon.ico")
