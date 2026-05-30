# Production Deployment Plan — Extracta AI

> This document covers the full path from local → production: hosting, CI/CD, domain,
> SSL, secrets, monitoring, and cost. Get it running and passing tests locally first
> (see the **Testing** section in `../README.md`) — nothing goes to production until
> it passes locally.

---

## Recommended Production Stack

| Layer | Service | Why |
|-------|---------|-----|
| Frontend | **Vercel** | Free CDN, auto SSL, instant React/Vite deploys, zero config |
| Backend API | **Railway** | Cheapest managed FastAPI hosting ($5–20/mo), auto-deploy from GitHub |
| Celery Worker | **Railway** (separate service) | Same image, different start command |
| Database | **MongoDB Atlas** | Free M0 for dev → M10 ($57/mo) for production |
| Redis | **Upstash** | Free 10k req/day, pay-per-request after, no idle cost |
| Object Storage | **Cloudflare R2** | S3-compatible, **free egress**, $0.015/GB stored |
| GPU Inference | **Modal** | Pay-per-second, scales to zero, no reserved GPU cost |
| Domain + DNS + CDN | **Cloudflare** | Free DNS, DDoS protection, free CDN for API |
| CI/CD | **GitHub Actions** | Free for public repos, 2,000 min/month for private |
| Monitoring | **Sentry** (errors) + **UptimeRobot** (uptime) | Both have free tiers |
| SSL | Automatic via Vercel + Railway | No manual cert management |

**Estimated monthly cost at launch (0–100 users):** ~$15–40/month  
**Estimated monthly cost at growth (1,000 users, 10k pages/day):** ~$150–300/month

---

## Part 1 — Repository Setup (GitHub)

### 1.1 Initialize Git Repository

```bash
cd /home/vinay8671/Development-projects/MVP/SaaS-OCR_Expense_Intelligence

git init
git branch -M main

# A .gitignore already ships with the repo; this recreates it if starting fresh
cat > .gitignore << 'EOF'
# Python
.venv/
__pycache__/
*.pyc
*.pyo
.pytest_cache/
*.egg-info/

# Environment
.env
.env.local
.env.production

# Uploads (local dev only — production uses S3)
/data/
uploads/

# Node
frontend/node_modules/
frontend/dist/

# Docker
*.log

# IDE
.vscode/
.idea/
EOF

git add .
git commit -m "Initial commit: Extracta AI FARM stack foundation"
```

### 1.2 Create GitHub Repository

1. Go to github.com → New Repository
2. Name: `extracta-ai` (or your preferred name)
3. Visibility: **Private** (contains business logic)
4. Do NOT initialize with README (you already have one)

```bash
git remote add origin https://github.com/YOUR_USERNAME/extracta-ai.git
git push -u origin main
```

### 1.3 Branch Strategy

```
main          → production (protected, requires PR + passing CI)
staging       → staging environment (auto-deploys to Railway staging)
dev/*         → feature branches (merged via PR into staging)
```

Protect `main` in GitHub → Settings → Branches → Add rule:
- Require pull request reviews before merging
- Require status checks to pass (CI must be green)
- Do not allow bypassing the above settings

---

## Part 2 — Domain Setup

### 2.1 Buy a Domain

Recommended registrar: **Namecheap** (~$10–15/year for `.com`)

Suggested names for this product:
- `extracta.ai` (premium, ~$200+)
- `getextracta.com` (~$12/year)
- `useextracta.com` (~$12/year)
- `extractaai.com` (~$12/year)

### 2.2 Move DNS to Cloudflare (Free)

1. Create a free Cloudflare account at cloudflare.com
2. Add your domain → Cloudflare will scan existing DNS records
3. In Namecheap, change nameservers to Cloudflare's (e.g. `aria.ns.cloudflare.com`)
4. Wait 24–48h for propagation

**Why Cloudflare for DNS:** Free DDoS protection, free CDN for your API, analytics, bot management, and SSL certificate management all from one dashboard.

