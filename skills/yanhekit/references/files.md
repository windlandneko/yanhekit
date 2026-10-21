# Workspace Files

## course.json

```json
{
  "course_id": "10001",
  "title": "示例课程",
  "teacher": "王老师",
  "term": "2026-2027-1",
  "lectures": [
    {"id": "09月23日第3-5节", "sub_id": "200001", "duration_s": 8100.0},
    {"id": "10月01日第3-5节", "sub_id": "200002", "dropped": true}
  ]
}
```

`lectures` lists every replay in class order. `id` is the folder name, built from the platform title (`2026-9-23第3-5节`) and numbered when another lecture has it (`补课 2`), and `sub_id` the platform's replay ID; a lecture keeps its `id` when the platform renames it. `duration_s` appears once the lecture is downloaded, and `dropped` marks an empty lecture whose folder `drop` deleted.

## Transcript

`.cache/transcript.tsv` holds the BCut transcript, tab-separated with a `start_s`, `end_s`, `text` header and rows ordered by start. `字幕.txt` is written from `transcript.tsv`, one `[00:02:30] text` line per segment.

## Slides

`slides/` holds the slides extracted from the screen recording, and `slides/index.json` lists them in time order; each stays on screen until the next one's time:

```json
[{"file": "001.jpg", "time_s": 0.0}, {"file": "002.jpg", "time_s": 96.0}]
```

## Unfinished downloads

A name with `.part` before its suffix is still being written, and `slides/` without `index.json` is extracted again. `.cache/录播-教室.parts/` holds the finished parts of an interrupted recording until the next `download` completes it.
