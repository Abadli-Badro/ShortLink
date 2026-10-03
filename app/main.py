import os
import secrets
import sqlite3
import string
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

from app.db import Database, Link
from app.schemas import LinkCreate, LinkResponse
from app.validation import InvalidUrlError, validate_target_url

CODE_LENGTH = 7
CODE_ALPHABET = string.ascii_letters + string.digits
MAX_CODE_ATTEMPTS = 10
DEFAULT_DB_PATH = "shortlink.db"
REDIRECT_STATUS_CODE = 302
DEFAULT_CORS_ORIGINS = "*"


def parse_origins(raw: str) -> list[str]:
    origins = [origin.strip() for origin in raw.split(",") if origin.strip()]
    return origins or [DEFAULT_CORS_ORIGINS]


def generate_code(length: int = CODE_LENGTH) -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(length))


def create_app(
    db_path: str | None = None,
    cors_origins: list[str] | str | None = None,
) -> FastAPI:
    database = Database(db_path or os.environ.get("SHORTLINK_DB", DEFAULT_DB_PATH))

    if cors_origins is None:
        origins = parse_origins(os.environ.get("CORS_ORIGINS", DEFAULT_CORS_ORIGINS))
    elif isinstance(cors_origins, str):
        origins = parse_origins(cors_origins)
    else:
        origins = cors_origins or [DEFAULT_CORS_ORIGINS]

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        database.initialize()
        yield

    app = FastAPI(
        title="ShortLink",
        summary="Turn a long URL into a short code and redirect on lookup.",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type"],
        expose_headers=["Location"],
    )

    def to_response(request: Request, link: Link) -> LinkResponse:
        short_url = str(request.base_url).rstrip("/") + "/" + link.code
        return LinkResponse(
            code=link.code,
            url=link.url,
            short_url=short_url,
            clicks=link.clicks,
            created_at=link.created_at,
        )

    def existing_response(request: Request, link: Link) -> JSONResponse:
        """200 for an already-shortened URL, so a repeat POST never mints a second code."""
        body = to_response(request, link).model_dump(mode="json")
        return JSONResponse(status_code=200, content=body)

    def error_response(description: str) -> dict[str, object]:
        return {
            "description": description,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {"detail": {"type": "string"}},
                        "required": ["detail"],
                    }
                }
            },
        }

    @app.get("/", tags=["meta"])
    def root() -> dict[str, str]:
        return {"name": "ShortLink", "docs": "/docs"}

    @app.post(
        "/links",
        status_code=201,
        response_model=LinkResponse,
        tags=["links"],
        responses={
            200: {
                "model": LinkResponse,
                "description": "URL was already shortened; the existing link is returned.",
            },
            400: error_response("Malformed target URL; nothing was stored."),
        },
    )
    def create_link(payload: LinkCreate, request: Request) -> LinkResponse | JSONResponse:
        try:
            url = validate_target_url(payload.url)
        except InvalidUrlError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        existing = database.get_link_by_url(url)
        if existing is not None:
            return existing_response(request, existing)

        for _ in range(MAX_CODE_ATTEMPTS):
            code = generate_code()
            try:
                link = database.create_link(code, url)
            except sqlite3.IntegrityError:
                existing = database.get_link_by_url(url)
                if existing is not None:
                    return existing_response(request, existing)
                continue
            return to_response(request, link)

        raise HTTPException(status_code=500, detail="Could not allocate a unique short code")

    @app.get(
        "/links/{code}",
        response_model=LinkResponse,
        tags=["links"],
        responses={404: error_response("No link exists for this code.")},
    )
    def link_stats(code: str, request: Request) -> LinkResponse:
        link = database.get_link(code)
        if link is None:
            raise HTTPException(status_code=404, detail=f"No link found for code '{code}'")
        return to_response(request, link)

    @app.get(
        "/{code}",
        tags=["redirects"],
        response_class=RedirectResponse,
        status_code=REDIRECT_STATUS_CODE,
        responses={
            REDIRECT_STATUS_CODE: {
                "description": "Redirects to the original URL and counts the click.",
                "headers": {
                    "location": {
                        "description": "Original long URL.",
                        "schema": {"type": "string"},
                        "example": "https://example.com/some/very/long/path",
                    }
                },
            },
            404: error_response("No link exists for this code."),
        },
    )
    def follow(code: str) -> RedirectResponse:
        link = database.record_click(code)
        if link is None:
            raise HTTPException(status_code=404, detail=f"No link found for code '{code}'")
        return RedirectResponse(url=link.url, status_code=REDIRECT_STATUS_CODE)

    return app


app = create_app()
