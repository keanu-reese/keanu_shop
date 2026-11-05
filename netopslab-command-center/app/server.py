#!/usr/bin/env python3
"""Self-hosted NETOPS Command Center. No telemetry, scheduled probes, or background scanning."""
from __future__ import annotations

import asyncio
from collections import defaultdict, deque
import fcntl
import html
import hmac
import ipaddress
import json
import os
from pathlib import Path
import pty
import re
import secrets
import signal
import socket
import struct
import tempfile
import termios
import time
import sys
import getpass
import store
from urllib.parse import urlsplit

from aiohttp import web, WSMsgType

BASE = Path(__file__).resolve().parent
STATIC = BASE / 'static'
DATA_FILE = Path(os.getenv('NETOPS_DATA_FILE', '/data/dashboard.json'))
HOST = os.getenv('NETOPS_LISTEN', '0.0.0.0')
PORT = int(os.getenv('NETOPS_PORT', '8765'))
PASSWORD = os.environ.get('NETOPS_PASSWORD', '')
MAX_BODY = 1_500_000
MAX_WS_BYTES = 16000
SESSION_SECONDS = 12 * 3600
HOST_PATTERN = re.compile(r'^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,251}[A-Za-z0-9])?$')
USER_PATTERN = re.compile(r'^[A-Za-z0-9._-]{1,64}$')
SESSIONS: dict[str, dict] = {}
ATTEMPTS = defaultdict(deque)
state_lock = asyncio.Lock()
OPEN_SESSIONS = defaultdict(int)



def check_host(raw) -> str:
    if not isinstance(raw, str):
        raise ValueError('Target hostname must be text')
    host = raw.strip()
    if host.startswith('[') and host.endswith(']'):
        host = host[1:-1]
    if not host or len(host) > 253 or host.startswith('-') or '%' in host:
        raise ValueError('Invalid target hostname or IP')
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        pass
    if not HOST_PATTERN.fullmatch(host):
        raise ValueError('Invalid hostname (letters, digits, dots, dashes, underscores only)')
    return host


def check_port(raw) -> int:
    if isinstance(raw, bool):
        raise ValueError('Invalid TCP port')
    try:
        p = int(raw)
    except (ValueError, TypeError):
        raise ValueError('Invalid TCP port') from None
    if not 1 <= p <= 65535:
        raise ValueError('TCP port must be 1-65535')
    return p


def check_user(raw) -> str:
    if not isinstance(raw, str):
        raise ValueError('Invalid SSH username')
    if raw and not USER_PATTERN.fullmatch(raw):
        raise ValueError('Invalid SSH username')
    return raw


def json_error(message: str, status: int = 400) -> web.Response:
    return web.json_response({'error': message}, status=status)


def security_headers(response: web.StreamResponse) -> web.StreamResponse:
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Content-Security-Policy'] = (
        "default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
        "connect-src 'self' ws: wss:; img-src 'self' data:; font-src 'self'; "
        "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
    )
    return response


def validate_origin(request: web.Request) -> bool:
    origin = request.headers.get('Origin')
    if not origin:
        # SameSite cookie and unpredictable X-NETOPS-Token still required for API mutations.
        return True
    try:
        u = urlsplit(origin)
        return u.scheme in ('http', 'https') and u.netloc.lower() == request.host.lower() and not u.username
    except ValueError:
        return False


@web.middleware
async def guard(request: web.Request, handler):
    # Authenticate before delivering even static dashboard assets.
    if request.path in ('/login', '/healthz'):
        return security_headers(await handler(request))
    sid = request.cookies.get('netops_session', '')
    user_session = SESSIONS.get(sid)
    if not user_session or user_session['expires'] <= time.monotonic():
        if request.path.startswith('/api/') or request.path.startswith('/ws/'):
            return security_headers(json_error('Login required', 401))
        raise web.HTTPFound('/login')
    current_user = store.get_user_by_id(user_session['user_id'])
    if not current_user:
        SESSIONS.pop(sid, None)
        return security_headers(json_error('Account is disabled or missing', 401))
    user_session['expires'] = time.monotonic() + SESSION_SECONDS
    request['session'] = user_session
    request['user'] = current_user
    if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
        if not validate_origin(request):
            return security_headers(json_error('Forbidden origin', 403))
        if request.headers.get('X-NETOPS-Token') != user_session['csrf']:
            return security_headers(json_error('Invalid session token', 403))
    if request.path.startswith('/ws/') and not validate_origin(request):
        return security_headers(json_error('Forbidden WebSocket origin', 403))
    return security_headers(await handler(request))


