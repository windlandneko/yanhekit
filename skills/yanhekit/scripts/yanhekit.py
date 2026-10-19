# /// script
# requires-python = '>=3.11'
# dependencies = [
#   'beautifulsoup4>=4.12',
#   'flask>=3.1',
#   'imageio-ffmpeg>=0.6',
#   'img2pdf>=0.6',
#   'keyring>=25',
#   'm3u8>=6',
#   'pillow>=11',
#   'pycryptodome>=3.23',
#   'requests>=2.32',
#   'tenacity>=9',
# ]
# ///
"""Yanhekt lecture material: download, transcribe, query and review slides."""

import argparse
import math
import subprocess
import sys
from itertools import groupby
from pathlib import Path

import requests

import download
import transcribe
import transcript
from client import STREAMS, APIError, Yanhekt, course_id, describe, upgrade_to_campus


def signed_in():
  client = Yanhekt()
  if not client.authenticate():
    import server  # Flask and the sign-in stack load only when the page is needed

    server.serve(client)
  return client


def select(course, lectures):
  """Map lecture IDs or sub_ids to sub_ids; none given means every replay, as `--all` asks."""
  known = {download.lecture_id(row['title']): row['sub_id'] for row in course['replays']}
  if not known:
    raise ValueError(f'{course["title"]} has no replays')
  if not lectures:
    return list(known.values())
  unknown = [name for name in lectures if name not in known and name not in known.values()]
  if unknown:
    raise ValueError(f'Unknown lecture {", ".join(unknown)}; run `lectures` for the list')
  return [known.get(name, name) for name in lectures]


class Printer:
  """Download progress for a log: each lecture's ID, each video at every quarter, then what the lecture holds."""

  def __init__(self):
    self.lecture, self.task, self.quarter = None, None, 0

  def __call__(self, lecture, task, fraction=0, speed=None, remaining=None):
    if lecture['id'] != self.lecture:
      self.lecture = lecture['id']
      print(lecture['id'], flush=True)
    if task != self.task:
      self.task, self.quarter = task, 0
    if task in STREAMS and self.quarter < int(fraction * 4) < 4:
      self.quarter = int(fraction * 4)
      left = f'  {transcript.stamp(remaining)} left' if remaining else ''
      print(f'  {task} video {fraction:.0%}  {speed / 1e6:.0f} MB/s{left}', flush=True)
    elif task == 'done':
      held = [f'{lecture[name]} {name}' for name in ('subtitles', 'slides') if name in lecture]
      held += [f'{view} video' for view in lecture.get('videos', [])]
      print('  ' + '  '.join([transcript.clock(lecture['duration_s']), *held]), flush=True)


def capture(value):
  return int(Path(value).stem)  # 44, 044.jpg or slides/044.jpg


def absolute(value):
  return Path(value).resolve()  # so that `.` too has the lecture ID as its name


def parse():
  parser = argparse.ArgumentParser(description=__doc__)
  commands = parser.add_subparsers(dest='command', required=True)
  commands.add_parser('login', help='Sign in through the local page unless the saved session is still valid')
  listing = commands.add_parser('courses', help='List enrolled courses by term: id, title, teacher')
  listing.add_argument('--term', help='Only this term, such as 2026-2027-1')
  replays = commands.add_parser('lectures', help='List a course’s replays: lecture ID and sub_id')
  replays.add_argument('course', type=course_id, help='Course ID or coursedetail URL')
  fetch = commands.add_parser('download', help='Download lecture resources and update course.json')
  fetch.add_argument('course', type=course_id, help='Course ID or coursedetail URL')
  chosen = fetch.add_mutually_exclusive_group(required=True)
  chosen.add_argument('-l', '--lecture', action='append', help='Lecture ID or sub_id; repeat it for several')
  chosen.add_argument('--all', action='store_true', help='Every replay of the course')
  fetch.add_argument(
    '--video', nargs='*', choices=STREAMS, help='Recording views, teacher and screen (default: teacher)'
  )
  fetch.add_argument('--subtitles', action='store_true', help='Platform subtitles as .cache/subtitles.tsv')
  fetch.add_argument('--slides', action='store_true', help='Timed PPT captures in slides/')
  fetch.add_argument('--pdf', action='store_true', help='Combine every capture into 课件.pdf')
  fetch.add_argument('--connections', type=int, default=16)
  fetch.add_argument('--overwrite', action='store_true', help='Fetch selected resources again')
  fetch.add_argument('-o', '--output', type=Path, default=Path('.'), help='Workspace (default: current folder)')
  page = commands.add_parser('ui', help='Open the download page and keep it running')
  page.add_argument('course', nargs='?', type=course_id, help='Course to open first')
  page.add_argument('-o', '--output', type=Path, default=Path('.'), help='Workspace (default: current folder)')

  asr = commands.add_parser('transcribe', help='Transcribe recordings with BCut into .cache/transcript.tsv')
  asr.add_argument('paths', nargs='+', type=absolute, help='Lecture folders, course folders or .mp4 recordings')
  asr.add_argument('--overwrite', action='store_true', help='Extract and transcribe again')
  text = argparse.ArgumentParser(add_help=False)
  text.add_argument('lecture', type=absolute)
  text.add_argument('--source', choices=['transcript', 'subtitles'], default='transcript')
  find = commands.add_parser('query', parents=[text], help='Print matching segments, each after the capture on screen')
  find.add_argument('--keyword')
  find.add_argument('--start', type=transcript.seconds, default=0, help='Seconds or [h:]mm:ss')
  find.add_argument('--end', type=transcript.seconds, default=math.inf, help='Seconds or [h:]mm:ss')
  render = commands.add_parser('export', parents=[text], help='Write 文字稿.txt, 字幕.srt or 字幕.vtt')
  render.add_argument('--format', choices=['txt', 'srt', 'vtt'], action='append')
  attach = commands.add_parser(
    'embed', help='Add a subtitle file to a recording as a soft track, replacing earlier ones'
  )
  attach.add_argument('video', type=absolute, help='Recording to replace, such as 录播-教师.mp4')
  attach.add_argument('subtitles', type=absolute, help='.srt or .vtt file')

  grid = commands.add_parser('sheet', help='Write contact sheets of numbered, timed thumbnails to .cache/')
  grid.add_argument('lecture', type=absolute)
  grid.add_argument('captures', nargs='*', type=capture, help='Capture numbers; default: every capture')
  grid.add_argument('--columns', type=int, default=4, help='Fewer columns give larger thumbnails')
  grid.add_argument('--rows', type=int, default=4)
  alike = commands.add_parser(
    'similar', help='Print groups of near-identical captures with their times, one group a line'
  )
  alike.add_argument('lecture', type=absolute)
  alike.add_argument('--distance', type=int, default=12, help='Most differing bits of 256 (default: 12)')
  combine = commands.add_parser('pdf', help='Write 课件.pdf, one capture per page')
  combine.add_argument('lecture', type=absolute)
  combine.add_argument('captures', nargs='*', type=capture, help='Capture numbers in page order; default: all')
  return parser.parse_args()