### 2.3 DNS Records to Create

In Cloudflare DNS panel:

| Type | Name | Value | Proxy | Purpose |
|------|------|-------|-------|---------|
| CNAME | `@` or `www` | `cname.vercel-dns.com` | DNS only (grey) | Frontend on Vercel |
| CNAME | `api` | your-app.railway.app | Proxied (orange) | Backend API |
| MX | `@` | Cloudflare Email or your mail provider | — | Email (for transactional later) |
| TXT | `@` | `v=spf1 include:... ~all` | — | Email SPF |

---

## Part 3 — Frontend Deployment (Vercel)

### 3.1 Connect to Vercel

1. Go to vercel.com → Import Project → Connect GitHub
2. Select your `extracta-ai` repository
3. Set **Root Directory** to `frontend`
4. Framework Preset: **Vite**
5. Build Command: `npm run build`
6. Output Directory: `dist`

### 3.2 Environment Variables in Vercel

In Vercel → Project → Settings → Environment Variables:

```
VITE_API_URL=https://api.yourdomain.com
```

The frontend already reads `VITE_API_URL` in `frontend/src/api/client.js` (falling
back to `http://localhost:8000`), so set it per environment instead of hardcoding.

**`frontend/src/api/client.js` (already wired this way):**
```javascript
const client = axios.create({
  baseURL: import.meta.env.VITE_API_URL || 'http://localhost:8000',
});
```

When API-key auth lands (Phase A), add the header here too:
```javascript
client.defaults.headers.common['X-API-Key'] = localStorage.getItem('api_key') ?? '';
```

### 3.3 Add Custom Domain in Vercel

1. Vercel → Project → Settings → Domains
2. Add `yourdomain.com` and `www.yourdomain.com`
3. Vercel gives you a CNAME value — add it in Cloudflare DNS
4. SSL is issued automatically by Vercel

### 3.4 Automatic Deployments

Every push to `main` triggers a production deploy on Vercel automatically.  
Every PR gets a unique preview URL (e.g. `extracta-ai-git-feature-branch.vercel.app`).

---

## Part 4 — Backend Deployment (Railway)

Railway is recommended over Heroku (expensive), Render (slower cold starts), and raw VPS (too much ops overhead at this stage).

### 4.1 Setup Railway Project

1. Go to railway.app → New Project → Deploy from GitHub Repo
2. Select `extracta-ai`
3. Railway auto-detects the Dockerfile — point it to `backend/Dockerfile`

### 4.2 Create Two Services in Railway

**Service 1: API**
- Source: GitHub repo, `backend/Dockerfile`
- Start command: `uvicorn main:app --host 0.0.0.0 --port 8000`
- Health check path: `/health/ready`

**Service 2: Worker**
- Source: Same GitHub repo, same `backend/Dockerfile`
- Start command: `celery -A celery_app worker -l INFO -Q default,standard,bulk`
- No health check needed (Celery manages its own)

### 4.3 Environment Variables in Railway

Set these in Railway → Service → Variables for **both** API and Worker services:

```env
# MongoDB Atlas
MONGODB_URL=mongodb+srv://user:pass@cluster.mongodb.net/expense_intelligence?retryWrites=true&w=majority

# Upstash Redis
REDIS_URL=rediss://default:TOKEN@HOST.upstash.io:6379

# Cloudflare R2 (S3-compatible)
S3_ENDPOINT_URL=https://ACCOUNT_ID.r2.cloudflarestorage.com
AWS_ACCESS_KEY_ID=your-r2-access-key
AWS_SECRET_ACCESS_KEY=your-r2-secret-key
UPLOAD_BUCKET=extracta-uploads

# App config
SECRET_KEY=your-random-64-char-secret        # generate: openssl rand -hex 32
ALLOWED_ORIGINS=https://yourdomain.com,https://www.yourdomain.com

# Modal (GPU)
MODAL_TOKEN_ID=ak-...
MODAL_TOKEN_SECRET=as-...

# Sentry
SENTRY_DSN=https://...@sentry.io/...
```

