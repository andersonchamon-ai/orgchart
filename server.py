#!/usr/bin/env python3
"""Orgchart API server with SQLite backend."""
import json, os, threading, time, secrets, hashlib, subprocess, smtplib
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from email.mime.text import MIMEText
from http import cookies
import db

PORT = 8790
STATIC_DIR = os.path.dirname(os.path.abspath(__file__))

# === AUTH CONFIG ===
ALLOWED_EMAIL = "andersonchamon@gmail.com"
AUTH_PASSWORD_HASH = hashlib.sha256("lionx2026!AC".encode()).hexdigest()  # password: lionx2026!AC
SESSION_EXPIRY = 86400 * 30  # 30 days
OTP_EXPIRY = 300  # 5 minutes

# In-memory stores (persist across requests, reset on server restart)
_sessions = {}   # token -> {"email": ..., "created": timestamp}
_otps = {}       # email -> {"code": "123456", "created": timestamp}

# Also persist sessions to disk so they survive restarts
_SESSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.sessions.json')
def _load_sessions():
    global _sessions
    try:
        if os.path.exists(_SESSION_FILE):
            with open(_SESSION_FILE) as f:
                _sessions = json.load(f)
            # Clean expired
            now = time.time()
            _sessions = {k:v for k,v in _sessions.items() if now - v['created'] < SESSION_EXPIRY}
    except: pass

def _save_sessions():
    try:
        with open(_SESSION_FILE, 'w') as f:
            json.dump(_sessions, f)
    except: pass

_load_sessions()

# Pages that don't require auth
PUBLIC_PATHS = {
    '/album-copa.html', '/album-copa-styles.html', '/album-copa-capivara.html', '/moodboard.html',
    '/api/auth/login', '/api/auth/request-otp', '/api/auth/verify-otp', '/api/auth/check',
    '/api/health', '/login.html', '/album-api/', '/album-copa/',
}
PUBLIC_PREFIXES = ('/capybara_', '/album-api/', '/album-copa/')  # capybara images + album copa proxy

def _is_public(path):
    if path in PUBLIC_PATHS:
        return True
    for pfx in PUBLIC_PREFIXES:
        if path.startswith(pfx):
            return True
    # Allow static assets needed by login page
    if path.endswith(('.ico', '.png', '.webmanifest', '.json')) and '/api/' not in path:
        return True
    return False

def _get_session_token(handler):
    cookie_header = handler.headers.get('Cookie', '')
    c = cookies.SimpleCookie()
    try:
        c.load(cookie_header)
    except Exception:
        return None
    if 'session' in c:
        return c['session'].value
    # Also check Authorization header
    auth = handler.headers.get('Authorization', '')
    if auth.startswith('Bearer '):
        return auth[7:]
    return None

def _is_authenticated(handler):
    token = _get_session_token(handler)
    if not token:
        return False
    sess = _sessions.get(token)
    if not sess:
        return False
    if time.time() - sess['created'] > SESSION_EXPIRY:
        del _sessions[token]
        return False
    return True

