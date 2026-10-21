---
name: yanhekit
description: Download Yanhekt (延河课堂, aita.yanhekt.cn) lecture recordings and transcripts, extract slides from screen recordings, build slide PDFs, transcribe with BCut, embed subtitles, and maintain course notes or answer questions from the material. Use for Yanhekt course links, BIT lecture replays, local lecture recordings, and requests for lecture transcripts, slides, study notes, review outlines or course questions.
license: AGPL-3.0-only
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/yanhekit.py *)
---

# Yanhekit

`<yanhekit>` below is the absolute path of `scripts/yanhekit.py` beside this `SKILL.md`; write it out in each command and run commands from the user's workspace. To install the skills, or when `uv` is missing or its first run cannot download, turn to [Setup](#setup).

Pick the workflow that fits the request. Notes are the product: transcripts only need to be readable enough to work from, so never rewrite them line by line. When a command reports an error, such as HTTP 404 for a recording, pass it on to the user rather than fetching platform URLs yourself, changing the scripts or editing `course.json`.

## Workspace

```text
<course title>/
  course.json          course and lecture index
  课程总结.md           course notes
  复习提纲.md           review outline, when asked for
  10月08日第6-7节/      one folder per lecture, named by its lecture ID
    笔记.md            lecture notes
    录播-教室.mp4       classroom recording
    录播-屏幕.mp4       screen recording, when kept
    字幕.txt            transcript, a [00:02:30] line per segment
    课件.pdf            slides
    slides/            index.json and numbered slides: 001.jpg, 002.jpg, …
    frames/            frames taken at given times: 教室-00-41-20.jpg, …
    .cache/            transcript.tsv, audio.m4a, sheet-001-016.jpg
```

Commands take targets: a course folder stands for its lectures not dropped, and `--latest N` narrows it to the last N; a lecture folder stands for itself, and `download` and `query --fast-transcript` also take one not yet created, named `<course folder>/<lecture ID>` as `status` lists it. [references/files.md](references/files.md) gives the file formats and [references/notes.md](references/notes.md) the note templates.

## Workflows

### Start

1. Run `login` in the background. It signs in with the saved session, the remembered SSO session or the saved password, and only when all fail opens a local sign-in page and prints its URL for the user. Never ask for or handle the password yourself.
2. Find the course with `courses` (enrolled courses, latest term first) or `search` (the whole platform); ask only when several courses match.
3. Run `add <course>` for a new course. For an existing course folder, read `课程总结.md` and run `status` instead.

### Process a course

The default for a course or some of its lectures, with `C` the course folder. A rerun picks up new lectures and skips finished work.

