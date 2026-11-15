#!/usr/bin/env python3
"""Loopback-only web dashboard for the Chrome launcher (no dependencies)."""
import argparse
from collections import deque
import copy
import hashlib
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import queue
import subprocess
import threading
import time
from urllib.parse import parse_qs, urlsplit

from chrome_profiles import DEFAULT_CONFIG, build_command, chrome_binary, read_config
from storage import connect, import_json, save_profile, delete_profile, touch_profile
from auth_store import AuthStore

ROOT = Path(__file__).resolve().parent


def render_page(filename, section):
    public = ROOT / 'public'
    header = (public / 'partials' / 'header.html').read_text()
    footer = (public / 'partials' / 'footer.html').read_text()
    header = header.replace(f'data-nav="{section}"', f'data-nav="{section}" aria-current="page"')
    return (public / filename).read_text().replace('<!--#header-->', header).replace('<!--#footer-->', footer).encode()



def validate_profile(name, profile):
    if not isinstance(name, str) or not re.fullmatch(r'[\w-]{1,64}', name):
        raise ValueError('配置名称需为 1–64 个字母、数字、下划线或短横线')
    if not isinstance(profile, dict):
        raise ValueError('配置格式错误')
    allowed = {'user_data_dir', 'profile_directory', 'proxy', 'proxy_bypass_list', 'args', 'urls'}
    if set(profile) - allowed:
        raise ValueError('配置包含不支持的字段')
    for key in allowed - {'args', 'urls'}:
        value = profile.get(key, '')
        if not isinstance(value, str) or '\x00' in value or len(value) > 4096:
            raise ValueError(f'{key} 必须为有效字符串')
    if not profile.get('user_data_dir', '').strip():
        raise ValueError('请输入用户数据目录')
    if profile.get('profile_directory') in {'.', '..'} or any(c in profile.get('profile_directory', '') for c in '/\\'):
        raise ValueError('Profile 子目录只能是目录名称，如 Default 或 Profile 1')
    proxy = profile.get('proxy')
    if proxy:
        parsed = urlsplit(proxy)
        if parsed.scheme not in {'http', 'https', 'socks4', 'socks5'} or not parsed.hostname or not parsed.port:
            raise ValueError('代理格式应为 http://host:port 或 socks5://host:port')
        if parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            raise ValueError('代理仅支持协议、主机和端口；不支持在地址中填写账号密码')
    args = profile.get('args', [])
    if not isinstance(args, list) or not all(isinstance(v, str) and v.startswith('--') and '\x00' not in v for v in args):
        raise ValueError('每行参数必须以 -- 开头')
    reserved = {'--user-data-dir', '--profile-directory', '--proxy-server', '--proxy-bypass-list'}
    if any(v.split('=', 1)[0] in reserved for v in args):
        raise ValueError('目录和代理请使用专用字段设置')
    urls = profile.get('urls', [])
    if not isinstance(urls, list) or not all(isinstance(v, str) and '\x00' not in v and urlsplit(v).scheme in {'http', 'https'} and urlsplit(v).netloc for v in urls):
        raise ValueError('每行网页必须是有效的 http:// 或 https:// 地址')
    return profile


