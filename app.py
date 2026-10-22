import os
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import mysql.connector
from mysql.connector import Error
from dotenv import load_dotenv
from google import genai

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-key-change-me")

# Gemini client
gemini = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
MODEL = "gemini-2.0-flash"

# ---------- DB helpers ----------
def get_db():
    return mysql.connector.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "health_assistant"),
    )

def query(sql, params=(), fetchone=False, fetchall=False, commit=False):
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute(sql, params)
    if commit:
        conn.commit()
        result = cur.lastrowid
    elif fetchone:
        result = cur.fetchone()
    elif fetchall:
        result = cur.fetchall()
    else:
        result = None
    cur.close()
    conn.close()
    return result

# ---------- Auth decorators ----------
def login_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if "user_id" not in session:
            flash("Please log in first.", "error")
            return redirect(url_for("login"))
        return f(*a, **kw)
    return wrapper

def admin_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if session.get("role") != "admin":
            flash("Administrator access only.", "error")
            return redirect(url_for("chat"))
        return f(*a, **kw)
    return wrapper

# ---------- Auth routes ----------
@app.route("/")
def index():
    return redirect(url_for("chat") if "user_id" in session else url_for("login"))

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not username or not email or not password:
            flash("All fields are required.", "error")
            return redirect(url_for("register"))

        existing = query(
            "SELECT id FROM users WHERE username=%s OR email=%s",
            (username, email), fetchone=True
        )
        if existing:
            flash("Username or email already exists.", "error")
            return redirect(url_for("register"))

        pw_hash = generate_password_hash(password)
        user_id = query(
            "INSERT INTO users (username, email, password_hash) VALUES (%s,%s,%s)",
            (username, email, pw_hash), commit=True
        )
        query("INSERT INTO health_profiles (user_id) VALUES (%s)", (user_id,), commit=True)

        session["user_id"] = user_id
        session["username"] = username
        session["role"] = "user"
        flash("Account created. Please complete your health profile.", "success")
        return redirect(url_for("profile"))
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = query("SELECT * FROM users WHERE email=%s", (email,), fetchone=True)
        if not user or not check_password_hash(user["password_hash"], password):
            flash("Invalid email or password.", "error")
            return redirect(url_for("login"))
        session["user_id"] = user["id"]
        session["username"] = user["username"]
        session["role"] = user["role"]
        return redirect(url_for("chat"))
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out.", "success")
    return redirect(url_for("login"))

# ---------- Profile ----------
@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        query("""
            UPDATE health_profiles
            SET age=%s, gender=%s, known_conditions=%s,
                current_medications=%s, allergies=%s, lifestyle_factors=%s
            WHERE user_id=%s
        """, (
            request.form.get("age") or None,
            request.form.get("gender") or None,
            request.form.get("known_conditions") or None,
            request.form.get("current_medications") or None,
            request.form.get("allergies") or None,
            request.form.get("lifestyle_factors") or None,
            session["user_id"]
        ), commit=True)
        flash("Health profile saved.", "success")
        return redirect(url_for("profile"))
    p = query("SELECT * FROM health_profiles WHERE user_id=%s",
              (session["user_id"],), fetchone=True) or {}
    return render_template("profile.html", profile=p)

# ---------- Chat ----------
@app.route("/chat")
@login_required
def chat():
    conv = query("SELECT id FROM conversations WHERE user_id=%s ORDER BY id DESC LIMIT 1",
                 (session["user_id"],), fetchone=True)
    if not conv:
        new_id = query("INSERT INTO conversations (user_id) VALUES (%s)",
                       (session["user_id"],), commit=True)
        session["conversation_id"] = new_id
    else:
        session["conversation_id"] = conv["id"]
    msgs = query("SELECT role, content FROM messages WHERE conversation_id=%s ORDER BY id",
                 (session["conversation_id"],), fetchall=True)
    return render_template("chat.html", messages=msgs)

@app.route("/new_conversation")
@login_required
def new_conversation():
    new_id = query("INSERT INTO conversations (user_id) VALUES (%s)",
                   (session["user_id"],), commit=True)
    session["conversation_id"] = new_id
    return redirect(url_for("chat"))

