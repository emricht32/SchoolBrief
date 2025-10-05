
from datetime import datetime
from typing import Optional
from sqlalchemy.orm import declarative_base, relationship, Mapped, mapped_column
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, Boolean, UniqueConstraint

Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    families = relationship("Family", back_populates="owner")
    providers = relationship("ProviderAccount", back_populates="user")

# --- Family -------------------------------------------------
class Family(Base):
    __tablename__ = "families"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    display_name: Mapped[Optional[str]] = mapped_column(String(200))

    owner = relationship("User", back_populates="families")
    children = relationship("Child", back_populates="family", cascade="all, delete-orphan")
    prefs = relationship("DigestPreference", back_populates="family", uselist=False, cascade="all, delete-orphan")

    # 👇 disambiguate: subscriptions where THIS family is the subscriber
    subs = relationship(
        "Subscription",
        back_populates="family",
        foreign_keys="Subscription.family_id",
        cascade="all, delete-orphan",
    )

    # (optional, view-only) subscriptions where THIS family is the referrer
    referrals_made = relationship(
        "Subscription",
        foreign_keys="Subscription.referrer_family_id",
        viewonly=True,
    )

    runs = relationship("DigestRun", back_populates="family")

class Child(Base):
    __tablename__ = "children"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id"))
    name: Mapped[str] = mapped_column(String(120))
    grade: Mapped[Optional[str]] = mapped_column(String(50))
    school_name: Mapped[Optional[str]] = mapped_column(String(200))

    family = relationship("Family", back_populates="children")

class ProviderAccount(Base):
    __tablename__ = "provider_accounts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    provider: Mapped[str] = mapped_column(String(50), default="google")
    email_on_provider: Mapped[Optional[str]] = mapped_column(String(320))
    scopes: Mapped[Optional[str]] = mapped_column(Text)  # space-separated
    token_json_enc: Mapped[Optional[str]] = mapped_column(Text)  # Fernet encrypted
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="providers")

# app/models.py (or wherever DigestPreference lives)
class DigestPreference(Base):
    __tablename__ = "digest_prefs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id"))
    cadence: Mapped[str] = mapped_column(String(20), default="weekly")  # daily/weekly
    send_time_local: Mapped[str] = mapped_column(String(5), default="07:00")
    timezone: Mapped[str] = mapped_column(String(64), default="America/Los_Angeles")
    days_of_week: Mapped[Optional[str]] = mapped_column(String(50))  # CSV "0,2,4"
    include_keywords: Mapped[Optional[str]] = mapped_column(Text)     # CSV
    school_domains: Mapped[Optional[str]] = mapped_column(Text)       # CSV
    to_addresses: Mapped[Optional[str]] = mapped_column(Text)         # CSV

    # NEW
    detail_level: Mapped[str] = mapped_column(String(20), default="full")  # "full" | "focused"

    family = relationship("Family", back_populates="prefs")


# --- Subscription -------------------------------------------
class Subscription(Base):
    __tablename__ = "subscriptions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # two FKs pointing at families.id:
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id"))
    referrer_family_id: Mapped[Optional[int]] = mapped_column(ForeignKey("families.id"))

    stripe_customer_id: Mapped[Optional[str]] = mapped_column(String(120))
    stripe_subscription_id: Mapped[Optional[str]] = mapped_column(String(120))
    plan: Mapped[Optional[str]] = mapped_column(String(120))
    status: Mapped[Optional[str]] = mapped_column(String(30))  # trialing/active/past_due/canceled
    current_period_end: Mapped[Optional[datetime]] = mapped_column(DateTime)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False)
    trial_end: Mapped[Optional[datetime]] = mapped_column(DateTime)
    base_included_recipients: Mapped[int] = mapped_column(Integer, default=2)
    extra_recipients: Mapped[int] = mapped_column(Integer, default=0)
    first_payment_recorded: Mapped[bool] = mapped_column(Boolean, default=False)
    pending_credit_cents: Mapped[int] = mapped_column(Integer, default=0)

    # 👇 disambiguate both sides
    family = relationship(
        "Family",
        back_populates="subs",
        foreign_keys=[family_id],
    )
    referrer = relationship(
        "Family",
        foreign_keys=[referrer_family_id],
    )


class ReferralCode(Base):
    __tablename__ = "referral_codes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id"))
    code: Mapped[str] = mapped_column(String(32), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    uses: Mapped[int] = mapped_column(Integer, default=0)

class DigestRun(Base):
    __tablename__ = "digest_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id"))
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    cadence: Mapped[Optional[str]] = mapped_column(String(20))
    messages_scanned: Mapped[int] = mapped_column(Integer, default=0)
    items_found: Mapped[int] = mapped_column(Integer, default=0)
    email_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[Optional[str]] = mapped_column(Text)

    family = relationship("Family", back_populates="runs")

class ProcessedEmail(Base):
    __tablename__ = "processed_emails"
    id = Column(Integer, primary_key=True)
    family_id = Column(Integer, ForeignKey("families.id"), nullable=False)
    gmail_msg_id = Column(String(128), nullable=False)  # Gmail message id
    content_hash = Column(String(64), nullable=False)   # sha256 hex
    subject = Column(Text, nullable=True)
    processed_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    __table_args__ = (UniqueConstraint("family_id", "content_hash", name="uix_family_hash"),)

class OneLiner(Base):
    __tablename__ = "one_liners"
    id = Column(Integer, primary_key=True)
    family_id = Column(Integer, ForeignKey("families.id"), nullable=False)
    source_msg_id = Column(String(128), nullable=False)     # Gmail message id for traceability
    one_liner = Column(Text, nullable=False)                # short sentence
    when_ts = Column(DateTime, nullable=True)               # normalized datetime (UTC) if any
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    date_string = Column(String(20), nullable=True)     # e.g. "2025-09-04"
    time_string = Column(String(20), nullable=True)     # e.g. "3:15 PM"
    domain      = Column(String(255), nullable=True)    # e.g. "schoology.com"


class SchoologyItem(Base):
    """Normalized Schoology assignment/event/test.

    We keep it minimal and idempotent so repeated syncs do not duplicate data.
    """
    __tablename__ = "schoology_items"
    id = Column(Integer, primary_key=True)
    family_id = Column(Integer, ForeignKey("families.id"), nullable=False)
    provider_account_id = Column(Integer, ForeignKey("provider_accounts.id"), nullable=False)
    schoology_id = Column(String(64), nullable=False)          # assignment/event id
    item_type = Column(String(32), nullable=False)             # assignment|event|update|other
    title = Column(String(500), nullable=True)
    description = Column(Text, nullable=True)
    due_at = Column(DateTime, nullable=True)                   # UTC converted
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    course_title = Column(String(255), nullable=True)
    raw_json = Column(Text, nullable=True)                     # debugging/trace

    __table_args__ = (
        UniqueConstraint("family_id", "schoology_id", name="uix_family_schoology_id"),
    )
