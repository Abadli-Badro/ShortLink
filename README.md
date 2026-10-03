# ShortLink

Turn a long URL into a shorter link, then redirect visitors to it and count how often it was followed.

Built with Python 3.11, FastAPI, and SQLite.

## Quickstart

```bash
python -m venv .venv
pip install -r requirements.txt      
uvicorn app.main:app --reload        
```

Interactive API docs: <http://127.0.0.1:8000/docs>

The database is a single SQLite file (`shortlink.db` by default, override with the
`SHORTLINK_DB` environment variable). It is created automatically on startup.

```bash
# Create a short link
curl -X POST http://127.0.0.1:8000/links \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/some/very/long/path?with=query"}'
# 201 {"code":"aB3xY9z","url":"...","short_url":"http://127.0.0.1:8000/aB3xY9z","clicks":0,"created_at":"..."}

# Follow it
curl -i http://127.0.0.1:8000/aB3xY9z
# HTTP/1.1 302 Found
# location: https://example.com/some/very/long/path?with=query

# See how often it was followed
curl http://127.0.0.1:8000/links/aB3xY9z
# 200 {"code":"aB3xY9z",...,"clicks":1,...}
```

## API

| Method | Path          | Description                                             |
| ------ | ------------- | ------------------------------------------------------- |
| `POST` | `/links`      | Create a short link. `201` for a new link; `200` with the existing link if that URL was already shortened; `400` if the URL is malformed (nothing is stored). |
| `GET`  | `/{code}`     | Follow the link. Increments the counter, responds `302` with `Location`. Unknown code → `404`. |
| `GET`  | `/links/{code}` | Stats for a link: original URL, click count, creation time. Unknown code → `404`. |
| `GET`  | `/`           | Service name and docs link.                             |

Notes:

- Short codes are 7 characters drawn from `A-Za-z0-9` (62 symbols, ~10^12 combos),
  generated with `secrets` and retried on primary-key collision, so codes never collide.
- A target URL is accepted only if it parses, has an `http`/`https` scheme, and has a
  host. Empty, schemeless, whitespace-containing, `ftp://`, `javascript:`, `data:` and
  overlong (4096+ char) values are rejected with `400` **before** anything is written.
- **One code per URL.** Shortening the same target twice returns the *same* code
  with `200` (instead of `201`), so a retry or a duplicate submit never silently
  mints a second code — the response status tells you which happened. Enforced by
  a `UNIQUE` index on `url`, and duplicate rows from older databases are merged on
  startup (earliest code wins, click counts are added).
- Click counts are incremented with a single atomic `UPDATE ... SET clicks = clicks + 1`,
  so concurrent readers never lose a count.

## CORS

Browser calls from another origin (a frontend on `localhost:3000` talking to the API
on `localhost:8000`) are allowed out of the box: the app installs
`CORSMiddleware`, so every response — including `302`, `400` and `404` — carries
`Access-Control-Allow-Origin`. Without those headers the browser reports a generic
"CORS error" and hides the real status code.

- Default: any origin (`*`), no credentials.
- Lock it down with a comma-separated env var:

  ```bash
  CORS_ORIGINS="http://localhost:3000,https://app.example.com" uvicorn app.main:app
  ```

- `Location` is listed in `Access-Control-Expose-Headers` so JS can read it.

Caveat: with `fetch(shortUrl)` and the default `redirect: "follow"`, the browser
also applies CORS to the **target** site's response after the hop — if that site
sends no CORS headers, the console still shows an error even though the redirect
(and the click count) worked. If you only need the target URL or the count, call
`GET /links/{code}` instead; it returns plain JSON.

### Swagger UI shows "Failed to fetch" / "Undocumented"

Two separate things, both expected:

- **"Undocumented"** appears when the returned status code is missing from the
  OpenAPI spec. Redirect responses used to be published as `307` because
  Starlette's `RedirectResponse.__init__` defaults to 307 while this service
  deliberately sends `302`. The route now pins `status_code=302` and documents
  `302`, `404` and `400` explicitly, so `/docs` matches the wire.
- **"Failed to fetch"** is the browser following our `302` to the third-party
  target and being refused: the target site sends no CORS headers, so `fetch`
  rejects the final response. The redirect *did* happen and the click *was*
  counted — check `GET /links/{code}`, or verify the hop with curl, which does
  not apply CORS:

  ```bash
  curl -i http://127.0.0.1:8000/N3PlYzD     # HTTP/1.1 302 Found + location:
  ```


## Why 302 and not 301?

Redirect status codes look interchangeable, but **301 is a one-way door**:

- **301 means "moved permanently."** Browsers and shared caches store it — often
  forever, and often without ever asking this server again. After a visitor's first
  hit, their browser may jump straight to the long URL from its cache. That request
  never reaches ShortLink, so **the click counter silently stops counting** that
  visitor (and eventually everyone).
- Once cached, a 301 is effectively unreversible for those clients. You cannot fix a
  bad target URL, re-point the short code, or turn counting back on for clients that
  already cached it. The only remedy is to change the short code itself.
- **302 means "found" (temporary).** It is not cached by default, so every follow
  round-trips through ShortLink, the count stays accurate, and the destination stays
  under your control — you can repoint a code at any time.

Cost of 302: one extra hop through this service for repeat visitors. That trade is
exactly what a link shortener with analytics wants — the redirect *is* the product.

Close relatives, for the record: `307 Temporary Redirect` has the same non-caching
semantics as 302 while also forbidding method rewriting (irrelevant for `GET`
short links); `308 Permanent Redirect` is the caching twin of 301. If you ever want
belt-and-braces protection against intermediaries that cache aggressively, send
`Cache-Control: no-store` alongside the 302.

## Development

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest -q
.venv\Scripts\ruff check .
```

Layout:

```
app/
  main.py         # create_app() factory and routes
  db.py           # SQLite schema + queries (one connection per operation)
  schemas.py      # request/response models
  validation.py   # target URL validation (rejects before storing)
tests/
  test_api.py     # end-to-end tests against the ASGI app
```
