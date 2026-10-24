# ---- imports ----
import os
from datetime import datetime, timedelta
from flask import Flask, render_template_string, request, redirect, url_for, jsonify, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash

# ---- app setup ----
app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret")   # used to sign session cookies
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///semesteros.db"  # db file auto-created next to app.py
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False               # silences a warning

db = SQLAlchemy(app)                    # db object shared everywhere
login_manager = LoginManager(app)       # handles current_user + @login_required
login_manager.login_view = "login"      # where to send anonymous users

# ---- models: one class = one db table ----
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    pw_hash = db.Column(db.String(255), nullable=False)   # never store raw passwords
    name = db.Column(db.String(80))

    # deleting a user wipes their courses/tasks/events too
    courses = db.relationship("Course", backref="user", cascade="all, delete-orphan")
    tasks = db.relationship("Task", backref="user", cascade="all, delete-orphan")
    events = db.relationship("Event", backref="user", cascade="all, delete-orphan")

    def set_pw(self, pw):
        self.pw_hash = generate_password_hash(pw)         # hash on the way in

    def check_pw(self, pw):
        return check_password_hash(self.pw_hash, pw)      # compare on the way out

class Course(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)  # owner
    name = db.Column(db.String(120), nullable=False)
    code = db.Column(db.String(20))                       # e.g. "CS101"
    color = db.Column(db.String(10), default="#4f46e5")   # used for calendar/badge colour

class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    course_id = db.Column(db.Integer, db.ForeignKey("course.id"))  # optional
    title = db.Column(db.String(200), nullable=False)
    due = db.Column(db.DateTime)                          # when it's due
    minutes = db.Column(db.Integer, default=60)           # estimated work time
    weight = db.Column(db.Float, default=0)               # % of final grade
    priority = db.Column(db.String(10), default="medium") # low/medium/high
    done = db.Column(db.Boolean, default=False)           # checkbox state

    course = db.relationship("Course", backref="tasks")   # task.course works

