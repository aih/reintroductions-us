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
| `deploy/install-crons.sh` | `/etc/cron.d/reintro`: the update daily at 08:17 UTC |

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
  50 KB, about 20 typical bills. A bill over the limit gets its XML only, and the bill page shows
  no PDF link. The daily update is 73 s of CPU for the load plus usc-run's downloads.
- **Disk.** About 7 GB: congress data 3.5 GB with every bill type, the database 0.6 GB, the draft
  and PDF caches up to 2 GB, and room for the next Congress. It goes on a volume of its own at
  `/var/lib/reintro`, off the root volume (the root disk filled once already, uscode-redesign
  ADR-0086). The image (0.6 GB) is on the root volume with the others.

Rendering every PDF ahead of time does not fit the box: about 6–7 CPU-hours for the 16,447
eligible bills of the 119th, about 1.5 GB of PDFs, and 2.7 GB of memory for the longest bills.
That is the serverless job below.

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

## Serverless pre-generation

A job that renders every draft and PDF ahead of time, off the box:

| Part | |
|---|---|
| Trigger | EventBridge after the daily update, or once a new US Code release point appears |
| Planner | Reads `reintro.db`, lists bills whose draft key changed (bill text version, target Congress, US Code release point), and sends one SQS message per bill |
| Worker | Lambda from this image (arm64), 2 GB, 4 GB for bills over 1 MB of source, 15-minute limit; builds the draft, runs the citation checks, renders the PDF, writes XML, PDF and warnings JSON to S3 |
| Citation checks | The planner collects the cited US Code sections and Public Laws of the whole batch and fetches each once with the tokens; the workers read those answers instead of calling the two sites per bill |
| Serving | The box syncs the S3 prefix into `${DATA_ROOT}/cache` (same key as on-request drafts and PDFs), or the site redirects a PDF download to a signed S3 URL |

A full run of the 119th is about 23,000 Lambda-seconds at 2 GB, about $1; a day's changed bills
cost cents; S3 holds about 1.8 GB, under $0.05 a month. Building it needs an AWS administrator for
the IAM role, the queue, the function and the bucket prefix, as for the other sites' backups.
Until it exists, the on-request limits above apply and the 14 bills over 1 MB have XML only.
