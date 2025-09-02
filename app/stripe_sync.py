
import os, stripe
from .utils import csv_to_list

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

def compute_extra_recipients(pref, base_included: int = 2) -> int:
    recipients = csv_to_list(pref.to_addresses)
    return max(0, len(recipients) - (base_included or 2))

def ensure_subscription_items(sub, pref):
    base_price = os.getenv("STRIPE_PRICE_ID")
    addon_price = os.getenv("STRIPE_ADDON_PRICE_ID")
    if not sub.stripe_subscription_id or not base_price:
        return
    s = stripe.Subscription.retrieve(sub.stripe_subscription_id, expand=["items.data.price"])
    items = s["items"]["data"]
    # find base and addon
    base_item = next((it for it in items if it["price"]["id"] == base_price), None)
    addon_item = next((it for it in items if addon_price and it["price"]["id"] == addon_price), None)
    extra_qty = compute_extra_recipients(pref, sub.base_included_recipients or 2)
    if addon_price:
        if addon_item:
            stripe.Subscription.modify(
                s["id"],
                proration_behavior="none",
                items=[{"id": addon_item["id"], "quantity": extra_qty if extra_qty>0 else 0}]
            )
        elif extra_qty > 0:
            stripe.Subscription.modify(
                s["id"],
                proration_behavior="none",
                items=[{"price": addon_price, "quantity": extra_qty}]
            )
