import os
import secrets
import uuid
import hmac
import logging
import math
import re
import sqlite3
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from functools import wraps
from flask import (
    Flask, render_template, request, redirect, url_for, session,
    flash, send_from_directory, abort
)
from werkzeug.utils import secure_filename
from ai_service import validate_file_content
from dotenv import load_dotenv

# Load configuration before database and integration modules read environment.
load_dotenv()
from n8n_service import build_bill_submitted_payload, send_bill_submitted

from database import (
    init_db,
    seed_data,
    get_user_by_credentials,
    get_dashboard_summary,
    get_all_stores,
    get_store_by_id,
    bill_exists_for_period,
    get_all_bills,
    get_bills_requiring_verification,
    get_bill_by_id,
    get_db_connection,
    insert_bill,
    set_bill_automation_request,
    set_bill_automation_status,
    apply_n8n_bill_result,
)

app = Flask(__name__)
development_mode = os.environ.get('DEVELOPMENT', 'true').lower() in {'1', 'true', 'yes'}
secret_key = os.environ.get('SECRET_KEY', '').strip()
if not secret_key and not development_mode:
    raise RuntimeError('SECRET_KEY must be configured when DEVELOPMENT is disabled.')
app.secret_key = secret_key or 'utilityops-local-development-key-change-before-deployment'
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=os.environ.get('SESSION_COOKIE_SECURE', 'false').lower() in {'1', 'true', 'yes'},
)


@app.errorhandler(413)
def upload_too_large(_error):
    return 'The uploaded file exceeds the 16 MB limit.', 413


@app.context_processor
def inject_csrf_token():
    def csrf_token():
        token = session.get('_csrf_token')
        if not token:
            token = secrets.token_urlsafe(32)
            session['_csrf_token'] = token
        return token
    return {'csrf_token': csrf_token}


@app.before_request
def protect_form_posts():
    if request.method == 'POST' and request.path in {'/login', '/submit-bill'}:
        expected = session.get('_csrf_token', '')
        supplied = request.form.get('csrf_token', '')
        if not expected or not hmac.compare_digest(expected, supplied):
            abort(400, description='Invalid form token')
logger = logging.getLogger(__name__)

# File upload configuration
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 MB maximum upload
ALLOWED_EXTENSIONS = {'jpg', 'jpeg', 'png', 'pdf'}
ALLOWED_UTILITIES = {'Electricity', 'Water', 'Gas'}


def parse_nonnegative_decimal(raw, label, *, money=False):
    if raw is None or not raw.strip():
        return None
    try:
        value = Decimal(raw.strip())
    except (InvalidOperation, ValueError):
        raise ValueError(f'{label} must be a valid number.')
    if not value.is_finite() or value < 0:
        raise ValueError(f'{label} must be a non-negative number.')
    if value > Decimal('1000000000000000'):
        raise ValueError(f'{label} is too large.')
    if money:
        value = value.quantize(Decimal('0.01'))
    return float(value)


def parse_billing_period(raw):
    raw = raw.strip() if raw else ''
    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', raw):
        raise ValueError('Please select a valid billing period in YYYY-MM format.')
    try:
        return datetime.strptime(raw, '%Y-%m').strftime('%Y-%m')
    except (ValueError, AttributeError):
        raise ValueError('Please select a valid billing period in YYYY-MM format.')


def parse_bill_date(raw):
    if not raw:
        return None
    try:
        return date.fromisoformat(raw.strip()).isoformat()
    except (ValueError, AttributeError):
        raise ValueError('Please enter a valid bill date.')


