"""Self-check against a fake Metabase. Run: python test_main.py"""
import json, os, threading, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

seen = {}

class FakeMB(BaseHTTPRequestHandler):
    def do_POST(self):
        seen["key"] = self.headers["x-api-key"]
        seen["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        out = json.dumps({"data": {"cols": [{"name": "a"}, {"name": "b"}], "rows": [[5, None]]}}).encode()
        self.send_response(202); self.send_header("Content-Length", str(len(out))); self.end_headers()
        self.wfile.write(out)
    def log_message(self, *a): pass

mb = ThreadingHTTPServer(("127.0.0.1", 0), FakeMB)
threading.Thread(target=mb.serve_forever, daemon=True).start()
os.environ.update(METABASE_URL=f"http://127.0.0.1:{mb.server_port}", METABASE_API_KEY="k",
                  METABASE_CARD_ID="1", KOITA_API_USER="u", KOITA_API_SECRET="s" * 20, JWT_SECRET="j" * 20)
os.environ.pop("PORT", None)
import main
main.load_env = lambda *a: None
api = ThreadingHTTPServer(("127.0.0.1", 0), main.Handler)
threading.Thread(target=api.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{api.server_port}"

def call(path, body=None, token=None):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None)
    if token: req.add_header("Authorization", f"token {token}")
    try:
        with urllib.request.urlopen(req) as r: return r.status, json.load(r)
    except urllib.error.HTTPError as e: return e.code, json.load(e)

import socket
from datetime import date

def raw(data):
    s = socket.create_connection(("127.0.0.1", api.server_port), timeout=3)
    s.sendall(data); out = s.recv(4096).decode(); s.close(); return out

assert call("/getToken", {"user": "u", "secret": "bad"})[0] == 401
assert call("/getToken", {"user": "u", "secret": "pässwörd"})[0] == 401          # crashed before
assert call("/getToken", {"user": "u"})[0] == 400
code, tok = call("/getToken", {"user": "u", "secret": "s" * 20}); assert code == 200
t = tok["token"]
assert call("/getdata?dt=01-Oct-2026")[0] == 401
assert call("/getdata?dt=01-Oct-2026", token=t + "x")[0] == 401
assert call("/getdata?dt=01-Oct-2026", token="w6TDpMOk")[0] == 401
assert call("/getdata?dt=2026-10-01", token=t)[0] == 400
assert call("/getdata?dt=01-Dec-2030", token=t)[0] == 400                          # future month: 200 before
assert call("/getdata?dt=01-Feb-2026", token=t) == (200, {"a": 5, "b": 0})
assert [p["value"] for p in seen["body"]["parameters"]] == ["2026-02-01", "2026-03-01"]
assert seen["key"] == "k"
assert main.month_range("15-Dec-2026", today=date(2027, 1, 2)) == ("2026-12-01", "2027-01-01")
assert main.month_range("01-Oct-2026", today=date(2026, 11, 1)) == ("2026-10-01", "2026-11-01")
assert '{"error"' in raw(b"PUT /getdata HTTP/1.1\r\nHost: x\r\n\r\n")             # was HTML
assert " 400 " in raw(b"POST /getToken HTTP/1.1\r\nHost: x\r\nContent-Length: -1\r\n\r\n{}")  # hung before
print("all checks passed")
