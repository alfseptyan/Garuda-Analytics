import csv
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import feedparser
import requests
from bs4 import BeautifulSoup

ROOT_DIR = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT_DIR / "config" / "players.json"
OUTPUT_DIR = ROOT_DIR / "output"
RAW_DIR = OUTPUT_DIR / "raw"
STAGING_DIR = OUTPUT_DIR / "staging"
CURATED_DIR = OUTPUT_DIR / "curated"
RAW_MARKET_VALUES_PATH = RAW_DIR / "transfermarkt_market_values.csv"
RAW_NEWS_ARTICLES_PATH = RAW_DIR / "google_news_articles.csv"
STAGING_MARKET_VALUES_PATH = STAGING_DIR / "player_market_values.csv"
STAGING_NEWS_ARTICLES_PATH = STAGING_DIR / "news_articles.csv"
OUTPUT_PATH = CURATED_DIR / "player_daily_snapshot.csv"
NEWS_ARTICLES_PATH = CURATED_DIR / "player_news_articles.csv"
ERROR_REPORT_PATH = OUTPUT_DIR / "timnas_mvp_error_report.csv"
DATA_QUALITY_REPORT_PATH = CURATED_DIR / "data_quality_report.csv"
LOG_PATH = OUTPUT_DIR / "garuda_mvp.log"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9,id;q=0.8",
}


@dataclass
class PlayerConfig:
    name: str
    transfermarkt_url: str
    news_query: str


def load_config(path: Path) -> Tuple[int, List[PlayerConfig]]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)

    delay_seconds = int(data.get("request_delay_seconds", 2))
    players = [
        PlayerConfig(
            name=item["name"],
            transfermarkt_url=item["transfermarkt_url"],
            news_query=item["news_query"],
        )
        for item in data.get("players", [])
    ]
    return delay_seconds, players


def fetch_transfermarkt_market_value(url: str) -> Tuple[Optional[str], Optional[str]]:
    try:
        response = requests.get(url, headers=DEFAULT_HEADERS, timeout=20)
        response.raise_for_status()
    except requests.RequestException as exc:
        return None, f"request_failed: {exc}"

    soup = BeautifulSoup(response.text, "html.parser")

    # 1) Coba selector lama (layout Transfermarkt lama)
    value_tag = soup.select_one(".dataMarktwert .dataMarktwert")
    if not value_tag:
        value_tag = soup.select_one(".dataMarktwert")

    if value_tag:
        market_value = value_tag.get_text(strip=True)
        if market_value:
            return market_value, None

    # 2) Fallback utama: ambil nilai tepat setelah label
    #    "Current Market Value" (sesuai tampilan situs).
    page_text = soup.get_text(" ", strip=True)
    label_pattern = r"Current\s+Market\s+Value\s*:?\s*(€\s*[0-9][0-9.,]*\s*[mkMK]?)"
    match = re.search(label_pattern, page_text, flags=re.IGNORECASE)
    if match:
        market_value = match.group(1).strip()
        return market_value, None

    # 3) Fallback terakhir: cari pola nilai euro pertama di halaman.
    generic_match = re.search(r"€\s*[0-9][0-9.,]*\s*[mkMK]?", page_text)
    if generic_match:
        market_value = generic_match.group(0).strip()
        return market_value, None

    return None, "market_value_not_found"


def fetch_google_news_articles(query: str) -> Tuple[List[Dict[str, str]], Optional[str]]:
    rss_url = (
        "https://news.google.com/rss/search?q="
        f"{requests.utils.quote(query)}&hl=id&gl=ID&ceid=ID:id"
    )
    try:
        feed = feedparser.parse(rss_url)
    except Exception as exc:  # feedparser can raise broad exceptions
        return [], f"rss_parse_failed: {exc}"

    if getattr(feed, "bozo", False):
        bozo_exception = getattr(feed, "bozo_exception", None)
        if bozo_exception:
            return [], f"rss_bozo: {bozo_exception}"

    articles: List[Dict[str, str]] = []
    for entry in feed.entries:
        title = (entry.get("title") or "").strip()
        link = (entry.get("link") or "").strip()
        published = (entry.get("published") or "").strip()
        if not title and not link:
            continue
        articles.append({
            "title": title,
            "link": link,
            "published": published,
        })
    return articles, None


