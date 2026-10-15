import urllib.parse
import os
import time
import random
import uuid
import re
import io
import base64
import json
import logging
from pathlib import Path
from flask import Flask, render_template, render_template_string, request, jsonify, session, redirect, url_for, send_file, Response, make_response
from werkzeug.utils import secure_filename
import msal

import config
import models
import resume_bot
import cloud_align
import resume_versions
import application_tracker
import outreach_ai
import resume_editor
import docx_editor
import application_brief
import regions
import reply_inbox
import inbox_requirements
import work_backfill
import threading
import apify_service
import linkedin_sourcing
import harvest_direct
import sourcing_store
import linkedin_ingest
import education_filters
import education_match
import gmail_multi_manager
import us_job_scrapers
import india_job_scrapers
import sourcing_tracker
import vendors
import job_experience
from datetime import datetime, timedelta, timezone, date as date_cls
from flask.json.provider import DefaultJSONProvider
import job_description_fetch
from templates_bundle import EMBEDDED_LOGIN_HTML, EMBEDDED_DASHBOARD_HTML, EMBEDDED_STYLE_CSS, EMBEDDED_APP_JS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class _JSONProvider(DefaultJSONProvider):
    """Dates go to the page as '2026-10-13 12:00:00' - the text SQLite returns and the screens slice.
    On Postgres the driver returns datetime objects, which Flask would send as 'Tue, 13 Oct 2026 ...'
    (the tracker showed 'Tue, 13 Oc' and the interview-date box came up empty)."""

    @staticmethod
    def default(o):
        if isinstance(o, datetime):
            return o.strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(o, date_cls):
            return o.isoformat()
        return DefaultJSONProvider.default(o)


app = Flask(__name__)
app.json = _JSONProvider(app)
app.secret_key = config.SECRET_KEY
app.permanent_session_lifetime = timedelta(days=30)   # "Keep me signed in" on the login page
if config.USING_DEFAULT_SECRET_KEY and config.ENV == "production":
    logger.warning("SECURITY: SECRET_KEY is not set, so sessions are signed with the publicly known default key. "
                   "Set SECRET_KEY to a long random value in the server environment.")
if config.ALLOW_DEV_LOGIN and config.ENV == "production":
    logger.warning("SECURITY: ALLOW_DEV_LOGIN is enabled in production - /dev-login logs in as admin without a password.")

# Ensure required directories exist
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RESUMES_DIR = os.path.join(BASE_DIR, "data", "resumes")
TOKENS_DIR = os.path.join(BASE_DIR, "data", "tokens")
os.makedirs(RESUMES_DIR, exist_ok=True)
os.makedirs(TOKENS_DIR, exist_ok=True)

# Initialize database
models.init_db()
# Load the Gemini library in the background so the first Resume Optimizer run doesn't wait for it.
threading.Thread(target=resume_bot.prewarm, daemon=True).start()

@app.errorhandler(models.DatabaseUnavailable)
def _database_unavailable(ex):
    """Postgres unreachable: say so plainly (503) - never pretend something was saved."""
    if request.path.startswith("/api/"):
        return jsonify({"error": str(ex)}), 503
    return make_response(f"<h3>{ex}</h3>", 503)


def current_user():
    return session.get("user")


def _int_or_none(value):
    """A whole number from a form field, or None when it's empty / not a number (never a made-up default)."""
    try:
        return int(str(value).strip()) if str(value or "").strip() else None
    except ValueError:
        return None


_PRIVATE_CANDIDATE_FIELDS = ("gmail_app_password", "gmail_token_path")


def _public_candidate(cand):
    """A consultant record for the browser: never the Gmail App Password (or the OAuth token file path).
    `SELECT c.*` returned the password in /api/consultants responses - the page never used it."""
    return {k: v for k, v in (cand or {}).items() if k not in _PRIVATE_CANDIDATE_FIELDS}


def _view_regions(user):
    """Regions the user is looking at: a recruiter's own team; an admin's switcher choice (default Both)."""
    if not user:
        return set()
    conn = models.get_db_connection()
    try:
        stored = regions.user_region(conn, user["id"])
    finally:
        conn.close()
    return regions.view_regions(user, stored, session.get("view_region"))


def _team_country_problem(user, country):
    """A recruiter can't put a consultant in the OTHER team's country - the consultant would vanish from
    their own list. Admins can (that is how a consultant moves team)."""
    if not user or "Admin" in (user.get("role") or ""):
        return ""
    target = regions.candidate_region({"country": country})
    mine = next(iter(_view_regions(user)))
    if target != mine:
        return (f"This consultant's country ({country or 'not set'}) belongs to the {target} team, and you are on the {mine} team. "
                f"Ask an admin to add or move this consultant.")
    return ""


def _job_country_for(user, requested):
    """The Jobs feed follows the team: US jobs for USA, India jobs for India (admins viewing Both choose)."""
    vr = _view_regions(user)
    if len(vr) == 1:
        return regions.JOB_COUNTRY[next(iter(vr))]
    return requested


def can_access_candidate(user, candidate_id) -> bool:
    """Admins can act on any consultant; a recruiter only on consultants assigned to them."""
    try:
        cid = int(candidate_id)
    except (TypeError, ValueError):
        return False
    is_admin = "Admin" in (user or {}).get("role", "")
    cand = models.get_candidate_by_id(cid, user_id=user["id"], is_admin=is_admin) if user else None
    # Teams: a consultant belongs to the USA or India team by country; only that team (or an admin
    # viewing it) can act on them.
    return bool(cand) and regions.candidate_region(cand) in _view_regions(user)

@app.before_request
def require_login_for_student_api():
    """Every /api/students/* route (search, paid LinkedIn search, add-to-bench,
    pitch, CSV export) needs a logged-in user. Several of them previously had no
    check, so anyone could create bench candidates or trigger paid searches."""
    if request.method != "OPTIONS" and request.path.startswith("/api/students/") and not current_user():
        return jsonify({"error": "Login required"}), 401


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type,Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET,PUT,POST,DELETE,OPTIONS"
    return response

# --- Static Asset Routing ---

@app.route("/static/css/style.css")
def serve_custom_css():
    css_file = os.path.join(BASE_DIR, "static", "css", "style.css")
    if os.path.exists(css_file):
        with open(css_file, "r", encoding="utf-8") as f:
            return Response(f.read(), mimetype="text/css")
    return Response(EMBEDDED_STYLE_CSS, mimetype="text/css")

@app.route("/static/js/app.js")
def serve_custom_js():
    js_file = os.path.join(BASE_DIR, "static", "js", "app.js")
    if os.path.exists(js_file):
        with open(js_file, "r", encoding="utf-8") as f:
            resp = Response(f.read(), mimetype="application/javascript")
            resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            resp.headers["Pragma"] = "no-cache"
            return resp
    resp = Response(EMBEDDED_APP_JS, mimetype="application/javascript")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp

# --- Authentication ---

