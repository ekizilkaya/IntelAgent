import datetime
import email.utils
import re
import ssl
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from typing import Dict, Iterable, List, Tuple

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("NEWS_DISCOVERY")
USER_AGENT = "NewsDiscoveryAgent/1.0 (+https://example.com)"
REQUEST_TIMEOUT = 15
TIME_WINDOW_HOURS = 6

REGION_ALIAS: Dict[str, str] = {
    "tr": "turkey",
    "turkiye": "turkey",
    "turkey": "turkey",
    "hu": "hungary",
    "hungary": "hungary",
    "international": "international",
    "global": "international",
    "world": "international",
}

REGION_LABELS: Dict[str, str] = {
    "turkey": "Turkey",
    "hungary": "Hungary",
    "international": "International",
}

REGION_FEEDS = {
    "turkey": {
        "pro_gov": [
            "https://www.hurriyet.com.tr/rss/anasayfa",
            "https://www.sabah.com.tr/rss/anasayfa.xml",
            "https://www.trthaber.com/manset_articles.rss",
            "https://www.yenisafak.com/rss",
        ],
        "independent": [
            "https://www.sozcu.com.tr/feeds-rss-category-sozcu",
            "https://www.birgun.net/rss/home",
            "https://www.cumhuriyet.com.tr/rss/",
            "https://halktv.com.tr/service/rss.php",
        ],
        "intl_local": [
            "https://feeds.bbci.co.uk/turkce/rss.xml",
            "https://rss.dw.com/rdf/rss-tur-all",
        ],
    },
    "hungary": {
        "pro_gov": [
            "https://index.hu/24ora/rss/",
            "https://mandiner.hu/rss",
        ],
        "independent": [
            "https://telex.hu/rss",
            "https://444.hu/feed",
            "https://hvg.hu/rss",
            "https://24.hu/feed/",
        ],
        "intl_local": [
            "https://hu.euronews.com/rss",
            "https://www.szabadeuropa.hu/rss",
        ],
    },
    "international": {
        "us": [
            "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml",
            "https://feeds.washingtonpost.com/rss/world",
        ],
        "uk": [
            "https://feeds.bbci.co.uk/news/rss.xml",
            "https://www.theguardian.com/world/rss",
        ],
        "europe": [
            "https://rss.dw.com/rdf/rss-en-all",
            "https://www.france24.com/en/rss",
        ],
        "world": [
            "https://asia.nikkei.com/rss/feed/nar",
            "https://www.scmp.com/rss/91/feed/",
            "https://www.rt.com/rss/",
            "https://www.thehindu.com/feeder/default.rss",
            "https://www.aljazeera.com/xml/rss/all.xml",
            "https://latinamericareports.com/feed/",
        ],
    },
}

DATE_TAGS = [
    "pubDate",
    "updated",
    "published",
    "{http://www.w3.org/2005/Atom}updated",
    "{http://www.w3.org/2005/Atom}published",
]

LINK_TAGS = [
    "link",
    "{http://www.w3.org/2005/Atom}link",
]

NAMESPACE_PATTERN = re.compile(r"\{.*\}")


class FetchError(Exception):
    pass


def _normalize_text(value: str) -> str:
    sanitized = re.sub(r"[^a-z0-9\s]", " ", value.lower())
    return re.sub(r"\s+", " ", sanitized).strip()


def _parse_datetime(value: str) -> datetime.datetime | None:
    if not value:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.astimezone(datetime.timezone.utc)


def _strip_namespace(tag: str) -> str:
    return NAMESPACE_PATTERN.sub("", tag)


def _find_link(element: ET.Element) -> str | None:
    for tag in LINK_TAGS:
        for candidate in element.findall(tag):
            if candidate is None:
                continue
            value = (candidate.text or "").strip()
            if not value:
                value = candidate.attrib.get("href", "").strip()
            if value:
                return value
    explicit = element.find("link")
    if explicit is not None and explicit.text:
        return explicit.text.strip()
    return None


def _fetch_feed(url: str) -> Tuple[str, List[Dict[str, str]]]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT, context=ssl.create_default_context()) as response:
            raw = response.read()
    except Exception as exc:
        raise FetchError(f"{url} ({exc})")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise FetchError(f"Failed to parse XML from {url}: {exc}")
    feed_title = root.findtext("./channel/title") or root.findtext(".//title") or url
    entries: List[Dict[str, str]] = []
    items = root.findall(".//item")
    is_atom = False
    if not items:
        items = root.findall(".//{http://www.w3.org/2005/Atom}entry")
        is_atom = bool(items)
    for item in items:
        title = item.findtext("title") or ""
        if not title.strip():
            continue
        link = _find_link(item)
        if not link:
            continue
        published = None
        for tag in DATE_TAGS:
            value = item.findtext(tag)
            if value:
                published = _parse_datetime(value)
                if published:
                    break
        if not published:
            continue
        entries.append({
            "title": title.strip(),
            "link": link,
            "published": published,
            "source": feed_title,
            "feed_url": url,
            "is_atom": is_atom,
        })
    return feed_title, entries


