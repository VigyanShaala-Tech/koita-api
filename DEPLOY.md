# Deploying the Koita KPI API

Target: an Ubuntu/Debian Linux server you can SSH into with sudo, for example the one that runs
dashboard.vigyanshaala.in. Result: `https://api.vigyanshaala.in/getToken` and `/getdata`, always on.

```
DesignScape --HTTPS--> nginx :443 --> main.py on 127.0.0.1:8000 --> Metabase
                       (certbot cert)   (systemd keeps it running)
```

## 1. DNS (whoever manages the vigyanshaala.in domain)
Add an **A record**: `api` → the server's public IP. Check it from your PC: `nslookup api.vigyanshaala.in`.

On **AWS EC2**: in the instance's Security Group allow inbound **80 and 443** from anywhere, and
use an **Elastic IP** so the address doesn't change when the instance restarts. Don't open 8000.

## 2. Get the code from GitHub (on EC2)
The repo is private, so give EC2 a **read-only deploy key** (it can pull this one repo, nothing else):
```
ssh -i your-key.pem ubuntu@EC2_IP
ssh-keygen -t ed25519 -f ~/.ssh/koita_deploy -N ""
cat ~/.ssh/koita_deploy.pub
```
GitHub → repo → Settings → Deploy keys → Add key → paste it, leave "Allow write access" **off**. Then:
```
printf "Host github.com\n  IdentityFile ~/.ssh/koita_deploy\n" >> ~/.ssh/config
sudo mkdir -p /opt/koita-api && sudo chown ubuntu /opt/koita-api
git clone git@github.com:OWNER/REPO.git /opt/koita-api
```
`.env` is not in GitHub (it's in `.gitignore`): production gets fresh secrets in step 3.

## 3. Install on the server
```
sudo apt update && sudo apt install -y python3 nginx certbot python3-certbot-nginx
sudo useradd --system --no-create-home koita
cd /opt/koita-api
sudo cp .env.example .env
python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # run twice: one for KOITA_API_SECRET, one for JWT_SECRET
sudo nano /opt/koita-api/.env
```
In `.env` set:
- `METABASE_URL=https://dashboard.vigyanshaala.in`
- `METABASE_API_KEY=` the koita-api key (same one as on your PC, or a fresh one from Admin → API keys in the same group)
- `METABASE_CARD_ID=625`
- `KOITA_API_USER=designscape`
- `KOITA_API_SECRET=` new random value #1 (this is what DesignScape gets)
- `JWT_SECRET=` new random value #2 (never leaves the server)
- leave `TOKEN_TTL_SECONDS=3600` and `PORT=8000`

Lock it down: only the service can read the secrets (the code stays owned by ubuntu so `git pull` works).
```
sudo chown koita:koita /opt/koita-api/.env
sudo chmod 600 /opt/koita-api/.env
```

## 4. Keep it running (systemd)
```
sudo cp deploy/koita-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now koita-api
sudo systemctl status koita-api          # should say "active (running)"
curl -s localhost:8000/getdata           # should print {"error": "missing, invalid or expired token"}
```
If it fails to start: `sudo journalctl -u koita-api -n 30`.

## 5. HTTPS (nginx + free Let's Encrypt certificate)
```
sudo cp deploy/nginx-koita-api.conf /etc/nginx/sites-available/koita-api
sudo ln -s /etc/nginx/sites-available/koita-api /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d api.vigyanshaala.in --redirect
```
certbot renews the certificate automatically. Firewall: ports 80 and 443 open; port 8000 stays closed
(main.py only listens on 127.0.0.1 anyway).

## 6. Test from your PC, exactly as DesignScape will
```
curl -s -X POST https://api.vigyanshaala.in/getToken -H "Content-Type: application/json" -d "{\"user\":\"designscape\",\"secret\":\"PASTE_SECRET\"}"
curl -s "https://api.vigyanshaala.in/getdata?dt=01-Oct-2026" -H "Authorization: token PASTE_TOKEN"
```
Expect the token, then the 10 KPIs. Clear your terminal afterwards: the secret is in its history.

## 7. Hand over to DesignScape
- Send them: the two URLs, the `dt` format (`01-Oct-2026`), "token valid 1 hour", and the 10 field names.
- Send `KOITA_API_SECRET` separately and securely (password-manager share or a one-time-secret link),
  never in the same email as the URL.
- Never share `JWT_SECRET` or the Metabase API key.

## Updating later
Push to GitHub from your PC, then on EC2:
```
cd /opt/koita-api && git pull && sudo systemctl restart koita-api
```
Changing the SQL only needs an edit to card 625 in Metabase. Keep the column names the same.