def _send_otp(email, code):
    """Send OTP via Telegram using openclaw CLI."""
    try:
        msg = f"🔐 Código de acesso LionX: {code}\n\nVálido por 5 minutos."
        result = subprocess.run(
            ['openclaw', 'send', '--channel', 'telegram', '--to', '951774483', '--message', msg],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            # Fallback: write to file for Bob to pick up during heartbeat
            otp_file = os.path.join(STATIC_DIR, '.pending_otp')
            with open(otp_file, 'w') as f:
                json.dump({'email': email, 'code': code, 'ts': time.time()}, f)
        return True
    except Exception as e:
        print(f"OTP send error: {e}")
        # Fallback to file
        try:
            otp_file = os.path.join(STATIC_DIR, '.pending_otp')
            with open(otp_file, 'w') as f:
                json.dump({'email': email, 'code': code, 'ts': time.time()}, f)
            return True
        except:
            return False

# Lock to serialize all DB writes
DB_LOCK = threading.Lock()

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def read_body(self):
        length = int(self.headers.get('Content-Length', 0))
        return json.loads(self.rfile.read(length)) if length > 0 else {}

    def do_OPTIONS(self):
        path = urlparse(self.path).path
        if path.startswith('/album-api/') or path.startswith('/album-copa/'):
            return self._proxy_album('OPTIONS')
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS, PATCH')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
        self.end_headers()

    def _check_auth(self):
        """Returns True if request is allowed, False if blocked (already sent 401)."""
        path = urlparse(self.path).path
        if _is_public(path):
            return True
        if _is_authenticated(self):
            return True
        # Redirect HTML requests to login, return 401 for API
        if path.startswith('/api/'):
            self.send_json({'error': 'Unauthorized', 'login_required': True}, 401)
        else:
            self.send_response(302)
            self.send_header('Location', '/login.html')
            self.end_headers()
        return False

    def _set_session_cookie(self, token):
        self.send_header('Set-Cookie', f'session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_EXPIRY}')

    def _proxy_album(self, method='GET'):
        """Proxy /album-copa/* and /album-api/* requests to Album da Copa backend on port 8891"""
        import urllib.request as ur, urllib.error
        path = urlparse(self.path).path
        if path.startswith('/album-copa/'):
            subpath = path[len('/album-copa/'):]  # strip prefix, keep /api/...
        else:
            subpath = 'api/' + path[len('/album-api/'):]  # legacy: /album-api/X -> /api/X
        qs = urlparse(self.path).query
        target = f'http://127.0.0.1:8891/{subpath}'
        if qs:
            target += f'?{qs}'
        try:
            body = None
            if method in ('POST', 'PUT'):
                length = int(self.headers.get('Content-Length', 0))
                body = self.rfile.read(length) if length > 0 else None
            headers = {}
            for key in ('Content-Type', 'Authorization'):
                if self.headers.get(key):
                    headers[key] = self.headers[key]
            req = ur.Request(target, data=body, headers=headers, method=method)
            resp = ur.urlopen(req, timeout=30)
            data = resp.read()
            self.send_response(resp.status)
            self.send_header('Content-Type', resp.headers.get('Content-Type', 'application/json'))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
            self.end_headers()
            self.wfile.write(data)
        except urllib.error.HTTPError as e:
            data = e.read()
            self.send_response(e.code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            self.send_response(502)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())

    def do_GET(self):
        path = urlparse(self.path).path

        # Album API proxy (public)
        if path.startswith('/album-api/') or path.startswith('/album-copa/'):
            return self._proxy_album('GET')

        # Auth endpoints (public)
        if path == '/api/auth/check':
            self.send_json({'authenticated': _is_authenticated(self)})
            return

        # Block unauthorized access
        if not self._check_auth():
            return

        if path == '/api/data':
            self.send_json(db.export_all())
        elif path == '/api/todos':
            self.send_json(db.get_todos())
        elif path == '/api/settings/todo-cat-order':
            val = db.get_setting('todo-cat-order')
            self.send_json(json.loads(val) if val else [])
        elif path == '/api/snapshots':
            self.send_json(db.list_snapshots())
        elif path == '/api/health':
            conn = db.get_db()
            people_count = conn.execute("SELECT COUNT(*) FROM people").fetchone()[0]
            snapshot_count = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
            last_audit = conn.execute("SELECT timestamp FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
            conn.close()
            self.send_json({
                'status': 'ok',
                'db': db.DB_PATH,
                'people': people_count,
                'snapshots': snapshot_count,
                'last_change': last_audit['timestamp'] if last_audit else None
            })
        elif path == '/api/audit':
            conn = db.get_db()
            rows = conn.execute("SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT 100").fetchall()
            conn.close()
            self.send_json([dict(r) for r in rows])
        elif path == '/api/threads':
            self.send_json(db.get_threads())
        elif path.startswith('/api/threads/') and path.count('/') == 3:
            try:
                tid = int(path.split('/')[-1])
                t = db.get_thread(tid)
                if t:
                    self.send_json(t)
                else:
                    self.send_json({'error': 'Not found'}, 404)
            except ValueError:
                self.send_json({'error': 'Invalid id'}, 400)
        else:
            super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            # Album API proxy (public)
            if path.startswith('/album-api/') or path.startswith('/album-copa/'):
                return self._proxy_album('POST')

            # === AUTH ENDPOINTS (public) ===
            if path == '/api/auth/login':
                body = self.read_body()
                email = body.get('email', '').strip().lower()
                password = body.get('password', '')
                pw_hash = hashlib.sha256(password.encode()).hexdigest()
                if email != ALLOWED_EMAIL:
                    self.send_json({'error': 'Email não autorizado'}, 403)
                    return
                if pw_hash != AUTH_PASSWORD_HASH:
                    self.send_json({'error': 'Senha incorreta'}, 401)
                    return
                # Valid! Create session
                token = secrets.token_urlsafe(32)
                _sessions[token] = {'email': email, 'created': time.time()}
                _save_sessions()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Access-Control-Allow-Origin', '*')
                self._set_session_cookie(token)
                self.end_headers()
                self.wfile.write(json.dumps({'ok': True, 'token': token}).encode())
                return

            elif path == '/api/auth/request-otp':
                body = self.read_body()
                email = body.get('email', '').strip().lower()
                if email != ALLOWED_EMAIL:
                    self.send_json({'error': 'Email não autorizado'}, 403)
                    return
                code = f"{secrets.randbelow(1000000):06d}"
                _otps[email] = {'code': code, 'created': time.time()}
                sent = _send_otp(email, code)
                if sent:
                    self.send_json({'ok': True, 'message': 'Código enviado'})
                else:
                    self.send_json({'error': 'Falha ao enviar código'}, 500)
                return

            elif path == '/api/auth/verify-otp':
                body = self.read_body()
                email = body.get('email', '').strip().lower()
                code = body.get('code', '').strip()
                otp_data = _otps.get(email)
                if not otp_data:
                    self.send_json({'error': 'Nenhum código solicitado'}, 400)
                    return
                if time.time() - otp_data['created'] > OTP_EXPIRY:
                    del _otps[email]
                    self.send_json({'error': 'Código expirado'}, 400)
                    return
                if otp_data['code'] != code:
                    self.send_json({'error': 'Código inválido'}, 400)
                    return
                del _otps[email]
                token = secrets.token_urlsafe(32)
                _sessions[token] = {'email': email, 'created': time.time()}
                _save_sessions()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Access-Control-Allow-Origin', '*')
                self._set_session_cookie(token)
                self.end_headers()
                self.wfile.write(json.dumps({'ok': True, 'token': token}).encode())
                return

            # === AUTH CHECK for all other POST routes ===
            if not self._check_auth():
                return

            if path == '/api/save':
                body = self.read_body()
                with DB_LOCK:
                    db.import_all(body)
                self.send_json({'ok': True})

            elif path == '/api/person':
                body = self.read_body()
                with DB_LOCK:
                    pid = db.upsert_person(body)
                self.send_json({'ok': True, 'id': pid})

            elif path == '/api/snapshot':
                body = self.read_body()
                reason = body.get('reason', 'manual')
                with DB_LOCK:
                    db.take_snapshot(reason)
                self.send_json({'ok': True})

            elif path == '/api/restore':
                body = self.read_body()
                snapshot_id = body.get('snapshot_id')
                if not snapshot_id:
                    self.send_json({'error': 'snapshot_id required'}, 400)
                    return
                with DB_LOCK:
                    db.take_snapshot(f'pre-restore-{snapshot_id}')
                    data = db.restore_snapshot(snapshot_id)
                self.send_json({'ok': True, 'data': data})

            elif path == '/api/todos':
                body = self.read_body()
                text = body.get('text','').strip()
                if not text:
                    self.send_json({'error': 'text required'}, 400)
                    return
                tid = db.create_todo(text, body.get('category',''))
                self.send_json({'ok': True, 'id': tid})

            elif path == '/api/todos/clear-done':
                db.clear_done_todos()
                self.send_json({'ok': True})

            elif path == '/api/backup':
                dest = db.backup_db()
                self.send_json({'ok': True, 'path': dest})
            elif path == '/api/threads':
                body = self.read_body()
                title = body.get('title','').strip()
                if not title:
                    self.send_json({'error': 'title required'}, 400)
                    return
                with DB_LOCK:
                    tid = db.create_thread(title, body.get('summary',''))
                self.send_json({'ok': True, 'id': tid})
            elif path.endswith('/attachments') and '/api/threads/' in path:
                parts = path.split('/')
                tid = int(parts[3])
                body = self.read_body()
                with DB_LOCK:
                    aid = db.add_thread_attachment(tid, body.get('label',''), body.get('url',''), body.get('type','link'))
                self.send_json({'ok': True, 'id': aid})
            elif path.endswith('/notes') and '/api/threads/' in path:
                parts = path.split('/')
                tid = int(parts[3])
                body = self.read_body()
                with DB_LOCK:
                    nid = db.add_thread_note(tid, body.get('content',''))
                self.send_json({'ok': True, 'id': nid})
            elif path.endswith('/todos') and '/api/threads/' in path:
                parts = path.split('/')
                tid = int(parts[3])
                body = self.read_body()
                text = body.get('text','').strip()
                if not text:
                    self.send_json({'error': 'text required'}, 400)
                    return
                with DB_LOCK:
                    todo_id = db.add_thread_todo(tid, text)
                self.send_json({'ok': True, 'id': todo_id})
            else:
                self.send_json({'error': 'Not found'}, 404)
        except Exception as e:
            self.send_json({'error': str(e)}, 500)

    def do_PUT(self):
        path = urlparse(self.path).path
        if path.startswith('/album-api/') or path.startswith('/album-copa/'):
            return self._proxy_album('PUT')
        if not self._check_auth():
            return
        try:
            if path == '/api/settings/todo-cat-order':
                body = self.read_body()
                with DB_LOCK:
                    db.set_setting('todo-cat-order', json.dumps(body))
                self.send_json({'ok': True})
            elif path.startswith('/api/todos/'):
                tid = int(path.split('/')[-1])
                body = self.read_body()
                with DB_LOCK:
                    db.update_todo(tid, body)
                self.send_json({'ok': True})
            elif path.startswith('/api/threads/notes/'):
                nid = int(path.split('/')[-1])
                body = self.read_body()
                with DB_LOCK:
                    db.update_thread_note(nid, body.get('content',''))
                self.send_json({'ok': True})
            elif path.startswith('/api/threads/todos/'):
                todo_id = int(path.split('/')[-1])
                body = self.read_body()
                with DB_LOCK:
                    db.update_thread_todo(todo_id, body)
                self.send_json({'ok': True})
            elif path.startswith('/api/threads/') and path.count('/') == 3:
                tid = int(path.split('/')[-1])
                body = self.read_body()
                with DB_LOCK:
                    db.update_thread(tid, body)
                self.send_json({'ok': True})
            else:
                self.send_json({'error': 'Not found'}, 404)
        except Exception as e:
            self.send_json({'error': str(e)}, 500)

    def do_DELETE(self):
        path = urlparse(self.path).path
        if path.startswith('/album-api/') or path.startswith('/album-copa/'):
            return self._proxy_album('DELETE')
        if not self._check_auth():
            return
        try:
            if path.startswith('/api/person/'):
                pid = int(path.split('/')[-1])
                with DB_LOCK:
                    db.delete_person(pid)
                self.send_json({'ok': True})
            elif path.startswith('/api/todos/'):
                tid = int(path.split('/')[-1])
                with DB_LOCK:
                    db.delete_todo(tid)
                self.send_json({'ok': True})
            elif path.startswith('/api/threads/attachments/'):
                aid = int(path.split('/')[-1])
                with DB_LOCK:
                    db.delete_thread_attachment(aid)
                self.send_json({'ok': True})
            elif path.startswith('/api/threads/notes/'):
                nid = int(path.split('/')[-1])
                with DB_LOCK:
                    db.delete_thread_note(nid)
                self.send_json({'ok': True})
            elif path.startswith('/api/threads/todos/'):
                todo_id = int(path.split('/')[-1])
                with DB_LOCK:
                    db.delete_thread_todo(todo_id)
                self.send_json({'ok': True})
            elif path.startswith('/api/threads/') and path.count('/') == 3:
                tid = int(path.split('/')[-1])
                with DB_LOCK:
                    db.delete_thread(tid)
                self.send_json({'ok': True})
            else:
                self.send_json({'error': 'Not found'}, 404)
        except Exception as e:
            self.send_json({'error': str(e)}, 500)

    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        super().end_headers()

    def log_message(self, format, *args):
        try:
            if args and isinstance(args[0], str) and '/api/' in args[0]:
                super().log_message(format, *args)
        except Exception:
            super().log_message(format, *args)

# Background: snapshot every 2 hours, backup every 12 hours
def background_tasks():
    last_snapshot = time.time()
    last_backup = time.time()
    while True:
        time.sleep(60)
        now = time.time()
        if now - last_snapshot > 7200:  # 2 hours
            try:
                with DB_LOCK:
                    db.take_snapshot('auto-2h')
                last_snapshot = now
            except Exception as e:
                print(f'Snapshot error: {e}')
        if now - last_backup > 43200:  # 12 hours
            try:
                db.backup_db()
                last_backup = now
            except Exception as e:
                print(f'Backup error: {e}')

if __name__ == '__main__':
    # Migrate from data.json if DB is empty
    conn = db.get_db()
    count = conn.execute("SELECT COUNT(*) FROM people").fetchone()[0]
    conn.close()
    if count == 0:
        json_path = os.path.join(STATIC_DIR, 'data.json')
        if os.path.exists(json_path):
            with open(json_path, 'r') as f:
                legacy = json.load(f)
            db.import_all(legacy, reason='migration from data.json')
            print(f'✅ Migrated {len(legacy.get("people",[]))} people from data.json')

    # Start background thread
    threading.Thread(target=background_tasks, daemon=True).start()

    print(f'🦁 Orgchart server on http://localhost:{PORT}')
    print(f'📀 Database: {db.DB_PATH}')
    HTTPServer(('0.0.0.0', PORT), Handler).serve_forever()

# === Album Copa Backend Proxy ===
import urllib.request as urllib_req
import urllib.error

@app.route('/album-api/<path:subpath>', methods=['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'])
def album_proxy(subpath):
    """Proxy requests to the Album da Copa backend on port 8891"""
    from flask import request as flask_request, Response
    target_url = f'http://127.0.0.1:8891/api/{subpath}'
    
    try:
        req_data = flask_request.get_data() if flask_request.method in ('POST', 'PUT') else None
        headers = {}
        for key in ('Content-Type', 'Authorization'):
            if key in flask_request.headers:
                headers[key] = flask_request.headers[key]
        
        req = urllib_req.Request(target_url, data=req_data, headers=headers, method=flask_request.method)
        resp = urllib_req.urlopen(req, timeout=30)
        
        response = Response(resp.read(), status=resp.status, content_type=resp.headers.get('Content-Type', 'application/json'))
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        return response
    except urllib.error.HTTPError as e:
        response = Response(e.read(), status=e.code, content_type='application/json')
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response
    except Exception as e:
        response = Response(f'{{"error": "{str(e)}"}}', status=502, content_type='application/json')
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response