async def login_get(request: web.Request):
    error = 'Invalid username or password.' if request.query.get('error') else ''
    return web.Response(text=f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NETOPS · Sign in</title><style>
    *{{box-sizing:border-box}}body{{margin:0;min-height:100vh;display:grid;place-items:center;background:radial-gradient(ellipse at 75% 0,#172e43,#090f18 62%);color:#eaf6ff;font:15px system-ui,sans-serif}}main{{width:min(410px,92vw);background:#101c2a;border:1px solid #2e4356;border-radius:16px;padding:35px;box-shadow:0 20px 80px #0008}}.logo{{color:#65e5b4;font-size:13px;letter-spacing:.2em;font-weight:800}}h1{{font-size:26px;margin:16px 0 9px}}p{{color:#92aabf;line-height:1.6}}input{{display:block;width:100%;border:1px solid #36516a;background:#0b1622;color:white;padding:13px;border-radius:8px;margin:8px 0 18px}}button{{width:100%;border:0;border-radius:8px;background:#65e5b4;color:#0a291d;font-weight:800;padding:13px;cursor:pointer}}small{{display:block;color:#7290a8;margin-top:18px}}label{{font-size:12px;color:#b9cfe0}}.error{{color:#ffc6cb}}</style></head><body><main><div class="logo">⌘ &nbsp; NETOPS / COMMAND CENTER</div><h1>Welcome back.</h1><p>Sign in to your Umbrel-hosted network workspace.</p><p class="error">{html.escape(error)}</p><form method="post" action="/login"><label for="username">Username</label><input id="username" name="username" autocomplete="username" required maxlength="32" value="admin" autofocus><label for="password">Password</label><input id="password" name="password" type="password" autocomplete="current-password" required maxlength="256"><button type="submit">Open Command Center</button></form><small>Local accounts · No external identity provider</small></main></body></html>''', content_type='text/html')


async def login_post(request: web.Request):
    peer = request.remote or 'unknown'
    cutoff = time.monotonic() - 180
    while ATTEMPTS[peer] and ATTEMPTS[peer][0] < cutoff:
        ATTEMPTS[peer].popleft()
    if len(ATTEMPTS[peer]) >= 8:
        await asyncio.sleep(0.5)
        return json_error('Too many attempts; try again shortly', 429)
    if not validate_origin(request) or request.content_length is None or request.content_length > 2048:
        return json_error('Invalid login request',400)
    body = await request.post()
    username = str(body.get('username',''))[:32]
    password = str(body.get('password',''))[:256]
    account = store.authenticate(username, password)
    if not account:
        ATTEMPTS[peer].append(time.monotonic())
        await asyncio.sleep(0.35)
        raise web.HTTPFound('/login?error=1')
    ATTEMPTS[peer].clear()
    sid = secrets.token_urlsafe(32)
    SESSIONS[sid] = {'user_id':account['id'], 'expires':time.monotonic()+SESSION_SECONDS,
                     'csrf':secrets.token_urlsafe(32)}
    result = web.HTTPFound('/')
    result.set_cookie('netops_session', sid, httponly=True, samesite='Strict',
          secure=os.getenv('NETOPS_SECURE_COOKIE','false').lower() == 'true',
          max_age=SESSION_SECONDS, path='/')
    return result


async def dashboard(_request):
    return web.FileResponse(STATIC / 'dashboard.html')


async def static_asset(request):
    filename = request.match_info['file']
    if filename not in ('xterm.js', 'xterm.css'):
        raise web.HTTPNotFound()
    return web.FileResponse(STATIC / filename)


async def bridge(request):
    return web.json_response({'ready':True, 'token':request['session']['csrf'],
        'platform':'Docker / Linux','version':'3.0', 'terminal':'browser', 'storage':'server',
        'user':request['user']})


async def api_boards(request):
    return web.json_response({'dashboards':store.list_dashboards(request['user'])})


async def api_board_create(request):
    try:
        data=await body_json(request)
        bid=store.new_dashboard(request['user'], data.get('name'), data.get('teamAccess','none'))
        return web.json_response({'id':bid},status=201)
    except store.StoreError as e:return json_error(str(e),e.status)


async def api_state_get(request):
    try:
        bid=request.query.get('board','')
        return web.json_response(store.get_dashboard(bid,request['user']))
    except store.StoreError as e:return json_error(str(e),e.status)


async def body_json(request):
    if request.content_length is None or request.content_length > MAX_BODY:
        raise ValueError('Request size exceeds 1.5MB limit')
    if not request.content_type == 'application/json':
        raise ValueError('Expected application/json')
    obj = await request.json()
    if not isinstance(obj, dict):
        raise ValueError('Expected a JSON object')
    return obj


async def api_state_put(request):
    try:
        data=await body_json(request)
        state=data.get('state')
        if not isinstance(state,dict) or not all(isinstance(state.get(k),list) for k in ('assets','links','tasks','snippets')):
            raise ValueError('Invalid dashboard state')
        if not isinstance(state.get('notes',''),str):raise ValueError('Invalid notes')
        compact=json.dumps(state,ensure_ascii=False,separators=(',',':'))
        if len(compact.encode('utf-8'))>MAX_BODY:raise ValueError('Dashboard state too large')
        async with state_lock:
            revision=await asyncio.to_thread(store.put_dashboard,request.query.get('board',''),
                                 request['user'],state,data.get('revision'))
        return web.json_response({'saved':True,'revision':revision})
    except store.StoreError as e:return json_error(str(e),e.status)
    except (ValueError,json.JSONDecodeError) as e:return json_error(str(e))


async def api_share_get(request):
    try:return web.json_response(store.share_detail(request['user'],request.match_info['bid']))
    except store.StoreError as e:return json_error(str(e),e.status)


async def api_share_put(request):
    try:
        data=await body_json(request)
        store.save_share(request['user'],request.match_info['bid'],data)
        return web.json_response({'saved':True})
    except store.StoreError as e:return json_error(str(e),e.status)


async def user_directory(request):
    return web.json_response({'users':[{'id':u['id'],'username':u['username']} for u in store.list_users() if not u['disabled']]})


async def admin_users(request):
    if request['user']['role']!='admin':return json_error('Administrator permission required',403)
    return web.json_response({'users':store.list_users()})


async def admin_create(request):
    if request['user']['role']!='admin':return json_error('Administrator permission required',403)
    try:
        data=await body_json(request)
        uid=await asyncio.to_thread(store.create_user,data.get('username'),data.get('password'),data.get('role','user'))
        return web.json_response({'id':uid},status=201)
    except store.StoreError as e:return json_error(str(e),e.status)


async def admin_update(request):
    if request['user']['role']!='admin':return json_error('Administrator permission required',403)
    try:
        data=await body_json(request)
        uid=int(request.match_info['uid'])
        store.update_user(request['user']['id'],uid,data)
        # Changes to password, role, or disabled status take effect for all browser sessions.
        for sid,session in list(SESSIONS.items()):
            if session['user_id']==uid: SESSIONS.pop(sid,None)
        return web.json_response({'saved':True})
    except store.StoreError as e:return json_error(str(e),e.status)
    except ValueError:return json_error('Invalid user id')


async def change_password(request):
    try:
        data=await body_json(request)
        store.change_password(request['user']['id'],data.get('current'),data.get('new'))
        for sid,session in list(SESSIONS.items()):
            if session['user_id']==request['user']['id']:SESSIONS.pop(sid,None)
        response=web.json_response({'saved':True,'loginRequired':True})
        response.del_cookie('netops_session',path='/')
        return response
    except store.StoreError as e:return json_error(str(e),e.status)



async def logout(request):
    sid = request.cookies.get('netops_session', '')
    SESSIONS.pop(sid, None)
    response = web.json_response({'loggedOut': True})
    response.del_cookie('netops_session', path='/')
    return response


async def check_one(entry):
    identifier = str(entry.get('id', ''))[:100]
    try:
        host = check_host(entry.get('host'))
        port = check_port(entry.get('port'))
        start = time.perf_counter()
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=1.5)
        writer.close()
        await writer.wait_closed()
        return {'id': identifier, 'ok': True, 'elapsedMs': int((time.perf_counter() - start) * 1000)}
    except (OSError, ValueError, TimeoutError, asyncio.TimeoutError):
        return {'id': identifier, 'ok': False}


async def check_many(request):
    try:
        data = await body_json(request)
        targets = data.get('targets')
        if not isinstance(targets, list) or len(targets) > 40 or not all(isinstance(x, dict) for x in targets):
            raise ValueError('Supply up to 40 device targets')
        sem = asyncio.Semaphore(10)
        async def limited(entry):
            async with sem:
                return await check_one(entry)
        results = await asyncio.gather(*(limited(x) for x in targets))
        return web.json_response({'results': results})
    except ValueError as err:
        return json_error(str(err))


async def diagnostic(request):
    try:
        data = await body_json(request)
        host = check_host(data.get('host'))
        task = data.get('task')
        if task == 'ping':
            cmd, timeout = ['ping', '-c', '3', '-W', '1', '--', host], 12
        elif task == 'trace':
            # traceroute may differ from the same command run on Windows due to Docker routing.
            cmd, timeout = ['traceroute', '-n', '-m', '12', '-w', '1', '--', host], 38
        elif task == 'dns':
            cmd, timeout = ['nslookup', host], 15
        else:
            raise ValueError('Unsupported diagnostic type')
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.STDOUT)
        try:
            output, _ = await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return web.json_response({'output': f'{task} timed out after {timeout} seconds.'})
        return web.json_response({'output': output.decode('utf-8', 'replace')[:12000] or f'Exit status: {proc.returncode}'})
    except (OSError, ValueError) as err:
        return json_error(str(err))


def set_controlling_tty(fd):
    # Called in child process *after* start_new_session, before exec.
    fcntl.ioctl(fd, termios.TIOCSCTTY, 0)


async def ssh_socket(request: web.Request):
    user_id = request['user']['id']
    if OPEN_SESSIONS[user_id] >= 4 or sum(OPEN_SESSIONS.values()) >= 20:
        return json_error('SSH session limit reached (4 per user, 20 global)',429)
    OPEN_SESSIONS[user_id]+=1
    ws = web.WebSocketResponse(heartbeat=30, max_msg_size=MAX_WS_BYTES)
    await ws.prepare(request)
    master = None
    proc = None
    writer_task = None
    loop = asyncio.get_running_loop()
    output_q = asyncio.Queue(maxsize=256)
    try:
        initial = await ws.receive(timeout=15)
        if initial.type != WSMsgType.TEXT:
            raise ValueError('Expected SSH connection details')
        details = json.loads(initial.data)
        if not isinstance(details, dict) or details.get('type') != 'connect':
            raise ValueError('Expected connection request')
        if not hmac.compare_digest(str(details.get('csrf','')), request['session']['csrf']):
            raise ValueError('SSH session authorization failed')
        host = check_host(details.get('host'))
        port = check_port(details.get('port', 22))
        username = check_user(details.get('username', ''))
        # Every user has their own known_hosts and optional id_ed25519 key path.
        # Preserve the v2 administrator's existing ~/.ssh directory and configuration.
        if user_id == 1:
            ssh_dir = Path('/home/netops/.ssh')
            ssh_config = ssh_dir / 'config' if (ssh_dir / 'config').is_file() else Path('/dev/null')
        else:
            ssh_dir = DATA_FILE.parent / 'ssh-users' / str(user_id)
            ssh_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            ssh_dir.chmod(0o700)
            ssh_config = Path('/dev/null')
        # Passwords are entered directly into OpenSSH's PTY, never saved or logged.
        command = ['ssh', '-tt', '-F', str(ssh_config),
                   '-o', f'UserKnownHostsFile={ssh_dir / "known_hosts"}',
                   '-o', 'GlobalKnownHostsFile=/dev/null',
                   '-o', 'IdentityAgent=none', '-o', 'IdentitiesOnly=yes',
                   '-o', 'ConnectTimeout=12', '-o', 'StrictHostKeyChecking=ask',
                   '-o', 'ServerAliveInterval=30', '-o', 'ServerAliveCountMax=3',
                   '-o', 'UpdateHostKeys=no', '-p', str(port)]
        if username:
            command += ['-l', username]
        if user_id != 1:
            key_path = ssh_dir / 'id_ed25519'
            if key_path.is_file():
                command += ['-i', str(key_path)]
            else:
                command += ['-o', 'IdentityFile=none']
        command += ['--', host]
        master, slave = pty.openpty()
        def tty_setup():
            set_controlling_tty(slave)
        try:
            proc = await asyncio.create_subprocess_exec(*command, stdin=slave, stdout=slave,
                  stderr=slave, start_new_session=True, preexec_fn=tty_setup)
        finally:
            os.close(slave)
        os.set_blocking(master, False)
        def on_readable():
            try:
                buf = os.read(master, 8192)
                if not buf:
                    loop.remove_reader(master)
                    output_q.put_nowait(None)
                else:
                    if not output_q.full():
                        output_q.put_nowait(buf)
            except (OSError, asyncio.QueueFull):
                loop.remove_reader(master)
                if not output_q.full():
                    output_q.put_nowait(None)
        loop.add_reader(master, on_readable)
        async def write_output():
            while True:
                block = await output_q.get()
                if block is None:
                    if not ws.closed:
                        await ws.send_json({'type': 'exit', 'message': 'SSH session finished'})
                        await ws.close()
                    break
                if not ws.closed:
                    await ws.send_bytes(block)
        writer_task = asyncio.create_task(write_output())
        await ws.send_json({'type': 'connected', 'message': f'SSH client started: {host}:{port}'})
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    if data.get('type') == 'input':
                        keydata = data.get('data', '')
                        if isinstance(keydata, str) and len(keydata) <= 8000:
                            os.write(master, keydata.encode('utf-8'))
                    elif data.get('type') == 'resize':
                        rows = max(10, min(100, int(data.get('rows', 30))))
                        cols = max(40, min(280, int(data.get('cols', 100))))
                        fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))
                except (ValueError, OSError, AttributeError, TypeError):
                    pass
            elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE, WSMsgType.CLOSED):
                break
    except (ValueError, json.JSONDecodeError, OSError, asyncio.TimeoutError) as err:
        if not ws.closed:
            await ws.send_json({'type': 'error', 'message': str(err)[:250]})
    finally:
        if master is not None:
            loop.remove_reader(master)
            os.close(master)
        if proc is not None and proc.returncode is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), 2)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        if writer_task:
            writer_task.cancel()
            await asyncio.gather(writer_task, return_exceptions=True)
        if not ws.closed:
            await ws.close()
        OPEN_SESSIONS[user_id] = max(0, OPEN_SESSIONS[user_id]-1)
    return ws


