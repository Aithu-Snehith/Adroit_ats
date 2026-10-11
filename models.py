import os
import re
import sqlite3
import urllib.parse
import json
import logging
from datetime import datetime, timedelta, timezone
from werkzeug.security import generate_password_hash, check_password_hash
import config

logger = logging.getLogger("models")


class DbRow(dict):
    """Row wrapper compatible with both SQLite Row and psycopg2 tuple/dict access."""
    def __init__(self, col_names, row_tuple):
        super().__init__(zip(col_names, row_tuple))
        self._tuple = row_tuple
        self._col_names = col_names

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._tuple[key]
        return super().__getitem__(key)


class PgCursorWrapper:
    def __init__(self, cursor, conn):
        self._cursor = cursor
        self._conn = conn
        self.lastrowid = None

    @property
    def description(self):
        return self._cursor.description

    def _adapt_query(self, sql):
        s = sql.strip()
        is_insert = bool(re.match(r'^\s*INSERT\s+INTO\s+', s, re.IGNORECASE))
        has_returning = bool(re.search(r'\bRETURNING\b', s, re.IGNORECASE))

        # Convert SQLite AUTOINCREMENT to Postgres SERIAL
        s = re.sub(r'id\s+INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT', 'id SERIAL PRIMARY KEY', s, flags=re.IGNORECASE)
        # Convert parameter placeholders ? to %s
        s = s.replace('?', '%s')

        append_returning = is_insert and not has_returning
        if append_returning:
            if s.endswith(';'):
                s = s[:-1].strip()
            s += ' RETURNING id'

        return s, append_returning

    def execute(self, sql, params=None):
        adapted_sql, append_returning = self._adapt_query(sql)
        try:
            if params is not None:
                self._cursor.execute(adapted_sql, params)
            else:
                self._cursor.execute(adapted_sql)
        except Exception:
            try:
                self._conn.rollback()
            except Exception:
                pass
            raise

        if append_returning:
            try:
                row = self._cursor.fetchone()
                if row:
                    self.lastrowid = row[0]
            except Exception:
                self.lastrowid = None
        else:
            self.lastrowid = None

        return self

    def executemany(self, sql, seq_of_params):
        adapted_sql, _ = self._adapt_query(sql)
        try:
            self._cursor.executemany(adapted_sql, seq_of_params)
        except Exception:
            try:
                self._conn.rollback()
            except Exception:
                pass
            raise
        return self

    def fetchone(self):
        row = self._cursor.fetchone()
        if row is None:
            return None
        col_names = [desc[0] for desc in self._cursor.description]
        return DbRow(col_names, row)

    def fetchall(self):
        rows = self._cursor.fetchall()
        if not rows:
            return []
        col_names = [desc[0] for desc in self._cursor.description]
        return [DbRow(col_names, r) for r in rows]

    def close(self):
        self._cursor.close()


class PgConnectionWrapper:
    def __init__(self, raw_conn):
        self._conn = raw_conn

    def cursor(self):
        return PgCursorWrapper(self._conn.cursor(), self._conn)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


def is_postgres(conn):
    return isinstance(conn, PgConnectionWrapper)


class DatabaseUnavailable(RuntimeError):
    pass