class Event(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    course_id = db.Column(db.Integer, db.ForeignKey("course.id"))
    title = db.Column(db.String(200), nullable=False)
    start = db.Column(db.DateTime, nullable=False)
    end = db.Column(db.DateTime, nullable=False)
    kind = db.Column(db.String(20), default="class")      # class/study/exam/personal

    course = db.relationship("Course", backref="events")

@login_manager.user_loader
def load_user(uid):
    return db.session.get(User, int(uid))                 # Flask-Login looks up the user by id

# ---- backward planner: split a task into sessions in free time ----
def plan_task(user_id, task, session_minutes=60):
    # can't plan without a deadline or time estimate
    if not task.due or not task.minutes or task.due <= datetime.utcnow():
        return []

    now = datetime.utcnow()

    # every event that overlaps [now, deadline] is "busy"
    busy = [
        (e.start, e.end) for e in
        Event.query.filter_by(user_id=user_id)
                   .filter(Event.end >= now, Event.start <= task.due)
                   .order_by(Event.start).all()
    ]

    # walk through the busy blocks, collect the gaps between them
    slots = []
    cursor = now
    for b_start, b_end in busy:
        if b_start - cursor >= timedelta(minutes=session_minutes):
            slots.append(cursor)                          # gap is big enough to use
        cursor = max(cursor, b_end)                       # jump past this busy block
    if task.due - cursor >= timedelta(minutes=session_minutes):
        slots.append(cursor)                              # final gap before deadline

    # how many sessions do we need?
    total = max(1, task.minutes // session_minutes)
    return [
        {"n": i + 1, "start": s, "end": s + timedelta(minutes=session_minutes)}
        for i, s in enumerate(slots[:total])
    ]

# ---- templates are just big strings ----
BASE = """
<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SemesterOS</title>
<link href="https://cdn.jsdelivr.net/npm/fullcalendar@6.1.11/index.global.min.css" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/fullcalendar@6.1.11/index.global.min.js"></script>
<style>
*{box-sizing:border-box}
body{font-family:system-ui,sans-serif;margin:0;background:#f9fafb;color:#111}
nav{display:flex;gap:1rem;padding:1rem;background:#4f46e5;color:#fff;align-items:center;flex-wrap:wrap}
nav a{color:#fff;text-decoration:none;font-weight:500}
nav .sp{flex:1}
nav .who{opacity:.85;font-size:.9rem}
main{max-width:1000px;margin:2rem auto;padding:0 1rem}
.card{background:#fff;border-radius:12px;padding:1.25rem;margin-bottom:1rem;box-shadow:0 1px 3px rgba(0,0,0,.06)}
.card.hi{border-left:4px solid #4f46e5;background:#eef2ff}
h1{margin:.2rem 0 1rem}
h2{font-size:1.05rem;margin:0 0 .75rem}
input,select,button{padding:.5rem .7rem;border-radius:8px;border:1px solid #d1d5db;font-size:1rem;margin:.2rem 0;font-family:inherit}
input[type=color]{padding:.15rem;width:48px;height:38px}
button{background:#4f46e5;color:#fff;border:none;cursor:pointer;font-weight:500}
button:hover{background:#4338ca}
.danger{background:#ef4444}.danger:hover{background:#dc2626}
ul.tasks{list-style:none;padding:0;margin:0}
ul.tasks li{padding:.55rem .2rem;border-bottom:1px solid #e5e7eb;display:flex;gap:.5rem;align-items:center;flex-wrap:wrap}
ul.tasks li:last-child{border-bottom:none}
li.done .t{text-decoration:line-through;opacity:.5}
.b{display:inline-block;padding:.1rem .5rem;border-radius:999px;font-size:.75rem;background:#eef2ff;color:#4338ca;font-weight:500}
.m{color:#6b7280;font-size:.85rem}
.row{display:flex;gap:.5rem;flex-wrap:wrap;align-items:center}
.flash{padding:.7rem 1rem;border-radius:8px;margin-bottom:1rem}
.flash.error{background:#fee2e2;color:#991b1b}
.flash.success{background:#dcfce7;color:#166534}
.empty{color:#6b7280;font-style:italic}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:1rem}
@media(max-width:700px){.grid{grid-template-columns:1fr}}
</style></head><body>
<nav>
<a href="{{ url_for('index') }}">Home</a>
<a href="{{ url_for('calendar') }}">Calendar</a>
<a href="{{ url_for('tasks_page') }}">Tasks</a>
<a href="{{ url_for('courses_page') }}">Courses</a>
<span class="sp"></span>
{% if current_user.is_authenticated %}
<span class="who">{{ current_user.name or current_user.email }}</span>
<a href="{{ url_for('logout') }}">Logout</a>
{% endif %}
</nav>
<main>
{% for cat,msg in get_flashed_messages(with_categories=true) %}
<div class="flash {{ cat }}">{{ msg }}</div>{% endfor %}
{% block content %}{% endblock %}
</main></body></html>
"""

# login + register share one template; "mode" decides which form shows
LOGIN = """
{% extends "base" %}{% block content %}
<div class="card" style="max-width:400px;margin:2rem auto">
<h1>{{ "Register" if mode=="register" else "Login" }}</h1>
<form method="POST" style="display:flex;flex-direction:column;gap:.4rem">
{% if mode=="register" %}<input name="name" placeholder="Name" style="width:100%">{% endif %}
<input name="email" type="email" placeholder="Email" required style="width:100%">
<input name="password" type="password" placeholder="Password" required style="width:100%">
<button type="submit">{{ "Create account" if mode=="register" else "Login" }}</button>
</form>
<p class="m">
{% if mode=="register" %}Have an account? <a href="{{ url_for('login') }}">Login</a>
{% else %}New? <a href="{{ url_for('register') }}">Register</a>{% endif %}
</p></div>{% endblock %}
"""

# the home page: today's events + week's deadlines + auto plan + add forms
DASH = """
{% extends "base" %}{% block content %}
<h1>Today · {{ now.strftime("%a %b %d") }}</h1>

<div class="grid">
<section class="card">
<h2>Today's schedule</h2>
{% if events %}<ul class="tasks">
{% for e in events %}<li>
<span class="b">{{ e.start.strftime("%H:%M") }}</span>
<span class="t">{{ e.title }}</span>
{% if e.course %}<span class="b" style="background:{{ e.course.color }}22;color:{{ e.course.color }}">{{ e.course.code or e.course.name }}</span>{% endif %}
<span class="m">· {{ e.kind }}</span>
</li>{% endfor %}</ul>
{% else %}<p class="empty">Nothing today.</p>{% endif %}
</section>

<section class="card">
<h2>Due in 7 days</h2>
{% if tasks %}<ul class="tasks">
{% for t in tasks %}<li>
<span class="t">{{ t.title }}</span>
<span class="m">· {{ t.due.strftime("%a %H:%M") }}</span>
{% if t.weight %}<span class="b">{{ t.weight }}%</span>{% endif %}
{% if t.course %}<span class="b" style="background:{{ t.course.color }}22;color:{{ t.course.color }}">{{ t.course.code or t.course.name }}</span>{% endif %}
</li>{% endfor %}</ul>
{% else %}<p class="empty">Nothing due.</p>{% endif %}
</section>
</div>

{% if focus and plan %}
<section class="card hi">
<h2>Plan: {{ focus.title }}</h2>
<p class="m">Auto-slotted study sessions before {{ focus.due.strftime("%b %d %H:%M") }}:</p>
<ul class="tasks">
{% for s in plan %}<li>
<span class="b">S{{ s.n }}</span>
<span class="t">{{ s.start.strftime("%a %b %d %H:%M") }} -> {{ s.end.strftime("%H:%M") }}</span>
</li>{% endfor %}</ul>
</section>{% endif %}

<section class="card">
<h2>Add task</h2>
<form method="POST" action="{{ url_for('add_task') }}" class="row">
<input name="title" placeholder="Task" required style="flex:2;min-width:160px">
<select name="course_id" style="min-width:130px">
<option value="">No course</option>
{% for c in courses %}<option value="{{ c.id }}">{{ c.code or c.name }}</option>{% endfor %}
</select>
<input name="due" type="datetime-local" required>
<input name="minutes" type="number" value="60" placeholder="min" style="width:90px">
<input name="weight" type="number" step="0.1" placeholder="% grade" style="width:100px">
<select name="priority">
<option value="low">Low</option>
<option value="medium" selected>Medium</option>
<option value="high">High</option>
</select>
<button>Add</button>
</form>
</section>

<section class="card">
<h2>Add event</h2>
<form method="POST" action="{{ url_for('add_event') }}" class="row">
<input name="title" placeholder="Event" required style="flex:2;min-width:160px">
<select name="course_id" style="min-width:130px">
<option value="">No course</option>
{% for c in courses %}<option value="{{ c.id }}">{{ c.code or c.name }}</option>{% endfor %}
</select>
<select name="kind">
<option>class</option><option>study</option><option>exam</option><option>personal</option>
</select>
<input name="start" type="datetime-local" required>
<input name="end" type="datetime-local" required>
<button>Add</button>
</form>
</section>
{% endblock %}
"""

# tasks page: two lists — pending and completed
TASKS = """
{% extends "base" %}{% block content %}
<h1>Tasks</h1>

<section class="card">
<h2>Pending</h2>
{% if pending %}<ul class="tasks">
{% for t in pending %}<li>
<form method="POST" action="{{ url_for('toggle_task', tid=t.id) }}">
<button class="b">o</button></form>
<span class="t">{{ t.title }}</span>
{% if t.due %}<span class="m">· {{ t.due.strftime("%b %d %H:%M") }}</span>{% endif %}
{% if t.weight %}<span class="b">{{ t.weight }}%</span>{% endif %}
{% if t.priority != "medium" %}<span class="b">{{ t.priority }}</span>{% endif %}
{% if t.course %}<span class="b" style="background:{{ t.course.color }}22;color:{{ t.course.color }}">{{ t.course.code or t.course.name }}</span>{% endif %}
<span style="flex:1"></span>
<form method="POST" action="{{ url_for('del_task', tid=t.id) }}">
<button class="danger">Del</button></form>
</li>{% endfor %}</ul>
{% else %}<p class="empty">No pending tasks.</p>{% endif %}
</section>

<section class="card">
<h2>Completed</h2>
{% if done %}<ul class="tasks">
{% for t in done %}<li class="done">
<form method="POST" action="{{ url_for('toggle_task', tid=t.id) }}">
<button class="b">v</button></form>
<span class="t">{{ t.title }}</span>
<span style="flex:1"></span>
<form method="POST" action="{{ url_for('del_task', tid=t.id) }}">
<button class="danger">Del</button></form>
</li>{% endfor %}</ul>
{% else %}<p class="empty">Nothing done yet.</p>{% endif %}
</section>
{% endblock %}
"""

# courses page: list + add form
COURSES = """
{% extends "base" %}{% block content %}
<h1>Courses</h1>

<section class="card">
<h2>Your courses</h2>
{% if courses %}<ul class="tasks">
{% for c in courses %}<li>
<span class="b" style="background:{{ c.color }}22;color:{{ c.color }}">{{ c.code or "-" }}</span>
<span class="t">{{ c.name }}</span>
<span class="m">· {{ c.tasks|length }} tasks · {{ c.events|length }} events</span>
<span style="flex:1"></span>
<form method="POST" action="{{ url_for('del_course', cid=c.id) }}">
<button class="danger">Del</button></form>
</li>{% endfor %}</ul>
{% else %}<p class="empty">No courses yet.</p>{% endif %}
</section>

<section class="card">
<h2>Add course</h2>
<form method="POST" action="{{ url_for('add_course') }}" class="row">
<input name="name" placeholder="Course name" required style="flex:2;min-width:200px">
<input name="code" placeholder="Code (e.g. CS101)" style="width:140px">
<input name="color" type="color" value="#4f46e5">
<button>Add</button>
</form>
</section>
{% endblock %}
"""

# calendar page: FullCalendar reads events from /events/api
CAL = """
{% extends "base" %}{% block content %}
<h1>Calendar</h1>
<section class="card"><div id="cal"></div></section>
<script>
document.addEventListener('DOMContentLoaded',()=>{
 new FullCalendar.Calendar(document.getElementById('cal'),{
   initialView:'timeGridWeek',
   headerToolbar:{left:'prev,next today',center:'title',
                  right:'dayGridMonth,timeGridWeek,timeGridDay'},
   events:'/events/api',height:'auto',
   slotMinTime:'06:00:00',slotMaxTime:'23:00:00',nowIndicator:true
 }).render();
});
</script>{% endblock %}
"""

# lookup table so render("dash") knows which string to use
TEMPLATES = {
    "base": BASE, "login": LOGIN, "dash": DASH,
    "tasks": TASKS, "courses": COURSES, "cal": CAL,
}

# render a template by name; DictLoader tricks Jinja into finding "base" for extends
def render(name, **ctx):
    from jinja2 import DictLoader
    app.jinja_loader = DictLoader(TEMPLATES)
    return render_template_string(TEMPLATES[name], **ctx)

# ---- tiny form-parsing helpers ----
def _dt(s):
    try:
        return datetime.fromisoformat(s) if s else None
    except ValueError:
        return None

def _int(v):
    try:
        return int(v) if v else None
    except (ValueError, TypeError):
        return None

def _float(v):
    try:
        return float(v) if v else 0.0
    except (ValueError, TypeError):
        return 0.0

# ---- auth routes ----
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        if User.query.filter_by(email=email).first():     # already taken?
            flash("Email already registered", "error")
            return redirect(url_for("register"))
        u = User(email=email, name=request.form.get("name", "").strip())
        u.set_pw(request.form["password"])
        db.session.add(u)
        db.session.commit()
        login_user(u)                                     # log them in right away
        return redirect(url_for("index"))
    return render("login", mode="register")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = User.query.filter_by(email=request.form["email"].strip().lower()).first()
        if u and u.check_pw(request.form["password"]):
            login_user(u)
            return redirect(url_for("index"))
        flash("Invalid credentials", "error")
    return render("login", mode="login")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))

