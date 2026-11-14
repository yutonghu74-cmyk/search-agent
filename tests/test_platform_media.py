from io import BytesIO
import unittest

from dsh_retrieval.errors import ServiceError
from dsh_retrieval.platform_media import PlatformMediaReader


class Reply(BytesIO):
    status = 200
    headers = {}


class MediaReaderTest(unittest.TestCase):
    def setUp(self):
        self.url = 'https://objects.invalid/image?signature=private'
        self.calls = []
        self.data = b'picture'
        self.headers = {}
        def opener(request, timeout):
            self.calls.append(request)
            result = Reply(self.data)
            result.headers = self.headers
            return result
        self.reader = PlatformMediaReader(lambda *args: self.url,
            allowed_origins={'https://objects.invalid'}, opener=opener)

    def test_temp_url_read_without_forwarding_credentials(self):
        with self.reader(None, '101', 'opaque', 100) as stream:
            self.assertEqual(stream.read(), self.data)
        self.assertIsNone(self.calls[0].get_header('Authorization'))
        self.assertIsNone(self.calls[0].get_header('Cookie'))

    def test_foreign_host_userinfo_and_non_https_rejected(self):
        for url in ('https://evil.invalid/file', 'http://objects.invalid/file',
                    'https://secret@objects.invalid/file', 'https://objects.invalid/file#secret'):
            self.url = url
            with self.assertRaises(ServiceError):
                with self.reader(None, '101', 'opaque', 100):
                    self.fail('untrusted media URL accepted')
        self.assertEqual(self.calls, [])

    def test_actual_and_declared_size_limits(self):
        with self.assertRaises(ServiceError) as caught:
            with self.reader(None, '101', 'opaque', 3) as stream:
                stream.read()
        self.assertEqual(caught.exception.status, 413)
        self.headers = {'Content-Length': '999'}
        with self.assertRaises(ServiceError):
            with self.reader(None, '101', 'opaque', 100):
                self.fail('oversize declaration accepted')

    def test_upstream_error_does_not_expose_signed_url(self):
        def fail(*args, **kwargs):
            raise OSError('https://objects.invalid/?signature=private')
        self.reader.opener = fail
        with self.assertRaises(ServiceError) as caught:
            with self.reader(None, '101', 'opaque', 100):
                pass
        self.assertNotIn('signature', str(caught.exception))
