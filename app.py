"""
app.py - Main Flask Application for Personal Expense Tracker
=============================================================
This file handles web routing, user authentication, session handling,
income & expense transactions, database queries, and daily summaries.
"""

from datetime import date, datetime
from functools import wraps
from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash, jsonify
)
from werkzeug.security import generate_password_hash, check_password_hash
from database import get_db_connection, init_db

# Initialize Flask application
app = Flask(__name__)
# Secret key used for signing session cookies
app.secret_key = 'personal_expense_tracker_secret_key_student_project_2026'

# Expense categories list
EXPENSE_CATEGORIES = [
    'Food',
    'Travel',
    'Shopping',
    'Education',
    'Bills',
    'Entertainment',
    'Other'
]


# -----------------------------------------------------------------------------
# Template Filters & Context Processors
# -----------------------------------------------------------------------------
@app.template_filter('currency')
def currency_filter(value):
    """Formats a numeric value as Indian Rupee (e.g., 2500 -> ₹2,500.00)."""
    try:
        val = float(value or 0)
        return f"₹{val:,.2f}"
    except (ValueError, TypeError):
        return "₹0.00"


@app.context_processor
def inject_today():
    """Makes today's date available in all Jinja templates."""
    return {
        'current_date_str': date.today().strftime('%Y-%m-%d'),
        'current_date_display': date.today().strftime('%d-%m-%Y'),
        'expense_categories': EXPENSE_CATEGORIES
    }


