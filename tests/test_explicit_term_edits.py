import unittest

from dsh_retrieval.explicit_term_edits import explicit_term_list


class ExplicitTermEditsTest(unittest.TestCase):
    def test_full_quoted_list_includes_primary_and_replaces_auxiliary_terms(self):
        groups = [{'primary_label': '小孩抱狗', 'auxiliary_terms': ['旧词一', '旧词二']}]
        self.assertEqual(explicit_term_list(
            '检索词减少到两个，保留“小孩抱狗”和“小女孩在草地上抱着狗”，其他需求和1张目标不变。',
            groups, target_count=1),
            [{'primary_label': '小孩抱狗', 'auxiliary_terms': ['小女孩在草地上抱着狗']}])
        self.assertIsNone(explicit_term_list(
            '检索词减少到两个，保留“小孩抱狗”和“小女孩在草地上抱着狗”，其他需求和2张目标不变。',
            groups, target_count=1))
        self.assertEqual(explicit_term_list('检索词减少到一个，保留“小孩抱狗”', groups),
                         [{'primary_label': '小孩抱狗', 'auxiliary_terms': []}])

    def test_ambiguous_or_conflicting_edits_fall_back(self):
        groups = [{'primary_label': '小孩抱狗', 'auxiliary_terms': ['旧词一', '旧词二']}]
        for text in ('检索词减少到两个，保留“小孩抱狗”和“小女孩在草地上抱着狗”，再加黑狗',
                     '检索词减少到三个，保留“小孩抱狗”和“小女孩在草地上抱着狗”',
                     '检索词减少到两个，保留“猫”和“小女孩在草地上抱着狗”',
                     '检索词减少到两个，保留“小孩抱狗”和“小孩抱狗”'):
            self.assertIsNone(explicit_term_list(text, groups), text)

    def test_named_auxiliary_replacement_preserves_other_terms(self):
        groups = [{'primary_label': '小孩抱狗', 'auxiliary_terms': ['旧词一', '旧词二']}]
        self.assertEqual(explicit_term_list('把“旧词二”替换成“小女孩在草地上抱着狗”', groups),
                         [{'primary_label': '小孩抱狗',
                           'auxiliary_terms': ['旧词一', '小女孩在草地上抱着狗']}])
        self.assertIsNone(explicit_term_list('把“没有的词”替换成“新词”', groups))
        self.assertIsNone(explicit_term_list('把“旧词二”替换成“旧词一”', groups))
