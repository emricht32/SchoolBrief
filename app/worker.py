
# import os, traceback
# from datetime import datetime, timezone
# from sqlalchemy.orm import Session
# from google.auth.transport.requests import Request
# from .models import Family, DigestPreference, ProviderAccount, Child, DigestRun, Subscription
# from .security import decrypt_text
# from .google_oauth import creds_from_token_json
# from .gmail_ingest import build_service, build_query, extract_text_from_message, email_headers
# from .extractors import classify
# from .summarizer import summarize_html_text
# from .emailer import send_email
# from .utils import csv_to_list
# import unicodedata
# import re

# # app/worker.py (top)

# def _domain_of(addr: str) -> str:
#     m = re.search(r'@([A-Za-z0-9.\-]+)', addr or '')
#     return (m.group(1).lower() if m else '')

# def _listid_domain(list_id: str) -> str:
#     if not list_id:
#         return ''
#     m = re.search(r'<([^>]+)>', list_id)
#     host = (m.group(1) if m else list_id).strip()
#     return host.split()[-1].lower()

# def _looks_schooly(headers: dict, allowed_domains: list[str], include_keywords: list[str]) -> bool:
#     """
#     Strictly allow if sender (From) or List-Id is under an allowed domain.
#     Otherwise allow only if Subject contains an include keyword.
#     """
#     subj = (headers.get('subject') or '').lower()
#     from_addr = headers.get('from', '')
#     listid = headers.get('list_id', '')

#     dom = _domain_of(from_addr)
#     list_dom = _listid_domain(listid)

#     if any(dom.endswith(d) for d in allowed_domains):
#         return True
#     if any(list_dom.endswith(d) for d in allowed_domains):
#         return True
#     if include_keywords and any(k.lower() in subj for k in include_keywords):
#         return True
#     return False

# # Broader promo/finance/collection/newsletter blockers
# _BLOCK_PATTERNS = [
#     r'\bAPR\b', r'\bAuto\s*Pay\b', r'\bSubscribe\s*&\s*Save\b', r'\bcredit\s+card\b',
#     r'\bstatement\b', r'\bminimum payment\b', r'\binterest\b',
#     r'\bcollection agency\b', r'\bmedical bill\b', r'\baccount ending\b',
#     r'\bCapital\s+One\b', r'\bchase\b', r'\bdiscover\b', r'\bamerican express\b',
#     r'\bpaypal\b', r'\bvenmo\b', r'\bverizon\b', r'\bcomcast\b',
#     r'unsubscribe\s+from\s+this\s+list', r'manage\s+your\s+preferences', r'\bprivacy policy\b',
# ]
# _block_re = re.compile("|".join(_BLOCK_PATTERNS), re.I)

# def _is_blocked(text: str, headers: dict) -> bool:
#     hay = " ".join([
#         headers.get('from',''), headers.get('subject',''), headers.get('list_id',''),
#         (text or '')
#     ])
#     # Heuristic: lots of dollar amounts → likely billing/promos
#     if len(re.findall(r'\$\s?\d', hay)) >= 2:
#         return True
#     return bool(_block_re.search(hay))


# def _norm_title(subject: str, snippet: str) -> str:
#     t = (subject or snippet or '').strip()
#     t = unicodedata.normalize('NFKC', t)
#     # strip boilerplate words and punctuation
#     t = re.sub(r'\b(weekly|this week|reminder|due|deadline|event|field\s*trip|update)\b[:\-\s]*', '', t, flags=re.I)
#     t = re.sub(r'[\[\]\(\)]+', ' ', t)
#     t = re.sub(r'\s+', ' ', t).strip().lower()
#     # keep a sane length for the key
#     return t[:160] or 'item'

# def _first_iso(it: dict) -> str:
#     for d in it.get('dates', []):
#         if d.get('iso'):
#             return d['iso']
#     return ''

# def dedupe_items(items: list[dict]) -> list[dict]:
#     """Collapse repeats: keep the best-scored item per (normalized_title, YYYY-MM-DD)."""
#     dd = {}
#     for it in items:
#         iso = _first_iso(it)
#         if not iso:
#             continue
#         title = _norm_title(it.get("_subject",""), it.get("snippet",""))
#         key = (title, iso[:10])
#         prev = dd.get(key)
#         score = (
#             1 if it.get("_calendar_url") else 0,  # calendar links preferred
#             it.get("_msg_ts", 0)                  # latest email wins
#         )
#         if prev is None or score > prev.get("_score", (0,0)):
#             it["_score"] = score
#             dd[key] = it
#     return list(dd.values())



# ###########################