class App:
    def __init__(self, path, auth_directory=None):
        self.path = path
        self.auth = AuthStore(auth_directory or Path.home() / ".codex")
        self.lock = threading.RLock()
        self.token = secrets.token_urlsafe(32)
        self.events = deque(maxlen=50)
        self.children = []
        self.usage_cache = {}

    def auth_snapshot(self):
        return {**self.auth.snapshot(), 'token': self.token}

    def auth_action(self, action, body):
        if action == 'remove-active':
            return self.auth.remove_active()
        if action == 'backup':
            return self.auth.backup(body.get('name'))
        if action == 'activate':
            return self.auth.activate(body.get('name'))
        if action == 'restore':
            return self.auth.restore()
        if action == 'rename':
            return self.auth.rename(body.get('name'), body.get('new_name'))
        if action == 'delete':
            return self.auth.delete(body.get('name'))
        raise ValueError('不支持的操作')

    def auth_usage(self, name=None, force=False):
        """Read Codex account limits through the local Codex app-server protocol."""
        credential = self.auth.read_profile(name)
        fingerprint = hashlib.sha256(credential.get('tokens', {}).get('access_token', '').encode()).hexdigest()
        key = (name or '__active__', fingerprint)
        cached = self.usage_cache.get(key)
        now = time.monotonic()
        if not force and cached and now - cached['fetched_at'] < 30:
            return copy.deepcopy(cached['data'])
        executable = os.environ.get('CODEX_BIN', 'codex')
        try:
            process = subprocess.Popen([executable, 'app-server', '--stdio'], stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                       cwd=str(ROOT), start_new_session=True)
        except OSError as exc:
            raise ValueError('找不到 Codex CLI，请确保 codex 命令可用') from exc
        try:
            access_token = credential.get('tokens', {}).get('access_token')
            account_id = credential.get('tokens', {}).get('account_id')
            if not access_token or not account_id:
                raise ValueError('该认证档案不包含 Codex 登录令牌')
            messages = queue.Queue()
            def collect_output():
                for line in process.stdout:
                    try:
                        messages.put(json.loads(line))
                    except ValueError:
                        continue
            reader = threading.Thread(target=collect_output, daemon=True)
            reader.start()
            def send(message):
                process.stdin.write(json.dumps(message, separators=(',', ':')) + '\n')
                process.stdin.flush()
            def wait_response(request_id):
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    try:
                        message = messages.get(timeout=max(.01, deadline - time.monotonic()))
                    except queue.Empty:
                        break
                    if isinstance(message, dict) and message.get('id') == request_id:
                        return message
                raise ValueError('Codex App Server 响应超时')
            send({'id': 1, 'method': 'initialize', 'params': {
                'clientInfo': {'name': 'local-profile-switcher', 'version': '0.1.0'},
                'capabilities': {'experimentalApi': True}}})
            initialized = wait_response(1)
            if initialized.get('error'):
                raise ValueError('Codex App Server 初始化失败')
            send({'method': 'initialized', 'params': {}})
            send({'id': 2, 'method': 'account/login/start', 'params': {
                'accessToken': access_token, 'chatgptAccountId': account_id,
                'chatgptPlanType': credential.get('tokens', {}).get('plan_type'),
                'type': 'chatgptAuthTokens'}})
            login = wait_response(2)
            if login.get('error'):
                error = login['error']
                raise ValueError(error.get('message', 'Codex 登录初始化失败') if isinstance(error, dict) else 'Codex 登录初始化失败')
            send({'id': 3, 'method': 'account/rateLimits/read', 'params': {'excludeResetCreditDetails': False}})
            usage = wait_response(3)
            if usage.get('error'):
                error = usage['error']
                raise ValueError(error.get('message', 'Codex 用量查询失败') if isinstance(error, dict) else 'Codex 用量查询失败')
            result = usage.get('result')
            if not isinstance(result, dict):
                raise ValueError('Codex App Server 未返回用量数据')
            data = {'rateLimits': result.get('rateLimits'),
                    'rateLimitsByLimitId': result.get('rateLimitsByLimitId'),
                    'ordinaryUsageAllowed': result.get('ordinaryUsageAllowed'),
                    'rateLimitResetCredits': result.get('rateLimitResetCredits'),
                    'planType': credential.get('tokens', {}).get('plan_type'),
                    'fetchedAt': datetime.now(timezone.utc).isoformat()}
            by_limit = data.get('rateLimitsByLimitId')
            if isinstance(by_limit, dict) and isinstance(by_limit.get('codex'), dict):
                data['rateLimits'] = by_limit['codex']
            self.usage_cache[key] = {'fetched_at': time.monotonic(), 'data': data}
            return copy.deepcopy(data)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=2)

    def snapshot(self):
        config = read_config(self.path)
        metadata = config['profile_meta']
        try:
            binary, error = chrome_binary(config), None
        except ValueError as exc:
            binary, error = None, str(exc)
        for child in self.children[:]:
            if child.poll() is not None:
                self.children.remove(child)
        return {'profiles': config['profiles'], 'profile_meta': metadata, 'chrome_binary': binary, 'chrome_error': error,
                'events': list(self.events), 'token': self.token}

    def action(self, action, body):
        config = read_config(self.path)
        name = body.get('name')
        if action == 'save':
            profile = validate_profile(name, body.get('profile'))
            original = body.get('original')
            save_profile(self.path, name, profile, original)
            return {'message': f'已保存 {name}'}
        if not isinstance(name, str) or name not in config['profiles']:
            raise ValueError('找不到此配置，请刷新页面')
        if action == 'duplicate':
            validate_profile(name, config['profiles'][name])
            base = name[:59] + '-copy' if len(name) > 59 else name + '-copy'
            candidate = base
            suffix = 2
            while candidate in config['profiles']:
                ending = f'-{suffix}'
                candidate = base[:64 - len(ending)] + ending
                suffix += 1
            save_profile(self.path, candidate, config['profiles'][name])
            return {'message': f'已复制为 {candidate}', 'name': candidate}
        if action == 'delete':
            delete_profile(self.path, name)
            return {'message': f'已移除 {name}，浏览器数据保留'}
        if action in {'preview', 'launch'}:
            validate_profile(name, config['profiles'][name])
            command = build_command(config, name, [], [])
            if action == 'preview':
                return {'command': command}
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, start_new_session=True)
            self.children.append(process)
            event = {'name': name, 'time': datetime.now(timezone.utc).isoformat(), 'pid': process.pid}
            self.events.appendleft(event)
            touch_profile(self.path, name)
            return {'message': f'已发送 {name} 的启动请求', 'event': event}
        raise ValueError('不支持的操作')


