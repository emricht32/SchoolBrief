# extractors.py (new or extend your existing helpers)
import re
from datetime import datetime, timedelta, timezone
from dateutil import parser as dateparser
from urllib.parse import urlparse, parse_qs
from typing import Optional, List, Dict, Any
from bs4 import BeautifulSoup
from .logger import logger

# Lightweight keyword sniffing (tune as you like)
KEY_PATTERNS = {
    "deadline": re.compile(r"\b(due|deadline|submit by|turn in by)\b[:\s]*([^ \n]+.*)", re.I),
    "event": re.compile(r"\b(event|meeting|assembly|performance|concert|field trip|spirit day)\b", re.I),
    "reminder": re.compile(r"\b(reminder|don't forget|remember to)\b", re.I),
}

# Common “date-y” shapes
DATE_PATTERNS = [
    r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\b[^,\n]{0,40}",
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+\d{1,2}(?:,\s*\d{4})?\b",
    r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b",
    r"\b\d{4}-\d{1,2}-\d{1,2}\b",
]

# --- Weekday inference --------------------------------------------------------

_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6
}

def _next_weekday(anchor: datetime, target_idx: int) -> datetime:
    """Next occurrence of this weekday (including today)."""
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone.utc)
    delta = (target_idx - anchor.weekday()) % 7
    return anchor + timedelta(days=delta)

# lines like "Monday: read 10 pages"
_WD_LINE = re.compile(r'^\s*(?P<wd>monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s*[:\-–]\s*(?P<task>.+?)\s*$', re.I)

# child header lines like "Aria" / "Chance"
_CHILD_LINE = re.compile(r'^\s*([A-Z][a-z]{1,30})\s*$')

def _infer_homework_items(text: str, anchor_dt: datetime) -> List[Dict[str, Any]]:
    """
    Parse blocks like:
        Aria
        Monday: Read 10 pages
        Thursday: math problems pg 10

        Chance
        Wednesday: music note test
    Returns 'deadline' items with inferred dates.
    """
    items: List[Dict[str, Any]] = []
    if not text:
        return items

    # split into lines and sweep
    lines = [ln.strip("\r") for ln in (text or "").split("\n")]
    current_child: Optional[str] = None

    for ln in lines:
        if not ln.strip():
            continue

        # Child header?
        m_child = _CHILD_LINE.match(ln)
        if m_child:
            current_child = m_child.group(1)
            continue

        # Weekday task?
        m = _WD_LINE.match(ln)
        if m:
            wd = m.group("wd").lower()
            task = m.group("task").strip()
            if wd in _WEEKDAYS:
                dt = _next_weekday(anchor_dt, _WEEKDAYS[wd])
                iso = dt.isoformat()
                subject = f"{current_child}: {task}" if current_child else task
                items.append({
                    "type": "deadline",
                    "snippet": subject[:300],
                    "dates": [{"raw": wd.title(), "iso": iso}]
                })

    return items

# Google Calendar "Add to Calendar" links (carry clean title + dates)
_CAL_RENDER_RE = re.compile(r'https?://calendar\.google\.com/calendar/render\?[^)\s"\']+', re.I)

def _ics_from_text(text: str) -> List[Dict[str, Any]]:
    out = []
    for m in _CAL_RENDER_RE.finditer(text or ""):
        u = m.group(0)
        q = parse_qs(urlparse(u).query)
        title = (q.get("text", [""])[0] or "").strip()
        dates = (q.get("dates", [""])[0] or "").strip()  # e.g., 20251004T000000Z/20251004T013000Z
        start_raw, _, end_raw = dates.partition("/")
        out.append({"title": title, "start_raw": start_raw, "end_raw": end_raw, "url": u})
    return out

def _to_iso(dt_str: str, anchor_dt: datetime) -> Optional[str]:
    if not dt_str:
        return None
    try:
        dt = dateparser.parse(dt_str, default=anchor_dt)
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=anchor_dt.tzinfo or timezone.utc)
        return dt.isoformat()
    except Exception:
        return None

