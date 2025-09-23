import pytest
from unittest.mock import patch, MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models import Base, User, Family, DigestPreference, ProcessedEmail
from app.ingest_job import process_forwarded_emails_and_update_domains
import os

@pytest.fixture
def db_session(tmp_path):
    db_url = f"sqlite:///{tmp_path}/test.db"
    engine = create_engine(db_url)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    engine.dispose()

@patch("app.ingest_job.imaplib.IMAP4_SSL")
@patch("app.ingest_job.email_mod.message_from_bytes")
def test_process_forwarded_emails_and_update_domains(mock_message_from_bytes, mock_imap, db_session):
    # Setup test user/family/pref
    user = User(email="forwarder@example.com")
    db_session.add(user)
    db_session.commit()
    fam = Family(owner_user_id=user.id)
    db_session.add(fam)
    db_session.commit()
    pref = DigestPreference(family_id=fam.id, to_addresses="forwarder@example.com", school_domains="")
    db_session.add(pref)
    db_session.commit()

    # Mock IMAP
    mock_mail = MagicMock()
    mock_imap.return_value = mock_mail
    mock_mail.login.return_value = ("OK", [])
    mock_mail.select.return_value = ("OK", [])
    mock_mail.search.return_value = ("OK", [b'1'])
    mock_mail.fetch.return_value = ("OK", [(None, b"rawbytes")])
    mock_mail.store.return_value = ("OK", [])
    mock_mail.logout.return_value = ("OK", [])

    # Mock email parsing
    mock_msg = MagicMock()
    mock_msg.get.return_value = "forwarder@example.com"
    mock_msg.get_payload.return_value = "From: Teacher <teacher@school.org>\nBody text"
    mock_message_from_bytes.return_value = mock_msg

    # Set env var for IMAP_PASS
    os.environ["FORWARD_IMAP_PASS"] = "dummy"

    # Run
    added = process_forwarded_emails_and_update_domains(db_session)
    db_session.commit()
    # Check
    pref = db_session.query(DigestPreference).filter_by(family_id=fam.id).first()
    assert "school.org" in (pref.school_domains or "")
    assert added == 1
    processed = db_session.query(ProcessedEmail).filter_by(family_id=fam.id).first()
    assert processed is not None
