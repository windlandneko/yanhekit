"""The course workspace: folders, course.json, timelines and the helpers the other modules share."""

import csv
import json
import math
import re
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager

from imageio_ffmpeg import get_ffmpeg_exe

VIEWS = {'classroom': '教室', 'screen': '屏幕'}


@contextmanager
def replacing(path):
  """Yield a sibling to write instead of `path`, so that `path` appears only once the block completes."""
  path.parent.mkdir(parents=True, exist_ok=True)
  part = path.with_suffix('.part' + path.suffix)  # ffmpeg picks the format from the last suffix
  try:
    yield part
    part.replace(path)
  finally:
    part.unlink(missing_ok=True)


def ffmpeg(*args, **kwargs):
  """Run the ffmpeg build that the imageio-ffmpeg package bundles."""
  return subprocess.run([get_ffmpeg_exe(), '-v', 'error', '-y', *args], check=True, **kwargs)


def read_json(path, default):
  return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def write_json(path, value):
  with replacing(path) as part:
    part.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def read_index(folder):
  return read_json(folder / 'course.json', {})


def file_name(text):
  return re.sub(r'[\x00-\x1f<>:"/\\|?*]', '_', text).strip(' .')


def lecture_id(title):
  """2026-10-8第6-7节 -> 10月08日第6-7节; another title is kept as a file name, and an empty one becomes 课次."""
  match = re.fullmatch(r'\d{4}-(\d+)-(\d+)(第.+节)', title)
  return file_name(f'{int(match[1]):02}月{int(match[2]):02}日{match[3]}' if match else title) or '课次'


def course_folder(workspace, course):
  """The course's folder, named by its title, or by title and ID when another course has the title."""
  folder = workspace / file_name(course['title'])
  if read_index(folder).get('course_id', course['id']) != course['id']:
    folder = folder.with_name(f'{folder.name} {course["id"]}')
  return folder


def merge_lectures(course, index):
  """The current replay list as course.json lectures, keeping the IDs recorded before; a new lecture is numbered when
  another has its ID: 补课, 补课 2."""
  known = {lecture['sub_id']: lecture for lecture in index.get('lectures', [])}
  taken = {lecture['id'] for lecture in known.values()}
  for row in course['replays']:
    if row['sub_id'] not in known:
      name = given = lecture_id(row['title'])
      number = 1
      while name in taken:
        number += 1
        name = f'{given} {number}'
      taken.add(name)
      known[row['sub_id']] = {'id': name, 'sub_id': row['sub_id']}
  return [known[row['sub_id']] for row in course['replays']]


def sync_index(course, folder):
  index = read_index(folder)
  if index.get('course_id', course['id']) != course['id']:
    raise ValueError(f'{folder} belongs to course {index["course_id"]}')
  index = {
    'course_id': course['id'],
    'title': course['title'],
    'teacher': course['teacher'],
    'term': course['term'],
    'lectures': merge_lectures(course, index),
  }
  write_json(folder / 'course.json', index)
  return index


def holdings(lecture, folder):
  """What a lecture has: its length from course.json, then the files in its folder."""
  if lecture.get('dropped'):
    return 'dropped'
  held = [clock(lecture['duration_s'])] if 'duration_s' in lecture else []
  if slides := read_slides(folder):
    held.append(count(len(slides), 'slide'))
  held += [f'{view} video' for view in recordings(folder)]
  held += [name for name, path in (('transcript', timeline(folder)), ('notes', folder / '笔记.md')) if path.exists()]
  return ', '.join(held) or 'nothing saved'


def drop(lecture):
  """Delete an empty lecture's folder, if any, and mark it in course.json, so that its course folder passes over it."""
  index = read_index(lecture.parent)
  entry = next((entry for entry in index.get('lectures', []) if entry['id'] == lecture.name), None)
  if entry is None:
    raise ValueError(f'{lecture.name} is not a lecture in {lecture.parent.name}/course.json')
  if lecture.exists():
    shutil.rmtree(lecture)
  entry['dropped'] = True
  write_json(lecture.parent / 'course.json', index)


def select(index, names=None, latest=None):
  """The course.json lectures with the given IDs, or else those not dropped, or the last `latest` of them."""
  if names is None:
    kept = [lecture for lecture in index['lectures'] if not lecture.get('dropped')]
    return kept[-latest:] if latest else kept
  chosen = [lecture for lecture in index['lectures'] if lecture['id'] in names]
  unknown = set(names) - {lecture['id'] for lecture in chosen}
  if unknown:
    raise ValueError(f'Unknown lecture {", ".join(sorted(unknown))}; run `status` for the list')
  return chosen


