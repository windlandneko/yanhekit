# yanhekit - 延河课堂工具包

新版延河课堂（<https://aita.yanhekt.cn>）工具包。~~其实就是个大号 SKILL~~

本工具包提供了以下能力：下载回放视频、字幕和 PPT，整理课件，字幕转录和内嵌，笔记生成。

## 安装

在 Harness 中安装插件：

- Claude Code，在会话中输入：

```
/plugin marketplace add windlandneko/yanhekit
/plugin install yanhekit@yanhekit
```

或在终端中运行 `claude plugin install yanhekit --marketplace windlandneko/yanhekit`。

- Codex，在终端中运行：

```bash
codex plugin marketplace add windlandneko/yanhekit
codex plugin add yanhekit@yanhekit
```

也可以只运行第一条，再在插件目录中安装。

- 其他 Harness：通过 SKILL 方式安装，即 `npx skills add windlandneko/yanhekit -g`，或把 `skills/yanhekit/` 复制到对应目录。

第一次使用时，如果电脑上还没有 [uv](https://docs.astral.sh/uv/)，Agent 会运行插件自带的安装脚本把它装好，Python 和全部依赖（包括 ffmpeg）随后由 uv 自动下载。也可以不用 uv：在 Python 3.11+ 虚拟环境中安装 `skills/yanhekit/scripts/yanhekit.py` 头部列出的包，用这个环境的 Python 运行脚本。

## 使用

在你最喜欢的 Harness 应用（比如 Codex、Claude Desktop、DeepSeek Harness）中打开工作区与 Agent 对话，就可以通过本插件检索材料、整理笔记和回答课程问题了。以后只需打开同一个工作区即可，不同课程和课次的材料会自动归类整理，可以直接点开查看。

例如，你可以这么问：

- 上节课点我名了吗？
- 把昨天的作业帮我做了
- 明天就要考试了一章没看怎么办，帮帮我

另外还可以让 Agent 在浏览器打开管理页面，手动查看和下载课程资源。

## 技术细节

- **登录**：在自制的本地网页中完成登录和二次验证，Agent 不会读到密码。其中登录态保存在 `~/.config/yanhekit/` 和系统钥匙串。
- **下载**：自动探测校园内网并直连，可以跑到 100+ MByte/s。Agent 直接查课程列表、下载录播视频、字幕和 PPT，不用再打开 [AutoSlides](https://github.com/bit-admin/Yanhekt-AutoSlides) 下完视频再看，全流程对话就行。
- **整理**：PPT 截图可以合成纯图 PDF；调用 B 站必剪接口转录出比延河课堂自带的更准的字幕，并软嵌入视频。
- **问答与笔记**：Agent 参考字幕文本和 PPT 图片回答课程的具体问题，并为课程和每节课各维护一篇完整的笔记，点名、作业、考试一查便知。

## 免责声明

> 这种东西小范围享受就可以了.jpg

- **用途**：本工具包仅供个人学习和研究使用，不得用于商业或非法用途。
- **课程内容**：录播涉及老师和同学的隐私与版权，下载和使用时请遵守法律法规和学校规定，不要传播给未经授权的第三方，也不要在其他平台公开分享。
- **转录**：BCut 转录会把提取的音频上传到 Bilibili 的服务，请确保你有权使用这些音频。结果仅供参考，可能有错漏，漏掉的作业和知识点别怪我喵。

## 鸣谢

- [bit-admin/AutoSlides](https://github.com/bit-admin/Yanhekt-AutoSlides)
- [Fangwenky/yanhekt-downloader](https://github.com/Fangwenky/yanhekt-downloader)
- [BIT101-dev/BIT-Login](https://github.com/BIT101-dev/BIT-Login)
