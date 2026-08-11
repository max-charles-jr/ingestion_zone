# Cloud Data Ingestion Zone (AWS)

A small AWS data-ingestion pipeline built for TESU CLD-410 (Developing in Cloud): a Squid **Proxy** for outbound internet egress, an internal Network **Load Balancer** in front of it, three S3 **Storage Containers**, and a private Linux **VM** that runs two Python scripts to pull data from a free API and a public web page. Everything is provisioned by a single CloudFormation template.

## Architecture

![Architecture diagram](architecture_diagram.png)

```
Internet
   |
Proxy (EC2, Squid :3128, public subnet, Elastic IP)
   |
Internal Network Load Balancer (TCP :3128 -> Proxy target group)
   |
Ingestion VM (EC2, private subnet, IAM instance role)
   |  \data_ingest\  ->  api_ingestion.py, text_ingestion.py
   |
   +--> S3 Storage Containers x3 (via S3 Gateway VPC Endpoint)
          ingest-zone-storage-a/b/c
            /api/  <- api_ingestion.py output
            /text/ <- text_ingestion.py output
```

The private subnet (Load Balancer, VM, S3 endpoint) has **no internet-gateway route on purpose** — the VM can only reach the public internet by proxying through the Load Balancer to Squid. This is documented in full, including the design rationale and trade-offs, in `SWDD-CloudDataIngestion.docx`.

## Repository contents

| File | Purpose |
|---|---|
| `ingestion-zone.yaml` | CloudFormation template — provisions the entire architecture above |
| `ingestion_config.py` | Shared config module (proxy address, bucket names, retry/timeout settings) |
| `api_ingestion.py` | Calls the Open-Meteo API through the Proxy/LB, writes JSON to `/api/` in all 3 buckets |
| `text_ingestion.py` | Scrapes a web page through the Proxy/LB, strips HTML, writes text to `/text/` in all 3 buckets |
| `architecture_diagram.png` | Architecture diagram used above and in the SWDD |
| `SWDD-CloudDataIngestion.docx` | Full Software Design Document (architecture rationale, data design, security review, deployment runbook) |

## Prerequisites

- AWS CLI v2, configured with credentials that can create VPC/EC2/ELB/S3/IAM resources
- An existing EC2 key pair in the target region
- Your own public IP address (for the `AdminCidr` parameter, used to scope SSH to the Proxy)

## Quick start

```bash
aws cloudformation deploy \
  --template-file ingestion-zone.yaml \
  --stack-name ingest-zone \
  --parameter-overrides \
      KeyPairName=<your-key-pair-name> \
      AdminCidr=<your-ip-address>/32 \
  --capabilities CAPABILITY_IAM
```

Grab the outputs once it completes:

```bash
aws cloudformation describe-stacks --stack-name ingest-zone --query "Stacks[0].Outputs"
```

You'll need `LoadBalancerDnsName`, `StorageBucketNames`, and `IngestionVmInstanceId` (or `IngestionEC2InstanceId` if you've renamed it) from the output for the steps below.

### Post-deployment steps

