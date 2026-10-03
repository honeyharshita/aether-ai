import datetime as dt
import hashlib
import logging
import secrets
import smtplib
from email.message import EmailMessage
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from fastapi.responses import RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session
import requests

from app.database import get_db
from app.models import User, AuditLog, PasswordResetToken
from app.config import settings
from app.schemas import RegisterRequest, LoginRequest, TokenResponse, RefreshRequest, UserOut
from app.auth import (
    hash_password, verify_password, create_access_token, create_refresh_token,
    decode_token, get_current_user, ROLES,
)


class GoogleAuthRequest(BaseModel):
    credential: str


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetCompleteRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)


router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
logger = logging.getLogger(__name__)


@router.post("/register", response_model=UserOut, status_code=201)
def register(req: RegisterRequest, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == req.email).first():
        raise HTTPException(status_code=400, detail="Email already registered")
    role = req.role if req.role in ROLES else "VIEWER"
    user = User(
        email=req.email,
        hashed_password=hash_password(req.password),
        full_name=req.full_name,
        role=role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    db.add(AuditLog(user_id=user.id, action="user.registered", detail={"email": user.email}))
    db.commit()
    return user


@router.post("/login", response_model=TokenResponse)
def login(req: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == req.email).first()
    if not user or not verify_password(req.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    db.add(AuditLog(user_id=user.id, action="user.login", detail={}))
    db.commit()
    return TokenResponse(
        access_token=create_access_token(user),
        refresh_token=create_refresh_token(user),
    )


def _password_reset_email_configured():
    return bool(settings.SMTP_HOST and settings.SMTP_FROM_EMAIL)


def _send_password_reset_email(recipient: str, reset_url: str):
    message = EmailMessage()
    message["Subject"] = "Reset your AetherAI password"
    message["From"] = settings.SMTP_FROM_EMAIL
    message["To"] = recipient
    message.set_content(
        "A password reset was requested for your AetherAI account.\n\n"
        f"Use this link within one hour to choose a new password:\n{reset_url}\n\n"
        "If you did not request this, you can ignore this email."
    )
    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as server:
        if settings.SMTP_STARTTLS:
            server.starttls()
        if settings.SMTP_USERNAME:
            server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
        server.send_message(message)


@router.post("/forgot-password")
def request_password_reset(req: PasswordResetRequest, db: Session = Depends(get_db)):
    if not _password_reset_email_configured():
        raise HTTPException(
            status_code=503,
            detail="Password reset email is not configured on this server. Set SMTP_HOST and SMTP_FROM_EMAIL.",
        )

    generic_message = "If an account exists for that email, password reset instructions have been sent."
    user = db.query(User).filter(func.lower(User.email) == str(req.email).lower()).first()
    if not user:
        return {"message": generic_message}

    now = dt.datetime.utcnow()
    db.query(PasswordResetToken).filter(
        PasswordResetToken.user_id == user.id,
        PasswordResetToken.used_at.is_(None),
    ).update({PasswordResetToken.used_at: now}, synchronize_session=False)

    raw_token = secrets.token_urlsafe(32)
    reset_record = PasswordResetToken(
        user_id=user.id,
        token_hash=hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
        expires_at=now + dt.timedelta(hours=1),
    )
    db.add(reset_record)
    db.commit()

    reset_url = f"{settings.FRONTEND_URL.rstrip('/')}/#password-reset={raw_token}"
    try:
        _send_password_reset_email(user.email, reset_url)
    except (OSError, smtplib.SMTPException) as exc:
        reset_record.used_at = dt.datetime.utcnow()
        db.add(reset_record)
        db.commit()
        logger.error("Password reset email delivery failed (%s)", type(exc).__name__)
    return {"message": generic_message}


@router.post("/reset-password")
def complete_password_reset(req: PasswordResetCompleteRequest, db: Session = Depends(get_db)):
    token_hash = hashlib.sha256(req.token.encode("utf-8")).hexdigest()
    now = dt.datetime.utcnow()
    reset_record = db.query(PasswordResetToken).filter(
        PasswordResetToken.token_hash == token_hash,
        PasswordResetToken.used_at.is_(None),
        PasswordResetToken.expires_at > now,
    ).first()
    if not reset_record:
        raise HTTPException(status_code=400, detail="This password reset link is invalid or expired")

    user = db.query(User).filter(User.id == reset_record.user_id).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=400, detail="This password reset link is invalid or expired")

    reset_record.used_at = now
    user.hashed_password = hash_password(req.new_password)
    db.query(PasswordResetToken).filter(
        PasswordResetToken.user_id == user.id,
        PasswordResetToken.used_at.is_(None),
    ).update({PasswordResetToken.used_at: now}, synchronize_session=False)
    db.add(AuditLog(user_id=user.id, action="user.password_reset", detail={}))
    db.commit()
    return {"message": "Password reset successfully. You can now sign in."}


@router.post("/google", response_model=TokenResponse)
def google_login(req: GoogleAuthRequest, db: Session = Depends(get_db)):
    if not settings.GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=503, detail="Google authentication is not configured")

    try:
        tokeninfo_resp = requests.get(
            "https://oauth2.googleapis.com/tokeninfo",
            params={"id_token": req.credential},
            timeout=10,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=503, detail=f"Google verification failed: {exc}")

    if tokeninfo_resp.status_code != 200:
        raise HTTPException(status_code=401, detail="Google token verification failed")

    payload = tokeninfo_resp.json()
    if payload.get("aud") != settings.GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=401, detail="Google token audience mismatch")
    email_verified = payload.get("email_verified")
    if email_verified is not True and str(email_verified).strip().lower() != "true":
        raise HTTPException(status_code=401, detail="Google email is not verified")

    email = (payload.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(status_code=401, detail="Google account did not include an email")

    user = db.query(User).filter(User.email == email).first()
    if not user:
        user = User(
            email=email,
            hashed_password=hash_password(f"google-oauth:{email}"),
            full_name=payload.get("name") or email.split("@")[0],
            role="VIEWER",
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    db.add(AuditLog(user_id=user.id, action="user.login.google", detail={"email": user.email}))
    db.commit()
    return TokenResponse(
        access_token=create_access_token(user),
        refresh_token=create_refresh_token(user),
    )


@router.get("/github/config")
def github_oauth_config():
    return {"configured": bool(settings.GITHUB_CLIENT_ID and settings.GITHUB_CLIENT_SECRET)}


@router.get("/github")
def github_login():
    if not settings.GITHUB_CLIENT_ID or not settings.GITHUB_CLIENT_SECRET:
        raise HTTPException(status_code=503, detail="GitHub sign-in is not configured")

    state = secrets.token_urlsafe(32)
    params = urlencode({
        "client_id": settings.GITHUB_CLIENT_ID,
        "redirect_uri": settings.GITHUB_REDIRECT_URI,
        "scope": "read:user user:email repo",
        "prompt": "select_account",
        "state": state,
    })
    response = RedirectResponse(f"https://github.com/login/oauth/authorize?{params}")
    response.set_cookie(
        "github_oauth_state", state, max_age=600, httponly=True,
        secure=settings.ENV == "prod", samesite="lax",
        path="/api/v1/auth/github/callback",
    )
    return response


def _github_login_error(reason: str) -> RedirectResponse:
    return RedirectResponse(f"{settings.FRONTEND_URL.rstrip('/')}/#github_error={reason}")


@router.get("/github/callback")
def github_callback(
    request: Request,
    code: str = None,
    state: str = None,
    error: str = None,
    db: Session = Depends(get_db),
):
    expected_state = request.cookies.get("github_oauth_state")
    if not state or not expected_state or not secrets.compare_digest(state, expected_state):
        return _github_login_error("invalid_state")
    if error:
        return _github_login_error("access_denied")
    if not code:
        return _github_login_error("missing_code")

    try:
        token_response = requests.post(
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": settings.GITHUB_CLIENT_ID,
                "client_secret": settings.GITHUB_CLIENT_SECRET,
                "code": code,
                "redirect_uri": settings.GITHUB_REDIRECT_URI,
            },
            timeout=10,
        )
        token_response.raise_for_status()
        github_token = token_response.json().get("access_token")
        if not github_token:
            return _github_login_error("token_exchange_failed")

        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {github_token}",
        }
        profile_response = requests.get("https://api.github.com/user", headers=headers, timeout=10)
        profile_response.raise_for_status()
        profile = profile_response.json()
        email_response = requests.get("https://api.github.com/user/emails", headers=headers, timeout=10)
        email_response.raise_for_status()
        verified_emails = [entry for entry in email_response.json() if entry.get("verified")]
    except (requests.RequestException, ValueError):
        return _github_login_error("github_verification_failed")

    email_entry = next((entry for entry in verified_emails if entry.get("primary")), None)
    email_entry = email_entry or (verified_emails[0] if verified_emails else None)
    if not email_entry:
        return _github_login_error("verified_email_required")

    email = email_entry["email"].strip().lower()
    user = db.query(User).filter(User.email == email).first()
    if not user:
        user = User(
            email=email,
            hashed_password=hash_password(f"github-oauth:{profile['id']}"),
            full_name=profile.get("name") or profile.get("login") or email.split("@")[0],
            role="VIEWER",
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    db.add(AuditLog(user_id=user.id, action="user.login.github", detail={"email": user.email}))
    db.commit()
    frontend = settings.FRONTEND_URL.rstrip("/") + "/#" + urlencode({
        "access_token": create_access_token(user),
        "github_access_token": github_token,
    })
    response = RedirectResponse(frontend)
    response.delete_cookie("github_oauth_state", path="/api/v1/auth/github/callback")
    return response


@router.get("/github/repositories")
def github_repositories(
    user: User = Depends(get_current_user),
    github_token: str = Header(default=None, alias="X-GitHub-Token"),
):
    if not github_token:
        raise HTTPException(status_code=401, detail="Sign in with GitHub to list your repositories")

    repositories = []
    page = 1
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {github_token}",
    }
    while True:
        try:
            response = requests.get(
                "https://api.github.com/user/repos",
                headers=headers,
                params={
                    "visibility": "all",
                    "affiliation": "owner,collaborator,organization_member",
                    "sort": "updated",
                    "per_page": 100,
                    "page": page,
                },
                timeout=10,
            )
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=f"GitHub repository lookup failed: {exc}")
        if response.status_code in (401, 403):
            raise HTTPException(status_code=401, detail="GitHub token is invalid or lacks repository access")
        if response.status_code != 200:
            raise HTTPException(status_code=502, detail="GitHub repository lookup failed")

        page_repositories = response.json()
        repositories.extend({
            "full_name": repo["full_name"],
            "private": repo.get("private", False),
            "default_branch": repo.get("default_branch", "main"),
            "description": repo.get("description"),
            "html_url": repo.get("html_url"),
            "updated_at": repo.get("updated_at"),
        } for repo in page_repositories)
        if len(page_repositories) < 100:
            return repositories
        page += 1


@router.post("/refresh", response_model=TokenResponse)
def refresh(req: RefreshRequest, db: Session = Depends(get_db)):
    payload = decode_token(req.refresh_token)
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Wrong token type")
    user = db.query(User).filter(User.id == payload["sub"]).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return TokenResponse(
        access_token=create_access_token(user),
        refresh_token=create_refresh_token(user),
    )


@router.post("/logout")
def logout(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # Stateless JWT: logout is audited; a real production system would also
    # add the token jti to a short-lived revocation blocklist in Redis.
    db.add(AuditLog(user_id=user.id, action="user.logout", detail={}))
    db.commit()
    return {"status": "logged_out"}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user
