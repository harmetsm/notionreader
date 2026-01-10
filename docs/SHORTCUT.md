# iOS Shortcut: Add Book to Notion

This shortcut uses your backend to search Google Books and add the selected result to Notion.

## Inputs
- Share sheet from Safari or any app with a URL or text.

## Steps
1. **Receive** input from Share Sheet (Type: URL or Text).
2. **Get Details of Safari Web Page** (Name) if input is a URL. Otherwise use the text.
3. **Set Variable** `Query` to the page title or shared text.
4. **Get Contents of URL**
   - Method: `GET`
   - URL: `https://YOUR_BACKEND_DOMAIN/search?q=${Query}`
   - Headers: `Accept: application/json`
5. **Get Dictionary Value** from `results`.
6. **Choose from List** (use item title for display).
7. **Get Dictionary Value** for selected item fields.
8. **Get Contents of URL**
   - Method: `POST`
   - URL: `https://YOUR_BACKEND_DOMAIN/add`
   - Headers: `Content-Type: application/json`
   - Request Body: JSON with the selected book fields.
9. **Show Notification** “Added to Notion ✅”.

## JSON payload for step 8
```json
{
  "title": "{{Title}}",
  "authors": ["{{Author 1}}"],
  "isbn": "{{ISBN}}",
  "published": "{{Published}}",
  "cover_url": "{{Cover URL}}",
  "google_books_id": "{{Google Books ID}}",
  "status": "Want to Read"
}
```

Tip: For multiple authors, build an array from the `authors` list in the search response.