def create_app():
    if not store.password_ok(PASSWORD) or PASSWORD.startswith('CHANGE_ME'):
        raise RuntimeError('NETOPS_PASSWORD must be configured (14+ chars, no sample value). See setup.sh.')
    store.setup(PASSWORD)
    app = web.Application(middlewares=[guard], client_max_size=MAX_BODY)
    app.router.add_get('/healthz', lambda _: web.json_response({'status': 'ok'}))
    app.router.add_get('/login', login_get)
    app.router.add_post('/login', login_post)
    app.router.add_get('/', dashboard)
    app.router.add_get('/dashboard.html', dashboard)
    app.router.add_get('/static/{file}', static_asset)
    app.router.add_get('/api/bridge', bridge)
    app.router.add_get('/api/boards',api_boards)
    app.router.add_post('/api/boards',api_board_create)
    app.router.add_get('/api/boards/{bid}/share',api_share_get)
    app.router.add_put('/api/boards/{bid}/share',api_share_put)
    app.router.add_get('/api/users/directory',user_directory)
    app.router.add_get('/api/admin/users',admin_users)
    app.router.add_post('/api/admin/users',admin_create)
    app.router.add_patch('/api/admin/users/{uid}',admin_update)
    app.router.add_post('/api/me/password',change_password)
    app.router.add_get('/api/state', api_state_get)
    app.router.add_put('/api/state', api_state_put)
    app.router.add_post('/api/logout', logout)
    app.router.add_post('/api/check', check_many)
    app.router.add_post('/api/diagnostic', diagnostic)
    app.router.add_get('/ws/ssh', ssh_socket)
    return app


if __name__ == '__main__':
    if len(sys.argv)>2 and sys.argv[1]=='reset-password':
        store.setup(PASSWORD)
        pw=getpass.getpass('New password for '+sys.argv[2]+' (14+ characters): ')
        if pw!=getpass.getpass('Confirm password: '):
            raise SystemExit('Passwords do not match')
        store.reset_password_cli(sys.argv[2],pw)
        print('Password updated. Restart the container to revoke currently active sessions.')
    else:
        print(f'NETOPS Command Center Docker server on {HOST}:{PORT}, authentication enabled', flush=True)
        web.run_app(create_app(), host=HOST, port=PORT, access_log=None)
