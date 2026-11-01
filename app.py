
import os
import uuid
import sqlite3
from pathlib import Path
from functools import wraps

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    send_file,
    jsonify,
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

from agents.orchestrator_agent import run_orchestration
from agents.dataset_chatbot_agent import answer_dataset_question
from agents.data_quality_agent import (
    analyze_data_quality,
    compare_data_quality,
)


# ==================================================
# CONFIGURATION
# ==================================================

app = Flask(__name__)

app.secret_key = os.environ.get(
    "FLASK_SECRET_KEY",
    "local-development-secret-change-this",
)

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_FOLDER = BASE_DIR / "uploads"
OUTPUT_FOLDER = BASE_DIR / "outputs"
GENERATED_APPS_FOLDER = BASE_DIR / "generated_apps"
DATABASE_PATH = BASE_DIR / "users.db"

for folder in (
    UPLOAD_FOLDER,
    OUTPUT_FOLDER,
    GENERATED_APPS_FOLDER,
):
    folder.mkdir(parents=True, exist_ok=True)

app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024


# ==================================================
# DATABASE
# Existing columns: id, name, email, password_hash
# ==================================================

def get_db_connection():
    connection = sqlite3.connect(str(DATABASE_PATH))
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    with get_db_connection() as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL
            )
        """)


init_db()


# ==================================================
# LOGIN REQUIRED
# ==================================================

def login_required(view_function):
    @wraps(view_function)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in first.", "warning")
            return redirect(url_for("login"))

        return view_function(*args, **kwargs)

    return wrapper


# ==================================================
# PATH HELPERS
# ==================================================

def path_is_inside(path, folder):
    """Return True only when path is inside folder."""
    try:
        resolved_path = Path(path).resolve()
        resolved_folder = Path(folder).resolve()
        return resolved_path.is_relative_to(resolved_folder)
    except (OSError, ValueError):
        return False


def locate_dashboard_file(value):
    """
    Resolve the generated dashboard path.
    Accepts an HTML file or a directory containing index.html.
    Returns None if no valid HTML dashboard exists.
    """
    if not value:
        return None

    try:
        candidate = Path(value)

        if not candidate.is_absolute():
            candidate = BASE_DIR / candidate

        candidate = candidate.resolve()

        if not path_is_inside(candidate, GENERATED_APPS_FOLDER):
            app.logger.warning(
                "Dashboard path is outside generated_apps: %s",
                candidate,
            )
            return None

        if candidate.is_dir():
            candidate = (candidate / "index.html").resolve()

        if not path_is_inside(candidate, GENERATED_APPS_FOLDER):
            return None

        if not candidate.is_file():
            return None

        if candidate.suffix.lower() != ".html":
            return None

        return candidate

    except (OSError, ValueError, TypeError):
        app.logger.exception("Could not resolve dashboard path")
        return None


# ==================================================
# HOME
# ==================================================

@app.route("/")
def home():
    if "user_id" in session:
        return redirect(url_for("dashboard"))

    return redirect(url_for("login"))


# ==================================================
# SIGNUP
# ==================================================

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        name = (
            request.form.get("name")
            or request.form.get("username")
            or ""
        ).strip()

        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not name or not email or not password:
            flash(
                "Enter your name, email and password.",
                "danger",
            )
            return redirect(url_for("signup"))

        if len(password) < 6:
            flash(
                "Password must contain at least 6 characters.",
                "danger",
            )
            return redirect(url_for("signup"))

        password_hash = generate_password_hash(password)

        try:
            with get_db_connection() as connection:
                connection.execute(
                    """
                    INSERT INTO users (name, email, password_hash)
                    VALUES (?, ?, ?)
                    """,
                    (name, email, password_hash),
                )

            flash("Signup successful. Please log in.", "success")
            return redirect(url_for("login"))

        except sqlite3.IntegrityError:
            flash("Email already registered.", "warning")

        except sqlite3.Error:
            app.logger.exception("Signup database error")
            flash("Database error during signup.", "danger")

    return render_template("login.html", signup_mode=True)


# ==================================================
# LOGIN
# ==================================================

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not email or not password:
            flash("Enter your email and password.", "danger")
            return redirect(url_for("login"))

        try:
            with get_db_connection() as connection:
                user = connection.execute(
                    """
                    SELECT id, name, email, password_hash
                    FROM users
                    WHERE email = ?
                    """,
                    (email,),
                ).fetchone()

        except sqlite3.Error:
            app.logger.exception("Login database error")
            flash("Unable to access the user database.", "danger")
            return redirect(url_for("login"))

        if user and check_password_hash(
            user["password_hash"], password
        ):
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["name"]
            session["user_name"] = user["name"]
            session["email"] = user["email"]

            return redirect(url_for("dashboard"))

        flash("Invalid email or password.", "danger")

    return render_template("login.html")


# ==================================================
# LOGOUT
# ==================================================

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ==================================================
# MAIN DASHBOARD / UPLOAD PAGE
# ==================================================

@app.route("/dashboard")
@login_required
def dashboard():
    return render_template(
        "index.html",
        username=session.get("username"),
        user_name=session.get("username"),
        email=session.get("email"),
    )


# ==================================================
# INTERACTIVE DASHBOARD
# ==================================================

@app.route("/interactive-dashboard")
@login_required
def interactive_dashboard():
    dashboard_file = session.get("dashboard_file")

    if not dashboard_file:
        flash(
            "Dashboard path was not saved. Please upload your CSV again.",
            "warning",
        )
        return redirect(url_for("dashboard"))

    dashboard_path = locate_dashboard_file(dashboard_file)

    if dashboard_path is None:
        app.logger.error(
            "Dashboard HTML not found or invalid. Saved path: %s",
            dashboard_file,
        )

        flash(
            "Generated dashboard HTML was not found. "
            "Check the generated_apps folder and orchestrator output.",
            "danger",
        )
        return redirect(url_for("dashboard"))

    return send_file(str(dashboard_path))


# ==================================================
# CHATBOT PAGE
# ==================================================

@app.route("/chatbot-page")
@login_required
def chatbot_page():
    return render_template(
        "chatbot.html",
        username=session.get("username"),
    )


# ==================================================
# CSV UPLOAD AND ORCHESTRATION
# ==================================================

@app.route("/upload", methods=["POST"])
@login_required
def upload():
    uploaded_file = request.files.get("dataset")

    if not uploaded_file or not uploaded_file.filename:
        flash("Please select a CSV file.", "danger")
        return redirect(url_for("dashboard"))

    if Path(uploaded_file.filename).suffix.lower() != ".csv":
        flash("Only CSV files are supported.", "danger")
        return redirect(url_for("dashboard"))

    safe_name = secure_filename(uploaded_file.filename)

    if not safe_name:
        flash("Invalid filename.", "danger")
        return redirect(url_for("dashboard"))

    input_path = UPLOAD_FOLDER / f"{uuid.uuid4().hex}_{safe_name}"

    # Do not retain an old dashboard link after a new upload starts.
    session.pop("dashboard_file", None)
    session.pop("cleaned_file", None)

    try:
        uploaded_file.save(str(input_path))

        # Analyze original dataset.
        before_quality = analyze_data_quality(str(input_path))

        if not before_quality.get("success"):
            raise ValueError(
                "Original dataset quality analysis failed: "
                + str(before_quality.get("error", "Unknown error"))
            )

        # Run multi-agent workflow.
        result = run_orchestration(str(input_path))

        if not isinstance(result, dict):
            raise ValueError(
                "Orchestrator returned an invalid result."
            )

        report = result.get("cleaning_report")
        analysis = result.get("analysis_report", {})

        if not isinstance(report, dict):
            raise ValueError(
                "Cleaning report is missing or invalid."
            )

        if not isinstance(analysis, dict):
            analysis = {}

        # Validate cleaned CSV output.
        output_path_value = report.get("output_path")

        if not output_path_value:
            raise ValueError(
                "Cleaning report does not contain output_path."
            )

        cleaned_path = Path(output_path_value).resolve()

        if not path_is_inside(cleaned_path, OUTPUT_FOLDER):
            raise ValueError(
                "Cleaned CSV must be inside the outputs folder."
            )

        if not cleaned_path.is_file():
            raise FileNotFoundError(
                f"Cleaned CSV not found: {cleaned_path.name}"
            )

        # Analyze cleaned dataset.
        after_quality = analyze_data_quality(str(cleaned_path))

        if not after_quality.get("success"):
            raise ValueError(
                "Cleaned dataset quality analysis failed: "
                + str(after_quality.get("error", "Unknown error"))
            )

        # Compare quality before and after cleaning.
        quality_comparison = compare_data_quality(
            str(input_path),
            str(cleaned_path),
        )

        if not quality_comparison.get("success"):
            raise ValueError(
                quality_comparison.get(
                    "error",
                    "Dataset quality comparison failed.",
                )
            )

        # Save cleaned file for download and chatbot.
        session["cleaned_file"] = str(cleaned_path)

        # Save the generated interactive dashboard.
        dashboard_value = result.get("dashboard_path")
        dashboard_file = locate_dashboard_file(dashboard_value)

        if dashboard_file:
            session["dashboard_file"] = str(dashboard_file)
            dashboard_available = True
        else:
            session.pop("dashboard_file", None)
            dashboard_available = False

            app.logger.warning(
                "No valid dashboard HTML was found. "
                "Orchestrator dashboard_path: %r",
                dashboard_value,
            )

        return render_template(
            "result.html",
            report=report,
            analysis=analysis,
            dashboard_path=(
                str(dashboard_file) if dashboard_file else None
            ),
            dashboard_available=dashboard_available,
            before_quality=before_quality,
            after_quality=after_quality,
            quality_comparison=quality_comparison,
        )

    except Exception as error:
        app.logger.exception("CSV processing failed")

        flash(
            f"Processing error: {type(error).__name__}: {error}",
            "danger",
        )

        return redirect(url_for("dashboard"))


# ==================================================
# DOWNLOAD CLEANED CSV
# ==================================================

@app.route("/download-cleaned")
@login_required
def download_cleaned():
    cleaned_file = session.get("cleaned_file")

    if not cleaned_file:
        flash(
            "Please upload and process a dataset first.",
            "warning",
        )
        return redirect(url_for("dashboard"))

    cleaned_path = Path(cleaned_file).resolve()

    if (
        not path_is_inside(cleaned_path, OUTPUT_FOLDER)
        or not cleaned_path.is_file()
    ):
        session.pop("cleaned_file", None)

        flash(
            "Cleaned file not found. Please upload again.",
            "danger",
        )
        return redirect(url_for("dashboard"))

    return send_file(
        str(cleaned_path),
        as_attachment=True,
        download_name=cleaned_path.name,
    )


# ==================================================
# DATASET CHATBOT API
# ==================================================

@app.route("/chatbot", methods=["POST"])
@login_required
def chatbot():
    cleaned_file = session.get("cleaned_file")

    if not cleaned_file:
        return jsonify({
            "success": False,
            "answer": "Please upload and process a CSV file first.",
        }), 400

    cleaned_path = Path(cleaned_file).resolve()

    if (
        not path_is_inside(cleaned_path, OUTPUT_FOLDER)
        or not cleaned_path.is_file()
    ):
        session.pop("cleaned_file", None)

        return jsonify({
            "success": False,
            "answer": "Cleaned dataset not found. Please upload again.",
        }), 404

    data = request.get_json(silent=True) or {}
    question = str(data.get("question", "")).strip()

    if not question:
        question = request.form.get("question", "").strip()

    if not question:
        return jsonify({
            "success": False,
            "answer": "Please enter a question about your dataset.",
        }), 400

    try:
        answer = answer_dataset_question(
            str(cleaned_path),
            question,
        )

        return jsonify({
            "success": True,
            "answer": str(answer),
        })

    except Exception as error:
        app.logger.exception("Dataset chatbot failed")

        return jsonify({
            "success": False,
            "answer": f"Chatbot error: {type(error).__name__}: {error}",
        }), 500


# ==================================================
# ERROR HANDLERS
# ==================================================

@app.errorhandler(413)
def file_too_large(error):
    flash(
        "File is too large. Maximum size is 25 MB.",
        "danger",
    )
    return redirect(url_for("dashboard"))


# ==================================================
# RUN APPLICATION
# ==================================================

if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=5000,
        debug=True,
    )