def get_db_connection():
    # If DATABASE_URL is configured (Render PostgreSQL), connect via psycopg2. Never fall back to
    # SQLite here: that silently saved data (e.g. an uploaded vendor list) into a throwaway file on
    # Render's disk, which every deploy wipes - the recruiter saw it once and then it was gone.
    # Retry briefly instead, then fail loudly so nothing is "saved" where it won't last.
    if getattr(config, "DATABASE_URL", None):
        import time as _time
        import psycopg2
        last = None
        for wait in (0, 0.5, 1.5):
            if wait:
                _time.sleep(wait)
            try:
                return PgConnectionWrapper(psycopg2.connect(config.DATABASE_URL, connect_timeout=10))
            except Exception as e:
                last = e
                logger.warning("PostgreSQL connection failed (%s); retrying.", e.__class__.__name__)
        logger.error("PostgreSQL unavailable after retries: %s", last)
        raise DatabaseUnavailable("The database is unavailable right now - nothing was saved. Please try again in a minute.")

    # Local development (no DATABASE_URL): SQLite
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    # 1. Create Users table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT,
        role TEXT DEFAULT 'Recruiter',
        avatar_url TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # 2. Create Candidates / Consultants table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS candidates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT NOT NULL,
        phone TEXT,
        title TEXT,
        primary_skills TEXT,
        experience_years INTEGER DEFAULT 5,
        target_rate TEXT DEFAULT '$90/hr (C2C)',
        visa_status TEXT DEFAULT 'C2C Eligible',
        status TEXT DEFAULT 'Available',
        location TEXT DEFAULT 'United States (Remote)',
        resume_filename TEXT,
        resume_path TEXT,
        resume_text TEXT,
        resume_summary TEXT,
        gmail_account TEXT,
        gmail_token_path TEXT,
        assigned_user_id INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # 3. Create Jobs table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        company TEXT NOT NULL,
        location TEXT DEFAULT 'United States',
        job_type TEXT DEFAULT 'Contract (C2C)',
        salary TEXT,
        source TEXT DEFAULT 'Dice',
        url TEXT,
        recruiter_email TEXT,
        recruiter_phone TEXT,
        recruiter_name TEXT,
        description TEXT,
        matched_skills TEXT,
        match_score INTEGER DEFAULT 88,
        status TEXT DEFAULT 'Open',
        is_24h INTEGER DEFAULT 1,
        is_seed_example INTEGER DEFAULT 0,
        scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # 4. Create Applications / Pipeline table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS applications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER NOT NULL,
        candidate_id INTEGER NOT NULL,
        user_id INTEGER,
        stage TEXT DEFAULT 'Saved',
        notes TEXT,
        draft_id TEXT,
        drafted_at TIMESTAMP,
        applied_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (job_id) REFERENCES jobs (id) ON DELETE CASCADE,
        FOREIGN KEY (candidate_id) REFERENCES candidates (id) ON DELETE CASCADE
    );
    """)

    # 5. Create Activity Logs table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS activity_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        user_name TEXT,
        action TEXT NOT NULL,
        target_type TEXT,
        target_id INTEGER,
        details TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # 6. Sourcing: verified matches found by ANY recruiter, shared by the whole team so
    # nobody pays to re-scan a search someone else already ran (see sourcing_store.py).
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS sourced_candidates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT NOT NULL,
        search_year TEXT NOT NULL DEFAULT '',
        profile_url TEXT NOT NULL,
        name TEXT,
        headline TEXT,
        bachelor_year TEXT,
        bachelor_degree TEXT,
        bachelor_college TEXT,
        master_degree TEXT,
        master_university TEXT,
        master_year TEXT,
        location TEXT,
        status_tag TEXT,
        settlement_badge TEXT,
        settlement_sub TEXT,
        quality TEXT,
        degree TEXT,
        found_by_user_id INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # 7. Sourcing: one search cursor per (source, year) shared by the whole team - the
    # next click continues from wherever the LAST recruiter's search left off, instead of
    # every recruiter re-scanning from page 1.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS sourcing_cursor (
        source TEXT NOT NULL,
        search_year TEXT NOT NULL DEFAULT '',
        next_page INTEGER DEFAULT 1,
        scroll_token TEXT,
        mode TEXT,
        exhausted INTEGER DEFAULT 0,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (source, search_year)
    );
    """)

    # 8. The consultant's ORIGINAL resume file. Kept in the database (not only on disk) because
    # Render's disk is wiped on every deploy, and the Resume Optimizer needs the original .docx
    # to edit it in place without losing its formatting. Separate table so `SELECT c.*` on
    # candidates never drags file bytes into list/JSON responses.
    blob_type = "BYTEA" if is_postgres(conn) else "BLOB"
    cursor.execute(f"""
    CREATE TABLE IF NOT EXISTS resume_files (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        candidate_id INTEGER UNIQUE NOT NULL,
        filename TEXT,
        data {blob_type},
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    # Application tracker history: every status / field change of an application (who, when, what).
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS application_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        application_id INTEGER NOT NULL,
        user_id INTEGER,
        kind TEXT NOT NULL,
        detail TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    # JD-tailored resume versions the recruiter reviewed and chose to SAVE (resume_versions.py).
    # Several per consultant; the original resume in resume_files is never touched.
    cursor.execute(f"""
    CREATE TABLE IF NOT EXISTS optimized_resumes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        candidate_id INTEGER NOT NULL,
        job_id INTEGER,
        filename TEXT NOT NULL,
        data {blob_type},
        primary_skill TEXT,
        jd_cloud TEXT,
        jd_excerpt TEXT,
        created_by INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # 9. Education-based Sourcing (Filter A: Indian college -> US Master's; Filter B: US university
    # -> Indian undergrad). One row per LinkedIn profile (deduplicated on linkedin_url), its
    # education entries, the institution alias table used to infer each school's country, the admin
    # list of schools that couldn't be matched, and an audit log of captures/exports.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS linkedin_profiles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        linkedin_url TEXT UNIQUE NOT NULL,
        name TEXT,
        headline TEXT,
        location TEXT,
        current_company TEXT,
        current_title TEXT,
        experience_json TEXT,
        source TEXT NOT NULL,
        captured_by INTEGER,
        education_complete INTEGER DEFAULT 0,
        captured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS profile_education (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        profile_id INTEGER NOT NULL,
        institution_name TEXT,
        institution_norm TEXT,
        institution_canonical_id TEXT,
        degree TEXT,
        degree_level TEXT,
        field_of_study TEXT,
        country TEXT,
        start_year INTEGER,
        end_year INTEGER,
        match_method TEXT
    );
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS institution_aliases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        canonical_id TEXT NOT NULL,
        canonical_name TEXT NOT NULL,
        alias TEXT NOT NULL,
        alias_norm TEXT UNIQUE NOT NULL,
        country TEXT NOT NULL,
        city TEXT
    );
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS unmapped_institutions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        name_norm TEXT UNIQUE NOT NULL,
        status TEXT,
        suggested_canonical_id TEXT,
        suggested_score INTEGER,
        seen_count INTEGER DEFAULT 1,
        first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS capture_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        action TEXT NOT NULL,
        source TEXT,
        profile_id INTEGER,
        details TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    # People a Filter A / Filter B LinkedIn search verified for a chosen institution (shown in that
    # filter even when their OTHER school isn't on the college list).
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS profile_verifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        profile_id INTEGER NOT NULL,
        filter_key TEXT NOT NULL,
        institution_id TEXT NOT NULL,
        chosen_year INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    # Resume a recruiter attached to a sourced candidate's card (one per candidate; copied to the
    # bench consultant on Add to Bench). Own table so profile queries never load file bytes.
    cursor.execute(f"""
    CREATE TABLE IF NOT EXISTS sourcing_resumes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        profile_id INTEGER UNIQUE NOT NULL,
        filename TEXT NOT NULL,
        data {blob_type},
        uploaded_by INTEGER,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    # New requirement emails found in a consultant's Gmail (inbox_requirements.py): each message once,
    # and what happened to it (filtered / not a requirement / low match / drafted / error).
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS inbox_requirements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        candidate_id INTEGER NOT NULL,
        message_id TEXT NOT NULL,
        from_email TEXT,
        from_name TEXT,
        subject TEXT,
        received_at TEXT,
        status TEXT,
        reason TEXT,
        match_pct INTEGER,
        job_id INTEGER,
        resume_version_id INTEGER,
        attempts INTEGER DEFAULT 0,
        ai_model TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    # Recruiter replies to applications, read from the consultant's Gmail (reply_inbox.py): the text,
    # the AI's sorting (interview / question / ...), the prepared reply and what was done with it.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS application_replies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        application_id INTEGER NOT NULL,
        candidate_id INTEGER NOT NULL,
        message_id TEXT NOT NULL,
        in_reply_to TEXT,
        references_hdr TEXT,
        from_email TEXT,
        from_name TEXT,
        subject TEXT,
        received_at TEXT,
        body TEXT,
        category TEXT,
        category_reason TEXT,
        ai_model TEXT,
        draft_body TEXT,
        draft_note TEXT,
        gmail_draft_at TEXT,
        sent_at TEXT,
        handled INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    # Sourcing tracker: recruiters' comments on a sourced candidate (and a record of every status
    # change, kind = 'status'). Team-wide, like the rest of Sourcing.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS sourcing_comments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        profile_id INTEGER NOT NULL,
        user_id INTEGER,
        kind TEXT NOT NULL DEFAULT 'comment',
        comment TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    # Vendor contacts: each recruiter's private list of vendor recruiters (see vendors.py).
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS vendor_contacts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_user_id INTEGER NOT NULL,
        company_name TEXT NOT NULL,
        company_norm TEXT,
        contact_name TEXT,
        email TEXT NOT NULL,
        email_domain TEXT,
        phone TEXT,
        title TEXT,
        notes TEXT,
        status TEXT NOT NULL DEFAULT 'active',
        source TEXT,
        last_emailed_at TIMESTAMP,
        last_emailed_note TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    # HarvestAPI direct sourcing runs (harvest_direct.py): one row per LinkedIn result page.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS harvest_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        status TEXT NOT NULL DEFAULT 'READY',
        start_page INTEGER,
        params_json TEXT,
        items_json TEXT,
        search_total INTEGER,
        search_found INTEGER,
        profiles_ok INTEGER,
        profile_calls INTEGER,
        cost_usd REAL DEFAULT 0,
        error TEXT,
        abort INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        finished_at TIMESTAMP
    );
    """)
    # Automatic known-vendor drafts made from Browse Jobs (limit: 10 per consultant per day, never
    # the same job + consultant twice).
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS vendor_auto_drafts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        candidate_id INTEGER NOT NULL,
        job_id INTEGER NOT NULL,
        user_id INTEGER,
        to_email TEXT,
        bcc_count INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS app_meta (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        meta_key TEXT UNIQUE NOT NULL,
        meta_value TEXT
    );
    """)

    conn.commit()

    for idx_sql in (
        "CREATE INDEX IF NOT EXISTS ix_profile_education_profile ON profile_education (profile_id)",
        "CREATE INDEX IF NOT EXISTS ix_profile_education_inst ON profile_education (institution_canonical_id, degree_level)",
        "CREATE INDEX IF NOT EXISTS ix_institution_aliases_canonical ON institution_aliases (canonical_id)",
        "CREATE INDEX IF NOT EXISTS ix_sourcing_comments_profile ON sourcing_comments (profile_id)",
        "CREATE INDEX IF NOT EXISTS ix_vendor_contacts_owner ON vendor_contacts (owner_user_id, email_domain)",
        "CREATE INDEX IF NOT EXISTS ix_vendor_contacts_company ON vendor_contacts (owner_user_id, company_norm)",
        "CREATE INDEX IF NOT EXISTS ix_vendor_auto_drafts_cand ON vendor_auto_drafts (candidate_id, created_at)",
        # one row per Gmail message per consultant - also how two server workers avoid handling a reply twice
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_application_replies_msg ON application_replies (candidate_id, message_id)",
        "CREATE INDEX IF NOT EXISTS ix_application_replies_app ON application_replies (application_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_inbox_requirements_msg ON inbox_requirements (candidate_id, message_id)",
    ):
        try:
            cursor.execute(idx_sql)
            conn.commit()
        except Exception as ex:
            print("[WARN] education index:", ex)

    # sourced_candidates needs one profile per source at most - added after the CREATE (same
    # statement works on both SQLite and Postgres) so it applies to databases that already
    # had the table before this constraint existed.
    try:
        cursor.execute("""CREATE UNIQUE INDEX IF NOT EXISTS ux_sourced_candidates_source_url
                          ON sourced_candidates (source, profile_url)""")
        conn.commit()
    except Exception as ex:
        print("[WARN] sourced_candidates unique index:", ex)

    # Apply schema migrations for missing columns in existing databases
    migrate_db(conn)
    try:
        import regions
        regions.backfill(conn)          # existing users get a region once (from their consultants' countries)
    except Exception as ex:
        print("[WARN] region backfill:", ex)
    try:
        cursor.execute("CREATE INDEX IF NOT EXISTS ix_profile_education_norm ON profile_education (institution_norm)")
        conn.commit()
    except Exception as ex:
        print("[WARN] education index:", ex)

    # Seed initial data if tables are empty
    cursor.execute("SELECT COUNT(*) FROM users")
    if cursor.fetchone()[0] == 0:
        seed_initial_data(conn)

    # NOTE: there used to be an ensure_default_consultants(conn) call here that re-inserted a
    # hardcoded list of consultants on EVERY startup (every Render deploy), so any consultant a
    # recruiter deleted came straight back. The bench is recruiter-managed data - startup never
    # recreates candidates.

    # Ensure fresh 24h US IT jobs exist (Localhost & Render)
    ensure_default_jobs(conn)

    # Institution alias list for the education filters (only inserts rows new in this seed version).
    try:
        ensure_institution_aliases(conn)
    except Exception as ex:
        print("[WARN] institution alias seed:", ex)

    # Copy Sourcing results not yet in the education-filter tables (free, no API calls).
    try:
        import linkedin_ingest
        linkedin_ingest.import_sourced_pool(conn)
        linkedin_ingest.sync_verified_years(conn)
    except Exception as ex:
        print("[WARN] education filters pool import:", ex)

    conn.close()


def ensure_institution_aliases(conn):
    """Insert institutions_seed.INSTITUTIONS into institution_aliases when the code's SEED_VERSION is
    newer than the one this database last applied. Only aliases not already present are inserted,
    so admin-added aliases are kept. Runs once per seed version - not on every restart - so an alias
    an admin deleted is only re-added if a later seed version changes the list."""
    import institutions_seed
    import education_match

    cursor = conn.cursor()
    cursor.execute("SELECT meta_value FROM app_meta WHERE meta_key = ?", ("institution_seed_version",))
    row = cursor.fetchone()
    applied = int(row[0]) if row and str(row[0]).isdigit() else 0
    if applied >= institutions_seed.SEED_VERSION:
        return

    cursor.execute("SELECT alias_norm FROM institution_aliases")
    existing = {r[0] for r in cursor.fetchall()}
    added = 0
    for canonical_id, canonical_name, country, city, aliases in institutions_seed.INSTITUTIONS:
        for alias in [canonical_name] + list(aliases):
            norm = education_match.normalize(alias)
            if not norm or norm in existing:
                continue
            cursor.execute("""INSERT INTO institution_aliases
                              (canonical_id, canonical_name, alias, alias_norm, country, city)
                              VALUES (?, ?, ?, ?, ?, ?)""",
                           (canonical_id, canonical_name, alias, norm, country, city))
            # saved profiles that already list this school name now count for it too
            cursor.execute("""UPDATE profile_education SET institution_canonical_id = ?, country = ?, match_method = 'seed_alias'
                              WHERE institution_norm = ? AND institution_canonical_id IS NULL""", (canonical_id, country, norm))
            existing.add(norm)
            added += 1
    if row:
        cursor.execute("UPDATE app_meta SET meta_value = ? WHERE meta_key = ?",
                       (str(institutions_seed.SEED_VERSION), "institution_seed_version"))
    else:
        cursor.execute("INSERT INTO app_meta (meta_key, meta_value) VALUES (?, ?)",
                       ("institution_seed_version", str(institutions_seed.SEED_VERSION)))
    conn.commit()
    education_match.invalidate_cache()
    logger.info("Institution aliases: seed v%s applied, %d alias rows added.", institutions_seed.SEED_VERSION, added)

def migrate_db(conn):
    """Automatically adds missing columns to existing SQLite or Postgres tables."""
    cursor = conn.cursor()
    use_pg = is_postgres(conn)

    def get_existing_cols(table_name):
        if use_pg:
            cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name = %s", (table_name,))
            return [row[0].lower() for row in cursor.fetchall()]
        else:
            cursor.execute(f"PRAGMA table_info({table_name})")
            return [row[1].lower() for row in cursor.fetchall()]

    # Users table columns
    u_cols = get_existing_cols("users")
    if "password_hash" not in u_cols:
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
        except Exception:
            pass
    if "region" not in u_cols:          # USA / India team (regions.py)
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN region TEXT")
        except Exception:
            pass
    if "auto_reply_mode" not in u_cols:   # off / draft / send_safe (reply_inbox.py)
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN auto_reply_mode TEXT DEFAULT 'draft'")
        except Exception:
            pass

    # Candidate table columns
    c_cols = get_existing_cols("candidates")
    candidate_new_cols = {
        "visa_status": "TEXT DEFAULT 'C2C Eligible'",
        "resume_filename": "TEXT",
        "resume_path": "TEXT",
        "resume_text": "TEXT",
        "gmail_account": "TEXT",
        "gmail_token_path": "TEXT",
        "gmail_app_password": "TEXT",
        "assigned_user_id": "INTEGER DEFAULT 1",
        "country": "TEXT DEFAULT 'United States'"
    }
    for col, c_type in candidate_new_cols.items():
        if col.lower() not in c_cols:
            try:
                cursor.execute(f"ALTER TABLE candidates ADD COLUMN {col} {c_type}")
            except Exception:
                pass

    # Jobs table columns
    j_cols = get_existing_cols("jobs")
    job_new_cols = {
        "recruiter_email": "TEXT",
        "recruiter_phone": "TEXT",
        "recruiter_name": "TEXT",
        "matched_skills": "TEXT",
        "is_24h": "INTEGER DEFAULT 1",
        "is_seed_example": "INTEGER DEFAULT 0",
        "scraped_at": "TEXT",
        "country": "TEXT DEFAULT 'United States'",
        # Full job description read from the posting on demand (Resume Optimizer); the scraped
        # one-line summary stays in `description`.
        "full_description": "TEXT",
        # How the team applied to this job (Jobs tab "Applied" column): '', portal, email, both.
        "apply_status": "TEXT"
    }
    for col, c_type in job_new_cols.items():
        if col.lower() not in j_cols:
            try:
                cursor.execute(f"ALTER TABLE jobs ADD COLUMN {col} {c_type}")
            except Exception:
                pass

    # Applications table columns
    a_cols = get_existing_cols("applications")
    app_new_cols = {
        "draft_id": "TEXT",
        "drafted_at": "TEXT",
        # Application tracker (application_tracker.py)
        "apply_method": "TEXT",
        "client_company": "TEXT",
        "recruiter_name": "TEXT",
        "recruiter_email": "TEXT",
        "recruiter_phone": "TEXT",
        "rate": "TEXT",
        "resume_version_id": "INTEGER",
        "follow_up_date": "TEXT",
        "interview_at": "TEXT",
        "interview_round": "TEXT",
        "last_reply_at": "TEXT",
        # Message-ID of the email we drafted, so a reply is matched to it and answered in the same thread
        "outreach_message_id": "TEXT",
    }
    for col, c_type in app_new_cols.items():
        if col.lower() not in a_cols:
            try:
                cursor.execute(f"ALTER TABLE applications ADD COLUMN {col} {c_type}")
            except Exception:
                pass

    # Education-filter tables (added after their first deploy)
    for table, new_cols in (("linkedin_profiles", {"education_complete": "INTEGER DEFAULT 0",
                                                   "verified_bachelor_year": "INTEGER",
                                                   "tracking_status": "TEXT",
                                                   # sourcing tracker card (sourcing_tracker.py)
                                                   "contact_email": "TEXT", "contact_phone": "TEXT",
                                                   "visa_status": "TEXT", "current_location": "TEXT",
                                                   "open_to_relocate": "TEXT", "expected_rate": "TEXT",
                                                   "availability": "TEXT", "follow_up_date": "TEXT",
                                                   "owner_user_id": "INTEGER", "bench_candidate_id": "INTEGER",
                                                   # more card details (links, work, extras)
                                                   "github_url": "TEXT", "portfolio_url": "TEXT",
                                                   "experience_years": "TEXT", "primary_skills": "TEXT",
                                                   "work_mode": "TEXT", "preferred_locations": "TEXT",
                                                   "current_rate": "TEXT", "visa_expiry": "TEXT",
                                                   "certifications": "TEXT", "referred_by": "TEXT",
                                                   "marital_status": "TEXT",
                                                   # from LinkedIn (linkedin_ingest.work_info)
                                                   "open_to_work": "INTEGER", "current_employment_type": "TEXT",
                                                   "current_workplace_type": "TEXT"}),
                            ("profile_education", {"institution_norm": "TEXT"})):
        existing_cols = get_existing_cols(table)
        for col, c_type in new_cols.items():
            if existing_cols and col not in existing_cols:
                try:
                    cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col} {c_type}")
                except Exception:
                    pass

    # Ensure default Admin account has password_hash and 'Admin' role
    try:
        default_admin_hash = generate_password_hash("Admin@2026")
        cursor.execute("SELECT id, password_hash, role FROM users WHERE email = ?", ("praveen@adroit-ai.com",))
        admin_user = cursor.fetchone()
        if admin_user:
            admin_id = admin_user["id"]
            has_pw = bool(admin_user["password_hash"]) if "password_hash" in [col[0] for col in cursor.description] and admin_user["password_hash"] else False
            if not has_pw:
                cursor.execute("UPDATE users SET password_hash = ?, role = 'Admin' WHERE id = ?", (default_admin_hash, admin_id))
            else:
                cursor.execute("UPDATE users SET role = 'Admin' WHERE id = ?", (admin_id,))
        else:
            cursor.execute("""
            INSERT INTO users (name, email, password_hash, role, avatar_url)
            VALUES (?, ?, ?, ?, ?)
            """, (
                "Praveen Valipireddy",
                "praveen@adroit-ai.com",
                default_admin_hash,
                "Admin",
                "https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=150&auto=format&fit=crop&q=80"
            ))

        cursor.execute("SELECT id, password_hash FROM users WHERE email = ?", ("praveen@aventra-ai.com",))
        aventra_user = cursor.fetchone()
        if aventra_user:
            av_has_pw = bool(aventra_user["password_hash"]) if "password_hash" in [col[0] for col in cursor.description] and aventra_user["password_hash"] else False
            if not av_has_pw:
                cursor.execute("UPDATE users SET password_hash = ?, role = 'Admin' WHERE id = ?", (default_admin_hash, aventra_user["id"]))

        # Admin password: set ADMIN_PASSWORD on the server to control it. Without it the
        # default published in this public repo stays in effect, so warn loudly.
        if config.ADMIN_PASSWORD:
            new_admin_hash = generate_password_hash(config.ADMIN_PASSWORD)
            for admin_email in ("praveen@adroit-ai.com", "praveen@aventra-ai.com"):
                cursor.execute("UPDATE users SET password_hash = ? WHERE email = ?", (new_admin_hash, admin_email))
        else:
            cursor.execute("SELECT password_hash FROM users WHERE email = ?", ("praveen@adroit-ai.com",))
            pw_row = cursor.fetchone()
            if pw_row and pw_row["password_hash"] and check_password_hash(pw_row["password_hash"], "Admin@2026"):
                logger.warning("SECURITY: the admin account still uses the default password published in the source code. "
                               "Set ADMIN_PASSWORD in the server environment to change it.")

        # Migrate any orphaned candidates without assigned_user_id to Admin (id=1)
        cursor.execute("UPDATE candidates SET assigned_user_id = 1 WHERE assigned_user_id IS NULL OR assigned_user_id = 0")
    except Exception as ex:
        print("[WARN] migrate admin/candidates error:", ex)

    conn.commit()


def ensure_default_jobs(conn):
    """Guarantees that full catalog of 24h US contract jobs is available in the database across environments."""
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM jobs")
    count = cursor.fetchone()[0]
    if count < 20:
        seed_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "seed_jobs.json")
        if os.path.exists(seed_path):
            try:
                with open(seed_path, "r", encoding="utf-8") as f:
                    jobs = json.load(f)
                for j in jobs:
                    # is_seed_example=1 marks this as historical example data, never a live scrape -
                    # the "LIVE 24h" filter (get_jobs(is_24h_only=True)) must never show it, no matter
                    # how fresh scraped_at looks. scraped_at is left NULL (not "now") so it can never
                    # pass a naive freshness check either, even if that exclusion is ever bypassed.
                    cursor.execute("""
                    INSERT INTO jobs (
                        title, company, location, job_type, salary, source, url,
                        recruiter_email, recruiter_phone, recruiter_name, description,
                        matched_skills, match_score, status, is_24h, is_seed_example, scraped_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        j.get("title"), j.get("company"), j.get("location"), j.get("job_type", "Contract (C2C)"),
                        j.get("salary"), j.get("source", "Dice"), j.get("url"),
                        j.get("recruiter_email"), j.get("recruiter_phone"), j.get("recruiter_name"),
                        j.get("description"), j.get("matched_skills"), j.get("match_score", 88),
                        j.get("status", "Open"), j.get("is_24h", 1), 1, None
                    ))
                conn.commit()
                logger.info(f"Seeded {len(jobs)} live US IT jobs into database.")
            except Exception as e:
                logger.error(f"Error seeding jobs: {e}")

