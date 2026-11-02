"""Drive the REAL web UI with Playwright so E2E cases are *visible* in a browser.

Run with the interpreter that has Playwright installed (see AGENTS.md: no new
dependency is installed on a whim, so we reuse the already-installed miniconda3
build):

    /Users/hw-edit/miniconda3/bin/python tools/e2e_live_demo/run_live_demo.py

The runner:

1. writes a trusted fake model config into gitignored ``var/e2e/`` (no literal
   key or loopback address is committed anywhere),
2. starts a stdlib fake OpenAI ``/chat/completions`` stub on a free port,
3. starts the *real* production server ``python3 -m dsh_retrieval.api`` on a
   free port with isolated ledgers under ``var/e2e/`` and
   ``--agent-runtime direct`` (no DSH interpreter, no external model),
4. opens a headed Chromium window and simulates the full human flow:
   找狗图片 → 需要多少张图片？ → 3张 → 收到，共 3 张，对吗？ → 是 →
   确认检索词并开始检索 → background first-search → 15 raw hits (dog-1/dog-2),
5. saves screenshots, a Playwright trace and a video under ``var/e2e/`` as the
   repeatable "visible test case" evidence.

Exit code 0 means every assertion passed; the headed window and the recorded
artifacts let a human watch the same flow.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VAR = ROOT / "var" / "e2e"
STUB_SCRIPT = ROOT / "tools" / "e2e_live_demo" / "fake_model_stub.py"

FAKE_KEY_ENV = "E2E_FAKE_API_KEY"
FAKE_KEY = "e2e-dummy-key-not-a-secret"
# >= 32 bytes as required by --local-first-search-fixture.
CURSOR_SECRET = "e2e_local_first_search_cursor_secret_0123456789abcdef"

# The production ModelConfig and ModelSettings strictly require HTTPS base URLs,
# so the local fake-model stub serves HTTPS with a throwaway self-signed cert.
# Both the cert and its private key live under gitignored var/e2e/ (minted fresh
# each run) and are trusted only for this run via SSL_CERT_FILE; nothing leaves
# the machine and nothing is committed.
SELF_SIGNED_CERT = "e2e-local-fake-model-ca.pem"
SELF_SIGNED_KEY = "e2e-local-fake-model-key.pem"

CATALOG_SOURCE = "config/local-first-search-catalog.example.json"
CATALOG_LEDGER = "var/e2e/catalog.sqlite3"
QUERY_CAPABILITIES = "config/local-first-search-capabilities.example.json"
FIXTURE = "config/local-first-search-guided.example.json"


def log(message: str) -> None:
    print(f"[e2e] {message}", flush=True)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def mint_self_signed_cert() -> tuple[str, str]:
    """Throwaway CA for the local fake-model stub; kept only under var/e2e/.

    Imported lazily so the runner stays importable without the ``cryptography``
    package (the server/stub themselves never need it). Uses the miniconda3
    interpreter this runner already requires, which ships ``cryptography``.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    import datetime
    import ipaddress

    cert_path, key_path = VAR / SELF_SIGNED_CERT, VAR / SELF_SIGNED_KEY
    if cert_path.exists() and key_path.exists():
        return str(cert_path), str(key_path)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,
                                         "DSH e2e local fake model CA")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                                     key_encipherment=True, data_encipherment=False,
                                     key_agreement=False, key_cert_sign=True, crl_sign=True,
                                     encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(x509.SubjectAlternativeName([
            x509.DNSName("localhost"),
            x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
        .sign(key, hashes.SHA256()))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                           serialization.PrivateFormat.TraditionalOpenSSL,
                                           serialization.NoEncryption()))
    return str(cert_path), str(key_path)