def allowed_file(filename):
    """Check if uploaded file has an allowed extension."""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def validate_callback_fields(payload):
    """Validate the callback schema without accepting arbitrary bill fields."""
    if not isinstance(payload, dict):
        return None

    allowed_keys = {
        'bill_id', 'request_id', 'decision', 'extracted_fields',
        'reason', 'recommended_action',
    }
    if set(payload) - allowed_keys:
        return None

    bill_id = payload.get('bill_id')
    if type(bill_id) is not int or not 1 <= bill_id <= 2**63 - 1:
        return None

    request_id = payload.get('request_id')
    if not isinstance(request_id, str) or len(request_id) > 64:
        return None
    try:
        uuid.UUID(request_id)
    except (ValueError, TypeError, AttributeError):
        return None

    decision = payload.get('decision')
    if not isinstance(decision, str) or decision not in {'NORMAL', 'VERIFY', 'ANOMALY'}:
        return None

    extracted = payload.get('extracted_fields', {})
    if not isinstance(extracted, dict):
        return None

    allowed_fields = {'bill_number', 'units', 'unit_rate', 'amount', 'bill_date'}
    if set(extracted) - allowed_fields:
        return None

    for field in ('bill_number', 'bill_date'):
        value = extracted.get(field)
        if value is not None and not isinstance(value, str):
            return None
        if isinstance(value, str) and len(value) > 200:
            return None

    bill_date = extracted.get('bill_date')
    if bill_date:
        try:
            date.fromisoformat(bill_date)
        except ValueError:
            return None

    for field in ('units', 'unit_rate', 'amount'):
        value = extracted.get(field)
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return None
            try:
                numeric_value = float(value)
            except (OverflowError, ValueError):
                return None
            if not math.isfinite(numeric_value) or not 0 <= numeric_value <= 1e15:
                return None

    for field in ('reason', 'recommended_action'):
        value = payload.get(field)
        if value is not None and (not isinstance(value, str) or len(value) > 2000):
            return None

    return {
        'bill_id': bill_id,
        'request_id': request_id,
        'decision': decision,
        'extracted_fields': extracted,
        'reason': payload.get('reason'),
        'recommended_action': payload.get('recommended_action'),
    }


# Automatically initialize database schema and seed data if not present
init_db()
seed_data()