### 4.4 Custom Domain for API

1. Railway → Service (API) → Settings → Networking → Custom Domain
2. Add `api.yourdomain.com`
3. Railway provides a CNAME value — add it in Cloudflare DNS (proxied/orange)
4. Railway auto-issues Let's Encrypt SSL

---

## Part 5 — MongoDB Atlas Setup

### 5.1 Create Cluster

1. cloud.mongodb.com → Create → Shared (free M0 for staging, M10 for production)
2. Cloud provider: **AWS** (same region as Railway — `us-east-1` recommended)
3. Cluster name: `extracta-prod`

### 5.2 Database User

1. Database Access → Add User
2. Username: `extracta-app`
3. Password: generate a strong password (save it)
4. Role: `readWrite` on database `expense_intelligence`

### 5.3 Network Access

1. Network Access → Add IP Address
2. For Railway: Add `0.0.0.0/0` (allow all) — Railway IPs are dynamic
3. For extra security: use Railway's static IP add-on ($5/month) and whitelist only that IP

### 5.4 Connection String

Copy from Atlas → Connect → Drivers:
```
mongodb+srv://extracta-app:PASSWORD@cluster.mongodb.net/expense_intelligence?retryWrites=true&w=majority
```

---

## Part 6 — Upstash Redis Setup

1. console.upstash.com → Create Database
2. Region: `us-east-1` (match Railway region)
3. TLS: **enabled** (required for `rediss://`)
4. Copy the connection string (starts with `rediss://`)

Free tier: 10,000 requests/day, 256MB storage — enough for development.  
Production: pay-per-request model, ~$0.20 per 100k requests.

---

## Part 7 — Cloudflare R2 Storage

### 7.1 Create R2 Bucket

1. Cloudflare Dashboard → R2 → Create Bucket
2. Name: `extracta-uploads`
3. Location: `EEUR` or `ENAM` (match Railway region)

### 7.2 Create API Token for R2

1. R2 → Manage R2 API Tokens → Create API Token
2. Permissions: `Object Read & Write`
3. Specify bucket: `extracta-uploads`
4. Save Access Key ID and Secret Access Key

### 7.3 Cost Comparison vs AWS S3

| | AWS S3 | Cloudflare R2 |
|---|---|---|
| Storage | $0.023/GB | $0.015/GB |
| Egress | $0.09/GB | **$0.00** |
| Operations | $0.005/1k PUT | $4.50/million |

R2 is ~40% cheaper on storage and **free egress** — critical when returning document images to the frontend.

---

## Part 8 — CI/CD with GitHub Actions

Create `.github/workflows/deploy.yml`:

```yaml
name: CI/CD

on:
  push:
    branches: [main, staging]
  pull_request:
    branches: [main, staging]

jobs:
  test:
    name: Test Backend
    runs-on: ubuntu-latest
    services:
      mongo:
        image: mongo:7
        ports: ['27017:27017']
      redis:
        image: redis:7-alpine
        ports: ['6379:6379']
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python 3.9
        uses: actions/setup-python@v5
        with:
          python-version: '3.9'
          cache: pip

      - name: Install dependencies
        run: pip install -r requirements.txt pytest pytest-asyncio httpx mongomock-motor fakeredis

      - name: Run tests
        working-directory: backend
        env:
          MONGODB_URL: mongodb://localhost:27017
          REDIS_URL: redis://localhost:6379/0
          SECRET_KEY: test-secret
          UPLOAD_BUCKET: test-bucket
          S3_ENDPOINT_URL: http://localhost:9000
          AWS_ACCESS_KEY_ID: test
          AWS_SECRET_ACCESS_KEY: test
        run: pytest tests/ -v --tb=short

  lint:
    name: Lint
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.9'
      - run: pip install ruff
      - run: ruff check backend/

  deploy-staging:
    name: Deploy to Staging
    needs: [test, lint]
    if: github.ref == 'refs/heads/staging'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Deploy to Railway (staging)
        uses: railway-app/railway-action@v1
        with:
          service: extracta-staging
          token: ${{ secrets.RAILWAY_TOKEN_STAGING }}

  deploy-production:
    name: Deploy to Production
    needs: [test, lint]
    if: github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Deploy to Railway (production)
        uses: railway-app/railway-action@v1
        with:
          service: extracta-production
          token: ${{ secrets.RAILWAY_TOKEN_PROD }}
      - name: Notify Sentry of deployment
        uses: getsentry/action-release@v1
        with:
          environment: production
        env:
          SENTRY_AUTH_TOKEN: ${{ secrets.SENTRY_AUTH_TOKEN }}
          SENTRY_ORG: your-org
          SENTRY_PROJECT: extracta-backend
```

