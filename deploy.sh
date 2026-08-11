#!/bin/bash

aws cloudformation deploy --template-file ingestion-zone.yaml --stack-name ingest-zone \
  --parameter-overrides KeyPairName=mcc AdminCidr=24.184.34.214/32 \
  --capabilities CAPABILITY_IAM --profile default