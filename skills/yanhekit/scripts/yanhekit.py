# /// script
# requires-python = '>=3.11'
# dependencies = [
#   'beautifulsoup4>=4.12',
#   'flask>=3.1',
#   'imageio-ffmpeg>=0.6',
#   'img2pdf>=0.6',
#   'keyring>=25',
#   'm3u8>=6',
#   'numpy>=2',
#   'pillow>=11',
#   'pycryptodome>=3.23',
#   'requests>=2.32',
#   'tenacity>=9',
# ]
# ///
"""Yanhekit: download, transcribe, query and review slides."""

import argparse
import math
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import download
import transcribe
import transcript
from client import FAILURES, SORTS, Yanhekt, describe, upgrade_to_campus
from workspace import (
  VIEWS,
  clock,
  count,
  course_folder,
  drop,
  holdings,
  label,
  lectures,
  log,
  parse_time,
  read_index,
  read_tsv,
  recordings,
  select,
  slide_index,
  sync_index,
  timeline,
)


def signed_in():
  upgrade_to_campus()
  client = Yanhekt()
  if not client.authenticate():
    import server

    server.sign_in(client)
  return client


def course_id(value):
  """Accept a numeric course ID or a coursedetail URL."""
  if value.isdigit():
    return value
  match = re.search(r'[?&]course_id=(\d+)', value)
  if not match:
    raise argparse.ArgumentTypeError(f'{value!r} is not a course ID or coursedetail URL; `courses` lists the IDs')
  return match[1]


def slide(value):
  return int(Path(value).stem)  # 44, 044.jpg or slides/044.jpg


def pattern(value):
  try:
    return re.compile(value, re.IGNORECASE)
  except re.error as error:
    raise argparse.ArgumentTypeError(f'{value!r} is not a regular expression: {error}') from None


def positive(value):
  number = int(value)
  if number < 1:
    raise argparse.ArgumentTypeError(f'{value} is not a positive number')
  return number


def absolute(value):
  return Path(value).resolve()  # so that `.` too has the lecture ID as its name


def show(folder, index):
  print(f'{folder.name}: {index["term"]}, {index["teacher"]}, {count(len(index["lectures"]), "lecture")}')
  for lecture in index['lectures']:
    print(f'{label(folder / lecture["id"])}: {holdings(lecture, folder / lecture["id"])}')


def by_course(paths):
  """Map each course folder among the targets to its named lectures, or None for all of them."""
  courses = {}
  for path in paths:
    if read_index(path):
      courses[path] = None
    elif read_index(path.parent):
      if courses.get(path.parent, []) is not None:
        courses.setdefault(path.parent, []).append(path.name)
    else:
      raise ValueError(f'{path.name} is not a course or lecture folder; run `add` first')
  return courses


def having(args, check, missing):
  """The target lectures for which `check` holds, or an error saying what to run first."""
  found = [lecture for lecture in lectures(args.paths, args.latest) if check(lecture)]
  if not found:
    raise ValueError(missing)
  return found


def transcripts(args):
  """(lecture, BCut segments or why there are none) for each target lecture."""
  for lecture in lectures(args.paths, args.latest):
    yield (
      lecture,
      read_tsv(timeline(lecture)) if timeline(lecture).exists() else 'no transcript; run `transcribe` first',
    )


def fast_transcripts(args):
  """(lecture, platform segments or why there are none) for each target lecture, fetched online in parallel."""
  client = signed_in()

  def fetch(lecture):
    try:
      return client.fast_transcript(lecture['sub_id'])
    except FAILURES as error:
      return f'fast transcript failed: {describe(error)}'

  for folder, names in by_course(args.paths).items():
    index = sync_index(client.course(read_index(folder)['course_id']), folder)
    chosen = select(index, names, args.latest)
    with ThreadPoolExecutor(8) as pool:
      yield from zip((folder / lecture['id'] for lecture in chosen), pool.map(fetch, chosen))