### GitHub Secrets to Set

In GitHub → Settings → Secrets and Variables → Actions:

```
RAILWAY_TOKEN_STAGING    # Railway token for staging service
RAILWAY_TOKEN_PROD       # Railway token for production service
SENTRY_AUTH_TOKEN        # Sentry auth token
```

---

## Part 9 — Modal GPU Deployment

### 9.1 Deploy the GPU Worker

```bash
# Install Modal CLI
pip install modal

# Authenticate
modal setup   # opens browser for auth

# Deploy the GPU worker as a Modal app
modal deploy backend/ml/modal_worker.py
```

### 9.2 `backend/ml/modal_worker.py` structure

```python
import modal

app = modal.App("extracta-ocr")

image = (
    modal.Image.debian_slim()
    .pip_install("transformers", "torch", "pillow", "qwen-vl-utils")
)

@app.function(
    image=image,
    gpu="A10G",
    keep_warm=1,           # keep 1 warm worker during business hours
    timeout=120,
    secrets=[modal.Secret.from_name("extracta-secrets")]
)
def extract_with_qwen(image_bytes: bytes, schema: dict) -> dict:
    # VLM inference here
    pass
```

### 9.3 Cost Control

- `keep_warm=1`: ~$0.40/hr when idle. Toggle off nights/weekends until you have revenue.
- A10G is sufficient for 7B model. Only upgrade to A100 for 72B enterprise route.
- Monitor GPU spend in Modal dashboard → set a budget alert at $50/month.

---

## Part 10 — Alternative: VPS Deployment

Use this if you prefer full control over infrastructure (recommended when GPU spend > $500/month).

### Recommended VPS Providers

| Provider | Spec | Monthly Cost | Best For |
|----------|------|-------------|----------|
| Hetzner CX21 | 3 vCPU, 4GB RAM | €4.51 | Early stage, Europe users |
| DigitalOcean Basic | 2 vCPU, 4GB RAM | $24 | North America users |
| Hetzner CCX13 | 2 vCPU, 8GB RAM | €12.49 | More memory for worker |

### VPS Setup Steps

```bash
# 1. Create server (Ubuntu 22.04 LTS)

# 2. SSH in and run initial setup
ssh root@YOUR_SERVER_IP

# 3. Create non-root user
adduser deploy
usermod -aG sudo deploy
rsync --archive --chown=deploy:deploy ~/.ssh /home/deploy

# 4. Harden SSH
sed -i 's/PermitRootLogin yes/PermitRootLogin no/' /etc/ssh/sshd_config
sed -i 's/#PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config
systemctl restart sshd

# 5. Install Docker
curl -fsSL https://get.docker.com | sh
usermod -aG docker deploy

# 6. Install Caddy (handles SSL automatically)
apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/caddy-stable-archive-keyring.gpg] https://dl.cloudsmith.io/public/caddy/stable/deb/debian any-release main" | tee /etc/apt/sources.list.d/caddy-stable.list
apt update && apt install caddy

# 7. Deploy app
su - deploy
git clone https://github.com/YOUR_USERNAME/extracta-ai.git
cd extracta-ai
cp .env.example .env
# Edit .env with production values
nano .env
docker compose -f docker-compose.prod.yml up -d
```

