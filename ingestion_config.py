#!/usr/bin/env python3
"""
ingestion_config.py

Shared configuration for api_ingestion.py and text_ingestion.py.

Both ingestion scripts live in /data_ingest/ on the ingestion instance and import
this module so that the proxy address, load balancer address, and target
S3 buckets only need to be maintained in one place. This keeps each
ingestion script focused on a single responsibility (SRP) and avoids
duplicated, drift-prone configuration values.

Every value below is read from an environment variable first (so the
values used in production can be injected at instance-launch time via
user data / SSM Parameter Store) and falls back to the value used in this
project's reference deployment.
"""

import os

# ---------------------------------------------------------------------------
# Network path: EC2 instance -> Network Load Balancer -> Squid Proxy -> Internet
# ---------------------------------------------------------------------------
# The NLB's internal DNS name. All outbound HTTP/HTTPS calls made by the
# ingestion scripts are routed through this address so that traffic leaves
# the instance, is distributed by the Load Balancer to the healthy Proxy target,
# and the Proxy performs the actual connection out to the public internet.
LOAD_BALANCER_DNS = os.environ.get(
    "INGEST_LB_DNS",
    "ingest-proxy-nlb-3ca79b50d94e11a3.elb.us-east-1.amazonaws.com",
)

# Port the Squid proxy listens on and that the NLB listener forwards to.
PROXY_PORT = int(os.environ.get("INGEST_PROXY_PORT", "3128"))

PROXY_URL = f"http://{LOAD_BALANCER_DNS}:{PROXY_PORT}"

# requests-compatible proxy map used by both scripts.
PROXIES = {
    "http": PROXY_URL,
    "https": PROXY_URL,
}

# ---------------------------------------------------------------------------
# Storage Containers (S3 buckets) attached behind the Load Balancer's
# private-subnet route table via an S3 Gateway VPC Endpoint.
# ---------------------------------------------------------------------------
S3_BUCKETS = os.environ.get(
    "INGEST_S3_BUCKETS",
    "ingest-zone-storage-a,ingest-zone-storage-b,ingest-zone-storage-c",
).split(",")

AWS_REGION = os.environ.get("INGEST_AWS_REGION", "us-east-1")

# ---------------------------------------------------------------------------
# Filesystem
# ---------------------------------------------------------------------------
DATA_INGEST_DIR = os.environ.get("INGEST_HOME", "/data_ingest")
LOG_DIR = os.path.join(DATA_INGEST_DIR, "logs")

# ---------------------------------------------------------------------------
# Networking behavior
# ---------------------------------------------------------------------------
REQUEST_TIMEOUT_SECONDS = int(os.environ.get("INGEST_TIMEOUT", "15"))
MAX_RETRIES = int(os.environ.get("INGEST_MAX_RETRIES", "3"))
RETRY_BACKOFF_SECONDS = float(os.environ.get("INGEST_RETRY_BACKOFF", "2"))


def filename_for_now(now=None):
    """
    Build the ingestion filename in MMDDYYYY_HHMM.txt format, matching the
    activity's example filename (07072022_1036.txt = July 7, 2022, 10:36).

    A single shared helper guarantees api_ingestion.py and text_ingestion.py
    never drift into two different naming conventions.
    """
    import datetime

    now = now or datetime.datetime.now()
    return now.strftime("%m%d%Y_%H%M") + ".txt"
