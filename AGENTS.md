# 项目开发约定

## 文风

- `README.md`、`AGENTS.md` 和笔记模板使用中文；`SKILL.md` 和 `references/` 等内容均使用简洁的英文。
- 每个正文段落写在同一行，让显示工具自动换行，不用手动断行。
- 以最新、唯一版本的口吻描述完整功能，让文档与当前实现保持同步，不要有歧义或旧信息。用正向、直接的表述说明行为和用途，不要在段落末尾添加多余内容，如辩解性的内容、删去也不影响内容的文字等。
- 目录与文件名称简短、直观，README 写安装和产品决策，`SKILL.md` 写 Agent 的命令与流程，`references/files.md` 写文件格式，`references/notes.md` 写笔记模板，本文件写开发约束与代码结构。

## 实现范围

- 平台集成范围为课程元数据、视频、字幕和 PPT 截图，以及 zy-eclass 的只读作业、考试、提交状态和截止时间查询。
- 功能按输入输出解耦，文档说明能力与必要依赖，调用顺序和组合由 Agent 根据任务决定。脚本承担省事的机械处理，如拼图和相似分组，采用简单默认值与显式选项。
- 命令行统一在 `skills/yanhekit/scripts/yanhekit.py`，依赖集中写在它的 PEP 723 头部；其余模块只提供函数，新功能以子命令加入。
- 脚本在各平台和 Harness 中直接可用：文本读写显式指定 UTF-8，需要用户打开的地址打印到输出。
- 命令输出供 Agent 和人阅读：每项一行，从属内容缩进，课次内的路径相对课次目录。
- 工作区结构、产物路径和笔记模板固定遵循 `SKILL.md` 与 `references/`；Agent 在该框架内选择工具、分析方式和内容深度。
- 改动保持精小，采用单一实现路径；辅助函数以实际复用和清晰职责为依据。优先使用成熟库，但不要使用 Playwright 这样的大型依赖。
- 验证限于改动涉及的实际行为，测试与校验以功能必需为准。
- 登录交互和凭证处理完整收敛在本地 UI 与后端。
- Agent 结合课程上下文、PPT 和音频完成转录的语义修订。

## 本地 UI

- 本地页面是快速操作和查看下载情况的辅助窗口，能力以命令行为准，页面只做选择和展示。后端用 Flask，状态经 SSE 推送；`/api/` 校验 Host 和启动时生成的 token。
- 登录卡片的高度由 `useHeight` 测量并过渡，各步骤横向滑动；首次状态到达后显示页面，动画用于后续状态切换。
- 下载行参照 macOS 下载列表：细进度条、一行状态文字，右侧是暂停/继续/重试和取消两个圆形小按钮。

## 命令

- 前端开发：在 `ui/` 运行 `pnpm dev`，再用 `YANHEKIT_DEV=http://localhost:5173 uv run skills/yanhekit/scripts/yanhekit.py ui` 启动后端，Vite 把 `/api` 代理到 5174 并提供热更新。
- 前端提交前运行 `pnpm format` 和 `pnpm check`，再用 `pnpm build` 把静态产物输出到 `skills/yanhekit/scripts/ui/`。
- Python 使用 `uvx ruff format skills/yanhekit/scripts` 和 `uvx ruff check skills/yanhekit/scripts`，风格由 `ruff.toml` 规定。
- eclass 回归验证：`uv run --with-requirements <(uv export --script skills/yanhekit/scripts/yanhekit.py --no-hashes) python -m unittest discover -s skills/yanhekit/scripts -p 'test_*.py'`；测试使用合成 Cookie 和模拟接口，不访问账户、浏览器或真实平台。
