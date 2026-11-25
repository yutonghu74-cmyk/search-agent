from io import BytesIO
import unittest
from unittest.mock import patch

from PIL import Image
from dsh_retrieval.delivery import validate_delivery_image
from dsh_retrieval.errors import ServiceError
from image_fixtures import image_bytes
import test_delivery as delivery_fixture
from zipfile import ZipFile


class DeliveryImageValidationTest(unittest.TestCase):
    def test_real_jpeg_and_png_preserve_exported_bytes(self):
        for format, extension in [('JPEG', '.jpg'), ('PNG', '.png')]:
            with self.subTest(format=format), patch('test_delivery.IMAGE', image_bytes(format)):
                fixture = delivery_fixture.DeliveryTest()
                try:
                    fixture.setUp()
                    package, _ = fixture.prepare()
                    fixture.publish(package)
                    self.assertEqual(package['manifest']['members'][0]['file'], 'images/0001' + extension)
                    content = fixture.service.download(fixture.actor, fixture.task_id, package['package_id'])
                    with ZipFile(BytesIO(content)) as archive:
                        exported = archive.read('images/0001' + extension)
                    self.assertEqual(exported, image_bytes(format))
                    with Image.open(BytesIO(exported)) as image:
                        image.load()
                        self.assertEqual(image.size, (8, 8))
                finally:
                    fixture.doCleanups()

    def test_corrupt_images_never_stage_or_write_delivery_events(self):
        for content in [b'\xff\xd8\xfffake', b'\x89PNG\r\n\x1a\nfake',
                        image_bytes()[:-20], image_bytes('PNG')[:-12]]:
            with self.subTest(size=len(content)), patch('test_delivery.IMAGE', content):
                fixture = delivery_fixture.DeliveryTest()
                try:
                    fixture.setUp()
                    before = fixture.repo.tasks.get(fixture.actor, fixture.task_id).task_version
                    with self.assertRaises(ServiceError) as caught:
                        fixture.prepare()
                    self.assertEqual(caught.exception.code, 'MEDIA_READ_INVALID')
                    self.assertFalse(fixture.service.status(fixture.actor, fixture.task_id)['available'])
                    self.assertEqual(fixture.repo.tasks.get(fixture.actor, fixture.task_id).task_version, before)
                    from contextlib import closing
                    with closing(fixture.repo.tasks._connect()) as db:
                        self.assertEqual(db.execute('SELECT COUNT(*) FROM image_delivery_events').fetchone()[0], 0)
                finally:
                    fixture.doCleanups()

    def test_pixel_limit_precedes_decode(self):
        with patch('dsh_retrieval.delivery.MAX_IMAGE_PIXELS', 63):
            with self.assertRaises(ServiceError) as caught:
                validate_delivery_image(image_bytes())
        self.assertEqual(caught.exception.code, 'MEDIA_LIMIT_EXCEEDED')

    def test_animated_png_is_rejected(self):
        output = BytesIO()
        Image.new('RGB', (8, 8), 'red').save(output, format='PNG', save_all=True,
            append_images=[Image.new('RGB', (8, 8), 'blue')], duration=100, loop=0)
        with self.assertRaises(ServiceError) as caught:
            validate_delivery_image(output.getvalue())
        self.assertEqual(caught.exception.code, 'FEATURE_NOT_SUPPORTED')

    def test_missing_decoder_fails_closed(self):
        data = image_bytes()
        with patch.dict('sys.modules', {'PIL': None}):
            with self.assertRaises(ServiceError) as caught:
                validate_delivery_image(data)
        self.assertEqual(caught.exception.code, 'DEPENDENCY_UNAVAILABLE')

    def test_decompression_bomb_is_rejected(self):
        data = image_bytes()
        with patch.object(Image, 'MAX_IMAGE_PIXELS', 10):
            with self.assertRaises(ServiceError) as caught:
                validate_delivery_image(data)
        self.assertEqual(caught.exception.code, 'MEDIA_LIMIT_EXCEEDED')