def make_server(path, port=8767, auth_directory=None):
    app = App(path, auth_directory)

    class Handler(BaseHTTPRequestHandler):
        def respond(self, status, data, mime='application/json; charset=utf-8'):
            content = json.dumps(data, ensure_ascii=False).encode() if mime.startswith('application/json') else data
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(content)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(content)

        def allowed(self):
            hosts = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
            if self.headers.get('Host') not in hosts:
                self.respond(403, {'error': '不允许的 Host'})
                return False
            origin = self.headers.get('Origin')
            if origin and origin != 'http://' + self.headers['Host']:
                self.respond(403, {'error': '不允许跨站请求'})
                return False
            return True

        def do_GET(self):
            if not self.allowed():
                return
            route = urlsplit(self.path).path
            try:
                if route == '/api/auth/state':
                    with app.lock:
                        self.respond(200, app.auth_snapshot())
                elif route == '/api/auth/usage':
                    query = parse_qs(urlsplit(self.path).query)
                    profile = query.get('profile', [None])[0]
                    self.respond(200, app.auth_usage(profile, force='refresh' in query))
                elif route == '/api/state':
                    with app.lock:
                        self.respond(200, app.snapshot())
                elif route in {'/', '/app.js', '/style.css', '/auth', '/auth.js'}:
                    filename, mime = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8'), '/auth': ('auth.html', 'text/html; charset=utf-8'), '/auth.js': ('auth.js', 'text/javascript; charset=utf-8')}[route]
                    content = render_page(filename, 'auth' if route == '/auth' else 'chrome') if route in {'/', '/auth'} else (ROOT / 'public' / filename).read_bytes()
                    self.respond(200, content, mime)
                else:
                    self.respond(404, {'error': '页面不存在'})
            except (OSError, ValueError) as exc:
                self.respond(400, {'error': str(exc)})

        def do_POST(self):
            if not self.allowed():
                return
            if not secrets.compare_digest(self.headers.get('X-Local-Token', ''), app.token):
                self.respond(403, {'error': '请刷新页面后重试'})
                return
            try:
                length = int(self.headers.get('Content-Length', 0))
                if not 0 < length <= 65536:
                    raise ValueError('请求大小需介于 1–65536 字节')
                if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                    raise ValueError('需要 JSON 请求')
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError('请求格式错误')
                route = urlsplit(self.path).path
                if route not in {'/api/save', '/api/delete', '/api/preview', '/api/launch', '/api/duplicate', '/api/auth/backup', '/api/auth/activate', '/api/auth/restore', '/api/auth/rename', '/api/auth/delete', '/api/auth/remove-active'}:
                    self.respond(404, {'error': '接口不存在'})
                    return
                with app.lock:
                    if route.startswith('/api/auth/'):
                        self.respond(200, app.auth_action(route.removeprefix('/api/auth/'), body))
                    else:
                        self.respond(200, app.action(route.removeprefix('/api/'), body))
            except (OSError, ValueError, TypeError) as exc:
                self.respond(400, {'error': str(exc)})

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.app = app
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8767)
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    parser.add_argument('--auth-directory', type=Path, default=Path.home() / '.codex')
    args = parser.parse_args()
    path = args.config.expanduser().resolve()
    with connect(path) as connection:
        legacy = path.with_name('profiles.json')
        import_json(connection, legacy if legacy.exists() else ROOT / 'profiles.example.json')
    server = make_server(path, args.port, args.auth_directory)
    print(f'Local Profile Switcher: http://127.0.0.1:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
