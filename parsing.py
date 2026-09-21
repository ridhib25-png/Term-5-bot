"""Shared parsing logic: turns raw timetable rows (from either Excel or a
live Google Sheet CSV export) into the clean schedule.json structure."""

import re
from datetime import datetime

KNOWN_CODES = [
    # B&FS's own subjects (selectable this term)
    'DMAM', 'ALLM', 'BDA', 'BF', 'PA', 'IB', 'LRE', 'BV', 'FM', 'PM', 'GENAI',
    # Other sections' subjects — not selectable by B&FS students, but still
    # need to be recognized so they're correctly filtered out per-user
    # instead of leaking through as generic "event" text shown to everyone.
    'BM', 'IMC', 'SNA', 'CRM', 'RM', 'BAD', 'SSP', 'CS', 'DBS', 'FRA', 'MR',
]

# Some subjects appear as a full written-out phrase in the sheet instead of a
# short code (e.g. "Project Management", "Gen AI", "ESG & Sustainable
# Finance"). These are matched by phrase, case-insensitively, and mapped to
# a short canonical code for consistency with everything else. Both British
# and American spellings of Financial Modelling/Modeling are covered since
# the sheet uses "Modeling" this term but "Modelling" last term.
PHRASE_CODES = {
    'Project Management': 'PM',
    'Gen AI': 'GENAI',
    'ESG & Sustainable Finance': 'ESG',
    'Financial Modeling': 'FM',
    'Financial Modelling': 'FM',
    'E-commerce': 'ECOM',
    'Storytelling': 'STORY',
}

SESSION_TIMES = {
    'S1': '8:30 - 10:00', 'S2': '10:15 - 11:45', 'S3': '12:00 - 1:30',
    'S4': '2:30 - 4:00', 'S5': '4:15 - 5:45', 'S6': '6:00 - 7:30'
}

ALIAS = {}
# NOTE (Term V): subject codes and formatting changed completely from Term
# IV — batches now sometimes appear as a bare trailing number ("IB 1", "BF 2")
# instead of the word "Batch", and quizzes use "Q-2" as well as "Quiz-2".
# Both are handled below. "NAM" (a college event, not a subject) is
# intentionally left out of KNOWN_CODES so it falls through as a plain event
# notice rather than a selectable subject.


def clean_date(val):
    """Accepts a datetime, or a string like '22nd Jun 2026' / '22-Jun-26'."""
    if isinstance(val, datetime):
        return val.date().isoformat()
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    s = re.sub(r'(\d+)(st|nd|rd|th)', r'\1', s)
    s = s.replace('July', 'Jul').replace('Sept', 'Sep')
    for fmt in ('%d %b %Y', '%d %B %Y', '%d-%b-%y', '%d-%b-%Y', '%Y-%m-%d', '%m/%d/%Y'):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def split_entries(text):
    if not text:
        return []
    text = str(text).replace('\n', ' ')
    parts = re.split(r',(?![^(]*\))', text)
    return [p.strip() for p in parts if p.strip()]


def extract_quiz(text):
    """Detects a quiz mention embedded inside a cell — e.g. a class cell for
    one subject that also announces a quiz for a DIFFERENT subject, like
    "SM(Post Mid term) Prof. A.Khanna RM Quiz-2 Time: 1.35 p.m Venue: Orion
    Class". Returns the quiz's own subject/time/venue, separate from
    whatever the main class in that cell is. Also matches the shorter "Q-2"
    style used this term (e.g. "FM Q-2")."""
    codes_by_len = sorted(KNOWN_CODES, key=len, reverse=True)
    pattern = r'\b(' + '|'.join(re.escape(c) for c in codes_by_len) + r')\b(?:-?I{1,3})?[\s\-]{0,4}(?:Quiz|Q)\b[\s\-]?(\d*)'
    m = re.search(pattern, text)
    if not m:
        return None
    code = m.group(1)
    canonical = ALIAS.get(code, code)
    quiz_num = m.group(2)
    label = f"Quiz-{quiz_num}" if quiz_num else "Quiz"

    time_str = None
    tm = re.search(r'Time[:\s]*([\d.:]+\s*[ap]\.?m\.?)', text, re.I)
    if tm:
        time_str = tm.group(1).strip()
    else:
        tm2 = re.search(r'@\s*([\d.:]+\s*[ap]\.?m\.?)', text, re.I)
        if tm2:
            time_str = tm2.group(1).strip()

    venue_str = None
    vm = re.search(r'Venue[:\s]*([A-Za-z0-9\-\s.]+)', text, re.I)
    if vm:
        venue_str = vm.group(1).strip()

    return {'subject': canonical, 'label': label, 'time': time_str, 'venue': venue_str, 'raw': text}