1. Survey the course with `query C --fast-transcript -m '点名|签到|作业|考试'`, which takes seconds.
2. Drop empty lectures, such as holidays, cancelled classes or empty rooms: their fast transcripts have few or no segments, and reading a doubtful one with `query <lecture> --fast-transcript` settles it. List each in `课程总结.md` with its reason.
3. `download C --classroom --slides --transcribe` in the background.
4. Fix recurring BCut errors with one `replace C -r <wrong> <right>...`. BCut turns course terms into homophones and garbles campus names: `延河课堂` is the lecture platform, `乐学` the learning platform and `i北理` the campus app built on 钉钉. Take the terms from the slides, grep the transcripts for their misheard forms, and fix the forms that recur and that the slides or context confirm; leave one-off errors and unclear phrases.
5. `embed C`.
6. Slides and lecture notes as in the [subagent brief](#subagent-brief), a few lectures per subagent, or yourself one lecture at a time.
7. Integrate the reports into `课程总结.md`, and tell the user what was processed and dropped and which assignments, attendance checks and exam hints turned up.

### Check recent lectures

For questions such as "上节课点名了吗" or "最近留了什么作业". For each lecture, read `笔记.md` when it exists, or else `字幕.txt`; for lectures with neither, run `query <course folders> --latest 1 --fast-transcript`, with `-m` for a specific question. Answer with timestamps, and offer to process the course when the fast transcript is too garbled to be sure.

### Slide deck

Run `download <targets> --slides` in the background, then build a reviewed `课件.pdf` per lecture as under [Slides and frames](#slides-and-frames), through subagents when available. Tell the user which lectures have no screen recording.

### Questions and review

Answer from the notes first, then `query` the transcripts and open the slides they point to; cite timestamps and slide images. For a review outline or self-test, process the lectures that lack notes, then write `复习提纲.md` from `课程总结.md` and the lecture notes.

## Subagent brief

Use the cheapest vision-capable subagent with write access. Give it the absolute `<yanhekit>` path, its lecture folders, the path of `references/notes.md`, and these tasks:

1. Slides: build a reviewed `课件.pdf` as under [Slides and frames](#slides-and-frames).
2. Notes: read `字幕.txt` and `query` it to see which slide each passage refers to; take a `frame` of the classroom view where the teacher writes on the board or the transcript is unclear. Write `笔记.md` with the lecture template and leave `字幕.txt` as it is.
3. Report per lecture: page count, one-line overview, and the attendance, assignment and exam items with timestamps.

## Commands

### Courses and downloads

```sh
uv run <yanhekit> login
uv run <yanhekit> courses [--term 2026-2027-1]
uv run <yanhekit> search [<keyword>] [--term 2024-2025-2] [--college 计算机学院] [--sort newest|oldest|popular] [--page 1] [--limit 50]
uv run <yanhekit> add <course>... [-o <workspace>]
uv run <yanhekit> status [<course folder>...]
uv run <yanhekit> download <target>... [--latest N] [--classroom] [--screen] [--slides] [--transcribe] [--overwrite]
uv run <yanhekit> drop <lecture>...
```

`<course>` is a course ID or a coursedetail URL. `courses` and `search` print a course per line: ID, title, teacher, term and, for `search`, college. `search` matches titles and teacher names and returns one page of up to `--limit 100`, so narrow the keyword, term or college rather than paging. `add` and `status` list each lecture with what its folder holds; `status` stays offline and covers every course folder here by default.

`download` refreshes the lecture list first. `--classroom` and `--screen` save those recordings. `--slides` extracts the slides from the screen recording, fetching it for the purpose and removing it afterwards unless `--screen` keeps it; downloads pause while two such recordings wait for extraction. `--transcribe` transcribes as `transcribe` does and needs a kept recording, usually `--classroom`. Slides and transcripts are made in two queues while the next lectures download. Existing files are kept unless you pass `--overwrite`, and an interrupted recording resumes.

A recording is 2–3 GB, so run downloads of recordings and slides in the background. Each output line starts with the time and the lecture, a long step reports its progress at least every 10 seconds, and the run ends with a line per lecture on what it holds.

`drop` deletes a lecture folder and marks it dropped, so its course folder passes over it; naming the lecture folder in `download` fetches it again.

### Transcripts

```sh
uv run <yanhekit> transcribe <target>... [--latest N] [--overwrite]
uv run <yanhekit> replace <target>... -r <wrong> <right> [-r <wrong> <right>]...
uv run <yanhekit> embed <target>... [--file <subtitles>]
uv run <yanhekit> query <target>... [--latest N] [--fast-transcript] [-m 流水线] [--start 2:30] [--end 5:00]
```

`transcribe` uploads the sound track of each lecture's recording to Bilibili’s BCut service, one lecture at a time, and writes `字幕.txt`; lectures with a transcript are skipped. A recording from elsewhere works too, saved as `录播-教室.mp4` in a lecture folder of its own.

`replace` applies the pairs in order to every transcript, prints the hits, and rewrites `字幕.txt`; check a wrong form that is also a real word with `query -m` first. `embed` replaces each recording with a copy whose only subtitle track is the transcript, or the .srt or .vtt `--file` given, without re-encoding, and skips recordings that already carry the current transcript.

`query` prints the segments of the BCut transcript as `[00:02:30] text` lines. `--fast-transcript` fetches the platform's own transcript instead, online and in seconds for any lecture; it is rougher than BCut's, so it serves surveys and quick checks. `-m`/`--match` keeps the segments matching a regular expression in any case, so one call covers alternatives such as `点名|签到|考勤`; matching skips punctuation and fillers such as 嗯 and 啊, and a match may run on into the next two segments. Each lecture starts with a line giving its segment count, or why it has none, and each run of segments follows the slide then on screen, such as `slides/008.jpg`.

### Slides and frames

```sh
uv run <yanhekit> frame <lecture> <time>... [--view classroom|screen]
uv run <yanhekit> sheet <target>... [--columns 4] [--rows 4]
uv run <yanhekit> similar <target>... [--distance 12]
uv run <yanhekit> pdf <lecture> [<slide>...]
```

`frame` saves frames of a saved recording at the given times to `frames/`. `sheet` writes contact sheets of numbered, timed slides to `.cache/`; fewer columns give larger thumbnails, and the original slide shows small text and formulas. `similar` prints each group of near-identical slides on a line, such as `005@00:02:10 044@00:41:20`. `pdf` writes `课件.pdf` from the given slide numbers, such as `5` or `005`, in order, or from all of them.

`slides/` holds every screen the recording settled on, cropped to the slide where it can be found, including desktops, editing views, video playback and each step of a slide built up line by line. For a reviewed deck, go through the sheets and the similar groups, keep the clearest, most complete version of each slide in lecture order, usually the last step of a build-up, and pass the kept numbers to `pdf`. Check the page count and a few pages before delivering.

## Notes

A class spans two or three 45-minute periods with 5-minute breaks; expect stray talk or silence around each break and after the end.

Prioritize what is easy to miss: attendance, assignments and deadlines, exam hints, teacher warnings, platform notices and procedural details. By default, map the content with timestamps rather than retelling it, and go deeper when asked. Write only what the transcript, slides, frames or audio support, cite timestamps and images, and mark doubtful passages `需确认`.

## Setup

To install Yanhekit from its repository, fetch https://github.com/windlandneko/yanhekit with `git clone --depth 1` into a temporary folder, or as https://github.com/windlandneko/yanhekit/archive/refs/heads/main.zip without git. Copy every folder under its `skills/`, names unchanged and replacing older copies, into the user skills directory of the harness you run in:

| Harness | Skills directory | What it adds |
| --- | --- | --- |
| Claude Code | `~/.claude/skills` | `/yhkt-init` and the other commands with argument hints; `allowed-tools` approves the script's commands |
| Codex | `~/.agents/skills` | `$yhkt-init` or `/skills`; `agents/openai.yaml` gives the display name, starter prompt and invocation policy |
| DeepSeek Harness | `~/.dsh/skills` | the commands in its skill catalog, taking precedence over `~/.agents/skills` |
| Another harness | its own user skills directory, or else `~/.agents/skills` | |

Then tell the user to start a new session so that the harness loads the skills.

`uv` fetches Python and the script's dependencies on its first run. They include an ffmpeg build; to use another one, set `IMAGEIO_FFMPEG_EXE` to its path. When the shell does not find `uv`, tell the user and, once they agree, install it with the first of these that works:

1. The official installer: `curl -LsSf https://astral.sh/uv/install.sh | sh` on macOS and Linux, or `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"` on Windows. It places `uv` in `~/.local/bin`, or `%USERPROFILE%\.local\bin` on Windows; call it by that path while the current shell does not find it.
2. When the installer cannot reach GitHub and Python 3 with pip is present: `python3 -m pip install --user uv -i https://pypi.tuna.tsinghua.edu.cn/simple`, with `py` for `python3` on Windows; then write `python3 -m uv` for `uv`.

When the first run stalls or fails while downloading Python or the dependencies, retry it once with these environment variables, set as `$env:NAME = 'value'` in PowerShell:

```sh
UV_PYTHON_INSTALL_MIRROR=https://mirror.nju.edu.cn/github-release/astral-sh/python-build-standalone
UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
```

Use only these sources. When they fail too, give the user the failing command and its error and stop, without trying other download sites, proxies, unofficial builds, disabled certificate checks, `sudo`, `--break-system-packages` or changed system settings.
