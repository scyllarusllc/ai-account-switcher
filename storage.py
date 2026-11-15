"""SQLite storage for named Chrome profiles; imports a legacy JSON file once."""
import json
from pathlib import Path
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

DEFAULT_DB = Path(__file__).resolve().with_name('profiles.db')


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect(path):
    path = Path(path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    existed = path.exists()
    connection = sqlite3.connect(path, timeout=5)
    if not existed:
        path.chmod(0o600)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA busy_timeout=5000')
    connection.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
    connection.execute('''CREATE TABLE IF NOT EXISTS profiles (
        name TEXT PRIMARY KEY, data TEXT NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
    connection.commit()
    try:
        yield connection
    finally:
        connection.close()


def import_json(connection, json_path):
    json_path = Path(json_path)
    if not json_path.is_file():
        return False
    if connection.execute("SELECT 1 FROM settings WHERE key='legacy_import_done'").fetchone():
        return False
    data = json.loads(json_path.read_text())
    if not isinstance(data, dict) or not isinstance(data.get('profiles'), dict):
        raise ValueError('旧配置必须包含 profiles 对象')
    timestamp = datetime.fromtimestamp(json_path.stat().st_mtime, timezone.utc).isoformat()
    metadata = data.get('profile_meta', {})
    with connection:
        for name, profile in data['profiles'].items():
            dates = metadata.get(name, {}) if isinstance(metadata, dict) else {}
            connection.execute('INSERT INTO profiles VALUES (?, ?, ?, ?)',
                               (name, json.dumps(profile, ensure_ascii=False), dates.get('created_at', timestamp), dates.get('updated_at', timestamp)))
        if data.get('chrome_binary'):
            connection.execute('INSERT OR REPLACE INTO settings VALUES (?, ?)', ('chrome_binary', data['chrome_binary']))
        connection.execute("INSERT OR REPLACE INTO settings VALUES ('legacy_import_done', '1')")
    return True


def read_config(path):
    with connect(path) as connection:
        profiles = {}
        metadata = {}
        for row in connection.execute('SELECT name, data, created_at, updated_at FROM profiles ORDER BY name COLLATE NOCASE'):
            profiles[row['name']] = json.loads(row['data'])
            metadata[row['name']] = {'created_at': row['created_at'], 'updated_at': row['updated_at']}
        binary = connection.execute("SELECT value FROM settings WHERE key='chrome_binary'").fetchone()
    return {'profiles': profiles, 'profile_meta': metadata, **({'chrome_binary': binary[0]} if binary else {})}


def save_profile(path, name, profile, original=None):
    with connect(path) as connection:
        with connection:
            if original is not None:
                row = connection.execute('SELECT created_at FROM profiles WHERE name=?', (original,)).fetchone()
                if row is None:
                    raise ValueError('原配置已不存在，请刷新页面')
            else:
                row = None
            if name != original and connection.execute('SELECT 1 FROM profiles WHERE name=?', (name,)).fetchone():
                raise ValueError('配置名称已存在，请使用其他名称')
            created = row['created_at'] if row else now()
            if original and original != name:
                connection.execute('DELETE FROM profiles WHERE name=?', (original,))
            connection.execute('INSERT OR REPLACE INTO profiles VALUES (?, ?, ?, ?)',
                               (name, json.dumps(profile, ensure_ascii=False), created, now()))


def delete_profile(path, name):
    with connect(path) as connection:
        with connection:
            if not connection.execute('DELETE FROM profiles WHERE name=?', (name,)).rowcount:
                raise ValueError('找不到此配置，请刷新页面')


def touch_profile(path, name):
    with connect(path) as connection:
        with connection:
            if not connection.execute('UPDATE profiles SET updated_at=? WHERE name=?', (now(), name)).rowcount:
                raise ValueError('找不到此配置，请刷新页面')
