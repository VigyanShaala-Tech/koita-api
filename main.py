"""Koita / DesignScape monthly KPI API. Stdlib only: python main.py

POST /getToken            {"user": "...", "secret": "..."}  -> {"token": "...", "expires_in": 3600}
GET  /getdata?dt=01-Nov-2026   Authorization: token <x>      -> {"kpi_name": number, ...}
"""
import base64, hashlib, hmac, json, os, time, urllib.request
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs


def load_env(path=".env"):
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


load_env()
MB_URL = os.environ["METABASE_URL"].rstrip("/")
MB_KEY = os.environ["METABASE_API_KEY"]
MB_CARD = os.environ["METABASE_CARD_ID"]
API_USER = os.environ["KOITA_API_USER"]
API_SECRET = os.environ["KOITA_API_SECRET"]
SIGN_KEY = os.environ["JWT_SECRET"].encode()
TTL = int(os.environ.get("TOKEN_TTL_SECONDS", "3600"))
PORT = int(os.environ.get("PORT", "8000"))
HOST = os.environ.get("HOST", "127.0.0.1")  # 0.0.0.0 behind the AWS ALB; security group limits who connects
for _name, _val in (("KOITA_API_SECRET", API_SECRET), ("JWT_SECRET", os.environ["JWT_SECRET"])):
    if len(_val) < 16 or _val.startswith("change-me"):
        raise SystemExit(f"{_name} in .env must be a long random value")


def same(a, b):
    return hmac.compare_digest(a.encode(), b.encode())  # bytes: str compare crashes on non-ASCII


def make_token():
    exp = str(int(time.time()) + TTL)
    sig = hmac.new(SIGN_KEY, exp.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{exp}.{sig}".encode()).decode()


def token_ok(token):
    try:
        exp, sig = base64.urlsafe_b64decode(token.encode()).decode().split(".")
        good = hmac.new(SIGN_KEY, exp.encode(), hashlib.sha256).hexdigest()
        return same(sig, good) and int(exp) > time.time()
    except Exception:
        return False


def month_range(dt, today=None):
    """'01-Nov-2026' -> ('2026-11-01', '2026-12-01'); end is exclusive.
    Raises ValueError for a month that hasn't finished: its figures would change later."""
    d = datetime.strptime(dt, "%d-%b-%Y").date().replace(day=1)
    nxt = d.replace(year=d.year + 1, month=1) if d.month == 12 else d.replace(month=d.month + 1)
    if nxt > (today or date.today()):
        raise ValueError("month not complete")
    return d.isoformat(), nxt.isoformat()


def fetch_kpis(start, end):
    def p(name, value):
        return {"type": "date/single", "value": value,
                "target": ["variable", ["template-tag", name]]}
    body = json.dumps({"parameters": [p("start_date", start), p("end_date", end)]}).encode()
    req = urllib.request.Request(f"{MB_URL}/api/card/{MB_CARD}/query", data=body, method="POST",
                                 headers={"x-api-key": MB_KEY, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        res = json.load(r)
    if res.get("error"):
        raise RuntimeError(res["error"])
    cols = [c["name"] for c in res["data"]["cols"]]
    rows = res["data"]["rows"]
    if len(rows) != 1:
        raise RuntimeError(f"Metabase question must return 1 row, got {len(rows)}")
    return {c: (0 if v is None else v) for c, v in zip(cols, rows[0])}


class Handler(BaseHTTPRequestHandler):
    def send(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_error(self, code, message=None, explain=None):
        # stdlib errors (bad request line, unsupported method) are HTML by default; spec wants JSON only
        self.close_connection = True
        self.send(code, {"error": message or "request error"})

    def do_POST(self):
        if urlparse(self.path).path != "/getToken":
            return self.send(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if not 0 < n <= 10_000:  # negative length would block the thread forever
                raise ValueError
            body = json.loads(self.rfile.read(n))
            user, secret = str(body["user"]), str(body["secret"])
        except Exception:
            self.close_connection = True
            return self.send(400, {"error": "body must be JSON with user and secret"})
        if not (same(user, API_USER) and same(secret, API_SECRET)):
            return self.send(401, {"error": "invalid credentials"})
        self.send(200, {"token": make_token(), "expires_in": TTL})

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/health":  # load balancer health check
            return self.send(200, {"ok": True})
        if url.path != "/getdata":
            return self.send(404, {"error": "not found"})
        auth = self.headers.get("Authorization", "")
        if not (auth.startswith("token ") and token_ok(auth[6:].strip())):
            return self.send(401, {"error": "missing, invalid or expired token"})
        try:
            start, end = month_range(parse_qs(url.query)["dt"][0])
        except Exception:
            return self.send(400, {"error": "dt must be a completed month like 01-Oct-2026"})
        try:
            self.send(200, fetch_kpis(start, end))
        except Exception as e:
            self.log_error("getdata %s failed: %s", start, e)
            self.send(500, {"error": "internal error"})


if __name__ == "__main__":
    print(f"Listening on http://{HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
