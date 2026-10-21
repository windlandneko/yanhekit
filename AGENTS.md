# 项目开发约定

## 文风

- `README.md`、`AGENTS.md`、笔记模板和 GitHub 模板用中文，其余文档用简洁的英文；每个段落写在同一行。
- 只描述当前唯一的版本，与实现保持同步；用正向、直接的表述，不写辩解或删去也不影响内容的文字。

## 贡献

- Agent 禁止代替用户撰写 issue 或 PR 的文本，也不能自行提交。提交信息必须由真人撰写并审阅通过，否则会被直接关闭。

## 结构

- README 写安装和产品决策，本文件写开发约束。`skills/yanhekit/` 是主技能：`SKILL.md` 写命令与流程，`references/files.md` 写文件格式，`references/notes.md` 写笔记模板。
- `skills/yhkt-*/` 是只供用户调用的命令技能：中文描述、英文正文，只指向主技能的流程，用 `disable-model-invocation` 和 `agents/openai.yaml` 关闭自动调用。
- 命令行统一在 `scripts/yanhekit.py`，依赖写在它的 PEP 723 头部，新功能以子命令加入。其余模块只提供函数：`client.py` 平台接口，`auth.py` 统一认证，`server.py` 本地登录页，`download.py` 下载，`transcribe.py` BCut 转录，`transcript.py` 字幕查询、替换与嵌入，`slides.py` PPT 提取、拼图与 PDF，`workspace.py` 工作区文件、日志和 `Ticker`。

## 实现

- 平台集成限于课程元数据、视频和字幕，PPT 从屏幕录像中提取。
- 脚本做省事的机械处理，如拼图、相似分组和批量替换，用简单默认值与显式选项。工作区结构和产物路径由 `SKILL.md` 与 `references/` 固定，Agent 在此框架内按请求选择流程、工具和深度。
- 改动精小，采用单一实现路径，辅助函数按实际复用抽取；优先用成熟库，但不用 Playwright 这样的大型依赖。验证限于改动涉及的实际行为。
- 脚本在各平台和 Harness 中直接可用：文本读写指定 UTF-8，需要用户打开的地址打印到输出。
- 输出每行自成一项，以 `课程/课次: ` 开头，不缩进，课次内的路径相对课次目录；`query` 像 ripgrep 一样先列课次再列片段。耗时命令按日志打印，行首带时间；长步骤用 `Ticker` 在状态变化或进度每过 10% 时打印，此外每 10 秒一次，两次至少相隔 0.5 秒，数值在打印时读取。
- 登录依次尝试保存的登录态、统一认证会话和钥匙串密码，都失败时才打开本地登录页；凭证只经过本地页面与后端。

## 登录页

- 本地页面只负责登录。后端用 Flask，状态经 SSE 推送；`/api/` 校验 Host 和启动时生成的 token。
- 登录卡片的高度由 `useHeight` 测量并过渡，各步骤横向滑动。

## 命令

- 前端开发：在 `ui/` 运行 `pnpm dev`，再在登录态失效时用 `YANHEKIT_DEV=http://localhost:5173 uv run skills/yanhekit/scripts/yanhekit.py login` 启动后端，Vite 把 `/api` 代理到 5174 并提供热更新。
- 前端提交前运行 `pnpm format` 和 `pnpm check`，再用 `pnpm build` 把静态产物输出到 `skills/yanhekit/scripts/ui/`。
- Python 提交前运行 `uvx ruff format skills/yanhekit/scripts` 和 `uvx ruff check skills/yanhekit/scripts`，风格由 `ruff.toml` 规定。