# -----------------------------------------------------------------------------
# Login Required Decorator (Security)
# -----------------------------------------------------------------------------
def login_required(f):
    """
    Decorator to ensure user is logged in before accessing protected routes.
    Redirects unauthenticated visitors to the login page.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in first to access this page.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


# -----------------------------------------------------------------------------
# Authentication Routes (Login, Register, Logout)
# -----------------------------------------------------------------------------
@app.route('/')
def index():
    """Root URL: Redirects to dashboard if logged in, else to login."""
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    """
    Login page:
    - Accepts username or email and password.
    - Uses password hash checking.
    - Sets session upon valid credentials.
    """
    if 'user_id' in session:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        login_input = request.form.get('login_input', '').strip()
        password = request.form.get('password', '')

        if not login_input or not password:
            flash('Please enter both username/email and password.', 'danger')
            return render_template('login.html', login_input=login_input)

        try:
            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)

            # Query user by username OR email (case-insensitive for both MySQL & SQLite)
            cursor.execute(
                "SELECT * FROM users WHERE LOWER(username) = LOWER(%s) OR LOWER(email) = LOWER(%s) LIMIT 1",
                (login_input, login_input)
            )
            user = cursor.fetchone()
            cursor.close()
            conn.close()

            # Verify password hash
            if user and check_password_hash(user['password_hash'], password):
                # Save user info in Flask session
                session['user_id'] = user['id']
                session['username'] = user['username']
                session['email'] = user['email']

                flash(f"Welcome back, {user['username']}!", 'success')
                return redirect(url_for('dashboard'))
            else:
                flash('Invalid username/email or password. Please try again.', 'danger')
        except Exception as e:
            flash(f'Database error: {str(e)}', 'danger')

    return render_template('login.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    """
    Registration page:
    - Creates a new user with secure password hashing.
    - Validates email and username uniqueness.
    """
    if 'user_id' in session:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')

        # Basic form validations
        if not username or not email or not password or not confirm_password:
            flash('All fields are required!', 'danger')
            return render_template('register.html', username=username, email=email)

        if len(username) < 3:
            flash('Username must be at least 3 characters long.', 'danger')
            return render_template('register.html', username=username, email=email)

        if len(password) < 6:
            flash('Password must be at least 6 characters long.', 'danger')
            return render_template('register.html', username=username, email=email)

        if password != confirm_password:
            flash('Passwords do not match. Please verify.', 'danger')
            return render_template('register.html', username=username, email=email)

        try:
            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)

            # Check if username or email is already taken (case-insensitive)
            cursor.execute(
                "SELECT id, username, email FROM users WHERE LOWER(username) = LOWER(%s) OR LOWER(email) = LOWER(%s) LIMIT 1",
                (username, email)
            )
            existing_user = cursor.fetchone()

            if existing_user:
                if existing_user['username'].lower() == username.lower():
                    flash('That username is already taken. Please pick another one.', 'danger')
                else:
                    flash('That email address is already registered. Please login.', 'danger')
                cursor.close()
                conn.close()
                return render_template('register.html', username=username, email=email)

            # Hash the password securely
            password_hash = generate_password_hash(password)

            # Insert new user into MySQL
            cursor.execute(
                "INSERT INTO users (username, email, password_hash) VALUES (%s, %s, %s)",
                (username, email, password_hash)
            )
            conn.commit()
            cursor.close()
            conn.close()

            flash('Registration successful! You can now log in with your credentials.', 'success')
            return redirect(url_for('login'))

        except Exception as e:
            flash(f'Registration failed: {str(e)}', 'danger')

    return render_template('register.html')


@app.route('/logout')
def logout():
    """Logs the user out and clears the session."""
    session.clear()
    flash('You have been logged out successfully.', 'info')
    return redirect(url_for('login'))


# -----------------------------------------------------------------------------
# Dashboard & Core Analytics
# -----------------------------------------------------------------------------
@app.route('/dashboard')
@login_required
def dashboard():
    """
    Dashboard:
    - Calculates Total Income, Total Expenses, Current Balance.
    - Calculates Today's Income, Today's Expenses, Today's Balance.
    - Shows Recent Transactions.
    - Shows Category Breakdown.
    """
    user_id = session['user_id']
    today_str = date.today().strftime('%Y-%m-%d')

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # 1. Total Income
    cursor.execute(
        "SELECT COALESCE(SUM(amount), 0) AS total FROM income WHERE user_id = %s",
        (user_id,)
    )
    total_income = float(cursor.fetchone()['total'])

    # 2. Total Expenses
    cursor.execute(
        "SELECT COALESCE(SUM(amount), 0) AS total FROM expenses WHERE user_id = %s",
        (user_id,)
    )
    total_expenses = float(cursor.fetchone()['total'])

    # Current Balance = Total Income - Total Expenses
    current_balance = total_income - total_expenses

    # 3. Today's Income
    cursor.execute(
        "SELECT COALESCE(SUM(amount), 0) AS total FROM income WHERE user_id = %s AND date = %s",
        (user_id, today_str)
    )
    today_income = float(cursor.fetchone()['total'])

    # 4. Today's Expenses
    cursor.execute(
        "SELECT COALESCE(SUM(amount), 0) AS total FROM expenses WHERE user_id = %s AND date = %s",
        (user_id, today_str)
    )
    today_expenses = float(cursor.fetchone()['total'])

    # Today's Balance
    today_balance = today_income - today_expenses

    # Count of transactions today (to avoid false notifications)
    cursor.execute(
        """
        SELECT 
            (SELECT COUNT(*) FROM income WHERE user_id = %s AND date = %s) +
            (SELECT COUNT(*) FROM expenses WHERE user_id = %s AND date = %s) AS today_count
        """,
        (user_id, today_str, user_id, today_str)
    )
    today_count = cursor.fetchone()['today_count']

    # 5. Recent Transactions (Union of Income and Expenses)
    cursor.execute(
        """
        (SELECT id, amount, description, 'Income' AS category, date, 'income' AS type, created_at
         FROM income WHERE user_id = %s)
        UNION ALL
        (SELECT id, amount, description, category, date, 'expense' AS type, created_at
         FROM expenses WHERE user_id = %s)
        ORDER BY date DESC, created_at DESC
        LIMIT 6
        """,
        (user_id, user_id)
    )
    recent_transactions = cursor.fetchall()

    # 6. Expense Category Breakdown for visual chart/bars
    cursor.execute(
        """
        SELECT category, SUM(amount) AS total, COUNT(*) AS count
        FROM expenses
        WHERE user_id = %s
        GROUP BY category
        ORDER BY total DESC
        """,
        (user_id,)
    )
    category_rows = cursor.fetchall()
    category_summary = [
        {'category': row['category'], 'total': float(row['total']), 'count': row['count']}
        for row in category_rows
    ]

    cursor.close()
    conn.close()

    return render_template(
        'dashboard.html',
        total_income=total_income,
        total_expenses=total_expenses,
        current_balance=current_balance,
        today_income=today_income,
        today_expenses=today_expenses,
        today_balance=today_balance,
        today_count=today_count,
        recent_transactions=recent_transactions,
        category_summary=category_summary,
        today_str=today_str
    )


# -----------------------------------------------------------------------------
# Add Income Route
# -----------------------------------------------------------------------------
@app.route('/add-income', methods=['GET', 'POST'])
@login_required
def add_income():
    """
    Add Income form:
    - Amount, Description, Date.
    - Saves directly into 'income' table.
    """
    if request.method == 'POST':
        amount = request.form.get('amount', '').strip()
        description = request.form.get('description', '').strip()
        income_date = request.form.get('date', '').strip()

        # Validation
        try:
            amount_val = float(amount)
            if amount_val <= 0:
                raise ValueError("Amount must be greater than zero.")
        except ValueError:
            flash('Please enter a valid positive amount (e.g. 2000 or 550.50).', 'danger')
            return render_template('add_income.html', description=description, date=income_date)

        if not description:
            flash('Please provide a description for the income.', 'danger')
            return render_template('add_income.html', amount=amount, date=income_date)

        if not income_date:
            income_date = date.today().strftime('%Y-%m-%d')

        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO income (user_id, amount, description, date)
                VALUES (%s, %s, %s, %s)
                """,
                (session['user_id'], amount_val, description, income_date)
            )
            conn.commit()
            cursor.close()
            conn.close()

            flash(f"Successfully added Income: ₹{amount_val:,.2f} ({description})", 'success')
            return redirect(url_for('dashboard'))
        except Exception as e:
            flash(f'Failed to save income: {str(e)}', 'danger')

    return render_template('add_income.html', default_date=date.today().strftime('%Y-%m-%d'))


