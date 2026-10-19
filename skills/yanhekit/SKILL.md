---
name: yanhekit
description: Download Yanhekt (延河课堂, aita.yanhekt.cn) lecture recordings, platform subtitles and PPT captures, build slide PDFs, transcribe with BCut, embed subtitles, and maintain course notes or answer questions from the material. Use for Yanhekt course links, BIT lecture replays, local lecture recordings, and requests for lecture transcripts, study notes, review outlines or course questions.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/yanhekit.py *)
---

# Yanhekit

`<yanhekit>` in the commands below is the absolute path of `scripts/yanhekit.py` beside this `SKILL.md`; write it out in each command and run from the user's workspace so course material stays there. uv brings Python and every dependency, ffmpeg included. When `uv` is not found, run the setup script beside `yanhekit.py` once and let it finish, which can take a few minutes; it downloads uv itself and no course material: `powershell -NoProfile -ExecutionPolicy Bypass -File <scripts>/setup.ps1` on Windows from any shell, `sh <scripts>/setup.sh` on macOS and Linux, with `<scripts>` written out as an absolute path. It installs uv and prints its path; write that path in place of `uv` until the harness restarts. Run the scripts through uv rather than a Python or environment found elsewhere on the machine; only a Python 3.11+ virtual environment that the user set up with the script's declared dependencies can replace `uv run`.

Use only the steps the request needs. Platform subtitles and slides are often enough for an overview or a targeted question; BCut gives a much better transcript of what was said.

## Workspace

```text
<course title>/
  course.json            course and lecture index
  课程总结.md            course notes
  10月08日第6-7节/       one folder per lecture, named by its lecture ID
    笔记.md              lecture notes
    录播-教师.mp4        teacher view; 录播-屏幕.mp4 is the screen view
    文字稿.txt           readable transcript
    字幕.srt             subtitles, also 字幕.vtt
    课件.pdf             slides
    slides/              index.json and numbered captures: 001.jpg, 002.jpg, …
    .cache/              subtitles.tsv, transcript.tsv, audio.mp3, sheet-001-016.jpg
```

`<lecture>` below is a lecture folder, such as `示例课程/10月08日第6-7节`; commands on it print paths relative to it. [references/files.md](references/files.md) gives the file formats.

## Download

```sh
uv run <yanhekit> login
uv run <yanhekit> courses [--term 2026-2027-1]
uv run <yanhekit> lectures <course>
uv run <yanhekit> download <course> (-l <lecture ID>... | --all) [--subtitles] [--slides] [--video [teacher] [screen]] [--pdf] [-o <workspace>]
```

Start with `login`, which returns at once while the saved session is valid. Otherwise it opens a local sign-in page, prints its URL and waits, so run it in the background when the harness allows and give the user the URL if no browser window appears. The user completes the password, captcha and SMS steps there. Never ask for or handle the password yourself.

`<course>` is a course ID from `courses` or a coursedetail URL. `-l` takes a lecture ID or `sub_id` and can repeat; `--all` selects every replay, so use it only when the user wants the whole course. `download` fetches only what you name:

- `--subtitles` saves the platform subtitles as `.cache/subtitles.tsv`.
- `--slides` saves the timed PPT captures into `slides/`.
- `--video` gets the teacher view; `--video screen` or `--video teacher screen` chooses the views, each saved as its own recording.
- `--pdf` combines every saved capture into `课件.pdf`.

Subtitles and slides take seconds, so fetch them first. Each lecture's last line gives its duration, subtitle segments, captures and recordings. A replay with no captures and few subtitle segments may be empty or have incomplete platform material. Read its subtitles and check the course context before skipping its recording or notes.

A recording is 1–2 GB and can outlast a command timeout, so run video downloads in the background when the harness allows. Progress goes to stdout and retries to stderr, and an interrupted recording resumes from its finished pieces. Existing files are kept unless you pass `--overwrite`. `--connections` (default 16) sets parallel requests.

`ui [<course>] [-o <workspace>]` opens a local page where the user picks lectures and follows the download queue. Offer it only when the user would rather click than say what to fetch; it runs until stopped.

## Transcribe

```sh
uv run <yanhekit> transcribe <lecture | course folder | recording>... [--overwrite]
```

