import json
import io
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch, Mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from server import make_server
from storage import read_config


class WebTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'profiles.db'
        self.auth_dir = Path(self.tmp.name) / "codex"
        self.auth_dir.mkdir()
        self.server = make_server(self.path, 0, self.auth_dir)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.profile = {'user_data_dir': '~/Chrome Test', 'profile_directory': 'Default',
                        'proxy': 'socks5://127.0.0.1:1080', 'args': ['--incognito'], 'urls': ['https://example.com']}

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def request(self, route, body=None, headers=None):
        req_headers = {'Content-Type': 'application/json', 'X-Local-Token': self.server.app.token}
        req_headers.update(headers or {})
        req = Request(self.base + route, data=json.dumps(body).encode() if body is not None else None, headers=req_headers)
        with urlopen(req, timeout=3) as response:
            return json.load(response)

    def test_create_edit_delete_and_persistence(self):
        self.request('/api/save', {'name': 'test', 'profile': self.profile})
        self.assertEqual(self.request('/api/state')['profiles']['test'], self.profile)
        created = self.request('/api/state')['profile_meta']['test']['created_at']
        self.assertEqual(read_config(self.path)['profiles']['test'], self.profile)
        with self.assertRaises(HTTPError) as error:
            self.request('/api/save', {'name': 'test', 'profile': self.profile})
        self.assertEqual(error.exception.code, 400)
        error.exception.close()
        self.request('/api/save', {'name': 'renamed', 'original': 'test', 'profile': self.profile})
        self.assertNotIn('test', self.request('/api/state')['profiles'])
        self.assertEqual(self.request('/api/state')['profile_meta']['renamed']['created_at'], created)
        self.request('/api/delete', {'name': 'renamed'})
        self.assertEqual(read_config(self.path)['profiles'], {})

    def test_duplicate_profile_creates_new_record(self):
        self.request('/api/save', {'name': 'test', 'profile': self.profile})
        original = self.request('/api/state')['profile_meta']['test']
        first = self.request('/api/duplicate', {'name': 'test'})
        second = self.request('/api/duplicate', {'name': 'test'})
        self.assertEqual(first['name'], 'test-copy')
        self.assertEqual(second['name'], 'test-copy-2')
        state = self.request('/api/state')
        self.assertEqual(state['profiles']['test-copy'], self.profile)
        self.assertEqual(state['profiles']['test-copy-2'], self.profile)
        self.assertEqual(state['profile_meta']['test'], original)
        self.assertNotEqual(state['profile_meta']['test-copy']['created_at'], original['created_at'])

    @patch('chrome_profiles.chrome_binary', return_value='/chrome')
    @patch('server.subprocess.Popen')
    def test_preview_does_not_launch_and_launch_uses_argument_array(self, popen, _):
        self.request('/api/save', {'name': 'test', 'profile': self.profile})
        command = self.request('/api/preview', {'name': 'test'})['command']
        popen.assert_not_called()
        self.assertIn('--proxy-server=socks5://127.0.0.1:1080', command)
        popen.return_value = Mock(pid=42)
        self.request('/api/launch', {'name': 'test'})
        self.assertEqual(popen.call_args.args[0], command)
        self.assertNotIn('shell', popen.call_args.kwargs)
        self.assertEqual(self.request('/api/state')['events'][0]['pid'], 42)
        self.assertGreaterEqual(self.request('/api/state')['profile_meta']['test']['updated_at'], self.request('/api/state')['profile_meta']['test']['created_at'])

    def test_cross_origin_host_and_missing_token_rejected(self):
        for headers in [{'Origin': 'https://evil.example'}, {'Host': 'evil.example'}, {'X-Local-Token': ''}]:
            with self.assertRaises(HTTPError) as error:
                self.request('/api/delete', {'name': 'test'}, headers)
            self.assertEqual(error.exception.code, 403)
            error.exception.close()

    def test_invalid_profiles_never_written(self):
        for changes in [{'proxy':'http://user:pass@host:8080'}, {'args':['--user-data-dir=/tmp/other']},
                        {'urls':['javascript:alert(1)']}, {'profile_directory':'../Default'}, {'user_data_dir':''}]:
            with self.assertRaises(HTTPError) as error:
                self.request('/api/save', {'name': 'test', 'profile': self.profile | changes})
            self.assertEqual(error.exception.code, 400)
            error.exception.close()
        self.assertEqual(read_config(self.path)['profiles'], {})

    def test_auth_api_and_pages(self):
        (self.auth_dir / 'auth.json').write_text('{"tokens":{"access_token":"one"}}')
        state = self.request('/api/auth/state')
        self.assertTrue(state['hasActive'])
        self.request('/api/auth/backup', {'name': 'first'})
        before = self.request('/api/auth/state')['profiles'][0]
        (self.auth_dir / 'auth.json').write_text('{"tokens":{"access_token":"two"}}')
        self.request('/api/auth/activate', {'name': 'first'})
        self.assertEqual(json.loads((self.auth_dir / 'auth.json').read_text())['tokens']['access_token'], 'one')
        after = self.request('/api/auth/state')['profiles'][0]
        self.assertGreaterEqual(after['updated_at'], before['updated_at'])
        self.request('/api/auth/restore', {})
        self.assertEqual(json.loads((self.auth_dir / 'auth.json').read_text())['tokens']['access_token'], 'two')
        self.request('/api/auth/rename', {'name': 'first', 'new_name': 'second'})
        self.request('/api/auth/delete', {'name': 'second'})
        self.assertEqual(self.request('/api/auth/state')['profiles'], [])
        with urlopen(self.base + '/auth') as response:
            self.assertIn(b'Local Profile Switcher', response.read())

    @patch('server.subprocess.Popen')
    def test_auth_usage_uses_app_server_and_caches_snapshot(self, popen):
        token = 'secret-token'
        (self.auth_dir / 'auth.json').write_text(json.dumps({'tokens': {'access_token': token, 'account_id': 'account-1'}}))
        process = Mock()
        process.poll.return_value = 0
        process.stdin = io.StringIO()
        process.stdout = io.StringIO('\n'.join([
            json.dumps({'id': 1, 'result': {}}),
            json.dumps({'id': 2, 'result': {}}),
            json.dumps({'id': 3, 'result': {'rateLimits': {'primary': {'usedPercent': 23, 'windowDurationMins': 300}},
                                               'ordinaryUsageAllowed': True,
                                               'rateLimitResetCredits': {'availableCount': 0, 'credits': None}}}),
        ]) + '\n')
        popen.return_value = process
        first = self.server.app.auth_usage(force=True)
        self.assertEqual(first['rateLimits']['primary']['usedPercent'], 23)
        self.assertNotIn(token, json.dumps(first))
        self.server.app.auth_usage()
        popen.assert_called_once()
        payload = process.stdin.getvalue()
        messages = [json.loads(line) for line in payload.splitlines()]
        login = next(message for message in messages if message.get('method') == 'account/login/start')
        self.assertEqual(login['params']['accessToken'], token)
        self.assertEqual(login['params']['type'], 'chatgptAuthTokens')
        self.assertTrue(next(message for message in messages if message.get('method') == 'initialize')['params']['capabilities']['experimentalApi'])

    def test_shared_header_and_footer(self):
        with urlopen(self.base) as response:
            chrome = response.read().decode()
        with urlopen(self.base + '/auth') as response:
            auth = response.read().decode()
        for page in (chrome, auth):
            self.assertEqual(page.count('<header class="topbar">'), 1)
            self.assertEqual(page.count('<footer class="site-footer">'), 1)
            self.assertIn('数据保存在本机 · 仅本机访问', page)
        self.assertIn('data-nav="chrome" aria-current="page"', chrome)
        self.assertIn('data-nav="auth" aria-current="page"', auth)

    def test_static_page_and_no_directory_traversal(self):
        with urlopen(self.base) as response:
            self.assertIn(b'Local Profile Switcher', response.read())
            self.assertIn("frame-ancestors 'none'", response.headers['Content-Security-Policy'])
        with self.assertRaises(HTTPError) as error:
            urlopen(self.base + '/profiles.json')
        self.assertEqual(error.exception.code, 404)
        error.exception.close()


if __name__ == '__main__':
    unittest.main()