1. **Create the `/api/` and `/text/` folder markers** in each bucket (CloudFormation can't create these):
   ```bash
   for b in $(aws cloudformation describe-stacks --stack-name ingest-zone \
       --query "Stacks[0].Outputs[?OutputKey=='StorageBucketNames'].OutputValue" --output text | tr ',' ' '); do
     aws s3api put-object --bucket "$b" --key api/
     aws s3api put-object --bucket "$b" --key text/
   done
   ```

2. **Copy the two entry-point scripts onto the VM.** The VM has no public IP and no SSH path by design — use SSM Session Manager (see [Troubleshooting](#troubleshooting) if it can't connect) or the AWS Console's Session Manager tab, then paste `api_ingestion.py` and `text_ingestion.py` into `/data_ingest/` (`ingestion_config.py` is already written there by the instance's user data with the real deployed values filled in). Then:
   ```bash
   chmod 750 /data_ingest/api_ingestion.py /data_ingest/text_ingestion.py
   ```

3. **Run each script once** to confirm the full path works end to end:
   ```bash
   cd /data_ingest && source venv/bin/activate
   ./api_ingestion.py
   ./text_ingestion.py
   ```
   Check `logs/api_ingestion.log` / `logs/text_ingestion.log`, and confirm a `MMDDYYYY_HHMM.txt` object landed in `/api/` and `/text/` of all three buckets.

## Configuration reference

| Parameter | Default | Description |
|---|---|---|
| `KeyPairName` | *(required)* | Existing EC2 key pair for emergency SSH to the Proxy |
| `AdminCidr` | *(required)* | Your IP in CIDR form, allowed to SSH to the Proxy |
| `LatestAmiId` | Amazon Linux 2023 SSM parameter | Always resolves to the current AL2023 AMI |
| `ProxyInstanceType` | `t3.micro` | Squid Proxy instance size |
| `VmInstanceType` | `t3.micro` | Ingestion VM instance size |
| `VpcCidr` | `10.0.0.0/16` | VPC CIDR block |
| `PublicSubnetCidr` | `10.0.1.0/24` | Public subnet (Proxy) |
| `PrivateSubnetCidr` | `10.0.2.0/24` | Private subnet (Load Balancer, VM, S3 endpoint) |

## Troubleshooting

These are real issues hit while standing this up — check here before re-diagnosing from scratch.

**Squid returns 400/403 or every request is denied.** ACL ordering matters — Squid evaluates `http_access` rules top to bottom and stops at the first match, so any `allow` rule must appear *before* the file's `http_access deny all`. Verify with `sudo squid -k parse` (each processed line is echoed in order) and `sudo tail -f /var/log/squid/access.log` while retrying a request (`TCP_DENIED` = Squid rejected it; `TCP_TUNNEL/200` = Squid forwarded it fine and any error is from the origin site itself).

**Connecting through the NLB DNS name hangs, but `curl -x http://localhost:3128` on the Proxy itself works.** Network Load Balancers have **cross-zone load balancing disabled by default**. If your public and private subnets land in different Availability Zones (CloudFormation's `!GetAZs` selection isn't guaranteed to put both subnets in the same AZ), the NLB node in the Proxy's AZ works fine, but the node in the *other* AZ has no local healthy target and the connection just times out — non-deterministically, since NLB DNS returns IPs for both nodes. Fix:
```bash
aws elbv2 modify-load-balancer-attributes \
  --load-balancer-arn <your-nlb-arn> \
  --attributes Key=load_balancing.cross_zone.enabled,Value=true
```
(Confirm which AZs your subnets actually landed in with `aws ec2 describe-subnets --subnet-ids <public-subnet-id> <private-subnet-id> --query "Subnets[].AvailabilityZone"`.)

**SSM Session Manager can't connect to the ingestion VM, even with `AmazonSSMManagedInstanceCore` attached** — you'll see the agent log something like `dial tcp ...:443: i/o timeout`. That's a network problem, not a permissions problem (a permissions problem would be an HTTP 403, not a TCP timeout): the private subnet has no internet route by design, and the SSM Agent doesn't know to use the Squid proxy, so its calls to `ssm`/`ssmmessages`/`ec2messages` never reach AWS. Fix — add VPC interface endpoints for those three services in the private subnet:
```bash
aws ec2 create-security-group --group-name ingest-ssm-endpoint-sg \
  --description "SSM interface endpoints" --vpc-id <your-vpc-id>
aws ec2 authorize-security-group-ingress --group-id <endpoint-sg-id> \
  --protocol tcp --port 443 --source-group <vm-security-group-id>

for svc in ssm ssmmessages ec2messages; do
  aws ec2 create-vpc-endpoint --vpc-id <your-vpc-id> \
    --service-name com.amazonaws.<region>.$svc \
    --vpc-endpoint-type Interface \
    --subnet-ids <private-subnet-id> \
    --security-group-ids <endpoint-sg-id> \
    --private-dns-enabled
done
```
Then `sudo systemctl restart amazon-ssm-agent` on the instance (via EC2 Instance Connect if Session Manager still isn't reachable) and retry. This adds a small ongoing per-endpoint hourly charge — delete the endpoints during teardown.

## Security notes

IAM access for the VM is scoped to `s3:PutObject`/`GetObject`/`ListBucket` on only the three project buckets (no static AWS keys are ever stored on disk). Squid's ACL restricts proxy use to the VPC CIDR. All three buckets block public access and use SSE-S3 encryption by default. See `SWDD-CloudDataIngestion.docx` Section 10 for the full list of known issues and mitigations, and Section 11 for the complete access-control listing.

## Teardown

```bash
# Empty the buckets first -- CloudFormation won't delete non-empty buckets
for b in <bucket-a> <bucket-b> <bucket-c>; do
  aws s3 rm "s3://$b" --recursive
done

# Delete any SSM interface endpoints created during troubleshooting (if applicable)
aws ec2 delete-vpc-endpoints --vpc-endpoint-ids <endpoint-id-1> <endpoint-id-2> <endpoint-id-3>

aws cloudformation delete-stack --stack-name ingest-zone
```

## Course context

Built for TESU CLD-410, Developing in Cloud, as the Cloud-Based Data Ingestion Zone application activity. See `SWDD-CloudDataIngestion.docx` for the full Software Design Document, including architecture rationale, data design, and the deployment/validation checklist.