def _aggregate_region_feeds(region: str, cutoff: datetime.datetime) -> Tuple[List[Dict[str, str]], List[str]]:
    collected: List[Dict[str, str]] = []
    errors: List[str] = []
    if region not in REGION_FEEDS:
        raise ValueError("Unknown region")
    mapping = REGION_FEEDS[region]
    for category, urls in mapping.items():
        for url in urls:
            try:
                feed_title, entries = _fetch_feed(url)
            except FetchError as exc:
                errors.append(str(exc))
                continue
            for entry in entries:
                if entry["published"] < cutoff:
                    continue
                entry["category"] = category
                entry["source_title"] = feed_title
                collected.append(entry)
    return collected, errors


def _build_story_index(entries: Iterable[Dict[str, str]]) -> Dict[str, Dict[str, object]]:
    index: Dict[str, Dict[str, object]] = {}
    for entry in entries:
        norm_title = _normalize_text(entry["title"])
        if not norm_title:
            continue
        bucket = index.setdefault(norm_title, {
            "title": entry["title"],
            "mentions": set(),
            "links": [],
            "latest": entry["published"],
        })
        bucket["mentions"].add(entry["source_title"])
        if entry["published"] > bucket["latest"]:
            bucket["title"] = entry["title"]
            bucket["latest"] = entry["published"]
        bucket["links"].append(entry["link"])
    return index


def _select_top_stories(index: Dict[str, Dict[str, object]], limit: int) -> List[Dict[str, object]]:
    choices = list(index.values())
    choices.sort(key=lambda record: (-len(record["mentions"]), -record["latest"].timestamp()))
    return choices[:limit]


def _unique_entries(entries: List[Dict[str, str]], category: str) -> List[Dict[str, str]]:
    key_map: Dict[str, set] = defaultdict(set)
    registry: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for entry in entries:
        if entry["category"] not in {"pro_gov", "independent"}:
            continue
        key = _normalize_text(entry["title"])
        key_map[key].add(entry["category"])
        registry[key].append(entry)
    unique_keys = [k for k, cats in key_map.items() if cats == {category}]
    results: List[Dict[str, str]] = []
    for key in unique_keys:
        results.extend(registry[key])
    results.sort(key=lambda item: -item["published"].timestamp())
    return results[:3]


def _format_story_list(stories: List[Dict[str, object]]) -> List[str]:
    lines: List[str] = []
    for idx, story in enumerate(stories, start=1):
        link = story["links"][0] if story.get("links") else "link not available"
        mentions = len(story["mentions"])
        time_label = story["latest"].astimezone(datetime.timezone.utc).strftime("%H:%M UTC")
        lines.append(
            f"{idx}. {story['title']} (mentioned by {mentions} sources, latest {time_label})\n   Link: {link}"
        )
    return lines


def _build_summary(region: str) -> str:
    now = datetime.datetime.now(datetime.timezone.utc)
    cutoff = now - datetime.timedelta(hours=TIME_WINDOW_HOURS)
    entries, errors = _aggregate_region_feeds(region, cutoff)
    if not entries:
        message = f"No articles were retrieved for {REGION_LABELS.get(region, region)} in the last {TIME_WINDOW_HOURS} hours."
        if errors:
            message += " Errors: " + "; ".join(errors)
        return message
    index = _build_story_index(entries)
    top_stories = _select_top_stories(index, 3)
    lines: List[str] = []
    lines.append(f"Top 3 Most Mentioned Stories ({REGION_LABELS.get(region, region)} focus, last {TIME_WINDOW_HOURS}h):")
    if top_stories:
        lines.extend(_format_story_list(top_stories))
    else:
        lines.append("No frequent stories identified in this window.")
    if region in {"turkey", "hungary"}:
        for leaning, heading in (("pro_gov", "Top 3 İktidar kontrolündeki medya yönergeli stories"), ("independent", "Top 3 Bağımsız Medya yönergeli stories")):
            lines.append("\n" + heading + ":")
            unique = _unique_entries(entries, leaning)
            if unique:
                for idx, entry in enumerate(unique, start=1):
                    lines.append(f"{idx}. {entry['title']} (source: {entry['source_title']} / {entry['feed_url']})")
            else:
                lines.append("No unique stories focused exclusively by that leaning during this window.")
    if errors:
        lines.append("\nFeeds that failed to load: " + "; ".join(errors))
    lines.append("\nWould you like me to dive deeper into any of these stories or pull an international report as well?")
    return "\n".join(lines)


def _resolve_region(value: str | None) -> str:
    if not value:
        return "international"
    guess = value.strip().lower()
    return REGION_ALIAS.get(guess, "international")


@mcp.tool()
def summarize_news(region: str | None = None) -> str:
    """Summarizes current headlines for Turkey, Hungary, or international focus."""
    region_key = _resolve_region(region)
    try:
        return _build_summary(region_key)
    except Exception as exc:
        return f"Üzgünüm, haberleri değerlendirirken hata oluştu: {exc}"


if __name__ == "__main__":
    mcp.run()
