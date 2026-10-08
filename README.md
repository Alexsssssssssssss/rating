# 雀魂 Rating 笔记

本地浏览器小工具。输入 `fioq421 20`，查询雀魂牌谱屋该用户最近 **20 场四人南**（金南 / 玉南 / 王座南），逐场提交 Mortal，绘制与参考图同结构的趋势图。

## 当前完成情况与真实访问限制

- 已实现用户名精确匹配、同名账号选择、分页、仅四人南过滤、从旧到新排序、串行任务、取消/重试、缓存、历史记录。
- 已实现 Rating / 一致率双轴图、恶手率图、每场 / 累计 PT 图，以及 PNG、SVG、CSV、JSON 导出。
- 已在真实 Mortal 网站验证检讨表单：准确定位 `reviewForm`，指定目标座位、Mortal 引擎、Classic 界面、固定模型 4.1b，并展开高级选项启用 Rating。**尚未通过真实分析结果验证报告获取流程，不能声称完整自动分析已可用。**
- 牌谱屋访问 key 已在发布后的环境中生效。真实查询 `fioq421 20` 成功：账号 ID 22214610，返回 20 场四人南，全部有可用 UUID 和 PT 数据。逐场链接保存在 `.data/fioq421-recent20.csv`。
- 经用户明确授权，已执行环境代理 CA 信任配置并核验证书指纹。Chromium 在可正常打开用户 NSS 证书数据库的执行上下文中已取得 Mortal HTTP 200，TLS 校验保持开启；只读沙箱上下文仍可能报证书错误，需要正常的证书数据库读写权限。真实页面的提交按钮被 Cloudflare Turnstile 验证禁用，尚未提交任何比赛。
- **已取得 `fioq421` 的真实比赛记录，尚未取得真实 Mortal 评分。** 最新一场的实际分析在 Turnstile 验证阶段暂停，未提交。完整评分和最终评分图仍待验证通过后才能生成；测试数据不会代替真实评分。

## 安装与启动

需要 Python 3.12+、Chromium（或 Playwright Chromium）。

```bash
cd /workspace/rating
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
# 若没有系统 Chromium，安装 Playwright 浏览器：
.venv/bin/python -m playwright install chromium
.venv/bin/python -m uvicorn rating.app:app --host 127.0.0.1 --port 8000
```

在自己的本地机器可打开浏览器访问端口 8000。云环境当前仅做本地 HTTP 验证，未部署公开访问地址。当前仓库目录是 `/workspace/rating`，在其他机器上改用实际目录。

### 在本地桌面中完成 Mortal 验证

此云环境无法向你展示 Chromium 窗口来操作 Turnstile。若在自己的桌面机器运行项目，可让 Mortal 浏览器窗口可见，并给正常的网页验证留出时间：

```bash
MORTAL_HEADLESS=0 MORTAL_VERIFICATION_TIMEOUT=180 .venv/bin/python -m uvicorn rating.app:app --host 127.0.0.1 --port 8000
```

仍需在该本地进程中安全设置 `AMAE_BEARER_TOKEN`。输入查询后，若弹出的 Mortal 页面要求人机验证，可在该窗口中人工完成；程序会等待网页正常启用提交按钮，然后继续分析。验证是否需要逐场重复由 Mortal/Cloudflare 决定，不能保证只验证一次。这个有界面的人工协助流程尚未在桌面环境实测，不代表云环境已完成自动分析。

程序使用环境变量；`.env.example` 只是说明模板，不会自动加载。可在环境设置安全注入，或在自己的本地终端设置。不要把真实凭据提交到 Git 或发送到聊天。

### 网络与授权

需要访问 `5-data.amae-koromo.com`、`amae-koromo.sapk.ch`、`mjai.ekyu.moe`。浏览器分析过程中第三方站点可能另请求其资源/服务；仅在观察到实际需求后添加对应域名。

已观察到 Mortal 的验证组件依赖 `challenges.cloudflare.com`；该域名和官方示例报告域名 `gh.ekyu.moe` 已添加到环境配置草稿。保存草稿不代表运行时已应用，也不代表验证已通过。

牌谱屋前端用 `Authorization: Bearer ...` 发送合法验证凭据。本工具支持通过 `AMAE_BEARER_TOKEN` 发送站方提供且适用于此认证方式的凭据；若站方提供的爬取 key 使用其他方式，需按站方文档调整适配器，不猜测凭据格式。此凭据只发送到牌谱屋查询域名，不跟随跨域重定向。

