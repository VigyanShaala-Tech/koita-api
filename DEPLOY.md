# Deploying the Koita KPI API (AWS EC2 behind the existing load balancer)

Result: `https://koita-api.vigyanshaala.in/getToken` and `/getdata`, always on.
The EC2 instance already sits behind an AWS Application Load Balancer (security group `ALB-SG`), the same way
Metabase (port 3000) and the 850x apps do. The ALB handles HTTPS, so there is no nginx or certbot.

```
DesignScape --HTTPS--> ALB (AWS certificate) --HTTP :8000--> main.py on EC2 --> Metabase
                       rule: host = koita-api.vigyanshaala.in    (systemd keeps it running)
```

## Part A: on the EC2 instance (web terminal)

### 1. Check port 8000 is free
```
whoami                          # should print: ubuntu
python3 --version               # 3.8 or newer
sudo ss -tlnp | grep ':8000 '   # should print nothing. If something shows, pick another port (e.g. 8506) and use it everywhere below
```

### 2. Install and create the service user
```
sudo apt update && sudo apt install -y git python3
sudo useradd --system --no-create-home koita
```

### 3. Get the code from GitHub
The repo is private, so EC2 gets a **read-only deploy key** that can pull this one repo and nothing else:
```
ssh-keygen -t ed25519 -f ~/.ssh/koita_deploy -N ""
cat ~/.ssh/koita_deploy.pub
```
GitHub → VigyanShaala-Tech/koita-api → Settings → Deploy keys → Add deploy key → paste it.
Leave "Allow write access" **off**. Then:
```
printf "Host github.com\n  IdentityFile ~/.ssh/koita_deploy\n" >> ~/.ssh/config
sudo mkdir -p /opt/koita-api && sudo chown ubuntu /opt/koita-api
git clone git@github.com:VigyanShaala-Tech/koita-api.git /opt/koita-api
```
Answer `yes` if it asks whether to trust github.com.

### 4. Secrets file (`.env` is never in GitHub)
```
cd /opt/koita-api
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
cp .env.example .env
nano .env
```
Set:
- `METABASE_URL=https://dashboard.vigyanshaala.in`
- `METABASE_API_KEY=` the koita-api Metabase key (copy from your PC's `.env`)
- `METABASE_CARD_ID=625`
- `KOITA_API_USER=designscape`
- `KOITA_API_SECRET=` random value #1 (this is what DesignScape gets)
- `JWT_SECRET=` random value #2 (never leaves the server)
- `PORT=8000`
- `HOST=0.0.0.0` (so the load balancer can reach it; the security group in step 7 blocks everyone else)

Save with Ctrl+O, Enter, then Ctrl+X. Then lock the file:
```
sudo chown koita:koita .env && sudo chmod 600 .env
```

### 5. Start it and keep it running (systemd)
```
sudo cp deploy/koita-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now koita-api
sudo systemctl status koita-api --no-pager     # should say: active (running)
curl -s localhost:8000/health                  # should print {"ok": true}
```
If it fails: `sudo journalctl -u koita-api -n 30 --no-pager`. This shows errors, not secrets.

## Part B: AWS console

### 6. Target group
EC2 → Target Groups → **Create target group**:
- Target type **Instances**, name `koita-api-tg`, protocol **HTTP**, port **8000**, same VPC as the instance.
- Health check path **`/health`**.
- Next → tick this EC2 instance → **Include as pending below** → Create.

### 7. Security group: let only the ALB reach port 8000
The instance's security group → Edit inbound rules → **Add rule**:
Custom TCP, port **8000**, source **`sg-07822b6e0ca6344d0` (ALB-SG)**. This is the same pattern as the 3000/850x rules.
Do not use 0.0.0.0/0.

### 8. Certificate
EC2 → Load Balancers → the ALB → **Listeners** → HTTPS:443 → look at its certificate(s).
- If it is `*.vigyanshaala.in`, it already covers the new name. Go to step 9.
- If not: Certificate Manager (ACM, same region) → Request → `koita-api.vigyanshaala.in` → DNS validation →
  add the CNAME it shows at your DNS provider → wait for "Issued" → back on the listener: **Certificates → Add** it.

### 9. Listener rule
Same HTTPS:443 listener → **Manage rules → Add rule**:
- Condition: **Host header** = `koita-api.vigyanshaala.in`
- Action: **Forward to** `koita-api-tg`
- Any free priority number. Save.

Target Groups → `koita-api-tg` → Targets should turn **healthy** within about a minute.

### 10. DNS
At your DNS provider, find the existing record for `dashboard.vigyanshaala.in` and copy what it points to
(the ALB's DNS name, `xxxx.elb.amazonaws.com`). Add a new record:
- **CNAME** `koita-api` → that same ALB DNS name. On Route 53, use an **A record, Alias** → the ALB instead.
- On Cloudflare: **DNS only** (grey cloud).

## Part C: test from your PC (PowerShell)
```
curl.exe -s https://koita-api.vigyanshaala.in/health
```
Expect `{"ok": true}`. Then the real calls, exactly as DesignScape makes them:
```
curl.exe -s -X POST https://koita-api.vigyanshaala.in/getToken -H "Content-Type: application/json" -d '{\"user\":\"designscape\",\"secret\":\"PASTE_SECRET\"}'
curl.exe -s "https://koita-api.vigyanshaala.in/getdata?dt=01-Oct-2026" -H "Authorization: token PASTE_TOKEN"
```
Afterwards, clear the history: `Clear-History` and close the window.

## Hand over to DesignScape
- One message: the two URLs, the `dt` format (`01-Oct-2026`), "token valid 1 hour", and the 10 field names.
- `KOITA_API_SECRET` separately and securely (password-manager share or a one-time-secret link).
- Never share `JWT_SECRET` or the Metabase API key.

## Updating later
Push to GitHub from your PC, then on EC2:
```
cd /opt/koita-api && git pull && sudo systemctl restart koita-api
```
Changing the SQL only needs an edit to card 625 in Metabase. Keep the column names the same.
