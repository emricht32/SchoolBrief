
import os
from starlette.middleware.sessions import SessionMiddleware

SESSION_SECRET_KEY = os.getenv("SESSION_SECRET_KEY", "dev-session-secret")

def add_session_middleware(app):
    app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET_KEY, same_site="lax")