# BLOCK_PATTERNS = [
#     r'\bAPR\b', r'\bAuto\s*Pay\b', r'\bSubscribe\s*&\s*Save\b', r'\bcredit\s+card\b',
#     r'\bCapital\s+One\b', r'\bcostco\b', r'\bcollection agency\b', r'\bmedical bill\b',
#     r'\bstatement\b', r'\baccount ending\b', r'\bminimum payment\b', r'\bfinanc(e|ial)\b',
#     r'\$\d', r'\binterest\b', r'\bAPR\b'
# ]
# _block_re = re.compile("|".join(BLOCK_PATTERNS), re.I)

# def _domain_of(addr: str) -> str:
#     m = re.search(r'@([A-Za-z0-9.\-]+)', addr or '')
#     return (m.group(1).lower() if m else '')

# def _listid_domain(list_id: str) -> str:
#     if not list_id:
#         return ''
#     m = re.search(r'<([^>]+)>', list_id)
#     host = (m.group(1) if m else list_id).strip()
#     return host.split()[-1].lower()

# def _is_schoolish(headers: dict, allowed_domains: list[str], include_keywords: list[str]) -> bool:
#     from_addr = headers.get('from', '')
#     subj = (headers.get('subject') or '').lower()
#     listid = headers.get('list_id', '')

#     dom = _domain_of(from_addr)
#     list_dom = _listid_domain(listid)

#     if any(dom.endswith(d) for d in allowed_domains) or any(list_dom.endswith(d) for d in allowed_domains):
#         return True
#     # keyword backstop in subject only
#     return any(k.lower() in subj for k in include_keywords) if include_keywords else False

# def _is_blocked(text: str, headers: dict) -> bool:
#     hay = " ".join([headers.get('from',''), headers.get('subject',''), headers.get('list_id',''), text or ''])
#     return bool(_block_re.search(hay))


# def _norm_title(subject: str, snippet: str) -> str:
#     t = subject or snippet or ''
#     t = unicodedata.normalize('NFKC', t)
#     # remove generic labels
#     t = re.sub(r'\b(meeting|reminder|due|deadline|event|field\s*trip|update)\b[:\-\s]*', '', t, flags=re.I)
#     t = re.sub(r'\s+', ' ', t).strip().lower()
#     # trim long tails from marketing
#     return t[:140]

# def _first_iso(it: dict) -> str:
#     for d in it.get('dates', []):
#         iso = d.get('iso')
#         if iso:
#             return iso
#     return ''

# def _get_family_context(db: Session, family_id: int):
#     fam = db.query(Family).filter_by(id=family_id).first()
#     pref = fam.prefs
#     owner = fam.owner
#     prov = db.query(ProviderAccount).filter_by(user_id=owner.id, provider="google").first()
#     children = fam.children
#     return fam, pref, owner, prov, children

# def run_digest_for_family(db: Session, family_id: int):
#     fam, pref, owner, prov, children = _get_family_context(db, family_id)
#     run = DigestRun(family_id=fam.id, started_at=datetime.utcnow(), cadence=pref.cadence)
#     db.add(run); db.commit(); db.refresh(run)

#     def _finish_ok(items_scanned: int, items_found: int, note: str = ""):
#         run.messages_scanned = items_scanned
#         run.items_found = items_found
#         run.email_sent = False
#         run.ended_at = datetime.utcnow()
#         if note:
#             # if your DigestRun has a notes column, use it; otherwise drop this
#             run.notes = (getattr(run, "notes", "") or "")
#             run.notes = (run.notes + ("\n" if run.notes else "") + note).strip()
#         db.add(run); db.commit()

#     try:
#         # Subscription gate
#         sub = db.query(Subscription).filter_by(family_id=fam.id).order_by(Subscription.id.desc()).first()
#         if not sub:
#             raise RuntimeError("No subscription found.")
#         now_utc = datetime.utcnow()
#         if sub.status == "trialing":
#             if not sub.trial_end or sub.trial_end <= now_utc:
#                 sub.status = "canceled"
#                 db.add(sub); db.commit()
#                 raise RuntimeError("Trial ended. Please subscribe on the Billing page.")
#         elif sub.status != "active":
#             raise RuntimeError("No active subscription. Please subscribe on the Billing page.")

#         # Google auth
#         if not (prov and prov.token_json_enc):
#             raise RuntimeError("No Google account connected. Please connect in settings.")
#         token_json = decrypt_text(prov.token_json_enc)
#         creds = creds_from_token_json(token_json)
#         if creds and creds.expired and creds.refresh_token:
#             creds.refresh(Request())

#         service = build_service(creds)
#         days = 7 if pref.cadence == "weekly" else 1

