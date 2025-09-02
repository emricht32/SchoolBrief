
import os, json
import stripe
from dotenv import load_dotenv

load_dotenv()    
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse, PlainTextResponse
from sqlalchemy.orm import Session
from .db import SessionLocal
from .models import User, Family, Subscription
from .stripe_sync import compute_extra_recipients

router = APIRouter()
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

def _db():
    return SessionLocal()

def _current_user(db: Session, request: Request):
    email = request.session.get("user_email")
    if not email:
        return None
    return db.query(User).filter_by(email=email).first()

@router.get("", response_class=HTMLResponse)
def billing_index(request: Request):
    from fastapi.templating import Jinja2Templates
    templates = Jinja2Templates(directory="templates")
    db = _db()
    try:
        user = _current_user(db, request)
        if not user:
            return RedirectResponse("/?flash=Please+sign+in")
        fam = db.query(Family).filter_by(owner_user_id=user.id).first()
        sub = db.query(Subscription).filter_by(family_id=fam.id).order_by(Subscription.id.desc()).first()
        return templates.TemplateResponse("billing.html", {"request": request, "user": user, "family": fam, "sub": sub})
    finally:
        db.close()

@router.post("/checkout")
def create_checkout(request: Request):
    db = _db()
    try:
        user = _current_user(db, request)
        if not user:
            return RedirectResponse("/?flash=Please+sign+in")
        fam = db.query(Family).filter_by(owner_user_id=user.id).first()
        price_id = os.getenv("STRIPE_PRICE_ID")
        addon_price = os.getenv("STRIPE_ADDON_PRICE_ID")
        base_url = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000")
        if not price_id or not stripe.api_key:
            raise HTTPException(status_code=500, detail="Stripe not configured")

        extra_qty = 0
        if fam.prefs:
            extra_qty = compute_extra_recipients(fam.prefs, 2)
        line_items=[{"price": price_id, "quantity": 1}]
        if addon_price and extra_qty > 0:
            line_items.append({"price": addon_price, "quantity": extra_qty})

        session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=line_items,
            success_url=f"{base_url}/billing?flash=Subscription+active",
            cancel_url=f"{base_url}/billing?flash=Checkout+canceled",
            metadata={"family_id": str(fam.id), "user_email": user.email},
            automatic_tax={"enabled": True}
        )
        return RedirectResponse(session.url, status_code=303)
    finally:
        db.close()

@router.post("/portal")
def create_portal(request: Request):
    db = _db()
    try:
        user = _current_user(db, request)
        if not user:
            return RedirectResponse("/?flash=Please+sign+in")
        fam = db.query(Family).filter_by(owner_user_id=user.id).first()
        sub = db.query(Subscription).filter_by(family_id=fam.id).order_by(Subscription.id.desc()).first()
        base_url = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000")
        if not sub or not sub.stripe_customer_id:
            return RedirectResponse("/billing?flash=No+active+subscription")
        portal = stripe.billing_portal.Session.create(customer=sub.stripe_customer_id, return_url=f"{base_url}/billing")
        return RedirectResponse(portal.url, status_code=303)
    finally:
        db.close()

@router.post("/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET")
    event = None
    try:
        if webhook_secret:
            event = stripe.Webhook.construct_event(payload=payload, sig_header=sig_header, secret=webhook_secret)
        else:
            event = stripe.Event.construct_from(json.loads(payload), stripe.api_key)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    db = _db()
    try:
        et = event["type"]

        if et == "checkout.session.completed":
            sess = event["data"]["object"]
            customer = sess.get("customer")
            subscription = sess.get("subscription")
            metadata = sess.get("metadata", {}) or {}
            family_id = int(metadata.get("family_id", "0"))
            if family_id:
                sub = db.query(Subscription).filter_by(family_id=family_id).first()
                if not sub:
                    sub = Subscription(family_id=family_id)
                sub.stripe_customer_id = customer
                sub.stripe_subscription_id = subscription
                sub.status = "active"
                db.add(sub); db.commit()

                # apply any pending credit (referral) if exists
                if sub.pending_credit_cents and customer:
                    try:
                        stripe.Customer.create_balance_transaction(customer=customer, amount=-abs(sub.pending_credit_cents), currency="usd", description="Referral credit")
                        sub.pending_credit_cents = 0
                        db.add(sub); db.commit()
                    except Exception:
                        pass

        elif et in ("customer.subscription.created","customer.subscription.updated"):
            sub_obj = event["data"]["object"]
            status = sub_obj.get("status")
            sub_id = sub_obj.get("id")
            current_period_end = sub_obj.get("current_period_end")
            cust_id = sub_obj.get("customer")
            sub = db.query(Subscription).filter((Subscription.stripe_subscription_id==sub_id) | (Subscription.stripe_customer_id==cust_id)).first()
            if sub:
                sub.status = status
                if current_period_end:
                    import datetime
                    sub.current_period_end = datetime.datetime.utcfromtimestamp(current_period_end)
                db.add(sub); db.commit()

        elif et == "customer.subscription.deleted":
            sub_obj = event["data"]["object"]
            sub_id = sub_obj.get("id")
            sub = db.query(Subscription).filter(Subscription.stripe_subscription_id==sub_id).first()
            if sub:
                sub.status = "canceled"
                db.add(sub); db.commit()

        elif et == "invoice.paid":
            inv = event["data"]["object"]
            cust_id = inv.get("customer")
            sub_id = inv.get("subscription")
            sub = db.query(Subscription).filter((Subscription.stripe_subscription_id==sub_id) | (Subscription.stripe_customer_id==cust_id)).first()
            if sub and not sub.first_payment_recorded:
                sub.first_payment_recorded = True
                db.add(sub); db.commit()
                # Referral credit to referrer
                if sub.referrer_family_id:
                    ref_sub = db.query(Subscription).filter_by(family_id=sub.referrer_family_id).first()
                    credit = int(os.getenv("REFERRAL_CREDIT_AMOUNT_CENTS","499"))
                    if ref_sub:
                        if ref_sub.stripe_customer_id:
                            try:
                                stripe.Customer.create_balance_transaction(customer=ref_sub.stripe_customer_id, amount=-abs(credit), currency="usd", description="Referral credit")
                            except Exception:
                                # if failing, queue as pending
                                ref_sub.pending_credit_cents += credit
                        else:
                            ref_sub.pending_credit_cents += credit
                        db.add(ref_sub); db.commit()

        return PlainTextResponse("ok", status_code=200)
    finally:
        db.close()
