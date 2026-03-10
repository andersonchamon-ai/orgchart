#!/usr/bin/env python3
"""SQLite database layer for orgchart."""
import sqlite3, json, os, shutil
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'orgchart.db')
BACKUP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backups')

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn

def init_db():
    """Create tables if they don't exist."""
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS companies (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            tag TEXT NOT NULL,
            color TEXT NOT NULL,
            sort_order INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS people (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT DEFAULT '',
            role TEXT NOT NULL,
            company_id TEXT NOT NULL REFERENCES companies(id),
            level INTEGER NOT NULL DEFAULT 2,
            reports_to INTEGER REFERENCES people(id) ON DELETE SET NULL,
            hc_filled INTEGER NOT NULL DEFAULT 0,
            hc_open INTEGER NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS responsibilities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
            description TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS unassigned_responsibilities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id TEXT NOT NULL REFERENCES companies(id),
            description TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id TEXT,
            old_data TEXT,
            new_data TEXT,
            timestamp TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data TEXT NOT NULL,
            reason TEXT DEFAULT 'auto',
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.commit()
    conn.close()

def seed_companies():
    """Insert default companies if empty."""
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
    if count == 0:
        conn.executemany("INSERT INTO companies (id, name, tag, color, sort_order) VALUES (?,?,?,?,?)", [
            ('lionx', 'LionX (Holding)', 'tag-holding', '#3b82f6', 0),
            ('pws', 'PWS Cloud', 'tag-pws', '#22c55e', 1),
            ('phiz', 'PhizChat', 'tag-phiz', '#818cf8', 2),
            ('fabrica', 'Fábrica de Software', 'tag-fab', '#f59e0b', 3),
        ])
        conn.commit()
    conn.close()

def log_audit(conn, action, entity_type, entity_id, old_data=None, new_data=None):
    conn.execute(
        "INSERT INTO audit_log (action, entity_type, entity_id, old_data, new_data) VALUES (?,?,?,?,?)",
        (action, entity_type, str(entity_id), json.dumps(old_data, ensure_ascii=False) if old_data else None,
         json.dumps(new_data, ensure_ascii=False) if new_data else None)
    )

def take_snapshot(reason='auto'):
    """Save a full snapshot of all data."""
    conn = get_db()
    data = export_all(conn)
    conn.execute("INSERT INTO snapshots (data, reason) VALUES (?,?)",
                 (json.dumps(data, ensure_ascii=False), reason))
    # Keep last 100 snapshots
    conn.execute("""
        DELETE FROM snapshots WHERE id NOT IN (
            SELECT id FROM snapshots ORDER BY created_at DESC LIMIT 100
        )
    """)
    conn.commit()
    conn.close()
    return data

def restore_snapshot(snapshot_id):
    """Restore from a snapshot."""
    conn = get_db()
    row = conn.execute("SELECT data FROM snapshots WHERE id=?", (snapshot_id,)).fetchone()
    if not row:
        conn.close()
        raise ValueError(f"Snapshot {snapshot_id} not found")
    data = json.loads(row['data'])
    import_all(data, conn, reason=f'restore from snapshot {snapshot_id}')
    conn.close()
    return data

def list_snapshots():
    conn = get_db()
    rows = conn.execute(
        "SELECT id, reason, created_at, LENGTH(data) as size FROM snapshots ORDER BY created_at DESC LIMIT 50"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def export_all(conn=None):
    """Export full database as dict."""
    close = False
    if conn is None:
        conn = get_db()
        close = True
    
    people = []
    for row in conn.execute("SELECT * FROM people ORDER BY level, name"):
        resps = [r['description'] for r in conn.execute(
            "SELECT description FROM responsibilities WHERE person_id=? ORDER BY id", (row['id'],)
        )]
        people.append({
            'id': row['id'], 'name': row['name'], 'role': row['role'],
            'company': row['company_id'], 'level': row['level'],
            'reportsTo': row['reports_to'], 'responsibilities': resps,
            'hcFilled': row['hc_filled'] if 'hc_filled' in row.keys() else 0,
            'hcOpen': row['hc_open'] if 'hc_open' in row.keys() else 0,
            'sortOrder': row['sort_order'] if 'sort_order' in row.keys() else 0,
            'region': row['region'] if 'region' in row.keys() else '',
        })
    
    unassigned = {}
    for comp in conn.execute("SELECT id FROM companies"):
        cid = comp['id']
        items = [r['description'] for r in conn.execute(
            "SELECT description FROM unassigned_responsibilities WHERE company_id=? ORDER BY id", (cid,)
        )]
        unassigned[cid] = items

    if close:
        conn.close()
    return {'people': people, 'unassigned': unassigned}

def import_all(data, conn=None, reason='import'):
    """Import full dataset, replacing everything."""
    close = False
    if conn is None:
        conn = get_db()
        close = True

    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("DELETE FROM responsibilities")
    conn.execute("DELETE FROM unassigned_responsibilities")
    conn.execute("DELETE FROM people")

    for p in data.get('people', []):
        conn.execute(
            "INSERT INTO people (id, name, role, company_id, level, reports_to, hc_filled, hc_open, sort_order) VALUES (?,?,?,?,?,?,?,?,?)",
            (p['id'], p.get('name',''), p.get('role',''), p['company'], p['level'], p.get('reportsTo'),
             p.get('hcFilled', 0), p.get('hcOpen', 0), p.get('sortOrder', 0))
        )
        for r in p.get('responsibilities', []):
            conn.execute("INSERT INTO responsibilities (person_id, description) VALUES (?,?)", (p['id'], r))

    for company_id, items in data.get('unassigned', {}).items():
        for desc in items:
            conn.execute("INSERT INTO unassigned_responsibilities (company_id, description) VALUES (?,?)",
                        (company_id, desc))

    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()
    if close:
        conn.close()

# --- CRUD operations ---

def get_person(person_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM people WHERE id=?", (person_id,)).fetchone()
    if not row:
        conn.close()
        return None
    resps = [r['description'] for r in conn.execute(
        "SELECT description FROM responsibilities WHERE person_id=? ORDER BY id", (row['id'],)
    )]
    conn.close()
    return {**dict(row), 'responsibilities': resps}

def upsert_person(person_data):
    conn = get_db()
    pid = person_data.get('id')
    
    if pid:
        old = conn.execute("SELECT * FROM people WHERE id=?", (pid,)).fetchone()
        if old:
            log_audit(conn, 'update', 'person', pid, dict(old), person_data)
            conn.execute("""
                UPDATE people SET name=?, role=?, company_id=?, level=?, reports_to=?, hc_filled=?, hc_open=?, region=?, updated_at=datetime('now')
                WHERE id=?
            """, (person_data.get('name',''), person_data['role'], person_data['company'],
                  person_data['level'], person_data.get('reportsTo'),
                  person_data.get('hcFilled', 0), person_data.get('hcOpen', 0), person_data.get('region', ''), pid))
            # Replace responsibilities
            conn.execute("DELETE FROM responsibilities WHERE person_id=?", (pid,))
            for r in person_data.get('responsibilities', []):
                conn.execute("INSERT INTO responsibilities (person_id, description) VALUES (?,?)", (pid, r))
            conn.commit()
            conn.close()
            return pid
    
    # Insert new
    log_audit(conn, 'create', 'person', None, None, person_data)
    cursor = conn.execute(
        "INSERT INTO people (name, role, company_id, level, reports_to, hc_filled, hc_open, region) VALUES (?,?,?,?,?,?,?,?)",
        (person_data.get('name',''), person_data['role'], person_data['company'],
         person_data['level'], person_data.get('reportsTo'),
         person_data.get('hcFilled', 0), person_data.get('hcOpen', 0), person_data.get('region', ''))
    )
    pid = cursor.lastrowid
    for r in person_data.get('responsibilities', []):
        conn.execute("INSERT INTO responsibilities (person_id, description) VALUES (?,?)", (pid, r))
    conn.commit()
    conn.close()
    return pid

def delete_person(person_id):
    conn = get_db()
    old = conn.execute("SELECT * FROM people WHERE id=?", (person_id,)).fetchone()
    if old:
        log_audit(conn, 'delete', 'person', person_id, dict(old), None)
        # Reassign reports
        conn.execute("UPDATE people SET reports_to=NULL WHERE reports_to=?", (person_id,))
        conn.execute("DELETE FROM people WHERE id=?", (person_id,))
        conn.commit()
    conn.close()

def backup_db():
    """Physical backup of the SQLite file."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    dest = os.path.join(BACKUP_DIR, f'orgchart_{ts}.db')
    # Use SQLite backup API for safe copy
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(dest)
    src.backup(dst)
    dst.close()
    src.close()
    # Keep last 50 backups
    backups = sorted([f for f in os.listdir(BACKUP_DIR) if f.endswith('.db')])
    while len(backups) > 50:
        os.remove(os.path.join(BACKUP_DIR, backups.pop(0)))
    return dest

def migrate_hc_columns():
    """Add hc_filled and hc_open columns if missing."""
    conn = get_db()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(people)").fetchall()]
    if 'hc_filled' not in cols:
        conn.execute("ALTER TABLE people ADD COLUMN hc_filled INTEGER NOT NULL DEFAULT 0")
    if 'hc_open' not in cols:
        conn.execute("ALTER TABLE people ADD COLUMN hc_open INTEGER NOT NULL DEFAULT 0")
    if 'sort_order' not in cols:
        conn.execute("ALTER TABLE people ADD COLUMN sort_order INTEGER DEFAULT 0")
    conn.commit()
    conn.close()

# --- Todos CRUD ---

def init_todos():
    """Create todos table if not exists."""
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS todos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            category TEXT DEFAULT '',
            done INTEGER NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            completed_at TEXT,
            sort_order INTEGER DEFAULT 0
        );
    """)
    conn.commit()
    conn.close()

def get_todos():
    conn = get_db()
    rows = conn.execute("SELECT * FROM todos ORDER BY done ASC, sort_order ASC, created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def create_todo(text, category=''):
    conn = get_db()
    cursor = conn.execute(
        "INSERT INTO todos (text, category) VALUES (?,?)", (text, category or '')
    )
    tid = cursor.lastrowid
    conn.commit()
    conn.close()
    return tid

def update_todo(todo_id, updates):
    conn = get_db()
    fields = []
    vals = []
    for k in ('text', 'category', 'done', 'sort_order'):
        if k in updates:
            fields.append(f"{k}=?")
            vals.append(updates[k])
    if 'done' in updates and updates['done']:
        fields.append("completed_at=datetime('now')")
    elif 'done' in updates and not updates['done']:
        fields.append("completed_at=NULL")
    if not fields:
        conn.close()
        return
    vals.append(todo_id)
    conn.execute(f"UPDATE todos SET {','.join(fields)} WHERE id=?", vals)
    conn.commit()
    conn.close()

def delete_todo(todo_id):
    conn = get_db()
    conn.execute("DELETE FROM todos WHERE id=?", (todo_id,))
    conn.commit()
    conn.close()

def clear_done_todos():
    conn = get_db()
    conn.execute("DELETE FROM todos WHERE done=1")
    conn.commit()
    conn.close()

# --- Settings KV store ---

def init_settings():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT DEFAULT ''
        );
    """)
    conn.commit()
    conn.close()

def get_setting(key):
    conn = get_db()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row['value'] if row else None

def set_setting(key, value):
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))
    conn.commit()
    conn.close()

# --- Threads CRUD ---

def init_threads():
    """Create threads tables if not exists."""
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS threads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            summary TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS thread_attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id INTEGER NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
            label TEXT NOT NULL,
            url TEXT NOT NULL,
            type TEXT DEFAULT 'link',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS thread_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id INTEGER NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
            content TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.commit()
    conn.close()

def get_threads():
    conn = get_db()
    rows = conn.execute("SELECT * FROM threads ORDER BY updated_at DESC").fetchall()
    result = []
    for r in rows:
        note_count = conn.execute("SELECT COUNT(*) FROM thread_notes WHERE thread_id=?", (r['id'],)).fetchone()[0]
        att_count = conn.execute("SELECT COUNT(*) FROM thread_attachments WHERE thread_id=?", (r['id'],)).fetchone()[0]
        result.append({**dict(r), 'note_count': note_count, 'attachment_count': att_count})
    conn.close()
    return result

def get_thread(thread_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM threads WHERE id=?", (thread_id,)).fetchone()
    if not row:
        conn.close()
        return None
    t = dict(row)
    t['attachments'] = [dict(a) for a in conn.execute(
        "SELECT * FROM thread_attachments WHERE thread_id=? ORDER BY created_at", (thread_id,)
    )]
    t['notes'] = [dict(n) for n in conn.execute(
        "SELECT * FROM thread_notes WHERE thread_id=? ORDER BY created_at", (thread_id,)
    )]
    conn.close()
    return t

def create_thread(title, summary=''):
    conn = get_db()
    cursor = conn.execute("INSERT INTO threads (title, summary) VALUES (?,?)", (title, summary))
    tid = cursor.lastrowid
    conn.commit()
    conn.close()
    return tid

def update_thread(thread_id, updates):
    conn = get_db()
    fields, vals = [], []
    for k in ('title', 'summary'):
        if k in updates:
            fields.append(f"{k}=?")
            vals.append(updates[k])
    if fields:
        fields.append("updated_at=datetime('now')")
        vals.append(thread_id)
        conn.execute(f"UPDATE threads SET {','.join(fields)} WHERE id=?", vals)
        conn.commit()
    conn.close()

def delete_thread(thread_id):
    conn = get_db()
    conn.execute("DELETE FROM threads WHERE id=?", (thread_id,))
    conn.commit()
    conn.close()

def add_thread_attachment(thread_id, label, url, atype='link'):
    conn = get_db()
    cursor = conn.execute(
        "INSERT INTO thread_attachments (thread_id, label, url, type) VALUES (?,?,?,?)",
        (thread_id, label, url, atype)
    )
    aid = cursor.lastrowid
    conn.execute("UPDATE threads SET updated_at=datetime('now') WHERE id=?", (thread_id,))
    conn.commit()
    conn.close()
    return aid

def delete_thread_attachment(att_id):
    conn = get_db()
    row = conn.execute("SELECT thread_id FROM thread_attachments WHERE id=?", (att_id,)).fetchone()
    conn.execute("DELETE FROM thread_attachments WHERE id=?", (att_id,))
    if row:
        conn.execute("UPDATE threads SET updated_at=datetime('now') WHERE id=?", (row['thread_id'],))
    conn.commit()
    conn.close()

def add_thread_note(thread_id, content):
    conn = get_db()
    cursor = conn.execute(
        "INSERT INTO thread_notes (thread_id, content) VALUES (?,?)", (thread_id, content)
    )
    nid = cursor.lastrowid
    conn.execute("UPDATE threads SET updated_at=datetime('now') WHERE id=?", (thread_id,))
    conn.commit()
    conn.close()
    return nid

def update_thread_note(note_id, content):
    conn = get_db()
    row = conn.execute("SELECT thread_id FROM thread_notes WHERE id=?", (note_id,)).fetchone()
    conn.execute("UPDATE thread_notes SET content=?, updated_at=datetime('now') WHERE id=?", (content, note_id))
    if row:
        conn.execute("UPDATE threads SET updated_at=datetime('now') WHERE id=?", (row['thread_id'],))
    conn.commit()
    conn.close()

def delete_thread_note(note_id):
    conn = get_db()
    row = conn.execute("SELECT thread_id FROM thread_notes WHERE id=?", (note_id,)).fetchone()
    conn.execute("DELETE FROM thread_notes WHERE id=?", (note_id,))
    if row:
        conn.execute("UPDATE threads SET updated_at=datetime('now') WHERE id=?", (row['thread_id'],))
    conn.commit()
    conn.close()

# Initialize on import
init_db()
seed_companies()
migrate_hc_columns()
init_todos()
init_settings()
init_threads()