# ---- dashboard ----
@app.route("/")
@login_required
def index():
    now = datetime.utcnow()
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)  # midnight today

    # events starting today
    events = (Event.query.filter_by(user_id=current_user.id)
              .filter(Event.start >= day0, Event.start < day0 + timedelta(days=1))
              .order_by(Event.start).all())

    # unfinished tasks due within the next 7 days
    tasks = (Task.query.filter_by(user_id=current_user.id, done=False)
             .filter(Task.due.isnot(None), Task.due <= now + timedelta(days=7))
             .order_by(Task.due).all())

    courses = Course.query.filter_by(user_id=current_user.id).order_by(Course.name).all()

    # most urgent task is what we build the plan around
    focus = tasks[0] if tasks else None
    plan = plan_task(current_user.id, focus) if focus else []

    return render("dash", now=now, events=events, tasks=tasks,
                  courses=courses, focus=focus, plan=plan)

# ---- tasks ----
@app.route("/tasks")
@login_required
def tasks_page():
    pending = (Task.query.filter_by(user_id=current_user.id, done=False)
               .order_by(Task.due.asc().nulls_last()).all())   # undated tasks last
    done = (Task.query.filter_by(user_id=current_user.id, done=True)
            .order_by(Task.id.desc()).limit(50).all())          # most recent first, cap at 50
    return render("tasks", pending=pending, done=done)

