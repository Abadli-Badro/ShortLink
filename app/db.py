import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS links (
    code       TEXT PRIMARY KEY,
    url        TEXT    NOT NULL,
    clicks     INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_links_created_at ON links (created_at);
"""

UNIQUE_URL_INDEX = "CREATE UNIQUE INDEX IF NOT EXISTS idx_links_url ON links (url)"


@dataclass(frozen=True, slots=True)
class Link:
    code: str
    url: str
    clicks: int
    created_at: str


def _row_to_link(row: sqlite3.Row) -> Link:
    return Link(
        code=row["code"],
        url=row["url"],
        clicks=row["clicks"],
        created_at=row["created_at"],
    )


class Database:
    """Thin SQLite wrapper. Opens a short-lived connection per operation so it
    is safe to call from FastAPI's threadpool."""

    def __init__(self, path: str) -> None:
        self.path = path

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            self._ensure_unique_urls(conn)

    @staticmethod
    def _ensure_unique_urls(conn: sqlite3.Connection) -> None:
        """Merge duplicate targets left behind by older schemas, then enforce
        one short code per URL: the earliest code wins and click counts add up."""
        seen: dict[str, str] = {}
        rows = conn.execute("SELECT rowid, code, url, clicks FROM links ORDER BY rowid").fetchall()
        for row in rows:
            keeper = seen.get(row["url"])
            if keeper is None:
                seen[row["url"]] = row["code"]
            elif keeper != row["code"]:
                conn.execute(
                    "UPDATE links SET clicks = clicks + ? WHERE code = ?",
                    (row["clicks"], keeper),
                )
                conn.execute("DELETE FROM links WHERE code = ?", (row["code"],))
        conn.execute(UNIQUE_URL_INDEX)

    def create_link(self, code: str, url: str) -> Link:
        created_at = datetime.now(UTC).isoformat()
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO links (code, url, clicks, created_at) VALUES (?, ?, 0, ?)",
                (code, url, created_at),
            )
        return Link(code=code, url=url, clicks=0, created_at=created_at)

    def get_link(self, code: str) -> Link | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM links WHERE code = ?", (code,)).fetchone()
        return _row_to_link(row) if row else None

    def get_link_by_url(self, url: str) -> Link | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM links WHERE url = ? ORDER BY rowid LIMIT 1", (url,)
            ).fetchone()
        return _row_to_link(row) if row else None

    def record_click(self, code: str) -> Link | None:
        """Atomically increment the click counter and return the updated link."""
        with self.connection() as conn:
            cursor = conn.execute("UPDATE links SET clicks = clicks + 1 WHERE code = ?", (code,))
            if cursor.rowcount == 0:
                return None
            row = conn.execute("SELECT * FROM links WHERE code = ?", (code,)).fetchone()
        return _row_to_link(row) if row else None

    def count_links(self) -> int:
        with self.connection() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM links").fetchone()
        return int(row["n"])
