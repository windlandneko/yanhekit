---
name: yhkt-sync
description: 同步：更新本地数据并查看最近的点名、作业、考试和通知，推荐日常运行
argument-hint: "[课程名，默认全部课程]"
disable-model-invocation: true
---

Follow the "Start" and "Check recent lectures" workflows of the yanhekit skill, [../yanhekit/SKILL.md](../yanhekit/SKILL.md), for the course, lectures and requests the user gives with this command.

Without a course, check the latest lecture of each course in the latest term. Report attendance checks, assignments and deadlines, exam hints and notices with timestamps, one course per section, and say plainly when a lecture has none.
