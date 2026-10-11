"""Optional, site-owner-authorized Turnstile provider. Credentials stay in memory."""
import asyncio
import os
import time

import httpx

from .reports import AnalysisError

API = "https://api.2captcha.com"

# Install before navigation so the site's actual callback is retained. No changes
# to browser fingerprints, challenge scripts, or the site's validation rules.
TURNSTILE_CAPTURE = r"""(() => {
  let api;
  function wrap(value) {
    if (!value || !value.render || value.render.__ratingWrapped) return;
    const render = value.render;
    function wrapped(container, options) {
      const result = render.call(this, container, options);
      if (options && options.sitekey) {
        window.__ratingTurnstile = {
          sitekey: options.sitekey, action: options.action,
          data: options.cData, pagedata: options.chlPageData,
          callback: options.callback, widget: result
        };
      }
      return result;
    }
    wrapped.__ratingWrapped = true;
    value.render = wrapped;
  }
  const descriptor = Object.getOwnPropertyDescriptor(window, 'turnstile');
  if (!descriptor || descriptor.configurable) {
    api = window.turnstile;
    Object.defineProperty(window, 'turnstile', {
      configurable: true, enumerable: true,
      get: () => api, set: value => { api = value; wrap(value); }
    });
    wrap(api);
  }
})();"""


def enabled():
    provider = os.getenv("MORTAL_CAPTCHA_PROVIDER", "manual").strip().lower()
    if provider not in ("manual", "2captcha"):
        raise AnalysisError("MORTAL_CAPTCHA_PROVIDER 必须为 manual 或 2captcha。")
    if provider == "2captcha" and not os.getenv("TWOCAPTCHA_API_KEY", "").strip():
        raise AnalysisError("已启用 2Captcha，但未配置 TWOCAPTCHA_API_KEY。请在本地环境安全设置密钥。")
    return provider == "2captcha"


async def _pause(seconds, cancelled):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cancelled():
            raise AnalysisError("任务已取消；已创建的 2Captcha 任务仍可能计费。")
        await asyncio.sleep(min(.5, max(0, end - time.monotonic())))


async def solve(parameters, cancelled=lambda: False, transport=None):
    """One paid task at most. Never retry task creation on uncertain responses."""
    key = os.getenv("TWOCAPTCHA_API_KEY", "").strip()
    if not key:
        raise AnalysisError("缺少 TWOCAPTCHA_API_KEY。")
    task = {"type": "TurnstileTaskProxyless", "websiteURL": parameters["url"],
            "websiteKey": parameters["sitekey"]}
    for name in ("action", "data", "pagedata"):
        if parameters.get(name):
            task[name] = parameters[name]
    deadline = time.monotonic() + max(10, int(os.getenv("TWOCAPTCHA_TIMEOUT", "180")))
    async with httpx.AsyncClient(timeout=30, follow_redirects=False, transport=transport) as client:
        async def request(path, payload):
            try:
                response = await client.post(API + path, json=payload)
                response.raise_for_status()
                body = response.json()
                if not isinstance(body, dict):
                    raise ValueError()
            except (httpx.HTTPError, ValueError) as exc:
                raise AnalysisError("2Captcha 请求失败；未自动重试创建任务，请检查服务连接与账户任务记录。") from exc
            if body.get("errorId"):
                # Only expose documented code-like identifiers; descriptions can
                # echo request data and must never appear in saved task errors.
                code = str(body.get("errorCode", "UNKNOWN"))
                if not code.startswith("ERROR_") or not all(c.isupper() or c.isdigit() or c == "_" for c in code):
                    code = "UNKNOWN"
                raise AnalysisError(f"2Captcha 返回错误：{code}。")
            return body
        if cancelled():
            raise AnalysisError("任务已取消。")
        created = await request("/createTask", {"clientKey": key, "task": task})
        task_id = created.get("taskId")
        if not isinstance(task_id, int) or isinstance(task_id, bool) or task_id <= 0:
            raise AnalysisError("2Captcha 未返回有效任务编号；请检查账户记录，不会自动再次付费创建。")
        while time.monotonic() < deadline:
            await _pause(min(5, max(0, deadline - time.monotonic())), cancelled)
            if time.monotonic() >= deadline:
                break
            result = await request("/getTaskResult", {"clientKey": key, "taskId": task_id})
            if result.get("status") == "processing":
                continue
            if result.get("status") != "ready":
                raise AnalysisError("2Captcha 返回未知任务状态。")
            solution = result.get("solution")
            token = solution.get("token") if isinstance(solution, dict) else None
            if not isinstance(token, str) or not token:
                raise AnalysisError("2Captcha 未返回有效验证结果。")
            return token
    raise AnalysisError("2Captcha 等待超时；任务仍可能计费，请检查账户记录后再重试。")


async def complete(page, cancelled=lambda: False):
    parameters = None
    for _ in range(10):
        if cancelled():
            raise AnalysisError("任务已取消。")
        parameters = await page.evaluate("""() => {
          const captured = window.__ratingTurnstile;
          if (captured && captured.sitekey && typeof captured.callback === 'function')
            return {sitekey: captured.sitekey, action: captured.action,
                    data: captured.data, pagedata: captured.pagedata, url: location.href};
          const widget = document.querySelector('form[name="reviewForm"] [data-sitekey]');
          const callback = widget && widget.dataset.callback;
          if (widget && callback && typeof window[callback] === 'function')
            return {sitekey: widget.dataset.sitekey, action: widget.dataset.action,
                    data: widget.dataset.cdata, pagedata: widget.dataset.chlPageData,
                    url: location.href};
          return null;
        }""")
        if parameters:
            break
        await _pause(1, cancelled)
    if not parameters:
        raise AnalysisError("无法取得网站 Turnstile 参数及回调；未创建付费任务。请使用手动验证或报告导入。")
    token = await solve(parameters, cancelled)
    if cancelled():
        raise AnalysisError("任务已取消；2Captcha 任务可能已经计费。")
    applied = await page.evaluate("""token => {
      const form = document.querySelector('form[name="reviewForm"]');
      if (!form) return false;
      const fields = form.querySelectorAll('[name="cf-turnstile-response"]');
      if (fields.length) fields.forEach(field => { field.value = token; });
      else {
        const field = document.createElement('input');
        field.type = 'hidden'; field.name = 'cf-turnstile-response';
        field.value = token; form.appendChild(field);
      }
      const captured = window.__ratingTurnstile;
      let callback = captured && captured.callback;
      if (typeof callback !== 'function') {
        const widget = form.querySelector('[data-sitekey][data-callback]');
        callback = widget && window[widget.dataset.callback];
      }
      if (typeof callback !== 'function') return false;
      callback(token);
      return true;
    }""", token)
    if not applied:
        raise AnalysisError("网站验证回调已变化；2Captcha 已完成，但未提交牌谱。")
