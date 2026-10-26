from io import BytesIO
from http.client import RemoteDisconnected
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from urllib.error import HTTPError, URLError

from dsh_retrieval.errors import ServiceError
from dsh_retrieval.intent_model import IntentModelClient, ModelConfig, NoRedirect, resolve_config, load_dsh_config


class Opener:
    def __init__(self, data=None, error=None):
        self.data, self.error, self.calls = data, error, []

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        if self.error:
            raise self.error
        return BytesIO(self.data)


def item(quote="不要文字", **changes):
    result = {"quote": quote, "occurrence": 0, "kind": "constraint", "hardness": "hard",
              "predicate_ref": "text.present@1", "operator": "eq", "value": False, "question": None}
    return result | changes


def response(items=None, reason="stop", media_type="image"):
    content = {"media_type": media_type, "items": [item()] if items is None else items}
    return json.dumps({"choices": [{"finish_reason": reason, "message": {"content": json.dumps(content)}}]}).encode()


class IntentModelTest(unittest.TestCase):
    def setUp(self):
        self.config = ModelConfig("fixture", "fixture-model", "https://fixture.invalid/v1", "dummy-test-secret")

    def client(self, opener):
        return IntentModelClient(self.config, opener=opener)

    def assert_code(self, code, call):
        with self.assertRaises(ServiceError) as error:
            call()
        self.assertEqual(error.exception.code, code)
        self.assertNotIn("dummy-test-secret", str(error.exception))

    def test_real_adapter_request_but_unverified_capability_still_blocked(self):
        opener = Opener(response())
        result = self.client(opener).interpret("不要文字")
        self.assertEqual(result["interpretation"]["items"][0]["start"], 0)
        self.assertEqual(result["review"]["items"][0]["capability_status"], "unsupported")
        self.assertFalse(result["review"]["execution_allowed"])
        request, timeout = opener.calls[0]
        self.assertEqual(request.full_url, "https://fixture.invalid/v1/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer dummy-test-secret")
        system_prompt = json.loads(request.data)['messages'][0]['content']
        self.assertIn('首次必须为 0', system_prompt)
        self.assertIn('绝不是字符位置', system_prompt)
        self.assertNotIn("dummy-test-secret", request.data.decode())
        self.assertNotIn("dummy-test-secret", repr(self.config))
        self.assertNotIn("dummy-test-secret", json.dumps(result))
        self.assertEqual(len(opener.calls), 1)

    def test_repeated_quote_uses_explicit_occurrence(self):
        result = self.client(Opener(response([item(occurrence=1)]))).interpret("不要文字，不要文字")
        self.assertEqual(result["interpretation"]["items"][0]["start"], 5)
        self.assertTrue(result["review"]["has_blockers"])

    def test_hallucinated_quote_falls_back_to_unresolved_original(self):
        raw = "100个盒马各种各样的姿势"
        result = self.client(Opener(response([item("100张"), item("各种各样的姿势")]))).interpret(raw)
        self.assertEqual(result["mode"], "SAFE_CLARIFICATION")
        self.assertEqual(result["interpretation"]["items"][0]["quote"], raw)
        self.assertEqual(result["review"]["items"][0]["quote"], raw)
        self.assertEqual(result["review"]["items"][0]["kind"], "unresolved")
        self.assertIsNone(result["review"]["media_type"])
        self.assertTrue(result["review"]["has_blockers"])
        self.assertFalse(result["review"]["execution_allowed"])
        self.assertNotIn("100张", json.dumps(result, ensure_ascii=False))

    def test_invalid_model_media_type_falls_back_without_client_spec_error(self):
        raw = "100个盒马各种各样的姿势\n图片"
        result = self.client(Opener(response([item(raw)], media_type="unknown"))).interpret(raw)
        self.assertEqual(result["mode"], "SAFE_CLARIFICATION")
        self.assertIsNone(result["review"]["media_type"])
        self.assertEqual(result["review"]["items"][0]["quote"], raw)
        self.assertFalse(result["review"]["execution_allowed"])

    def test_model_cannot_inject_approval_or_offsets(self):
        for key in ("execution_allowed", "start", "tenant_id", "script"):
            self.assert_code("MODEL_INTERPRETATION_INVALID", lambda: self.client(
                Opener(response([item(**{key: "untrusted"})]))).interpret("不要文字"))

    def test_auth_failure_does_not_log_body_or_retry(self):
        error = HTTPError("https://fixture.invalid", 401, "dummy-test-secret", {}, BytesIO(b"dummy-test-secret"))
        opener = Opener(error=error)
        self.assert_code("MODEL_AUTH_FAILED", lambda: self.client(opener).interpret("不要文字"))
        self.assertEqual(len(opener.calls), 1)

    def test_transport_failure_redacted(self):
        self.assert_code("MODEL_UNAVAILABLE", lambda: self.client(Opener(error=URLError("dummy-test-secret"))).interpret("不要文字"))

    def test_timeout_and_disconnect_are_redacted(self):
        for error, code in ((TimeoutError("dummy-test-secret"), "MODEL_TIMEOUT"),
                            (RemoteDisconnected("dummy-test-secret"), "MODEL_UNAVAILABLE")):
            self.assert_code(code, lambda: self.client(Opener(error=error)).interpret("不要文字"))

    def test_malformed_envelope_is_safe(self):
        for payload in (b'null', b'{"choices":[null]}', b'{"choices":[]}'):
            self.assert_code("MODEL_RESPONSE_INVALID", lambda: self.client(Opener(payload)).interpret("不要文字"))

    def test_public_credentials_rejected_before_parsing(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "credentials.yaml"
            path.touch(mode=0o644)
            path.chmod(0o644)
            self.assert_code("CREDENTIAL_PERMISSIONS", lambda: load_dsh_config("missing", path))

    def test_truncated_or_malformed_response_rejected(self):
        self.assert_code("MODEL_RESPONSE_INCOMPLETE", lambda: self.client(Opener(response(reason="length"))).interpret("不要文字"))
        self.assert_code("MODEL_RESPONSE_INVALID", lambda: self.client(Opener(b"not JSON")).interpret("不要文字"))

    def test_size_limit(self):
        self.assert_code("MODEL_RESPONSE_LIMIT", lambda: self.client(Opener(b"x" * 1_000_001)).interpret("不要文字"))

    def test_invalid_input_never_calls_network(self):
        opener = Opener(response())
        with self.assertRaises(ServiceError):
            self.client(opener).interpret("")
        self.assertEqual(opener.calls, [])

    def test_insecure_or_credential_bearing_url_rejected(self):
        for url in ("http://fixture.invalid", "https://user:pass@fixture.invalid", "https://fixture.invalid?key=secret"):
            with self.assertRaises(ValueError):
                ModelConfig("p", "m", url, "dummy-test-secret")
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.invalid"))

    def test_config_selects_only_default_provider_key(self):
        profile = [{"name": "@deepseek-ai/dsh-agent-default-model", "config": {"provider": "chosen", "model": "m"}},
                   {"name": "@deepseek-ai/dsh-llm-pi-ai", "config": {"providers": {"chosen": {
                       "api": "openai-completions", "apiKeyEnv": "CHOSEN_KEY", "baseURL": "https://fixture.invalid/v1",
                       "models": [{"id": "m"}]}}}}]
        config = resolve_config(profile, {"refs": {"CHOSEN_KEY": "dummy-test-secret", "OTHER_KEY": "never-use"}})
        self.assertEqual(config.provider, "chosen")
        self.assertEqual(config.api_key, "dummy-test-secret")
        self.assert_code("MODEL_CONFIG_INVALID", lambda: resolve_config(profile, {"refs": {}}))

    def test_preference_is_not_converted_to_hard_filter(self):
        result = self.client(Opener(response([item("横图优先", kind="preference", hardness="soft",
                                                   predicate_ref="orientation@1", value="landscape")]))).interpret("横图优先")
        self.assertEqual(result["review"]["items"][0]["hardness"], "soft")
