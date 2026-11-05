"""SQLite persistence, account permissions and migration from the v2 shared JSON dashboard."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time

DB_PATH = Path(os.environ.get('NETOPS_DB_FILE', '/data/netops.sqlite3'))
LEGACY_PATH = Path(os.environ.get('NETOPS_DATA_FILE', '/data/dashboard.json'))
USERNAME = re.compile(r'^[A-Za-z][A-Za-z0-9._-]{2,31}$')
VALID_ROLE = ('admin', 'user')
VALID_ACCESS = ('none', 'view', 'edit')


class StoreError(ValueError):
    def __init__(self, msg, status=400):
        super().__init__(msg)
        self.status = status


def password_ok(value):
    return isinstance(value, str) and 14 <= len(value) <= 256 and '\x00' not in value


def password_hash(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    result = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 480_000)
    return salt.hex(), result.hex()


def verified(password, row):
    try:
        stored = bytes.fromhex(row['hash'])
        supplied = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(row['salt']), 480_000)
        return hmac.compare_digest(stored, supplied)
    except (TypeError, ValueError):
        return False


def connection():
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    c.execute('PRAGMA busy_timeout=10000')
    return c


def setup(bootstrap):
    if not password_ok(bootstrap):
        raise RuntimeError('NETOPS_PASSWORD in .env must be at least 14 characters (bootstrap secret).')
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    first = not DB_PATH.exists()
    if first:
        flags = os.umask(0o077)
    try:
        with connection() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY, username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                role TEXT NOT NULL CHECK(role IN ('admin','user')),
                salt TEXT NOT NULL, hash TEXT NOT NULL,
                disabled INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL)''')
            c.execute('''CREATE TABLE IF NOT EXISTS dashboards (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, owner_id INTEGER NOT NULL REFERENCES users(id),
                team_access TEXT NOT NULL DEFAULT 'none' CHECK(team_access IN ('none','view','edit')),
                state_json TEXT, revision INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)''')
            c.execute('''CREATE TABLE IF NOT EXISTS members (
                dashboard_id TEXT NOT NULL REFERENCES dashboards(id) ON DELETE CASCADE,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                access TEXT NOT NULL CHECK(access IN ('view','edit')),
                PRIMARY KEY(dashboard_id, user_id))''')
            if c.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 0:
                salt, hashed = password_hash(bootstrap)
                now = int(time.time())
                c.execute('INSERT INTO users(username,role,salt,hash,created_at) VALUES(?,?,?,?,?)',
                          ('admin', 'admin', salt, hashed, now))
                admin_id = c.execute('SELECT id FROM users WHERE username=?', ('admin',)).fetchone()['id']
                legacy = None
                if LEGACY_PATH.is_file():
                    try:
                        data = json.loads(LEGACY_PATH.read_text(encoding='utf-8'))
                        if isinstance(data, dict) and isinstance(data.get('assets'), list):
                            legacy = json.dumps(data, separators=(',', ':'))
                    except (OSError, ValueError):
                        pass
                c.execute('INSERT INTO dashboards VALUES(?,?,?,?,?,?,?,?)',
                          ('shared-operations', 'Operations · Shared', admin_id, 'edit', legacy, 0, now, now))
                c.execute('INSERT INTO dashboards VALUES(?,?,?,?,?,?,?,?)',
                          ('admin-private', 'My workspace', admin_id, 'none', None, 0, now, now))
    finally:
        if first:
            os.umask(flags)
    os.chmod(DB_PATH, 0o600)


def get_user_by_id(uid):
    with connection() as c:
        r = c.execute('SELECT id,username,role,disabled FROM users WHERE id=?', (uid,)).fetchone()
        return dict(r) if r and not r['disabled'] else None


def authenticate(username, password):
    if not isinstance(username, str) or not isinstance(password, str):
        return None
    with connection() as c:
        r = c.execute('SELECT * FROM users WHERE username=? AND disabled=0', (username,)).fetchone()
    if r and verified(password, r):
        return {'id': r['id'], 'username': r['username'], 'role': r['role']}
    # Equalize computational cost for unknown users.
    password_hash(password[:256], bytes(16))
    return None


def list_users():
    with connection() as c:
        return [dict(r) for r in c.execute('SELECT id,username,role,disabled,created_at FROM users ORDER BY username')]


def create_user(username, password, role):
    if not isinstance(username, str) or not USERNAME.fullmatch(username):
        raise StoreError('Username must be 3–32 characters, start with a letter, and use letters, numbers, ., _ or -')
    if not password_ok(password):
        raise StoreError('Password must be 14–256 characters')
    if role not in VALID_ROLE:
        raise StoreError('Invalid role')
    salt, hashed = password_hash(password)
    with connection() as c:
        try:
            c.execute('INSERT INTO users(username,role,salt,hash,created_at) VALUES(?,?,?,?,?)',
                      (username,role,salt,hashed,int(time.time())))
        except sqlite3.IntegrityError:
            raise StoreError('That username already exists', 409) from None
        uid = c.execute('SELECT id FROM users WHERE username=?',(username,)).fetchone()['id']
        c.execute('INSERT INTO dashboards VALUES(?,?,?,?,?,?,?,?)',
                  (secrets.token_hex(10), 'My workspace',uid,'none',None,0,int(time.time()),int(time.time())))
    return uid


def update_user(actor_id, uid, data):
    with connection() as c:
        r = c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
        if not r:
            raise StoreError('User not found',404)
        role = data.get('role',r['role'])
        disabled = data.get('disabled',bool(r['disabled']))
        password = data.get('password')
        if role not in VALID_ROLE or not isinstance(disabled, bool):
            raise StoreError('Invalid role or enabled setting')
        if uid == actor_id and (role != 'admin' or disabled):
            raise StoreError('You cannot revoke your own admin access',400)
        if r['role']=='admin' and (role !='admin' or disabled):
            if c.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND disabled=0").fetchone()[0] <= 1:
                raise StoreError('Cannot disable or demote the final active administrator',409)
        if password is not None and not password_ok(password):
            raise StoreError('Password must be 14–256 characters')
        c.execute('UPDATE users SET role=?, disabled=? WHERE id=?',(role,int(disabled),uid))
        if password is not None:
            salt, hashed=password_hash(password)
            c.execute('UPDATE users SET salt=?,hash=? WHERE id=?',(salt,hashed,uid))
    return True


def change_password(uid,old,new):
    if not isinstance(old,str):
        raise StoreError('Current password is required')
    if not password_ok(new):
        raise StoreError('New password must be 14–256 characters')
    with connection() as c:
        r=c.execute('SELECT * FROM users WHERE id=? AND disabled=0',(uid,)).fetchone()
        if not r or not verified(old,r):
            raise StoreError('Current password is incorrect',403)
        salt, hashed=password_hash(new)
        c.execute('UPDATE users SET salt=?,hash=? WHERE id=?',(salt,hashed,uid))


def reset_password_cli(username, password):
    if not password_ok(password):
        raise StoreError('Password must be at least 14 characters')
    with connection() as c:
        row=c.execute('SELECT id FROM users WHERE username=?',(username,)).fetchone()
        if not row:raise StoreError('User not found',404)
        salt, hashed=password_hash(password)
        c.execute('UPDATE users SET salt=?,hash=? WHERE id=?',(salt,hashed,row['id']))


def permission(c, board, user):
    if not board or not user: return None
    if user['role']=='admin' or board['owner_id']==user['id']:return 'owner'
    acl=c.execute('SELECT access FROM members WHERE dashboard_id=? AND user_id=?',
                  (board['id'],user['id'])).fetchone()
    if acl:return acl['access']
    return board['team_access'] if board['team_access']!='none' else None


def list_dashboards(user):
    with connection() as c:
        boards=c.execute('SELECT * FROM dashboards ORDER BY created_at,id').fetchall()
        return [{'id':b['id'],'name':b['name'],'access':p,'teamAccess':b['team_access'],
                 'ownerId':b['owner_id'],'revision':b['revision']}
                for b in boards if (p:=permission(c,b,user))]


def get_dashboard(uid, user):
    with connection() as c:
        b=c.execute('SELECT * FROM dashboards WHERE id=?',(uid,)).fetchone()
        p=permission(c,b,user)
        if not p:raise StoreError('Dashboard not found or inaccessible',404)
        return {'id':b['id'],'name':b['name'],'access':p,'revision':b['revision'],
                'state':json.loads(b['state_json']) if b['state_json'] else None}


def put_dashboard(uid,user,state,expected_rev):
    if not isinstance(expected_rev,int) or isinstance(expected_rev,bool) or expected_rev<0:
        raise StoreError('Missing or invalid revision')
    serialized=json.dumps(state,ensure_ascii=False,separators=(',',':'))
    with connection() as c:
        b=c.execute('SELECT * FROM dashboards WHERE id=?',(uid,)).fetchone()
        p=permission(c,b,user)
        if not p:raise StoreError('Dashboard not found',404)
        if p not in ('owner','edit'):raise StoreError('You have read-only access',403)
        if b['revision']!=expected_rev:
            raise StoreError('Dashboard changed in another session. Export your unsaved work, then reload.',409)
        c.execute('UPDATE dashboards SET state_json=?,revision=revision+1,updated_at=? WHERE id=?',
                  (serialized,int(time.time()),uid))
        return b['revision']+1


def new_dashboard(user,name,team_access):
    if not isinstance(name,str) or not 1 <= len(name.strip())<=70:raise StoreError('Dashboard name must be 1–70 characters')
    if team_access not in VALID_ACCESS:raise StoreError('Invalid team access')
    bid=secrets.token_hex(12)
    now=int(time.time())
    with connection() as c:
        c.execute('INSERT INTO dashboards VALUES(?,?,?,?,?,?,?,?)',
                  (bid,name.strip(),user['id'],team_access,None,0,now,now))
    return bid


def share_detail(user,bid):
    with connection() as c:
        b=c.execute('SELECT * FROM dashboards WHERE id=?',(bid,)).fetchone()
        if permission(c,b,user)!='owner':raise StoreError('Only the owner or an administrator can manage sharing',403)
        memberships=[dict(r) for r in c.execute('SELECT user_id AS userId, access FROM members WHERE dashboard_id=? ORDER BY user_id',(bid,))]
        return {'id':bid,'name':b['name'],'teamAccess':b['team_access'],'members':memberships}


def save_share(user,bid,data):
    name=data.get('name')
    team=data.get('teamAccess')
    members=data.get('members')
    if not isinstance(name,str) or not 1<=len(name.strip())<=70:raise StoreError('Dashboard name must be 1–70 characters')
    if team not in VALID_ACCESS or not isinstance(members,list) or len(members)>200:raise StoreError('Invalid sharing settings')
    normalized={}
    for entry in members:
        if not isinstance(entry,dict) or type(entry.get('userId')) is not int or entry.get('access') not in ('view','edit'):
            raise StoreError('Invalid user sharing entry')
        normalized[entry['userId']]=entry['access']
    with connection() as c:
        b=c.execute('SELECT * FROM dashboards WHERE id=?',(bid,)).fetchone()
        if permission(c,b,user)!='owner':raise StoreError('Permission denied',403)
        available={r['id'] for r in c.execute('SELECT id FROM users WHERE disabled=0')}
        if any(u not in available for u in normalized):raise StoreError('A shared user is missing or disabled')
        c.execute('UPDATE dashboards SET name=?, team_access=? WHERE id=?',(name.strip(),team,bid))
        c.execute('DELETE FROM members WHERE dashboard_id=?',(bid,))
        for member,access in normalized.items():
            if member!=b['owner_id']:
                c.execute('INSERT INTO members VALUES(?,?,?)',(bid,member,access))
    return True