#         allowed = [p.strip().lstrip("@").lower() for p in (pref.school_domains or "").split(",") if p.strip()]
#         inc_kw  = [k.strip() for k in (pref.include_keywords or "").split(",") if k.strip()]
#         query = build_query(days_back=days, include_keywords=inc_kw, allowed_domains=allowed)
#         print("query=", query)

#         # --- Pagination over Gmail search ---
#         all_ids = []
#         page_token = None
#         while True:
#             resp = service.users().messages().list(
#                 userId="me",
#                 q=query,
#                 maxResults=500,
#                 pageToken=page_token
#             ).execute()
#             all_ids.extend([m["id"] for m in resp.get("messages", [])])
#             page_token = resp.get("nextPageToken")
#             if not page_token:
#                 break

#         if not all_ids:
#             _finish_ok(items_scanned=0, items_found=0, note="No matching messages.")
#             return

#         items = []
#         scanned = 0

#         for mid in all_ids:
#             try:
#                 msg = service.users().messages().get(userId="me", id=mid, format="full").execute()
#                 scanned += 1
#                 headers = email_headers(msg)

#                 # 1) header prefilter
#                 if not _looks_schooly(headers, allowed, inc_kw):
#                     continue

#                 content = extract_text_from_message(service, msg)
#                 text = content.get("text","") or ""
#                 html = content.get("html","") or ""
#                 if not text.strip() and not html.strip():
#                     continue

#                 # block check (use both text+html)
#                 if _is_blocked((text + " " + html), headers):
#                     continue

#                 # 3) anchor date
#                 ms = msg.get("internalDate")
#                 anchor_dt = datetime.fromtimestamp(int(ms)/1000.0, tz=timezone.utc) if ms else datetime.now(timezone.utc)

#                 # 4) classify
#                 # anchor_dt as you already compute...
#                 extracted = classify(text, anchor_dt=anchor_dt, html=html, subject=headers.get("subject"))

#                 # 5) attach subject/sender/ts for dedupe & context
#                 for it in extracted:
#                     it["_subject"] = headers.get("subject") or ""
#                     it["_from"]    = headers.get("from") or ""
#                     it["_msg_ts"]  = int(ms) if ms else 0

#                 items.extend(extracted)

#             except Exception as per_msg_err:
#                 # Don’t kill the whole run for one bad message; log and continue
#                 print(f"[ingest warn] message {mid} skipped: {per_msg_err}")
#                 continue

#         # 6) dedupe before stale filter
#         items = dedupe_items(items)

#         # 7) stale filter: keep future or last 14 days
#         from datetime import timedelta
#         total_before_stale = len(items)
#         nowz = datetime.now(timezone.utc)
#         stale_cutoff = nowz - timedelta(days=14)

#         fresh = []
#         for it in items:
#             kept = False
#             for d in it.get("dates", []):
#                 try:
#                     dt = datetime.fromisoformat(d["iso"])
#                     if dt.tzinfo is None:
#                         dt = dt.replace(tzinfo=timezone.utc)
#                     if dt >= stale_cutoff:
#                         kept = True
#                         break
#                 except Exception:
#                     pass
#             if kept:
#                 fresh.append(it)

#         items = fresh
#         filtered_count = total_before_stale - len(items)

#         # Optional: a second dedupe in case stale filtering created new collisions
#         items = dedupe_items(items)

#         run.messages_scanned = scanned
#         run.items_found = len(items)

#         if not items:
#             _finish_ok(items_scanned=scanned, items_found=0, note=f"Filtered {filtered_count} stale items; nothing to send.")
#             return False, "Nothing to send"

#         brand = {
#             "product_name": "SchoolBrief",
#             "children": [{"name": c.name, "grade": c.grade} for c in children]
#         }

#         # Summarize first — if this fails, DO NOT send
#         try:
#             html, txt = summarize_html_text(items, brand)
#         except Exception as se:
#             # record the error and bubble a user-safe message
#             run.error = f"Summarization failed: {se}"
#             run.ended_at = datetime.utcnow()
#             db.add(run); db.commit()
#             return False, "Summarization failed"

#         if not html or not txt:
#             run.error = "Summarization returned empty content"
#             run.ended_at = datetime.utcnow()
#             db.add(run); db.commit()
#             return False, "Empty summary"

#         # Send
#         to_addrs = csv_to_list(pref.to_addresses) or [owner.email]
#         subject = f"SchoolBrief — {pref.cadence.title()} Digest"
#         send_email(subject, html, txt, to_addrs)  # raises on error

#         run.email_sent = True
#         run.ended_at = datetime.utcnow()
#         db.add(run); db.commit()
#         return True, f"Sent to {', '.join(to_addrs)}"

#     except Exception as e:
#         run.error = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
#         run.ended_at = datetime.utcnow()
#         db.add(run); db.commit()
#         return False, f"Error: {e}"
