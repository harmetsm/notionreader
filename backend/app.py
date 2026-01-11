import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    load_dotenv = None

if load_dotenv:
    load_dotenv()

NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")
GOOGLE_BOOKS_API_KEY = os.getenv("GOOGLE_BOOKS_API_KEY")
API_KEY = os.getenv("API_KEY")
RATE_LIMIT_PER_MIN = int(os.getenv("RATE_LIMIT_PER_MIN", "60"))
NOTION_AUTHOR_DB_ID = os.getenv("NOTION_AUTHOR_DB_ID")

AUTHOR_RELATION_PROPERTY_NAME = "Author"

NOTION_VERSION = "2022-06-28"
GOOGLE_BOOKS_URL = "https://www.googleapis.com/books/v1/volumes"
NOTION_PAGES_URL = "https://api.notion.com/v1/pages"
NOTION_DATABASE_QUERY_URL = "https://api.notion.com/v1/databases/{database_id}/query"
NOTION_DATABASE_URL = "https://api.notion.com/v1/databases/{database_id}"

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
    mainCategory: Optional[str] = None
    categories: List[str] = []
    page_count: Optional[int] = None
    description: Optional[str] = None
    publisher: Optional[str] = None
    notes: Optional[str] = None


_rate_bucket: Dict[str, List[float]] = {}
_database_title_prop_cache: Dict[str, str] = {}
_database_schema_cache: Dict[str, Dict[str, str]] = {}


def _notion_headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _notion_query_url(database_id: str) -> str:
    return NOTION_DATABASE_QUERY_URL.format(database_id=database_id)


def _notion_database_url(database_id: str) -> str:
    return NOTION_DATABASE_URL.format(database_id=database_id)


async def _get_database_title_property_name(client: httpx.AsyncClient, database_id: str) -> Optional[str]:
    cached = _database_title_prop_cache.get(database_id)
    if cached:
        return cached

    response = await client.get(_notion_database_url(database_id), headers=_notion_headers())
    if response.status_code >= 400:
        logger.warning("Database fetch error: %s %s", response.status_code, response.text)
        return None

    properties = (response.json() or {}).get("properties", {}) or {}
    for prop_name, prop in properties.items():
        if (prop or {}).get("type") == "title":
            _database_title_prop_cache[database_id] = prop_name
            return prop_name
    return None


async def _get_database_schema(client: httpx.AsyncClient, database_id: str) -> Dict[str, str]:
    cached = _database_schema_cache.get(database_id)
    if cached is not None:
        return cached

    response = await client.get(_notion_database_url(database_id), headers=_notion_headers())
    if response.status_code >= 400:
        logger.warning("Database fetch error: %s %s", response.status_code, response.text)
        _database_schema_cache[database_id] = {}
        return {}

    properties = (response.json() or {}).get("properties", {}) or {}
    schema = {prop_name: (prop or {}).get("type", "") for prop_name, prop in properties.items()}
    _database_schema_cache[database_id] = schema
    return schema


def _set_rich_text(properties: Dict[str, Any], schema: Dict[str, str], name: str, value: Optional[str]) -> None:
    if not value:
        return
    if schema.get(name) != "rich_text":
        return
    properties[name] = {"rich_text": [{"text": {"content": value}}]}


def _to_notion_date_start(value: str) -> Optional[str]:
    value = value.strip()
    if re.fullmatch(r"\d{4}", value):
        return f"{value}-01-01"
    if re.fullmatch(r"\d{4}-\d{2}", value):
        return f"{value}-01"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return value
    return None


def _set_date(properties: Dict[str, Any], schema: Dict[str, str], name: str, value: Optional[str]) -> None:
    if not value:
        return
    if schema.get(name) != "date":
        return
    start = _to_notion_date_start(value)
    if not start:
        return
    properties[name] = {"date": {"start": start}}


def _set_url(properties: Dict[str, Any], schema: Dict[str, str], name: str, value: Optional[str]) -> None:
    if not value:
        return
    if schema.get(name) != "url":
        return
    properties[name] = {"url": value}


