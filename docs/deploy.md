# Deploying reintro.linkedlegislation.org

The site runs on the EC2 box that serves uscode.linkedlegislation.org and
statutes.linkedlegislation.org: `i-06b433caacd78fd96`, a `t4g.large` in us-east-1 (2 vCPU,
7.8 GB memory, 30% baseline CPU per vCPU on credits), Elastic IP `52.1.30.78`. It is a third
compose project, `reintroductions`, behind the shared edge Caddy that statutes-at-large keeps in
`deploy/edge`.

```
:443 ── edge caddy ─┬── uscode.linkedlegislation.org   → uscode-proxy:8000
                    ├── statutes.linkedlegislation.org → statutes-proxy:8000
                    └── reintro.linkedlegislation.org  → reintro-proxy:8000 ── app:8020
```

| File | |
|---|---|
| `Dockerfile` | Python 3.13, WeasyPrint's Pango and DejaVu, `uv sync --extra ingest` (usc-run) |
| `docker-compose.prod.yml` | `app` (uvicorn, 1 GB), `proxy` (Caddy, 64 MB, alias `reintro-proxy` on `edge`), `jobs` (the update, 1 GB, profile `jobs`) |
| `deploy/Caddyfile` | robots `Disallow: /`, crawler User-Agents refused, the cookie gate, security headers |
| `deploy/deploy-on-box.sh` | Checkout, Caddyfile validation, build, `up -d --wait`, a check through the edge |
| `deploy/update.sh` | `reintro update` for the current Congress, then `reintro link` against the one before |
| `deploy/install-crons.sh` | `/etc/cron.d/reintro`: the update daily at 08:17 UTC, the PDF sync every 30 minutes |
| `deploy/aws-setup.sh` | One-time AWS setup for the pre-generated PDFs (CloudShell) |
| `lambda/Dockerfile`, `deploy/lambda-deploy.sh`, `.github/workflows/pdf-lambda.yml` | The PDF renderer on Lambda |
| `deploy/pregen-sync.sh` | Rendered PDFs from S3 into `${DATA_ROOT}/cache/pdf` |

## Sizing

Measured on a workstation over the 119th Congress (16,619 bills, 16,560 with text):

| | |
|---|---|
| App container at rest | 271 MB |
| Proxy container | 12 MB |
| Draft of a typical bill (convert + reintroduce, no checks) | 0.01 s median |
| PDF of a typical bill (40-bill sample) | 1.3 s CPU median, 76 KB |
| PDF of a bill at 0.97 MB of source (`hr4275-119`) | 19 s CPU, 420 MB peak, 0.7 MB |
| PDF of the longest bill (`s2296-119`, 9.0 MB of source) | 149 s CPU, 2.7 GB peak, 5.5 MB |
| `reintro load --congress 119` | 103 s, 73 s CPU, 398 MB peak |
| usc-run data, 118th and 119th, `hr` and `s` | 2.7 GB |
| `reintro.db`, both Congresses | 585 MB |
| Image | 563 MB |

Source sizes: median 9 KB, p90 37 KB, p99 210 KB; 34 bills over 500 KB, 14 over 1 MB (44 MB in
all), 7 over 2 MB.

On the box this comes to:

- **Memory.** The box had 2.9 GB available before the statutes site arrived; that site's limits
  add up to 1.7 GB. This site uses about 300 MB serving, up to 1 GB while a long PDF renders (the
  `app` limit), and up to 1 GB more for the few minutes of the daily update (`jobs`). The 6 GB
  swapfile backs it. Check `free -m` before the first deploy.
- **CPU.** Pages and XML downloads cost milliseconds. A PDF is rendered on request only for a bill
  of at most 1 MB of source text, one at a time (`REINTRO_PDF_MAX_SOURCE_BYTES`,
  `REINTRO_PDF_CONCURRENCY`), and kept in `${DATA_ROOT}/cache/pdf` by the draft's hash, so each
  is rendered once. The PDFs in one zip download share a 1 MB budget, each counted at least
  50 KB, about 20 typical bills. A bill over the limit with no pre-generated PDF gets its XML
  only, and the bill page shows no PDF link. A PDF in the cache is served whatever the bill's
  length. The daily update is 73 s of CPU for the load plus usc-run's downloads.
