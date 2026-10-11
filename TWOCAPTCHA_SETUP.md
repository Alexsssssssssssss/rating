# 可选 2Captcha 接入

仅在 Mortal 站长允许此用途时启用。默认仍为人工验证，不调用付费 API。
38 项测试通过，使用模拟服务和本地网页验证；尚未调用真实付费服务，不能保证 Cloudflare 接受结果。
此更新没有你本地 Codex 的 Windows/报告解析修复，请让本地 Codex 合并变更，不要覆盖整个项目。

## 合并到本地已有项目

新增 `rating/captcha.py` 和 `tests/test_captcha.py`；将 `rating/mortal.py`、`tests/test_browser.py`、`.env.example` 的增量修改合入本地版本。
不需要添加 Python 依赖；使用已有 httpx 和 Playwright。
默认 `MORTAL_CAPTCHA_PROVIDER=manual`。显式设为 `2captcha` 后，一个遇到验证的场次最多创建一个付费任务；任务取消/超时仍可能计费，点击重试会创建新任务。
密钥仅发送到固定的 `https://api.2captcha.com`，不写入任务、缓存或源代码。需要网络允许该主机。

## Windows PowerShell

在运行网页服务的同一个 PowerShell 窗口（提示符有 PS），停止服务后执行：

```powershell
$secret = Read-Host "请输入 2Captcha API key" -AsSecureString
$env:TWOCAPTCHA_API_KEY = [System.Net.NetworkCredential]::new("", $secret).Password.Trim()
Remove-Variable secret
$env:MORTAL_CAPTCHA_PROVIDER = "2captcha"
$env:TWOCAPTCHA_TIMEOUT = "180"
$env:PYTHONUTF8 = "1"
$env:MORTAL_HEADLESS = "0"
.\.venv\Scripts\python.exe -m uvicorn rating.app:app --host 127.0.0.1 --port 8000
```

牌谱屋的 `AMAE_BEARER_TOKEN` 仍需配置，与此密钥独立。
程序不会自动加载 .env.example；上面的环境变量仅对当前窗口有效。
网页取消勾选“仅查询牌谱，手动分析并导入”，先测试 `fioq421 1`。
Cloudflare 仍可能拒绝结果；若报错，提供任务错误文本，不发送 API key。
验证通过不等于报告解析成功，仍需匹配牌谱 ID、座位以及真实 Rating。

切回人工验证：`$env:MORTAL_CAPTCHA_PROVIDER = "manual"` 并重启服务。

## 实现限制

使用 2Captcha v2 `createTask` / `getTaskResult` 和 `TurnstileTaskProxyless`。
捕获原站公开组件参数及正常回调，在结果就绪后调用站点回调；不修改浏览器指纹或隐藏自动化状态。
无法确认回调时不创建付费任务，不自动重试收费创建请求。
当前环境无法打开 2Captcha 文档（网络策略返回 403），上线前可由本地 Codex对照 https://2captcha.com/api-docs/cloudflare-turnstile 核对当前字段。
不支持整页 Cloudflare 拦截或需要更改浏览器 User-Agent 的流程。

## Classic 报告没有 log_id 时

自动流程只在实际 `/review` POST 的牌谱 URL、玩家座位、Mortal 引擎和目标 UUID 全部对应时，通过当前导航的服务器响应绑定身份；支持 URL-encoded 和 multipart 表单，以及同站 HTTP 重定向。读取实际响应正文并解析目标座位，记录 `identity_method=official-post-response`。报告自带的明确编号冲突仍拒绝。
离线 HTML 没有这些请求记录时，继续拒绝直接赋予牌谱身份，保留本地原有 ref/完整牌谱内容校验。仅凭打开报告的标签页地址不能验证身份。脚本或跨站跳转、无法追溯的弹窗仍会安全拒绝。
真实 HTML 与用户本地解析器已在模拟提交中验证；未在真实 Mortal 上重新付费跑通整个流程。

## 新标签页报告

`context-response-v3` 使用 BrowserContext 级响应监听，覆盖提交到新标签页时的第一条请求。不会依赖请求的 Frame 对象（新标签页第一次导航时可能尚不可用）。先验证实际 POST/重定向链，再读取其 HTTP 200 响应正文。清理时移除监听，避免下一场误用之前的响应。相关测试包含同页/新标签页、URL-encoded/multipart 表单及字段冲突；真实上传 HTML 与用户本地解析器在模拟的新标签页流程验证通过。
替换后可在启动目录运行：

```powershell
.\.venv\Scripts\python.exe -c "from rating.mortal import MORTAL_FLOW_VERSION; print(MORTAL_FLOW_VERSION)"
```

应显示 `context-response-v3`，然后才重新启动网页服务进行单场测试。
