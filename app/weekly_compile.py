# # app/weekly_compile.py
# from datetime import datetime, timedelta, timezone
# from sqlalchemy.orm import Session
# from openai import OpenAI
# import os
# from .models import OneLiner
# from .emailer import send_email

# client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# DIGEST_SYS = (
#     "You are a concise editorial assistant. Format a parent-friendly school digest email "
#     "from short one-liners that already contain the key info. Group by day, then by child/class "
#     "if obvious from the text. Keep it skim-able."
# )

# def render_digest_email(one_liners: list[tuple[str, datetime]], product_name="SchoolBrief"):
#     lines = []
#     for txt, when_ts in one_liners:
#         when_s = when_ts.astimezone(timezone.utc).isoformat() if when_ts else ""
#         lines.append(f"- {txt} || {when_s}")
#     prompt = (
#         f"{product_name} weekly digest inputs (one per line; '||' followed by ISO date if provided):\n"
#         + "\n".join(lines)
#         + "\n\nReturn two sections EXACTLY like this:\n"
#           "HTML:\n<full html email body here>\n\n"
#           "TEXT:\n<full plaintext body here>\n"
#     )

#     resp = client.chat.completions.create(
#         model="gpt-4o-mini",
#         temperature=0.3,
#         messages=[
#             {"role": "system", "content": DIGEST_SYS},
#             {"role": "user", "content": prompt},
#         ],
#     )
#     content = resp.choices[0].message.content or ""
#     html, text = "", ""
#     if "HTML:" in content and "TEXT:" in content:
#         _, rest = content.split("HTML:", 1)
#         html_part, text_part = rest.split("TEXT:", 1)
#         html = html_part.strip()
#         text = text_part.strip()
#     else:
#         text = content.strip()
#         html = f"<pre>{text}</pre>"
#     return html, text

# def compile_and_send_digest(db: Session, family_id: int, to_emails: list[str], cadence="weekly"):
#     now = datetime.now(timezone.utc)
#     start = now - timedelta(days=7) if cadence == "weekly" else now - timedelta(days=1)

#     rows = (
#         db.query(OneLiner)
#         .filter(OneLiner.family_id == family_id)
#         .filter(OneLiner.created_at >= start)
#         .order_by(OneLiner.when_ts.is_(None), OneLiner.when_ts.asc())
#         .all()
#     )
#     if not rows:
#         return False, "No one-liners to include"

#     data = [(r.one_liner, r.when_ts) for r in rows]
#     html, text = render_digest_email(data)

#     subject = f"SchoolBrief — {'Weekly' if cadence=='weekly' else 'Daily'} Digest"
#     send_email(subject, html, text, to_emails)
#     return True, f"Sent to {', '.join(to_emails)}"
