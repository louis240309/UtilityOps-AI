import os
import secrets
import uuid
from functools import wraps
from flask import (
    Flask, render_template, request, redirect, url_for, session,
    flash, send_from_directory, abort
)
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

from database import (
    init_db,
    seed_data,
    get_user_by_credentials,
    get_dashboard_summary,
    get_all_stores,
    get_store_by_id,
    get_all_bills,
    get_bills_requiring_verification,
    get_bill_by_id,
    insert_bill
)

# Load environment variables if present
load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY') or secrets.token_hex(32)

# File upload configuration
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 MB maximum upload
ALLOWED_EXTENSIONS = {'jpg', 'jpeg', 'png', 'pdf'}


def allowed_file(filename):
    """Check if uploaded file has an allowed extension."""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


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
        allowed_utilities = {'Electricity', 'Water', 'Gas', 'Diesel'}
        if utility_type not in allowed_utilities:
            flash('Please select a valid utility type (Electricity, Water, Gas, or Diesel).', 'error')
            return render_template('submit_bill.html', stores=stores, username=username, role=role), 400

        # 3. Billing period validation
        billing_period = request.form.get('billing_period', '').strip()
        if not billing_period:
            flash('Billing period is required (e.g. September 2026).', 'error')
            return render_template('submit_bill.html', stores=stores, username=username, role=role), 400

        # 4. Optional fields parsing (allows NULL per Phase 2 schema)
        bill_number = request.form.get('bill_number', '').strip() or None

        units = None
        units_raw = request.form.get('units', '').strip()
        if units_raw:
            try:
                units = float(units_raw)
            except ValueError:
                flash('Units consumed must be a valid numerical value.', 'error')
                return render_template('submit_bill.html', stores=stores, username=username, role=role), 400

        unit_rate = None
        unit_rate_raw = request.form.get('unit_rate', '').strip()
        if unit_rate_raw:
            try:
                unit_rate = float(unit_rate_raw)
            except ValueError:
                flash('Unit rate must be a valid numerical value.', 'error')
                return render_template('submit_bill.html', stores=stores, username=username, role=role), 400

        amount = None
        amount_raw = request.form.get('amount', '').strip()
        if amount_raw:
            try:
                amount = float(amount_raw)
            except ValueError:
                flash('Total amount must be a valid numerical value.', 'error')
                return render_template('submit_bill.html', stores=stores, username=username, role=role), 400

        bill_date = request.form.get('bill_date', '').strip() or None

        # 5. File upload handling
        file_path = None
        if 'bill_file' in request.files:
            file = request.files['bill_file']
            if file and file.filename != '':
                if not allowed_file(file.filename):
                    flash('Invalid file extension. Allowed file formats are JPG, JPEG, PNG, and PDF.', 'error')
                    return render_template('submit_bill.html', stores=stores, username=username, role=role), 400

                clean_name = secure_filename(file.filename)
                # Generate collision-free unique filename preserving extension
                extension = clean_name.rsplit('.', 1)[1].lower() if '.' in clean_name else ''
                base_stem = clean_name.rsplit('.', 1)[0] if '.' in clean_name else clean_name
                unique_name = f"{uuid.uuid4().hex[:8]}_{base_stem}.{extension}"
                destination = os.path.join(app.config['UPLOAD_FOLDER'], unique_name)
                file.save(destination)
                file_path = f"uploads/{unique_name}"

        # 6. Insert bill into database with initial status 'VERIFY'
        bill_id = insert_bill(
            store_id=store_id,
            utility_type=utility_type,
            billing_period=billing_period,
            bill_number=bill_number,
            units=units,
            unit_rate=unit_rate,
            amount=amount,
            bill_date=bill_date,
            file_path=file_path,
            status='VERIFY'
        )

        flash(
            f'Bill #{bill_id} for store {store["store_code"]} ({utility_type} - {billing_period}) '
            f'submitted successfully with status VERIFY.',
            'success'
        )
        return redirect(url_for('bill_details', bill_id=bill_id))

    return render_template('submit_bill.html', stores=stores, username=username, role=role)


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

    return render_template(
        'bill_details.html',
        bill=bill,
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
    print(f"\n=======================================================")
    print(f"  UtilityOps AI - Phase 3 Web Application")
    print(f"  Running at: http://127.0.0.1:{port}")
    print(f"  Demo credentials:")
    print(f"    - Admin:      admin / admin123")
    print(f"    - Supervisor: supervisor / super123")
    print(f"=======================================================\n")
    app.run(host='127.0.0.1', port=port, debug=True)
