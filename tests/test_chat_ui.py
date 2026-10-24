import unittest

from dsh_retrieval.chat_ui import CHAT_CSS, CHAT_HTML, CHAT_JS


class ChatUiTest(unittest.TestCase):
    def test_page_has_explicit_confirmation_and_local_limit_copy(self):
        self.assertIn("生成确认卡", CHAT_HTML)
        self.assertIn("不会启动真实检索", CHAT_HTML)
        self.assertIn("确认提单", CHAT_JS)
        self.assertIn("确认新版本", CHAT_JS)
        self.assertIn("尚未编译为真实清洗规则", CHAT_JS)
        self.assertIn("模型清洗建议 · 未批准", CHAT_JS)
        self.assertIn("不会启动真实检索或自动清洗", CHAT_HTML)
        self.assertIn("feedback-suggestions", CHAT_JS)
        self.assertIn("initialize", CHAT_JS)
        self.assertIn("pending_draft", CHAT_JS)
        self.assertIn("new-task", CHAT_HTML)
        self.assertIn("localStorage.removeItem", CHAT_JS)
        self.assertIn("历史 Chat", CHAT_HTML)
        self.assertIn("草稿消息不会进入业务执行时间线", CHAT_JS)
        self.assertIn("refreshHistory", CHAT_JS)
        self.assertIn("openTask", CHAT_JS)
        self.assertIn("openConversation", CHAT_JS)
        self.assertIn("不会进入业务执行时间线", CHAT_JS)
        self.assertIn("节点流水线", CHAT_JS)
        self.assertIn("节点产出", CHAT_JS)
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

    def test_local_first_search_requires_separate_choice_confirmation_and_tick(self):
        self.assertIn('id="first-search"', CHAT_HTML)
        self.assertIn('id="first-search-candidates"', CHAT_HTML)
        self.assertIn('本地首次检索（需确认）', CHAT_HTML)
        self.assertIn('type=\'checkbox\'', CHAT_JS)
        self.assertIn('生成检索确认卡', CHAT_JS)
        self.assertIn('确认整组检索方案', CHAT_JS)
        self.assertIn('执行下一批', CHAT_JS)
        self.assertIn('非合格结果', CHAT_JS)
        self.assertIn('未接授权媒体网关', CHAT_JS)
        self.assertNotIn('innerHTML', CHAT_JS)

    def test_page_exposes_model_roles_and_accessible_structure(self):
        self.assertIn('id="main-content"', CHAT_HTML)
        self.assertIn('class="skip-link"', CHAT_HTML)
        self.assertIn('role="log"', CHAT_HTML)
        self.assertIn('aria-relevant="additions"', CHAT_HTML)
        self.assertIn('DSH 需求理解 / 扩词模型', CHAT_HTML)
        self.assertIn('清洗语义判定模型', CHAT_HTML)
        self.assertIn('使用 API 与密钥配置', CHAT_HTML)
        self.assertIn('id="api-key" type="password"', CHAT_HTML)
        self.assertIn('密钥不写入浏览器存储', CHAT_HTML)
        self.assertIn('id="new-chat"', CHAT_HTML)
        self.assertIn('新建 Chat', CHAT_HTML)
        self.assertIn('class="right-rail"', CHAT_HTML)
        self.assertGreater(CHAT_HTML.index('class="right-rail"'), CHAT_HTML.index('class="chat"'))
        self.assertNotIn('LOCAL WORKSPACE · PREVIEW', CHAT_HTML)
        self.assertNotIn('把检索需求整理成', CHAT_HTML)
        self.assertNotIn('先梳理目标、分组和推进方式', CHAT_HTML)
        self.assertIn('class="context-bar"', CHAT_HTML)
        self.assertIn("$('new-chat').addEventListener('click',startNewChat)", CHAT_JS)
        self.assertIn("function renameChat", CHAT_JS)
        self.assertIn("expected_draft_version=state.resumeDraft.draft_version", CHAT_JS)
        self.assertIn("function saveApiConfig", CHAT_JS)
        self.assertNotIn("localStorage.setItem('dsh-api", CHAT_JS)
        self.assertIn("document.createElement('details')", CHAT_JS)
        self.assertIn("activity.setAttribute('aria-busy'", CHAT_JS)
        self.assertIn(':focus-visible', CHAT_CSS)
        self.assertIn('@media(max-width:560px)', CHAT_CSS)
        self.assertIn('@media(prefers-reduced-motion:reduce)', CHAT_CSS)

    def test_chat_analyzes_each_submitted_utterance_before_draft_write(self):
        self.assertIn('/v1/retrieval/projects/demo/intent-preview', CHAT_JS)
        self.assertIn('LLM 诉求分析 · 待审阅', CHAT_JS)
        self.assertIn('await analyzeChatText(text);const expansion=', CHAT_JS)
        self.assertIn("await analyzeChatText(text);state.feedback=", CHAT_JS)
        self.assertIn('分析只供审阅，不自动改写原文', CHAT_JS)
        self.assertNotIn('innerHTML', CHAT_JS)


if __name__ == "__main__":
    unittest.main()
