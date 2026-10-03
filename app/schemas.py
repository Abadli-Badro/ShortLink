from datetime import datetime

from pydantic import BaseModel, Field


class LinkCreate(BaseModel):
    url: str = Field(
        description="Long URL to shorten. Must be an absolute http(s) URL.",
        examples=["https://example.com/some/very/long/path?with=query"],
    )


class LinkResponse(BaseModel):
    code: str = Field(description="Short code identifying the link.")
    url: str = Field(description="Original long URL.")
    short_url: str = Field(description="Absolute URL for the short link.")
    clicks: int = Field(description="How often the short link has been followed.")
    created_at: datetime = Field(description="Creation time (UTC, ISO 8601).")
