#!/usr/bin/env python3
"""Tiny API server for orgchart data persistence."""
import json, os, shutil, subprocess
from datetime import datetime
from http.server import HTTPServer, SimpleHTTPRequestHandler

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data.json')
BACKUP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backups')
PORT = 8790

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=os.path.dirname(os.path.abspath(__file__)), **kwargs)

    def do_POST(self):
        if self.path == '/api/save':
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length)
            try:
                parsed = json.loads(body)
                # Validate structure
                if 'people' not in parsed:
                    raise ValueError('Missing people key')
                
                # Backup current data.json before overwriting
                if os.path.exists(DATA_FILE):
                    os.makedirs(BACKUP_DIR, exist_ok=True)
                    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
                    shutil.copy2(DATA_FILE, os.path.join(BACKUP_DIR, f'data_{ts}.json'))
                    # Keep last 50 backups
                    backups = sorted(os.listdir(BACKUP_DIR))
                    while len(backups) > 50:
                        os.remove(os.path.join(BACKUP_DIR, backups.pop(0)))
                
                # Save
                with open(DATA_FILE, 'w', encoding='utf-8') as f:
                    json.dump(parsed, f, ensure_ascii=False, indent=2)
                
                # Auto git commit (non-blocking)
                try:
                    repo_dir = os.path.dirname(os.path.abspath(__file__))
                    subprocess.Popen(
                        ['git', 'add', 'data.json', 'backups/', '-A'],
                        cwd=repo_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                    )
                    subprocess.Popen(
                        ['git', 'commit', '-m', f'auto-sync data {datetime.now().strftime("%Y-%m-%d %H:%M")}', '--no-verify'],
                        cwd=repo_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                    )
                except Exception:
                    pass

                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'ok': True}).encode())
            except Exception as e:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        super().end_headers()

if __name__ == '__main__':
    os.makedirs(BACKUP_DIR, exist_ok=True)
    print(f'🦁 Orgchart server running on http://localhost:{PORT}')
    print(f'📁 Data file: {DATA_FILE}')
    print(f'💾 Backups: {BACKUP_DIR}')
    HTTPServer(('0.0.0.0', PORT), Handler).serve_forever()
