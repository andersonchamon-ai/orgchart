#!/usr/bin/env python3
"""Orgchart API server with SQLite backend."""
import json, os, threading, time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse
import db

PORT = 8790
STATIC_DIR = os.path.dirname(os.path.abspath(__file__))

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
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS, PATCH')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
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
