const body = document.body;
const API_BASE = body.dataset.apiBase || "http://localhost:8000";
const API_KEY = body.dataset.apiKey || "";

const searchInput = document.getElementById("searchInput");
const searchButton = document.getElementById("searchButton");
const resultsEl = document.getElementById("results");
const cardTemplate = document.getElementById("resultCard");

let debounceTimer;

function setBusy(isBusy) {
  const container = document.querySelector(".results");
  container.setAttribute("aria-busy", isBusy ? "true" : "false");
}

async function fetchBooks(query) {
  const url = new URL(`${API_BASE}/search`);
  url.searchParams.set("q", query);
  const response = await fetch(url.toString(), {
    headers: API_KEY ? { "x-api-key": API_KEY } : {}
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new Error(detail.detail || "Search failed");
  }
  return response.json();
}

async function addBook(payload, statusEl) {
  statusEl.textContent = "Adding...";
  try {
    const response = await fetch(`${API_BASE}/add`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(API_KEY ? { "x-api-key": API_KEY } : {})
      },
      body: JSON.stringify(payload)
    });

    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      throw new Error(detail.detail || "Add failed");
    }

    statusEl.textContent = "Added to Notion";
  } catch (error) {
    statusEl.textContent = error.message || "Could not add";
  }
}

function renderResults(results) {
  resultsEl.innerHTML = "";

  if (!results.length) {
    resultsEl.innerHTML = '<p class="hint">No matches found. Try a different query.</p>';
    return;
  }

  results.forEach((book) => {
    const card = cardTemplate.content.cloneNode(true);
    const coverEl = card.querySelector(".cover");
    const titleEl = card.querySelector(".title");
    const authorsEl = card.querySelector(".authors");
    const metaEl = card.querySelector(".meta");
    const addButton = card.querySelector(".add-button");
    const statusEl = card.querySelector(".status");

    if (book.cover_url) {
      coverEl.style.backgroundImage = `url('${book.cover_url}')`;
    }

    titleEl.textContent = book.title;
    authorsEl.textContent = book.authors.length ? book.authors.join(", ") : "Unknown author";

    const metaParts = [];
    if (book.published) metaParts.push(book.published);
    if (book.isbn) metaParts.push(`ISBN ${book.isbn}`);
    metaEl.textContent = metaParts.join(" · ");

    addButton.addEventListener("click", () => {
    addBook(
        {
          title: book.title,
          authors: book.authors,
          isbn: book.isbn,
          published: book.published,
          cover_url: book.cover_url,
          google_books_id: book.google_books_id,
          categories: book.categories || [],
          page_count: book.page_count || null,
          status: "Want to Read"
        },
        statusEl
      );
    });

    resultsEl.appendChild(card);
  });
}

async function handleSearch() {
  const query = searchInput.value.trim();
  if (!query) return;

  setBusy(true);
  try {
    const data = await fetchBooks(query);
    renderResults(data.results || []);
  } catch (error) {
    resultsEl.innerHTML = `<p class="hint">${error.message || "Search failed."}</p>`;
  } finally {
    setBusy(false);
  }
}

searchButton.addEventListener("click", handleSearch);

searchInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    handleSearch();
  }
});

searchInput.addEventListener("input", () => {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(() => {
    if (searchInput.value.trim().length >= 3) {
      handleSearch();
    }
  }, 450);
});