def login_required(f):
    """Decorator to enforce authentication on protected endpoints."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            flash('Please log in to access this page.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


@app.route('/')
def index():
    """Root endpoint: route to dashboard if authenticated, else to login."""
    if session.get('logged_in'):
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    """Login endpoint verifying credentials against SQLite users table."""
    if session.get('logged_in'):
        return redirect(url_for('dashboard'))

    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        user = get_user_by_credentials(username, password)
        if user:
            session.clear()
            session['logged_in'] = True
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['role'] = user['role']
            flash('Successfully logged in!', 'success')
            return redirect(url_for('dashboard'))
        else:
            error = 'Invalid username or password. Please try again.'
            return render_template('login.html', error=error), 401

    return render_template('login.html', error=error)


@app.route('/dashboard')
@login_required
def dashboard():
    """Protected dashboard view displaying dynamic metrics and pending attention list."""
    stats = get_dashboard_summary()
    automation_bills = get_all_bills()
    stats.update({
        'normal_count': sum(1 for bill in automation_bills if bill['analysis_status'] == 'NORMAL'),
        'verify_count': sum(1 for bill in automation_bills if bill['processing_state'] in {'RECEIVED', 'PROCESSING', 'REVIEW', 'FAILED'}),
        'anomaly_count': sum(1 for bill in automation_bills if bill['analysis_status'] == 'ANOMALY'),
        'automation_queued_count': sum(1 for bill in automation_bills if bill['automation_status'] == 'queued'),
        'automation_unavailable_count': sum(
            1 for bill in automation_bills
            if bill['automation_status'] in {'unavailable', 'failed'}
        ),
    })
    role = session.get('role', 'supervisor')
    attention_bills = get_bills_requiring_verification() if role == 'admin' else []

    return render_template(
        'dashboard.html',
        username=session.get('username', 'User'),
        role=role,
        stats=stats,
        attention_bills=attention_bills
    )


@app.route('/submit-bill', methods=['GET', 'POST'])
@login_required
def submit_bill():
    """Bill submission endpoint for intake and document upload."""
    stores = get_all_stores()
    username = session.get('username', 'User')
    role = session.get('role', 'supervisor')

    if request.method == 'POST':
        # 1. Store validation
        store_id_raw = request.form.get('store_id', '').strip()
        if not store_id_raw:
            flash('Please select a valid store location.', 'error')
            return render_template('submit_bill.html', stores=stores, username=username, role=role), 400

        try:
            store_id = int(store_id_raw)
        except ValueError:
            flash('Invalid store identifier.', 'error')
            return render_template('submit_bill.html', stores=stores, username=username, role=role), 400

        store = get_store_by_id(store_id)
        if not store:
            flash('Selected store location does not exist.', 'error')
            return render_template('submit_bill.html', stores=stores, username=username, role=role), 400

        # 2. Utility type validation
        utility_type = request.form.get('utility_type', '').strip()
        if utility_type not in ALLOWED_UTILITIES:
            flash('Please select Electricity, Water, or Gas.', 'error')
            return render_template('submit_bill.html', stores=stores, username=username, role=role), 400

        # 3. Billing period validation
        billing_period = request.form.get('billing_period', '').strip()
        try:
            billing_period = parse_billing_period(billing_period)
        except ValueError as exc:
            flash(str(exc), 'error')
            return render_template('submit_bill.html', stores=stores, username=username, role=role), 400

        if bill_exists_for_period(store_id, utility_type, billing_period):
            flash('A bill for this store, utility, and billing period already exists.', 'error')
            return render_template('submit_bill.html', stores=stores, username=username, role=role), 409

        # 4. Optional fields parsing (allows NULL per Phase 2 schema)
        bill_number = request.form.get('bill_number', '').strip() or None

        try:
            units = parse_nonnegative_decimal(request.form.get('units'), 'Units consumed')
            unit_rate = parse_nonnegative_decimal(request.form.get('unit_rate'), 'Rate')
            amount = parse_nonnegative_decimal(request.form.get('amount'), 'Amount', money=True)
            bill_date = parse_bill_date(request.form.get('bill_date', '').strip())
        except ValueError as exc:
            flash(str(exc), 'error')
            return render_template('submit_bill.html', stores=stores, username=username, role=role), 400

        # 5. File upload handling
        file_path = None
        if 'bill_file' in request.files:
            file = request.files['bill_file']
            if file and file.filename != '':
                if not allowed_file(file.filename):
                    flash('Only PDF, JPG, and PNG files are supported.', 'error')
                    return render_template('submit_bill.html', stores=stores, username=username, role=role), 400

                valid_file, file_error = validate_file_content(file.stream, file.filename)
                if not valid_file:
                    flash(file_error or 'The uploaded file is not a valid PDF, JPG, or PNG.', 'error')
                    return render_template('submit_bill.html', stores=stores, username=username, role=role), 400

                clean_name = secure_filename(file.filename)
                # Generate collision-free unique filename preserving extension
                extension = clean_name.rsplit('.', 1)[1].lower() if '.' in clean_name else ''
                base_stem = clean_name.rsplit('.', 1)[0] if '.' in clean_name else clean_name
                unique_name = f"{uuid.uuid4().hex}_{base_stem}.{extension}"
                destination = os.path.join(app.config['UPLOAD_FOLDER'], unique_name)
                file.save(destination)
                file_path = f"uploads/{unique_name}"

        # 6. Insert bill into database with initial status 'VERIFY'
        try:
            bill_id = insert_bill(
                store_id=store_id, utility_type=utility_type,
                billing_period=billing_period, bill_number=bill_number,
                units=units, unit_rate=unit_rate, amount=amount,
                bill_date=bill_date, file_path=file_path, status='VERIFY'
            )
        except sqlite3.IntegrityError as exc:
            if file_path:
                try:
                    os.remove(os.path.join(BASE_DIR, file_path))
                except OSError:
                    logger.warning('Could not remove rejected bill upload')
            if 'UNIQUE constraint failed' in str(exc):
                flash('A bill for this store, utility, and billing period already exists.', 'error')
                return render_template('submit_bill.html', stores=stores, username=username, role=role), 409
            logger.exception('Bill database insert failed')
            flash('The bill could not be saved. Please try again.', 'error')
            return render_template('submit_bill.html', stores=stores, username=username, role=role), 500
        except sqlite3.Error:
            logger.exception('Bill database insert failed')
            flash('The bill could not be saved. Please try again.', 'error')
            return render_template('submit_bill.html', stores=stores, username=username, role=role), 500

        # The bill is committed by insert_bill before n8n is contacted.
        event_payload = build_bill_submitted_payload(
            bill_id=bill_id,
            store=store,
            bill_data={
                'utility_type': utility_type,
                'billing_period': billing_period,
                'bill_number': bill_number,
                'units': units,
                'unit_rate': unit_rate,
                'amount': amount,
                'bill_date': bill_date,
                'file_path': file_path,
            },
        )
        request_id = event_payload['request_id']
        request_recorded = set_bill_automation_request(bill_id, request_id)
        if not request_recorded:
            automation_result = {'status': 'unavailable'}
        else:
            try:
                automation_result = send_bill_submitted(event_payload)
            except Exception as exc:
                # Submission is already durable; automation failure must not undo it.
                logger.warning("n8n dispatch failed (%s)", type(exc).__name__)
                automation_result = {'status': 'unavailable'}
            result_status = automation_result.get('status')
            set_bill_automation_status(
                bill_id,
                request_id,
                result_status if result_status in {'queued', 'not_configured'}
                else 'unavailable',
            )

        flash(
            f'Bill #{bill_id} for store {store["store_code"]} ({utility_type} - {billing_period}) '
            f'submitted successfully and is awaiting review.',
            'success'
        )
        if automation_result.get('status') == 'not_configured':
            flash(
                'Automation is not configured. Your bill was still saved successfully for manual review.',
                'warning'
            )
        elif automation_result.get('status') != 'queued':
            flash(
                'Automation is currently unavailable. Your bill was still saved successfully for manual review.',
                'warning'
            )
        return redirect(url_for('bill_details', bill_id=bill_id))

    return render_template('submit_bill.html', stores=stores, username=username, role=role)


@app.route('/api/automation/bill-result', methods=['POST'])
def bill_result_callback():
    """Authenticate, validate, and apply n8n results to their submitted bill."""
    shared_secret = os.environ.get('N8N_WEBHOOK_SECRET') or os.environ.get('N8N_SHARED_SECRET', '')
    supplied_auth = request.headers.get('Authorization', '')
    expected_auth = f'Bearer {shared_secret}'
    if not shared_secret or not hmac.compare_digest(
        supplied_auth.encode('utf-8'), expected_auth.encode('utf-8')
    ):
        return {'error': 'Unauthorized'}, 401

    if not request.is_json:
        return {'error': 'JSON content type required'}, 415

    validated = validate_callback_fields(request.get_json(silent=True))
    if validated is None:
        return {'error': 'Invalid callback payload'}, 400

    try:
        result = apply_n8n_bill_result(
            bill_id=validated['bill_id'], request_id=validated['request_id'],
            decision=validated['decision'], extracted_fields=validated['extracted_fields'],
            reason=validated['reason'], recommended_action=validated['recommended_action'],
        )
    except sqlite3.Error:
        logger.exception('Could not apply n8n callback')
        return {'error': 'Result could not be applied'}, 500
    if result == 'not_found':
        return {'error': 'Bill not found'}, 404
    if result == 'request_mismatch':
        return {'error': 'Request ID does not match the active bill automation request'}, 409
    if result == 'invalid':
        return {'error': 'Invalid callback payload'}, 400
    if result == 'duplicate':
        return {'accepted': True, 'applied': False, 'duplicate': True}, 200
    return {'accepted': True, 'applied': True}, 200


@app.route('/bills')
@login_required
def bills():
    """Protected bills overview page listing all bills ordered newest first."""
    all_bills = get_all_bills()
    return render_template(
        'bills.html',
        bills=all_bills,
        username=session.get('username', 'User'),
        role=session.get('role', 'supervisor')
    )


@app.route('/bills/<int:bill_id>')
@login_required
def bill_details(bill_id):
    """Protected bill details page displaying statement metadata and file links."""
    bill = get_bill_by_id(bill_id)
    if not bill:
        flash(f'Bill #{bill_id} could not be found.', 'error')
        return redirect(url_for('bills'))

    conn = get_db_connection()
    try:
        alerts = conn.execute("""
            SELECT severity, reason, recommended_action, is_reviewed, created_at
            FROM alerts
            WHERE bill_id = ?
            ORDER BY id DESC
        """, (bill_id,)).fetchall()
    finally:
        conn.close()

    return render_template(
        'bill_details.html',
        bill=bill,
        alerts=alerts,
        username=session.get('username', 'User'),
        role=session.get('role', 'supervisor')
    )



@app.route('/uploads/<path:filename>')
@login_required
def download_file(filename):
    """Safe download endpoint serving uploaded bill documents to authenticated users."""
    safe_name = secure_filename(os.path.basename(filename))
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], safe_name)
    if not os.path.isfile(file_path):
        flash('Requested document was not found.', 'error')
        return redirect(url_for('bills')), 404

    return send_from_directory(app.config['UPLOAD_FOLDER'], safe_name)


@app.route('/logout')
def logout():
    """Logout endpoint clearing the user session."""
    session.clear()
    flash('You have been logged out successfully.', 'info')
    return redirect(url_for('login'))


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    debug = development_mode and os.environ.get('FLASK_DEBUG', 'false').lower() in {'1', 'true', 'yes'}
    print(f"\n=======================================================")
    print(f"  UtilityOps AI - Phase 3 Web Application")
    print(f"  Running at: http://127.0.0.1:{port}")
    print(f"=======================================================\n")
    app.run(host='127.0.0.1', port=port, debug=debug)