def summarize_articles(articles: List[Dict[str, str]], limit: int = 3) -> str:
    items = []
    for article in articles[:limit]:
        title = article.get("title", "").replace("|", "-")
        published = article.get("published", "")
        if published:
            items.append(f"{title} ({published})")
        else:
            items.append(title)
    return " | ".join(item for item in items if item)


def parse_market_value_eur(market_value: Optional[str]) -> Optional[int]:
    if not market_value:
        return None

    match = re.search(
        r"€\s*([0-9]+(?:[.,][0-9]+)?)\s*([mk])?",
        market_value,
        flags=re.IGNORECASE,
    )
    if not match:
        return None

    numeric_value = float(match.group(1).replace(",", "."))
    suffix = (match.group(2) or "").lower()
    multiplier = {"m": 1_000_000, "k": 1_000}.get(suffix, 1)
    return int(numeric_value * multiplier)


def build_article_id(article: Dict[str, str]) -> str:
    identity = "|".join([
        (article.get("link") or "").strip().lower(),
        (article.get("title") or "").strip().lower(),
        (article.get("published") or "").strip().lower(),
    ])
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


def build_article_records(
    player: PlayerConfig,
    articles: List[Dict[str, str]],
    extract_date: str,
    run_id: str,
) -> List[Dict[str, str]]:
    records = []
    for idx, article in enumerate(articles, start=1):
        article_id = build_article_id(article)
        records.append({
            "article_id": article_id,
            "run_id": run_id,
            "player_name": player.name,
            "article_rank": str(idx),
            "title": article.get("title", ""),
            "link": article.get("link", ""),
            "published": article.get("published", ""),
            "extract_date": extract_date,
        })
    return records


def build_player_record(
    player: PlayerConfig,
    extract_date: str,
    run_id: str,
) -> Tuple[Dict[str, str], List[Dict[str, str]], List[Dict[str, str]]]:
    errors: List[Dict[str, str]] = []

    market_value, market_error = fetch_transfermarkt_market_value(
        player.transfermarkt_url
    )
    if market_error:
        errors.append({
            "player_name": player.name,
            "stage": "extract_transfermarkt",
            "error_message": market_error,
            "extract_date": extract_date,
            "source": player.transfermarkt_url,
        })

    articles, news_error = fetch_google_news_articles(player.news_query)
    if news_error:
        errors.append({
            "player_name": player.name,
            "stage": "extract_google_news",
            "error_message": news_error,
            "extract_date": extract_date,
            "source": player.news_query,
        })

    article_summary = summarize_articles(articles)
    market_value_eur = parse_market_value_eur(market_value)

    record = {
        "run_id": run_id,
        "player_name": player.name,
        "market_value": market_value or "N/A",
        "market_value_eur": str(market_value_eur) if market_value_eur is not None else "N/A",
        "news_count": str(len(articles)),
        "news_summary": article_summary or "N/A",
        "extract_date": extract_date,
        "transfermarkt_url": player.transfermarkt_url,
    }
    article_records = build_article_records(player, articles, extract_date, run_id)
    return record, errors, article_records


def ensure_output_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def append_csv_records(
    records: List[Dict[str, str]],
    path: Path,
    fieldnames: List[str],
) -> None:
    ensure_output_dir(path.parent)
    write_header = not path.exists() or path.stat().st_size == 0

    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerows(records)


def read_csv_records(path: Path) -> List[Dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []

    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def rewrite_csv_records(
    records: List[Dict[str, str]],
    path: Path,
    fieldnames: List[str],
) -> None:
    ensure_output_dir(path.parent)

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)


def write_csv(records: List[Dict[str, str]], path: Path) -> None:
    fieldnames = [
        "run_id",
        "player_name",
        "market_value",
        "market_value_eur",
        "news_count",
        "news_summary",
        "extract_date",
        "transfermarkt_url",
    ]
    append_csv_records(records, path, fieldnames)


def write_raw_market_values(records: List[Dict[str, str]], path: Path) -> None:
    fieldnames = [
        "run_id",
        "player_name",
        "source",
        "source_url",
        "market_value_raw",
        "extract_date",
        "status",
    ]
    append_csv_records(records, path, fieldnames)


def write_raw_news_articles(records: List[Dict[str, str]], path: Path) -> None:
    fieldnames = [
        "article_id",
        "run_id",
        "player_name",
        "source",
        "news_query",
        "article_rank",
        "title",
        "link",
        "published",
        "extract_date",
    ]
    append_csv_records(records, path, fieldnames)


