import unittest

from dsh_retrieval.chat_ui import CHAT_CSS, CHAT_HTML, CHAT_JS


class ChatUiTest(unittest.TestCase):
    def test_page_has_explicit_confirmation_and_local_limit_copy(self):
        self.assertIn("生成确认卡", CHAT_HTML)
        self.assertIn("不会启动真实检索", CHAT_HTML)
        self.assertIn("确认提单", CHAT_JS)
        self.assertIn("确认新版本", CHAT_JS)
        self.assertIn("尚未编译为真实清洗规则", CHAT_JS)
        self.assertIn("initialize", CHAT_JS)
        self.assertIn("pending_draft", CHAT_JS)
        self.assertIn("new-task", CHAT_HTML)
        self.assertIn("localStorage.removeItem", CHAT_JS)
        self.assertIn("历史记录", CHAT_HTML)
        self.assertIn("草稿会话与正式版本分层保存", CHAT_HTML)
        self.assertIn("refreshHistory", CHAT_JS)
        self.assertIn("openTask", CHAT_JS)
        self.assertIn("openConversation", CHAT_JS)
        self.assertIn("不是业务执行时间线", CHAT_JS)
        self.assertIn("五节点进度", CHAT_JS)
        self.assertIn("progress.counts.accepted===null?'未知'", CHAT_JS)
        self.assertIn("结果尚未发布", CHAT_JS)
        self.assertIn("权威结果统计", CHAT_JS)
        self.assertIn("preview_status==='unavailable'", CHAT_JS)
        self.assertIn("loadMoreResults", CHAT_JS)
        self.assertIn("主标签 ·", CHAT_JS)
        self.assertIn("辅助检索词", CHAT_JS)
        self.assertIn("独立打包", CHAT_JS)
        self.assertIn("同一素材聚合总数只计一次", CHAT_JS)
        self.assertNotIn("完成百分比", CHAT_JS)

    def test_assets_do_not_embed_remote_dependencies_or_dynamic_html(self):
        combined = CHAT_HTML + CHAT_CSS + CHAT_JS
        self.assertNotIn("https://", combined)
        self.assertNotIn("http://", combined)
        self.assertNotIn("innerHTML", CHAT_JS)


if __name__ == "__main__":
    unittest.main()