BCut uploads extracted audio to Bilibili’s transcription service and writes `.cache/audio.mp3` and `.cache/transcript.tsv`, from the teacher view when both are saved. Lectures with a transcript are skipped, and a course folder covers every downloaded lecture. A recording from elsewhere works from a folder of its own, passed as its `.mp4` path or saved as `录播-教师.mp4`.

Run `export <lecture>` after transcription to create `文字稿.txt`; `transcribe` saves the timeline only. Read the exported text rather than the TSV, and clean only what the task needs: the raw text is enough to index a lecture and find assignments or exam hints. Polish the passages you quote, or the whole transcript when the user wants clean subtitles. BCut and the platform turn course terms into homophones, such as 赌马 for 补码 or 阿斯玛 for ASCII, and garble campus names: `乐学` is the learning platform and `i北理` the campus app built on 钉钉. Collect the course's terms from the slide sheets and replace a recurring error throughout `.cache/transcript.tsv` in one pass, with `sed` or a short script, checking its hits with `query --keyword` first when the wrong form is also a real word. Edit other errors in place, check doubtful ones against the nearby slides and the audio, then run `export <lecture> --format txt --format srt` and `embed` the new `字幕.srt` into the saved recordings.

## Query, export and embed

```sh
uv run <yanhekit> query <lecture> [--source transcript|subtitles] [--keyword 流水线] [--start 2:30] [--end 5:00]
uv run <yanhekit> export <lecture> [--source transcript|subtitles] [--format txt|srt|vtt]...
uv run <yanhekit> embed <recording> <subtitles>
```

The source is the BCut transcript unless you pass `--source subtitles`. `query` prints matching segments as `[02:30] text` lines, each run after the capture then on screen, such as `slides/008.jpg`; open the slide images for formulas, diagrams and uncertain terms. `export` writes `文字稿.txt` by default, or `字幕.srt` and `字幕.vtt`. `embed` replaces `<recording>`, such as `<lecture>/录播-教师.mp4`, with a copy carrying the `.srt` or `.vtt` file as its subtitle track, without re-encoding and replacing any earlier track. Run it once per recording; the platform subtitles are `export --source subtitles --format srt`.

## Slides

```sh
uv run <yanhekit> sheet <lecture> [<capture>...] [--columns 4] [--rows 4]
uv run <yanhekit> similar <lecture> [--distance 12]
uv run <yanhekit> pdf <lecture> [<capture>...]
```

A capture is its number, such as `44`; `044.jpg` and `slides/044.jpg` work too. `sheet` and `pdf` take every capture when none are given.

`sheet` writes contact sheets of 16 thumbnails labelled with number and time, as `.cache/sheet-001-016.jpg` and so on. Use them to survey a lecture, find the slide a passage refers to, or collect terms for transcript fixes. Fewer columns give larger thumbnails; open the original capture for small text and formulas.

`similar` prints groups of near-identical captures with their times, one group per line, such as `005@02:10  044@41:20  054@55:05`. It is a rough first filter; confirm every choice in the images. Neighbours in a group are usually one slide shown longer or built up step by step, and the last is usually the most complete; distant ones are repeat visits. Small changes, such as another contents item highlighted or one added line, can share a group.

`pdf` writes `课件.pdf` with one capture per page in the given order. When the user asks for slides, build a reviewed deck: go through the sheets and the similar groups, using the cheapest vision-capable subagent when available, and keep the clearest, most complete capture of each slide in lecture order. Drop desktops, transitions, video playback and repeat visits, pass the kept captures to `pdf`, and check the page count and a few pages before delivering.

## Notes and analysis

Maintain `笔记.md` and `课程总结.md` with the templates in [references/notes.md](references/notes.md).

A class spans two or three 45-minute periods with 5-minute breaks. The recording starts on time and may run past the end, so expect stray talk or silence around each break and at the end.

Prioritize what is easy to miss: attendance, assignments and deadlines, exam hints, teacher warnings, platform notices and procedural details. By default, map the content with timestamps rather than retelling it. Go deeper when asked: concept notes, formulas and worked examples, a review outline, cross-lecture answers or self-test questions. Write only what the transcript, slides or audio support, and cite timestamps and slide images.

For many lectures, keep the transcripts out of your own context. Give read-only subagents about three lectures each; each reads the `文字稿.txt` files, and slides where needed, and returns a concise structured report per lecture. Write the notes and the summary yourself from the reports, so the format and voice stay consistent.