### Caddy Configuration (`/etc/caddy/Caddyfile`)

```caddyfile
api.yourdomain.com {
    reverse_proxy localhost:8000
}

yourdomain.com {
    reverse_proxy localhost:3000
}
```

Caddy auto-issues Let's Encrypt SSL — no certbot needed.

```bash
systemctl reload caddy
```

### VPS `docker-compose.prod.yml`

```yaml
services:
  backend:
    build:
      context: .
      dockerfile: backend/Dockerfile
    restart: always
    env_file: .env
    depends_on: [mongo, redis]

  worker:
    build:
      context: .
      dockerfile: backend/Dockerfile
    command: celery -A celery_app worker -l INFO
    restart: always
    env_file: .env
    depends_on: [mongo, redis]

  celery-beat:
    build:
      context: .
      dockerfile: backend/Dockerfile
    command: celery -A celery_app beat -l INFO
    restart: always
    env_file: .env
    depends_on: [mongo, redis]

  mongo:
    image: mongo:7
    restart: always
    volumes:
      - mongo_data:/data/db
    # DO NOT expose port 27017 publicly on VPS

  redis:
    image: redis:7-alpine
    restart: always
    volumes:
      - redis_data:/data
    # DO NOT expose port 6379 publicly on VPS

  frontend:
    build: ./frontend
    restart: always

volumes:
  mongo_data:
  redis_data:
```

**Important:** On VPS, do not expose Mongo (27017) or Redis (6379) ports publicly. Only Caddy (80/443) should be publicly accessible.

---

## Part 11 — Secrets Management

Never commit secrets to git. Use one of these patterns:

### Option A: Railway Variables (recommended for Railway)

Set all env vars in Railway dashboard → automatically injected at runtime.

### Option B: GitHub Actions Secrets + Deployment

Secrets stored in GitHub → injected as env vars during deployment step.

### Option C: AWS Secrets Manager (when you need audit trail)

```python
# backend/config.py
import boto3
import json

def get_secret(secret_name: str) -> dict:
    client = boto3.client("secretsmanager", region_name="us-east-1")
    return json.loads(client.get_secret_value(SecretId=secret_name)["SecretString"])
```

Use for production when SOC2 preparation begins (Phase E).

---

## Part 12 — Monitoring & Alerting

### 12.1 Sentry (Error Tracking)

```bash
pip install sentry-sdk[fastapi]
```

```python
# backend/main.py
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.celery import CeleryIntegration

sentry_sdk.init(
    dsn=os.getenv("SENTRY_DSN"),
    integrations=[FastApiIntegration(), CeleryIntegration()],
    traces_sample_rate=0.1,
    environment=os.getenv("ENVIRONMENT", "development"),
)
```

Free tier: 5,000 errors/month. Alerts via email/Slack on new errors.

### 12.2 UptimeRobot (Uptime Monitoring)

1. uptimerobot.com → Free account
2. Add monitor: `https://api.yourdomain.com/health/ready`
3. Check interval: 5 minutes
4. Alert contact: your email + Slack webhook

Free tier: 50 monitors, 5-minute checks.

### 12.3 MongoDB Atlas Monitoring

Atlas includes built-in monitoring for:
- Query performance (slow queries > 100ms)
- Index usage
- Disk IOPS
- Connection count

Enable alerts: Atlas → Alerts → Add Alert for "Query Targeting Scanned Objects > 1000" (catches missing indexes).

---

## Part 13 — Deployment Runbook

Step-by-step for each new release:

### Pre-deploy
```bash
# 1. All tests passing locally
cd backend && pytest tests/ -v

# 2. Feature branch merged to staging via PR
# 3. Staging environment tested (Railway staging service auto-deploys)
# 4. PR reviewed and approved
```