@app.route("/")
def home():
    return redirect(url_for("dashboard"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("dashboard"))

    error_msg = None
    if request.method == "POST":
        email = request.form.get("email") or (request.get_json(silent=True) or {}).get("email", "")
        password = request.form.get("password") or (request.get_json(silent=True) or {}).get("password", "")
        email = email.strip()

        user = models.authenticate_user(email, password)
        if user:
            session["user"] = user
            # "Keep me signed in": the login survives closing the browser (30 days); otherwise it ends with the browser.
            session.permanent = bool(request.form.get("remember") or (request.get_json(silent=True) or {}).get("remember"))
            models.log_activity(user["id"], user["name"], "User Logged In", "Auth", user["id"], f"User {user['name']} signed in")
            if request.is_json:
                return jsonify({"success": True, "redirect": url_for("dashboard")})
            return redirect(url_for("dashboard"))
        else:
            error_msg = "Invalid email or password. Please verify credentials."
            if request.is_json:
                return jsonify({"error": error_msg}), 401

    try:
        return render_template("login.html", error=error_msg, has_azure=config.HAS_AZURE_AUTH, allow_dev_login=config.ALLOW_DEV_LOGIN, env=config.ENV)
    except Exception:
        return render_template_string(EMBEDDED_LOGIN_HTML, error=error_msg, has_azure=config.HAS_AZURE_AUTH, allow_dev_login=config.ALLOW_DEV_LOGIN, env=config.ENV)

@app.route("/dev-login", methods=["GET", "POST"])
def dev_login():
    # This route logs in as the admin WITHOUT checking any password (it falls back to
    # get_or_create_user, which returns the existing admin account as-is), so it is
    # disabled unless ALLOW_DEV_LOGIN=1 is set on the server.
    if not config.ALLOW_DEV_LOGIN:
        return make_response("Not found", 404)
    user = models.authenticate_user("praveen@adroit-ai.com", "Admin@2026")
    if not user:
        user = models.get_or_create_user(
            name="Praveen Valipireddy",
            email="praveen@adroit-ai.com",
            role="Admin",
            avatar_url="https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=150&auto=format&fit=crop&q=80",
            password="Admin@2026"
        )
    session["user"] = user
    models.log_activity(
        user["id"], 
        user["name"], 
        "User Logged In", 
        "Auth", 
        user["id"], 
        f"Logged in via Dev Test Mode (User: {user['name']})"
    )
    return redirect(url_for("dashboard"))

@app.route("/logout")
def logout():
    user = current_user()
    if user:
        models.log_activity(user["id"], user["name"], "User Logged Out", "Auth", user["id"], f"User {user['name']} logged out")
    session.clear()
    return redirect(url_for("login"))

# --- Dashboard Main View ---

@app.route("/dashboard")
def dashboard():
    user = current_user()
    if not user:
        return redirect(url_for("login"))

    is_admin = ("Admin" in user.get("role", ""))
    selected_recruiter_id = request.args.get("recruiter_id", type=int) if is_admin else user["id"]
    stats = models.get_dashboard_stats(user_id=selected_recruiter_id, is_admin=(is_admin and not request.args.get("recruiter_id")))
    view_regions = _view_regions(user)
    candidates = regions.filter_candidates(
        models.get_candidates(user_id=selected_recruiter_id, is_admin=(is_admin and not request.args.get("recruiter_id"))), view_regions)
    for c in candidates:
        c_status = gmail_multi_manager.is_candidate_connected(c["id"])
        c["gmail_connected"] = c_status.get("connected", False)

    recruiters = [r for r in models.get_users() if regions.normalize(r.get("region")) in view_regions or "Admin" in (r.get("role") or "")] if is_admin else []
    view_region = (session.get("view_region") or "Both") if is_admin else next(iter(view_regions))

    try:
        return render_template(
            "dashboard.html",
            user=user,
            stats=stats,
            candidates=candidates,
            recruiters=recruiters,
            selected_recruiter_id=selected_recruiter_id if is_admin else None,
            view_region=view_region,
            env=config.ENV,
            has_apify=bool(config.APIFY_API_TOKEN) or (harvest_direct.enabled() and harvest_direct.configured()),
            has_google_oauth=gmail_multi_manager.oauth_configured(),
            has_gemini=resume_bot.gemini_configured(),
            has_claude=resume_bot.claude_configured(),
            claude_model=resume_bot.CLAUDE_MODEL,
            has_grok=resume_bot.xai_configured(),
            has_openrouter=resume_bot.openrouter_configured()
        )
    except Exception as e:
        logger.warning(f"Using embedded template fallback: {e}")
        return render_template_string(
            EMBEDDED_DASHBOARD_HTML,
            user=user,
            stats=stats,
            candidates=candidates,
            recruiters=recruiters,
            selected_recruiter_id=selected_recruiter_id if is_admin else None,
            view_region=view_region,
            env=config.ENV,
            has_apify=bool(config.APIFY_API_TOKEN) or (harvest_direct.enabled() and harvest_direct.configured()),
            has_google_oauth=gmail_multi_manager.oauth_configured(),
            has_gemini=resume_bot.gemini_configured(),
            has_claude=resume_bot.claude_configured(),
            claude_model=resume_bot.CLAUDE_MODEL,
            has_grok=resume_bot.xai_configured(),
            has_openrouter=resume_bot.openrouter_configured()
        )

# --- Stats & Activity API ---

@app.route("/api/stats")
def api_stats():
    user = current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    is_admin = ("Admin" in user.get("role", ""))
    recruiter_id = request.args.get("recruiter_id", type=int)
    return jsonify(models.get_dashboard_stats(user_id=recruiter_id if is_admin else user["id"], is_admin=(is_admin and not recruiter_id)))

@app.route("/api/activity")
def api_activity():
    if not current_user():
        return jsonify({"error": "Unauthorized"}), 401
    return jsonify(models.get_activity_logs(limit=25))

# --- Consultant / Candidate Management APIs ---

@app.route("/api/candidates", methods=["GET", "POST", "OPTIONS"])
@app.route("/api/consultants", methods=["GET", "POST", "OPTIONS"])
def api_consultants():
    if request.method == "OPTIONS":
        return jsonify({}), 200

    user = current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    if request.method == "POST":
        # Check if multipart form with resume upload or JSON
        if request.content_type and "multipart/form-data" in request.content_type:
            name = request.form.get("name", "").strip()
            email = request.form.get("email", "").strip()
            phone = request.form.get("phone", "").strip()
            title = request.form.get("title", "Technical Consultant").strip()
            skills = request.form.get("skills", "").strip()
            exp = _int_or_none(request.form.get("experience"))
            rate = (request.form.get("rate") or "").strip()
            visa = (request.form.get("visa_status") or "").strip()
            location = (request.form.get("location") or "").strip()
            country = request.form.get("country", "United States").strip()
            summary = request.form.get("summary", "").strip()

            resume_filename = None
            resume_path = None
            resume_text = None
            resume_raw = None

            if "resume_file" in request.files:
                file = request.files["resume_file"]
                if file.filename:
                    filename = secure_filename(file.filename)
                    dest_path = os.path.join(RESUMES_DIR, filename)
                    file.save(dest_path)
                    resume_filename = filename
                    resume_path = dest_path

                    # Extract text for ATS analysis
                    try:
                        with open(dest_path, "rb") as rf:
                            resume_raw = rf.read()
                        resume_text = resume_bot.extract_text_from_file_bytes(resume_raw, filename)
                    except Exception as e:
                        logger.error(f"Error extracting resume text: {e}")
        else:
            resume_raw = None
            data = request.json or {}
            name = data.get("name", "").strip()
            email = data.get("email", "").strip()
            phone = data.get("phone", "").strip()
            title = data.get("title", "Technical Consultant").strip()
            skills = data.get("skills", "").strip()
            exp = _int_or_none(data.get("experience"))
            rate = (data.get("rate") or "").strip()
            visa = (data.get("visa_status") or "").strip()
            location = (data.get("location") or "").strip()
            country = data.get("country", "United States").strip()
            summary = data.get("summary", "").strip()
            resume_filename = data.get("resume_filename")
            resume_path = data.get("resume_path")
            resume_text = data.get("resume_text")

        if not name or not email:
            return jsonify({"error": "Consultant name and email are required"}), 400

        is_admin = ("Admin" in user.get("role", ""))
        team_problem = _team_country_problem(user, country)
        if team_problem:
            return jsonify({"error": team_problem}), 400
        assigned_user_id = user["id"]
        if is_admin:
            req_assigned = (request.form.get("assigned_user_id") if request.form else None) or (request.get_json(silent=True) or {}).get("assigned_user_id")
            if req_assigned:
                try: assigned_user_id = int(req_assigned)
                except: pass

        if country == "India":
            visa = ""   # no visa / relocation question for India-based consultants
        cand_id = models.create_candidate(
            name=name,
            email=email,
            phone=phone,
            title=title,
            primary_skills=skills,
            experience_years=exp,
            target_rate=rate,
            visa_status=visa,
            status="Available",
            location=location,
            country=country,
            resume_filename=resume_filename,
            resume_path=resume_path,
            resume_text=resume_text,
            resume_summary=summary,
            gmail_account=email,
            assigned_user_id=assigned_user_id
        )
        if resume_raw and resume_filename:
            try:
                models.save_resume_file(cand_id, resume_filename, resume_raw)
            except Exception as e:
                logger.error(f"Could not store the original resume file for consultant {cand_id}: {e}")

        models.log_activity(
            user["id"],
            user["name"],
            "Added Consultant",
            "Candidate",
            cand_id,
            f"Added US bench consultant: {name} ({title}) - {rate}"
        )

        return jsonify({"success": True, "candidate_id": cand_id, "name": name})

    # GET request - return candidates scoped to current recruiter
    is_admin = ("Admin" in user.get("role", ""))
    recruiter_id = request.args.get("recruiter_id", type=int)
    candidates = regions.filter_candidates(
        models.get_candidates(user_id=recruiter_id if is_admin else user["id"], is_admin=(is_admin and not recruiter_id)), _view_regions(user))
    for c in candidates:
        c_status = gmail_multi_manager.is_candidate_connected(c["id"])
        c["gmail_connected"] = c_status.get("connected", False)

    return jsonify([_public_candidate(c) for c in candidates])

@app.route("/api/consultants/<int:candidate_id>", methods=["GET", "PUT", "DELETE", "OPTIONS"])
def api_consultant_detail(candidate_id):
    if request.method == "OPTIONS":
        return jsonify({}), 200

    user = current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    is_admin = ("Admin" in user.get("role", ""))
    cand = models.get_candidate_by_id(candidate_id, user_id=user["id"], is_admin=is_admin)
    if not cand:
        return jsonify({"error": "Consultant not found or unauthorized"}), 404

    if request.method == "GET":
        _ensure_resume_text(cand)
        cand_status = gmail_multi_manager.is_candidate_connected(candidate_id)
        cand["gmail_connected"] = cand_status.get("connected", False)
        return jsonify(_public_candidate(cand))

    if request.method == "DELETE":
        models.delete_candidate(candidate_id, user_id=user["id"], is_admin=is_admin)
        models.log_activity(
            user["id"],
            user["name"],
            "Deleted Consultant",
            "Candidate",
            candidate_id,
            f"Removed consultant profile #{candidate_id} ({cand['name']})"
        )
        return jsonify({"success": True, "deleted_id": candidate_id})

    if request.method == "PUT":
        data = request.json or {}
        update_fields = {}
        for key in ["name", "email", "phone", "title", "primary_skills", "experience_years", "target_rate", "visa_status", "status", "location", "country", "resume_summary"]:
            if key in data:
                update_fields[key] = data[key]
        if "country" in update_fields:
            team_problem = _team_country_problem(user, update_fields["country"])
            if team_problem:
                return jsonify({"error": team_problem}), 400
        models.update_candidate(candidate_id, **update_fields)
        return jsonify({"success": True, "candidate_id": candidate_id})

@app.route("/api/consultants/<int:candidate_id>/upload-resume", methods=["POST", "OPTIONS"])
def api_consultant_upload_resume(candidate_id):
    if request.method == "OPTIONS":
        return jsonify({}), 200

    user = current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    if not can_access_candidate(user, candidate_id):
        return jsonify({"error": "Consultant not found"}), 404

    if "resume_file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["resume_file"]
    if not file.filename:
        return jsonify({"error": "No selected file"}), 400

    filename = secure_filename(file.filename)
    dest_path = os.path.join(RESUMES_DIR, f"{candidate_id}_{filename}")
    file.save(dest_path)

    # Extract text
    resume_text = ""
    try:
        with open(dest_path, "rb") as rf:
            resume_raw = rf.read()
        resume_text = resume_bot.extract_text_from_file_bytes(resume_raw, filename)
        # Keep the original file itself in the database so the optimizer can edit it in place.
        models.save_resume_file(candidate_id, filename, resume_raw)
    except Exception as e:
        logger.error(f"Error extracting text from uploaded resume: {e}")

    models.update_candidate(
        candidate_id,
        resume_filename=filename,
        resume_path=dest_path,
        resume_text=resume_text
    )

    return jsonify({
        "success": True, 
        "filename": filename, 
        "path": dest_path,
        "text_preview": resume_text[:300] + "..." if resume_text else ""
    })

# --- Consultant Gmail OAuth Management ---

@app.route("/api/consultants/<int:candidate_id>/set-app-password", methods=["POST", "OPTIONS"])
def api_set_consultant_app_password(candidate_id):
    if request.method == "OPTIONS":
        return jsonify({}), 200

    user = current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    if not can_access_candidate(user, candidate_id):
        return jsonify({"error": "Consultant not found"}), 404

    data = request.json or {}
    gmail_account = data.get("gmail_account", "").strip()
    app_password = data.get("app_password", "").replace(" ", "").strip()

    if not gmail_account or not app_password:
        return jsonify({"error": "Gmail address and 16-character App Password are required."}), 400

    # Verify credentials via live IMAP login test
    valid, msg = gmail_multi_manager.verify_gmail_app_password(gmail_account, app_password)
    if not valid:
        return jsonify({"error": f"Failed to authenticate with Gmail: {msg}. Please ensure 2-Step Verification is enabled and the 16-character App Password is correct."}), 400

    models.update_candidate(
        candidate_id,
        gmail_account=gmail_account,
        gmail_app_password=app_password
    )

    models.log_activity(
        user["id"],
        user["name"],
        "Connected Gmail (App Password)",
        "Candidate",
        candidate_id,
        f"Verified & connected Gmail App Password for consultant #{candidate_id} ({gmail_account})"
    )

    return jsonify({
        "success": True, 
        "candidate_id": candidate_id, 
        "gmail_account": gmail_account,
        "message": f"Gmail ({gmail_account}) verified and connected successfully via App Password!"
    })

@app.route("/api/consultants/<int:candidate_id>/disconnect-gmail", methods=["POST"])
def api_disconnect_consultant_gmail(candidate_id):
    """Remove the connected Gmail (App Password / OAuth) so another Gmail can be connected."""
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401
    if not can_access_candidate(user, candidate_id):
        return jsonify({"error": "Consultant not found"}), 404
    cand = models.get_candidate_by_id(candidate_id) or {}
    old = cand.get("gmail_account") or ""
    gmail_multi_manager.disconnect_candidate_gmail(candidate_id)
    models.log_activity(user["id"], user["name"], "Disconnected Gmail", "Candidate", candidate_id,
                        f"Removed Gmail {old or '(none)'} from consultant #{candidate_id}")
    return jsonify({"success": True, "candidate_id": candidate_id, "removed": old})


@app.route("/api/consultants/<int:candidate_id>/connect-gmail")
def api_connect_candidate_gmail(candidate_id):
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    if not can_access_candidate(user, candidate_id):
        return jsonify({"error": "Consultant not found"}), 404
    redirect_uri = f"{request.host_url.rstrip('/')}/api/auth/google/callback"
    auth_url, verifier = gmail_multi_manager.get_auth_url(candidate_id, redirect_uri=redirect_uri)
    if not auth_url:
        return jsonify({"error": "Could not generate Google OAuth URL. Ensure oauth_credentials.json is present."}), 400
    if verifier:
        session[f"oauth_verifier_{candidate_id}"] = verifier
    return redirect(auth_url)

@app.route("/api/auth/google/callback")
def api_google_auth_callback():
    code = request.args.get("code")
    state = request.args.get("state") # candidate_id
    error = request.args.get("error")

    if error or not code or not state:
        return render_template_string("""
        <html><body style="font-family:sans-serif; background:#0f172a; color:#f8fafc; text-align:center; padding:50px;">
            <h2 style="color:#ef4444;">❌ Gmail Authorization Failed</h2>
            <p>{{ error or 'Missing code/state' }}</p>
            <a href="/dashboard" style="display:inline-block; padding:10px 20px; background:#3b82f6; color:#fff; text-decoration:none; border-radius:6px; margin-top:20px;">Return to Dashboard</a>
        </body></html>
        """, error=error), 400

    candidate_id = int(state)
    redirect_uri = f"{request.host_url.rstrip('/')}/api/auth/google/callback"
    code_verifier = session.get(f"oauth_verifier_{candidate_id}")
    
    result = gmail_multi_manager.exchange_code_and_save_token(
        candidate_id, 
        code, 
        redirect_uri=redirect_uri,
        code_verifier=code_verifier
    )

    if result.get("success"):
        return redirect(f"/dashboard?auth_success=1&cand_id={candidate_id}")
    else:
        return render_template_string("""
        <html><body style="font-family:sans-serif; background:#0f172a; color:#f8fafc; text-align:center; padding:50px;">
            <h2 style="color:#ef4444;">❌ Token Exchange Error</h2>
            <p>{{ error }}</p>
            <a href="/dashboard" style="display:inline-block; padding:10px 20px; background:#3b82f6; color:#fff; text-decoration:none; border-radius:6px; margin-top:20px;">Return to Dashboard</a>
        </body></html>
        """, error=result.get("error")), 400

@app.route("/api/consultants/<int:candidate_id>/gmail-status")
def api_candidate_gmail_status(candidate_id):
    user = current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    if not can_access_candidate(user, candidate_id):
        return jsonify({"error": "Consultant not found"}), 404
    status = gmail_multi_manager.is_candidate_connected(candidate_id)
    return jsonify(status)

# --- US IT Job Searching & Live Scrapers ---

def _job_region(job) -> str:
    """A job's vendor list: India jobs -> India vendors, everything else -> USA vendors."""
    return "India" if (job.get("country") or "").strip().lower() == "india" else "USA"


def _cand_region(candidate_id) -> str:
    """A consultant's vendor list follows their team (country India -> India, else USA)."""
    cand = models.get_candidate_by_id(int(candidate_id)) if _int_or_none(candidate_id) else None
    return regions.candidate_region(cand) if cand else "USA"


def _vendor_region_for(user, data) -> str:
    """Which list a Vendors-tab / Paste-window request is about: the consultant's region when one is
    given (and visible to this user), else an explicit region, else the user's own team."""
    cid = _int_or_none((data or {}).get("candidate_id"))
    if cid and can_access_candidate(user, cid):
        return _cand_region(cid)
    explicit = vendors.norm_region((data or {}).get("region"), default="")
    if explicit:
        return explicit
    vr = _view_regions(user)
    return next(iter(vr)) if len(vr) == 1 else "USA"


def _tag_known_vendors(jobs, user):
    """job["vendor"] = {company, count} when the job's company is in this recruiter's own vendor list
    of the job's region (India jobs are checked against India vendors only)."""
    if not user or not jobs:
        return
    idx = {}
    conn = models.get_db_connection()
    try:
        for reg in {_job_region(j) for j in jobs if isinstance(j, dict)}:
            idx[reg] = vendors.vendor_index(conn, user["id"], reg)
    finally:
        conn.close()
    for j in jobs:
        if isinstance(j, dict):
            ix = idx.get(_job_region(j))
            if not ix or (not ix["domains"] and not ix["companies"]):
                continue
            company, rows = vendors.match_job(ix, j)
            if rows:
                j["vendor"] = {"company": company, "count": len(rows)}


@app.route("/api/jobs/search", methods=["POST", "OPTIONS"])
def api_jobs_search():
    if request.method == "OPTIONS":
        return jsonify({}), 200

    data = request.json or {}
    query = data.get("query", "").strip()
    location = data.get("location", "").strip()
    source = data.get("source", "All")
    job_type = data.get("job_type", "Contract")
    contract_only = data.get("contract_only", False)
    is_24h_only = data.get("is_24h_only", False)
    live_scrape = data.get("live_scrape", False)
    country = _job_country_for(current_user(), (data.get("country") or "United States").strip())
    my_experience = _int_or_none(data.get("my_experience"))
    include_unstated = data.get("include_unstated", True) is not False

    results = models.get_jobs(
        query=query if query else None,
        location=location if location and location.lower() != "united states" else None,
        source=source if source != "All" else None,
        job_type=job_type if job_type != "All" else None,
        contract_only=contract_only,
        is_24h_only=is_24h_only,
        country=country,
        my_experience=my_experience,
        include_unstated=include_unstated
    )

    # If 0 results or live_scrape requested, trigger a live scrape of the selected market. This no
    # longer requires a typed query: since is_24h_only now genuinely hides stale/example data, an
    # empty-query tab load with nothing fresh in the DB must still go get real jobs rather than
    # show an empty table - "Software Engineer" is the same generic default already used elsewhere
    # (triggerUsScrape) when no keyword is given.
    if len(results) == 0 or live_scrape:
        scrape_query = query or "Software Engineer"
        try:
            if country.lower() == "india":
                scrape_res = india_job_scrapers.run_multi_source_india_scrape(
                    keywords=[scrape_query], location=location or "India", save_to_db=True
                )
            else:
                scrape_res = us_job_scrapers.run_multi_source_us_scrape(
                    keywords=[scrape_query],
                    location=location or "United States",
                    contract_only=True,
                    save_to_db=True,
                    experience_years=my_experience
                )
            results = models.get_jobs(
                query=query if query else None,
                location=location if location and location.lower() != "united states" else None,
                source=source if source != "All" else None,
                job_type=job_type if job_type != "All" else None,
                contract_only=False,
                is_24h_only=is_24h_only,
                country=country,
                my_experience=my_experience,
                include_unstated=include_unstated
            )
            if not results and scrape_res.get("jobs"):
                results = scrape_res["jobs"]
        except Exception as e:
            logger.error(f"Live scrape on-demand error: {e}")

    _tag_known_vendors(results, current_user())

    return jsonify({
        "jobs": results,
        "results": results,
        "count": len(results),
        "query": query,
        "contract_only": contract_only,
        "is_24h_only": is_24h_only
    })

@app.route("/api/jobs/scrape-us", methods=["POST", "OPTIONS"])
def api_trigger_us_scrape():
    if request.method == "OPTIONS":
        return jsonify({}), 200

    user = current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json or {}
    keywords = data.get("keywords")
    location = data.get("location", "United States")
    contract_only = data.get("contract_only", True)

    scrape_res = us_job_scrapers.run_multi_source_us_scrape(
        keywords=keywords,
        location=location,
        contract_only=contract_only,
        save_to_db=True
    )

    models.log_activity(
        user["id"],
        user["name"],
        "Scraped 24h US Jobs",
        "Scraper",
        0,
        f"Scraped {scrape_res.get('count', 0)} US tech contract roles from Dice and LinkedIn"
    )

    return jsonify({
        "success": True,
        "count": scrape_res.get("count", 0),
        "saved_to_db": scrape_res.get("saved_to_db", 0),
        "message": f"Successfully scraped {scrape_res.get('count', 0)} fresh US contract jobs."
    })

@app.route("/api/jobs/scrape-india", methods=["POST", "OPTIONS"])
def api_trigger_india_scrape():
    """Scrapes real live India job postings (Naukri, Foundit/Monster India, LinkedIn) via
    Apify actors - each is pay-per-result (roughly $0.001/job), and an empty search costs
    nothing. Requires APIFY_API_TOKEN on the server, same as the LinkedIn Sourcing feature."""
    if request.method == "OPTIONS":
        return jsonify({}), 200

    user = current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    if not (config.APIFY_API_TOKEN or "").strip():
        return jsonify({"error": "APIFY_API_TOKEN is not configured on the server. India sourcing (Naukri, Foundit) needs it."}), 503

    data = request.json or {}
    keywords = data.get("keywords")
    location = data.get("location", "India")

    scrape_res = india_job_scrapers.run_multi_source_india_scrape(
        keywords=keywords,
        location=location,
        save_to_db=True
    )

    models.log_activity(
        user["id"],
        user["name"],
        "Scraped India Jobs",
        "Scraper",
        0,
        f"Scraped {scrape_res.get('count', 0)} India roles from Naukri, Foundit, and LinkedIn"
    )

    return jsonify({
        "success": True,
        "count": scrape_res.get("count", 0),
        "saved_to_db": scrape_res.get("saved_to_db", 0),
        "message": f"Successfully scraped {scrape_res.get('count', 0)} fresh India jobs."
    })

# --- In-Table Quick Email & Contact Update API ---

@app.route("/api/jobs/<int:job_id>/full-description", methods=["POST", "OPTIONS"])
def api_job_full_description(job_id):
    """Full job description for the Resume Optimizer. Pasted requirements already have it; for a
    LinkedIn/Dice posting it is read from that one public posting (free, on demand) and saved, so
    the next recruiter gets it instantly. Never invented: an unreadable posting returns 422."""
    if request.method == "OPTIONS":
        return jsonify({}), 200
    user = current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    job = models.get_job_by_id(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    if (job.get("full_description") or "").strip():
        return jsonify({"description": job["full_description"], "cached": True})
    if job.get("source") == "Manual Paste" and (job.get("description") or "").strip():
        return jsonify({"description": job["description"], "cached": True})
    text, reason = job_description_fetch.fetch_full_description(job.get("url"))
    if not text:
        return jsonify({"error": reason}), 422
    models.save_job_full_description(job_id, text)
    return jsonify({"description": text, "cached": False})


@app.route("/api/jobs/update-email", methods=["POST", "OPTIONS"])
def api_update_job_email():
    if request.method == "OPTIONS":
        return jsonify({}), 200

    user = current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json or {}
    job_id = data.get("job_id")
    email = data.get("recruiter_email", "").strip()
    phone = data.get("recruiter_phone", "").strip()
    name = data.get("recruiter_name", "").strip()
    salary = data.get("salary", "").strip()

    if not job_id:
        return jsonify({"error": "job_id is required"}), 400

    models.update_job_recruiter_info(
        job_id=int(job_id),
        email=email if email else None,
        phone=phone if phone else None,
        name=name if name else None,
        salary=salary if salary else None
    )

    models.log_activity(
        user["id"],
        user["name"],
        "Updated Recruiter Email",
        "Job",
        job_id,
        f"Saved recruiter email '{email}' for Job #{job_id}"
    )

    return jsonify({
        "success": True, 
        "job_id": job_id, 
        "recruiter_email": email,
        "recruiter_name": name,
        "recruiter_phone": phone,
        "salary": salary
    })

# --- 1-Click Gmail Draft Creation Outreach API ---


# --- Instant Manual Requirement Paste & Draft API ---

def extract_details_from_raw_jd(text: str) -> dict:
    email_match = re.search(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+', text)
    email = email_match.group(0) if email_match else ""

    salary_match = re.search(r'\$[\d,]+(?:\.\d+)?(?:\s*[-–—to]+\s*\$?[\d,]+(?:\.\d+)?)?(?:\s*\/\s*(?:hr|hour|yr|year|mo|month|annum|day))?', text, re.IGNORECASE)
    salary = salary_match.group(0) if salary_match else ""   # never an invented rate

    title = ""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    for line in lines:
        m = re.match(r'^(?:Role|Position|Job\s*Title|Title|Requirement)\s*[:\-=]\s*(.+)$', line, re.IGNORECASE)
        if m:
            clean_title = m.group(1).strip()
            if len(clean_title) > 2 and not clean_title.lower().startswith("for"):
                title = clean_title
                break

    if not title:
        for line in lines[:5]:
            if re.search(r'\b(engineer|developer|architect|lead|specialist|consultant|manager|analyst|administrator)\b', line, re.IGNORECASE):
                if len(line) <= 75 and not line.startswith("http") and not "@" in line:
                    title = line
                    break


    company = ""
    for line in lines:
        m = re.match(r'^(?:Client|End\s*Client|Company|Organization|Vendor)\s*[:\-=]\s*(.+)$', line, re.IGNORECASE)
        if m:
            clean_comp = m.group(1).strip()
            if 2 <= len(clean_comp) <= 60:
                company = clean_comp
                break

    if not company and email and "@" in email:
        domain = email.split("@")[1].split(".")[0]
        if domain not in ["gmail", "yahoo", "hotmail", "outlook", "icloud"]:
            company = domain.capitalize()

    return {
        "title": title,
        "company": company,
        "recruiter_email": email,
        "salary": salary
    }

@app.route("/api/outreach/parse-jd", methods=["POST", "OPTIONS"])
def api_parse_jd():
    if request.method == "OPTIONS":
        return jsonify({}), 200

    data = request.json or {}
    text = data.get("raw_jd_text", "").strip()
    if not text:
        return jsonify({"error": "No requirement text provided"}), 400

    extracted = extract_details_from_raw_jd(text)
    return jsonify(extracted)

_READ_SYSTEM = """You read a job requirement that a recruiter pasted (an email, a LinkedIn post or a JD).
Return ONLY JSON: {"title": "", "company": "client or company hiring", "location": "", "recruiter_email": "only an address
that appears in the text", "rate": "only if stated", "employment_type": "C2C / W2 / 1099 / Full-time / Contract / unknown",
"skills": ["key skills, max 10"]}. Leave a field empty when the text doesn't say it - never guess."""


def _duplicates(cid, title, company, recruiter_email):
    """This consultant's applications in the last 30 days for the same requirement (same title + company,
    or the same recruiter address)."""
    since = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")
    conn = models.get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("""SELECT a.id, a.stage, a.drafted_at, a.applied_date, a.updated_at, j.title, j.company,
                              LOWER(COALESCE(a.recruiter_email, j.recruiter_email, '')) AS rec
                       FROM applications a JOIN jobs j ON j.id = a.job_id WHERE a.candidate_id = ?""", (cid,))
        rows = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
    t, c, e = (title or "").strip().lower(), (company or "").strip().lower(), (recruiter_email or "").strip().lower()
    out = []
    for r in rows:
        when = str(r.get("drafted_at") or r.get("applied_date") or r.get("updated_at") or "")[:10]
        if when and when < since:
            continue
        same_job = t and (r["title"] or "").strip().lower() == t and (not c or (r["company"] or "").strip().lower() == c)
        same_rec = e and r["rec"] == e
        if same_job or same_rec:
            out.append({"application_id": r["id"], "title": r["title"], "company": r["company"], "when": when,
                        "status": application_tracker.STATUS_LABELS.get(r["stage"], r["stage"] or ""),
                        "why": "same requirement" if same_job else "same recruiter"})
    return out[:5]


@app.route("/api/outreach/read-requirement", methods=["POST"])
def api_read_requirement():
    """Paste window: the AI reads the pasted text (fields + what the posting asks applicants to do) and
    says whether this consultant already applied for it recently."""
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401
    data = request.get_json(silent=True) or {}
    raw = str(data.get("raw_text") or "").strip()[:20000]
    if data.get("duplicates_only"):      # consultant changed: only the "already applied" check, no new AI read
        cid = _int_or_none(data.get("candidate_id"))
        dups = _duplicates(cid, data.get("title"), data.get("company"), data.get("recruiter_email")) if cid and can_access_candidate(user, cid) else []
        return jsonify({"duplicates": dups})
    if len(raw) < 40:
        return jsonify({"error": "Paste the requirement first."}), 400
    fields = {}
    if resume_bot.ai_configured():
        try:
            fields, _ = reply_inbox._ai(_READ_SYSTEM, f"REQUIREMENT:\n{raw[:12000]}")
        except Exception as ex:
            logger.warning(f"Reading the requirement failed: {ex}")
            fields = {}
    regex = extract_details_from_raw_jd(raw)
    low = raw.lower()
    rec = str(fields.get("recruiter_email") or "").strip().lower()
    out = {
        "title": str(fields.get("title") or "").strip() or regex.get("title") or "",
        "company": str(fields.get("company") or "").strip() or regex.get("company") or "",
        "location": str(fields.get("location") or "").strip(),
        "recruiter_email": rec if rec and rec in low else (regex.get("recruiter_email") or ""),
        "rate": str(fields.get("rate") or "").strip() or regex.get("salary") or "",
        "employment_type": str(fields.get("employment_type") or "").strip(),
        "skills": [str(x).strip() for x in (fields.get("skills") or []) if str(x).strip()][:10],
        "ai": bool(fields),
    }
    out["brief"] = {k: v for k, v in application_brief.brief(raw).items() if k != "model"}
    cid = _int_or_none(data.get("candidate_id"))
    out["duplicates"] = _duplicates(cid, out["title"], out["company"], out["recruiter_email"]) if cid and can_access_candidate(user, cid) else []
    return jsonify(out)


@app.route("/api/outreach/match-check", methods=["POST"])
def api_match_check():
    """Match % for this consultant and the pasted JD (the optimizer's step 1, cached for Optimize)."""
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401
    data = request.get_json(silent=True) or {}
    cid = _int_or_none(data.get("candidate_id"))
    if not cid or not can_access_candidate(user, cid):
        return jsonify({"error": "Consultant not found"}), 404
    cand = models.get_candidate_by_id(cid)
    found = models.find_resume_file(cand)
    docx = found[1] if found and found[0].lower().endswith(".docx") else None
    text = (cand.get("resume_text") or "") if docx is None else ""
    if docx is None and not text.strip() and found:
        text = resume_bot.extract_text_from_file_bytes(found[1], found[0], strict=True)
    res = resume_bot.match_check(text, str(data.get("jd_text") or "")[:20000], docx_bytes=docx)
    return (jsonify(res), 200) if not res.get("error") else (jsonify(res), 400)


@app.route("/api/outreach/paste-and-draft", methods=["POST", "OPTIONS"])
def api_paste_and_draft():
    if request.method == "OPTIONS":
        return jsonify({}), 200

    user = current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json or {}
    candidate_id = data.get("candidate_id")
    raw_jd_text = data.get("raw_jd_text", "").strip()
    job_title = data.get("job_title", "").strip()
    company = data.get("company", "").strip()
    recruiter_email = data.get("recruiter_email", "").strip()
    salary = data.get("salary", "").strip()
    custom_notes = data.get("custom_notes", "").strip()

    if not candidate_id:
        return jsonify({"error": "Candidate ID is required"}), 400
    if not can_access_candidate(user, candidate_id):
        return jsonify({"error": "Consultant not found"}), 404
    if not raw_jd_text and not job_title:
        return jsonify({"error": "Please provide requirement text or job title"}), 400

    # Auto-extract missing fields from raw text
    if raw_jd_text:
        extracted = extract_details_from_raw_jd(raw_jd_text)
        if not job_title:
            job_title = extracted["title"]
        if not company:
            company = extracted["company"]
        if not recruiter_email:
            recruiter_email = extracted["recruiter_email"]
        if not salary:
            salary = extracted["salary"]

    if not recruiter_email or "@" not in recruiter_email:
        return jsonify({"error": "Recruiter email is required. Please type recruiter email."}), 400
    if not job_title:
        return jsonify({"error": "Job title is required. Please type the job title."}), 400

    # Vendor contacts ticked in the modal: re-checked here - only this recruiter's own, active ones,
    # never the To address, at most vendors.MAX_BCC.
    conn = models.get_db_connection()
    try:
        bcc_contacts = vendors.contacts_for_bcc(conn, user["id"], data.get("bcc_contact_ids") or [],
                                                exclude_emails=[recruiter_email], region=_cand_region(candidate_id))
    finally:
        conn.close()

    # Save as a job record - only what the requirement actually says (no invented rate / location).
    job_id = models.save_or_update_scraped_job({
        "title": job_title,
        "company": company,
        "location": "",
        "job_type": "",
        "salary": salary,
        "source": "Manual Paste",
        "url": "",
        "recruiter_email": recruiter_email,
        "description": raw_jd_text or f"Manual Requirement for {job_title} at {company}",
        "matched_skills": job_title,
        "match_score": 95
    })

    # Call multi-manager to draft in Gmail with attached .docx resume
    version_id = _int_or_none(data.get("optimized_resume_id"))
    attachment = _resume_version_for(user, candidate_id, version_id) if version_id else None
    if version_id and not attachment:
        return jsonify({"error": "That saved resume version doesn't belong to this consultant."}), 400
    result = gmail_multi_manager.create_candidate_draft(
        candidate_id=int(candidate_id),
        job_id=job_id,
        custom_to_email=recruiter_email,
        custom_notes=custom_notes,
        custom_subject=(data.get("custom_subject") or "").strip() or None,
        custom_body=(data.get("custom_body") or "").strip() or None,
        bcc=[c["email"] for c in bcc_contacts],
        attachment=attachment[:2] if attachment else None,
        acting_user_id=user["id"],
        resume_version_id=attachment[2] if attachment else None
    )

    if not result.get("success"):
        return jsonify(result), 400

    if bcc_contacts:
        conn = models.get_db_connection()
        try:
            vendors.mark_emailed(conn, [c["id"] for c in bcc_contacts], f"{job_title} - {result.get('candidate_name', '')}")
        finally:
            conn.close()
    result["job_id"] = job_id
    return jsonify(result)

@app.route("/api/outreach/create-draft", methods=["POST", "OPTIONS"])
def api_create_outreach_draft():
    if request.method == "OPTIONS":
        return jsonify({}), 200

    user = current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json or {}
    candidate_id = data.get("candidate_id")
    job_id = data.get("job_id")
    custom_to_email = data.get("custom_to_email")
    custom_notes = data.get("custom_notes", "")

    if not candidate_id or not job_id:
        return jsonify({"error": "candidate_id and job_id are required"}), 400
    if not can_access_candidate(user, candidate_id):
        return jsonify({"error": "Consultant not found"}), 404

    custom_subject = data.get("custom_subject")
    custom_body = data.get("custom_body")

    # Known vendor: BCC this recruiter's own active contacts at the job's company (max 10).
    bcc_contacts = []
    job = models.get_job_by_id(int(job_id))
    if job and data.get("vendor_bcc", True) is not False:
        conn = models.get_db_connection()
        try:
            to_email = (custom_to_email or job.get("recruiter_email") or "").strip()
            bcc_contacts = vendors.bcc_for_job(vendors.vendor_index(conn, user["id"], _cand_region(candidate_id)), job, to_email)
        finally:
            conn.close()

    # A requirement from the consultant's Gmail, reviewed in the Optimize window: answer in the vendor's thread.
    req_id, req_row, reply_to = _int_or_none(data.get("requirement_id")), None, None
    if req_id:
        conn = models.get_db_connection()
        try:
            req_row = inbox_requirements.get(conn, req_id)
        finally:
            conn.close()
        if not req_row or req_row["candidate_id"] != int(candidate_id) or req_row.get("job_id") != int(job_id):
            return jsonify({"error": "That requirement doesn't belong to this consultant and job."}), 400
        reply_to = inbox_requirements.reply_headers(req_row, custom_to_email or "")

    chosen = _resume_version_for(user, candidate_id, _int_or_none(data.get("optimized_resume_id")), job_id)
    result = gmail_multi_manager.create_candidate_draft(
        candidate_id=int(candidate_id),
        job_id=int(job_id),
        custom_to_email=custom_to_email,
        custom_notes=custom_notes,
        custom_subject=custom_subject,
        custom_body=custom_body,
        bcc=[c["email"] for c in bcc_contacts],
        attachment=chosen[:2] if chosen else None,
        acting_user_id=user["id"],
        resume_version_id=chosen[2] if chosen else None,
        reply_to=reply_to
    )

    if not result.get("success"):
        return jsonify(result), 400
    if req_row:
        conn = models.get_db_connection()
        try:
            inbox_requirements.mark_drafted(conn, req_id, chosen[0] if chosen else "", chosen[2] if chosen else None)
        finally:
            conn.close()
        result["in_thread"] = bool(reply_to)

    if bcc_contacts:
        conn = models.get_db_connection()
        try:
            vendors.mark_emailed(conn, [c["id"] for c in bcc_contacts], f"{job.get('title', '')} - {result.get('candidate_name', '')}")
        finally:
            conn.close()
    return jsonify(result)


AUTO_VENDOR_DRAFTS_PER_DAY = 10


@app.route("/api/outreach/auto-vendor-drafts", methods=["POST"])
def api_auto_vendor_drafts():
    """Browse Jobs: draft the consultant for the shown jobs that come from one of the recruiter's
    known vendors - To = the job's recruiter (else the first vendor contact), BCC = the other
    contacts (max 10). Only jobs that fit the consultant's experience, never the same job +
    consultant twice, at most AUTO_VENDOR_DRAFTS_PER_DAY per consultant per day (UTC).
    Drafts only - nothing is sent. Gmail drafts are free (no paid API)."""
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401
    data = request.get_json(silent=True) or {}
    candidate_id = _int_or_none(data.get("candidate_id"))
    if not candidate_id or not can_access_candidate(user, candidate_id):
        return jsonify({"error": "Consultant not found"}), 404
    job_ids = []
    for j in (data.get("job_ids") or [])[:200]:
        jid = _int_or_none(j)
        if jid and jid not in job_ids:
            job_ids.append(jid)
    cand = models.get_candidate_by_id(candidate_id)
    years = _int_or_none(cand.get("experience_years"))
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d 00:00:00")

    conn = models.get_db_connection()
    try:
        idx = vendors.vendor_index(conn, user["id"], regions.candidate_region(cand))
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM vendor_auto_drafts WHERE candidate_id = ? AND created_at >= ?", (candidate_id, today))
        used = int(cur.fetchone()[0])
        cur.execute("SELECT job_id FROM vendor_auto_drafts WHERE candidate_id = ?", (candidate_id,))
        done = {r[0] for r in cur.fetchall()}
        cur.execute("SELECT job_id FROM applications WHERE candidate_id = ? AND draft_id IS NOT NULL AND draft_id <> ''", (candidate_id,))
        done |= {r[0] for r in cur.fetchall()}
    finally:
        conn.close()

    skipped = {"already_drafted": 0, "experience": 0, "not_vendor": 0, "daily_limit": 0}
    plan = []
    for jid in job_ids:
        job = models.get_job_by_id(jid)
        if not job:
            continue
        company, rows = vendors.match_job(idx, job)
        if not rows:
            skipped["not_vendor"] += 1
        elif jid in done:
            skipped["already_drafted"] += 1
        elif years is not None and not job_experience.fits(job_experience.classify(job), years):
            skipped["experience"] += 1
        elif used + len(plan) >= AUTO_VENDOR_DRAFTS_PER_DAY:
            skipped["daily_limit"] += 1
        else:
            plan.append((job, company, rows))

    created, error = [], ""
    for job, company, rows in plan:
        job_email = (job.get("recruiter_email") or "").strip()
        to_email = job_email if "@" in job_email else rows[0]["email"]
        bcc_contacts = vendors.bcc_for_job(idx, job, to_email)
        result = gmail_multi_manager.create_candidate_draft(
            candidate_id=candidate_id, job_id=job["id"], custom_to_email=to_email,
            bcc=[c["email"] for c in bcc_contacts], save_to_email=False, acting_user_id=user["id"])
        if not result.get("success"):
            error = result.get("error") or "Draft failed"
            if result.get("needs_auth"):
                break   # Gmail not connected: every other draft would fail the same way
            continue
        conn = models.get_db_connection()
        try:
            conn.cursor().execute("INSERT INTO vendor_auto_drafts (candidate_id, job_id, user_id, to_email, bcc_count) VALUES (?, ?, ?, ?, ?)",
                                  (candidate_id, job["id"], user["id"], to_email, len(bcc_contacts)))
            conn.commit()
            vendors.mark_emailed(conn, [c["id"] for c in bcc_contacts], f"{job.get('title', '')} - {cand.get('name', '')} (auto)")
        finally:
            conn.close()
        created.append({"job_id": job["id"], "title": job.get("title"), "company": company, "to": to_email, "bcc": len(bcc_contacts)})
    if created:
        models.log_activity(user["id"], user["name"], "Auto Vendor Drafts", "Candidate", candidate_id,
                            f"{len(created)} known-vendor drafts for {cand.get('name', '')}")
    return jsonify({"created": created, "skipped": skipped, "error": error, "candidate_name": cand.get("name", ""),
                    "remaining_today": max(0, AUTO_VENDOR_DRAFTS_PER_DAY - used - len(created))})


@app.route("/api/ai/personalize-draft", methods=["POST", "OPTIONS"])
def api_ai_personalize_draft():
    if request.method == "OPTIONS":
        return jsonify({}), 200

    user = current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json or {}
    candidate_id = data.get("candidate_id")
    job_id = data.get("job_id")
    user_instruction = data.get("instruction", "").strip()
    current_subject = data.get("current_subject", "")
    current_body = data.get("current_body", "")

    if not candidate_id or not (job_id or data.get("job_title") or data.get("jd_text")):
        return jsonify({"error": "candidate_id and job_id are required"}), 400
    if not can_access_candidate(user, candidate_id):
        return jsonify({"error": "Consultant not found"}), 404

    cand = models.get_candidate_by_id(int(candidate_id))
    job = _job_from_request(data)
    if not cand or not job:
        return jsonify({"error": "Candidate or Job not found"}), 404

    to_email = (data.get("to_email") or job.get("recruiter_email") or "").strip()
    out = {"success": True, "to_email": job.get("recruiter_email") or "", "consultant_name": cand.get("name"),
           "job_title": job.get("title"), "company": job.get("company")}
    if not user_instruction:
        # Opening the window. With a full job description the email follows the JD's application
        # instructions (application_brief: where to send, subject, its questions answered from the
        # resume that will be attached); otherwise the standard pitch. Plus what will be attached / BCC'd.
        version_id = _int_or_none(data.get("optimized_resume_id"))
        jd = str(data.get("jd_text") or job.get("full_description") or "").strip()
        if not jd and len((job.get("description") or "").strip()) >= 400:
            jd = job["description"].strip()
        checklist, needs = [], []
        if current_body:
            subject, body = current_subject or gmail_multi_manager.build_subject(cand, job), current_body
        elif jd:
            b = application_brief.build(cand, job, jd, version_id=version_id, default_to=to_email)
            subject, body, checklist, needs = b["subject"], b["body"], b["checklist"], b["needs_input"]
            if b["to"]:
                out["to_email"] = to_email = b["to"]
        else:
            subject, body = gmail_multi_manager.build_subject(cand, job), gmail_multi_manager.generate_consultant_pitch(cand, job, "")
        out.update(subject=subject, body=body, checklist=checklist, needs_input=needs,
                   rate_on_file=bool((cand.get("target_rate") or "").strip()),
                   us_based=gmail_multi_manager._is_us_based(cand), **_draft_extras(user, cand, job, to_email, version_id))
        return jsonify(out)
    out.update(outreach_ai.edit_draft(cand, job, user_instruction, current_subject, current_body,
                                      resume_text=str(data.get("resume_text") or "")[:20000],
                                      history=data.get("history") if isinstance(data.get("history"), list) else None,
                                      jd_text=str(data.get("jd_text") or "")[:20000]))
    return jsonify(out)


def _job_from_request(data):
    """The job for a preview / AI edit: the saved job (job_id), or - in the Paste Requirement window,
    before anything is saved - the pasted requirement as typed."""
    jid = _int_or_none(data.get("job_id"))
    if jid:
        return models.get_job_by_id(jid)
    title = str(data.get("job_title") or "").strip()
    if not title and not str(data.get("jd_text") or "").strip():
        return None
    return {"id": None, "title": title or "this role", "company": str(data.get("company") or "").strip(),
            "recruiter_email": str(data.get("to_email") or "").strip(), "recruiter_name": "", "location": str(data.get("location") or ""),
            "description": str(data.get("jd_text") or "")[:20000], "source": "Manual Paste"}


@app.route("/api/outreach/application-preview", methods=["POST"])
def api_application_preview():
    """The email that "Save & Create Draft" will send, shown INSIDE the Optimize window before anything
    is saved: written from the optimized resume text on screen and the JD's instructions."""
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401
    data = request.get_json(silent=True) or {}
    cid = _int_or_none(data.get("candidate_id"))
    if not cid or not can_access_candidate(user, cid):
        return jsonify({"error": "Consultant not found"}), 404
    cand, job = models.get_candidate_by_id(cid), _job_from_request(data)
    if not cand or not job:
        return jsonify({"error": "Job not found"}), 404
    b = application_brief.build(cand, job, str(data.get("jd_text") or "")[:20000],
                                version_id=_int_or_none(data.get("optimized_resume_id")),
                                default_to=(data.get("to_email") or job.get("recruiter_email") or "").strip(),
                                resume_text=str(data.get("resume_text") or "")[:20000],
                                tailored=None if data.get("tailored") is None else bool(data.get("tailored")))
    return jsonify({"to": b["to"], "subject": b["subject"], "body": b["body"], "checklist": b["checklist"],
                    "needs_input": b["needs_input"], "fallback": b["fallback"]})


def _draft_extras(user, cand, job, to_email, version_id=None):
    """What Save to Gmail will attach and BCC - shown in the chat window before saving."""
    chosen = _resume_version_for(user, cand["id"], version_id, job.get("id"))
    if chosen:
        attachment, kind = chosen[0], "JD-tailored version"
    else:
        found = models.find_resume_file(cand, persist=False)
        attachment, kind = (found[0], "original resume") if found else ("", "")
    bcc = []
    conn = models.get_db_connection()
    try:
        bcc = [c["email"] for c in vendors.bcc_for_job(vendors.vendor_index(conn, user["id"], regions.candidate_region(cand)), job, to_email)]
    except Exception as ex:
        logger.warning(f"Vendor BCC preview failed: {ex}")
    finally:
        conn.close()
    return {"attachment": attachment, "attachment_kind": kind, "bcc": bcc}


# --- Pipeline APIs ---

@app.route("/api/pipeline")
def api_pipeline():
    user = current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    is_admin = ("Admin" in user.get("role", ""))
    recruiter_id = request.args.get("recruiter_id", type=int)
    return jsonify(models.get_pipeline_by_stages(user_id=recruiter_id if is_admin else user["id"], is_admin=(is_admin and not recruiter_id)))

@app.route("/api/pipeline/update-stage", methods=["POST"])
def api_update_pipeline_stage():
    user = current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json or {}
    app_id = data.get("application_id")
    new_stage = data.get("stage")
    notes = data.get("notes", "")

    if not app_id or not new_stage:
        return jsonify({"error": "application_id and stage are required"}), 400

    models.update_application_stage(app_id, new_stage, notes)
    return jsonify({"success": True, "stage": new_stage})

# --- Master Resume Bot APIs ---

@app.route("/api/resume-bot/optimize", methods=["POST", "OPTIONS"])
def api_resume_bot_optimize():
    if request.method == "OPTIONS":
        return jsonify({}), 200

    # Login required: this returns the (rewritten) resume text, and used to accept any
    # candidate_id from anyone - so a stranger could enumerate ids and read other people's resumes.
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401

    # Three ways in:
    #  - multipart with an attached .docx  -> edited IN PLACE (original formatting preserved)
    #  - JSON + use_stored_file + candidate_id -> the consultant's stored original .docx, same
    #  - JSON with resume_text (pasted / PDF / txt / edited) -> text path, Word file regenerated
    docx_bytes = None
    format_note = ""
    if request.content_type and "multipart/form-data" in request.content_type:
        jd_text = (request.form.get("jd_text") or "").strip()
        custom_instructions = (request.form.get("custom_instructions") or "").strip()
        resume_text = ""
        f = request.files.get("resume_file")
        if not f or not f.filename:
            return jsonify({"error": "No resume to optimize. Attach a .docx file or paste the resume text first."}), 400
        if not secure_filename(f.filename).lower().endswith(".docx"):
            return jsonify({"error": "Editing in the original format needs a .docx file. For other formats paste the text instead."}), 400
        docx_bytes = f.read()
        if len(docx_bytes) > 5 * 1024 * 1024:
            return jsonify({"error": "That file is over 5 MB - attach a smaller resume."}), 413
    else:
        data = request.get_json(silent=True) or {}
        resume_text = (data.get("resume_text") or "").strip()
        jd_text = (data.get("jd_text") or "").strip()
        custom_instructions = (data.get("custom_instructions") or "").strip()
        candidate_id = data.get("candidate_id")
        use_stored_file = bool(data.get("use_stored_file"))

        cand = None
        if candidate_id and (use_stored_file or not resume_text):
            # Only a consultant this user may see. Never invent a stand-in resume from their
            # name/title/skills - an optimization of made-up text is worse than an error.
            try:
                cand = models.get_candidate_by_id(int(candidate_id), user_id=user["id"], is_admin=("Admin" in user.get("role", "")))
            except (TypeError, ValueError):
                cand = None
        if cand and use_stored_file:
            docx_bytes = _stored_docx_bytes(cand)
            if docx_bytes is None:
                format_note = ("This consultant's original Word file is no longer stored (or was uploaded as a PDF/text file), so a clean "
                               "Word file was built from the resume text. Attach the original .docx to keep its exact formatting.")
        if cand and not resume_text:
            resume_text = (_ensure_resume_text(cand).get("resume_text") or "").strip()

        if not resume_text and docx_bytes is None:
            return jsonify({"error": "No resume to optimize. Attach a .docx / .pdf / .txt file or paste the resume text first."}), 400

    if not jd_text:
        return jsonify({"error": "Paste the client's Job Description (JD) first."}), 400

    result = resume_bot.optimize_resume_for_jd(
        resume_text=resume_text,
        jd_text=jd_text,
        custom_instructions=custom_instructions,
        docx_bytes=docx_bytes,
    )
    if result.get("error"):
        return jsonify(result), 400

    if docx_bytes is None and not format_note:
        format_note = ("Original formatting is only kept when the resume is a .docx file (attached or stored). This download is a clean "
                       "Word file built from the resume text.")
    result["format_note"] = "" if result.get("format_preserved") else format_note
    # Item: <Name>_<PrimarySkill>.docx - the name the version gets when the recruiter clicks Save.
    named_cand = None
    name_cid = _int_or_none((request.form.get("candidate_id") if request.form else None) or (request.get_json(silent=True) or {}).get("candidate_id"))
    if name_cid and can_access_candidate(user, name_cid):
        named_cand = models.get_candidate_by_id(name_cid)
    result["primary_skill"] = resume_versions.primary_skill(result, named_cand, jd_text)
    person = (named_cand or {}).get("name") or (result.get("candidate_name") if result.get("candidate_name_detected") else "") or "Consultant"
    result["suggested_filename"] = resume_versions.make_filename(
        person, result["primary_skill"], resume_versions.taken_names(name_cid) if named_cand else [])
    return jsonify(result)


def _stored_resume_file(cand):
    """(filename, bytes) of a consultant's original resume file - see models.find_resume_file."""
    return models.find_resume_file(cand)


def _stored_docx_bytes(cand):
    """The original .docx of a consultant the caller may see, or None."""
    found = _stored_resume_file(cand)
    if found and found[0].lower().endswith(".docx"):
        return found[1]
    return None


def _ensure_resume_text(cand):
    """A consultant can have a resume FILE but no extracted text - e.g. those created by the old
    startup seeding, which saved resume_path/resume_filename only. The Resume Optimizer reads the
    text, so it reported "No resume is on file" although the file was there. Extract it once,
    store it (and keep the file in the database so it survives deploys). Mutates and returns cand."""
    if (cand.get("resume_text") or "").strip():
        return cand
    found = _stored_resume_file(cand)
    if not found:
        return cand
    filename, data = found
    text = (resume_bot.extract_text_from_file_bytes(data, filename) or "").strip()
    if len(text) < 30:
        return cand
    models.update_candidate(cand["id"], resume_text=text,
                            **({} if cand.get("resume_filename") else {"resume_filename": filename}))
    if not models.get_resume_file(cand["id"]):
        models.save_resume_file(cand["id"], filename, data)
    cand["resume_text"] = text
    cand["resume_filename"] = cand.get("resume_filename") or filename
    logger.info(f"Extracted stored resume text for consultant #{cand['id']} ({filename}, {len(text)} chars)")
    return cand


APPLY_STATUSES = {"": "Not applied", "portal": "Applied in portal", "email": "Email sent", "both": "Applied in portal + email sent"}


@app.route("/api/jobs/<int:job_id>/apply-status", methods=["POST"])
def api_job_apply_status(job_id):
    """Jobs tab "Applied" column: how this job was applied to (per job)."""
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401
    status = str((request.get_json(silent=True) or {}).get("status") or "").strip()
    if status not in APPLY_STATUSES:
        return jsonify({"error": "Unknown status."}), 400
    job = models.get_job_by_id(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    cid = _int_or_none((request.get_json(silent=True) or {}).get("candidate_id"))
    conn = models.get_db_connection()
    try:
        conn.cursor().execute("UPDATE jobs SET apply_status = ? WHERE id = ?", (status, job_id))
        conn.commit()
        # Tracker: the row's Target Candidate applied this way (one consultant per job).
        if status and cid and can_access_candidate(user, cid):
            application_tracker.upsert(conn, job_id, cid, user["id"], stage="Applied", apply_method=status)
            cur = conn.cursor()
            cur.execute("UPDATE applications SET apply_method = ? WHERE job_id = ? AND candidate_id = ?", (status, job_id, cid))
            conn.commit()
    finally:
        conn.close()
    models.log_activity(user["id"], user["name"], "Job Apply Status", "Job", job_id,
                        f"{job.get('title', '')} at {job.get('company', '')}: {APPLY_STATUSES[status]}")
    return jsonify({"success": True, "job_id": job_id, "status": status, "label": APPLY_STATUSES[status]})


# ---------------------------------------------------------------- Application tracker (application_tracker.py)

def _tracker_candidate_ids(user, candidate_id=None):
    """Consultants whose applications this user may see: one (if allowed) or all of theirs (admin: all)."""
    if candidate_id:
        return [int(candidate_id)] if can_access_candidate(user, candidate_id) else []
    is_admin = "Admin" in (user.get("role") or "")
    cands = models.get_candidates(user_id=None if is_admin else user["id"], is_admin=is_admin)
    return [c["id"] for c in regions.filter_candidates(cands, _view_regions(user))]


def _tracker_app_allowed(user, app_id):
    conn = models.get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT candidate_id FROM applications WHERE id = ?", (app_id,))
        r = cur.fetchone()
    finally:
        conn.close()
    return bool(r) and can_access_candidate(user, r[0])


@app.route("/api/applications", methods=["GET", "POST"])
def api_applications():
    """GET ?candidate_id=&status=(open|<status>)&due=1 -> applications + summary counts.
    POST: track an application made outside the app (job title, company, link, recruiter...)."""
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        cid = _int_or_none(data.get("candidate_id"))
        if not cid or not can_access_candidate(user, cid):
            return jsonify({"error": "Choose the consultant."}), 404
        title = str(data.get("job_title") or "").strip()[:200]
        company = str(data.get("company") or "").strip()[:200]
        if not title or not company:
            return jsonify({"error": "Job title and company are required."}), 400
        url = str(data.get("job_url") or "").strip()[:500]
        if url and not url.startswith(("http://", "https://")):
            return jsonify({"error": "The job link must start with http:// or https://"}), 400
        job = models.save_or_update_scraped_job({"title": title, "company": company, "url": url, "source": str(data.get("source") or "Manual Entry")[:60],
                                                 "location": str(data.get("location") or "")[:120], "description": f"Tracked application: {title} at {company}",
                                                 "recruiter_email": str(data.get("recruiter_email") or "").strip()[:200]})
        job_id = job["id"] if isinstance(job, dict) else job
        status = str(data.get("status") or "Applied")
        if status not in application_tracker.STATUSES:
            return jsonify({"error": "Unknown status."}), 400
        conn = models.get_db_connection()
        try:
            app_id = application_tracker.upsert(conn, job_id, cid, user["id"], stage=status)
            row = application_tracker.update(conn, app_id, user, {k: data[k] for k in application_tracker.EDITABLE if k in data})
        except application_tracker.TrackerError as ex:
            return jsonify({"error": str(ex)}), 400
        finally:
            conn.close()
        return jsonify({"success": True, "application": row})
    cid = _int_or_none(request.args.get("candidate_id"))
    ids = _tracker_candidate_ids(user, cid)
    if cid and not ids:
        return jsonify({"error": "Consultant not found"}), 404
    conn = models.get_db_connection()
    try:
        rows = application_tracker.list_for(conn, ids, status=(request.args.get("status") or "").strip(), due_only=request.args.get("due") == "1")
        all_rows = rows if not (request.args.get("status") or request.args.get("due")) else application_tracker.list_for(conn, ids)
    finally:
        conn.close()
    return jsonify({"applications": rows, "summary": application_tracker.summary(all_rows),
                    "statuses": [{"value": s, "label": application_tracker.STATUS_LABELS[s]} for s in application_tracker.STATUSES],
                    "methods": [{"value": k, "label": v or "-"} for k, v in application_tracker.METHODS.items()]})


@app.route("/api/applications/<int:app_id>", methods=["PUT"])
def api_application_update(app_id):
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401
    if not _tracker_app_allowed(user, app_id):
        return jsonify({"error": "Application not found"}), 404
    conn = models.get_db_connection()
    try:
        row = application_tracker.update(conn, app_id, user, request.get_json(silent=True) or {})
    except application_tracker.TrackerError as ex:
        return jsonify({"error": str(ex)}), 400
    finally:
        conn.close()
    return jsonify({"success": True, "application": row})


@app.route("/api/applications/<int:app_id>/events", methods=["GET"])
def api_application_events(app_id):
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401
    if not _tracker_app_allowed(user, app_id):
        return jsonify({"error": "Application not found"}), 404
    conn = models.get_db_connection()
    try:
        return jsonify({"events": application_tracker.events(conn, app_id)})
    finally:
        conn.close()


@app.route("/api/applications/check-replies", methods=["POST"])
def api_applications_check_replies():
    """Read-only check of the consultant's Gmail for replies from each open application's recruiter."""
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401
    cid = _int_or_none((request.get_json(silent=True) or {}).get("candidate_id"))
    if not cid or not can_access_candidate(user, cid):
        return jsonify({"error": "Consultant not found"}), 404
    conn = models.get_db_connection()
    try:
        out = reply_inbox.sync_candidate(conn, models.get_candidate_by_id(cid), user["id"])
    except application_tracker.TrackerError as ex:
        return jsonify({"error": str(ex)}), 400
    finally:
        conn.close()
    return jsonify({"success": True, **out})


def _reply_for_user(user, reply_id):
    conn = models.get_db_connection()
    try:
        r = reply_inbox.get(conn, reply_id)
    finally:
        conn.close()
    return r if r and can_access_candidate(user, r["candidate_id"]) else None


@app.route("/api/replies", methods=["GET"])
def api_replies():
    """Recruiter replies for the user's consultants (?candidate_id= one, ?open=1 only not handled)."""
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401
    cid = _int_or_none(request.args.get("candidate_id"))
    ids = _tracker_candidate_ids(user, cid)
    if cid and not ids:
        return jsonify({"error": "Consultant not found"}), 404
    conn = models.get_db_connection()
    try:
        rows = reply_inbox.list_replies(conn, ids, unhandled_only=request.args.get("open") == "1")
        mode = reply_inbox.owner_mode(conn, {"assigned_user_id": user["id"]})
    finally:
        conn.close()
    return jsonify({"replies": rows, "mode": mode, "modes": reply_inbox.MODES})


@app.route("/api/replies/<int:reply_id>", methods=["GET"])
def api_reply_get(reply_id):
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401
    r = _reply_for_user(user, reply_id)
    return jsonify(r) if r else (jsonify({"error": "Reply not found"}), 404)


@app.route("/api/replies/<int:reply_id>/ai", methods=["POST"])
def api_reply_ai(reply_id):
    """Chat: the AI writes / edits the answer to this reply, from facts on file only."""
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401
    r = _reply_for_user(user, reply_id)
    if not r:
        return jsonify({"error": "Reply not found"}), 404
    data = request.get_json(silent=True) or {}
    out = reply_inbox.draft_reply(models.get_candidate_by_id(r["candidate_id"]) or {}, models.get_job_by_id(r["job_id"]) or {}, r,
                                  instruction=str(data.get("instruction") or "").strip()[:1000],
                                  current=str(data.get("current_body") or "").strip())
    return jsonify(out)


@app.route("/api/replies/<int:reply_id>/save-draft", methods=["POST"])
def api_reply_save_draft(reply_id):
    """Save the answer as a Gmail draft in the recruiter's thread (someone presses Send in Gmail)."""
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401
    r = _reply_for_user(user, reply_id)
    if not r:
        return jsonify({"error": "Reply not found"}), 404
    data = request.get_json(silent=True) or {}
    conn = models.get_db_connection()
    try:
        out = reply_inbox.save_reply_draft(conn, reply_id, str(data.get("body") or ""), user, attach_resume=bool(data.get("attach_resume")))
    except (reply_inbox.ReplyError, application_tracker.TrackerError) as ex:
        return jsonify({"error": str(ex)}), 400
    finally:
        conn.close()
    return jsonify(out)


@app.route("/api/replies/<int:reply_id>/handled", methods=["POST"])
def api_reply_handled(reply_id):
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401
    if not _reply_for_user(user, reply_id):
        return jsonify({"error": "Reply not found"}), 404
    conn = models.get_db_connection()
    try:
        reply_inbox.set_handled(conn, reply_id, bool((request.get_json(silent=True) or {}).get("handled", True)))
    finally:
        conn.close()
    return jsonify({"success": True})


@app.route("/api/replies/check-all", methods=["POST"])
def api_replies_check_all():
    """Check every consultant's Gmail (that the user can see) for new recruiter replies now."""
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401
    ids = set(_tracker_candidate_ids(user))
    conn = models.get_db_connection()
    try:
        cands = [models.get_candidate_by_id(i) for i in ids]
        out = reply_inbox.check_all(conn, [c for c in cands if c])
    finally:
        conn.close()
    return jsonify(out)


@app.route("/api/me/auto-reply-mode", methods=["POST"])
def api_auto_reply_mode():
    """The recruiter's own setting: off / draft (prepare replies) / send_safe (send resume-request answers)."""
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401
    conn = models.get_db_connection()
    try:
        mode = reply_inbox.set_mode(conn, user["id"], str((request.get_json(silent=True) or {}).get("mode") or ""))
    except reply_inbox.ReplyError as ex:
        return jsonify({"error": str(ex)}), 400
    finally:
        conn.close()
    return jsonify({"success": True, "mode": mode})


_req_scans = {}      # candidate id -> running thread (one scan per consultant at a time, per worker)


def _run_requirement_scan(cand_ids):
    conn = models.get_db_connection()
    try:
        for cid in cand_ids:
            cand = models.get_candidate_by_id(cid)
            if cand:
                try:
                    inbox_requirements.scan_candidate(conn, cand)
                except Exception as ex:
                    logger.warning(f"Requirement scan for {cid} failed: {ex}")
                    try:
                        inbox_requirements.note_scan_failure(conn, cid, str(ex))
                    except Exception:
                        pass
    finally:
        conn.close()
        for cid in cand_ids:
            _req_scans.pop(cid, None)


@app.route("/api/requirements", methods=["GET"])
def api_requirements():
    """Requirement emails found in the user's consultants' Gmail and what happened to each."""
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401
    cid = _int_or_none(request.args.get("candidate_id"))
    ids = _tracker_candidate_ids(user, cid)
    if cid and not ids:
        return jsonify({"error": "Consultant not found"}), 404
    conn = models.get_db_connection()
    try:
        rows = inbox_requirements.list_for(conn, ids)
    finally:
        conn.close()
    return jsonify({"requirements": rows, "scanning": any(i in _req_scans for i in ids), "min_match": inbox_requirements.MIN_MATCH})


@app.route("/api/requirements/scan", methods=["POST"])
def api_requirements_scan():
    """Scan now (in the background - tailoring a resume takes up to a minute per requirement)."""
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401
    cid = _int_or_none((request.get_json(silent=True) or {}).get("candidate_id"))
    ids = [i for i in _tracker_candidate_ids(user, cid) if i not in _req_scans]
    if cid and not _tracker_candidate_ids(user, cid):
        return jsonify({"error": "Consultant not found"}), 404
    with_gmail = [i for i in ids if (models.get_candidate_by_id(i) or {}).get("gmail_app_password")]
    if not with_gmail:
        return jsonify({"started": False, "message": "No consultant here has Gmail connected (App Password) - or a scan is already running."})
    t = threading.Thread(target=_run_requirement_scan, args=(with_gmail,), daemon=True)
    for i in with_gmail:
        _req_scans[i] = t
    t.start()
    return jsonify({"started": True, "consultants": len(with_gmail)})


@app.route("/api/requirements/<int:req_id>", methods=["GET"])
def api_requirement_detail(req_id):
    """One requirement for Review: the prepared email, the job (JD for the Optimize window) and the vendor."""
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401
    conn = models.get_db_connection()
    try:
        row = inbox_requirements.get(conn, req_id)
    finally:
        conn.close()
    if not row or not _tracker_candidate_ids(user, row["candidate_id"]):
        return jsonify({"error": "Not found"}), 404
    job = models.get_job_by_id(row["job_id"]) if row.get("job_id") else None
    try:
        checklist = json.loads(row.get("draft_checklist") or "[]")
    except ValueError:
        checklist = []
    return jsonify({"id": row["id"], "candidate_id": row["candidate_id"], "job_id": row.get("job_id"), "status": row.get("status"),
                    "status_label": inbox_requirements.STATUS_LABELS.get(row.get("status") or "", row.get("status") or ""),
                    "subject": row.get("subject"), "from_name": row.get("from_name"), "from_email": row.get("from_email"),
                    "match_pct": row.get("match_pct"), "resume_version_id": row.get("resume_version_id"),
                    "draft": {"to": row.get("draft_to") or "", "subject": row.get("draft_subject") or "", "body": row.get("draft_body") or "",
                              "checklist": checklist},
                    "job": {"title": (job or {}).get("title") or "", "company": (job or {}).get("company") or "",
                            "description": (job or {}).get("description") or ""}})


@app.route("/api/requirements/<int:req_id>/recheck", methods=["POST"])
def api_requirements_recheck(req_id):
    """'Check again' on a requirement email that was skipped or judged not a requirement: it is re-read
    (AI again) and, if it is one, tailored and drafted - a scan of that consultant starts now."""
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401
    conn = models.get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT candidate_id FROM inbox_requirements WHERE id = ?", (req_id,))
        row = cur.fetchone()
        if not row or not _tracker_candidate_ids(user, row[0]):
            return jsonify({"error": "Not found"}), 404
        cid = inbox_requirements.recheck(conn, req_id)
    finally:
        conn.close()
    if not cid:
        return jsonify({"error": "Only skipped, not-a-requirement or low-match emails can be checked again."}), 400
    if cid in _req_scans:
        return jsonify({"started": False, "message": "A scan is already running - it will be checked on the next one."})
    t = threading.Thread(target=_run_requirement_scan, args=([cid],), daemon=True)
    _req_scans[cid] = t
    t.start()
    return jsonify({"started": True})


def _reply_check_loop(minutes: float):
    """Every few minutes, check every consultant's Gmail for new recruiter replies (needs the server
    awake - a sleeping free Render instance pauses it; the "Check replies" buttons still work)."""
    time.sleep(90)
    while True:
        try:
            conn = models.get_db_connection()
            try:
                cur = conn.cursor()
                cur.execute("SELECT id FROM candidates WHERE gmail_app_password IS NOT NULL AND gmail_app_password <> ''")
                cands = [models.get_candidate_by_id(r[0]) for r in cur.fetchall()]
                out = reply_inbox.check_all(conn, [c for c in cands if c], time_budget_s=minutes * 60 * 0.4)
                if out["new_replies"]:
                    logger.info(f"Reply check: {out['new_replies']} new recruiter replies")
                if os.getenv("REQUIREMENT_SCAN", "1") != "0":     # new requirement emails -> tailored drafts
                    req = inbox_requirements.scan_all(conn, [c for c in cands if c], time_budget_s=minutes * 60 * 0.5)
                    if req["drafted"]:
                        logger.info(f"Requirement scan: {req['drafted']} drafts created")
            finally:
                conn.close()
        except Exception as ex:
            logger.warning(f"Reply check failed: {ex}")
        time.sleep(minutes * 60)


# Automatic reply checks: REPLY_CHECK_MINUTES (default 20 on the live server - the one with a
# DATABASE_URL; off locally and in tests so nothing logs in to a real Gmail by accident).
_reply_minutes = float(os.getenv("REPLY_CHECK_MINUTES", "20" if config.DATABASE_URL else "0") or 0)
if _reply_minutes > 0:
    threading.Thread(target=_reply_check_loop, args=(_reply_minutes,), daemon=True).start()


@app.route("/api/applications/export-xlsx", methods=["GET"])
def api_applications_export():
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401
    cid = _int_or_none(request.args.get("candidate_id"))
    ids = _tracker_candidate_ids(user, cid)
    if cid and not ids:
        return jsonify({"error": "Consultant not found"}), 404
    conn = models.get_db_connection()
    try:
        rows = application_tracker.list_for(conn, ids, status=(request.args.get("status") or "").strip())
    finally:
        conn.close()
    who = rows[0]["candidate_name"] if (cid and rows) else ("All consultants" if not cid else "Consultant")
    title = f"Applications - {who} - {datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
    slug = re.sub(r"[^A-Za-z0-9]+", "_", who).strip("_") or "Applications"
    return send_file(io.BytesIO(application_tracker.build_xlsx(rows, title)), as_attachment=True,
                     download_name=f"Applications_{slug}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")



@app.route("/api/optimized-resumes", methods=["GET", "POST"])
def api_optimized_resumes():
    """POST: save the version the recruiter reviewed / edited (never automatic) as
    <Name>_<PrimarySkill>.docx (_v2, _v3 ... if taken). GET ?candidate_id=: that consultant's versions."""
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401
    if request.method == "GET":
        cid = _int_or_none(request.args.get("candidate_id"))
        if not cid or not can_access_candidate(user, cid):
            return jsonify({"error": "Consultant not found"}), 404
        return jsonify({"versions": resume_versions.list_for(cid)})
    data = request.get_json(silent=True) or {}
    cid = _int_or_none(data.get("candidate_id"))
    if not cid or not can_access_candidate(user, cid):
        return jsonify({"error": "Choose the consultant this resume belongs to."}), 404
    edited = str(data.get("edited_text") or "").strip()
    if len(edited) < 50:
        return jsonify({"error": "The resume text is empty - optimize first, or undo the deletion."}), 400
    cand = models.get_candidate_by_id(cid)
    ai_docx = resume_versions.decode_docx(data.get("docx_base64") or "")
    if data.get("docx_base64") and ai_docx is None:
        return jsonify({"error": "The optimized Word file was not readable - optimize again."}), 400
    jd_text = str(data.get("jd_text") or "")
    skill = str(data.get("primary_skill") or "").strip() or resume_versions.primary_skill({}, cand, jd_text)
    filename = resume_versions.make_filename(cand.get("name") or "Consultant", skill, resume_versions.taken_names(cid))
    file_bytes, kept, note = resume_versions.build_final_docx(ai_docx, str(data.get("ai_text") or ""), edited, cand.get("name") or "Consultant")
    job_id = _int_or_none(data.get("job_id"))
    if job_id and not models.get_job_by_id(job_id):
        job_id = None
    version_id = resume_versions.save(cid, job_id, filename, file_bytes, skill, cloud_align.jd_cloud(jd_text)["primary"] or "",
                                      jd_text.strip()[:300], user["id"])
    models.log_activity(user["id"], user["name"], "Saved Tailored Resume", "Candidate", cid,
                        f"{filename}" + (f" for job #{job_id}" if job_id else ""))
    return jsonify({"success": True, "id": version_id, "filename": filename, "format_preserved": kept, "note": note})


@app.route("/api/optimized-resumes/for-job", methods=["GET"])
def api_optimized_resume_for_job():
    """The latest saved tailored resume for this consultant + job (with its text), so reopening Optimize
    for the same job shows it instead of running the AI again."""
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401
    cid, jid = _int_or_none(request.args.get("candidate_id")), _int_or_none(request.args.get("job_id"))
    if not cid or not jid or not can_access_candidate(user, cid):
        return jsonify({"error": "Consultant not found"}), 404
    v = resume_versions.latest_for_job(cid, jid)
    if not v or not v.get("data"):
        return jsonify({"version": None})
    try:
        text = docx_editor.extract_text(bytes(v["data"]))
    except Exception:
        text = ""
    return jsonify({"version": {"id": v["id"], "filename": v["filename"], "created_at": str(v.get("created_at") or "")[:16], "text": text,
                                "docx_base64": base64.b64encode(bytes(v["data"])).decode("ascii")}})


@app.route("/api/consultants/<int:candidate_id>/resume-docx", methods=["GET"])
def api_consultant_resume_docx(candidate_id):
    """The consultant's original Word resume (base64) - for the resume preview and the resume chat."""
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401
    if not can_access_candidate(user, candidate_id):
        return jsonify({"error": "Consultant not found"}), 404
    found = models.find_resume_file(models.get_candidate_by_id(candidate_id) or {})
    if not found or not found[0].lower().endswith(".docx"):
        return jsonify({"docx_base64": "", "filename": found[0] if found else ""})
    return jsonify({"docx_base64": base64.b64encode(found[1]).decode("ascii"), "filename": found[0]})


@app.route("/api/resume-editor/edit", methods=["POST"])
def api_resume_editor_edit():
    """Resume chat: apply the recruiter's instruction to the Word resume shown in the preview."""
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401
    data = request.get_json(silent=True) or {}
    cid = _int_or_none(data.get("candidate_id"))
    if cid and not can_access_candidate(user, cid):
        return jsonify({"error": "Consultant not found"}), 404
    docx_bytes = resume_versions.decode_docx(str(data.get("docx_base64") or ""))
    if docx_bytes is None:
        return jsonify({"error": "The resume chat needs the Word (.docx) resume - this one is text only."}), 400
    if len(docx_bytes) > 5 * 1024 * 1024:
        return jsonify({"error": "That resume is over 5 MB."}), 413
    out = resume_editor.edit(docx_bytes, str(data.get("instruction") or "")[:2000], str(data.get("original_text") or "")[:30000],
                             str(data.get("jd_text") or "")[:20000],
                             history=data.get("history") if isinstance(data.get("history"), list) else None,
                             keywords=str(data.get("keywords") or "")[:800])
    return jsonify(out)


@app.route("/api/optimized-resumes/<int:version_id>/download", methods=["GET"])
def api_optimized_resume_download(version_id):
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401
    v = resume_versions.get(version_id)
    if not v or not can_access_candidate(user, v["candidate_id"]):
        return jsonify({"error": "Not found"}), 404
    return send_file(io.BytesIO(v["data"]), as_attachment=True, download_name=v["filename"],
                     mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


def _resume_version_for(user, candidate_id, version_id, job_id=None):
    """(filename, bytes) of the saved version to attach: the one chosen, else the latest saved for
    this consultant + job, else None (the original resume is attached)."""
    v = None
    if version_id:
        v = resume_versions.get(version_id)
        if not v or v["candidate_id"] != int(candidate_id):
            return None
    elif job_id:
        v = resume_versions.latest_for_job(int(candidate_id), int(job_id))
    return (v["filename"], v["data"], v["id"]) if v and v.get("data") else None


@app.route("/api/jd/from-url", methods=["POST"])
def api_jd_from_url():
    """Resume Optimizer: the full JD read from a public job posting link (free; never invented -
    an unreadable posting returns 422 with the reason)."""
    if not current_user():
        return jsonify({"error": "Login required"}), 401
    url = str((request.get_json(silent=True) or {}).get("url") or "").strip()
    if not re.match(r"^https?://\S+$", url, re.I):
        return jsonify({"error": "Paste the full link to the job posting (starting with https://)."}), 400
    text, reason = job_description_fetch.fetch_full_description(url)
    if not text:
        return jsonify({"error": reason or "Could not read the job description from that link - paste it instead."}), 422
    return jsonify({"description": text})


@app.route("/api/jd/cloud", methods=["POST"])
def api_jd_cloud():
    """The JD's primary cloud (AWS / Azure / GCP) and whether the chosen consultant's profile shows it."""
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401
    data = request.get_json(silent=True) or {}
    info = cloud_align.jd_cloud(str(data.get("jd_text") or "")[:20000])
    consultant = []
    cid = _int_or_none(data.get("candidate_id"))
    if cid and can_access_candidate(user, cid):
        cand = models.get_candidate_by_id(cid) or {}
        consultant = cloud_align.clouds_in(" ".join(str(cand.get(k) or "") for k in ("primary_skills", "title", "resume_summary", "resume_text")))
    info["consultant_clouds"] = consultant
    info["mismatch"] = bool(info["primary"] and consultant and info["primary"] not in consultant)
    return jsonify(info)


@app.route("/api/resume-bot/extract-text", methods=["POST", "OPTIONS"])
def api_resume_bot_extract_text():
    """Reads an attached resume file and returns its text so the page can show exactly what will
    be optimized. Nothing is stored - the file is read in memory and discarded."""
    if request.method == "OPTIONS":
        return jsonify({}), 200
    if not current_user():
        return jsonify({"error": "Login required"}), 401

    f = request.files.get("resume_file")
    if not f or not f.filename:
        return jsonify({"error": "Choose a resume file (.docx, .pdf or .txt)."}), 400
    filename = secure_filename(f.filename) or "resume"
    if os.path.splitext(filename.lower())[1] not in (".docx", ".pdf", ".txt"):
        return jsonify({"error": "Unsupported file type. Attach a .docx, .pdf or .txt resume."}), 400
    raw = f.read()
    if len(raw) > 5 * 1024 * 1024:
        return jsonify({"error": "That file is over 5 MB - attach a smaller resume."}), 413

    text = resume_bot.extract_text_from_file_bytes(raw, filename, strict=True)
    if len(text.strip()) < 30:
        return jsonify({"error": "No readable text was found in that file (a scanned-image PDF can't be read). Paste the resume text instead."}), 422
    return jsonify({"text": text, "filename": filename, "chars": len(text)})

@app.route("/api/resume-bot/download-docx", methods=["POST", "OPTIONS"])
def api_resume_bot_download_docx():
    if request.method == "OPTIONS":
        return jsonify({}), 200

    if not current_user():
        return jsonify({"error": "Login required"}), 401

    data = request.get_json(silent=True) or {}
    resume_text = data.get("resume_text", "")
    candidate_name = data.get("candidate_name", "Consultant")

    if not resume_text:
        return jsonify({"error": "No resume text provided"}), 400

    docx_io = resume_bot.create_docx_resume(resume_text, candidate_name)
    safe_filename = f"{re.sub(r'[^a-zA-Z0-9_-]', '_', candidate_name)}_Optimized_Resume.docx"

    return send_file(
        docx_io,
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        as_attachment=True,
        download_name=safe_filename
    )


# =========================================================================
# US IT Staffing - Bench Candidates & Student Sourcing (2018 - 2026)
# =========================================================================

def _parse_bachelor_year(data):
    """Selected Bachelor's passout year (India). None means 'All years'. Clamped to the range
    Sourcing actually supports (linkedin_sourcing.MIN/MAX_BACHELOR_YEAR) - not a hardcoded 2020,
    which used to silently downgrade a request for e.g. 2023 to 2020 with no explanation."""
    raw_by = str((data or {}).get("bachelor_year") or (data or {}).get("year") or "").strip()
    year_match = re.search(r'\b(19\d\d|20\d\d)\b', raw_by)
    if not year_match:
        return None
    year = int(year_match.group(1))
    return max(linkedin_sourcing.MIN_BACHELOR_YEAR, min(linkedin_sourcing.MAX_BACHELOR_YEAR, year))


@app.route("/api/students/search", methods=["GET", "POST", "OPTIONS"])
def api_search_students():
    if request.method == "OPTIONS":
        return make_response("", 200)

    if not current_user():
        return jsonify({"error": "Login required"}), 401

    data = request.get_json(silent=True) or request.args.to_dict()
    keyword = data.get("keyword") or data.get("query") or data.get("category") or "Computer Science"
    category = data.get("category", "all")
    intent = data.get("intent", "ready_to_market")

    # Bachelor's passout year in India — the one filter this workflow uses today
    # (STRICT EXACT MATCH). More filters (technology/role, visa pathway, US
    # university, US region) can be reintroduced later without changing this shape.
    bachelor_year = _parse_bachelor_year(data)
    if bachelor_year:
        start_year = end_year = bachelor_year
    else:
        start_year, end_year = 2012, 2020
    location = data.get("location", "United States")

    # This endpoint is now FREE and instant: it only returns candidates the
    # recruiter has already added to the ATS. The live LinkedIn search (which
    # costs Apify credits) is a separate, explicit action: /api/students/search-start
    # + /api/students/search-poll.
    candidates = []

    # Prepend any candidates the recruiter has already added to the database
    # that genuinely match the requested Bachelor's year.
    try:
        db_cands = models.get_candidates()
        bench_imported = []
        for db_c in db_cands:
            c_summary = str(db_c.get("resume_summary") or "")
            c_skills = str(db_c.get("primary_skills") or "")
            c_title = str(db_c.get("title") or "")
            # Only include a candidate if their resume summary/skills/title
            # actually specifies the target year
            y_match = re.search(r'\b(19\d\d|20\d\d)\b', c_summary + " " + c_skills + " " + c_title)
            if not y_match:
                continue
            c_year = y_match.group(1)

            if bachelor_year is not None and int(c_year) != int(bachelor_year):
                continue

            # HONEST labelling: we only know this person is on the recruiter's
            # own bench roster and that their resume text mentions this year
            # somewhere. We do NOT know their degree, college or Master's, so
            # none is claimed - the recruiter confirms from the resume.
            bench_imported.append({
                "id": db_c.get("id"),
                "name": db_c.get("name"),
                "headline": db_c.get("title") or "Technical Consultant",
                "bachelor_year": c_year,
                "grad_year": c_year,
                "bachelor_degree": "Degree: see resume",
                "bachelor_college": "Recruiter-added (details not verified)",
                "master_degree": "Master's: see resume",
                "master_university": "See resume",
                "location": db_c.get("location") or "United States",
                "status_badge": "⭐ Bench - Added by Recruiter",
                "status_tag": "⭐ Bench - Added by Recruiter",
                "settlement_badge": "⭐ On your bench",
                "settlement_sub": "Year taken from resume text - unverified",
                "year_verified": False,
                "quality": "[BENCH] Recruiter-added candidate - education not verified",
                "degree": f"Resume mentions {c_year} (education not verified)",
                "profile_url": db_c.get("resume_filename") if (db_c.get("resume_filename") or "").startswith("http") else f"https://www.google.com/search?q=site:linkedin.com/in/+%22{urllib.parse.quote_plus(db_c.get('name', ''))}%22+USA",
                "linkedin_url": db_c.get("resume_filename") if (db_c.get("resume_filename") or "").startswith("http") else f"https://www.google.com/search?q=site:linkedin.com/in/+%22{urllib.parse.quote_plus(db_c.get('name', ''))}%22+USA",
                "is_imported": True
            })
        # Prepend imported candidates (deduped by name against live results)
        seen_names = set(c.get("name", "").lower() for c in bench_imported)
        for cand in candidates:
            if cand.get("name", "").lower() not in seen_names:
                bench_imported.append(cand)
        candidates = bench_imported
    except Exception as ex:
        logger.warning(f"Error fetching imported candidates: {ex}")

    return jsonify({
        "status": "success",
        "count": len(candidates),
        "query": {
            "keyword": keyword,
            "category": category,
            "intent": intent,
            "start_year": start_year,
            "end_year": end_year,
            "location": location
        },
        "students": candidates,
        "candidates": candidates,
        "results": candidates
    })

_APIFY_ID_RE = re.compile(r"^(?:[A-Za-z0-9]{8,40}|hv-\d{1,12})$")   # Apify run ids, or HarvestAPI direct runs (hv-<n>)


@app.route("/api/students/sourced-pool", methods=["GET"])
def api_students_sourced_pool():
    """Every verified match ANY recruiter has already found for this (source, year) -
    read from the shared database, costs nothing. The Sourcing tab calls this first so
    the whole team sees each other's results instantly instead of re-paying to re-find them."""
    if not current_user():
        return jsonify({"error": "Login required"}), 401
    source = (request.args.get("source") or "").strip()
    if source != "apify":
        return jsonify({"error": "source must be 'apify'"}), 400
    bachelor_year = _parse_bachelor_year({"bachelor_year": request.args.get("bachelor_year")})
    matches = sourcing_store.get_cached_matches(source, bachelor_year)
    cursor = sourcing_store.get_cursor(source, bachelor_year)
    return jsonify({"matches": matches, "exhausted": cursor["exhausted"],
                    "pool_counts": sourcing_store.pool_counts(source)})


@app.route("/api/students/search-start", methods=["POST", "OPTIONS"])
def api_students_search_start():
    """Start a live LinkedIn search (costs Apify credits, capped per search and
    per day). Returns the Apify run/dataset ids; the client then polls
    /api/students/search-poll. Login required - this spends real money.

    start_page comes from the TEAM'S shared cursor, not the browser: once one
    recruiter has scanned pages 1-5 for a year, the next recruiter's search (on
    any device) continues at page 6 instead of re-paying to re-scan 1-5."""
    if request.method == "OPTIONS":
        return make_response("", 200)
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401

    data = request.get_json(silent=True) or {}
    bachelor_year = _parse_bachelor_year(data)
    cursor = sourcing_store.get_cursor("apify", bachelor_year)
    start_page = cursor["next_page"]
    pages = data.get("pages") or 1
    # Re-trying ONE page whose run failed at the data provider: same page again, cursor untouched
    # (that page was already reserved when it was first started).
    retry_page = None
    if data.get("retry_start_page"):
        try:
            retry_page = max(1, min(int(data["retry_start_page"]), linkedin_sourcing.MAX_START_PAGE))
            start_page, pages = retry_page, 1
        except (TypeError, ValueError):
            retry_page = None
    result = linkedin_sourcing.start_search(
        bachelor_year,
        pages=pages,   # number of parallel one-page runs to start (max 5)
        location=data.get("location") or "United States",
        start_page=start_page,
    )
    if result.get("error"):
        return jsonify({"error": result["error"]}), result.get("code", 500)
    # Reserve this page range immediately so a second recruiter clicking Search in the
    # same moment gets the NEXT range, not an overlapping (double-paid) one.
    if not retry_page:
        sourcing_store.save_cursor("apify", bachelor_year, next_page=result["next_start_page"])
    result["bachelor_year"] = bachelor_year
    return jsonify(result)


@app.route("/api/students/search-poll", methods=["POST", "OPTIONS"])
def api_students_search_poll():
    """Poll a running LinkedIn search: returns run status plus ONLY the newly
    scanned profiles that pass the strict India-Bachelor's + US-Master's check."""
    if request.method == "OPTIONS":
        return make_response("", 200)
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401

    data = request.get_json(silent=True) or {}
    run_id = str(data.get("run_id") or "")
    dataset_id = str(data.get("dataset_id") or "")
    if not _APIFY_ID_RE.match(run_id) or not _APIFY_ID_RE.match(dataset_id):
        return jsonify({"error": "Invalid run reference"}), 400
    try:
        offset = max(0, int(data.get("offset") or 0))
        matched_so_far = max(0, int(data.get("matched_so_far") or 0))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid offset"}), 400

    bachelor_year = _parse_bachelor_year(data)
    result = linkedin_sourcing.poll_search(
        run_id, dataset_id, bachelor_year, offset, matched_so_far,
        target=data.get("target") or linkedin_sourcing.TARGET_MATCHES,
    )
    if result.get("error"):
        return jsonify({"error": result["error"]}), result.get("code", 500)
    # Every recruiter's find goes straight into the shared pool - the next person who
    # searches this year sees it for free instead of Apify re-scanning the same profile.
    sourcing_store.save_matches("apify", bachelor_year, result.get("new_matches") or [], user["id"])
    # Profiles that were fully verified but graduated in a DIFFERENT year are banked under their own
    # year (their year's search will then show them for free). Not sent to the browser for this year.
    other = result.pop("other_year_matches", None) or []
    result["banked_other_years"] = sourcing_store.save_matches("apify", None, other, user["id"]) if other else 0
    # EVERY scanned profile (not just this year's matches) has a full, already-paid-for education
    # list: store it for the education filters (Filter A / B). No extra API call.
    raw_items = result.pop("raw_items", None) or []
    if raw_items:
        try:
            linkedin_ingest.ingest_profiles(linkedin_ingest.ApifyProfileProvider().to_profiles(raw_items), "apify", user["id"])
            vconn = models.get_db_connection()
            try:
                linkedin_ingest.mark_verified(vconn, (result.get("new_matches") or []) + other)
                vconn.commit()
            finally:
                vconn.close()
        except Exception as ex:
            logger.warning(f"Education filters: could not store scanned profiles: {ex}")
    return jsonify(result)


@app.route("/api/students/add-to-bench", methods=["POST", "OPTIONS"])
def api_add_student_to_bench():
    if request.method == "OPTIONS":
        return make_response("", 200)

    data = request.get_json() or {}
    name = data.get("name", "Consultant").strip()
    if not name or name == "Candidate":
        name = "US Tech Consultant"

    clean_name = re.sub(r'[^a-zA-Z0-9]', '', name.lower())
    if not clean_name:
        clean_name = "consultant"
    unique_suffix = f"{int(time.time())}_{random.randint(100, 999)}"
    email = data.get("email") or f"{clean_name}.{unique_suffix}@talent.internal"
    phone = data.get("phone", "")
    title = data.get("headline") or f"{data.get('degree', 'MS in Tech')} Consultant"
    grad_year = str(data.get("grad_year", "2024"))
    university = data.get("university", "US University")
    location = data.get("location", "United States (Open to Relocate)")
    summary = data.get("summary", "")
    skills = data.get("skills") or f"{data.get('degree', '')}, {data.get('category', 'Tech')}, US Master's"

    try:
        exp_years = max(1, 2026 - int(grad_year))
    except:
        exp_years = 3

    # Add candidate to ATS database
    try:
        cid = models.create_candidate(
            name=name,
            email=email,
            phone=phone,
            title=title,
            primary_skills=skills,
            experience_years=exp_years,
            target_rate="$85/hr (C2C)",
            visa_status="OPT / STEM OPT / C2C",
            status="Bench - Ready to Market",
            location=location,
            resume_summary=f"Education: {data.get('degree', 'MS')} ({university}, Class of {grad_year}). LinkedIn: {data.get('profile_url', '')}. Summary: {summary}"
        )
    except Exception as ex:
        # Fallback with timestamped email if unique constraint hit
        email = f"{clean_name}.{uuid.uuid4().hex[:6]}@talent.internal"
        cid = models.create_candidate(
            name=name,
            email=email,
            phone=phone,
            title=title,
            primary_skills=skills,
            experience_years=exp_years,
            target_rate="$85/hr (C2C)",
            visa_status="OPT / STEM OPT / C2C",
            status="Bench - Ready to Market",
            location=location,
            resume_summary=f"Education: {data.get('degree', 'MS')} ({university}, Class of {grad_year}). LinkedIn: {data.get('profile_url', '')}. Summary: {summary}"
        )

    user = current_user() or {"id": 1, "name": "Valipireddy Praveen"}
    models.assign_candidate_to_recruiter(cid, user["id"])
    models.log_activity(
        user["id"],
        user["name"],
        "Added to Bench",
        "Candidate",
        cid,
        f"Added {name} ({data.get('degree', 'MS')} - {university}, {grad_year}) to {user['name']}'s Bench Consultants."
    )

    return jsonify({
        "status": "success",
        "message": f"Successfully onboarded {name} to Bench Hotlist!",
        "candidate_id": cid
    })

@app.route("/api/students/export-csv", methods=["GET", "POST", "OPTIONS"])
def api_export_students_csv():
    if request.method == "OPTIONS":
        return make_response("", 200)

    import io
    import csv

    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        candidates = data.get("candidates", [])
        if not candidates:
            kw = data.get("keyword", "Computer Science")
            by = data.get("bachelor_year")
            col = data.get("college")
            loc = data.get("location", "United States")
            candidates = apify_service.scrape_bench_candidates(keyword=kw, bachelor_year=by, college=col, location=loc)
    else:
        candidates = apify_service.scrape_bench_candidates()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Full Name", "Degree & Major", "Graduation Year", "University", 
        "Current Location", "Status & Intent", "Headline", "LinkedIn Profile URL", "Summary"
    ])

    for c in candidates:
        writer.writerow([
            c.get("name", ""),
            c.get("degree", ""),
            c.get("grad_year", ""),
            c.get("university", ""),
            c.get("location", ""),
            c.get("status_badge", ""),
            c.get("headline", ""),
            c.get("profile_url", ""),
            c.get("summary", "")
        ])

    csv_data = output.getvalue()
    response = make_response(csv_data)
    response.headers["Content-Disposition"] = "attachment; filename=LinkedIn_US_Candidates_2018_2026.csv"
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    return response

@app.route("/api/students/generate-pitch", methods=["POST", "OPTIONS"])
def api_generate_student_pitch():
    if request.method == "OPTIONS":
        return make_response("", 200)

    data = request.get_json() or {}
    name = data.get("name", "Candidate")
    degree = data.get("degree", "Master's")
    university = data.get("university", "your University")
    grad_year = data.get("grad_year", "2024")
    category = data.get("category", "Software Engineering")

    subject = f"US IT Bench Placement & Direct C2C Project Opportunities for {name} ({degree})"
    pitch_body = f"""Hi {name},

Hope you are having a productive week!

I came across your profile and noticed your strong profile with {degree} from {university} (Class of {grad_year}).

At our US Staffing & Consulting practice, we represent active bench consultants and recent Master's / STEM OPT graduates for top-tier C2C and W2 contract projects with Fortune 500 direct clients and Prime Vendors across the United States.

We have active client requisitions in {category.replace('_', ' ').title()} / Cloud / Tech domains with competitive rates ($75 - $95+/hr C2C).

Our bench support includes:
• Direct marketing to Tier-1 Vendors & Direct Implementation Partners
• Targeted resume packaging & technical interview coaching
• Dedicated marketing team with fast placement turnaround (2-3 weeks)

Are you currently open to exploring new project opportunities or bench marketing support? 

Let's connect for a quick 5-minute call this week.

Best regards,
US IT Staffing & Talent Placement Team
"""

    return jsonify({
        "status": "success",
        "subject": subject,
        "pitch_body": pitch_body
    })

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    print(f"\n=======================================================")
    print(f">> ADROIT MULTI-CONSULTANT ATS RUNNING AT http://localhost:{port}")
    print(f">> 1-Click Recruiter Login: http://localhost:{port}/dev-login")
    print(f"=======================================================\n")
    app.run(host="0.0.0.0", port=port, debug=True, threaded=True)


# --- Recruiter & Team Management APIs (Admin Only) ---

@app.route("/api/admin/recruiters", methods=["GET", "POST", "OPTIONS"])
def api_admin_recruiters():
    if request.method == "OPTIONS":
        return jsonify({}), 200

    user = current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    if "Admin" not in user.get("role", ""):
        return jsonify({"error": "Admin permission required."}), 403

    if request.method == "POST":
        data = request.json or {}
        name = data.get("name", "").strip()
        email = data.get("email", "").strip()
        password = data.get("password", "").strip()
        role = data.get("role", "Recruiter").strip()
        region = regions.normalize(data.get("region")) or "USA"

        if not name or not email or not password:
            return jsonify({"error": "Name, email, and password are required."}), 400

        try:
            new_user = models.create_user(name, email, password, role=role, region=region)
            models.log_activity(
                user["id"], user["name"], "Created Recruiter", "User", new_user["id"],
                f"Admin created recruiter account for {name} ({email}) with role '{role}'"
            )
            return jsonify({"success": True, "user": new_user})
        except ValueError as ve:
            return jsonify({"error": str(ve)}), 400
        except Exception as e:
            return jsonify({"error": f"Failed to create recruiter: {str(e)}"}), 500

    return jsonify(models.get_users())

@app.route("/api/admin/recruiters/<int:recruiter_id>/region", methods=["POST"])
def api_admin_recruiter_region(recruiter_id):
    """Admin: move a recruiter to the USA or India team."""
    user = current_user()
    if not user or "Admin" not in user.get("role", ""):
        return jsonify({"error": "Admin permission required."}), 403
    region = regions.normalize((request.get_json(silent=True) or {}).get("region"))
    if not region:
        return jsonify({"error": "Region must be USA or India."}), 400
    conn = models.get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE users SET region = ? WHERE id = ?", (region, recruiter_id))
        conn.commit()
    finally:
        conn.close()
    models.log_activity(user["id"], user["name"], "Recruiter Region", "User", recruiter_id, f"Moved to the {region} team")
    return jsonify({"success": True, "region": region})


@app.route("/api/view-region", methods=["POST"])
def api_view_region():
    """Admin: which team to look at - USA, India or Both."""
    user = current_user()
    if not user or "Admin" not in user.get("role", ""):
        return jsonify({"error": "Admin permission required."}), 403
    region = regions.normalize((request.get_json(silent=True) or {}).get("region"))
    if region:
        session["view_region"] = region
    else:
        session.pop("view_region", None)
    return jsonify({"success": True, "view_region": region or "Both"})


@app.route("/api/admin/recruiters/<int:recruiter_id>", methods=["DELETE", "OPTIONS"])
def api_admin_delete_recruiter(recruiter_id):
    if request.method == "OPTIONS":
        return jsonify({}), 200

    user = current_user()
    if not user or "Admin" not in user.get("role", ""):
        return jsonify({"error": "Forbidden"}), 403

    if recruiter_id == user["id"]:
        return jsonify({"error": "Cannot delete your own admin account."}), 400

    target = models.get_user_by_id(recruiter_id)
    if not target:
        return jsonify({"error": "Recruiter not found."}), 404

    models.delete_user(recruiter_id)
    models.log_activity(
        user["id"], user["name"], "Deleted Recruiter", "User", recruiter_id,
        f"Admin deleted recruiter account {target['name']} ({target['email']}). Candidates reassigned to Admin."
    )
    return jsonify({"success": True, "message": f"Recruiter {target['name']} deleted."})

@app.route("/api/admin/recruiters/<int:recruiter_id>/reset-password", methods=["POST", "OPTIONS"])
def api_admin_reset_password(recruiter_id):
    if request.method == "OPTIONS":
        return jsonify({}), 200

    user = current_user()
    if not user or "Admin" not in user.get("role", ""):
        return jsonify({"error": "Forbidden"}), 403

    data = request.json or {}
    new_password = data.get("new_password", "").strip()
    if not new_password or len(new_password) < 4:
        return jsonify({"error": "New password must be at least 4 characters."}), 400

    models.update_user_password(recruiter_id, new_password)
    return jsonify({"success": True, "message": "Password updated successfully."})

@app.route("/api/admin/candidates/<int:candidate_id>/reassign", methods=["POST", "OPTIONS"])
def api_admin_reassign_candidate(candidate_id):
    if request.method == "OPTIONS":
        return jsonify({}), 200

    user = current_user()
    if not user or "Admin" not in user.get("role", ""):
        return jsonify({"error": "Forbidden"}), 403

    data = request.json or {}
    new_user_id = data.get("assigned_user_id")
    if not new_user_id:
        return jsonify({"error": "assigned_user_id is required"}), 400

    target_user = models.get_user_by_id(new_user_id)
    if not target_user:
        return jsonify({"error": "Target recruiter not found"}), 404

    cand = models.get_candidate_by_id(candidate_id, is_admin=True)
    if not cand:
        return jsonify({"error": "Candidate not found"}), 404

    models.assign_candidate_to_recruiter(candidate_id, new_user_id)
    models.log_activity(
        user["id"], user["name"], "Reassigned Candidate", "Candidate", candidate_id,
        f"Reassigned candidate '{cand['name']}' to recruiter '{target_user['name']}'"
    )
    return jsonify({"success": True, "message": f"Candidate reassigned to {target_user['name']}."})


# --- Education filters (Sourcing: Filter A = Indian college -> US Master's, Filter B = US university
# -> Indian undergrad). Reads the team-wide education tables only - no Apify / paid calls. ---

def _edu_user():
    user = current_user()
    if not user:
        return None, (jsonify({"error": "Login required"}), 401)
    return user, None


@app.route("/api/admin/sourcing/fill-work-info", methods=["POST"])
def api_sourcing_fill_work_info():
    """Admin: fill Open to work / job type for profiles saved before the app kept them, by reading
    the stored results of past searches again (no new scraping; ~$0.0004 per 1,000 rows read)."""
    user, err = _edu_admin()
    if err:
        return err
    conn = models.get_db_connection()
    try:
        stats = work_backfill.run(conn)
    finally:
        conn.close()
    models.log_activity(user["id"], user["name"], "Filled LinkedIn work info", "Sourcing", 0,
                        f"{stats['filled']} profiles filled from {stats['searches_read']} past searches; {stats['still_missing']} still without data")
    return jsonify(stats)


def _edu_admin():
    user = current_user()
    if not user:
        return None, (jsonify({"error": "Login required"}), 401)
    if "Admin" not in user.get("role", ""):
        return None, (jsonify({"error": "Admin permission required."}), 403)
    return user, None


@app.route("/api/education/institutions", methods=["GET"])
def api_education_institutions():
    """Autocomplete list for one filter: A -> Indian colleges, B -> US universities."""
    user, err = _edu_user()
    if err:
        return err
    f = (request.args.get("filter") or "").strip().upper()
    if f not in ("A", "B"):
        return jsonify({"error": "filter must be 'A' or 'B'"}), 400
    conn = models.get_db_connection()
    try:
        insts = education_filters.institutions_for(conn, f)
    finally:
        conn.close()
    return jsonify({"institutions": insts})


@app.route("/api/education/search", methods=["GET"])
def api_education_search():
    user, err = _edu_user()
    if err:
        return err
    try:
        params = education_filters.parse_params(request.args)
    except education_filters.FilterError as ex:
        return jsonify({"error": str(ex)}), 400
    params["user_id"] = user["id"]
    conn = models.get_db_connection()
    try:
        return jsonify(education_filters.search(conn, params))
    finally:
        conn.close()


@app.route("/api/education/export-xlsx", methods=["GET"])
def api_education_export_xlsx():
    """Excel download of every row the current filter matches (up to 10,000). Logged with the recruiter id."""
    user, err = _edu_user()
    if err:
        return err
    try:
        params = education_filters.parse_params(request.args)
    except education_filters.FilterError as ex:
        return jsonify({"error": str(ex)}), 400
    params["user_id"] = user["id"]
    conn = models.get_db_connection()
    try:
        rows = education_filters.export_rows(conn, params)
        cur = conn.cursor()
        inst_name = education_filters.display_name(conn, params["institution_id"])
        if params["filter"] == "ALL":
            label, inst_name = "All saved profiles", "All_saved_profiles"
        elif params["filter"] == "P":
            span = (str(params["year_from"]) if params["year_from"] == params["year_to"]
                    else f"{params['year_from']}-{params['year_to']}")
            label = f"Passout {span}: Indian Bachelor's + US Master's"
            inst_name = f"Passout_{span}"
        else:
            label = ("Filter A: Indian college -> US Master's" if params["filter"] == "A"
                     else "Filter B: US university -> Indian undergrad") + f" | {inst_name}"
        if params["filter"] in ("A", "B") and (params["year_from"] or params["year_to"]):
            label += f" | years {params['year_from'] or '...'}-{params['year_to'] or '...'}"
        if params["location"]:
            label += f" | location: {params['location']}"
        if params["keyword"]:
            label += f" | keyword: {params['keyword']}"
        cur.execute("INSERT INTO capture_log (user_id, action, source, profile_id, details) VALUES (?, ?, ?, ?, ?)",
                    (user["id"], "export_xlsx", "education_filter", None, f"{len(rows)} rows | {label}"))
        conn.commit()
    finally:
        conn.close()
    models.log_activity(user["id"], user["name"], "Exported Excel", "Sourcing", 0, f"{len(rows)} candidates - {label}")
    # Email and phone are personal data: only admins get them in the Excel file.
    data = education_filters.build_xlsx(rows, label, include_contacts="Admin" in user.get("role", ""))
    slug = re.sub(r"[^A-Za-z0-9]+", "_", inst_name if params["filter"] in ("P", "ALL") else f"Filter_{params['filter']}_{inst_name}").strip("_")[:80]
    return send_file(io.BytesIO(data), as_attachment=True, download_name=f"{slug}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def _edu_live_params(data):
    """Validated (filter, member ids, year_from, year_to, school names) for a Filter A/B LinkedIn search."""
    f = str(data.get("filter") or "").strip().upper()
    if f not in ("A", "B"):
        raise education_filters.FilterError("filter must be 'A' or 'B'")
    params = education_filters.parse_params({"filter": f, "institution_id": data.get("institution_id"),
                                             "year_from": data.get("year_from"), "year_to": data.get("year_to")})
    members = education_filters.member_ids(params["institution_id"])
    want_country = education_filters.FILTERS[f]["chosen_country"]
    conn = models.get_db_connection()
    try:
        cur = conn.cursor()
        names = []
        for m in members:
            cur.execute("SELECT MIN(canonical_name), MIN(country) FROM institution_aliases WHERE canonical_id = ?", (m,))
            row = cur.fetchone()
            if not row or not row[0] or row[1] != want_country:
                raise education_filters.FilterError("Choose a college from the list.")
            # LinkedIn's school filter needs a real school name, not our "(campus not stated)" label.
            names.append(re.sub(r"\s*\(campus not stated\)\s*$", "", row[0]))
    finally:
        conn.close()
    # A paid search needs the exact passout year: without it every graduation year matched, and the
    # recruiter paid for people from years they didn't want.
    if not params["year_from"] or params["year_from"] != params["year_to"]:
        raise education_filters.FilterError("Choose a passout year before searching LinkedIn.")
    return f, params["institution_id"], members, params["year_from"], params["year_to"], names


@app.route("/api/education/live-start", methods=["POST", "OPTIONS"])
def api_education_live_start():
    """Start a paid LinkedIn search (Apify) for ONE chosen college/university (Filter A/B). Same
    caps as the Passout search: per-run spend cap, daily budget, Apify account headroom. The team's
    shared cursor per (filter, institution) makes the next search continue on new profiles."""
    if request.method == "OPTIONS":
        return make_response("", 200)
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401
    try:
        f, inst_id, members, y_from, y_to, schools = _edu_live_params(request.get_json(silent=True) or {})
    except education_filters.FilterError as ex:
        return jsonify({"error": str(ex)}), 400
    # LinkedIn's years-of-experience facet narrows the search to that cohort. Filter A's year is the
    # Bachelor's year; Filter B's is the Master's year, so its Bachelor's was ~1-3 years earlier.
    bachelor_years = [y_from] if f == "A" else [y_from - 3, y_from - 2, y_from - 1]
    exp_ids = []
    for y in bachelor_years:
        for e in linkedin_sourcing.experience_ids_for_year(y):
            if e not in exp_ids:
                exp_ids.append(e)
    # Per year: a 2023 search must not skip pages that were only scanned for another year.
    cursor_key = f"edu-{f}-{inst_id}-{y_from}"
    cursor = sourcing_store.get_cursor("apify", cursor_key)
    result = linkedin_sourcing.start_search(None, pages=1, start_page=cursor["next_page"],
                                            schools=schools, experience_ids=exp_ids)
    if result.get("error"):
        return jsonify({"error": result["error"]}), result.get("code", 500)
    sourcing_store.save_cursor("apify", cursor_key, next_page=result["next_start_page"])
    models.log_activity(user["id"], user["name"], "LinkedIn Search", "Sourcing", 0,
                        f"Filter {f} live search: {', '.join(schools)} (page {cursor['next_page']})")
    return jsonify(result)


@app.route("/api/education/live-poll", methods=["POST", "OPTIONS"])
def api_education_live_poll():
    """Poll a Filter A/B LinkedIn search run: stores every scanned profile, records the people
    verified for the chosen institution, banks Passout-year matches, returns counts (no raw data)."""
    if request.method == "OPTIONS":
        return make_response("", 200)
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401
    data = request.get_json(silent=True) or {}
    try:
        f, inst_id, members, y_from, y_to, _ = _edu_live_params(data)
    except education_filters.FilterError as ex:
        return jsonify({"error": str(ex)}), 400
    run_id, dataset_id = str(data.get("run_id") or ""), str(data.get("dataset_id") or "")
    if not _APIFY_ID_RE.match(run_id) or not _APIFY_ID_RE.match(dataset_id):
        return jsonify({"error": "Invalid run reference"}), 400
    try:
        offset = max(0, int(data.get("offset") or 0))
        matched_so_far = max(0, int(data.get("matched_so_far") or 0))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid offset"}), 400
    result = linkedin_sourcing.poll_filter_search(run_id, dataset_id, f, members, y_from, y_to,
                                                  offset=offset, matched_so_far=matched_so_far)
    if result.get("error"):
        return jsonify({"error": result["error"]}), result.get("code", 500)
    raw_items = result.pop("raw_items", None) or []
    matches = result.pop("new_matches", None) or []
    passout = result.pop("passout_matches", None) or []
    try:
        if raw_items:
            linkedin_ingest.ingest_profiles(linkedin_ingest.ApifyProfileProvider().to_profiles(raw_items), "apify", user["id"])
        if passout:
            sourcing_store.save_matches("apify", None, passout, user["id"])
        conn = models.get_db_connection()
        try:
            linkedin_ingest.mark_filter_verified(conn, matches)
            linkedin_ingest.mark_verified(conn, passout)
            conn.commit()
        finally:
            conn.close()
    except Exception as ex:
        logger.warning(f"Education live search: could not store results: {ex}")
    result["new_matches"] = len(matches)
    result["passout_banked"] = len(passout)
    return jsonify(result)


def _tracker_profile(profile_id):
    conn = models.get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, name, tracking_status FROM linkedin_profiles WHERE id = ?", (profile_id,))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _tracker_thread(profile_id):
    conn = models.get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("""SELECT c.id, c.kind, c.comment, c.created_at, c.user_id, u.name AS author
                       FROM sourcing_comments c LEFT JOIN users u ON u.id = c.user_id
                       WHERE c.profile_id = ? ORDER BY c.id DESC""", (profile_id,))
        return [{"id": r["id"], "kind": r["kind"], "text": r["comment"], "author": r["author"] or "",
                 "user_id": r["user_id"], "at": str(r["created_at"] or "")[:16]} for r in cur.fetchall()]
    finally:
        conn.close()


@app.route("/api/sourcing/profiles/<int:profile_id>/comments", methods=["GET", "POST", "OPTIONS"])
def api_sourcing_comments(profile_id):
    """Sourcing tracker: a sourced candidate's status history + comments (team-wide). POST adds a comment."""
    if request.method == "OPTIONS":
        return make_response("", 200)
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401
    prof = _tracker_profile(profile_id)
    if not prof:
        return jsonify({"error": "Candidate not found"}), 404
    if request.method == "POST":
        text = ((request.get_json(silent=True) or {}).get("comment") or "").strip()
        if not text:
            return jsonify({"error": "Write a comment first."}), 400
        if len(text) > 2000:
            return jsonify({"error": "Comments are limited to 2,000 characters."}), 400
        conn = models.get_db_connection()
        try:
            conn.cursor().execute("INSERT INTO sourcing_comments (profile_id, user_id, kind, comment) VALUES (?, ?, 'comment', ?)",
                                  (profile_id, user["id"], text))
            conn.commit()
        finally:
            conn.close()
    return jsonify({"status": prof.get("tracking_status") or "New", "thread": _tracker_thread(profile_id),
                    "statuses": education_filters.STATUSES})


@app.route("/api/sourcing/comments/<int:comment_id>", methods=["DELETE", "OPTIONS"])
def api_sourcing_comment_delete(comment_id):
    """Delete a comment - only its author or an admin. Status-change records can't be deleted."""
    if request.method == "OPTIONS":
        return make_response("", 200)
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401
    conn = models.get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, user_id, kind, profile_id FROM sourcing_comments WHERE id = ?", (comment_id,))
        row = cur.fetchone()
        if not row or row["kind"] != "comment":
            return jsonify({"error": "Comment not found"}), 404
        if row["user_id"] != user["id"] and "Admin" not in user.get("role", ""):
            return jsonify({"error": "Only the person who wrote it (or an admin) can delete a comment."}), 403
        cur.execute("DELETE FROM sourcing_comments WHERE id = ?", (comment_id,))
        conn.commit()
        profile_id = row["profile_id"]
    finally:
        conn.close()
    return jsonify({"success": True, "thread": _tracker_thread(profile_id)})


@app.route("/api/sourcing/profiles/<int:profile_id>/status", methods=["POST", "OPTIONS"])
def api_sourcing_status(profile_id):
    """Set a sourced candidate's status (owner rule + history in sourcing_tracker)."""
    if request.method == "OPTIONS":
        return make_response("", 200)
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401
    status = ((request.get_json(silent=True) or {}).get("status") or "").strip()
    conn = models.get_db_connection()
    try:
        c = sourcing_tracker.set_status(conn, profile_id, user, status)
    except sourcing_tracker.TrackerError as ex:
        return jsonify({"error": str(ex)}), 400
    except LookupError:
        return jsonify({"error": "Candidate not found"}), 404
    finally:
        conn.close()
    return jsonify({**c, "status": c["status"]})


@app.route("/api/sourcing/profiles/<int:profile_id>/card", methods=["GET", "POST", "OPTIONS"])
def api_sourcing_card(profile_id):
    """The tracker card: GET it, POST field updates (email, phone, visa, location, relocate, rate,
    availability, follow-up). Saving returns duplicate warnings for the email / phone."""
    if request.method == "OPTIONS":
        return make_response("", 200)
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401
    conn = models.get_db_connection()
    try:
        if request.method == "POST":
            c, warnings = sourcing_tracker.save_fields(conn, profile_id, user, request.get_json(silent=True) or {})
            return jsonify({**c, "warnings": warnings})
        c = sourcing_tracker.card(conn, profile_id)
        if not c:
            return jsonify({"error": "Candidate not found"}), 404
        return jsonify(c)
    except sourcing_tracker.TrackerError as ex:
        return jsonify({"error": str(ex)}), 400
    except LookupError:
        return jsonify({"error": "Candidate not found"}), 404
    finally:
        conn.close()


@app.route("/api/sourcing/profiles/<int:profile_id>/resume", methods=["GET", "POST", "DELETE"])
def api_sourcing_resume(profile_id):
    """The resume attached to a sourced candidate's card: GET downloads it, POST uploads (field
    'file', .docx / .pdf up to 5 MB, replaces any earlier one), DELETE removes it."""
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401
    conn = models.get_db_connection()
    try:
        if request.method == "GET":
            found = sourcing_tracker.get_resume(conn, profile_id)
            if not found:
                return jsonify({"error": "No resume attached"}), 404
            name, data = found
            mime = "application/pdf" if name.lower().endswith(".pdf") else \
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            return send_file(io.BytesIO(data), mimetype=mime, as_attachment=True, download_name=name)
        if request.method == "DELETE":
            return jsonify(sourcing_tracker.delete_resume(conn, profile_id, user))
        f = request.files.get("file")
        if not f or not f.filename:
            return jsonify({"error": "Choose a resume file first."}), 400
        return jsonify(sourcing_tracker.save_resume(conn, profile_id, user, f.filename, f.read(sourcing_tracker.RESUME_MAX_BYTES + 1)))
    except sourcing_tracker.TrackerError as ex:
        return jsonify({"error": str(ex)}), 400
    except LookupError:
        return jsonify({"error": "Candidate not found"}), 404
    finally:
        conn.close()


@app.route("/api/sourcing/profiles/<int:profile_id>/owner", methods=["POST", "OPTIONS"])
def api_sourcing_owner(profile_id):
    """Admins reassign (or clear) a sourced candidate's owner."""
    if request.method == "OPTIONS":
        return make_response("", 200)
    user, err = _edu_admin()
    if err:
        return err
    new_owner = (request.get_json(silent=True) or {}).get("user_id")
    try:
        new_owner = int(new_owner) if new_owner not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        return jsonify({"error": "Unknown recruiter."}), 400
    conn = models.get_db_connection()
    try:
        return jsonify(sourcing_tracker.reassign(conn, profile_id, user, new_owner))
    except sourcing_tracker.TrackerError as ex:
        return jsonify({"error": str(ex)}), 400
    except LookupError:
        return jsonify({"error": "Candidate not found"}), 404
    finally:
        conn.close()


@app.route("/api/sourcing/profiles/<int:profile_id>/add-to-bench", methods=["POST", "OPTIONS"])
def api_sourcing_add_to_bench(profile_id):
    """Create the bench consultant from the tracker card (owned by the recruiter clicking it)."""
    if request.method == "OPTIONS":
        return make_response("", 200)
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401
    conn = models.get_db_connection()
    try:
        c, cand_id = sourcing_tracker.add_to_bench(conn, profile_id, user)
    except sourcing_tracker.TrackerError as ex:
        return jsonify({"error": str(ex)}), 400
    except LookupError:
        return jsonify({"error": "Candidate not found"}), 404
    finally:
        conn.close()
    models.log_activity(user["id"], user["name"], "Added to Bench", "Candidate", cand_id,
                        f"{c['name']} added to the bench from Sourcing")
    return jsonify({**c, "candidate_id": cand_id})


# ---------------------------------------------------------------- Vendors (private per recruiter)

@app.route("/api/vendors", methods=["GET", "POST"])
def api_vendors():
    """GET: the recruiter's own vendor contacts (admins: everyone's, ?owner=<id> for one recruiter).
    POST: add one contact to the recruiter's own list."""
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401
    conn = models.get_db_connection()
    try:
        if request.method == "POST":
            body = request.get_json(silent=True) or {}
            region = _vendor_region_for(user, body)
            new_id, reason = vendors.add_contact(conn, user["id"], body, region=region)
            if not new_id:
                return jsonify({"error": reason}), 400
            return jsonify({"success": True, "id": new_id, "region": region})
        region = _vendor_region_for(user, {"region": request.args.get("region")})
        owner = _int_or_none(request.args.get("owner"))
        rows = vendors.list_contacts(conn, user, q=(request.args.get("q") or "").strip(), owner_id=owner, region=region)
        counts = vendors.region_counts(conn, user, owner_id=owner)
    finally:
        conn.close()
    if request.args.get("format") == "xlsx":
        return send_file(io.BytesIO(vendors.export_xlsx(rows, region)), as_attachment=True,
                         download_name=f"{region}_Vendor_Contacts.xlsx",
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    is_admin = "Admin" in (user.get("role") or "")
    owners = sorted({(r["owner_user_id"], r.get("owner_name") or "") for r in rows}, key=lambda o: o[1].lower()) if is_admin else []
    for r in rows:
        r["mine"] = r["owner_user_id"] == user["id"]
        for k in ("last_emailed_at", "created_at"):
            if r.get(k) is not None:
                r[k] = str(r[k])[:19]
    return jsonify({"contacts": rows, "is_admin": is_admin, "region": region, "region_counts": counts,
                    "owners": [{"id": o[0], "name": o[1]} for o in owners],
                    "companies": len({r["company_norm"] for r in rows})})


@app.route("/api/vendors/upload-preview", methods=["POST"])
def api_vendors_upload_preview():
    """Read an Excel / CSV upload and say what would happen to each row - nothing is saved yet."""
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "Choose an Excel or CSV file."}), 400
    conn = models.get_db_connection()
    try:
        rows = vendors.preview(conn, user["id"], f.read(), f.filename, region=_vendor_region_for(user, {"region": request.form.get("region")}))
    except vendors.VendorError as ex:
        return jsonify({"error": str(ex)}), 400
    finally:
        conn.close()
    counts = {k: sum(1 for r in rows if r["status"] == k) for k in ("new", "duplicate", "problem")}
    return jsonify({"rows": rows, "counts": counts, "filename": f.filename})


@app.route("/api/vendors/import", methods=["POST"])
def api_vendors_import():
    """Save the new rows of a previewed upload into the recruiter's own list (re-checked here)."""
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401
    data = request.get_json(silent=True) or {}
    rows = data.get("rows") or []
    if not isinstance(rows, list) or len(rows) > vendors.MAX_ROWS:
        return jsonify({"error": "Nothing to import."}), 400
    conn = models.get_db_connection()
    try:
        region = _vendor_region_for(user, {"region": data.get("region")})
        result = vendors.import_rows(conn, user["id"], [r for r in rows if isinstance(r, dict)], region=region)
    finally:
        conn.close()
    models.log_activity(user["id"], user["name"], "Imported Vendors", "Vendors", 0,
                        f"{result['added']} {region} vendor contacts from {str(data.get('filename') or 'upload')[:120]}")
    return jsonify({"success": True, "region": region, **result})


@app.route("/api/vendors/template", methods=["GET"])
def api_vendors_template():
    if not current_user():
        return jsonify({"error": "Login required"}), 401
    return send_file(io.BytesIO(vendors.template_xlsx()), as_attachment=True, download_name="Vendor_Contacts_Template.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route("/api/vendors/<int:contact_id>", methods=["PUT", "DELETE"])
def api_vendor_contact(contact_id):
    """Edit / delete a contact - your own (admins: anyone's)."""
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401
    conn = models.get_db_connection()
    try:
        if request.method == "DELETE":
            vendors.delete_contact(conn, contact_id, user)
            return jsonify({"success": True})
        row = vendors.update_contact(conn, contact_id, user, request.get_json(silent=True) or {})
        return jsonify({"success": True, "contact": {k: (str(v) if k.endswith("_at") and v is not None else v) for k, v in row.items()}})
    except vendors.VendorError as ex:
        return jsonify({"error": str(ex)}), 400
    except LookupError:
        return jsonify({"error": "Contact not found"}), 404
    finally:
        conn.close()


@app.route("/api/vendors/match", methods=["POST"])
def api_vendors_match():
    """The recruiter's OWN active contacts at a company (by email domain, then company name)."""
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401
    data = request.get_json(silent=True) or {}
    emails = data.get("emails") or []
    if isinstance(emails, str):
        emails = vendors.find_emails(emails)
    conn = models.get_db_connection()
    try:
        emails = [str(e).strip().lower() for e in emails][:50]
        region = _vendor_region_for(user, data)
        m = vendors.match(conn, user["id"], company=str(data.get("company") or ""), emails=emails, region=region)
        m["unknown_emails"] = vendors.unknown_emails(conn, user["id"], emails, region=region)
    finally:
        conn.close()
    m["contacts"] = [{k: c.get(k) for k in ("id", "company_name", "contact_name", "email", "phone", "title")} for c in m["contacts"]]
    m["max_bcc"] = vendors.MAX_BCC
    return jsonify(m)


@app.route("/api/education/institutions/add", methods=["POST", "OPTIONS"])
def api_education_institution_add():
    """Admin: add a college / university that isn't on the list (name, India/USA, city, short names).
    It appears in Filter A / B straight away, and saved profiles that list any of its names count for it."""
    if request.method == "OPTIONS":
        return make_response("", 200)
    user, err = _edu_admin()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    name = re.sub(r"\s+", " ", str(data.get("name") or "")).strip()[:150]
    country = str(data.get("country") or "").strip()
    city = re.sub(r"\s+", " ", str(data.get("city") or "")).strip()[:80]
    aliases = [re.sub(r"\s+", " ", a).strip()[:150] for a in str(data.get("aliases") or "").split(",") if a.strip()][:10]
    if len(name) < 4 or country not in ("India", "USA"):
        return jsonify({"error": "Enter the college's full name and choose India or USA."}), 400
    names, norms = [], []
    for n in [name] + aliases:
        norm = education_match.normalize(n)
        if norm and norm not in norms:
            names.append(n)
            norms.append(norm)
    conn = models.get_db_connection()
    try:
        cur = conn.cursor()
        for n, norm in zip(names, norms):
            cur.execute("SELECT MIN(canonical_name) FROM institution_aliases WHERE alias_norm = ?", (norm,))
            row = cur.fetchone()
            if row and row[0]:
                return jsonify({"error": f"'{n}' is already on the list as {row[0]} - search for it in Filter A / B."}), 400
        canonical_id = "custom-" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60]
        cur.execute("SELECT 1 FROM institution_aliases WHERE canonical_id = ?", (canonical_id,))
        if cur.fetchone():
            return jsonify({"error": "A college with that name already exists."}), 400
        for n, norm in zip(names, norms):
            cur.execute("""INSERT INTO institution_aliases (canonical_id, canonical_name, alias, alias_norm, country, city)
                           VALUES (?, ?, ?, ?, ?, ?)""", (canonical_id, name, n, norm, country, city))
        cur.execute(f"DELETE FROM unmapped_institutions WHERE name_norm IN ({', '.join(['?'] * len(norms))})", norms)
        conn.commit()
        education_match.invalidate_cache()
        updated = sum(linkedin_ingest.rematch_institution(conn, norm) for norm in norms)
        conn.commit()
    finally:
        conn.close()
    models.log_activity(user["id"], user["name"], "Added Institution", "Sourcing", 0,
                        f"{name} ({city or '-'}, {country}); names: {', '.join(names)}; {updated} saved education entries linked")
    return jsonify({"success": True, "canonical_id": canonical_id, "name": name, "entries_linked": updated})


@app.route("/api/education/unmapped", methods=["GET"])
def api_education_unmapped():
    """Admin list: school names from LinkedIn profiles that didn't match the institution list."""
    user, err = _edu_admin()
    if err:
        return err
    conn = models.get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("""SELECT u.id, u.name, u.name_norm, u.status, u.suggested_canonical_id, u.seen_count,
                              (SELECT MIN(a.canonical_name) FROM institution_aliases a
                               WHERE a.canonical_id = u.suggested_canonical_id) AS suggested_name,
                              (SELECT COUNT(DISTINCT e.profile_id) FROM profile_education e
                               WHERE e.institution_norm = u.name_norm) AS profiles
                       FROM unmapped_institutions u ORDER BY u.seen_count DESC, u.id DESC LIMIT 500""")
        items = [dict(r) for r in cur.fetchall()]
        cur.execute("""SELECT canonical_id, MIN(canonical_name) AS name, MIN(country) AS country
                       FROM institution_aliases GROUP BY canonical_id ORDER BY MIN(country), MIN(canonical_name)""")
        institutions = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
    return jsonify({"items": items, "institutions": institutions})


@app.route("/api/education/unmapped/<int:item_id>/map", methods=["POST", "OPTIONS"])
def api_education_unmapped_map(item_id):
    """Map an unmatched school name to an existing institution, or create a new institution for it.
    The name becomes an alias, and every stored education entry with that name is updated."""
    if request.method == "OPTIONS":
        return make_response("", 200)
    user, err = _edu_admin()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    conn = models.get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, name, name_norm FROM unmapped_institutions WHERE id = ?", (item_id,))
        item = cur.fetchone()
        if not item:
            return jsonify({"error": "Not found"}), 404
        item = dict(item)
        canonical_id = (data.get("canonical_id") or "").strip()
        if canonical_id:
            cur.execute("SELECT canonical_name, country, city FROM institution_aliases WHERE canonical_id = ? LIMIT 1",
                        (canonical_id,))
            inst = cur.fetchone()
            if not inst:
                return jsonify({"error": "Unknown institution"}), 400
            canonical_name, country, city = inst["canonical_name"], inst["country"], inst["city"]
        else:
            canonical_name = (data.get("new_name") or "").strip()
            country = (data.get("country") or "").strip()
            city = (data.get("city") or "").strip()
            if not canonical_name or country not in ("India", "USA", "Other"):
                return jsonify({"error": "A new institution needs a name and a country (India, USA or Other)."}), 400
            canonical_id = "custom-" + re.sub(r"[^a-z0-9]+", "-", canonical_name.lower()).strip("-")[:60]
            cur.execute("SELECT 1 FROM institution_aliases WHERE canonical_id = ?", (canonical_id,))
            if cur.fetchone():
                return jsonify({"error": "That institution already exists - pick it from the list instead."}), 400
            name_norm = education_match.normalize(canonical_name)
            cur.execute("SELECT 1 FROM institution_aliases WHERE alias_norm = ?", (name_norm,))
            if cur.fetchone():
                return jsonify({"error": "That name already belongs to an institution - pick it from the list instead."}), 400
            if name_norm != item["name_norm"]:
                cur.execute("""INSERT INTO institution_aliases (canonical_id, canonical_name, alias, alias_norm, country, city)
                               VALUES (?, ?, ?, ?, ?, ?)""",
                            (canonical_id, canonical_name, canonical_name, name_norm, country, city))
        cur.execute("SELECT canonical_id FROM institution_aliases WHERE alias_norm = ?", (item["name_norm"],))
        existing = cur.fetchone()
        if existing and existing[0] != canonical_id:
            conn.rollback()
            return jsonify({"error": "That name is already an alias of another institution."}), 400
        if not existing:
            cur.execute("""INSERT INTO institution_aliases (canonical_id, canonical_name, alias, alias_norm, country, city)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (canonical_id, canonical_name, item["name"], item["name_norm"], country, city))
        cur.execute("DELETE FROM unmapped_institutions WHERE id = ?", (item_id,))
        conn.commit()
        education_match.invalidate_cache()
        updated = linkedin_ingest.rematch_institution(conn, item["name_norm"])
        conn.commit()
    finally:
        conn.close()
    models.log_activity(user["id"], user["name"], "Mapped Institution", "Sourcing", item_id,
                        f"'{item['name']}' -> {canonical_name} ({country}); {updated} education entries updated")
    return jsonify({"success": True, "canonical_id": canonical_id, "canonical_name": canonical_name,
                    "entries_updated": updated})
