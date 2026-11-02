"""Local, personal model connections. Not an authentication service.

Only administrator-approved exact HTTPS base URLs may receive credentials.
Secrets use OpenSSL AES-256-CBC/PBKDF2 with encrypt-then-HMAC-SHA256;
the independent master key must live outside the repository.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import hmac
import json
import base64
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import stat
import struct
import subprocess
import threading
import urllib.error
import urllib.request
from urllib.parse import urlsplit
import uuid
import zlib

from .errors import ServiceError
from .intent_model import ModelConfig, NoRedirect


def invalid(message):
    raise ServiceError('MODEL_SETTINGS_INVALID', message, 422)


def base_url(value):
    if not isinstance(value, str) or len(value) > 2048:
        invalid('API URL 无效')
    value = value.rstrip('/')
    try:
        p = urlsplit(value)
        p.port
    except ValueError:
        invalid('API URL 无效')
    if (p.scheme != 'https' or not p.hostname or p.username or p.password
            or p.query or p.fragment or any(c.isspace() for c in value)
            or '%' in value or '\\' in value or '..' in p.path
            or p.path.endswith(('/models', '/chat/completions'))):
        invalid('请填写 HTTPS Base URL，不含密钥、查询参数或 /chat/completions')
    return value


def text_field(value, limit=256):
    if (not isinstance(value, str) or not value.strip() or len(value) > limit
            or any(ord(c) < 32 for c in value)):
        invalid('名称、模型 ID 或密钥格式无效')
    return value.strip()


class SecretBox:
    def __init__(self, key_path):
        path = Path(key_path).expanduser().absolute()
        repo = Path(__file__).resolve().parents[2]
        if path.resolve().is_relative_to(repo):
            raise ValueError('Model master key must be outside the repository')
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(os.urandom(32))
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077
                    or info.st_uid != os.getuid()):
                raise ValueError('Model master key must be an owner-only regular file')
            key = stream.read(33)
        if len(key) != 32:
            raise ValueError('Invalid model master key')
        self.enc = hmac.digest(key, b'model-settings/encryption/v1', 'sha256').hex().encode()
        self.mac = hmac.digest(key, b'model-settings/authentication/v1', 'sha256')

    def _crypt(self, value, decrypt=False):
        read_fd, write_fd = os.pipe()
        try:
            os.write(write_fd, self.enc + b'\n')
            os.close(write_fd)
            write_fd = None
            result = subprocess.run(['/usr/bin/openssl', 'enc', '-aes-256-cbc',
                '-pbkdf2', '-iter', '100000', '-md', 'sha256', '-pass', f'fd:{read_fd}',
                '-d' if decrypt else '-e'], input=value, capture_output=True,
                pass_fds=(read_fd,), timeout=10)
            if result.returncode:
                raise ValueError('cipher failed')
            return result.stdout
        except (OSError, ValueError, subprocess.SubprocessError):
            raise ServiceError('MODEL_SECRET_UNAVAILABLE', '无法访问模型凭据；请检查密钥文件及 OpenSSL', 503) from None
        finally:
            os.close(read_fd)
            if write_fd is not None:
                os.close(write_fd)

    def seal(self, secret, associated):
        payload = b'MS1' + self._crypt(secret.encode())
        return hmac.digest(self.mac, associated.encode() + b'\0' + payload, 'sha256') + payload

    def open(self, blob, associated):
        tag, payload = blob[:32], blob[32:]
        expected = hmac.digest(self.mac, associated.encode() + b'\0' + payload, 'sha256')
        if not payload.startswith(b'MS1') or not hmac.compare_digest(tag, expected):
            raise ServiceError('MODEL_SECRET_UNAVAILABLE', '模型凭据完整性校验失败', 503)
        return self._crypt(payload[3:], True).decode()


class ModelSettings:
    def __init__(self, path, key_path, allowed_urls, *, opener=None):
        self.allowed = {base_url(url) for url in allowed_urls}
        if Path(path).exists() and Path(path).stat().st_size and not Path(key_path).expanduser().exists():
            raise ValueError('Existing settings require their original master key; restore the key backup')
        self.box = SecretBox(key_path)
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.fchmod(fd, 0o600)
        os.close(fd)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.owner = ContextVar('model_settings_owner', default=None)
        self.opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with self.db:
            self.db.execute('CREATE TABLE IF NOT EXISTS model_connections '
                '(id TEXT PRIMARY KEY, owner TEXT NOT NULL, data TEXT NOT NULL, secret BLOB NOT NULL)')
            self.db.execute('CREATE TABLE IF NOT EXISTS model_defaults '
                '(owner TEXT PRIMARY KEY, data TEXT NOT NULL)')

    @contextmanager
    def scope(self, tenant, user):
        token = self.owner.set(json.dumps([tenant, user]) if tenant and user else None)
        try:
            yield
        finally:
            self.owner.reset(token)

    def _owner(self):
        if not self.owner.get():
            raise ServiceError('UNAUTHENTICATED', '模型配置需要用户身份', 401)
        return self.owner.get()

    def _rows(self):
        with self.lock:
            return self.db.execute('SELECT * FROM model_connections WHERE owner=? ORDER BY id',
                                   (self._owner(),)).fetchall()

    def _row(self, identifier):
        if not isinstance(identifier, str):
            invalid('请选择连接')
        with self.lock:
            row = self.db.execute('SELECT * FROM model_connections WHERE id=? AND owner=?',
                                  (identifier, self._owner())).fetchone()
        if row is None:
            raise ServiceError('MODEL_CONNECTION_NOT_FOUND', '连接不存在', 404)
        return row, json.loads(row['data'])

    def _approved(self, url):
        if url not in self.allowed:
            raise ServiceError('MODEL_ENDPOINT_NOT_APPROVED', '该 API URL 未经本机管理员批准，请加入 --model-allowed-url', 422)

    def read(self):
        return {'connections': [json.loads(row['data']) for row in self._rows()],
                'defaults': self.defaults(), 'allowed_urls': sorted(self.allowed)}

    def defaults(self):
        with self.lock:
            row = self.db.execute('SELECT data FROM model_defaults WHERE owner=?', (self._owner(),)).fetchone()
        return json.loads(row[0]) if row else {}

    def register(self, body):
        if set(body) != {'name', 'provider', 'base_url', 'api_key'} or body['provider'] != 'openai-compatible':
            invalid('当前支持 OpenAI-compatible API')
        url = base_url(body['base_url'])
        self._approved(url)
        name, secret = text_field(body['name'], 80), text_field(body['api_key'], 8192)
        if not secret.isascii():
            invalid('API 密钥必须为 ASCII 字符')
        identifier = 'conn_' + uuid.uuid4().hex
        data = {'id': identifier, 'name': name, 'provider': 'openai-compatible',
                'base_url': url, 'has_key': True, 'models': [], 'profiles': [],
                'discovery': 'not_checked', 'enabled': True}
        owner = self._owner()
        encrypted = self.box.seal(secret, owner + identifier)
        with self.lock, self.db:
            if len(self._rows()) >= 50:
                invalid('最多配置 50 个连接')
            self.db.execute('INSERT INTO model_connections VALUES (?,?,?,?)',
                            (identifier, owner, json.dumps(data), encrypted))
        return data

    def _save(self, identifier, data):
        self.db.execute('UPDATE model_connections SET data=? WHERE id=? AND owner=?',
                        (json.dumps(data), identifier, self._owner()))

    def discover(self, identifier):
        row, data = self._row(identifier)
        self._approved(data['base_url'])
        secret = self.box.open(row['secret'], self._owner() + identifier)
        request = urllib.request.Request(data['base_url'] + '/models',
            headers={'Authorization': 'Bearer ' + secret, 'Accept': 'application/json'})
        try:
            with self.opener.open(request, timeout=20) as response:
                raw = response.read(1_000_001)
            if len(raw) > 1_000_000:
                raise ValueError()
            payload = json.loads(raw)
            if not isinstance(payload, dict) or not isinstance(payload.get('data'), list):
                raise ValueError()
            if payload.get('has_more') or payload.get('next') or payload.get('next_page'):
                raise ServiceError('MODEL_DISCOVERY_PAGINATION_UNSUPPORTED', '服务使用非标准分页，请手动添加模型；原目录保留', 422)
            models = sorted({text_field(item['id']) for item in payload['data']})
            if len(models) > 5000:
                raise ValueError()
        except urllib.error.HTTPError as error:
            code = 'MODEL_DISCOVERY_UNSUPPORTED' if error.code in (404, 405, 501) else 'MODEL_DISCOVERY_FAILED'
            raise ServiceError(code, f'模型目录请求失败（HTTP {error.code}）；可检查连接或手动添加模型，原目录保留', 502) from None
        except ServiceError:
            raise
        except Exception:
            raise ServiceError('MODEL_DISCOVERY_FAILED', '无法读取模型目录；请检查连接，原目录保留', 502) from None
        with self.lock, self.db:
            _, data = self._row(identifier)
            data.update(models=models, discovery='listed')
            self._save(identifier, data)
        return data

    def bind(self, identifier, body):
        if set(body) != {'model', 'kind'} or body['kind'] not in ('llm', 'vlm'):
            invalid('请选择 LLM 或 VLM 及模型 ID')
        model = text_field(body['model'])
        with self.lock, self.db:
            _, data = self._row(identifier)
            self._approved(data['base_url'])
            profile_id = 'm_' + hashlib.sha256((identifier + '\0' + body['kind'] + '\0' + model).encode()).hexdigest()[:48]
            if not any(p['id'] == profile_id for p in data['profiles']):
                if len(data['profiles']) >= 100:
                    invalid('每个连接最多绑定 100 个模型')
                data['profiles'].append({'id': profile_id, 'kind': body['kind'], 'model': model,
                    'provider': data['name'], 'capability': 'user_assigned'})
                self._save(identifier, data)
        return {'profile_id': profile_id}

    def rotate(self, identifier, body):
        if set(body) != {'api_key'}:
            invalid('只能更新密钥；更换 URL 请注册新连接')
        secret = text_field(body['api_key'], 8192)
        if not secret.isascii():
            invalid('API 密钥必须为 ASCII 字符')
        with self.lock, self.db:
            _, data = self._row(identifier)
            encrypted = self.box.seal(secret, self._owner() + identifier)
            self.db.execute('UPDATE model_connections SET secret=? WHERE id=? AND owner=?',
                            (encrypted, identifier, self._owner()))
            for profile in data['profiles']:
                profile.pop('verified_at', None)
            self._save(identifier, data)
        return {'updated': True}

    def probe(self, body, runtime):
        if set(body) != {'profile_id', 'kind'} or body['kind'] not in ('llm', 'vlm'):
            invalid('请指定测试模型和能力')
        profile = runtime.select(body['profile_id'], body['kind'])
        content = 'Return {"ok":true}.'
        expected = {'ok': True}
        if body['kind'] == 'vlm':
            def chunk(kind, value):
                return struct.pack('!I', len(value)) + kind + value + struct.pack('!I', zlib.crc32(kind + value))
            png = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', 16, 16, 8, 2, 0, 0, 0))
                + chunk(b'IDAT', zlib.compress((b'\0' + b'\xff\0\0'*16)*16)) + chunk(b'IEND', b''))
            content = [{'type': 'text', 'text': 'Identify the solid color. Return {"color":"red|green|blue|other"}.'},
                       {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + base64.b64encode(png).decode()}}]
            expected = {'color': 'red'}
        result = runtime._request(profile, 'Return only the requested JSON object.', content)
        if result != expected:
            raise ServiceError('MODEL_PROBE_FAILED', '模型未通过最小能力测试；未修改默认模型', 422)
        checked = datetime.now(timezone.utc).isoformat()
        # This is evidence for a minimal call, not a general capability guarantee.
        with self.lock, self.db:
            for row in self._rows():
                data = json.loads(row['data'])
                for item in data['profiles']:
                    if item['id'] == profile.profile_id:
                        current_key = self.box.open(row['secret'], self._owner() + row['id'])
                        if not hmac.compare_digest(current_key, profile.config.api_key):
                            raise ServiceError('MODEL_CONFIG_CHANGED', '测试期间密钥已改变，请重新测试', 409)
                        item['verified_at'] = checked
                        self._save(row['id'], data)
        return {'verified_at': checked, 'kind': body['kind'], 'profile_id': profile.profile_id}

    def catalog(self):
        return [p for row in self._rows() for p in json.loads(row['data'])['profiles']]

    def select(self, profile_id, kind):
        from .model_runtime import ModelProfile
        for row in self._rows():
            data = json.loads(row['data'])
            for profile in data['profiles']:
                if profile['id'] == profile_id and profile['kind'] == kind:
                    self._approved(data['base_url'])
                    secret = self.box.open(row['secret'], self._owner() + row['id'])
                    return ModelProfile(profile_id, kind, ModelConfig('openai-compatible',
                        profile['model'], data['base_url'], secret))
        return None

    def save_defaults(self, body, runtime):
        if set(body) != {'llm', 'vlm'}:
            invalid('请配置默认 LLM 和 VLM')
        for kind in ('llm', 'vlm'):
            if not isinstance(body[kind], str):
                invalid('默认模型 ID 无效')
            if body[kind]:
                runtime.select(body[kind], kind)
        with self.lock, self.db:
            self.db.execute('INSERT OR REPLACE INTO model_defaults VALUES (?,?)',
                            (self._owner(), json.dumps(body)))
        return {'defaults': body}

    def close(self):
        self.db.close()