def parse():
  parser = argparse.ArgumentParser(description=__doc__)
  commands = parser.add_subparsers(dest='command', required=True)
  targets = argparse.ArgumentParser(add_help=False)
  targets.add_argument('paths', nargs='+', type=absolute, metavar='target', help='Course or lecture folders')
  targets.add_argument('--latest', type=positive, metavar='N', help='Only the last N lectures of each course folder')

  commands.add_parser('login', help='Sign in, opening the local sign-in page only when saved credentials fail')
  listing = commands.add_parser('courses', help='List enrolled courses by term: id, title, teacher')
  listing.add_argument('--term', help='Only this term, such as 2026-2027-1')
  lookup = commands.add_parser('search', help='Search every course on the platform, one page per call')
  lookup.add_argument('keyword', nargs='?', default='', help='Words in the course title or teacher name')
  lookup.add_argument('--term', help='Such as 2024-2025-2')
  lookup.add_argument('--college', help='Offering college, such as 计算机学院')
  lookup.add_argument('--sort', choices=SORTS, help='Default: platform order')
  lookup.add_argument('--page', type=int, default=1)
  lookup.add_argument('--limit', type=int, default=50, choices=range(1, 101), metavar='1-100')
  bind = commands.add_parser('add', help='Create or refresh course folders and list their lectures')
  bind.add_argument('courses', nargs='+', type=course_id, metavar='course', help='Course ID or coursedetail URL')
  bind.add_argument('-o', '--output', type=Path, default=Path('.'), help='Workspace (default: current folder)')
  state = commands.add_parser('status', help='List what each lecture holds, without going online')
  state.add_argument('paths', nargs='*', type=absolute, metavar='course', help='Default: every course folder here')
  fetch = commands.add_parser('download', parents=[targets], help='Download lecture resources')
  fetch.add_argument('--classroom', action='store_true', help='Classroom recording')
  fetch.add_argument('--screen', action='store_true', help='Screen recording')
  fetch.add_argument('--slides', action='store_true', help='Slides extracted from the screen recording')
  fetch.add_argument(
    '--transcribe', action='store_true', help='Transcribe each saved recording while the next downloads'
  )
  fetch.add_argument('--overwrite', action='store_true', help='Fetch selected resources again')
  empty = commands.add_parser('drop', help='Delete empty lectures and mark them dropped in course.json')
  empty.add_argument('lectures', nargs='+', type=absolute, help='Lecture folders')

  asr = commands.add_parser('transcribe', parents=[targets], help='Transcribe with BCut into 字幕.txt')
  asr.add_argument('--overwrite', action='store_true', help='Extract and transcribe again')
  fix = commands.add_parser('replace', parents=[targets], help='Fix recurring transcript errors')
  fix.add_argument('-r', dest='pairs', nargs=2, action='append', required=True, metavar=('WRONG', 'RIGHT'))
  attach = commands.add_parser('embed', parents=[targets], help='Embed the transcript into each recording')
  attach.add_argument('--file', type=absolute, help='An .srt or .vtt file instead')
  find = commands.add_parser('query', parents=[targets], help='Print segments, each after the slide on screen')
  find.add_argument(
    '--fast-transcript', action='store_true', help="The platform's own transcript, fetched online; rougher than BCut's"
  )
  find.add_argument('-m', '--match', type=pattern, help='Regular expression, such as 点名|签到; any case')
  find.add_argument('--start', type=parse_time, default=0, help='Seconds or [h:]mm:ss')
  find.add_argument('--end', type=parse_time, default=math.inf, help='Seconds or [h:]mm:ss')

  snap = commands.add_parser('frame', help='Save frames of a recording at given times to frames/')
  snap.add_argument('lecture', type=absolute)
  snap.add_argument('times', nargs='+', type=parse_time, metavar='time', help='Seconds or [h:]mm:ss')
  snap.add_argument('--view', choices=VIEWS, default='classroom')
  grid = commands.add_parser('sheet', parents=[targets], help='Write contact sheets of numbered, timed slides')
  grid.add_argument('--columns', type=int, default=4, help='Fewer columns give larger thumbnails')
  grid.add_argument('--rows', type=int, default=4)
  alike = commands.add_parser('similar', parents=[targets], help='Print groups of near-identical slides')
  alike.add_argument('--distance', type=int, default=12, help='Most differing bits of 256 (default: 12)')
  combine = commands.add_parser('pdf', help='Write 课件.pdf, one slide per page')
  combine.add_argument('lecture', type=absolute)
  combine.add_argument('slides', nargs='*', type=slide, metavar='slide', help='Slide numbers in page order')
  return parser.parse_args()