- **Disk.** About 7 GB: congress data 3.5 GB with every bill type, the database 0.6 GB, the draft
  and PDF caches up to 2 GB, and room for the next Congress. It goes on a volume of its own at
  `/var/lib/reintro`, off the root volume (the root disk filled once already, uscode-redesign
  ADR-0086). The image (0.6 GB) is on the root volume with the others.

Rendering every PDF ahead of time does not fit the box: about 6–7 CPU-hours for the 16,447
eligible bills of the 119th, about 1.5 GB of PDFs, and 2.7 GB of memory for the longest bills.
Lambda renders them instead (below).

## First deploy

All steps on the box as `ec2-user` unless marked root. Reach it with
`aws ssm start-session --target i-06b433caacd78fd96`.

1. **Data volume.** A 20 GB gp3 volume in us-east-1a, attached as `/dev/sdh` (from a workstation
   with EC2 rights):

   ```bash
   aws ec2 create-volume --availability-zone us-east-1a --size 20 --volume-type gp3 \
       --tag-specifications 'ResourceType=volume,Tags=[{Key=Name,Value=reintro-data}]'
   aws ec2 attach-volume --volume-id vol-… --instance-id i-06b433caacd78fd96 --device /dev/sdh
   ```

   On the box, as root, find the new device with `lsblk` (Nitro names it `/dev/nvmeXn1`; check the
   size before formatting), then mount it by UUID. A device name in `/etc/fstab` can change when
   volumes are reattached (uscode-redesign ADR-0086).

   ```bash
   mkfs.xfs /dev/nvmeXn1
   mkdir -p /var/lib/reintro
   echo "UUID=$(blkid -s UUID -o value /dev/nvmeXn1) /var/lib/reintro xfs defaults,nofail 0 2" >> /etc/fstab
   mount /var/lib/reintro && findmnt /var/lib/reintro
   chown ec2-user: /var/lib/reintro
   ```

2. **Checkout.**

   ```bash
   cd ~ && git clone https://github.com/aih/reintroductions-us.git && cd reintroductions-us
   ```

3. **`.env`** (mode 600):

   ```bash
   cat > .env <<'EOF'
   SITE_ADDRESS=http://reintro.linkedlegislation.org:8000
   DATA_ROOT=/var/lib/reintro
   USCODE_API_TOKEN=usc_…
   STATUTES_API_TOKEN=stat_…
   EOF
   chmod 600 .env
   ```

   The tokens are optional; `docs/api-tokens.md` has how to make them. `GATE_HOST=gate.invalid`
   turns the cookie gate off; `REINTRO_CONGRESS=119` pins the Congress the update fetches.

4. **The site.**

   ```bash
   bash deploy/deploy-on-box.sh
   ```

   It builds the image (a few minutes the first time) and starts `app` and `proxy`. The check
   through the edge fails until steps 6 and 7 are done.

5. **The data.** The first fetch downloads both Congresses from govinfo (about 3.5 GB) and loads
   them:

   ```bash
   C="docker compose -f docker-compose.prod.yml --profile jobs run --rm --no-deps jobs"
   $C reintro update --congress 118
   $C reintro update --congress 119
   $C reintro link --older 118 --newer 119
   ```

   The site serves while this runs; search fills in as each Congress loads.

6. **DNS.** An A record `reintro.linkedlegislation.org → 52.1.30.78`, or a CNAME to
   `uscode.linkedlegislation.org`.

7. **The edge.** Once statutes-at-large carries the `reintro.linkedlegislation.org` block in
   `deploy/edge/Caddyfile`, in that checkout:

   ```bash
   cd ~/statutes-at-large && git pull
   docker compose -p edge -f deploy/edge/docker-compose.yml up -d --force-recreate --wait
   ```

   Caddy obtains the certificate on the first request after the DNS record resolves. Both other
   sites are unreachable for the few seconds of the recreate.

8. **The schedule**, as root:

   ```bash
   bash /home/ec2-user/reintroductions-us/deploy/install-crons.sh
   ```

Checks:

```bash
curl -s https://reintro.linkedlegislation.org/healthz
curl -s https://reintro.linkedlegislation.org/robots.txt
curl -s -o /dev/null -w '%{http_code}\n' https://reintro.linkedlegislation.org/bills/hr206-119              # 403, the gate
curl -s -o /dev/null -w '%{http_code}\n' -b reintro_h=1 https://reintro.linkedlegislation.org/bills/hr206-119  # 200
curl -s 'https://reintro.linkedlegislation.org/api/v1/search?q=hr206' | head -c 300
```

