#!/bin/bash

bucket_a="ingest-zone-storage-a-638039899567-us-east-1"
bucket_b="ingest-zone-storage-b-638039899567-us-east-1"
bucket_c="ingest-zone-storage-c-638039899567-us-east-1"

aws s3 rm s3://${bucket_a} --recursive
aws s3 rm s3://${bucket_b} --recursive
aws s3 rm s3://${bucket_c} --recursive