def write_staging_market_values(records: List[Dict[str, str]], path: Path) -> None:
    fieldnames = [
        "run_id",
        "player_name",
        "market_value_raw",
        "market_value_eur",
        "extract_date",
        "transfermarkt_url",
    ]
    append_csv_records(records, path, fieldnames)


def write_staging_news_articles(records: List[Dict[str, str]], path: Path) -> None:
    fieldnames = [
        "article_id",
        "run_id",
        "player_name",
        "article_rank",
        "title",
        "link",
        "published",
        "extract_date",
    ]
    append_csv_records(records, path, fieldnames)


def write_news_articles(records: List[Dict[str, str]], path: Path) -> int:
    fieldnames = [
        "article_id",
        "run_id",
        "player_name",
        "article_rank",
        "title",
        "link",
        "published",
        "extract_date",
    ]
    existing_records = read_csv_records(path)
    existing_article_ids = set()
    migrated_records = []
    removed_existing_duplicates = 0

    for record in existing_records:
        migrated_record = {fieldname: record.get(fieldname, "") for fieldname in fieldnames}
        if not migrated_record["article_id"]:
            migrated_record["article_id"] = build_article_id(migrated_record)
        if migrated_record["article_id"] in existing_article_ids:
            removed_existing_duplicates += 1
            continue
        existing_article_ids.add(migrated_record["article_id"])
        migrated_records.append(migrated_record)

    if (
        existing_records
        and (
            set(existing_records[0].keys()) != set(fieldnames)
            or removed_existing_duplicates
        )
    ):
        rewrite_csv_records(migrated_records, path, fieldnames)

    new_records = []
    for record in records:
        article_id = record.get("article_id")
        if article_id in existing_article_ids:
            continue
        existing_article_ids.add(article_id)
        new_records.append(record)

    append_csv_records(new_records, path, fieldnames)
    if removed_existing_duplicates:
        logging.info("Removed %s duplicate article rows from history", removed_existing_duplicates)
    logging.info("Skipped %s duplicate article rows", len(records) - len(new_records))
    return len(new_records)


def write_error_report(errors: List[Dict[str, str]], path: Path) -> None:
    fieldnames = [
        "player_name",
        "stage",
        "error_message",
        "extract_date",
        "source",
    ]
    ensure_output_dir(path.parent)

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        if errors:
            writer.writerows(errors)


def build_data_quality_report(
    records: List[Dict[str, str]],
    article_records: List[Dict[str, str]],
    run_id: str,
    extract_date: str,
) -> List[Dict[str, str]]:
    report_rows: List[Dict[str, str]] = []
    seen_article_ids = set()

    def add_check(
        entity_type: str,
        entity_id: str,
        check_name: str,
        passed: bool,
        details: str,
    ) -> None:
        report_rows.append({
            "run_id": run_id,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "check_name": check_name,
            "status": "PASS" if passed else "FAIL",
            "details": details,
            "extract_date": extract_date,
        })

    for record in records:
        player_name = record.get("player_name", "")
        market_value_eur = record.get("market_value_eur", "")
        news_count = int(record.get("news_count") or 0)
        transfermarkt_url = record.get("transfermarkt_url", "")

        add_check(
            "player",
            player_name,
            "player_name_required",
            bool(player_name),
            player_name or "Missing player name",
        )
        add_check(
            "player",
            player_name,
            "transfermarkt_url_valid",
            transfermarkt_url.startswith("https://www.transfermarkt.com/"),
            transfermarkt_url or "Missing Transfermarkt URL",
        )
        add_check(
            "player",
            player_name,
            "market_value_present",
            record.get("market_value") != "N/A",
            record.get("market_value", "N/A"),
        )
        add_check(
            "player",
            player_name,
            "market_value_eur_positive",
            market_value_eur != "N/A" and int(market_value_eur) > 0,
            market_value_eur,
        )
        add_check(
            "player",
            player_name,
            "news_count_positive",
            news_count > 0,
            str(news_count),
        )

    for article in article_records:
        article_id = article.get("article_id", "")
        if article_id in seen_article_ids:
            continue
        seen_article_ids.add(article_id)
        add_check(
            "article",
            article_id,
            "article_identity_complete",
            bool(article_id and article.get("title") and article.get("link")),
            article.get("title", ""),
        )

    add_check(
        "run",
        run_id,
        "article_id_unique_within_run",
        len(seen_article_ids) == len(article_records),
        f"{len(seen_article_ids)} unique of {len(article_records)} fetched article rows",
    )
    return report_rows