### Deploy to Production
```bash
# 1. Merge staging → main via PR
# GitHub Actions CI runs → green → auto-deploys to Railway

# 2. Watch deployment logs in Railway dashboard

# 3. Verify health endpoint
curl https://api.yourdomain.com/health/ready
# Expected: {"status":"ready","mongo":"up","redis":"up"}

# 4. Smoke test
curl -X POST https://api.yourdomain.com/v1/extract \
  -H "X-API-Key: YOUR_PROD_KEY" \
  -F "file=@test_fixtures/receipt_walmart.jpg"

# 5. Watch Sentry for any new errors (first 30 minutes post-deploy)
```

### Rollback
```bash
# Railway: Deployments → Previous deployment → Rollback
# Or: revert the commit and push to main (triggers fresh deploy of old code)
git revert HEAD
git push origin main
```

---

## Part 14 — Cost Breakdown by Stage

### Stage 1: Development & Testing (months 1–2)
| Service | Cost |
|---------|------|
| MongoDB Atlas M0 | $0 |
| Upstash Redis | $0 |
| Railway (hobby) | $5 |
| Vercel (hobby) | $0 |
| Cloudflare R2 | $0 (free 10GB) |
| Modal GPU | $0 (free $30 credit) |
| Domain + Cloudflare | $12/year |
| **Total** | **~$5–6/month** |

### Stage 2: First Customers (months 3–6, ~10 tenants)
| Service | Cost |
|---------|------|
| MongoDB Atlas M10 | $57 |
| Upstash Redis | ~$5 |
| Railway (pro) | $20 |
| Vercel (pro) | $20 |
| Cloudflare R2 | ~$2 |
| Modal GPU (Starter volume) | ~$30 |
| Sentry | $0 (free tier) |
| **Total** | **~$134/month** |

### Stage 3: Growth (months 7–12, ~100 tenants)
| Service | Cost |
|---------|------|
| MongoDB Atlas M30 | $185 |
| Upstash Redis | $20 |
| Railway (scale) | $50–100 |
| Vercel (pro) | $20 |
| Cloudflare R2 | $10 |
| Modal GPU (sustained) | $100–200 |
| Sentry Team | $26 |
| **Total** | **~$400–560/month** |

At ~$500+/month GPU spend on Modal, evaluate moving to a reserved Hetzner GPU server (H100 SXM5 at ~€3.49/hr on-demand, or reserved for less).

---

## Part 15 — Security Checklist Before Going Live

- [ ] `.env` file is in `.gitignore` and never committed
- [ ] All secrets loaded from environment variables, not hardcoded
- [ ] MongoDB port 27017 not exposed publicly (VPS) or Atlas IP whitelist set
- [ ] Redis port 6379 not exposed publicly
- [ ] Mongo-Express service removed or password-protected in production
- [ ] `DEBUG=0` and `LOG_LEVEL=INFO` in production
- [ ] CORS `ALLOWED_ORIGINS` set to production domain only (not `*`)
- [ ] Rate limiting active on all public endpoints
- [ ] API keys required on all `/v1/*` endpoints
- [ ] HTTPS enforced (Caddy/Railway/Vercel all handle this)
- [ ] `X-Content-Type-Options: nosniff` header set
- [ ] File upload limited to 15MB and image/PDF content types only
- [ ] Sentry DSN set and error alerting active
- [ ] UptimeRobot monitoring `/health/ready`
- [ ] MongoDB Atlas slow query alerts configured

---

## Summary: Recommended Path

```
Week 1:  Git repo → GitHub → MongoDB Atlas (free) → Upstash → R2 bucket → Railway (staging)
Week 2:  Domain → Cloudflare DNS → Vercel frontend → API subdomain → GitHub Actions CI
Week 3:  Phase A code complete → Pass local tests → Deploy to staging → Smoke test
Week 4:  Staging validated → Merge to main → First production deploy → First real tenant
Week 5+: Phase B/C/D → Deploy incrementally via same pipeline
```

---

*Last updated: April 2026. Review service pricing before committing — Railway, Atlas, and Upstash pricing change periodically.*
