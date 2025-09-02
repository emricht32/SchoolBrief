# app/activity_discovery.py
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Tuple

# Lightweight blocklist (noise / promos) — expand as you see them
_BLOCK_DOMAIN_PATTERNS = [
    r"(?:amazon|ebay|bestbuy|costco|walmart|target)\.",
    r"(?:spotify|netflix|hulu|disney)\.",
    r"(?:credit|capitalone|chase|citi|amex|discover)\.",
    r"(?:facebook|instagram|x\.com|twitter)\.",
    r"(?:newsletters?|marketing|promo|offers?)\.",
]

# Positive hints for schools/activities
_POS_KEYWORDS_SUBJ = [
    r"\b(homework|assignment|syllabus|class|period|teacher|counselor|schedule|bell|pta|ptsa|field\s*trip|spirit\s*day|back\s*to\s*school|conference|volunteer)\b",
    r"\b(choir|band|orchestra|music|lesson|rehearsal|recital|practice|game|match|tournament|meet|tryout)\b",
    r"\b(library|cafeteria|lunch|bus|dismissal|attendance|grade|report card)\b",
]
_POS_KEYWORDS_BODY = [
    r"\b(homework|assignment|due|submit|permission\s*slip|sign[- ]?up|calendar|syllabus)\b",
    r"\b(practice|rehearsal|uniform|coach|season|meet|game|tournament)\b",
    r"\b(back\s*to\s*school|open\s*house|parent\s*night|pta|ptsa)\b",
]

def _domain_from_from_header(from_header: str) -> str:
    # very forgiving pull of the right-most domain
    m = re.search(r"@([A-Za-z0-9.-]+\.[A-Za-z]{2,})", (from_header or "").lower())
    return m.group(1) if m else ""

def _is_blocked_domain(domain: str) -> bool:
    return any(re.search(p, domain) for p in _BLOCK_DOMAIN_PATTERNS)

def _score_subject_body(subject: str, body: str) -> int:
    score = 0
    subj = subject or ""
    txt = body or ""
    for p in _POS_KEYWORDS_SUBJ:
        if re.search(p, subj, flags=re.I):
            score += 3
    for p in _POS_KEYWORDS_BODY:
        if re.search(p, txt, flags=re.I):
            score += 2
    # boost if “list-id” style content likely present (newsletters from schools)
    if "list-id:" in txt.lower():
        score += 1
    return score

def discover_candidate_domains(
    service,
    days_back: int = 7,
    hard_cap_msgs: int = 300,
    per_domain_min_score: int = 2,
    top_k: int = 20,
) -> List[Tuple[str, int]]:
    """
    Bootstrap discovery by scanning recent mail WITHOUT a domain filter.
    Returns list of (domain, score) sorted desc.
    """
    since = int((datetime.utcnow() - timedelta(days=days_back)).timestamp())
    # Pull a reasonable chunk from Primary + Updates; tune q as needed
    q = f"after:{since} -category:spam -in:trash"
    res = service.users().messages().list(userId="me", q=q, maxResults=min(500, hard_cap_msgs)).execute()
    ids = [m["id"] for m in res.get("messages", [])][:hard_cap_msgs]

    domain_scores = Counter()
    per_domain_counts = Counter()

    for mid in ids:
        try:
            msg = service.users().messages().get(userId="me", id=mid, format="full").execute()
            headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
            frm = headers.get("from", "")
            subj = headers.get("subject", "")
            dom = _domain_from_from_header(frm)
            if not dom or _is_blocked_domain(dom):
                continue

            # Extract tiny amount of body for keyword scoring (avoid heavy parsing here)
            snippet = (msg.get("snippet") or "")[:500]

            score = _score_subject_body(subj, snippet)
            if score:
                domain_scores[dom] += score
                per_domain_counts[dom] += 1
        except Exception:
            continue

    # Require some minimum relevance and sort
    candidates = [(d, sc) for d, sc in domain_scores.items() if sc >= per_domain_min_score]
    candidates.sort(key=lambda x: (x[1], per_domain_counts[x[0]]), reverse=True)
    return candidates[:top_k]