# -----------------------------------------------------------------------------
# Add Expense Route
# -----------------------------------------------------------------------------
@app.route('/add-expense', methods=['GET', 'POST'])
@login_required
def add_expense():
    """
    Add Expense form:
    - Amount, Description, Category, Date.
    - Categories: Food, Travel, Shopping, Education, Bills, Entertainment, Other.
    - Saves directly into 'expenses' table.
    """
    if request.method == 'POST':
        amount = request.form.get('amount', '').strip()
        description = request.form.get('description', '').strip()
        category = request.form.get('category', '').strip()
        expense_date = request.form.get('date', '').strip()

        # Validation
        try:
            amount_val = float(amount)
            if amount_val <= 0:
                raise ValueError("Amount must be greater than zero.")
        except ValueError:
            flash('Please enter a valid positive amount (e.g. 750 or 120.00).', 'danger')
            return render_template('add_expense.html', description=description, category=category, date=expense_date)

        if not description:
            flash('Please provide a description for the expense.', 'danger')
            return render_template('add_expense.html', amount=amount, category=category, date=expense_date)

        if category not in EXPENSE_CATEGORIES:
            flash('Please select a valid expense category.', 'danger')
            return render_template('add_expense.html', amount=amount, description=description, date=expense_date)

        if not expense_date:
            expense_date = date.today().strftime('%Y-%m-%d')

        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO expenses (user_id, amount, description, category, date)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (session['user_id'], amount_val, description, category, expense_date)
            )
            conn.commit()
            cursor.close()
            conn.close()

            flash(f"Successfully added Expense: ₹{amount_val:,.2f} under {category}", 'success')
            return redirect(url_for('dashboard'))
        except Exception as e:
            flash(f'Failed to save expense: {str(e)}', 'danger')

    return render_template('add_expense.html', default_date=date.today().strftime('%Y-%m-%d'))