def main():
  for stream in (sys.stdout, sys.stderr):
    stream.reconfigure(encoding='utf-8')
  args = parse()
  if args.command == 'login':
    signed_in()
    print('Signed in.')
  elif args.command == 'courses':
    courses = [course for course in signed_in().courses() if args.term in (None, course['term'])]
    for course in courses:
      print(f'{course["id"]}  {course["title"]}  {course["teacher"]}  {course["term"]}')
  elif args.command == 'search':
    total, courses = signed_in().search(
      args.keyword, term=args.term, college=args.college, sort=args.sort, page=args.page, per_page=args.limit
    )
    print(f'{count(total, "course")}, page {args.page} of {math.ceil(total / args.limit)}')
    for course in courses:
      print(f'{course["id"]}  {course["title"]}  {course["teacher"]}  {course["term"]}  {course["college"]}'.rstrip())
  elif args.command == 'add':
    client = signed_in()
    for number in args.courses:
      course = client.course(number)
      folder = course_folder(args.output, course)
      show(folder, sync_index(course, folder))
  elif args.command == 'status':
    folders = args.paths or [path.resolve() for path in sorted(Path('.').iterdir()) if read_index(path)]
    if not folders:
      raise ValueError('No course folders here; run `add` first')
    stray = [folder.name for folder in folders if not read_index(folder)]
    if stray:
      raise ValueError(f'Not a course folder: {", ".join(stray)}')
    for folder in folders:
      show(folder, read_index(folder))
  elif args.command == 'download':
    courses = by_course(args.paths)
    client = signed_in()
    views = [view for view in VIEWS if getattr(args, view)]
    failed = 0
    for folder, names in courses.items():
      course = client.course(read_index(folder)['course_id'])
      failed += download.download(
        client,
        course,
        folder,
        names,
        latest=args.latest,
        videos=views,
        slides=args.slides,
        transcribe=args.transcribe,
        overwrite=args.overwrite,
      )
    if failed:
      raise SystemExit(f'{count(failed, "lecture")} incomplete')
  elif args.command == 'drop':
    for lecture in args.lectures:
      drop(lecture)
      print(f'{label(lecture)}: dropped')

  elif args.command == 'transcribe':
    found = having(args, recordings, 'No recordings; run `download --classroom` first')
    failed = transcribe.transcribe(found, args.overwrite)
    if failed:
      raise SystemExit(f'{count(failed, "recording")} failed')
  elif args.command == 'replace':
    found = having(args, lambda lecture: timeline(lecture).exists(), 'No transcripts; run `transcribe` first')
    for lecture in found:
      hits = transcript.replace(lecture, args.pairs)
      done = [f'{wrong} -> {right} {hit}x' for (wrong, right), hit in zip(args.pairs, hits) if hit]
      print(f'{label(lecture)}: {", ".join(done) or "no hits"}')
  elif args.command == 'embed':
    found = having(
      args,
      lambda lecture: recordings(lecture) and (args.file or timeline(lecture).exists()),
      'No recordings with a transcript; run `download --classroom` and `transcribe` first',
    )
    for lecture in found:
      videos = [video for video in recordings(lecture).values() if args.file or not transcript.embedded(video, lecture)]
      if not videos:
        log(lecture, 'recordings already carry the transcript')
        continue
      subtitles = args.file.read_text(encoding='utf-8') if args.file else transcript.srt(lecture)
      for video in videos:
        transcript.embed(video, subtitles, None if args.file else timeline(lecture))
        log(lecture, f'embedded subtitles in {video.name}')
  elif args.command == 'query':
    found = fast_transcripts(args) if args.fast_transcript else transcripts(args)
    gap = False
    for lecture, rows in found:
      lines = [] if isinstance(rows, str) else list(transcript.query(lecture, rows, args.match, args.start, args.end))
      if gap:
        print()
      summary = rows if isinstance(rows, str) else count(len(rows), 'segment')
      print(f'{label(lecture)}: {summary}', *lines, sep='\n')  # a heading, then its lines, as ripgrep groups them
      gap = bool(lines)

  elif args.command == 'frame':
    import slides  # numpy and Pillow take a quarter of a second to load

    for path in slides.frames(args.lecture, args.times, args.view):
      print(f'{label(args.lecture)}: {path.relative_to(args.lecture)}')
  elif args.command in ('sheet', 'similar'):
    import slides

    found = having(args, lambda lecture: slide_index(lecture).exists(), 'No slides; run `download --slides` first')
    for lecture in found:
      entries = slides.pick(lecture)
      if args.command == 'sheet':
        for path in slides.sheets(lecture, entries, args.columns, args.rows):
          print(f'{label(lecture)}: {path.relative_to(lecture)}')
      else:
        groups = slides.similar(lecture, entries, args.distance)
        for group in groups:
          print(
            f'{label(lecture)}: ' + ' '.join(f'{e["file"].removesuffix(".jpg")}@{clock(e["time_s"])}' for e in group)
          )
        if not groups:
          print(f'{label(lecture)}: no similar slides')
  elif args.command == 'pdf':
    import slides

    entries = slides.pick(args.lecture, args.slides)
    print(f'{label(args.lecture)}: wrote {slides.write_pdf(args.lecture, entries).name}, {count(len(entries), "page")}')


if __name__ == '__main__':
  try:
    main()
  except KeyboardInterrupt:
    raise SystemExit(130) from None
  except FAILURES as error:
    raise SystemExit(describe(error)) from None