## Later deploys

```bash
cd ~/reintroductions-us && bash deploy/deploy-on-box.sh            # origin/main
bash deploy/deploy-on-box.sh <sha>                                 # a commit, or a rollback
```

Logs: `${DATA_ROOT}/logs/deploy.log`, `${DATA_ROOT}/logs/update.log`, and
`docker compose -f docker-compose.prod.yml logs app proxy`.

A token change in `.env` takes effect after
`docker compose -f docker-compose.prod.yml up -d --no-deps --force-recreate --wait app`.

## Pre-generated PDFs

PDFs are rendered ahead of time by the Lambda function `reintro-pdf`; the box builds the drafts
and copies the PDFs back.

```
box: reintro pregen ── XML → s3://reintro-linkedlegislation/in/ ── message → SQS reintro-pdf
                                                                          │
Lambda reintro-pdf (arm64, 4096 MB, 900 s, 10 at once) ◄──────────────────┘
   └── PDF → s3://reintro-linkedlegislation/pdf/{bill}-{xml hash}-1.pdf
box: reintro pregen-sync ── pdf/ → ${DATA_ROOT}/cache/pdf/ ── served by the site
```

- `reintro pregen --congress N` (`reintro/pregen.py`) builds each eligible bill's draft with the
  site's own cache and citation checks, so the XML hash, and with it the PDF's name, is the one
  the site looks up. It skips a bill whose PDF is on disk or in `pdf/`, and one whose XML is
  already in `in/` (in flight; `--resend` queues it again). `deploy/update.sh` runs it after the
  daily load once `REINTRO_PREGEN_BUCKET` is in `.env`; the first run builds every draft of the
  Congress, which takes hours of mostly network time for the citation checks.
- The function (`reintro.pregen.handler`, `lambda/Dockerfile`) reads the XML, renders the PDF
  and writes it to `pdf/`. A message that fails twice goes to `reintro-pdf-dlq`. `in/` expires
  after 14 days.
- `deploy/pregen-sync.sh` copies new PDFs into the cache every 30 minutes.
- A draft changes when the bill's text, the target Congress or the US Code release point the
  checks read changes; the next `pregen` queues it again and the old PDF stays unused.

A full run of the 119th is about 23,000 Lambda-seconds at 4 GB, about $1.25, and 40 minutes at 10
at once; a day's changed bills cost cents; S3 holds about 1.8 GB, under $0.05 a month. In the
Lambda image a 0.97 MB bill rendered in 16.8 s at 392 MB.

### Setup, once

1. **AWS**, in CloudShell (us-east-1) as an administrator, from a checkout of this repository:

   ```bash
   git clone https://github.com/aih/reintroductions-us.git && cd reintroductions-us
   bash deploy/aws-setup.sh
   ```

   It creates the bucket `reintro-linkedlegislation` (`BUCKET=…` to name another), the queue
   and its dead-letter queue, the ECR repository, the log group, the function's role, the
   policy `reintro-pdf` on the instance role `uscode-site`, and the GitHub deploy role
   `reintro-github-deploy`, and prints the values for the next two steps.

2. **GitHub**, Settings → Secrets and variables → Actions → Variables: `AWS_DEPLOY_ROLE_ARN`,
   `REINTRO_PREGEN_BUCKET`, `REINTRO_PREGEN_QUEUE_ARN`. Then Actions → pdf-lambda → Run
   workflow. It builds the image on an arm64 runner, pushes it, creates the function and its
   queue trigger. Later pushes to `main` that touch `reintro/` or `lambda/` redeploy it.

3. **The box**, `.env`: `REINTRO_PREGEN_BUCKET=…` and `REINTRO_PREGEN_QUEUE_URL=…`. Then a
   first run, a few bills at a time:

   ```bash
   C="docker compose -f docker-compose.prod.yml --profile jobs run --rm --no-deps jobs"
   $C reintro pregen --congress 119 --limit 20
   $C reintro pregen-sync
   ```

   and the rest in the background:

   ```bash
   nohup $C reintro pregen --congress 119 >> /var/lib/reintro/logs/pregen.log 2>&1 &
   ```

Checks: `aws sqs get-queue-attributes --queue-url … --attribute-names
ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible`, the dead-letter queue's count,
`aws logs tail /aws/lambda/reintro-pdf --since 1h`, and `${DATA_ROOT}/logs/pregen-sync.log`.
