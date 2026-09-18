from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import secrets
import sqlite3
import time

from .models import Settings
from .auth import new_password_record, verify_password


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS admin_auth (
                    id INTEGER PRIMARY KEY CHECK(id=1), username TEXT NOT NULL,
                    salt BLOB NOT NULL, password_hash BLOB NOT NULL,
                    must_change INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY, expires_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS channels (
                    id INTEGER PRIMARY KEY, url TEXT NOT NULL, name TEXT NOT NULL,
                    interval_minutes INTEGER NOT NULL, format TEXT NOT NULL, resolution INTEGER NOT NULL,
                    tab TEXT NOT NULL, initial_count INTEGER NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
                    initialized INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL,
                    last_scan REAL, next_scan REAL NOT NULL DEFAULT 0,
                    scanning INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '',
                    UNIQUE(url, tab)
                );
                CREATE TABLE IF NOT EXISTS videos (
                    channel_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
                    video_id TEXT NOT NULL, title TEXT NOT NULL, discovered_at REAL NOT NULL,
                    PRIMARY KEY(channel_id, video_id)
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
                    video_id TEXT NOT NULL, title TEXT NOT NULL, format TEXT NOT NULL, resolution INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued', progress REAL NOT NULL DEFAULT 0,
                    stage TEXT NOT NULL DEFAULT '', speed TEXT NOT NULL DEFAULT '', eta TEXT NOT NULL DEFAULT '',
                    attempts INTEGER NOT NULL DEFAULT 0, available_at REAL NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL, finished_at REAL, error TEXT NOT NULL DEFAULT '',
                    filepath TEXT NOT NULL DEFAULT '', UNIQUE(channel_id, video_id)
                );
                CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(status, available_at);
                CREATE TABLE IF NOT EXISTS manual_jobs (
                    id INTEGER PRIMARY KEY, video_id TEXT NOT NULL, title TEXT NOT NULL,
                    uploader TEXT NOT NULL DEFAULT '', format TEXT NOT NULL, resolution INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued', progress REAL NOT NULL DEFAULT 0,
                    stage TEXT NOT NULL DEFAULT '', speed TEXT NOT NULL DEFAULT '', eta TEXT NOT NULL DEFAULT '',
                    attempts INTEGER NOT NULL DEFAULT 0, available_at REAL NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL, finished_at REAL, error TEXT NOT NULL DEFAULT '',
                    filepath TEXT NOT NULL DEFAULT '', output_dir TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS manual_jobs_queue ON manual_jobs(status, available_at);
            ''')
            db.execute('INSERT OR IGNORE INTO settings VALUES (1, ?)', (Settings().model_dump_json(),))
            if not db.execute('SELECT 1 FROM admin_auth WHERE id=1').fetchone():
                salt, digest = new_password_record('keeper')
                db.execute('INSERT OR IGNORE INTO admin_auth VALUES (1, ?, ?, ?, 1)', ('keeper', salt, digest))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def settings(self):
        with self.connect() as db:
            return {**Settings().model_dump(), **json.loads(db.execute('SELECT value FROM settings WHERE id=1').fetchone()[0])}

    def save_settings(self, settings):
        with self.connect() as db:
            db.execute('UPDATE settings SET value=? WHERE id=1', (json.dumps(settings),))

    def auth_info(self):
        with self.connect() as db:
            row = db.execute('SELECT username,must_change FROM admin_auth WHERE id=1').fetchone()
            return {'username': row['username'], 'must_change': bool(row['must_change'])}

    def verify_credentials(self, username, password):
        with self.connect() as db:
            row = db.execute('SELECT username,salt,password_hash FROM admin_auth WHERE id=1').fetchone()
        valid_password = verify_password(password, row['salt'], row['password_hash'])
        return secrets.compare_digest(username, row['username']) and valid_password

    def create_session(self):
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode('ascii')).hexdigest()
        with self.connect() as db:
            db.execute('DELETE FROM sessions WHERE expires_at<=?', (time.time(),))
            db.execute('INSERT INTO sessions VALUES (?,?)', (digest, time.time() + 86400))
        return token

    def session_valid(self, token):
        if not token or len(token) > 128:
            return False
        digest = hashlib.sha256(token.encode('utf-8')).hexdigest()
        with self.connect() as db:
            return db.execute('SELECT 1 FROM sessions WHERE token_hash=? AND expires_at>?',
                              (digest, time.time())).fetchone() is not None

    def revoke_session(self, token):
        if not token:
            return
        digest = hashlib.sha256(token.encode('utf-8')).hexdigest()
        with self.connect() as db:
            db.execute('DELETE FROM sessions WHERE token_hash=?', (digest,))

    def change_credentials(self, current_password, username, new_password):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM admin_auth WHERE id=1').fetchone()
            if not verify_password(current_password, row['salt'], row['password_hash']):
                return False
            if not new_password and username == row['username']:
                raise ValueError('请输入新用户名或新密码')
            salt, digest = (new_password_record(new_password) if new_password else
                            (row['salt'], row['password_hash']))
            db.execute('''UPDATE admin_auth SET username=?,salt=?,password_hash=?,must_change=? WHERE id=1''',
                       (username, salt, digest, 0 if new_password else row['must_change']))
            db.execute('DELETE FROM sessions')
            return True

    def channels(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute('''SELECT c.*,
                (SELECT COUNT(*) FROM videos v WHERE v.channel_id=c.id) AS video_count,
                (SELECT COUNT(*) FROM jobs j WHERE j.channel_id=c.id AND j.status='completed') AS completed_count
                FROM channels c ORDER BY c.id DESC''')]

    def channel(self, channel_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM channels WHERE id=?', (channel_id,)).fetchone()
            return dict(row) if row else None

    def add_channels(self, channels):
        ids = []
        with self.connect() as db:
            for c in channels:
                cur = db.execute('''INSERT INTO channels
                    (url,name,interval_minutes,format,resolution,tab,initial_count,created_at,next_scan)
                    VALUES (?,?,?,?,?,?,?,?,?)''', (c.url, c.name.strip() or c.url.rsplit('/', 1)[-1],
                    c.interval_minutes, c.format, c.resolution, c.tab, c.initial_count, time.time(),
                    c.start_at if c.schedule_mode == 'scheduled' else 0))
                ids.append(cur.lastrowid)
        return ids

    def update_channel(self, channel_id, data):
        next_scan = ('next_scan' if data.schedule_mode == 'keep' else '?')
        next_value = data.start_at if data.schedule_mode == 'scheduled' else 0
        values = [data.name, data.interval_minutes, data.format, data.resolution, int(data.enabled)]
        if data.schedule_mode != 'keep':
            values.append(next_value)
        values.append(channel_id)
        with self.connect() as db:
            db.execute(f'''UPDATE channels SET name=?,interval_minutes=?,format=?,resolution=?,enabled=?,
                next_scan={next_scan} WHERE id=?''', values)

    def remove_channel(self, channel_id):
        with self.connect() as db:
            db.execute('DELETE FROM channels WHERE id=?', (channel_id,))

    def request_scan(self, channel_id=None):
        with self.connect() as db:
            if channel_id is None:
                db.execute('UPDATE channels SET next_scan=0 WHERE enabled=1 AND scanning=0')
            else:
                db.execute('UPDATE channels SET next_scan=0 WHERE id=? AND scanning=0', (channel_id,))

    def claim_scan(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('''SELECT * FROM channels WHERE enabled=1 AND scanning=0 AND next_scan<=?
                ORDER BY next_scan,id LIMIT 1''', (time.time(),)).fetchone()
            if row:
                db.execute('UPDATE channels SET scanning=1 WHERE id=?', (row['id'],))
            return dict(row) if row else None

    def finish_scan(self, channel_id, entries, channel_name=''):
        now = time.time()
        added = 0
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            channel = db.execute('SELECT * FROM channels WHERE id=?', (channel_id,)).fetchone()
            if not channel:
                return 0
            eligible = []
            for entry in entries:
                video_id = entry.get('id', '')
                if not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
                    raise ValueError('频道列表包含无效视频 ID，未保存此次扫描')
                if entry.get('live_status') in ('is_live', 'is_upcoming', 'post_live'):
                    continue
                eligible.append(entry)
            initial_ids = {e['id'] for e in eligible[:channel['initial_count']]}
            for entry in eligible:
                title = entry.get('title') or entry['id']
                cur = db.execute('INSERT OR IGNORE INTO videos VALUES (?,?,?,?)',
                                 (channel_id, entry['id'], title, now))
                if cur.rowcount and (channel['initialized'] or entry['id'] in initial_ids):
                    db.execute('''INSERT OR IGNORE INTO jobs
                        (channel_id,video_id,title,format,resolution,created_at) VALUES (?,?,?,?,?,?)''',
                        (channel_id, entry['id'], title, channel['format'], channel['resolution'], now))
                    added += 1
            name = channel['name']
            if not channel['initialized'] and name == channel['url'].rsplit('/', 1)[-1] and channel_name:
                name = channel_name[:120]
            db.execute('''UPDATE channels SET initialized=1,scanning=0,error='',last_scan=?,next_scan=?,name=?
                WHERE id=?''', (now, now + channel['interval_minutes'] * 60, name, channel_id))
        return added

    def scan_error(self, channel_id, error):
        with self.connect() as db:
            if error.startswith('Cookies 已失效'):
                db.execute('UPDATE channels SET scanning=0,error=?,next_scan=? WHERE id=?',
                           (error[-2000:], 32503680000, channel_id))
            else:
                db.execute('''UPDATE channels SET scanning=0,error=?,next_scan=? + MIN(interval_minutes,15)*60
                    WHERE id=?''', (error[-2000:], time.time(), channel_id))

    def jobs(self, limit=300):
        with self.connect() as db:
            return [dict(r) for r in db.execute('''SELECT j.*,c.name AS channel_name FROM jobs j
                JOIN channels c ON c.id=j.channel_id
                ORDER BY CASE WHEN j.status='downloading' THEN 0 ELSE 1 END,j.id DESC LIMIT ?''', (limit,))]

    def job(self, job_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            return dict(row) if row else None

    def claim_job(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('''SELECT j.*,c.name AS channel_name FROM jobs j JOIN channels c ON c.id=j.channel_id
                WHERE j.status IN ('queued','retrying') AND j.available_at<=? AND c.enabled=1
                ORDER BY j.id LIMIT 1''', (time.time(),)).fetchone()
            if row:
                db.execute("UPDATE jobs SET status='downloading',attempts=attempts+1,error='',stage='正在解析',progress=0 WHERE id=?", (row['id'],))
                row = dict(row)
                row['attempts'] += 1
            return dict(row) if row else None

    def update_job(self, job_id, **fields):
        allowed = {'status', 'progress', 'stage', 'speed', 'eta', 'available_at', 'finished_at', 'error', 'filepath', 'attempts'}
        if not fields or not fields.keys() <= allowed:
            raise ValueError('Invalid job fields')
        with self.connect() as db:
            db.execute(f"UPDATE jobs SET {','.join(k+'=?' for k in fields)} WHERE id=?",
                       (*fields.values(), job_id))

    def manual_jobs(self, limit=300):
        with self.connect() as db:
            return [dict(r) for r in db.execute('''SELECT * FROM manual_jobs
                ORDER BY CASE WHEN status='downloading' THEN 0 ELSE 1 END,id DESC LIMIT ?''', (limit,))]

    def manual_job(self, job_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM manual_jobs WHERE id=?', (job_id,)).fetchone()
            return dict(row) if row else None

    def add_manual_job(self, video_id, title, uploader, fmt, resolution, output_dir):
        with self.connect() as db:
            cur = db.execute('''INSERT INTO manual_jobs
                (video_id,title,uploader,format,resolution,created_at,output_dir)
                VALUES (?,?,?,?,?,?,?)''', (video_id, title[:500], uploader[:200], fmt,
                    resolution, time.time(), output_dir))
            return cur.lastrowid

    def claim_manual_job(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('''SELECT * FROM manual_jobs WHERE status IN ('queued','retrying')
                AND available_at<=? ORDER BY id LIMIT 1''', (time.time(),)).fetchone()
            if row:
                db.execute("UPDATE manual_jobs SET status='downloading',attempts=attempts+1,error='',stage='正在解析',progress=0 WHERE id=?", (row['id'],))
                row = dict(row)
                row['attempts'] += 1
                row['kind'] = 'manual'
            return row

    def update_manual_job(self, job_id, **fields):
        allowed = {'status', 'progress', 'stage', 'speed', 'eta', 'available_at', 'finished_at', 'error', 'filepath', 'attempts'}
        if not fields or not fields.keys() <= allowed:
            raise ValueError('Invalid job fields')
        with self.connect() as db:
            db.execute(f"UPDATE manual_jobs SET {','.join(k+'=?' for k in fields)} WHERE id=?",
                       (*fields.values(), job_id))

    def retry_auth_failures(self):
        with self.connect() as db:
            job_cursor = db.execute('''UPDATE jobs SET status='queued',attempts=0,available_at=0,
                error='',progress=0,stage='Cookies 已更新，等待重试',speed='',eta=''
                WHERE status IN ('failed','retrying') AND
                (error LIKE '%confirm you%bot%' OR error LIKE '%要求登录验证%' OR error LIKE 'Cookies 已失效%')''')
            manual_cursor = db.execute('''UPDATE manual_jobs SET status='queued',attempts=0,available_at=0,
                error='',progress=0,stage='Cookies 已更新，等待重试',speed='',eta=''
                WHERE status IN ('failed','retrying') AND
                (error LIKE '%confirm you%bot%' OR error LIKE '%要求登录验证%' OR error LIKE 'Cookies 已失效%')''')
            db.execute('''UPDATE channels SET next_scan=0,error=''
                WHERE enabled=1 AND (error LIKE '%confirm you%bot%' OR error LIKE '%要求登录验证%' OR error LIKE 'Cookies 已失效%')''')
            return job_cursor.rowcount + manual_cursor.rowcount

    def stats(self):
        with self.connect() as db:
            counts = dict(db.execute('SELECT status,COUNT(*) FROM jobs GROUP BY status').fetchall())
            for status, count in db.execute('SELECT status,COUNT(*) FROM manual_jobs GROUP BY status'):
                counts[status] = counts.get(status, 0) + count
            return {'channels': db.execute('SELECT COUNT(*) FROM channels').fetchone()[0],
                    'completed': counts.get('completed', 0),
                    'pending': sum(counts.get(s, 0) for s in ('queued', 'retrying', 'downloading')),
                    'failed': counts.get('failed', 0)}

    def recover(self):
        with self.connect() as db:
            db.execute('UPDATE channels SET scanning=0')
            db.execute("UPDATE jobs SET status='queued',progress=0,stage='重启后恢复',attempts=MAX(0,attempts-1) WHERE status='downloading'")
            db.execute("UPDATE manual_jobs SET status='queued',progress=0,stage='重启后恢复',attempts=MAX(0,attempts-1) WHERE status='downloading'")