# -----------------------------------------------------------------------------
# Transaction History & Filtering
# -----------------------------------------------------------------------------
@app.route('/transactions')
@login_required
def transactions():
    """
    Transaction History page:
    - Lists all income and expenses.
    - Supports filters:
        * type: 'all', 'income', 'expense'
        * category: 'all', or specific category
        * date: specific 'YYYY-MM-DD'
        * month: specific 'YYYY-MM'
    """
    user_id = session['user_id']
    type_filter = request.args.get('type', 'all').strip().lower()
    category_filter = request.args.get('category', 'all').strip()
    date_filter = request.args.get('date', '').strip()
    month_filter = request.args.get('month', '').strip()

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # Build income query conditions
    income_clauses = ["user_id = %s"]
    income_params = [user_id]

    if date_filter:
        income_clauses.append("date = %s")
        income_params.append(date_filter)
    elif month_filter:
        income_clauses.append("DATE_FORMAT(date, '%%Y-%%m') = %s")
        income_params.append(month_filter)

    # Build expense query conditions
    expense_clauses = ["user_id = %s"]
    expense_params = [user_id]

    if category_filter and category_filter != 'all':
        expense_clauses.append("category = %s")
        expense_params.append(category_filter)

    if date_filter:
        expense_clauses.append("date = %s")
        expense_params.append(date_filter)
    elif month_filter:
        expense_clauses.append("DATE_FORMAT(date, '%%Y-%%m') = %s")
        expense_params.append(month_filter)

    # Determine what to query based on type_filter
    all_transactions = []

    # Income part (only if type_filter is 'all' or 'income', and category_filter is 'all' or not selecting expense categories)
    include_income = (type_filter in ['all', 'income']) and (category_filter == 'all' or category_filter == 'Income')
    include_expenses = (type_filter in ['all', 'expense'])

    if include_income and include_expenses:
        query = f"""
        (SELECT id, amount, description, 'Income' AS category, date, 'income' AS type, created_at
         FROM income WHERE {' AND '.join(income_clauses)})
        UNION ALL
        (SELECT id, amount, description, category, date, 'expense' AS type, created_at
         FROM expenses WHERE {' AND '.join(expense_clauses)})
        ORDER BY date DESC, created_at DESC
        """
        combined_params = tuple(income_params + expense_params)
        cursor.execute(query, combined_params)
        all_transactions = cursor.fetchall()
    elif include_income:
        query = f"""
        SELECT id, amount, description, 'Income' AS category, date, 'income' AS type, created_at
        FROM income WHERE {' AND '.join(income_clauses)}
        ORDER BY date DESC, created_at DESC
        """
        cursor.execute(query, tuple(income_params))
        all_transactions = cursor.fetchall()
    elif include_expenses:
        query = f"""
        SELECT id, amount, description, category, date, 'expense' AS type, created_at
        FROM expenses WHERE {' AND '.join(expense_clauses)}
        ORDER BY date DESC, created_at DESC
        """
        cursor.execute(query, tuple(expense_params))
        all_transactions = cursor.fetchall()

    # Calculate filtered totals
    filtered_income = sum(float(t['amount']) for t in all_transactions if t['type'] == 'income')
    filtered_expense = sum(float(t['amount']) for t in all_transactions if t['type'] == 'expense')
    filtered_net = filtered_income - filtered_expense

    cursor.close()
    conn.close()

    return render_template(
        'transactions.html',
        transactions=all_transactions,
        type_filter=type_filter,
        category_filter=category_filter,
        date_filter=date_filter,
        month_filter=month_filter,
        filtered_income=filtered_income,
        filtered_expense=filtered_expense,
        filtered_net=filtered_net
    )


# -----------------------------------------------------------------------------
# Edit & Delete Income
# -----------------------------------------------------------------------------
@app.route('/edit-income/<int:income_id>', methods=['GET', 'POST'])
@login_required
def edit_income(income_id):
    """Edit existing income record belonging to logged-in user."""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute(
        "SELECT * FROM income WHERE id = %s AND user_id = %s",
        (income_id, session['user_id'])
    )
    income_record = cursor.fetchone()

    if not income_record:
        cursor.close()
        conn.close()
        flash('Income record not found or permission denied.', 'danger')
        return redirect(url_for('transactions'))

    if request.method == 'POST':
        amount = request.form.get('amount', '').strip()
        description = request.form.get('description', '').strip()
        income_date = request.form.get('date', '').strip()

        try:
            amount_val = float(amount)
            if amount_val <= 0:
                raise ValueError
        except ValueError:
            flash('Please enter a valid positive amount.', 'danger')
            cursor.close()
            conn.close()
            return render_template('edit_income.html', item=income_record)

        if not description:
            flash('Description cannot be empty.', 'danger')
            cursor.close()
            conn.close()
            return render_template('edit_income.html', item=income_record)

        cursor.execute(
            """
            UPDATE income
            SET amount = %s, description = %s, date = %s
            WHERE id = %s AND user_id = %s
            """,
            (amount_val, description, income_date, income_id, session['user_id'])
        )
        conn.commit()
        cursor.close()
        conn.close()

        flash('Income record updated successfully!', 'success')
        return redirect(url_for('transactions'))

    cursor.close()
    conn.close()
    return render_template('edit_income.html', item=income_record)