@app.route("/tasks/add", methods=["POST"])
@login_required
def add_task():
    title = request.form.get("title", "").strip()
    if not title:
        flash("Task title required", "error")
        return redirect(request.referrer or url_for("index"))
    t = Task(
        user_id=current_user.id,
        course_id=_int(request.form.get("course_id")),
        title=title,
        due=_dt(request.form.get("due")),
        minutes=_int(request.form.get("minutes")) or 60,
        weight=_float(request.form.get("weight")),
        priority=request.form.get("priority", "medium"),
    )
    db.session.add(t)
    db.session.commit()
    flash("Task added", "success")
    return redirect(request.referrer or url_for("index"))       # back to wherever you were

@app.route("/tasks/<int:tid>/toggle", methods=["POST"])
@login_required
def toggle_task(tid):
    t = Task.query.filter_by(id=tid, user_id=current_user.id).first_or_404()   # 404 if not yours
    t.done = not t.done
    db.session.commit()
    return redirect(request.referrer or url_for("tasks_page"))

@app.route("/tasks/<int:tid>/delete", methods=["POST"])
@login_required
def del_task(tid):
    t = Task.query.filter_by(id=tid, user_id=current_user.id).first_or_404()
    db.session.delete(t)
    db.session.commit()
    flash("Task deleted", "success")
    return redirect(request.referrer or url_for("tasks_page"))

