#!/bin/bash

bucket_a="ingest-zone-storage-a-638039899567-us-east-1"
bucket_b="ingest-zone-storage-b-638039899567-us-east-1"
bucket_c="ingest-zone-storage-c-638039899567-us-east-1"

aws s3api put-object --bucket "${bucket_a}" --key api/ && aws s3api put-object --bucket "${bucket_a}" --key text/
aws s3api put-object --bucket "${bucket_b}" --key api/ && aws s3api put-object --bucket "${bucket_b}" --key text/
aws s3api put-object --bucket "${bucket_c}" --key api/ && aws s3api put-object --bucket "${bucket_c}" --key text/

aws s3 cp api_ingestion.py s3://"${bucket_a}"/api/ && aws s3 cp text_ingestion.py s3://"${bucket_a}"/text/
aws s3 cp api_ingestion.py s3://"${bucket_b}"/api/ && aws s3 cp text_ingestion.py s3://"${bucket_b}"/text/
aws s3 cp api_ingestion.py s3://"${bucket_c}"/api/ && aws s3 cp text_ingestion.py s3://"${bucket_c}"/text/

# 3) Run each script once to confirm objects land in all three
# buckets. See SWDD Section 7 and 8 for the full checklist and screenshots to capture.