def count(number, noun):
  """1 lecture, 2 lectures."""
  return f'{number} {noun}{"" if number == 1 else "s"}'


def lectures(paths, latest=None):
  """Lecture folders; a course folder stands for its lectures as `select` picks them."""
  found = []
  for path in paths:
    if index := read_index(path):
      found += [path / lecture['id'] for lecture in select(index, latest=latest)]
    elif path.is_dir():
      found.append(path)
    else:
      raise ValueError(f'{path.name} is not a course or lecture folder')
  return found


def label(lecture):
  return f'{lecture.parent.name}/{lecture.name}'


PRINTING = threading.Lock()


def log(lecture, message):
  """Print a timestamped line, led by the lecture when given; safe to call from several threads."""
  subject = f'{label(lecture)}: ' if lecture else ''
  with PRINTING:
    print(f'{time.strftime("%H:%M:%S")} {subject}{message}', flush=True)


class Ticker:
  """Report the status of a long step through `say` soon after it is set or poked, again every SLOWEST seconds, and
  never twice within FASTEST seconds; the status is read as it prints. The status is the step's text with the time
  spent on it, or whatever a subclass's `status` gives."""

  SLOWEST, FASTEST = 10, 0.5

  def __init__(self, say, text=None):
    self.say = say
    self.last, self.due = -math.inf, math.inf
    self.running = True
    self.wake = threading.Condition()
    self.thread = threading.Thread(target=self.run, daemon=True)
    if text:
      self.set(text)

  def __enter__(self):
    self.thread.start()
    return self

  def __exit__(self, *exception):
    with self.wake:
      self.running = False
      self.wake.notify()
    self.thread.join()

  def set(self, text):
    self.text, self.start = text, time.monotonic()
    self.poke()

  def status(self):
    spent = time.monotonic() - self.start
    return f'{self.text} ({stamp(spent)})' if spent >= 1 else self.text

  def poke(self):
    """Report as soon as the last report is FASTEST seconds old."""
    with self.wake:
      self.due = min(self.due, self.last + self.FASTEST)
      self.wake.notify()

  def run(self):
    while True:
      with self.wake:
        while self.running and (wait := self.due - time.monotonic()) > 0:
          self.wake.wait(min(wait, self.SLOWEST))
        if not self.running:
          return
        self.last = time.monotonic()
        self.due = self.last + self.SLOWEST
      self.say(self.status())


def recording(lecture, view):
  return lecture / f'录播-{VIEWS[view]}.mp4'


def recordings(lecture):
  """The saved recordings by view, the classroom view first."""
  return {view: path for view in VIEWS if (path := recording(lecture, view)).exists()}


def timeline(lecture):
  """The timed text of the BCut transcript."""
  return lecture / '.cache' / 'transcript.tsv'


def slide_index(lecture):
  return lecture / 'slides' / 'index.json'


def read_slides(lecture):
  """Entries of slides/index.json, or none before the slides are extracted."""
  return read_json(slide_index(lecture), [])


def write_tsv(path, rows):
  """Write (start, end, text) rows by start time, with whitespace collapsed and empty text dropped; return the count."""
  rows = sorted((start, end, ' '.join(text.split())) for start, end, text in rows)
  rows = [row for row in rows if row[2]]
  with replacing(path) as part, part.open('w', encoding='utf-8', newline='') as file:
    writer = csv.writer(file, delimiter='\t', lineterminator='\n')
    writer.writerow(['start_s', 'end_s', 'text'])
    writer.writerows((f'{start:.3f}', f'{end:.3f}', text) for start, end, text in rows)
  return len(rows)


def read_tsv(path):
  with path.open(encoding='utf-8', newline='') as file:
    return [(float(row['start_s']), float(row['end_s']), row['text']) for row in csv.DictReader(file, delimiter='\t')]


def clock(seconds, separator=''):
  """01:02:03, or 01:02:03.456 when given the millisecond separator."""
  hours, rest = divmod(round(seconds * 1000), 3_600_000)
  minutes, rest = divmod(rest, 60_000)
  whole, millis = divmod(rest, 1000)
  text = f'{hours:02}:{minutes:02}:{whole:02}'
  return f'{text}{separator}{millis:03}' if separator else text


def parse_time(value):
  """Parse 150, 2:30 or 1:02:30."""
  return sum(float(part) * 60**power for power, part in enumerate(reversed(value.split(':'))))


def stamp(seconds):
  """02:30, or 01:02:30 from the first hour on."""
  return clock(seconds).removeprefix('00:')
