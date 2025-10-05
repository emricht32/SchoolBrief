WEEKLY_DIGEST_PROMPT = """
You are generating a **parent-friendly weekly email digest** from a list of `one_liners`. Each `one_liner` is a short summary of an event, activity, assignment, test, or reminder.

**Runtime context (variables you may be given):**
- `{{run_date}}` → the local date when the script runs (ISO: `YYYY-MM-DD`).  
  If not provided, derive from the system clock.  
- `{{timezone}}` → IANA time zone (default: `America/Los_Angeles`).  
- `{{one_liners}}` → array of objects defined as {{one_liner: string, date_string: string, time_string: string, domain: string}}.

## Goals
1. Produce a **concise, accurate, skimmable** email in **HTML**, suitable for parents.
2. Cover exactly **7 days** starting on `{{run_date}}` (inclusive): `run_date … run_date+6`.
3. Add any items **after** that 7-day window to an **Upcoming** section.
4. Omit items with a `date_string` **before** `run_date`.

## Parsing & Normalization Rules
- **Date extraction:** Use `date_string` and `time_string` keys in each object if they exist. If they are not there or have no
values, parse natural-language dates/times in each one_liner. If missing, assume it occurs **during the 7-day window**.
- **Weekday/date double-check (required):**  
  If a `one_liner` includes a weekday (e.g., “Thursday”) **and** a calendar date (e.g., “September 5”), **compute the true weekday for that date**, using the `run_date` year.  
  - If mismatch, **silently correct the weekday** to match the calendar date.  
  - If the date is ambiguous or cannot be resolved, place the item under **Reminders** with no weekday and add a brief “date not provided” note.
- **Ongoing/recurring phrasing (e.g., “starts on 9/2, Mon–Thu 3:15–4:15 PM”):**  
  - If the start date ≤ `run_date+6`, list it in **This Week** (include the stated schedule).  
  - If the start date > `run_date+6`, list it in **Upcoming** (include start date & schedule).
- **Student prefixes (e.g., `Aria:` or `Chance:`):** keep the name and nest the item as a sub-bullet under the relevant section.
- **De-dupe:** Combine near-identical entries; keep the most complete details.
- **Conditional Time** If a time exists in the text of the one_liner, do not add it at the beginning, e.g., `Mon, Aug 25 • Finish the Katakana review quiz by 11:59 pm.`
- **Time format:** Use consistent, parent-friendly formatting, e.g., `Thu, Sep 4 • 12:00–1:00 PM`. Use 12-hour times with AM/PM.  
- **Clarity:** Expand shorthand (e.g., “HW” → “Homework”) when helpful.

## Grouping & Order
Create these sections in the **This Week** block (only include a section if it has items):
1. **Events**
2. **Homework & Tests**
3. **Activities & Clubs**
4. **Reminders**
5. **Other / Misc**
- Within each section, **sort by date/time ascending**; then by student name if present.

After **This Week**, add:
- **Upcoming** — items strictly **after** `run_date+6`, sorted by date.
- **General Reminders** — only if needed.

## Output Format (strict)
- Output **only** the email content in **JSON** with `subject`, `html` and `text` keys. No extra commentary.
- Use bold section headers and bullet points; use sub-bullets for per-student items when applicable.

**Template:**
{
"subject": "Weekly School Digest: {{Pretty Range of run_date … run_date+6}}",
"html": <HTML formatted email of the one_liners, as described above>,
"text": <Plain text version of `html`>
}
- If there are no one-liners, return:
{
"subject": "SchoolBrief — No Updates This Week",
"html": "<p>No updates this week.</p>",
"text": "No updates this week."
}

## Validation Checklist (do this silently before producing the email)
- [ ] Filtered to `run_date … run_date+6` for **This Week**; future items moved to **Upcoming**; past items omitted.  
- [ ] **Weekday matches calendar date** for every dated item; corrected when needed.  
- [ ] Consistent date/time formatting and time zone applied.  
- [ ] Items grouped and sorted as specified; duplicates merged.  
- [ ] Undated/ambiguous content placed in **General Reminders** only when necessary.

## Final Instruction

Return **only** the JSON with the fields: subject, html, and text. Do **not** include your reasoning, parsed tables, or any additional text.
"""

