import logging
import os
import time
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")
GOOGLE_BOOKS_API_KEY = os.getenv("GOOGLE_BOOKS_API_KEY")
API_KEY = os.getenv("API_KEY")
RATE_LIMIT_PER_MIN = int(os.getenv("RATE_LIMIT_PER_MIN", "60"))
NOTION_AUTHOR_RELATION_PROP = os.getenv("NOTION_AUTHOR_RELATION_PROP", "Author")
NOTION_AUTHOR_DB_ID = os.getenv("NOTION_AUTHOR_DB_ID")
NOTION_AUTHOR_TITLE_PROP = os.getenv("NOTION_AUTHOR_TITLE_PROP", "Name")

NOTION_VERSION = "2022-06-28"
GOOGLE_BOOKS_URL = "https://www.googleapis.com/books/v1/volumes"
NOTION_PAGES_URL = "https://api.notion.com/v1/pages"
NOTION_DATABASE_QUERY_URL = "https://api.notion.com/v1/databases/{database_id}/query"

app = FastAPI(title="Notion Book Tracker")

logger = logging.getLogger("notion_book_tracker")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)


class AddBookPayload(BaseModel):
    title: str
    authors: List[str] = []
    isbn: Optional[str] = None
    published: Optional[str] = None
    cover_url: Optional[str] = None
    google_books_id: Optional[str] = None
    categories: List[str] = []
    page_count: Optional[int] = None
    status: Optional[str] = None
    notes: Optional[str] = None


_rate_bucket: Dict[str, List[float]] = {}


def _require_api_key(request: Request) -> None:
    if not API_KEY:
        return
    supplied = request.headers.get("x-api-key")
    if supplied != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


def _rate_limit(request: Request) -> None:
    if RATE_LIMIT_PER_MIN <= 0:
        return
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    window_start = now - 60
    hits = _rate_bucket.setdefault(ip, [])
    while hits and hits[0] < window_start:
        hits.pop(0)
    if len(hits) >= RATE_LIMIT_PER_MIN:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    hits.append(now)


def _best_isbn(identifiers: List[Dict[str, str]]) -> Optional[str]:
    if not identifiers:
        return None
    for kind in ("ISBN_13", "ISBN_10"):
        for item in identifiers:
            if item.get("type") == kind:
                return item.get("identifier")
    return identifiers[0].get("identifier")


def _normalize_google_book(item: Dict[str, Any]) -> Dict[str, Any]:
    info = item.get("volumeInfo", {})
    identifiers = info.get("industryIdentifiers", []) or []
    image_links = info.get("imageLinks", {}) or {}
    thumbnail = image_links.get("thumbnail") or image_links.get("smallThumbnail")
    if thumbnail:
        thumbnail = thumbnail.replace("http://", "https://")

    return {
        "google_books_id": item.get("id"),
        "title": info.get("title") or "Untitled",
        "authors": info.get("authors", []) or [],
        "published": info.get("publishedDate"),
        "isbn": _best_isbn(identifiers),
        "cover_url": thumbnail,
        "description": info.get("description"),
        "categories": info.get("categories", []) or [],
        "page_count": info.get("pageCount"),
    }


async def _get_or_create_author_ids(authors: List[str]) -> List[str]:
    if not NOTION_AUTHOR_DB_ID or not authors:
        return []

    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }

    author_ids: List[str] = []
    async with httpx.AsyncClient(timeout=10) as client:
        for name in authors:
            query_payload = {
                "filter": {
                    "property": NOTION_AUTHOR_TITLE_PROP,
                    "title": {"equals": name},
                }
            }
            query_url = NOTION_DATABASE_QUERY_URL.format(database_id=NOTION_AUTHOR_DB_ID)
            query_response = await client.post(query_url, json=query_payload, headers=headers)
            if query_response.status_code >= 400:
                logger.warning("Author query error: %s %s", query_response.status_code, query_response.text)
                continue

            results = query_response.json().get("results", [])
            if results:
                author_ids.append(results[0]["id"])
                continue

            create_payload = {
                "parent": {"database_id": NOTION_AUTHOR_DB_ID},
                "properties": {
                    NOTION_AUTHOR_TITLE_PROP: {"title": [{"text": {"content": name}}]}
                },
            }
            create_response = await client.post(NOTION_PAGES_URL, json=create_payload, headers=headers)
            if create_response.status_code >= 400:
                logger.warning("Author create error: %s %s", create_response.status_code, create_response.text)
                continue
            author_ids.append(create_response.json().get("id"))

    return author_ids


@app.get("/health")
async def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/search")
async def search_books(request: Request, q: str = Query(..., min_length=1), max_results: int = 10) -> Dict[str, Any]:
    _require_api_key(request)
    _rate_limit(request)
    params = {
        "q": q,
        "maxResults": max(1, min(max_results, 20))
    }
    if GOOGLE_BOOKS_API_KEY:
        params["key"] = GOOGLE_BOOKS_API_KEY

    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(GOOGLE_BOOKS_URL, params=params)

    if response.status_code != 200:
        logger.warning("Google Books error: %s %s", response.status_code, response.text)
        raise HTTPException(status_code=502, detail="Google Books API error")

    payload = response.json()
    items = payload.get("items", []) or []
    results = [_normalize_google_book(item) for item in items]
    return {"query": q, "results": results}


@app.post("/add")
async def add_book(request: Request, payload: AddBookPayload) -> Dict[str, Any]:
    _require_api_key(request)
    _rate_limit(request)
    if not NOTION_TOKEN or not NOTION_DATABASE_ID:
        raise HTTPException(status_code=500, detail="Notion credentials not configured")

    properties: Dict[str, Any] = {
        "Title": {"title": [{"text": {"content": payload.title}}]},
    }

    if payload.status:
        properties["Status"] = {"status": {"name": payload.status}}

    if payload.categories:
        properties["Genres"] = {
            "multi_select": [{"name": genre} for genre in payload.categories]
        }

    if payload.page_count:
        properties["Total Pages"] = {
            "rich_text": [{"text": {"content": str(payload.page_count)}}]
        }

    if NOTION_AUTHOR_RELATION_PROP and payload.authors and NOTION_AUTHOR_DB_ID:
        author_ids = await _get_or_create_author_ids(payload.authors)
        if author_ids:
            properties[NOTION_AUTHOR_RELATION_PROP] = {
                "relation": [{"id": author_id} for author_id in author_ids]
            }

    notion_payload = {
        "parent": {"database_id": NOTION_DATABASE_ID},
        "properties": properties,
    }

    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(NOTION_PAGES_URL, json=notion_payload, headers=headers)

    if response.status_code >= 400:
        logger.warning("Notion API error: %s %s", response.status_code, response.text)
        raise HTTPException(status_code=502, detail="Notion API error")

    return {"status": "added", "notion_id": response.json().get("id")}
