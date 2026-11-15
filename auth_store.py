"""Local Codex auth profile operations. Credential data stays in ~/.codex files."""
import base64
import binascii
from contextlib import contextmanager
from datetime import date, datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import secrets
import stat
import tempfile

PREFIX = 'auth.json.'
ACTIVE = 'auth.json'
PREVIOUS = '.codex-auth-switcher-previous'
LOCK = '.codex-auth-switcher-lock'


class AuthStore:
    def __init__(self, directory):
        self.directory = Path(directory).expanduser().resolve()

    def _name(self, name):
        if not isinstance(name, str) or not name or name != name.strip() or len(name) > 200 or name == 'lock' or '/' in name or '\\' in name or any(ord(c) < 32 for c in name):
            raise ValueError('档案名称无效')
        return PREFIX + name

    @contextmanager
    def _lock(self):
        fd = os.open(self.directory / LOCK, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _read(self, name):
        try:
            fd = os.open(self.directory / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return None
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError('认证文件不是普通文件')
            with os.fdopen(fd, 'rb', closefd=False) as file:
                data = file.read(16 * 1024 * 1024 + 1)
            if len(data) > 16 * 1024 * 1024:
                raise ValueError('认证文件过大')
            return data
        finally:
            os.close(fd)

    @staticmethod
    def _valid(data):
        try:
            value = json.loads(data)
        except (ValueError, TypeError):
            return False
        return isinstance(value, dict) and bool(value)

    def _stage(self, data):
        fd, path = tempfile.mkstemp(prefix='.codex-auth-switcher-', dir=self.directory)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, 'wb') as file:
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
            return Path(path)
        except BaseException:
            os.close(fd)
            Path(path).unlink(missing_ok=True)
            raise

    def _replace_active(self, data):
        active = self._read(ACTIVE)
        if active == data:
            return
        staged = self._stage(data)
        previous = self._stage(active) if active is not None else None
        try:
            if self._read(ACTIVE) != active:
                raise ValueError('当前认证已被其他程序更新，请重试')
            if previous:
                os.replace(previous, self.directory / PREVIOUS)
            os.replace(staged, self.directory / ACTIVE)
        finally:
            staged.unlink(missing_ok=True)
            if previous:
                previous.unlink(missing_ok=True)

    def snapshot(self):
        if not self.directory.is_dir():
            return {'profiles': [], 'hasActive': False, 'canRestore': False, 'suggestions': []}
        active = self._read(ACTIVE)
        profiles = []
        for path in self.directory.iterdir():
            if not path.name.startswith(PREFIX) or path.name == PREFIX + 'lock':
                continue
            try:
                data = self._read(path.name)
                if not self._valid(data):
                    continue
                info = path.stat(follow_symlinks=False)
                profiles.append({'name': path.name[len(PREFIX):], 'active': data == active,
                                 'created_at': datetime.fromtimestamp(getattr(info, 'st_birthtime', info.st_ctime), timezone.utc).isoformat(),
                                 'updated_at': datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat()})
            except (OSError, ValueError):
                continue
        profiles.sort(key=lambda profile: profile['name'].casefold())
        return {'profiles': profiles, 'hasActive': active is not None,
                'canRestore': self._valid(self._read(PREVIOUS)),
                'suggestions': self.suggested_names() if self._valid(active) else []}

    def suggested_names(self):
        active = self._read(ACTIVE)
        email = None
        try:
            token = json.loads(active)['tokens']['id_token']
            payload = token.split('.')[1]
            claims = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
            candidate = claims.get('email')
            if isinstance(candidate, str) and candidate.strip():
                self._name(candidate.strip())
                email = candidate.strip()
        except (ValueError, TypeError, KeyError, IndexError, binascii.Error):
            pass
        bases = [email, f'{email}-{date.today()}'] if email else [f'账号-{date.today()}', f'认证备份-{date.today()}']
        existing = {p.name for p in self.directory.iterdir()}
        suggestions = []
        for base in bases:
            candidate = base
            suffix = 2
            while PREFIX + candidate in existing:
                candidate = f'{base}-{suffix}'
                suffix += 1
            suggestions.append(candidate)
        return suggestions

    def read_profile(self, name=None):
        """Return a credential document for local read-only integrations."""
        filename = ACTIVE if name is None else self._name(name)
        data = self._read(filename)
        if not self._valid(data):
            raise ValueError('当前认证不存在或无效' if name is None else '档案不存在或认证无效')
        try:
            value = json.loads(data)
        except (ValueError, TypeError) as exc:
            raise ValueError('认证文件格式无效') from exc
        return value

    def backup(self, name):
        filename = self._name(name)
        with self._lock():
            data = self._read(ACTIVE)
            if not self._valid(data):
                raise ValueError('当前没有有效认证，请先登录')
            staged = self._stage(data)
            try:
                if self._read(ACTIVE) != data:
                    raise ValueError('当前认证已被其他程序更新，请重试')
                os.link(staged, self.directory / filename, follow_symlinks=False)
            finally:
                staged.unlink(missing_ok=True)
        return {'message': '已备份当前认证。'}

    def activate(self, name):
        filename = self._name(name)
        with self._lock():
            data = self._read(filename)
            if not self._valid(data):
                raise ValueError('档案不存在或认证无效')
            self._replace_active(data)
            os.utime(self.directory / filename, None, follow_symlinks=False)
        return {'message': f'已切换到「{name}」。'}

    def remove_active(self):
        with self._lock():
            active = self._read(ACTIVE)
            if active is None:
                raise ValueError('当前没有 auth.json')
            os.replace(self.directory / ACTIVE, self.directory / PREVIOUS)
        return {'message': '已移除当前认证。请重新打开 Codex 并登录新账号。'}

    def restore(self):
        with self._lock():
            data = self._read(PREVIOUS)
            if not self._valid(data):
                raise ValueError('没有可恢复的上一次认证')
            self._replace_active(data)
        return {'message': '已恢复上一次认证。'}

    def rename(self, name, new_name):
        old = self._name(name)
        new = self._name(new_name)
        if old == new:
            return {'message': '档案名称未改变。'}
        with self._lock():
            if not self._valid(self._read(old)):
                raise ValueError('档案不存在或认证无效')
            os.link(self.directory / old, self.directory / new, follow_symlinks=False)
            try:
                os.unlink(self.directory / old)
            except BaseException:
                os.unlink(self.directory / new)
                raise
        return {'message': '已重命名档案。'}

    def delete(self, name):
        filename = self._name(name)
        with self._lock():
            if not self._valid(self._read(filename)):
                raise ValueError('档案不存在或认证无效')
            os.unlink(self.directory / filename)
        return {'message': f'已删除档案「{name}」。'}
