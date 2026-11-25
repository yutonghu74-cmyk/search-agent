from contextlib import contextmanager
from io import BytesIO
import unittest

from dsh_retrieval.video_media import (VideoResourcePolicy, open_verified_video, inspect_video_file,
                                     byte_range)
from dsh_retrieval.multi_rule_contracts import bytes_digest
from dsh_retrieval.errors import ServiceError


class VideoMediaTest(unittest.TestCase):
    def test_chunked_video_read_cleanup_digest_and_inspection(self):
        body=b'x'*(11*1024**2)
        class Stream(BytesIO):
            def read(self,size=-1):
                if size<0 or size>65536: raise AssertionError('unbounded read')
                return super().read(size)
        class Gateway:
            @contextmanager
            def open_read(self,*args,**kwargs):
                with Stream(body) as stream: yield stream
        policy=VideoResourcePolicy(max_video_bytes=len(body)+1)
        with open_verified_video(Gateway(),None,'opaque',bytes_digest(body),policy=policy) as file:
            path=file.path;self.assertEqual(file.size,len(body));self.assertTrue(path.exists())
            metadata=inspect_video_file(file,file_inspector=lambda p:{'media_type':'video','container':'webm','duration_ms':1200},policy=policy)
            self.assertEqual(metadata['container'],'webm')
            with self.assertRaises(ServiceError):
                inspect_video_file(file,inspector=lambda content:self.fail('large file materialized as bytes'))
        self.assertFalse(path.exists())

    def test_oversize_mutation_and_duration_limits(self):
        class Gateway:
            @contextmanager
            def open_read(self,*args,**kwargs): yield BytesIO(b'video')
        with self.assertRaises(ServiceError) as error:
            with open_verified_video(Gateway(),None,'opaque',bytes_digest(b'video'),policy=VideoResourcePolicy(max_video_bytes=4)):pass
        self.assertEqual(error.exception.code,'MEDIA_LIMIT_EXCEEDED')
        with self.assertRaises(ServiceError):
            with open_verified_video(Gateway(),None,'opaque',bytes_digest(b'video')) as file:
                file.path.write_bytes(b'changed')
        with open_verified_video(Gateway(),None,'opaque',bytes_digest(b'video')) as file:
            with self.assertRaises(ServiceError) as error:
                inspect_video_file(file,file_inspector=lambda p:{'media_type':'video','container':'mp4','duration_ms':2000},
                    policy=VideoResourcePolicy(max_duration_ms=1000))
            self.assertEqual(error.exception.code,'VIDEO_RESOURCE_LIMIT')

    def test_single_ranges_and_invalid_ranges(self):
        self.assertEqual(byte_range(None,10),(0,9,False))
        self.assertEqual(byte_range('bytes=2-5',10),(2,5,True))
        self.assertEqual(byte_range('bytes=-3',10),(7,9,True))
        self.assertEqual(byte_range('bytes=8-',10),(8,9,True))
        for value in ('bytes=2-1','bytes=10-','bytes=-0','bytes=0-2,4-5','bytes=-','items=0-1','bytes='+'9'*200+'-'):
            with self.subTest(value=value),self.assertRaises(ServiceError):byte_range(value,10)
        with self.assertRaises(ValueError):VideoResourcePolicy(max_video_bytes=True)