# ---- courses ----
@app.route("/courses")
@login_required
def courses_page():
    courses = Course.query.filter_by(user_id=current_user.id).order_by(Course.name).all()
    return render("courses", courses=courses)

@app.route("/courses/add", methods=["POST"])
@login_required
def add_course():
    name = request.form.get("name", "").strip()
    if not name:
        flash("Course name required", "error")
        return redirect(url_for("courses_page"))
    c = Course(
        user_id=current_user.id,
        name=name,
        code=request.form.get("code", "").strip(),
        color=request.form.get("color", "#4f46e5"),
    )
    db.session.add(c)
    db.session.commit()
    flash("Course added", "success")
    return redirect(url_for("courses_page"))

@app.route("/courses/<int:cid>/delete", methods=["POST"])
@login_required
def del_course(cid):
    c = Course.query.filter_by(id=cid, user_id=current_user.id).first_or_404()
    db.session.delete(c)                                        # cascades to its tasks/events
    db.session.commit()
    flash("Course deleted", "success")
    return redirect(url_for("courses_page"))

# ---- calendar + events API ----
@app.route("/calendar")
@login_required
def calendar():
    return render("cal")

@app.route("/events/api")
@login_required
def events_api():
    out = []
    # real events
    for e in Event.query.filter_by(user_id=current_user.id).all():
        out.append({
            "id": f"e{e.id}",
            "title": e.title,
            "start": e.start.isoformat(),
            "end": e.end.isoformat(),
            "color": e.course.color if e.course else "#6366f1",
        })
    # unfinished tasks show up as red dots at their due time
    for t in (Task.query.filter_by(user_id=current_user.id, done=False)
              .filter(Task.due.isnot(None)).all()):
        out.append({
            "id": f"t{t.id}",
            "title": f"Task: {t.title}",
            "start": t.due.isoformat(),
            "color": t.course.color if t.course else "#ef4444",
        })
    return jsonify(out)                                          # FullCalendar eats this

@app.route("/events/add", methods=["POST"])
@login_required
def add_event():
    start = _dt(request.form.get("start"))
    end = _dt(request.form.get("end"))
    title = request.form.get("title", "").strip()
    if not (title and start and end) or end <= start:            # sanity check
        flash("Invalid event", "error")
        return redirect(request.referrer or url_for("index"))
    e = Event(
        user_id=current_user.id,
        course_id=_int(request.form.get("course_id")),
        title=title,
        start=start,
        end=end,
        kind=request.form.get("kind", "class"),
    )
    db.session.add(e)
    db.session.commit()
    flash("Event added", "success")
    return redirect(request.referrer or url_for("index"))

# ---- create tables + run ----
with app.app_context():
    db.create_all()                                              # safe to call every startup

if __name__ == "__main__":
    app.run(debug=True, port=5000)