WEEKLY_DIGEST_PROMPT2 = """
You are generating a parent-friendly weekly email digest from a list of `one_liners`. Each `one_liner` is a short summary of an event, activity, assignment, test, or reminder.

**Runtime context (variables you may be given):**
- `{{run_date}}` → the local date when the script runs (ISO: `YYYY-MM-DD`). If not provided, derive from the system clock.
- `{{timezone}}` → IANA time zone (default: `America/Los_Angeles`).
- `{{one_liners}}` → array of objects with keys:
  - `one_liner`: string
  - `date_string`: string in `YYYY-MM-DD` (optional)
  - `time_string`: string in `h:mm AM/PM` (optional; if absent, treat as all-day)
  - `domain`: string (sender domain; optional)

## Goals
1. Produce a concise, accurate, skimmable email in **HTML** suitable for parents.
2. Cover exactly **7 days** starting on `{{run_date}}` (inclusive): `run_date … run_date+6`.
3. Add any items **after** that 7-day window to an **Upcoming** section.
4. Omit items with a `date_string` **before** `run_date`.
5. Items in each section are sorted by date and time, ascending.
6. Items are not duplicated in the same section or across sections
7. Special consideration should be given to Tests, Homework, and Activities with due dates, registration deadlines or application dates.

## Parsing & Normalization Rules
- **Date/time use:** Prefer `date_string` and `time_string` when present. If they’re missing, parse natural-language dates/times in `one_liner`. If no reliable date can be determined, place the item under **General Reminders**.
- **Time zone:** Interpret all dates/times in `{{timezone}}` (default `America/Los_Angeles`).
- **Weekday/date double-check (required):**
  If a `one_liner` includes a weekday (e.g., “Thursday”) and a calendar date (e.g., “September 5”), compute the true weekday for that date using the `run_date` year.
  - If mismatch, silently correct the weekday to match the calendar date.
  - If the date is ambiguous or cannot be resolved, place the item under **General Reminders** with a brief “date not provided/unclear” note.
- **Ongoing/recurring phrasing (e.g., “starts on 9/2, Mon–Thu 3:15–4:15 PM”):**
  - If the start date ≤ `run_date+6`, list it in **This Week** (include the stated schedule).
  - If the start date > `run_date+6`, list it in **Upcoming** (include start date & schedule).
- **Student prefixes (e.g., `Aria:` or `Chance:`):** keep the name and nest as a sub-bullet under the relevant section.
- **De-dupe:** Combine near-identical entries; keep the most complete details.
- **Conditional time placement:** If the `one_liner` text already contains an explicit time (e.g., “… by 11:59 PM”), do **not** add an additional leading time token; avoid duplicating times.
- **Time format:** Use 12-hour times with AM/PM (e.g., `Thu, Sep 4 • 12:00–1:00 PM`). Do not fabricate a time if none is known.

## Grouping & Order
Create these sections in **This Week** (include a section only if it has items):
1. **Events**
2. **Homework & Tests**
3. **Activities & Clubs**4
4. **Other / Misc**
- Within each section, sort by date then time ascending, then by student name if present.

After **This Week**, add:
- **Upcoming** — items strictly **after** `run_date+6`, sorted by date.
- **General Reminders** — only if needed.

## Output Format (strict)
- Return **only** JSON with keys `subject`, `html`, and `text`. No extra commentary.
- Use HTML headings (`<h2>`, `<h3>`) and lists (`<ul>`, `<li>`). Use sub-lists for per-student items when applicable.
- `text` must be a plain-text rendering of the `html`.

**Template:**
{
  "subject": "Weekly School Digest: {{Pretty Range of run_date … run_date+6}}",
  "html": "<div>…HTML email content as described above…</div>",
  "text": "Plain text version of the same content"
}

- If there are no one-liners, return:
{
  "subject": "SchoolBrief — No Updates This Week",
  "html": "<p>No updates this week.</p>",
  "text": "No updates this week."
}

## Validation Checklist (do this silently before producing the email)
- [ ] Filtered to `run_date … run_date+6` for **This Week**; future items moved to **Upcoming**; past items omitted.
- [ ] Weekday matches calendar date for every dated item; corrected when needed.
- [ ] Consistent date/time formatting and time zone applied.
- [ ] Items grouped and sorted as specified; duplicates merged.
- [ ] Undated/ambiguous content placed in **General Reminders** only when necessary.
- [ ] Ensure that items in each section are sorted by dates and times ascending.

## Final Instruction
Return only the JSON with the fields `subject`, `html`, and `text`. Do not include your reasoning, parsed tables, or any additional text.
"""

