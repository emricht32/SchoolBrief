
# SchoolBrief — MVP+ (Trials, Referrals, Add-ons)
A multi-tenant MVP scaffold with:
- 14-day free trial (auto on signup)
- Stripe subscriptions (base + $1/additional recipient beyond 2)
- Referral codes with one-time credit applied to referrer’s Stripe customer balance
- Gmail ingest (readonly), GPT summarization, SMTP email send
- Per-family daily/weekly scheduler

## Quick Start
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env with Google, OpenAI, SMTP, and Stripe keys
uvicorn app.main:app --reload
```

### Google OAuth
- OAuth Client (Web): redirect URI `http://localhost:8000/auth/google/callback`
- Enable Gmail API. Add your Google account as a Test User.

### Stripe
- Create Product + Prices (base monthly, add-on $1).
- Set `STRIPE_PRICE_ID`, `STRIPE_ADDON_PRICE_ID`, `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `PUBLIC_BASE_URL` in `.env`.
- Start webhook tunneling:
```bash
stripe listen --forward-to localhost:8000/billing/webhook
```

### Trial Logic
- On first sign-in, a `Subscription` row is created with `status='trialing'` and `trial_end=now+14 days`.
- Digests run during trial; after `trial_end`, digests are blocked until subscription is active.

### Referrals
- Each family gets a unique code. Share: `/auth/google/start?ref=CODE`.
- When the referred family pays their **first invoice**, a one-time credit is applied to the referrer’s Stripe **customer balance** (defaults to 500 cents). If the referrer has no customer yet, credit is stored as `pending_credit_cents` and applied after they subscribe.

### Add-ons (extra recipients)
- Two recipients are included. Each additional recipient is $1/month.
- `Billing > Subscribe` creates a Checkout Session with base line item + an add-on line item with quantity equal to `max(0, recipients - 2)`.
- Changing recipients in **Settings** will attempt to update the Stripe subscription items.

### Notes
- SQLite by default; delete `schoolbrief.db` to reset.
- Tokens are encrypted with `APP_SECRET_KEY` (Fernet). Use a proper KMS for production.
- Email sending uses Gmail SMTP App Password.

## Where to click
- `/` Landing
- `/auth/google/start` Sign in
- `/app` Dashboard
- `/settings` Family settings + referral link
- `/billing` Subscribe & manage billing