@app.route('/delete-income/<int:income_id>', methods=['POST'])
@login_required
def delete_income(income_id):
    """Delete an income record belonging to logged-in user."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM income WHERE id = %s AND user_id = %s",
        (income_id, session['user_id'])
    )
    conn.commit()
    cursor.close()
    conn.close()
    flash('Income transaction deleted successfully.', 'info')
    return redirect(url_for('transactions'))


# -----------------------------------------------------------------------------
# Edit & Delete Expense
# -----------------------------------------------------------------------------
@app.route('/edit-expense/<int:expense_id>', methods=['GET', 'POST'])
@login_required
def edit_expense(expense_id):
    """Edit existing expense record belonging to logged-in user."""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute(
        "SELECT * FROM expenses WHERE id = %s AND user_id = %s",
        (expense_id, session['user_id'])
    )
    expense_record = cursor.fetchone()

    if not expense_record:
        cursor.close()
        conn.close()
        flash('Expense record not found or permission denied.', 'danger')
        return redirect(url_for('transactions'))

    if request.method == 'POST':
        amount = request.form.get('amount', '').strip()
        description = request.form.get('description', '').strip()
        category = request.form.get('category', '').strip()
        expense_date = request.form.get('date', '').strip()

        try:
            amount_val = float(amount)
            if amount_val <= 0:
                raise ValueError
        except ValueError:
            flash('Please enter a valid positive amount.', 'danger')
            cursor.close()
            conn.close()
            return render_template('edit_expense.html', item=expense_record)

        if not description:
            flash('Description cannot be empty.', 'danger')
            cursor.close()
            conn.close()
            return render_template('edit_expense.html', item=expense_record)

        if category not in EXPENSE_CATEGORIES:
            flash('Please select a valid category.', 'danger')
            cursor.close()
            conn.close()
            return render_template('edit_expense.html', item=expense_record)

        cursor.execute(
            """
            UPDATE expenses
            SET amount = %s, description = %s, category = %s, date = %s
            WHERE id = %s AND user_id = %s
            """,
            (amount_val, description, category, expense_date, expense_id, session['user_id'])
        )
        conn.commit()
        cursor.close()
        conn.close()

        flash('Expense record updated successfully!', 'success')
        return redirect(url_for('transactions'))

    cursor.close()
    conn.close()
    return render_template('edit_expense.html', item=expense_record)


@app.route('/delete-expense/<int:expense_id>', methods=['POST'])
@login_required
def delete_expense(expense_id):
    """Delete an expense record belonging to logged-in user."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM expenses WHERE id = %s AND user_id = %s",
        (expense_id, session['user_id'])
    )
    conn.commit()
    cursor.close()
    conn.close()
    flash('Expense transaction deleted successfully.', 'info')
    return redirect(url_for('transactions'))


