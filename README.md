# yanhekit - 延河课堂工具包

新版延河课堂（<https://aita.yanhekt.cn>）工具包。~~其实就是个大号 SKILL~~

本工具包提供了以下能力：下载回放视频和字幕、提取并整理 PPT、字幕转录和内嵌、笔记生成。

交流群：184242558，欢迎进群聊天、提出建议和 bug，第一时间接收新版动态喵～

## 安装

可以直接把本仓库地址发给 Agent，按 SKILL.md 的 Setup 章节安装。

如果想古法手动安装，首先安装 [uv](https://docs.astral.sh/uv/) 和 [npm](https://nodejs.org/zh-cn/download)，然后通过 SKILL 方式安装：

```bash
npx skills add windlandneko/yanhekit -g -s '*'
```

或把 `skills/` 下的各个文件夹复制到所用 Harness 的技能目录：Claude Code 是 `~/.claude/skills`，Codex 是 `~/.agents/skills`，DeepSeek Harness 是 `~/.dsh/skills`。

## 使用

新建一个工作区，直接与 Agent 对话，就可以通过本插件检索材料、整理笔记和PPT、解答课程问题和帮助你期末复习了。以后只需打开同一个工作区即可，不同课程和课次的材料会自动归类整理，可以直接点开文件夹翻阅文档和观看录播。

例如，你可以这么问：

- 上节课点我名了吗？
- 把昨天的作业帮我做了
- 明天就要考试了一章没看怎么办，帮帮我

还可以直接调用命令技能启动常用流程：Claude Code 输入 `/yhkt-init`，Codex 输入 `$yhkt-init`，其他 Harness 在技能列表中选择。这些技能只在你调用时运行。

- `yhkt-init <课程>`：整理一门课。先用平台的快速字幕浏览，删掉放假和空课并记入课程总结；再下载录播、转录、批量修正常见错字并嵌入字幕；最后由 subagent 检查 PPT、写课次笔记，汇总成课程总结。之后再运行只处理新课次。
- `yhkt-sync [课程]`：只看平台的快速字幕，查最近课次的点名、作业、考试和通知。
- `yhkt-ppt <课程> [课次]`：从屏幕录像提取 PPT，整理成去重后的课件 PDF。
- `yhkt-review <课程> [范围]`：根据笔记写复习提纲和自测题。

## 技术细节

- **登录**：依次尝试保存的登录态、统一身份认证的会话和钥匙串中的密码，都失效时才弹出本地网页完成登录和二次验证，Agent 不会读到密码。登录态保存在 `~/.config/yanhekit/`，密码默认在系统钥匙串。
- **下载**：自动探测校园内网并直连，可以跑到 ~90 MByte/s。Agent 直接查课程列表、搜索全平台课程、下载录播视频和字幕，全流程对话就行。
- **整理**：PPT 从屏幕录像自动提取并裁剪，Agent 看图去重后合成纯图 PDF；调用 B 站必剪接口转录出比延河课堂自带更准的字幕，批量替换课程术语的常见错字后软嵌入视频。
- **问答与笔记**：Agent 参考字幕文本和 PPT 图片回答课程的具体问题，并为课程和每节课各维护一篇完整的笔记，点名、作业、考试一查便知。

## 免责声明

> 这种东西小范围享受就可以了.jpg

- **用途**：只处理你本人有权访问的课程，不得用于非法用途。
- **课程内容**：录播涉及老师和同学的隐私与版权，下载和使用时请遵守法律法规和学校规定，不要传播给未经授权的第三方，也不要在其他平台公开分享。
- **转录**：BCut 转录会把提取的音频上传到 Bilibili 的服务，请确保你有权使用这些音频，结果仅供参考：转录可能存在不少错误，漏掉的作业和知识点别怪我喵。

## 鸣谢

- [bit-admin/AutoSlides](https://github.com/bit-admin/Yanhekt-AutoSlides)
- [Fangwenky/yanhekt-downloader](https://github.com/Fangwenky/yanhekt-downloader)
- [BIT101-dev/BIT-Login](https://github.com/BIT101-dev/BIT-Login)

## 许可

本项目以 [AGPL-3.0](LICENSE) 开源。分发修改版，或以网络服务的形式提供给他人使用时，须以同一许可证公开完整源码。