站方邮件已确认使用标准 Bearer token，最多 1 QPS。程序对用户名搜索、分页和重试共享限速，请求间隔至少 1.1 秒。仍仅支持单进程服务；不要同时使用同一个 token 在多台机器或其他脚本中查询，以免合计频率超过限制。token 在环境设置中填入 `AMAE_BEARER_TOKEN` 的安全值，只填 token 本身，不加 `Bearer ` 前缀，程序会自动添加。

Mortal 若遇到人机验证、限流、无法识别表单、缺少 Rating 等情况会停止该批访问，保留已成功结果并标明原因。站点访问控制或凭据缺失时，不会绕过验证。将访问问题记录下来并提供按钮不代表已解决访问问题。

### 使用方式

1. 输入 `用户名 场数`，例如 `fioq421 20`；范围 1–200。
2. 点击“开始分析”。同名用户需选择账号 ID。
3. 结果按旧到新排列。成功评分写入 `.data/cache`，历史任务在 `.data/jobs`，原始 HTML 报告在 `.data/reports`。
4. 勾选“重新分析，忽略缓存”可避免复用旧模型结果。默认固定模型 4.1b，可设置 `MORTAL_MODEL_TAG` 为官网支持的模型；模型选择包含在缓存键中。页面和导出均保留 `model_tag`。导入报告只归属该任务，不写入网站模型缓存，避免模型混用。
5. 在原站手动完成分析后，可对某一场“导入报告”。接受 mjai-reviewer 原始 JSON 或含 Rating 元数据的 HTML，必须有匹配的 `log_id` 和目标 `player_id`。不匹配会拒绝；本地 `-i` 模式生成的报告可能没有 `log_id`，此时无法自动验证，不接受无身份报告。

## 指标口径

- **Rating**：mjai-reviewer 报告的 `review.rating × 100`，范围 0–100。源代码用每步实际动作的 Q 值归一化后取平均，再平方；它不是段位分，也不等于一致率。
- **一致率**：`total_matches / total_reviewed × 100`。界面同时展示逐场算术平均和按总决策数加权的平均。
- **恶手率**：含逐步数据的 JSON 报告中，实际动作 softmax 概率小于 10% / 5% 的决策比例。不能从一致率推算；网站 HTML 没有可验证逐步概率时留空。
- **PT**：直接使用牌谱屋的 `gradingScore`。缺失时留空；不猜测段位相关的分数规则。累计 PT 从所选区间零点开始，遇到缺失后后续累计值也留空。
- 统计只纳入分析成功的场次；失败场次保留原位置，不填零、不用更老的比赛补足成功数。相同最终分按初始座位顺序判定顺位，与牌谱屋前端一致。

## 开发与测试

```bash
cd /workspace/rating
.venv/bin/python -m pytest -q
```

测试包含接口分页与限制、名称匹配、报告校验、数值口径、任务流程/缓存、导入/导出、绘图和 Chromium 表单提交。外部站点在测试中使用明确的测试响应；这些测试通过不等于真实外部服务已打通。

默认只运行一个 worker；不要使用多个 Uvicorn worker，因为队列为单进程内存队列，任务文件为本地 JSON。重启后未完成任务标为中断，可手动重试；正在运行的浏览器进程不随环境快照保存。本工具默认绑定 loopback，用于个人本地使用，未实现公开部署所需的账号认证。

## 结构与来源

`rating/source.py` 查询牌谱屋；`rating/mortal.py` 为网站适配器；`rating/reports.py` 校验报告；`rating/app.py` 提供任务 API；`rating/charts.py` 生成图表；`rating/static` 为中文界面。

接口及指标依据公开上游代码研究，未复制第三方项目源代码：

- [SAPikachu/amae-koromo](https://github.com/SAPikachu/amae-koromo)：`src/data/source/misc.ts`、`records/loader.ts`、`types/record.ts`、`types/gameMode.ts`。
- [Equim-chan/mjai-reviewer](https://github.com/Equim-chan/mjai-reviewer)：`src/review/mortal.rs`、`src/render.rs`、`templates/report.tera`。
- [Mortal](https://github.com/Equim-chan/Mortal)：本地推理另需模型权重，开源代码不包含可直接使用的官方训练权重。