def seed_initial_data(conn):
    cursor = conn.cursor()

    # 1. Default Recruiter / Admin User
    cursor.execute("""
    INSERT INTO users (name, email, role, avatar_url)
    VALUES (?, ?, ?, ?)
    """, (
        "Praveen Valipireddy",
        "praveen@adroit-ai.com",
        "Lead Technical Recruiter & Admin",
        "https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=150&auto=format&fit=crop&q=80"
    ))

    # 2. Seed Initial US Bench Consultants
    praveen_resume_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "resumes", "Praveen_Valipireddy_Resume.docx")
    praveen_token_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "tokens", "token_1.json")

    candidates = [
        (
            "Valipireddy Praveen",
            "praveen.valipireddy1998@gmail.com",
            "+1 (555) 382-9912",
            "Principal AI Automation & Cloud Architect",
            "Python, AI Automations, n8n, Make.com, LangChain, OpenAI APIs, Spring Boot, AWS, Docker",
            8,
            "$90/hr (C2C)",
            "US Citizen / C2C Eligible",
            "Available",
            "Dallas, TX (Hybrid/Remote)",
            "Praveen_Valipireddy_Resume.docx" if os.path.exists(praveen_resume_path) else None,
            praveen_resume_path if os.path.exists(praveen_resume_path) else None,
            "Specialist in enterprise AI agent automations, cloud architectures, n8n/Make workflows, and full-stack integration.",
            "praveen.valipireddy1998@gmail.com",
            praveen_token_path if os.path.exists(praveen_token_path) else None
        )
    ]

    cursor.executemany("""
    INSERT INTO candidates (
        name, email, phone, title, primary_skills, experience_years, 
        target_rate, visa_status, status, location, 
        resume_filename, resume_path, resume_summary, gmail_account, gmail_token_path
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, candidates)

    # 3. Seed Fresh US 24-Hour IT Jobs
    jobs = [
        (
            "Principal AI & Cloud Automation Engineer",
            "Apex Fintech Global",
            "Dallas, TX (Hybrid/Remote)",
            "Contract (C2C)",
            "$95 - $105/hr",
            "LinkedIn (Live 24h)",
            "https://www.linkedin.com/jobs/view/apex-fintech-ai-automation",
            "recruiter@apexfintech.com",
            "+1 (469) 882-9011",
            "Sarah Mitchell",
            "Seeking an AI Automation & Cloud Architect for 12+ month C2C engagement. Lead LLM agent automations and enterprise API workflows on AWS. Send resumes to recruiter@apexfintech.com.",
            "Python, AI Automations, LangChain, AWS, Docker",
            98,
            "Open",
            1
        ),
        (
            "Senior GenAI / LLM Systems Developer",
            "Cognitive Pulse AI",
            "San Francisco, CA (Remote)",
            "Contract (C2C)",
            "$90 - $110/hr",
            "Dice (Live 24h)",
            "https://www.dice.com/jobs/genai-eng-cognitive",
            "hiring@cognitivepulse.ai",
            "+1 (415) 773-8821",
            "David Vance",
            "Immediate C2C opening for GenAI Engineer. Experience with RAG pipelines, Vector databases, and LLM APIs. Contact hiring@cognitivepulse.ai with rate and updated resume.",
            "Python, LangChain, Vector DBs, PyTorch, OpenAI APIs",
            96,
            "Open",
            1
        ),
        (
            "Lead Kubernetes & DevOps Architect",
            "Vanguard Tech Solutions",
            "Austin, TX (Remote)",
            "Contract (6-12 Mos)",
            "$85 - $95/hr",
            "Indeed (Live 24h)",
            "https://www.indeed.com/viewjob?jk=vanguard-devops",
            "staffing@vanguardtech.io",
            "+1 (512) 662-3341",
            "Jennifer Hayes",
            "Urgent requirement for Senior DevOps/SRE Engineer. Multi-cluster Kubernetes, Terraform, ArgoCD, and AWS/Azure cloud security.",
            "Kubernetes, Terraform, ArgoCD, AWS, Azure, CI/CD",
            94,
            "Open",
            1
        ),
        (
            "Senior Full Stack React / TypeScript Specialist",
            "Northstar Health Systems",
            "New York, NY (Hybrid)",
            "Contract (C2C)",
            "$80 - $90/hr",
            "ZipRecruiter (Live 24h)",
            "https://www.ziprecruiter.com/jobs/northstar-fullstack",
            "techjobs@northstarhealth.org",
            "+1 (212) 993-4412",
            "Michael Chang",
            "Contract role for modernizing patient portal using React 19, TypeScript, Node.js microservices and PostgreSQL.",
            "TypeScript, React, Next.js, Node.js, PostgreSQL",
            92,
            "Open",
            1
        ),
        (
            "Java Spring Boot Microservices Architect",
            "Optima Financial Cloud",
            "Atlanta, GA (Remote)",
            "Contract (C2C)",
            "$90 - $100/hr",
            "CareerBuilder (Live 24h)",
            "https://www.careerbuilder.com/job/optima-java-spring",
            "resumes@optimafinancial.com",
            "+1 (404) 552-1928",
            "Amanda Cole",
            "High-priority C2C position for Senior Java 21 / Spring Boot Architect with Kafka and AWS experience.",
            "Java, Spring Boot, Microservices, Kafka, AWS",
            95,
            "Open",
            1
        )
    ]

    cursor.executemany("""
    INSERT INTO jobs (
        title, company, location, job_type, salary, 
        source, url, recruiter_email, recruiter_phone, recruiter_name, 
        description, matched_skills, match_score, status, is_24h
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, jobs)

    conn.commit()

# --- Database Helpers ---

def get_or_create_user(name, email, role="Recruiter", avatar_url=None, password="Password@123"):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE email = ?", (email,))
    user = cursor.fetchone()
    if not user:
        p_hash = generate_password_hash(password)
        cursor.execute("""
        INSERT INTO users (name, email, password_hash, role, avatar_url)
        VALUES (?, ?, ?, ?, ?)
        """, (name, email, p_hash, role, avatar_url or "https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=150&auto=format&fit=crop&q=80"))
        conn.commit()
        cursor.execute("SELECT * FROM users WHERE id = ?", (cursor.lastrowid,))
        user = cursor.fetchone()
    conn.close()
    return dict(user)

def create_user(name, email, password, role="Recruiter", avatar_url=None, region="USA"):
    """Creates a new recruiter account with hashed password."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM users WHERE email = ?", (email,))
    if cursor.fetchone():
        conn.close()
        raise ValueError(f"User with email '{email}' already exists.")

    p_hash = generate_password_hash(password)
    cursor.execute("""
    INSERT INTO users (name, email, password_hash, role, avatar_url, region)
    VALUES (?, ?, ?, ?, ?, ?)
    """, (
        name,
        email,
        p_hash,
        role,
        avatar_url or "https://images.unsplash.com/photo-1494790108377-be9c29b29330?w=150&auto=format&fit=crop&q=80",
        region if region in ("USA", "India") else "USA"
    ))
    conn.commit()
    user_id = cursor.lastrowid
    cursor.execute("SELECT id, name, email, role, avatar_url, created_at FROM users WHERE id = ?", (user_id,))
    user = cursor.fetchone()
    conn.close()
    return dict(user)

def authenticate_user(email, password):
    """Verifies user credentials and returns safe user dict if authenticated."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, email, password_hash, role, avatar_url, created_at FROM users WHERE email = ?", (email,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    user = dict(row)
    p_hash = user.get("password_hash")
    if not p_hash:
        if password in ["Admin@2026", "Password@123", "password"]:
            update_user_password(user["id"], password)
            del user["password_hash"]
            return user
        return None
    if check_password_hash(p_hash, password):
        del user["password_hash"]
        return user
    return None

def get_users():
    """Returns list of recruiters with active candidate count."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT 
        u.id, 
        u.name, 
        u.email, 
        u.role, 
        u.avatar_url, 
        u.created_at,
        u.region,
        COUNT(c.id) as consultant_count
    FROM users u
    LEFT JOIN candidates c ON c.assigned_user_id = u.id
    GROUP BY u.id, u.name, u.email, u.role, u.avatar_url, u.created_at, u.region
    ORDER BY u.id ASC
    """)
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_user_by_id(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, email, role, avatar_url, created_at FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def update_user_password(user_id, new_password):
    conn = get_db_connection()
    cursor = conn.cursor()
    p_hash = generate_password_hash(new_password)
    cursor.execute("UPDATE users SET password_hash = ? WHERE id = ?", (p_hash, user_id))
    conn.commit()
    conn.close()

def delete_user(user_id):
    """Deletes a recruiter account and reassigns their candidates to admin (id=1)."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE candidates SET assigned_user_id = 1 WHERE assigned_user_id = ?", (user_id,))
    cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()

def assign_candidate_to_recruiter(candidate_id, new_user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE candidates SET assigned_user_id = ? WHERE id = ?", (new_user_id, candidate_id))
    conn.commit()
    conn.close()

def get_dashboard_stats(user_id=None, is_admin=False):
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM jobs")
    total_jobs = cursor.fetchone()[0]

    if not is_admin and user_id:
        cursor.execute("SELECT COUNT(*) FROM candidates WHERE assigned_user_id = ?", (user_id,))
        total_candidates = cursor.fetchone()[0]

        cursor.execute("""
        SELECT COUNT(*) FROM applications a
        JOIN candidates c ON a.candidate_id = c.id
        WHERE (a.user_id = ? OR c.assigned_user_id = ?)
          AND a.stage IN ('Drafted', 'Applied', 'Screening', 'Interviewing')
        """, (user_id, user_id))
        active_pipeline = cursor.fetchone()[0]

        cursor.execute("""
        SELECT COUNT(*) FROM applications a
        JOIN candidates c ON a.candidate_id = c.id
        WHERE (a.user_id = ? OR c.assigned_user_id = ?) AND a.stage = 'Drafted'
        """, (user_id, user_id))
        drafted_count = cursor.fetchone()[0]

        cursor.execute("""
        SELECT COUNT(*) FROM applications a
        JOIN candidates c ON a.candidate_id = c.id
        WHERE (a.user_id = ? OR c.assigned_user_id = ?) AND a.stage = 'Interviewing'
        """, (user_id, user_id))
        interviews = cursor.fetchone()[0]

        cursor.execute("""
        SELECT COUNT(*) FROM applications a
        JOIN candidates c ON a.candidate_id = c.id
        WHERE (a.user_id = ? OR c.assigned_user_id = ?) AND a.stage IN ('Offer', 'Hired')
        """, (user_id, user_id))
        offers = cursor.fetchone()[0]

        cursor.execute("""
        SELECT a.stage, COUNT(*) as count 
        FROM applications a
        JOIN candidates c ON a.candidate_id = c.id
        WHERE (a.user_id = ? OR c.assigned_user_id = ?)
        GROUP BY a.stage
        """, (user_id, user_id))
        stage_rows = cursor.fetchall()
        stage_breakdown = {row["stage"]: row["count"] for row in stage_rows}
    else:
        cursor.execute("SELECT COUNT(*) FROM candidates")
        total_candidates = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM applications WHERE stage IN ('Drafted', 'Applied', 'Screening', 'Interviewing')")
        active_pipeline = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM applications WHERE stage = 'Drafted'")
        drafted_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM applications WHERE stage = 'Interviewing'")
        interviews = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM applications WHERE stage IN ('Offer', 'Hired')")
        offers = cursor.fetchone()[0]

        cursor.execute("""
        SELECT stage, COUNT(*) as count 
        FROM applications 
        GROUP BY stage
        """)
        stage_rows = cursor.fetchall()
        stage_breakdown = {row["stage"]: row["count"] for row in stage_rows}

    cursor.execute("SELECT COUNT(*) FROM jobs WHERE recruiter_email IS NOT NULL AND recruiter_email != ''")
    jobs_with_email = cursor.fetchone()[0]

    # Job sources breakdown
    cursor.execute("""
    SELECT source, COUNT(*) as count
    FROM jobs
    GROUP BY source
    """)
    source_rows = cursor.fetchall()
    source_breakdown = {row["source"]: row["count"] for row in source_rows}

    conn.close()

    return {
        "total_jobs": total_jobs,
        "total_candidates": total_candidates,
        "active_pipeline": active_pipeline,
        "drafted_count": drafted_count,
        "interviews": interviews,
        "offers": offers,
        "jobs_with_email": jobs_with_email,
        "stage_breakdown": stage_breakdown,
        "source_breakdown": source_breakdown,
    }

def get_candidates(user_id=None, is_admin=False):
    """
    Returns candidate list. 
    If user_id provided (recruiter, or admin filtering by one recruiter): only candidates assigned to that user.
    If is_admin and user_id is None: returns all candidates across the agency.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    query = """
    SELECT c.*, u.name as recruiter_name, u.email as recruiter_email
    FROM candidates c
    LEFT JOIN users u ON c.assigned_user_id = u.id
    """
    if user_id:
        # A recruiter sees only the consultants assigned to them (and an admin filtering by one
        # recruiter sees only that recruiter's). This used to also include every consultant owned by
        # user #1 (the admin) and unassigned ones, so the admin's bench showed up in every recruiter's login.
        query += " WHERE c.assigned_user_id = ?"
        cursor.execute(query + " ORDER BY c.id ASC", (user_id,))
    else:
        cursor.execute(query + " ORDER BY c.id ASC")

    rows = cursor.fetchall()
    conn.close()
    results = []
    for r in rows:
        d = dict(r)
        res_sum = d.get("resume_summary", "") or ""
        li_match = re.search(r'https?://[^\s,"]*linkedin\.com/in/([^\s,"?#]+)', res_sum)
        if li_match:
            slug = li_match.group(1).strip('/')
            d["linkedin_url"] = f"https://www.linkedin.com/in/{slug}/"
            d["profile_url"] = d["linkedin_url"]
        else:
            clean_slug = re.sub(r'[^a-zA-Z0-9]+', '-', d.get('name', 'consultant').lower()).strip('-')
            d["linkedin_url"] = f"https://www.linkedin.com/in/{clean_slug}/"
            d["profile_url"] = d["linkedin_url"]
        results.append(d)
    return results

def get_candidate_by_id(candidate_id, user_id=None, is_admin=False):
    conn = get_db_connection()
    cursor = conn.cursor()
    query = """
    SELECT c.*, u.name as recruiter_name, u.email as recruiter_email
    FROM candidates c
    LEFT JOIN users u ON c.assigned_user_id = u.id
    WHERE c.id = ?
    """
    if not is_admin and user_id:
        query += " AND c.assigned_user_id = ?"
        cursor.execute(query, (candidate_id, user_id))
    else:
        cursor.execute(query, (candidate_id,))

    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def create_candidate(name, email, phone="", title="Technical Consultant", primary_skills="", experience_years=None, target_rate="", visa_status="", status="Available", location="", country="United States", resume_filename=None, resume_path=None, resume_text=None, resume_summary="", gmail_account=None, gmail_token_path=None, assigned_user_id=1):
    if isinstance(primary_skills, (list, tuple, set)):
        primary_skills = ", ".join(str(s) for s in primary_skills)
    else:
        primary_skills = str(primary_skills or "")
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO candidates (
        name, email, phone, title, primary_skills, experience_years,
        target_rate, visa_status, status, location, country,
        resume_filename, resume_path, resume_text, resume_summary,
        gmail_account, gmail_token_path, assigned_user_id
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        name, email, phone, title, primary_skills, experience_years,
        target_rate, visa_status, status, location, country or "United States",
        resume_filename, resume_path, resume_text, resume_summary,
        gmail_account or email, gmail_token_path, assigned_user_id or 1
    ))
    conn.commit()
    cand_id = cursor.lastrowid
    conn.close()
    return cand_id

def update_candidate(candidate_id, **fields):
    if not fields:
        return
    conn = get_db_connection()
    cursor = conn.cursor()
    set_clauses = []
    params = []
    for k, v in fields.items():
        set_clauses.append(f"{k} = ?")
        params.append(v)
    params.append(candidate_id)
    query = f"UPDATE candidates SET {', '.join(set_clauses)} WHERE id = ?"
    cursor.execute(query, params)
    conn.commit()
    conn.close()

def delete_candidate(candidate_id, user_id=None, is_admin=False):
    conn = get_db_connection()
    cursor = conn.cursor()
    if not is_admin and user_id:
        cursor.execute("DELETE FROM candidates WHERE id = ? AND assigned_user_id = ?", (candidate_id, user_id))
    else:
        cursor.execute("DELETE FROM candidates WHERE id = ?", (candidate_id,))
    # Drop the stored resume file only if the candidate row really went away (the user may not
    # have been allowed to delete it).
    cursor.execute("DELETE FROM resume_files WHERE candidate_id NOT IN (SELECT id FROM candidates)")
    conn.commit()
    conn.close()


def save_resume_file(candidate_id, filename, data):
    """Stores (or replaces) the original uploaded resume file for a consultant."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM resume_files WHERE candidate_id = ?", (candidate_id,))
    if cursor.fetchone():
        cursor.execute("UPDATE resume_files SET filename = ?, data = ?, updated_at = CURRENT_TIMESTAMP WHERE candidate_id = ?",
                       (filename, bytes(data), candidate_id))
    else:
        cursor.execute("INSERT INTO resume_files (candidate_id, filename, data) VALUES (?, ?, ?)",
                       (candidate_id, filename, bytes(data)))
    conn.commit()
    conn.close()


def get_resume_file(candidate_id):
    """{'filename', 'data'} for the consultant's stored original resume file, or None."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT filename, data FROM resume_files WHERE candidate_id = ?", (candidate_id,))
    row = cursor.fetchone()
    conn.close()
    if not row or row["data"] is None:
        return None
    return {"filename": row["filename"], "data": bytes(row["data"])}

RESUMES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "resumes")