def parse_entry(text, source):
    # Find the code that appears EARLIEST in the actual text — not the first
    # one in KNOWN_CODES order. (Bug found: a cell like "SM(...) RM Quiz-2..."
    # was mislabeled as RM just because RM was earlier in the list, when SM
    # is clearly the actual class and RM is just a quiz mentioned in passing.)
    code = None
    code_match_end = None
    earliest_pos = None
    for c in KNOWN_CODES:
        m = re.search(r'\b' + re.escape(c) + r'\b', text)
        if m and (earliest_pos is None or m.start() < earliest_pos):
            code = c
            earliest_pos = m.start()
            code_match_end = m.end()
    for phrase, short_code in PHRASE_CODES.items():
        m = re.search(re.escape(phrase), text, re.I)
        if m and (earliest_pos is None or m.start() < earliest_pos):
            code = short_code
            earliest_pos = m.start()
            code_match_end = m.end()

    batch = None
    if code_match_end is not None:
        # Batch info appears right after the code — either the word "Batch"
        # ("BV Batch 1") or, this term, just a bare trailing number
        # ("IB 1", "BF 2"). Anchored tightly to the code so we never pick up
        # an unrelated number later in the text (like a floor number).
        after = text[code_match_end:code_match_end + 20]
        m = re.search(r'^[\s\-]*(?:Batch\s*-?\s*)?([IVX]+|\d+)\b', after, re.I)
        if m:
            b = m.group(1).upper()
            batch = {'1': 'I', '2': 'II', '3': 'III'}.get(b, b)
    prof = None
    m = re.search(r'Prof\.?\s*([A-Za-z.\s/&]+?)(?:\(|$)', text)
    if m:
        prof = m.group(1).strip().rstrip(',')
    venue = None
    parens = re.findall(r'\(([^)]+)\)', text)
    for p in parens:
        if re.search(r'\bfloor\b|\baudi\b|\borion\b|\bclassroom\b|\bitc\b|\bblock\b|\broom\b|\blab\b', p, re.I):
            venue = p.strip()
            break
    if not venue:
        m = re.search(r'Venue:\s*([A-Za-z0-9\-\s.]+)', text, re.I)
        if m:
            venue = m.group(1).strip()

    canonical = ALIAS.get(code, code) if code else None
    quiz = extract_quiz(text)

    return {
        'raw': text,
        'code': code,
        'canonical': canonical,
        'batch': batch,
        'prof': prof,
        'venue': venue,
        'source': source,
        'quiz': quiz,
    }


def score(e):
    return (1 if e['venue'] else 0) + (1 if e['prof'] else 0) + (1 if e['batch'] else 0)


def build_schedule(row_sources):
    """row_sources: list of (rows, source_label) where rows is a list of
    {'date': iso date str, 'day': str, 'sessions': {sname: raw_cell_text}}"""
    schedule = {}

    for rows, source in row_sources:
        for row in rows:
            d = row['date']
            if not d:
                continue
            if d not in schedule:
                schedule[d] = {'day': row['day'], 'sessions': {s: [] for s in SESSION_TIMES}}
            for sname, celltext in row['sessions'].items():
                for entry_text in split_entries(celltext):
                    parsed = parse_entry(entry_text, source)
                    schedule[d]['sessions'][sname].append(parsed)

    for d, block in schedule.items():
        for sname, entries in block['sessions'].items():
            best = {}
            events = []
            quizzes = {}
            for e in entries:
                if e.get('quiz'):
                    q = e['quiz']
                    qkey = (q['subject'], q['label'])
                    if qkey not in quizzes:
                        quizzes[qkey] = q
                if e['code'] is None:
                    events.append(e['raw'])
                    continue
                key = (e['canonical'], e['batch'])
                if key not in best or score(e) > score(best[key]):
                    best[key] = e
            block['sessions'][sname] = {
                'time': SESSION_TIMES[sname],
                'classes': list(best.values()),
                'events': sorted(set(events)),
                'quizzes': list(quizzes.values()),
            }

    return schedule
