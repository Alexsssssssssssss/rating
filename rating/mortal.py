import asyncio
import os
import re
import shutil
import time
from pathlib import Path
from email.parser import BytesParser
from email.policy import default as email_policy
from urllib.parse import parse_qs, quote, urlsplit

from playwright.async_api import async_playwright, Error as BrowserError

from .reports import AnalysisError, parse_html_report, log_matches
from . import captcha

MORTAL_SITE = "https://mjai.ekyu.moe/zh-cn.html"


class MortalBrowser:
    """串行使用官方网站表单，遇到验证立即停止，不使用未经确认的私有 API。"""
    def __init__(self):
        self.pw = self.browser = self.context = None
        self.last_submit = 0

    async def __aenter__(self):
        self.captcha_enabled = captcha.enabled()
        self.pw = await async_playwright().start()
        opts = {"headless": os.getenv("MORTAL_HEADLESS", "1") != "0"}
        executable = os.getenv("CHROMIUM_PATH") or shutil.which("chromium") or shutil.which("google-chrome")
        if executable:
            opts["executable_path"] = executable
        proxy = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy")
        if proxy:
            p = urlsplit(proxy)
            opts["proxy"] = {"server": f"{p.scheme}://{p.hostname}:{p.port or 80}"}
            if p.username:
                from urllib.parse import unquote
                opts["proxy"].update(username=unquote(p.username), password=unquote(p.password or ""))
        try:
            self.browser = await self.pw.chromium.launch(**opts)
            self.context = await self.browser.new_context(locale="zh-CN", accept_downloads=True)
            if self.captcha_enabled:
                await self.context.add_init_script(captcha.TURNSTILE_CAPTURE)
        except BrowserError as exc:
            await self.pw.stop()
            raise AnalysisError("Chromium 无法启动。请安装系统 Chromium 或运行 python -m playwright install chromium。") from exc
        return self

    async def __aexit__(self, *args):
        try:
            if self.browser:
                await self.browser.close()
        except BrowserError:
            pass
        finally:
            if self.pw:
                await self.pw.stop()

    async def prepare_form(self, page, game):
        """已在官方网站核对的 reviewForm 控件；不操作派遣个室表单。"""
        official = page.locator('form[name="reviewForm"]')
        form = official if await official.count() == 1 else page
        selector = os.getenv("MORTAL_URL_SELECTOR")
        field = form.locator(selector or 'input[name="log-url"]')
        if await field.count() != 1 and not selector:
            field = form.locator('input[type="url"]')
        if await field.count() != 1:
            raise AnalysisError("无法唯一识别 Mortal 的牌谱链接输入框；网站表单可能更新。可配置 MORTAL_URL_SELECTOR 后重试。")
        await field.fill(game["record_url"])
        if await official.count() == 1:
            await form.locator('select[name="player-id"]').select_option(str(game['seat']))
            await form.locator('select[name="engine"]').select_option('mortal')
            await form.locator('select[name="ui"]').select_option('classic')
            await form.locator('select[name="lang"]').select_option('zh-CN')
            model = os.getenv('MORTAL_MODEL_TAG', '4.1b')
            if model not in ('4.1a', '4.1b', '4.1c', '4.0', '3.0'):
                raise AnalysisError('MORTAL_MODEL_TAG 不属于官网支持的模型。')
            await form.locator('select[name="mortal-model-tag"]').select_option(model)
            # Rating 位于折叠的高级选项中，需要先展开再勾选。
            await form.get_by_text('高级选项', exact=True).click()
            await form.locator('input[name="show-rating"]').check()
        else:
            checkbox = form.get_by_label(re.compile(r"(?:显示|show).*rating", re.I))
            if await checkbox.count() == 1:
                await checkbox.check()
        selector = os.getenv("MORTAL_SUBMIT_SELECTOR")
        submit = form.locator(selector or 'button[type="submit"], input[type="submit"]')
        if await submit.count() != 1:
            raise AnalysisError("无法唯一识别 Mortal 提交按钮。可配置 MORTAL_SUBMIT_SELECTOR 后重试。")
        return submit

    @staticmethod
    def submission_matches(request, game):
        """Bind a document response to the exact official POST and redirects."""
        current = request
        while current is not None:
            url = urlsplit(current.url)
            if url.scheme != "https" or url.netloc != "mjai.ekyu.moe":
                return False
            previous = current.redirected_from
            if previous is None:
                break
            current = previous
        if current is None or current.method != "POST" or url.path != "/review":
            return False
        content_type = current.headers.get("content-type", "")
        body = current.post_data_buffer or b""
        fields = {}
        try:
            if content_type.startswith("application/x-www-form-urlencoded"):
                fields = parse_qs(body.decode("utf-8"), keep_blank_values=True)
            elif content_type.startswith("multipart/form-data"):
                message = BytesParser(policy=email_policy).parsebytes(
                    ("Content-Type: " + content_type + "\r\nMIME-Version: 1.0\r\n\r\n").encode() + body)
                if not message.is_multipart():
                    return False
                for part in message.iter_parts():
                    name = part.get_param("name", header="content-disposition")
                    if name in ("log-url", "player-id", "engine"):
                        fields.setdefault(name, []).append((part.get_payload(decode=True) or b"").decode("utf-8"))
            else:
                return False
        except (ValueError, UnicodeError):
            return False
        return (log_matches(game["uuid"], game["record_url"])
                and fields.get("log-url") == [game["record_url"]]
                and fields.get("player-id") == [str(game["seat"])]
                and fields.get("engine") == ["mortal"])

    @staticmethod
    def validate_report_identity(result, html, game, bound_submission=False):
        """Preserve a parsed report for diagnosis; never assign an unverified score."""
        log_id = result.get("log_id")
        if log_matches(game["uuid"], log_id):
            return
        if (not isinstance(log_id, str) or not log_id.strip()) and bound_submission:
            # The report itself has no ID. Record the provenance explicitly:
            # identity comes from the actual POST response, not invented metadata.
            result["log_id"] = game["uuid"]
            result["identity_method"] = "official-post-response"
            result["submitted_record_url"] = game["record_url"]
            return
        root = Path(os.getenv("RATING_DATA_DIR", ".data")).resolve() / "reports"
        # Use a generated name, never untrusted game identifiers as a path.
        path = root / f"unverified-{time.time_ns()}.html"
        try:
            root.mkdir(parents=True, exist_ok=True)
            path.write_text(html, encoding="utf-8")
        except OSError:
            saved = "报告保存失败，请检查数据目录写入权限。"
        else:
            saved = f"完整报告已保存到：{path}。"
        if not isinstance(log_id, str) or not log_id.strip():
            raise AnalysisError("Mortal 报告缺少牌谱 ID（log_id），尚不能确认所属比赛；这不等于报告来自另一场。"
                                + saved + "需要带原始 ref 的独立牌谱进行完整内容校验，未录入评分。")
        raise AnalysisError("Mortal 报告中的牌谱 ID 与当前比赛不同，拒绝录入评分。" + saved)

    async def analyze(self, game, cancelled=lambda: False):
        if game["masked"]:
            raise AnalysisError("牌谱屋隐藏了该场 UUID，原站也未提供 AI 检讨入口；无法自动提交 Mortal。")
        # Mortal 上游当前仅支持四人南；东场保留在最新 x 场中并清楚标注。
        if game["mode_id"] in (8, 11, 15):
            raise AnalysisError("当前 mjai-reviewer Mortal 引擎仅支持四人南；该四人东场保留在结果中但不伪造评分。")
        delay = max(0, float(os.getenv("MORTAL_INTERVAL", "15")) - (time.monotonic() - self.last_submit))
        while delay > 0:
            if cancelled():
                raise AnalysisError("任务已取消。")
            await asyncio.sleep(min(1, delay))
            delay -= 1
        page = await self.context.new_page()
        navigations = {}
        def track_navigation(request):
            if request.is_navigation_request():
                navigations[request.frame] = request
        page.on("request", track_navigation)
        try:
            target = f"{MORTAL_SITE}?url={quote(game['record_url'], safe='')}"
            try:
                response = await page.goto(target, wait_until="domcontentloaded", timeout=45000)
            except BrowserError as exc:
                if "ERR_CERT_AUTHORITY_INVALID" in str(exc):
                    raise AnalysisError("Chromium 尚未信任此云环境的代理 CA，TLS 校验失败；需要经授权配置证书信任后重试。") from exc
                raise AnalysisError("无法打开 Mortal 网站。请检查网络策略及 mjai.ekyu.moe 域名放行。") from exc
            if response and response.status >= 400:
                raise AnalysisError(f"Mortal 网站返回 HTTP {response.status}。")
            await page.wait_for_timeout(1000)
            submit = await self.prepare_form(page, game)
            if await submit.is_disabled() and self.captcha_enabled:
                await captcha.complete(page, cancelled)
            # 等待站点自己的正常验证回调；不生成 token、不点击/破解验证控件。
            verification_deadline = time.monotonic() + int(os.getenv('MORTAL_VERIFICATION_TIMEOUT', '15'))
            while await submit.is_disabled() and time.monotonic() < verification_deadline:
                if cancelled():
                    raise AnalysisError('任务已取消。')
                await asyncio.sleep(1)
            if await submit.is_disabled():
                raise AnalysisError("Mortal 提交按钮等待 Cloudflare 人机验证，已暂停自动提交。请用普通 Edge 在原站手动分析，启用 Rating 后保存 Classic HTML 或 KillerDucky 原始 JSON，再导入报告；未提交该场牌谱。")
            self.last_submit = time.monotonic()
            await submit.click()
            deadline = time.monotonic() + int(os.getenv("MORTAL_TIMEOUT", "300"))
            last_error = "未收到报告"
            while time.monotonic() < deadline:
                if cancelled():
                    raise AnalysisError("任务已取消。")
                # 官方结果可能显示于新页或 iframe；只解析真正的元数据。
                for p in self.context.pages:
                    for frame in p.frames:
                        try:
                            request = navigations.get(frame) if p == page else None
                            bound = bool(request and self.submission_matches(request, game))
                            if bound:
                                response = await request.response()
                                if not response or response.status != 200 or response.url != frame.url:
                                    continue
                                # Parse the actual server response, not an unrelated
                                # tab or a DOM that might belong to another navigation.
                                html = await response.text()
                            else:
                                html = await frame.content()
                            result = parse_html_report(html, game["seat"])
                            self.validate_report_identity(result, html, game, bound)
                            result['requested_model'] = os.getenv('MORTAL_MODEL_TAG', '4.1b')
                            return result, html
                        except AnalysisError as exc:
                            last_error = str(exc)
                            if any(s in last_error for s in ("未公开 Rating", "与目标用户不符", "无法与当前比赛匹配", "缺少牌谱 ID", "牌谱 ID 与当前比赛不同")):
                                raise
                        except BrowserError:
                            continue
                text = await page.locator("body").inner_text()
                if re.search(r"验证码|人机验证|too many requests|rate limit|access denied|captcha", text, re.I):
                    raise AnalysisError("Mortal 要求人机验证或触发限流；已暂停自动访问，请在原站完成验证或稍后重试。")
                await asyncio.sleep(2)
            raise AnalysisError(f"Mortal 分析超时：{last_error}。网站结果结构可能更新。")
        except BrowserError as exc:
            raise AnalysisError("Mortal 浏览器操作失败或控件超时，请确认网站表单并重试。") from exc
        finally:
            # 任务之间关闭所有页面，防止把上一场报告误当作新结果。
            for p in list(self.context.pages):
                try:
                    await p.close()
                except BrowserError:
                    pass