def _select_best_dates(anchor_dt: datetime, candidates: List[datetime]) -> List[str]:
    logger.debug("")
    """
    Given many parsed datetimes, choose the best one(s):
    - Prefer future >= (anchor_dt - 1 day)
    - Otherwise the most recent within 45 days past
    - Reject anything older than 1 year from anchor
    Return up to 2 in ISO strings (start, optional end).
    """
    if anchor_dt.tzinfo is None:
        anchor_dt = anchor_dt.replace(tzinfo=timezone.utc)

    year_cutoff = anchor_dt - timedelta(days=365)
    recent_cutoff = anchor_dt - timedelta(days=45)
    future_cutoff = anchor_dt - timedelta(days=1)

    # clean and keep bounded
    cans = [c for c in candidates if c and c.tzinfo]  # expect tz-aware
    cans = [c for c in cans if c >= year_cutoff]

    futures = [c for c in cans if c >= future_cutoff]
    if futures:
        futures.sort()
        return [futures[0].isoformat()]  # soonest upcoming

    recents = [c for c in cans if c >= recent_cutoff]
    if recents:
        recents.sort(reverse=True)
        return [recents[0].isoformat()]

    return []

_SECTION_TITLES = re.compile(r'\b(upcoming events?|events?|reminders?|important dates?)\b', re.I)

def _clean_text(s: str) -> str:
    s = re.sub(r'\s+', ' ', (s or '')).strip()
    return s

def _parse_date_fragments(text: str, anchor_dt: datetime) -> List[datetime]:
    logger.debug("")
    """Extract plausible datetimes from a line of text, anchored to email time."""
    out: List[datetime] = []
    # try multiple small parses; dateutil can pull several tokens when called repeatedly
    for m in re.finditer(r'([A-Z][a-z]{2,9}\s+\d{1,2}(?:,\s*\d{4})?)|(\b\d{1,2}/\d{1,2}/\d{2,4}\b)|(\bMon|Tue|Wed|Thu|Fri|Sat|Sun\b)', text, re.I):
        frag = m.group(0)
        try:
            dt = dateparser.parse(frag, fuzzy=True, default=anchor_dt)
            if dt:
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=anchor_dt.tzinfo or timezone.utc)
                out.append(dt)
        except Exception:
            pass
    return out

def _best_single_date(anchor_dt: datetime, candidates: List[datetime]) -> Optional[str]:
    logger.debug("")
    """Choose a single representative date: next future, else most recent within 45 days."""
    if not candidates:
        return None
    if anchor_dt.tzinfo is None:
        anchor_dt = anchor_dt.replace(tzinfo=timezone.utc)
    recent_cutoff = anchor_dt - timedelta(days=45)
    future_cutoff = anchor_dt - timedelta(days=1)

    c = [d for d in candidates if d]
    futures = sorted([d for d in c if d >= future_cutoff])
    if futures:
        return futures[0].isoformat()
    recents = sorted([d for d in c if d >= recent_cutoff], reverse=True)
    if recents:
        return recents[0].isoformat()
    return None

def extract_events_from_html(html: str, anchor_dt: datetime) -> List[Dict[str, Any]]:
    logger.debug("")
    """
    Extracts items from newsletter-like HTML:
    - Finds sections titled 'Upcoming Events', 'Reminders', 'Important Dates'
    - Pulls list items and strong/paragraph rows as events/reminders
    """
    items: List[Dict[str, Any]] = []
    if not html:
        return items

    soup = BeautifulSoup(html, "html.parser")

    # Find candidate section headers
    headers = [h for h in soup.find_all(['h1','h2','h3','h4','h5','h6']) if _SECTION_TITLES.search(h.get_text(' ', strip=True) or "")]
    seen = set()

    # Helper to emit an item from a line of text
    def _emit(line: str, itype: str = "event"):
        title = _clean_text(line)
        if not title or title.lower() in seen:
            return
        dates = _parse_date_fragments(title, anchor_dt)
        iso = _best_single_date(anchor_dt, dates)
        if not iso:
            return
        items.append({
            "type": itype,
            "snippet": title[:300],
            "dates": [{"raw": "", "iso": iso}]
        })
        seen.add(title.lower())

    # 1) Section blocks under headers
    for h in headers:
        # look at sibling lists/paragraphs following header
        for sib in h.find_all_next(['ul','ol','p','table'], limit=30):
            # stop when we hit the next big header
            if sib.name in ['h1','h2','h3'] and sib is not h:
                break
            if sib.name in ['ul','ol']:
                for li in sib.find_all('li', recursive=False):
                    txt = _clean_text(li.get_text(' ', strip=True))
                    _emit(txt, "event")
            elif sib.name == 'p':
                txt = _clean_text(sib.get_text(' ', strip=True))
                if len(txt) > 10:
                    _emit(txt, "reminder")
            elif sib.name == 'table':
                # simple table rows: strong in first cell → title, second cell → when
                for tr in sib.find_all('tr'):
                    cells = [c.get_text(' ', strip=True) for c in tr.find_all(['td','th'])]
                    if not cells:
                        continue
                    row = " — ".join([c for c in cells if c])
                    _emit(row, "event")

    # 2) Fallback: strong tags that look like titled items
    for st in soup.find_all('strong'):
        txt = _clean_text(st.get_text(' ', strip=True))
        if 3 <= len(txt) <= 200 and any(ch.isalpha() for ch in txt):
            _emit(txt, "event")

    return items