def write_models_json(stub_port: int) -> str:
    stub_url = f"https://127.0.0.1:{stub_port}/v1"
    models = {
        "models": [
            {"id": "fake-llm", "kind": "llm", "provider": "e2e-fake",
             "model": "fake-llm-v1", "base_url": stub_url, "api_key_env": FAKE_KEY_ENV},
            {"id": "fake-vlm", "kind": "vlm", "provider": "e2e-fake",
             "model": "fake-vlm-v1", "base_url": stub_url, "api_key_env": FAKE_KEY_ENV},
        ]
    }
    path = VAR / "models.json"
    path.write_text(json.dumps(models, ensure_ascii=False, indent=2) + "\n", "utf-8")
    return stub_url


def prepare_environment() -> None:
    VAR.mkdir(parents=True, exist_ok=True)
    # Fresh isolated state and evidence; everything under var/ is gitignored.
    for pattern in ("*.sqlite3", "*.png", "*.webm", "trace.zip", "*.pem"):
        for stale in VAR.glob(pattern):
            stale.unlink()
    (VAR / "dsh-home").mkdir(exist_ok=True)


def resolve_project_python() -> str:
    # The project core is pure stdlib under its own 3.13 venv; Playwright lives
    # in a separate miniconda3 3.10. The server and stub run on the project
    # interpreter, while this runner drives the browser with the Playwright one.
    candidates = [
        shutil.which("python3"),
        str(ROOT / ".venv-dsh" / "bin" / "python3"),
    ]
    for candidate in candidates:
        if candidate:
            return candidate
    raise RuntimeError("no project Python interpreter found")


def start_process(argv, *, env, log_path, name):
    stream = open(log_path, "wb")
    process = subprocess.Popen(
        argv, cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT)
    log(f"started {name} (pid {process.pid}) -> {log_path.relative_to(ROOT)}")
    return process, stream