def build_system_prompt(profile):
    def val(k):
        v = profile.get(k)
        return v if v not in (None, "", 0) else "not reported"
    return f"""You are VitaAssist, an AI-Driven Personalized Health Assistant.

You MUST personalize every response based on the user's profile below.
Do not give generic advice — reason explicitly against their conditions,
medications, allergies, and lifestyle.

USER HEALTH PROFILE:
- Age: {val('age')}
- Gender: {val('gender')}
- Known conditions: {val('known_conditions')}
- Current medications: {val('current_medications')}
- Allergies: {val('allergies')}
- Lifestyle factors: {val('lifestyle_factors')}

GUIDELINES:
- Be clear, caring, and non-alarmist.
- Where relevant, note how their existing conditions or medications
  may interact with the symptoms they describe.
- Always remind the user to consult a healthcare professional for
  serious, persistent, or worsening concerns.
- You are NOT a doctor and must not give a definitive diagnosis.
"""

@app.route("/send_message", methods=["POST"])
@login_required
def send_message():
    data = request.get_json() or {}
    text = (data.get("message") or "").strip()
    if not text:
        return jsonify({"error": "Empty message."}), 400

    conv_id = session.get("conversation_id")
    if not conv_id:
        return jsonify({"error": "No active conversation."}), 400

    # Fetch profile
    profile = query("SELECT * FROM health_profiles WHERE user_id=%s",
                    (session["user_id"],), fetchone=True) or {}

    # Fetch prior messages for context
    history_rows = query(
        "SELECT role, content FROM messages WHERE conversation_id=%s ORDER BY id",
        (conv_id,), fetchall=True
    ) or []

    system_prompt = build_system_prompt(profile)

    # Build Gemini content list: system instruction + history + new turn
    contents = []
    for m in history_rows:
        contents.append({
            "role": "user" if m["role"] == "user" else "model",
            "parts": [{"text": m["content"]}]
        })
    contents.append({"role": "user", "parts": [{"text": text}]})

    try:
        response = gemini.models.generate_content(
            model=MODEL,
            config={"system_instruction": system_prompt},
            contents=contents
        )
        reply = response.text
    except Exception as e:
        return jsonify({"error": f"AI service unavailable. Please try again. ({type(e).__name__})"}), 503

    # Save both turns
    query("INSERT INTO messages (conversation_id, role, content) VALUES (%s,'user',%s)",
          (conv_id, text), commit=True)
    query("INSERT INTO messages (conversation_id, role, content) VALUES (%s,'assistant',%s)",
          (conv_id, reply), commit=True)

    return jsonify({"reply": reply})

# ---------- History ----------
@app.route("/history")
@login_required
def history():
    rows = query("""
        SELECT c.id, c.started_at,
               (SELECT content FROM messages WHERE conversation_id=c.id ORDER BY id LIMIT 1) AS first_msg
        FROM conversations c
        WHERE c.user_id=%s
        ORDER BY c.id DESC
    """, (session["user_id"],), fetchall=True)
    return render_template("history.html", conversations=rows)

@app.route("/conversation/<int:conv_id>")
@login_required
def view_conversation(conv_id):
    owner = query("SELECT user_id FROM conversations WHERE id=%s", (conv_id,), fetchone=True)
    if not owner or owner["user_id"] != session["user_id"]:
        flash("Conversation not found.", "error")
        return redirect(url_for("history"))
    msgs = query("SELECT role, content, timestamp FROM messages WHERE conversation_id=%s ORDER BY id",
                 (conv_id,), fetchall=True)
    return render_template("conversation.html", messages=msgs)

# ---------- Admin ----------
@app.route("/admin")
@admin_required
def admin():
    users = query("""
        SELECT u.id, u.username, u.email, u.role, u.created_at,
               (SELECT COUNT(*) FROM conversations c WHERE c.user_id=u.id) AS conv_count
        FROM users u ORDER BY u.id
    """, fetchall=True)
    return render_template("admin.html", users=users)

@app.route("/admin/delete/<int:user_id>", methods=["POST"])
@admin_required
def admin_delete(user_id):
    if user_id == session["user_id"]:
        flash("You cannot delete your own account.", "error")
        return redirect(url_for("admin"))
    query("DELETE FROM users WHERE id=%s", (user_id,), commit=True)
    flash("User deleted.", "success")
    return redirect(url_for("admin"))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