def find_resume_file(cand, persist=True):
    """(filename, bytes) of a consultant's original resume (.docx/.pdf/.txt), or None.

    Looked up in the database first, then on disk: the stored resume_path (an absolute path from
    whichever server saved it - it changes between deploys / Docker vs native), then the same file
    name in THIS server's data/resumes. Only files inside data/resumes are read. A file found on
    disk only is copied into the database (persist=True) so it survives Render's disk wipe.
    Used by the Resume Optimizer AND Gmail drafts, so both always find the same resume."""
    from werkzeug.utils import secure_filename

    rec = get_resume_file(cand["id"])
    if rec and rec.get("data"):
        return rec.get("filename") or "resume", rec["data"]
    root = os.path.realpath(RESUMES_DIR)
    paths = []
    if cand.get("resume_path"):
        paths.append(cand["resume_path"])
    for name in (cand.get("resume_path"), cand.get("resume_filename")):
        base = secure_filename(os.path.basename(name or ""))
        if base:
            paths.append(os.path.join(RESUMES_DIR, base))
    for path in paths:
        if os.path.splitext(path.lower())[1] not in (".docx", ".pdf", ".txt"):
            continue
        real = os.path.realpath(path)
        if real.startswith(root + os.sep) and os.path.isfile(real):
            try:
                with open(real, "rb") as fh:
                    data = fh.read()
            except OSError:
                continue
            if persist and data:
                try:
                    save_resume_file(cand["id"], os.path.basename(real), data)
                except Exception as ex:
                    logger.warning("Could not copy resume file into the database: %s", ex)
            return os.path.basename(real), data
    return None


