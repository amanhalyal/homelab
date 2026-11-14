from http.server import HTTPServer, BaseHTTPRequestHandler
import json, os
import heimdall_actions as ha
from paths import CURRENT_PATH, DATA_DIR, ROOT_DIR

BASE = str(ROOT_DIR / 'web' / 'pages'); DATA = str(DATA_DIR); CURRENT = str(CURRENT_PATH)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split('?')[0]
        if path == '/current': return self.text(open(CURRENT).read().strip())
        if path == '/data/tasks.json': return self.json(ha.load_tasks())
        if path.startswith('/data/'):
            fn = path[len('/data/'):]
            try: return self.json(open(os.path.join(DATA, fn)).read())
            except: return self.json('{}')
        if path.startswith('/pages/'):
            fn = path[len('/pages/'):]
            try: return self.html(open(os.path.join(BASE, fn)).read())
            except: return self.html('<h1>PAGE NOT FOUND</h1>')
        return self.html(open(os.path.join(BASE, 'index.html')).read())

    def text(self, x):
        b = x.encode(); self.send_response(200); self.send_header('Content-Type', 'text/plain'); self.send_header('Content-Length', str(len(b))); self.end_headers(); self.wfile.write(b)

    def html(self, x):
        b = x.encode(); self.send_response(200); self.send_header('Content-Type', 'text/html; charset=utf-8'); self.send_header('Content-Length', str(len(b))); self.send_header('Cache-Control', 'no-store'); self.end_headers(); self.wfile.write(b)

    def json(self, x):
        if not isinstance(x, (str, bytes)): x = json.dumps(x)
        b = x.encode() if isinstance(x, str) else x; self.send_response(200); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(b))); self.end_headers(); self.wfile.write(b)

    def log_message(self, fmt, *args): print(fmt % args, flush=True)


HTTPServer(('0.0.0.0', 8080), Handler).serve_forever()