def http_json(url: str, timeout: float = 5.0, cafile: str | None = None) -> dict | None:
    try:
        if cafile:
            import ssl
            context = ssl.create_default_context(cafile=cafile)
            with urllib.request.urlopen(url, timeout=timeout, context=context) as response:
                raw = response.read(262_144)
        else:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                raw = response.read(262_144)
    except (urllib.error.URLError, OSError):
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def http_json_auth(url: str, timeout: float = 5.0) -> dict | None:
    """Same as http_json but with the same identity headers chat_ui.js sends."""
    request = urllib.request.Request(url, headers={
        "Content-Type": "application/json",
        "X-Tenant-Id": "tenant-a",
        "X-User-Id": "user-a",
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(262_144)
    except (urllib.error.URLError, OSError):
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def wait_for(fn, *, timeout: float, desc: str, interval: float = 0.1):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            value = fn()
            if value:
                return value
        except Exception as error:  # noqa: BLE001 - poll-then-raise on timeout
            last = error
        time.sleep(interval)
    raise AssertionError(
        f"timed out waiting for {desc}" + (f"; last error: {last}" if last else ""))


def body_text(page) -> str:
    return page.locator("body").inner_text()


def assert_text(page, needle: str, *, within: float = 20.0, desc: str | None = None) -> None:
    wait_for(lambda: needle in body_text(page), timeout=within,
             desc=desc or f'text "{needle}"')
    log(f"PASS: 页面上出现「{needle}」")


def run_browser_flow(api_url: str) -> dict:
    from playwright.sync_api import sync_playwright

    evidence = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            viewport={"width": 1440, "height": 940},
            record_video_dir=str(VAR),
            record_video_size={"width": 1440, "height": 940},
        )
        context.tracing.start(screenshots=True, snapshots=True)
        page = context.new_page()
        try:
            page.goto(f"{api_url}/", wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle")

            # Model auto-select: exactly one fake LLM / VLM profile.
            wait_for(lambda: page.evaluate(
                "document.getElementById('llm-model').value") == "fake-llm",
                timeout=15.0, desc="LLM 自动选择 fake-llm")
            wait_for(lambda: page.evaluate(
                "document.getElementById('vlm-model').value") == "fake-vlm",
                timeout=15.0, desc="VLM 自动选择 fake-vlm")
            log("PASS: 服务端 fake-llm / fake-vlm 已自动选中")

            request_box = page.locator("#request-text")
            request_box.fill("找狗图片")
            request_box.press("Enter")
            assert_text(page, "需要多少张图片？", desc="第一轮追问数量")

            request_box.fill("3张")
            request_box.press("Enter")
            assert_text(page, "收到，共 3 张，对吗？", desc="数量确认摘要")

            request_box.fill("是")
            request_box.press("Enter")

            # "是" routes straight to the term review without printing a reply.
            confirm_button = page.get_by_text("确认检索词并开始检索", exact=True)
            wait_for(lambda: confirm_button.is_visible(), timeout=30.0,
                     desc="检索词确认按钮")
            log("PASS: 出现检索词确认按钮（「是」直接进入扩词审阅）")
            assert_text(page, "狗 | 柯基，金毛，宠物狗", within=5.0, desc="狗分组词表")

            confirm_button.click()

            # Background first-search runs to completion; the live-status line
            # reports the raw hit count and the resolved media units dog-1/dog-2.
            def _first_search_progress():
                task_id = page.evaluate("localStorage.getItem('dsh-task-id') || ''")
                if not task_id:
                    return False
                base = f"{api_url}/v1/retrieval/tasks/{task_id}/first-search"
                progress = http_json_auth(f"{base}/progress")
                candidates = http_json_auth(f"{base}/candidates")
                if progress:
                    log("progress: pagination_stopped=%s status=%s members=%s" % (
                        progress.get("pagination_stopped"), progress.get("status"),
                        [(m.get("status"), m.get("stop_reason"), m.get("committed_pages"))
                         for m in progress.get("members", [])]))
                if candidates and isinstance(candidates, dict):
                    log("candidates: raw_hit_count=%s resolved=%s verified=%s" % (
                        candidates.get("raw_hit_count"), candidates.get("resolved_count"),
                        candidates.get("verified_count")))
                # The page itself renders "本地检索完成 · … · 原始命中 N 条" only once
                # pagination has stopped; assert the count that this fixture + the
                # 狗 group (柯基/金毛/宠物狗) deterministically resolve to.
                return "本地检索完成" in body_text(page) and "原始命中 15" in body_text(page)

            wait_for(_first_search_progress,
                     timeout=60.0, desc="本地检索完成，原始命中 15 条")
            assert_text(page, "素材：dog-1（v1）", within=10.0, desc="候选 dog-1")
            assert_text(page, "素材：dog-2（v1）", within=10.0, desc="候选 dog-2")
            log("PASS: 候选素材 dog-1 / dog-2 已在页面实时展示")

            screenshot = VAR / "final-state.png"
            page.screenshot(path=str(screenshot), full_page=True)
            evidence["screenshot"] = str(screenshot.relative_to(ROOT))
            log(f"PASS: 全页截图已保存 {evidence['screenshot']}")

            # Reopen the same candidates via REST as a machine-checkable
            # assertion of the signal a human just watched render.
            task_id = page.evaluate("localStorage.getItem('dsh-task-id') || ''")
            if task_id:
                candidates = http_json_auth(
                    f"{api_url}/v1/retrieval/tasks/{task_id}/first-search/candidates")
                if candidates:
                    assert candidates.get("raw_hit_count") == 15, candidates
                    assert {item.get("media_unit_id") for item in candidates.get("items", [])
                            if item.get("status") == "resolved"} == {"dog-1", "dog-2"}
                    log("PASS: 服务端候选接口 raw_hit_count == 15，且解析出 dog-1/dog-2")
                    evidence["task_id"] = task_id
        finally:
            trace = VAR / "trace.zip"
            context.tracing.stop(path=str(trace))
            evidence["trace"] = str(trace.relative_to(ROOT)) if trace.exists() else None
            context.close()
            browser.close()

    videos = sorted(VAR.glob("*.webm"), key=lambda path: path.stat().st_mtime)
    if videos:
        evidence["video"] = str(videos[-1].relative_to(ROOT))
        log(f"PASS: 录屏已保存 {evidence['video']}")
    return evidence


def main() -> int:
    project_python = resolve_project_python()
    prepare_environment()

    stub_port, api_port = free_port(), free_port()
    while api_port == stub_port:
        api_port = free_port()

    cert_file, key_file = mint_self_signed_cert()
    stub_url = write_models_json(stub_port)
    api_url = f"http://127.0.0.1:{api_port}"

    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    env[FAKE_KEY_ENV] = FAKE_KEY
    env["DSH_LOCAL_CURSOR_SECRET"] = CURSOR_SECRET
    env["DSH_HOME"] = str(VAR / "dsh-home")
    # Only this run trusts the throwaway CA: the server (as TLS client to the
    # stub) reads SSL_CERT_FILE via ssl.create_default_context; the runner
    # verifies the same CA explicitly on its own readiness probes.
    env["SSL_CERT_FILE"] = cert_file

    stub, stub_stream = start_process(
        [project_python, str(STUB_SCRIPT), "--port", str(stub_port),
         "--tls-cert", cert_file, "--tls-key", key_file],
        env=env, log_path=VAR / "stub.log", name="fake-model stub")
    server, server_stream = start_process(
        [project_python, "-m", "dsh_retrieval.api",
         "--demo-project", "--port", str(api_port),
         "--ledger", "var/e2e/ledger.sqlite3",
         "--dialogue-ledger", "var/e2e/dialogue.sqlite3",
         "--model-config", "var/e2e/models.json",
         "--agent-runtime", "direct",
         "--catalog-source", CATALOG_SOURCE,
         "--catalog-ledger", CATALOG_LEDGER,
         "--query-capabilities", QUERY_CAPABILITIES,
         "--local-first-search-fixture", FIXTURE],
        env=env, log_path=VAR / "server.log", name="DSH retrieval API")

    try:
        wait_for(lambda: http_json(f"{api_url}/v1/build"), timeout=40.0,
                 desc="API /v1/build")
        log(f"PASS: 真实 DSH 服务已在 {api_port} 就绪（隔离账本 + direct 运行时）")
        wait_for(lambda: http_json(f"{stub_url}/chat/completions", cafile=cert_file),
                 timeout=20.0, desc="fake model stub (https)")
        log(f"PASS: 假模型 stub 已在 {stub_port} 就绪（HTTPS + 一次性自签证书）")

        evidence = run_browser_flow(api_url)

        print("\n===== E2E 实时界面用例结果 =====")
        print("结论: PASS（全部页面断言通过）")
        for key, value in evidence.items():
            if value:
                print(f"  {key}: var/e2e/{Path(value).name}")
        print(f"  服务端口: {api_port} · 模型 stub: {stub_port}")
        print(f"  证据目录: {VAR.relative_to(ROOT)}（截图/录屏/浏览器 trace）")
        return 0
    except Exception as error:  # noqa: BLE001 - surfaced verbatim, not swallowed
        print(f"\n===== E2E 实时界面用例结果 =====\n结论: FAIL\n原因: {error!r}")
        print("  服务日志: var/e2e/server.log · stub 日志: var/e2e/stub.log")
        sys.stdout.flush()
        for stream in (stub_stream, server_stream):
            if not stream.closed:
                print("\n----- log tail -----")
                tail = (VAR / Path(stream.name).name).read_text(
                    encoding="utf-8", errors="replace").splitlines()[-40:]
                print("\n".join(tail) if tail else "(empty)")
        return 1
    finally:
        for process, stream in ((server, server_stream), (stub, stub_stream)):
            stream.close()
            if process.poll() is None:
                process.terminate()
        for process in (server, stub):
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
        log("teardown complete")


if __name__ == "__main__":
    raise SystemExit(main())