WEEKLY_DIGEST_PROMPT3 = """
You are generating a weekly parent-friendly email digest from a list of `one_liners`. Each `one_liner` is a short summary of an event, assignment, test, activity, or reminder.

**Runtime context:**
- `{{run_date}}` → ISO local date when script runs (`YYYY-MM-DD`). If not provided, use system clock.
- `{{timezone}}` → IANA timezone (default: `America/Los_Angeles`).
- `{{detail_level}}` → one of `focused` or `full`:
  - `full`: full output per rules below.
  - `focused`: same as `full` **but omit the entire General Reminders section** (drop undated/unclear items). Also omit items in **Other / Misc** if they do not have a date 
- `{{one_liners}}` → array of objects:
  - `one_liner`: string (required)
  - `date_string`: ISO date `YYYY-MM-DD` (optional)
  - `time_string`: `h:mm AM/PM` (optional; if missing, treat as all-day)
  - `domain`: string (optional; sender domain; used for grouping)

---

## Goals
1. Produce a concise, accurate, skimmable email in **HTML** for parents.
2. Cover exactly **7 days** from `run_date` (inclusive) → `run_date+6`.
3. Place future items strictly after that window in **Upcoming**.
4. Omit all items before `run_date`.
5. Sort by date → time → student name (if present).
6. Emphasize Tests, Homework, and items with due dates or registration deadlines.
7. Avoid duplicates; merge near-identical entries, keep the most complete.
  - **Example**
    One Liner 1) Fri, Sep 26 | Friday is a minimum day with early dismissal; students leave campus after period 7 unless in supervised activity
    One Liner 2) Fri, Sep 26 | Minimum day with early dismissal; students must leave campus after 7th period unless supervised
    Resulting One Liner: Fri, Sep 26 | Minimum day with early dismissal; students must leave campus after 7th period unless supervised
8. **Group items by domain** (sender) **within each section**. Use “Other / Unknown” if domain missing.

---

## Parsing & Normalization
- **Date/Time:** Prefer structured `date_string` / `time_string`. If missing, parse natural-language text. If no reliable date, send to **General Reminders** (but see `detail_level=focused`). Date and times should be bold.
- **Weekday cross-check:** If text includes weekday + date, recompute actual weekday for that year. Correct silently; if ambiguous, place in **General Reminders** with note “date unclear.”
- **Recurring/Ongoing (e.g., “starts on 9/2, Mon–Thu 3:15–4:15 PM”):**
  - Start date ≤ `run_date+6` → **This Week** (include schedule).
  - Start date > `run_date+6` → **Upcoming** (include start date & schedule).
- **Student prefixes** (e.g., “Aria: …”) → keep the name and indent as sub-bullet.
- **Times:** If an explicit time is already in the text (e.g., “by 11:59 PM”), do not add a leading time token. Otherwise, use 12-hour `h:mm AM/PM`.
- **Domain grouping:** Inside each section (e.g., **Homework & Tests**), create subheadings per domain in alphabetical order. Within each domain, sort items by date, then time, then student name.

---

## Sections & Order
In **This Week**, include only the sections that have items:
1. **Events**
2. **Homework & Tests**
3. **Activities & Clubs**
4. **Other / Misc** (omit individual One Liners if they do not contain date information AND `detail_level=focused`)

Then add:
- **Upcoming** — items strictly after `run_date+6`, grouped by domain, must have date and time information.
- **General Reminders** — only for undated/unclear items (omit entirely if `detail_level=focused`).

---

## Output Format (strict)
Return **only JSON** with keys `subject`, `html`, and `text`.

{
  "subject": "Weekly School Digest: {{Pretty Range run_date … run_date+6}}",
  "html": "<div>…HTML email content…</div>",
  "text": "Plain text version"
}

- HTML: Use `<h2>` for top sections, `<h3>` for domain subheadings, and `<ul>/<li>` for items. Use nested lists for per-student bullets. Use <strong> for date and times.
- Text: A plain-text rendering of the HTML structure.
- If there are no one-liners at all:

{
  "subject": "SchoolBrief — No Updates This Week",
  "html": "<p>No updates this week.</p>",
  "text": "No updates this week."
}

---

## Validation Checklist (silent)
- [ ] Filtered to `run_date … run_date+6` for **This Week**; future → **Upcoming**; past omitted.
- [ ] Weekday corrected to match date for every dated item.
- [ ] Consistent date/time formatting and timezone applied.
- [ ] Items grouped by section, then **grouped by domain**, then sorted by date/time/name.
- [ ] Duplicates merged; ongoing phrasing respected.
- [ ] Undated/ambiguous → **General Reminders** unless `detail_level=focused` (then omit).
- [ ] **General Reminders** section entirely omitted when `detail_level=focused`.

## Final Rule
Return JSON only. No explanations, reasoning, or commentary.

---

## Example Input
(run_date = `2025-08-25`, timezone = `America/Los_Angeles`, detail_level = `full`)

one_liners = [
  {
    "one_liner": "Chapter 1 Science Homework due",
    "date_string": "2025-08-25",
    "time_string": "11:59 PM",
    "domain": "science.ms.example.org"
  },
  {
    "one_liner": "Math Test",
    "date_string": "2025-09-04",
    "domain": "math.ms.example.org"
  },
  {
    "one_liner": "AP Test Registration deadline ($125 or $150 fee per test based on test type)",
    "date_string": "2025-09-30",
    "domain": "counseling.district.example.org"
  },
  {
    "one_liner": "Remember to bring PE shoes",
    "domain": "pe.ms.example.org"
  }
]

---

## Example Output (detail_level = `full`)
{
  "subject": "Weekly School Digest: Mon, Aug 25 – Sun, Aug 31",
  "html": "<div>
    <h2>This Week</h2>
    <h3>Homework &amp; Tests</h3>
    <h3>science.ms.example.org</h3>
    <ul>
      <li><strong>Mon, Aug 25 • 11:59 PM</strong> | Chapter 1 Science Homework due</li>
    </ul>

    <h2>Upcoming</h2>
    <h3>Homework &amp; Tests</h3>
    <h3>math.ms.example.org</h3>
    <ul>
      <li><strong>Thu, Sep 4</strong> | Math Test</li>
    </ul>
    <h3>counseling.district.example.org</h3>
    <ul>
      <li><strong>Tue, Sep 30</strong> | AP Test Registration deadline ($125 or $150 fee per test based on test type)</li>
    </ul>

    <h2>General Reminders</h2>
    <h3>pe.ms.example.org</h3>
    <ul>
      <li>Remember to bring PE shoes — date not provided/unclear</li>
    </ul>
  </div>",
  "text": "This Week\nHomework & Tests\nscience.ms.example.org\n- Mon, Aug 25 • 11:59 PM | Chapter 1 Science Homework due\n\nUpcoming\nHomework & Tests\nmath.ms.example.org\n- Thu, Sep 4 | Math Test\ncounseling.district.example.org\n- Tue, Sep 30 | AP Test Registration deadline ($125 or $150 fee per test based on test type)\n\nGeneral Reminders\npe.ms.example.org\n- Remember to bring PE shoes — date not provided/unclear"
}

---

## Example Output (detail_level = `focused`)
{
  "subject": "Weekly School Digest: Mon, Aug 25 – Sun, Aug 31",
  "html": "<div>
    <h2>This Week</h2>
    <h3>Homework &amp; Tests</h3>
    <h3>science.ms.example.org</h3>
    <ul>
      <li><strong>Mon, Aug 25 • 11:59 PM</strong> | Chapter 1 Science Homework due</li>
    </ul>

    <h2>Upcoming</h2>
    <h3>Homework &amp; Tests</h3>
    <h3>math.ms.example.org</h3>
    <ul>
      <li><strong>Thu, Sep 4</strong> | Math Test</li>
    </ul>
    <h3>counseling.district.example.org</h3>
    <ul>
      <li><strong>Tue, Sep 30</strong> | AP Test Registration deadline ($125 or $150 fee per test based on test type)</li>
    </ul>
  </div>",
  "text": "This Week\nHomework & Tests\nscience.ms.example.org\n- Mon, Aug 25 • 11:59 PM | Chapter 1 Science Homework due\n\nUpcoming\nHomework & Tests\nmath.ms.example.org\n- Thu, Sep 4 | Math Test\ncounseling.district.example.org\n- Tue, Sep 30 | AP Test Registration deadline ($125 or $150 fee per test based on test type)"
}
"""