def build_raw_market_record(record: Dict[str, str]) -> Dict[str, str]:
    return {
        "run_id": record["run_id"],
        "player_name": record["player_name"],
        "source": "transfermarkt",
        "source_url": record["transfermarkt_url"],
        "market_value_raw": record["market_value"],
        "extract_date": record["extract_date"],
        "status": "success" if record["market_value"] != "N/A" else "failed",
    }


def build_raw_news_records(
    player: PlayerConfig,
    articles: List[Dict[str, str]],
) -> List[Dict[str, str]]:
    records = []
    for article in articles:
        raw_article = article.copy()
        raw_article["source"] = "google_news_rss"
        raw_article["news_query"] = player.news_query
        records.append(raw_article)
    return records


def build_staging_market_record(record: Dict[str, str]) -> Dict[str, str]:
    return {
        "run_id": record["run_id"],
        "player_name": record["player_name"],
        "market_value_raw": record["market_value"],
        "market_value_eur": record["market_value_eur"],
        "extract_date": record["extract_date"],
        "transfermarkt_url": record["transfermarkt_url"],
    }


def write_data_quality_report(records: List[Dict[str, str]], path: Path) -> None:
    fieldnames = [
        "run_id",
        "entity_type",
        "entity_id",
        "check_name",
        "status",
        "details",
        "extract_date",
    ]
    append_csv_records(records, path, fieldnames)


def setup_logging(log_path: Path) -> None:
    ensure_output_dir(log_path.parent)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


def main() -> None:
    setup_logging(LOG_PATH)
    delay_seconds, players = load_config(CONFIG_PATH)
    if not players:
        raise SystemExit("No players configured in config/players.json")

    records: List[Dict[str, str]] = []
    article_records: List[Dict[str, str]] = []
    raw_market_records: List[Dict[str, str]] = []
    raw_news_records: List[Dict[str, str]] = []
    staging_market_records: List[Dict[str, str]] = []
    errors: List[Dict[str, str]] = []
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for idx, player in enumerate(players, start=1):
        logging.info("Extracting data for %s", player.name)
        extract_date = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        record, player_errors, player_articles = build_player_record(
            player,
            extract_date,
            run_id,
        )
        records.append(record)
        errors.extend(player_errors)
        article_records.extend(player_articles)
        raw_market_records.append(build_raw_market_record(record))
        raw_news_records.extend(build_raw_news_records(player, player_articles))
        staging_market_records.append(build_staging_market_record(record))
        if idx < len(players):
            time.sleep(max(delay_seconds, 0))

    quality_records = build_data_quality_report(
        records,
        article_records,
        run_id,
        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    write_raw_market_values(raw_market_records, RAW_MARKET_VALUES_PATH)
    write_raw_news_articles(raw_news_records, RAW_NEWS_ARTICLES_PATH)
    write_staging_market_values(staging_market_records, STAGING_MARKET_VALUES_PATH)
    write_staging_news_articles(article_records, STAGING_NEWS_ARTICLES_PATH)
    write_csv(records, OUTPUT_PATH)
    new_article_count = write_news_articles(article_records, NEWS_ARTICLES_PATH)
    write_error_report(errors, ERROR_REPORT_PATH)
    write_data_quality_report(quality_records, DATA_QUALITY_REPORT_PATH)
    logging.info("Wrote %s raw market rows to %s", len(raw_market_records), RAW_MARKET_VALUES_PATH)
    logging.info("Wrote %s raw news rows to %s", len(raw_news_records), RAW_NEWS_ARTICLES_PATH)
    logging.info("Wrote %s staging market rows to %s", len(staging_market_records), STAGING_MARKET_VALUES_PATH)
    logging.info("Wrote %s staging news rows to %s", len(article_records), STAGING_NEWS_ARTICLES_PATH)
    logging.info("Wrote %s rows to %s", len(records), OUTPUT_PATH)
    logging.info("Wrote %s new article rows to %s", new_article_count, NEWS_ARTICLES_PATH)
    logging.info("Wrote %s data quality rows to %s", len(quality_records), DATA_QUALITY_REPORT_PATH)
    if errors:
        logging.warning("Wrote %s errors to %s", len(errors), ERROR_REPORT_PATH)


if __name__ == "__main__":
    main()
