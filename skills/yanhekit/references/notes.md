# Note Templates

Lecture notes go in `<course>/<id>/笔记.md`, the course summary in `<course>/课程总结.md` and a requested review outline in `<course>/复习提纲.md`. Keep the sections and headings, and fill them to the depth the request needs. Maintain one complete version of each document in place: integrate new findings into the relevant sections, consolidate overlapping entries, and correct outdated content while preserving verified material. Do not append update batches, retain revision sections, create versioned copies, or use process wording such as 新增、本次补充 or 第 N 次更新.

- Write `无` under a heading that has nothing.
- Mark doubtful passages `需确认` with their timestamp instead of guessing.
- A dropped lecture has no folder or note; keep its row in 课程索引 with empty cells and the reason in 概述, such as `国庆放假，不上课`.

## Lecture

```markdown
# <id>

- [录播-教室.mp4](录播-教室.mp4)
- [字幕.txt](字幕.txt)
- [课件.pdf](课件.pdf)

## 时间轴

- `00:00:00` 主题或问题：一句话说明为什么值得回看
- `00:02:30` 公式或图示：[PPT](slides/002.jpg)
- `00:41:20` 板书推导：[画面](frames/教室-00-41-20.jpg)

## 关键点

### 考勤/签到/小测

### 作业

### 考试/复习重点

### 其他需要回看的片段

## 内容笔记

### 概念与公式

### 例题与推导

### 复习与自测
```

Link only files that exist; without a PDF, point the PPT line at `slides/`. Under 内容笔记, follow the lecture's main line with its terms, timestamps, slide and frame links. When the content does not fit 概念与公式 and 例题与推导, replace those two with one `###` heading per main topic, named after it and alike across similar lectures, and keep 复习与自测 last.

## Course

```markdown
# <course title> 课程总结

## 复习优先

### 考试/复习重点

### 作业

### 考勤/签到/小测

### 其他

## 课程索引

| 课次 | 笔记 | 视频 | 点名 | 作业 | 考试 | 概述 |
| --- | --- | --- | --- | --- | --- | --- |
| <id> | [笔记](<id>/笔记.md) | [教室](<id>/录播-教室.mp4) [屏幕](<id>/录播-屏幕.mp4) | ✅ | ✅ | ✅ | 一句话概括，不加句号 |

## 课程内容

### <id>

- 时间点：`00:00:00` 主题；`00:42:10` 主题
- PPT：[图片](<id>/slides/<slide>.jpg)

概括本节主线、关键概念、例题或证明位置。
```

In 课程索引, link only files that exist, mark 点名, 作业 and 考试 with ✅ when the lecture has them, and leave a cell empty otherwise. Whenever you write a lecture note, integrate its findings into the summary's index row, 课程内容 entry and relevant 复习优先 items so the summary remains a complete current review dashboard.

## Review

```markdown
# <course title> 复习提纲

范围：<lectures or weeks covered>

## 老师强调

- 考试范围、题型和明确提示，附 `[<id> 00:42:10](<id>/笔记.md)`

## <topic>

- 概念与公式：一句话说明，附时间点和 [PPT](<id>/slides/<slide>.jpg)
- 例题：题目要点和解法思路

## 自测

1. 问题

<details><summary>答案</summary>

答案和出处时间点

</details>
```

Group topics by the course's main line rather than by lecture, and keep the outline consistent with the lecture notes.