# -----------------------------------------------------------------------------
# Daily Summary (Important Feature 7 & 8)
# -----------------------------------------------------------------------------
@app.route('/daily-summary')
@login_required
def daily_summary():
    """
    Daily Summary Page:
    - Calculates income, expense, and balance for today (or a chosen date).
    - Produces personalized message / feedback:
      e.g. 'Good job! You saved ₹1,250 today.'
    """
    user_id = session['user_id']
    query_date = request.args.get('date', date.today().strftime('%Y-%m-%d')).strip()

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # 1. Income sum for date
    cursor.execute(
        "SELECT COALESCE(SUM(amount), 0) AS total FROM income WHERE user_id = %s AND date = %s",
        (user_id, query_date)
    )
    day_income = float(cursor.fetchone()['total'])

    # 2. Expense sum for date
    cursor.execute(
        "SELECT COALESCE(SUM(amount), 0) AS total FROM expenses WHERE user_id = %s AND date = %s",
        (user_id, query_date)
    )
    day_expense = float(cursor.fetchone()['total'])

    # Day's Balance = Day's Income - Day's Expense
    day_balance = day_income - day_expense

    # Detailed list of income records for this date
    cursor.execute(
        "SELECT * FROM income WHERE user_id = %s AND date = %s ORDER BY id DESC",
        (user_id, query_date)
    )
    day_income_items = cursor.fetchall()

    # Detailed list of expense records for this date
    cursor.execute(
        "SELECT * FROM expenses WHERE user_id = %s AND date = %s ORDER BY id DESC",
        (user_id, query_date)
    )
    day_expense_items = cursor.fetchall()

    cursor.close()
    conn.close()

    total_transactions_count = len(day_income_items) + len(day_expense_items)

    # Generate custom daily summary notification message
    if total_transactions_count == 0:
        summary_message = "No transactions recorded for this day yet. Start recording your daily income and expenses!"
        summary_badge = "neutral"
    elif day_balance > 0:
        summary_message = f"🌟 Good job! You saved ₹{day_balance:,.2f} today."
        summary_badge = "success"
    elif day_balance < 0:
        summary_message = f"⚠️ Heads up: You spent ₹{abs(day_balance):,.2f} more than you earned today."
        summary_badge = "danger"
    else:
        summary_message = "⚖️ Balanced: Your income and expenses were equal today."
        summary_badge = "info"

    # Human-readable date string
    try:
        parsed_date = datetime.strptime(query_date, '%Y-%m-%d')
        formatted_date = parsed_date.strftime('%d %B %Y')
    except Exception:
        formatted_date = query_date

    return render_template(
        'daily_summary.html',
        query_date=query_date,
        formatted_date=formatted_date,
        day_income=day_income,
        day_expense=day_expense,
        day_balance=day_balance,
        day_income_items=day_income_items,
        day_expense_items=day_expense_items,
        total_transactions_count=total_transactions_count,
        summary_message=summary_message,
        summary_badge=summary_badge
    )


# -----------------------------------------------------------------------------
# Daily Notification API (Used by frontend toast / popup notification)
# -----------------------------------------------------------------------------
@app.route('/api/daily-notification')
@login_required
def api_daily_notification():
    """
    Returns today's summary in JSON format so JavaScript can render a
    daily notification popup or toast banner when user opens the app.
    Does NOT report misleading figures when no transactions exist.
    """
    user_id = session['user_id']
    today_str = date.today().strftime('%Y-%m-%d')

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute(
        "SELECT COALESCE(SUM(amount), 0) AS total, COUNT(*) AS count FROM income WHERE user_id = %s AND date = %s",
        (user_id, today_str)
    )
    income_res = cursor.fetchone()
    today_income = float(income_res['total'])
    income_count = income_res['count']

    cursor.execute(
        "SELECT COALESCE(SUM(amount), 0) AS total, COUNT(*) AS count FROM expenses WHERE user_id = %s AND date = %s",
        (user_id, today_str)
    )
    expense_res = cursor.fetchone()
    today_expense = float(expense_res['total'])
    expense_count = expense_res['count']

    cursor.close()
    conn.close()

    total_count = income_count + expense_count
    today_balance = today_income - today_expense

    has_transactions = total_count > 0

    if not has_transactions:
        message = "No transactions recorded for today yet. Add your income or expense to start tracking!"
        status = "empty"
    elif today_balance > 0:
        message = f"Good job! You saved ₹{today_balance:,.2f} today."
        status = "saved"
    elif today_balance < 0:
        message = f"Notice: You spent ₹{abs(today_balance):,.2f} more than your income today."
        status = "deficit"
    else:
        message = "You broke even today. Total income equals total expenses."
        status = "even"

    return jsonify({
        'date': today_str,
        'has_transactions': has_transactions,
        'today_income': today_income,
        'today_expense': today_expense,
        'today_balance': today_balance,
        'total_count': total_count,
        'message': message,
        'status': status
    })


# -----------------------------------------------------------------------------
# Application Entry Point
# -----------------------------------------------------------------------------
if __name__ == '__main__':
    # Automatically initialize tables if not already present
    print("[INFO] Checking MySQL database tables...")
    try:
        init_db()
    except Exception as e:
        print(f"[WARNING] Database auto-initialization check encountered: {e}")
        print("[TIP] Make sure MySQL is running and DB_CONFIG in database.py has your root password.")

    print("\n========================================================")
    print(" Personal Expense Tracker is starting!")
    print(" Open your browser and go to: http://127.0.0.1:5000")
    print("========================================================\n")
    app.run(debug=True, host='127.0.0.1', port=5000)
