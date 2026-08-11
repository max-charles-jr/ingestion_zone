#!/usr/bin/env python3
"""
api_ingestion.py

Location:   /data_ingest/api_ingestion.py  (on the ingestion EC2 instance)
Purpose:    Call a free public API from the ingestion instance, routing the
            request through the Network Load Balancer and Squid Proxy,
            then write the raw JSON payload to the /api/ prefix of all
            three S3 "Storage Container" buckets attached behind the LB.

Data path:  EC2 instance -> NLB -> Squid Proxy -> Internet (Open-Meteo API)
            EC2 instance -> boto3/HTTPS -> S3 (ingest-zone-storage-a/b/c) /api/<file>.txt

Free API used: Open-Meteo (https://open-meteo.com) current-weather
forecast endpoint. No API key is required, which keeps the assignment's
"free API endpoint" requirement simple and avoids storing secrets on the
instance. Coordinates default to Philadelphia, PA and can be overridden with
the API_LATITUDE / API_LONGITUDE environment variables.

Run manually:
    cd /data_ingest && ./api_ingestion.py

Run on a schedule (see SWDD section 7 for the cron/systemd-timer setup):
    */15 * * * * /data_ingest/api_ingestion.py >> /data_ingest/logs/cron.log 2>&1
"""

import json
import logging
import os
import sys
import time
from logging.handlers import RotatingFileHandler

import boto3
import requests
from botocore.exceptions import BotoCoreError, ClientError

import ingestion_config as cfg

API_LATITUDE = os.environ.get("API_LATITUDE", "39.9526")
API_LONGITUDE = os.environ.get("API_LONGITUDE", "-75.1652")
API_URL = (
    "https://api.open-meteo.com/v1/forecast"
    f"?latitude={API_LATITUDE}&longitude={API_LONGITUDE}"
    "&current_weather=true&timezone=America%2FNew_York"
)

S3_PREFIX = "api"


def get_logger():
    os.makedirs(cfg.LOG_DIR, exist_ok=True)
    logger = logging.getLogger("api_ingestion")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        fmt = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        )
        file_handler = RotatingFileHandler(
            os.path.join(cfg.LOG_DIR, "api_ingestion.log"),
            maxBytes=1_000_000,
            backupCount=3,
        )
        file_handler.setFormatter(fmt)
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(fmt)
        logger.addHandler(file_handler)
        logger.addHandler(stream_handler)
    return logger


def fetch_api_payload(logger):
    """Call the free API through the Load Balancer + Proxy, with retries."""
    last_error = None
    for attempt in range(1, cfg.MAX_RETRIES + 1):
        try:
            logger.info(
                "Attempt %d/%d: GET %s via proxy %s",
                attempt,
                cfg.MAX_RETRIES,
                API_URL,
                cfg.PROXY_URL,
            )
            response = requests.get(
                API_URL,
                proxies=cfg.PROXIES,
                timeout=cfg.REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            payload = response.json()
            logger.info("API call succeeded (HTTP %s)", response.status_code)
            return payload
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            logger.warning("API call failed on attempt %d: %s", attempt, exc)
            if attempt < cfg.MAX_RETRIES:
                time.sleep(cfg.RETRY_BACKOFF_SECONDS * attempt)
    raise RuntimeError(f"API call failed after {cfg.MAX_RETRIES} attempts") from last_error


def build_record(payload):
    """Wrap the raw API payload with simple ingestion metadata."""
    record = {
        "source": "open-meteo",
        "source_url": API_URL,
        "ingested_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "payload": payload,
    }
    return json.dumps(record, indent=2)


def write_to_storage_containers(logger, filename, body_text):
    """
    Write the same file to the /api/ directory of each of the three
    Storage Container buckets attached to the Load Balancer.
    Uploads use the instance's IAM instance role -- no static AWS credentials
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
                ContentType="application/json",
            )
            logger.info("Wrote s3://%s/%s", bucket, key)
            successes += 1
        except (BotoCoreError, ClientError) as exc:
            logger.error("Failed to write s3://%s/%s: %s", bucket, key, exc)

    if successes == 0:
        raise RuntimeError("Failed to write payload to any Storage Container bucket")
    return successes


def main():
    logger = get_logger()
    logger.info("=== api_ingestion.py starting ===")

    try:
        payload = fetch_api_payload(logger)
    except RuntimeError as exc:
        logger.error("Aborting: %s", exc)
        sys.exit(1)

    body_text = build_record(payload)
    filename = cfg.filename_for_now()

    try:
        count = write_to_storage_containers(logger, filename, body_text)
    except RuntimeError as exc:
        logger.error("Aborting: %s", exc)
        sys.exit(1)

    logger.info(
        "=== api_ingestion.py complete: %s written to %d/%d buckets ===",
        filename,
        count,
        len(cfg.S3_BUCKETS),
    )


if __name__ == "__main__":
    main()