def _set_multi_select(properties: Dict[str, Any], schema: Dict[str, str], name: str, values: List[str]) -> None:
    values = [v for v in values if v]
    if not values:
        return
    if schema.get(name) != "multi_select":
        return
    properties[name] = {"multi_select": [{"name": v} for v in values]}


def _set_number(properties: Dict[str, Any], schema: Dict[str, str], name: str, value: Optional[int]) -> None:
    if value is None:
        return
    if schema.get(name) != "number":
        return
    properties[name] = {"number": value}


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
        "publisher": info.get("publisher"),
        "mainCategory": info.get("mainCategory"),
        "categories": info.get("categories", []) or [],
        "page_count": info.get("pageCount"),
    }


def _notion_cover(url: str) -> Dict[str, Any]:
    return {"type": "external", "external": {"url": url}}


def _build_book_page_payload(payload: AddBookPayload, schema: Dict[str, str], title_prop: str) -> Dict[str, Any]:
    properties: Dict[str, Any] = {title_prop: {"title": [{"text": {"content": payload.title}}]}}

    description = payload.description
    if description:
        description = description.strip()
        if len(description) > 1800:
            description = f"{description[:1797]}..."

    _set_rich_text(properties, schema, "Summary", description)
    _set_rich_text(properties, schema, "Genre", payload.mainCategory)
    _set_multi_select(properties, schema, "Tropes", payload.categories)
    _set_date(properties, schema, "Publication Date", payload.published)
    _set_rich_text(properties, schema, "Publication Date", payload.published)
    _set_rich_text(properties, schema, "ISBN", payload.isbn)
    _set_rich_text(properties, schema, "Google Books ID", payload.google_books_id)
    _set_rich_text(properties, schema, "Publisher", payload.publisher)
    _set_number(properties, schema, "Total Pages", payload.page_count)

    page_payload: Dict[str, Any] = {"properties": properties}
    if payload.cover_url:
        page_payload["cover"] = _notion_cover(payload.cover_url)
    return page_payload


async def _attach_author_relations(
    client: httpx.AsyncClient,
    properties: Dict[str, Any],
    authors: List[str],
) -> None:
    if not NOTION_AUTHOR_DB_ID or not authors:
        return
    author_ids = await _get_or_create_author_ids(client, authors)
    if author_ids:
        properties[AUTHOR_RELATION_PROPERTY_NAME] = {
            "relation": [{"id": author_id} for author_id in author_ids]
        }


async def _get_or_create_author_ids(client: httpx.AsyncClient, authors: List[str]) -> List[str]:
    if not NOTION_AUTHOR_DB_ID or not authors:
        return []

    headers = _notion_headers()

    author_ids: List[str] = []
    title_prop = await _get_database_title_property_name(client, NOTION_AUTHOR_DB_ID)
    if not title_prop:
        logger.warning("Could not resolve author title property name; skipping author linking.")
        return []

    for name in authors:
        query_payload = {
            "filter": {
                "property": title_prop,
                "title": {"equals": name},
            }
        }
        query_url = _notion_query_url(NOTION_AUTHOR_DB_ID)
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
                title_prop: {"title": [{"text": {"content": name}}]}
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

    async with httpx.AsyncClient(timeout=10) as client:
        schema = await _get_database_schema(client, NOTION_DATABASE_ID)
        title_prop = await _get_database_title_property_name(client, NOTION_DATABASE_ID) or "Name"
        page_payload = _build_book_page_payload(payload, schema, title_prop)
        await _attach_author_relations(client, page_payload["properties"], payload.authors)

        notion_payload: Dict[str, Any] = {"parent": {"database_id": NOTION_DATABASE_ID}, **page_payload}

        response = await client.post(NOTION_PAGES_URL, json=notion_payload, headers=_notion_headers())

    if response.status_code >= 400:
        logger.warning("Notion API error: %s %s", response.status_code, response.text)
        raise HTTPException(status_code=502, detail="Notion API error")

    return {"status": "added", "notion_id": response.json().get("id")}
