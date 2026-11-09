import unittest
from dsh_retrieval.media_cache import ByteCache

class MediaCacheTest(unittest.TestCase):
    def test_lru_bounds_and_failed_loads(self):
        cache=ByteCache(max_bytes=5,max_items=2)
        self.assertEqual(cache.get_or_create('a',lambda:b'aa'),b'aa')
        cache.get_or_create('b',lambda:b'bb')
        cache.get_or_create('a',lambda: self.fail('hit must not read'))
        cache.get_or_create('c',lambda:b'ccc')
        self.assertEqual(list(cache.items),['a','c']);self.assertEqual(cache.bytes,5)
        cache.get_or_create('huge',lambda:b'123456')
        self.assertNotIn('huge',cache.items)
        def fail():raise ValueError('invalid source')
        with self.assertRaises(ValueError):cache.get_or_create('bad',fail)
        self.assertNotIn('bad',cache.items)