def _parse_db_timestamp(value):
    """Best-effort parse of a jobs.scraped_at value into a timezone-aware UTC datetime.
    SQLite's CURRENT_TIMESTAMP yields a naive UTC string ('YYYY-MM-DD HH:MM:SS[.ffffff]');
    Postgres drivers hand back a real datetime (naive or tz-aware). Returns None (never a
    guessed time) if the value is missing or in an unrecognized format."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        s = value.strip().replace("T", " ")
        if not s:
            return None
        s = s.split("+")[0].split("Z")[0].strip()
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return None


def get_jobs(query=None, location=None, source=None, job_type=None, contract_only=False, is_24h_only=False, country=None,
             my_experience=None, include_unstated=True):
    """my_experience: the consultant's years - hides jobs that need clearly more (see job_experience).
    Every returned job carries "experience": {min, max, source, level, label}."""
    import job_experience
    conn = get_db_connection()
    cursor = conn.cursor()
    
    raw_jobs = cursor.execute("SELECT * FROM jobs ORDER BY id DESC").fetchall()
    jobs_list = [dict(j) for j in raw_jobs]
    conn.close()

    filtered = []
    q_lower = (query or "").strip().lower()
    q_terms = [t for t in q_lower.split() if len(t) > 1]
    source_clean = (source or "").lower().replace(".com", "").replace(" usa", "").strip()

    for j in jobs_list:
        title = (j.get("title") or "").lower()
        company = (j.get("company") or "").lower()
        desc = (j.get("description") or "").lower()
        loc = (j.get("location") or "").lower()
        src = (j.get("source") or "").lower()
        jtype = (j.get("job_type") or "").lower()
        skills = (j.get("matched_skills") or "").lower()

        j["experience"] = job_experience.classify(j)
        if not job_experience.fits(j["experience"], my_experience, include_unstated):
            continue

        # 0. Country / market filter - jobs scraped without an explicit country (older rows,
        # before this field existed) default to United States, matching what those scrapers
        # actually source (Dice/LinkedIn US).
        if country and country != "All":
            j_country = (j.get("country") or "United States")
            if j_country.lower() != country.lower():
                continue

        # 0b. "LIVE 24h" filter - honestly enforce it: exclude static example/seed rows outright
        # (they are never a real scrape, no matter what scraped_at says), and exclude anything
        # whose scraped_at is missing or older than 24 hours. A missing/unparseable scraped_at is
        # treated as NOT fresh (excluded), never assumed fresh.
        if is_24h_only:
            if j.get("is_seed_example"):
                continue
            scraped_dt = _parse_db_timestamp(j.get("scraped_at"))
            if scraped_dt is None or (datetime.now(timezone.utc) - scraped_dt) > timedelta(hours=24):
                continue

        # 1. Source filter (matches 'Dice' for 'Dice.com', 'LinkedIn' for 'LinkedIn (Live 24h)', etc.)
        if source and source != "All" and source_clean:
            if source_clean not in src:
                continue

        # 2. Location filter
        if location and location != "All" and location.lower() != "united states":
            if location.lower() not in loc:
                continue

        # 3. Contract filter
        if contract_only:
            if not any(k in jtype or k in desc for k in ["contract", "c2c", "corp", "1099", "temp"]):
                continue

        # 4. Relevance Scoring & Precise Title Matching
        if q_lower:
            relevance = 0

            # Tier 1: Exact phrase match in title (e.g. "data analyst" in "Senior Data Analyst") -> +100
            if q_lower in title:
                relevance += 100
            
            # Tier 2: All search words in title (e.g. "Clinical Data Business Analyst") -> +60
            elif len(q_terms) > 1 and all(term in title for term in q_terms):
                relevance += 60
            
            # Tier 3: Core role keyword in title (excluding generic terms) -> +25
            elif any(term in title for term in q_terms if term not in ["engineer", "developer", "specialist", "consultant", "lead", "senior", "junior"]):
                relevance += 25

            # Tier 4: Matched skills contains query -> +15
            if q_lower in skills or any(term in skills for term in q_terms if len(term) > 2):
                relevance += 15

            # If title has zero relation to query (e.g. "Cyber Security Engineer" when searching "data analyst"), exclude it
            if relevance == 0:
                continue

            j["_relevance"] = relevance
            j["match_score"] = min(99, max(75, 70 + (relevance // 4)))
            filtered.append(j)
        else:
            filtered.append(j)

    if q_lower:
        filtered.sort(key=lambda x: (x.get("_relevance", 0), x.get("is_24h", 0), x.get("id", 0)), reverse=True)
        for f in filtered:
            f.pop("_relevance", None)

    return filtered

def save_job_full_description(job_id, text):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE jobs SET full_description = ? WHERE id = ?", (text, job_id))
    conn.commit()
    conn.close()


def get_job_by_id(job_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def update_job_recruiter_info(job_id, email=None, phone=None, name=None, salary=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    updates = []
    params = []
    if email is not None:
        updates.append("recruiter_email = ?")
        params.append(email.strip())
    if phone is not None:
        updates.append("recruiter_phone = ?")
        params.append(phone.strip())
    if name is not None:
        updates.append("recruiter_name = ?")
        params.append(name.strip())
    if salary is not None:
        updates.append("salary = ?")
        params.append(salary.strip())

    if updates:
        params.append(job_id)
        sql = f"UPDATE jobs SET {', '.join(updates)} WHERE id = ?"
        cursor.execute(sql, params)
        conn.commit()
    conn.close()

def save_or_update_scraped_job(job_data, match_title_company=True):
    conn = get_db_connection()
    cursor = conn.cursor()

    title = job_data.get("title", "Software Engineer")
    company = job_data.get("company", "Direct Client")
    location = job_data.get("location", "United States (Remote)")
    job_type = job_data.get("job_type", "Contract (C2C)")
    salary = job_data.get("salary", "Rate on Discussion (C2C)")
    source = job_data.get("source", "Dice")
    url = job_data.get("url", "")
    recruiter_email = job_data.get("recruiter_email", "")
    recruiter_phone = job_data.get("recruiter_phone", "")
    recruiter_name = job_data.get("recruiter_name", "")
    description = job_data.get("description", "")
    matched_skills = job_data.get("matched_skills", "")
    match_score = job_data.get("match_score", 90)
    country = job_data.get("country", "United States")

    # Check if this job exists by URL or title+company
    existing = None
    if url:
        cursor.execute("SELECT id, recruiter_email FROM jobs WHERE url = ?", (url,))
        existing = cursor.fetchone()
    if not existing and match_title_company:
        # (off for requirement emails: two vendors sending the same title + client are two separate
        # submissions, each with its own recruiter - see inbox_requirements)
        cursor.execute("SELECT id, recruiter_email FROM jobs WHERE title = ? AND company = ?", (title, company))
        existing = cursor.fetchone()

    if existing:
        job_id = existing[0]
        current_email = existing[1]
        # This posting was just found again by a live scrape, so it is confirmed still live right
        # now - always refresh scraped_at so the "LIVE 24h" filter keeps showing it, not just on
        # the first time it was ever seen. Also update the email if a new one was found and the
        # old one was blank.
        if recruiter_email and not current_email:
            cursor.execute("UPDATE jobs SET recruiter_email = ?, salary = COALESCE(?, salary), scraped_at = CURRENT_TIMESTAMP, is_seed_example = 0 WHERE id = ?", (recruiter_email, salary, job_id))
        else:
            cursor.execute("UPDATE jobs SET scraped_at = CURRENT_TIMESTAMP, is_seed_example = 0 WHERE id = ?", (job_id,))
        conn.commit()
        conn.close()
        return job_id
    else:
        cursor.execute("""
        INSERT INTO jobs (
            title, company, location, job_type, salary,
            source, url, recruiter_email, recruiter_phone, recruiter_name,
            description, matched_skills, match_score, country, status, is_24h
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Open', 1)
        """, (
            title, company, location, job_type, salary,
            source, url, recruiter_email, recruiter_phone, recruiter_name,
            description, matched_skills, match_score, country
        ))
        conn.commit()
        job_id = cursor.lastrowid
        conn.close()
        return job_id

def create_application(candidate_id, job_id, user_id=1, stage="Saved", match_score=90, notes=""):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM applications WHERE job_id = ? AND candidate_id = ?", (job_id, candidate_id))
    existing = cursor.fetchone()
    if existing:
        app_id = existing[0]
        cursor.execute("UPDATE applications SET stage = ?, notes = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (stage, notes, app_id))
    else:
        cursor.execute("""
        INSERT INTO applications (job_id, candidate_id, user_id, stage, notes)
        VALUES (?, ?, ?, ?, ?)
        """, (job_id, candidate_id, user_id, stage, notes))
        app_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return app_id

def mark_job_drafted(job_id, candidate_id, draft_id, user_id=1, notes="", recruiter_email=None, resume_version_id=None,
                     outreach_message_id=None):
    """A Gmail draft was created: track it (application_tracker). The status only moves forward - a new
    draft for a job that is already at Interview stays at Interview. The draft note goes into the
    application's history, not over the recruiter's own notes."""
    import application_tracker
    conn = get_db_connection()
    try:
        app_id = application_tracker.upsert(conn, job_id, candidate_id, user_id, stage="Drafted", draft_id=draft_id,
                                            drafted_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                                            recruiter_email=recruiter_email, resume_version_id=resume_version_id,
                                            outreach_message_id=outreach_message_id)
        if notes:
            cur = conn.cursor()
            application_tracker.event(cur, app_id, user_id, "draft", notes)
            conn.commit()
    finally:
        conn.close()
    return app_id


def get_pipeline(user_id=None, is_admin=False):
    conn = get_db_connection()
    cursor = conn.cursor()
    query = """
    SELECT 
        a.id as app_id,
        a.stage,
        a.notes,
        a.draft_id,
        a.drafted_at,
        a.applied_date,
        a.updated_at,
        j.id as job_id,
        j.title as job_title,
        j.company as job_company,
        j.location as job_location,
        j.salary as job_salary,
        j.source as job_source,
        j.job_type as job_type,
        j.recruiter_email,
        j.url as job_url,
        j.match_score,
        c.id as candidate_id,
        c.name as candidate_name,
        c.title as candidate_title,
        c.email as candidate_email,
        c.phone as candidate_phone,
        c.target_rate,
        c.visa_status,
        c.assigned_user_id
    FROM applications a
    JOIN jobs j ON a.job_id = j.id
    JOIN candidates c ON a.candidate_id = c.id
    """
    if not is_admin and user_id:
        query += " WHERE (a.user_id = ? OR c.assigned_user_id = ?)"
        cursor.execute(query + " ORDER BY a.updated_at DESC", (user_id, user_id))
    elif is_admin and user_id:
        query += " WHERE (a.user_id = ? OR c.assigned_user_id = ?)"
        cursor.execute(query + " ORDER BY a.updated_at DESC", (user_id, user_id))
    else:
        cursor.execute(query + " ORDER BY a.updated_at DESC")

    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_pipeline_by_stages(user_id=None, is_admin=False):
    apps = get_pipeline(user_id=user_id, is_admin=is_admin)
    stages = {
        "Saved": [],
        "Drafted": [],
        "Applied": [],
        "Screening": [],
        "Interviewing": [],
        "Offer": [],
        "Hired": [],
        "Rejected": []
    }
    for app in apps:
        stage = app.get("stage", "Saved")
        if stage not in stages:
            stages[stage] = []
        stages[stage].append(app)
    return stages

def update_application_stage(app_id, new_stage, notes=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    if notes:
        cursor.execute("""
        UPDATE applications 
        SET stage = ?, notes = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """, (new_stage, notes, app_id))
    else:
        cursor.execute("""
        UPDATE applications 
        SET stage = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """, (new_stage, app_id))
    conn.commit()
    conn.close()

def log_activity(user_id, user_name, action, target_type, target_id, details):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO activity_logs (user_id, user_name, action, target_type, target_id, details)
    VALUES (?, ?, ?, ?, ?, ?)
    """, (user_id, user_name, action, target_type, target_id, details))
    conn.commit()
    conn.close()

def get_activity_logs(limit=25):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM activity_logs ORDER BY id DESC LIMIT ?", (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]
