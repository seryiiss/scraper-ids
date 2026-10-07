"""Servicio que recorre una página cambiando el id de la URL y guarda los datos en una base de datos."""
from __future__ import annotations

import csv
import io
import json
import os
import threading
import time
from datetime import datetime, timezone
from typing import Optional

import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import Column, DateTime, Integer, String, Text, create_engine, func, select
from sqlalchemy.orm import Session, declarative_base

# --- Configuración (variables de entorno) -----------------------------------
URL_TEMPLATE = os.environ.get("URL_TEMPLATE", "https://example.com/item/{id}")
START_ID = int(os.environ.get("START_ID", "1"))
END_ID = int(os.environ.get("END_ID", "100"))
DELAY_SECONDS = float(os.environ.get("DELAY_SECONDS", "2"))
# {"campo": "selector css"}  ·  "selector@atributo" para leer un atributo (p. ej. "img.foto@src")
SELECTORS = json.loads(os.environ.get("SELECTORS", '{"titulo": "title"}'))
# Para parar tras N ids seguidos sin página (404, etc.). 0 = no parar nunca.
MAX_CONSECUTIVE_MISSES = int(os.environ.get("MAX_CONSECUTIVE_MISSES", "0"))
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
AUTO_START = os.environ.get("AUTO_START", "false").lower() == "true"
USER_AGENT = os.environ.get("USER_AGENT", "Mozilla/5.0 (compatible; id-scraper/1.0)")

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./data.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

# --- Base de datos -----------------------------------------------------------
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
Base = declarative_base()


class Page(Base):
    __tablename__ = "pages"

    id = Column(Integer, primary_key=True, autoincrement=False)
    url = Column(String(2048), nullable=False)
    status_code = Column(Integer)
    data = Column(Text)  # JSON con los campos extraídos
    error = Column(Text)
    scraped_at = Column(DateTime(timezone=True))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "url": self.url,
            "status_code": self.status_code,
            "data": json.loads(self.data) if self.data else None,
            "error": self.error,
            "scraped_at": self.scraped_at.isoformat() if self.scraped_at else None,
        }


Base.metadata.create_all(engine)

# --- Scraper -----------------------------------------------------------------
stop_event = threading.Event()
worker: Optional[threading.Thread] = None
status = {"running": False, "current_id": None, "last_error": None, "started_at": None}


def extract(soup: BeautifulSoup, selector: str) -> Optional[str]:
    css, _, attr = selector.partition("@")
    el = soup.select_one(css.strip())
    if el is None:
        return None
    if attr:
        return el.get(attr.strip())
    return el.get_text(" ", strip=True)


def scrape_one(client: httpx.Client, page_id: int) -> Page:
    url = URL_TEMPLATE.format(id=page_id)
    page = Page(id=page_id, url=url, scraped_at=datetime.now(timezone.utc))
    try:
        r = client.get(url)
        page.status_code = r.status_code
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            fields = {name: extract(soup, sel) for name, sel in SELECTORS.items()}
            page.data = json.dumps(fields, ensure_ascii=False)
    except httpx.HTTPError as e:
        page.error = f"{type(e).__name__}: {e}"
    return page


def next_id() -> int:
    """Continúa donde se quedó: el id siguiente al mayor ya guardado."""
    with Session(engine) as s:
        last = s.scalar(select(func.max(Page.id)).where(Page.id >= START_ID))
    return START_ID if last is None else last + 1


def run_scraper() -> None:
    status.update(running=True, started_at=datetime.now(timezone.utc).isoformat(), last_error=None)
    misses = 0
    try:
        with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=20, follow_redirects=True) as client:
            for page_id in range(next_id(), END_ID + 1):
                if stop_event.is_set():
                    break
                status["current_id"] = page_id
                page = scrape_one(client, page_id)
                with Session(engine) as s:
                    s.merge(page)
                    s.commit()

                misses = 0 if page.status_code == 200 else misses + 1
                if page.error:
                    status["last_error"] = page.error
                if MAX_CONSECUTIVE_MISSES and misses >= MAX_CONSECUTIVE_MISSES:
                    status["last_error"] = f"Parado tras {misses} ids seguidos sin página"
                    break
                stop_event.wait(DELAY_SECONDS)
    except Exception as e:  # que un fallo inesperado quede visible en /status
        status["last_error"] = f"{type(e).__name__}: {e}"
    finally:
        status["running"] = False


def start_worker() -> bool:
    global worker
    if worker and worker.is_alive():
        return False
    stop_event.clear()
    worker = threading.Thread(target=run_scraper, daemon=True)
    worker.start()
    return True


# --- API ---------------------------------------------------------------------
app = FastAPI(title="Scraper por id")


def check_token(token: Optional[str]) -> None:
    if ADMIN_TOKEN and token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Token incorrecto")


@app.on_event("startup")
def on_startup() -> None:
    if AUTO_START:
        start_worker()


@app.get("/")
def get_status():
    with Session(engine) as s:
        total = s.scalar(select(func.count()).select_from(Page))
        ok = s.scalar(select(func.count()).select_from(Page).where(Page.status_code == 200))
    return {
        **status,
        "url_template": URL_TEMPLATE,
        "range": [START_ID, END_ID],
        "next_id": next_id(),
        "saved": total,
        "saved_ok": ok,
    }


@app.post("/start")
def start(x_admin_token: Optional[str] = Header(None)):
    check_token(x_admin_token)
    return {"started": start_worker()}


@app.post("/stop")
def stop(x_admin_token: Optional[str] = Header(None)):
    check_token(x_admin_token)
    stop_event.set()
    return {"stopping": True}


@app.get("/items")
def list_items(limit: int = 50, offset: int = 0, only_ok: bool = True):
    with Session(engine) as s:
        q = select(Page).order_by(Page.id).limit(min(limit, 500)).offset(offset)
        if only_ok:
            q = q.where(Page.status_code == 200)
        return [p.to_dict() for p in s.scalars(q)]


@app.get("/items/{page_id}")
def get_item(page_id: int):
    with Session(engine) as s:
        page = s.get(Page, page_id)
        if not page:
            raise HTTPException(status_code=404, detail="No existe en la base de datos")
        return page.to_dict()


@app.get("/export.csv")
def export_csv():
    def rows():
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["id", "url", *SELECTORS.keys()])
        yield buf.getvalue()
        with Session(engine) as s:
            for p in s.scalars(select(Page).where(Page.status_code == 200).order_by(Page.id)):
                fields = json.loads(p.data) if p.data else {}
                buf.seek(0)
                buf.truncate()
                writer.writerow([p.id, p.url, *(fields.get(k) for k in SELECTORS)])
                yield buf.getvalue()

    return StreamingResponse(rows(), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=datos.csv"})
