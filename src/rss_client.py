from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone

import feedparser
import requests


@dataclass
class NewsItem:
    guid: str
    title: str
    google_link: str
    published_at: datetime | None
    source_name: str | None
    search_term_id: str


def build_feed_url(query: str, hl: str, gl: str, ceid: str) -> str:
    params = {"q": query, "hl": hl, "gl": gl, "ceid": ceid}
    return f"https://news.google.com/rss/search?{urllib.parse.urlencode(params)}"


def fetch_news_items(search_term_id: str, query: str, rss_settings: dict, user_agent: str) -> list[NewsItem]:
    url = build_feed_url(query, rss_settings["hl"], rss_settings["gl"], rss_settings["ceid"])

    # feedparser não define um User-Agent próprio nas requisições e o Google
    # retorna corpo vazio nesse caso — buscamos com requests (UA identificável)
    # e entregamos o conteúdo já baixado para o feedparser processar.
    response = requests.get(url, headers={"User-Agent": user_agent}, timeout=10)
    response.raise_for_status()
    feed = feedparser.parse(response.content)

    items: list[NewsItem] = []
    for entry in feed.entries:
        guid = getattr(entry, "id", None) or getattr(entry, "link", None)
        if not guid:
            continue

        published_at = None
        if getattr(entry, "published_parsed", None):
            published_at = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)

        source_name = None
        source = getattr(entry, "source", None)
        if source:
            source_name = source.get("title")

        items.append(
            NewsItem(
                guid=guid,
                title=entry.get("title", "").strip(),
                google_link=entry.get("link", ""),
                published_at=published_at,
                source_name=source_name,
                search_term_id=search_term_id,
            )
        )

    return items
