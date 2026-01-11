# Notion Book Tracker

Lightweight book tracker that searches Google Books and adds entries to your Notion database. Includes a mobile-friendly embedded search UI and an iOS Shortcut flow.

## Architecture
- FastAPI backend: `/search` (Google Books) + `/add` (Notion)
- Static frontend: mobile-friendly search bar + add button
- iOS Shortcut: share sheet → search → add

## Notion Database Schema
Create a Notion database with these properties (case-sensitive names):
- `Title` (Title)
- `Author` (Rich text)
- `Status` (Select)
- `Genres` (Multi-select)
- `Total Pages` (Number)

Share the database with your Notion integration.

## Backend Setup
```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```
Fill in `.env` with your Notion token and database ID.

Run the API:
```bash
uvicorn app:app --reload --port 8000
```

Logs print to stdout from Uvicorn (requests and warnings).

## Frontend Setup
Update the API base in `frontend/index.html`:
```html
<body data-api-base="https://YOUR_BACKEND_DOMAIN" data-api-key="YOUR_API_KEY">
```

Serve the frontend locally:
```bash
cd frontend
python -m http.server 5173
```
Open `http://localhost:5173` and test the search + add flow.

## Embed in Notion
Host the `frontend` folder on a static host (GitHub Pages, Cloudflare Pages) and paste the URL into a Notion **Embed** block.

## iOS Shortcut
Follow the guide in `docs/SHORTCUT.md`.

## Suggested free hosting
- Backend: Cloudflare Workers (requires small refactor to JS), Fly.io, or Railway free tier
- Frontend: GitHub Pages or Cloudflare Pages

## Basic quota protection
- Set `API_KEY` and `RATE_LIMIT_PER_MIN` in `backend/.env`.
- Update `frontend/index.html` with the same `data-api-key` value.

## Matching your existing Notion schema
This project uses environment variables to map Google Books fields into your database properties.
If a property doesn't exist or has a different type, set the matching `NOTION_*_PROP` to empty or update the name.

Common mappings:
- `NOTION_TITLE_PROP` (default `Title`)
- `NOTION_AUTHOR_PROP` (default `Author`)
- `NOTION_STATUS_PROP` (default `Status`)
- `NOTION_GENRES_PROP` (default `Genres`)
- `NOTION_TOTAL_PAGES_PROP` (default `Total Pages`)

## Author relation support
If your `Author` field is a relation to an Author database, set these in `backend/.env`:
- `NOTION_AUTHOR_RELATION_PROP` (default `Author`)
- `NOTION_AUTHOR_DB_ID` (the author database ID)

When enabled, the API will query for matching author pages and create them if missing.