CONTEXT_CHARS = 240  # window around each keyword match

def _context(text: str, start: int, end: int, span: int = CONTEXT_CHARS) -> str:
    a = max(0, start - span)
    b = min(len(text), end + span)
    return text[a:b]

def classify(
    text: str,
    anchor_dt: datetime,
    html: Optional[str] = None,
    subject: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Return structured items with anchored dates.
    - Uses weekday→date inference for 'homework' style lines
    - Parses newsletter HTML sections (events/reminders)
    - Promotes Google Calendar links as explicit events
    - For keyword matches, mines dates from a local context window near the hit
    """
    items: List[Dict[str, Any]] = []

    # 0) Homework/week-schedule inference (weekday -> actual date)
    try:
        items.extend(_infer_homework_items(text, anchor_dt))
    except Exception:
        pass

    # 0.5) Newsletter HTML sections → events/reminders
    if html:
        try:
            items.extend(extract_events_from_html(html, anchor_dt))
        except Exception:
            pass

    # 1) Calendar links in plain text → events
    for cal in _ics_from_text(text):
        start_iso = _to_iso(cal.get("start_raw") or cal.get("start"), anchor_dt)
        end_raw = cal.get("end_raw") or cal.get("end")
        end_iso = _to_iso(end_raw, anchor_dt) if end_raw else None
        if start_iso:
            ev = {
                "type": "event",
                "snippet": (cal.get("title") or "Event")[:300],
                "dates": [{"raw": cal.get("start_raw") or "", "iso": start_iso}],
                "_calendar_url": cal.get("url"),
            }
            if end_iso:
                ev["dates"].append({"raw": end_raw or "", "iso": end_iso})
            items.append(ev)

    # 2) Keyword-driven extraction (KEY_PATTERNS + DATE_PATTERNS assumed present)
    # Include subject as searchable text so "This week's Homework" unlocks body parsing.
    search_text = ((subject or "") + "\n" + (text or "")).strip()

    for label, regex in KEY_PATTERNS.items():
        for m in regex.finditer(search_text):
            # mine a local window around the match; better signal than just m.group(0)
            ctx = _context(search_text, m.start(), m.end())

            # Prefer a single task-like line (strip markdown bullets etc.)
            # First line that contains the keyword span or follows it closely
            lines = [ln.strip(" •-*>\t") for ln in ctx.splitlines() if ln.strip()]
            snippet = lines[0][:300] if lines else m.group(0)[:300]

            # Mine dates from the local context
            local_dates = []
            for pat in DATE_PATTERNS:
                for dm in re.finditer(pat, ctx, re.I):
                    frag = dm.group(0)
                    try:
                        dt = dateparser.parse(frag, fuzzy=True, default=anchor_dt)
                        if dt and dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        if dt:
                            local_dates.append(dt)
                    except Exception:
                        pass

            # Fallback: very first 2k chars if nothing near the hit
            if not local_dates:
                head = search_text[:2000]
                for pat in DATE_PATTERNS:
                    for dm in re.finditer(pat, head, re.I):
                        frag = dm.group(0)
                        try:
                            dt = dateparser.parse(frag, fuzzy=True, default=anchor_dt)
                            if dt and dt.tzinfo is None:
                                dt = dt.replace(tzinfo=timezone.utc)
                            if dt:
                                local_dates.append(dt)
                        except Exception:
                            pass

            chosen = _select_best_dates(anchor_dt, local_dates)
            if chosen:
                items.append({
                    "type": label,
                    "snippet": snippet,
                    "dates": [{"raw": "", "iso": iso} for iso in chosen]
                })

    return items