def main():
  for stream in (sys.stdout, sys.stderr):
    stream.reconfigure(encoding='utf-8')
  args = parse()
  if args.command in ('login', 'courses', 'lectures', 'download', 'ui'):
    upgrade_to_campus()

  if args.command == 'ui':  # the page signs in itself
    import server

    server.serve(Yanhekt(), args.output, args.course)
  elif args.command == 'login':
    signed_in()
    print('Signed in.')
  elif args.command == 'courses':
    courses = [course for course in signed_in().courses() if args.term in (None, course['term'])]
    for number, (term, group) in enumerate(groupby(courses, key=lambda course: course['term'])):
      print(term + ('  latest term' if number == 0 and args.term is None else ''))
      for course in group:
        print(f'  {course["id"]}  {course["title"]}  {course["teacher"]}')
  elif args.command == 'lectures':
    course = signed_in().course(args.course)
    print(f'{course["title"]}  {course["term"]}  {course["teacher"]}')
    for row in course['replays']:
      print(f'  {download.lecture_id(row["title"])}  {row["sub_id"]}')
  elif args.command == 'download':
    client = signed_in()
    course = client.course(args.course)
    saved = download.download(
      client,
      course,
      args.output,
      select(course, args.lecture),
      videos=() if args.video is None else dict.fromkeys(args.video or ['teacher']),
      subtitles=args.subtitles,
      slides=args.slides,
      pdf=args.pdf,
      connections=args.connections,
      overwrite=args.overwrite,
      report=Printer(),
    )
    print(f'Saved to {saved}')

  elif args.command == 'transcribe':
    failed = transcribe.transcribe(args.paths, args.overwrite)
    if failed:
      raise SystemExit(f'{failed} recordings failed')
  elif args.command == 'query':
    for line in transcript.query(args.lecture, args.source, args.keyword, args.start, args.end):
      print(line)
  elif args.command == 'export':
    for format in args.format or ['txt']:
      print(transcript.export(args.lecture, format, args.source).name)
  elif args.command == 'embed':
    transcript.embed(args.video, args.subtitles)
    print(args.video.name)

  else:
    import slides

    entries = slides.captures(args.lecture, getattr(args, 'captures', ()))
    if args.command == 'sheet':
      for path in slides.sheets(args.lecture, entries, args.columns, args.rows):
        print(path.relative_to(args.lecture))
    elif args.command == 'similar':
      for group in slides.similar(args.lecture, entries, args.distance):
        print('  '.join(f'{entry["file"].removesuffix(".jpg")}@{transcript.stamp(entry["time_s"])}' for entry in group))
    else:
      print(f'{slides.pdf(args.lecture, entries).name}  {len(entries)} pages')


if __name__ == '__main__':
  try:
    main()
  except KeyboardInterrupt:
    raise SystemExit(130) from None
  except (APIError, ValueError, OSError, requests.RequestException, subprocess.CalledProcessError) as error:
    raise SystemExit(describe(error)) from None
