#!/usr/bin/env python3
"""
text_ingestion.py

Location:   /data_ingest/text_ingestion.py  (on the ingestion EC2 instance)
Purpose:    Scrape a public web page from the ingestion instance, routing the
            request through the Network Load Balancer and Squid Proxy,
            strip the HTML down to plain text with BeautifulSoup, and
            write the result to the /text/ prefix of all three S3
            "Storage Container" buckets attached behind the LB.

Data path:  EC2 instance -> NLB -> Squid Proxy -> Internet (quotes.toscrape.com)
            EC2 instance -> boto3/HTTPS -> S3 (ingest-zone-storage-a/b/c) /text/<file>.txt

Site scraped: https://quotes.toscrape.com/ -- a page purpose-built for
scraping practice (recommended by the Real Python Beautiful Soup guide
linked in the assignment), so the exercise scrapes a page whose owners
expect and welcome it, rather than sending an ad-hoc script at an
arbitrary third-party site.

Run manually:
    cd /data_ingest && ./text_ingestion.py

Run on a schedule (see SWDD section 7 for the cron/systemd-timer setup):
    */15 * * * * /data_ingest/text_ingestion.py >> /data_ingest/logs/cron.log 2>&1
"""

import logging
import os
import sys
import time
from logging.handlers import RotatingFileHandler

import boto3
import requests
from bs4 import BeautifulSoup
from botocore.exceptions import BotoCoreError, ClientError

import ingestion_config as cfg

TARGET_URL = os.environ.get("TEXT_TARGET_URL", "https://quotes.toscrape.com/")

S3_PREFIX = "text"


def get_logger():
    os.makedirs(cfg.LOG_DIR, exist_ok=True)
    logger = logging.getLogger("text_ingestion")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        fmt = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        )
        file_handler = RotatingFileHandler(
            os.path.join(cfg.LOG_DIR, "text_ingestion.log"),
            maxBytes=1_000_000,
            backupCount=3,
        )
        file_handler.setFormatter(fmt)
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(fmt)
        logger.addHandler(file_handler)
        logger.addHandler(stream_handler)
    return logger


def fetch_html(logger):
    """Fetch the target page through the Load Balancer + Proxy, with retries."""
    last_error = None
    for attempt in range(1, cfg.MAX_RETRIES + 1):
        try:
            logger.info(
                "Attempt %d/%d: GET %s via proxy %s",
                attempt,
                cfg.MAX_RETRIES,
                TARGET_URL,
                cfg.PROXY_URL,
            )
            response = requests.get(
                TARGET_URL,
                proxies=cfg.PROXIES,
                timeout=cfg.REQUEST_TIMEOUT_SECONDS,
                headers={"User-Agent": "CLD410-data-ingest-bot/1.0"},
            )
            response.raise_for_status()
            logger.info("Page fetch succeeded (HTTP %s)", response.status_code)
            return response.text
        except requests.RequestException as exc:
            last_error = exc
            logger.warning("Page fetch failed on attempt %d: %s", attempt, exc)
            if attempt < cfg.MAX_RETRIES:
                time.sleep(cfg.RETRY_BACKOFF_SECONDS * attempt)
    raise RuntimeError(f"Page fetch failed after {cfg.MAX_RETRIES} attempts") from last_error


def strip_html(html):
    """
    Use BeautifulSoup to strip out the HTML and return clean, readable
    text. Script/style tags are removed first so their contents don't
    leak into the output, then get_text() collapses the remaining markup
    into plain lines.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.splitlines()]
    clean_lines = [line for line in lines if line]
    return "\n".join(clean_lines)


def write_to_storage_containers(logger, filename, body_text):
    """
    Write the same file to the /text/ directory of each of the three
    Storage Container buckets attached to the Load Balancer.
    Uploads use the EC2 instance's IAM instance role -- no static AWS credentials
    are stored on disk.
    """
    s3 = boto3.client("s3", region_name=cfg.AWS_REGION)
    key = f"{S3_PREFIX}/{filename}"
    successes = 0
    for bucket in cfg.S3_BUCKETS:
        try:
            s3.put_object(
                Bucket=bucket,
                Key=key,
                Body=body_text.encode("utf-8"),
                ContentType="text/plain",
            )
            logger.info("Wrote s3://%s/%s", bucket, key)
            successes += 1
        except (BotoCoreError, ClientError) as exc:
            logger.error("Failed to write s3://%s/%s: %s", bucket, key, exc)

    if successes == 0:
        raise RuntimeError("Failed to write scraped text to any Storage Container bucket")
    return successes


def main():
    logger = get_logger()
    logger.info("=== text_ingestion.py starting ===")

    try:
        html = fetch_html(logger)
    except RuntimeError as exc:
        logger.error("Aborting: %s", exc)
        sys.exit(1)

    clean_text = strip_html(html)
    header = f"Source: {TARGET_URL}\nScraped at: {time.strftime('%Y-%m-%dT%H:%M:%S%z')}\n\n"
    body_text = header + clean_text
    filename = cfg.filename_for_now()

    try:
        count = write_to_storage_containers(logger, filename, body_text)
    except RuntimeError as exc:
        logger.error("Aborting: %s", exc)
        sys.exit(1)

    logger.info(
        "=== text_ingestion.py complete: %s written to %d/%d buckets ===",
        filename,
        count,
        len(cfg.S3_BUCKETS),
    )


if __name__ == "__main__":
    main()
