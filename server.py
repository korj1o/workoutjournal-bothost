"""Standalone, dependency-free backend for the Telegram workout Mini App.

Hosted Telegram accounts each receive an isolated SQLite database and profile.
Production access validates Telegram's signed initData on every API request.
"""
import hashlib
import hmac
import json
import os
import re
import sqlite3
import sys
import time
import threading
import uuid
from contextvars import ContextVar
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
sys.path.insert(0, str(ROOT.parent))

# Bothost mounts its persistent volume at /app/data. Locally, keep using the
# existing Windows journal directory when it is available; otherwise use a
# project-local data directory. This Mini App backend does not require the
# desktop application's sourceless Python bytecode to run on the host.
_legacy_main = None
try:
    import main as _legacy_main
except Exception:
    pass

_host_data_dir = Path("/app/data")
if os.environ.get("WORKOUT_DATA_DIR"):
    DATA_DIR = Path(os.environ["WORKOUT_DATA_DIR"]).expanduser()
elif _host_data_dir.is_dir():
    DATA_DIR = _host_data_dir
elif _legacy_main is not None:
    DATA_DIR = Path(_legacy_main.APP_DIR)
else:
    DATA_DIR = ROOT.parent / "data"

LEGACY_DB_PATH = getattr(_legacy_main, "DB_PATH", DATA_DIR / "workouts.sqlite3")
HOSTED_STORAGE = os.environ.get("WORKOUT_DATA_DIR", "").strip() != ""
if not HOSTED_STORAGE and _host_data_dir.is_dir():
    HOSTED_STORAGE = True

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
OWNER_USER_ID = os.environ.get("TELEGRAM_USER_ID", "")
DEV_MODE = os.environ.get("TELEGRAM_MINIAPP_DEV", "") == "1"
DEV_USER_ID = os.environ.get("TELEGRAM_MINIAPP_DEV_USER", "1")
PORT = int(os.environ.get("PORT", os.environ.get("TELEGRAM_MINIAPP_PORT", "3000")))
APP_URL = os.environ.get("APP_URL", "").strip()
if not APP_URL:
    _domain = os.environ.get("DOMAIN", "").strip()
    if _domain:
        APP_URL = _domain if "://" in _domain else f"https://{_domain}"
if APP_URL and not APP_URL.endswith("/"):
    APP_URL += "/"
EXERCISES = {
    "\u0413\u0440\u0443\u0434\u044c": ["\u0416\u0438\u043c \u0448\u0442\u0430\u043d\u0433\u0438 \u043b\u0451\u0436\u0430", "\u0416\u0438\u043c \u0433\u0430\u043d\u0442\u0435\u043b\u0435\u0439 \u043b\u0451\u0436\u0430", "\u0416\u0438\u043c \u043d\u0430 \u043d\u0430\u043a\u043b\u043e\u043d\u043d\u043e\u0439 \u0441\u043a\u0430\u043c\u044c\u0435", "\u0421\u0432\u0435\u0434\u0435\u043d\u0438\u0435 \u0440\u0443\u043a \u0432 \u043a\u0440\u043e\u0441\u0441\u043e\u0432\u0435\u0440\u0435", "\u041e\u0442\u0436\u0438\u043c\u0430\u043d\u0438\u044f \u043d\u0430 \u0431\u0440\u0443\u0441\u044c\u044f\u0445"],
    "\u041d\u043e\u0433\u0438": ["\u041f\u0440\u0438\u0441\u0435\u0434\u0430\u043d\u0438\u044f \u0441\u043e \u0448\u0442\u0430\u043d\u0433\u043e\u0439", "\u0416\u0438\u043c \u043d\u043e\u0433\u0430\u043c\u0438 \u0432 \u0442\u0440\u0435\u043d\u0430\u0436\u0451\u0440\u0435", "\u0420\u0443\u043c\u044b\u043d\u0441\u043a\u0430\u044f \u0442\u044f\u0433\u0430", "\u0421\u0433\u0438\u0431\u0430\u043d\u0438\u0435 \u043d\u043e\u0433 \u043b\u0451\u0436\u0430", "\u0420\u0430\u0437\u0433\u0438\u0431\u0430\u043d\u0438\u0435 \u043d\u043e\u0433 \u0432 \u0442\u0440\u0435\u043d\u0430\u0436\u0451\u0440\u0435", "\u041f\u043e\u0434\u044a\u0451\u043c\u044b \u043d\u0430 \u043d\u043e\u0441\u043a\u0438 \u0441\u0442\u043e\u044f"],
    "\u0421\u043f\u0438\u043d\u0430": ["\u0422\u044f\u0433\u0430 \u0448\u0442\u0430\u043d\u0433\u0438 \u0432 \u043d\u0430\u043a\u043b\u043e\u043d\u0435", "\u0422\u044f\u0433\u0430 \u0432\u0435\u0440\u0445\u043d\u0435\u0433\u043e \u0431\u043b\u043e\u043a\u0430", "\u0422\u044f\u0433\u0430 \u0433\u043e\u0440\u0438\u0437\u043e\u043d\u0442\u0430\u043b\u044c\u043d\u043e\u0433\u043e \u0431\u043b\u043e\u043a\u0430", "\u041f\u043e\u0434\u0442\u044f\u0433\u0438\u0432\u0430\u043d\u0438\u044f", "\u0422\u044f\u0433\u0430 \u0433\u0430\u043d\u0442\u0435\u043b\u0438 \u043e\u0434\u043d\u043e\u0439 \u0440\u0443\u043a\u043e\u0439", "\u0413\u0438\u043f\u0435\u0440\u044d\u043a\u0441\u0442\u0435\u043d\u0437\u0438\u044f"],
    "\u041a\u0430\u0440\u0434\u0438\u043e": ["\u0411\u0435\u0433\u043e\u0432\u0430\u044f \u0434\u043e\u0440\u043e\u0436\u043a\u0430", "\u042d\u043b\u043b\u0438\u043f\u0442\u0438\u0447\u0435\u0441\u043a\u0438\u0439 \u0442\u0440\u0435\u043d\u0430\u0436\u0451\u0440", "\u0412\u0435\u043b\u043e\u0442\u0440\u0435\u043d\u0430\u0436\u0451\u0440", "\u0413\u0440\u0435\u0431\u043d\u043e\u0439 \u0442\u0440\u0435\u043d\u0430\u0436\u0451\u0440", "\u0421\u0442\u0435\u043f\u043f\u0435\u0440", "\u0421\u043a\u0430\u043a\u0430\u043b\u043a\u0430"],
}
OTHER_CATEGORY = "\u0414\u0440\u0443\u0433\u043e\u0435"
CARDIO_CATEGORY = "\u041a\u0430\u0440\u0434\u0438\u043e"
CATEGORIES = (*EXERCISES.keys(), OTHER_CATEGORY, CARDIO_CATEGORY)
MAX_INIT_AGE = 24 * 60 * 60
CURRENT_USER_ID = ContextVar("workout_miniapp_user_id", default=None)


def database_path():
    user_id = CURRENT_USER_ID.get()
    if user_id and not DEV_MODE and (HOSTED_STORAGE or user_id != OWNER_USER_ID):
        path = DATA_DIR / "telegram_users" / user_id / "workouts.sqlite3"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
    return Path(LEGACY_DB_PATH)


def load_config():
    user_id = CURRENT_USER_ID.get()
    if user_id and not DEV_MODE and (HOSTED_STORAGE or user_id != OWNER_USER_ID):
        path = database_path().with_name("settings.json")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
    if _legacy_main is not None and not HOSTED_STORAGE:
        return _legacy_main.load_config()
    try:
        return json.loads(Path(LEGACY_DB_PATH).with_name("settings.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_config(config):
    user_id = CURRENT_USER_ID.get()
    if user_id and not DEV_MODE and (HOSTED_STORAGE or user_id != OWNER_USER_ID):
        path = database_path().with_name("settings.json")
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
        return
    if _legacy_main is not None and not HOSTED_STORAGE:
        _legacy_main.save_config(config)
        return
    path = Path(LEGACY_DB_PATH).with_name("settings.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def ensure_schema(path=None):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = path or database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS workouts (
            id INTEGER PRIMARY KEY, date TEXT NOT NULL, exercise TEXT NOT NULL,
            weight REAL NOT NULL, sets INTEGER NOT NULL, reps INTEGER NOT NULL,
            created TEXT NOT NULL, body_weight REAL, duration_minutes REAL,
            distance_km REAL, session_start TEXT, session_end TEXT)""")
        existing = {row[1] for row in db.execute("PRAGMA table_info(workouts)")}
        for column, kind in (("body_weight", "REAL"), ("duration_minutes", "REAL"),
                             ("distance_km", "REAL"), ("session_start", "TEXT"), ("session_end", "TEXT"),
                             ("session_id", "INTEGER")):
            if column not in existing:
                db.execute(f"ALTER TABLE workouts ADD COLUMN {column} {kind}")
        db.execute("""CREATE TABLE IF NOT EXISTS workout_sets (
            workout_id INTEGER NOT NULL, set_number INTEGER NOT NULL,
            weight REAL NOT NULL, reps INTEGER NOT NULL,
            completed INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (workout_id, set_number),
            FOREIGN KEY (workout_id) REFERENCES workouts(id) ON DELETE CASCADE)""")
        db.execute("""CREATE TABLE IF NOT EXISTS training_sessions (
            id INTEGER PRIMARY KEY, date TEXT NOT NULL, session_start TEXT NOT NULL,
            session_end TEXT, body_weight REAL NOT NULL, created TEXT NOT NULL)""")
        session_columns = {row[1] for row in db.execute("PRAGMA table_info(training_sessions)")}
        if "program_id" not in session_columns:
            db.execute("ALTER TABLE training_sessions ADD COLUMN program_id TEXT")
        if "program_name" not in session_columns:
            db.execute("ALTER TABLE training_sessions ADD COLUMN program_name TEXT")
        db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS one_active_training_session
            ON training_sessions((1)) WHERE session_end IS NULL""")
        db.execute("CREATE INDEX IF NOT EXISTS workouts_session_id ON workouts(session_id)")


def load_custom_exercises():
    config = load_config()
    return config.get("custom_exercises", {})


def save_custom_exercise(category, name):
    config = load_config()
    items = config.setdefault("custom_exercises", {}).setdefault(category, [])
    if any(existing.casefold() == name.casefold() for values in config["custom_exercises"].values() for existing in values):
        raise ValueError("����� ���������� ��� ���� � ������.")
    if any(existing.casefold() == name.casefold() for values in EXERCISES.values() for existing in values):
        raise ValueError("����� ���������� ��� ���� � ������.")
    items.append(name)
    save_config(config)
    return items


def rename_custom_exercise(category, old_name, new_name):
    config = load_config()
    items = config.setdefault("custom_exercises", {}).get(category, [])
    index = next((i for i, item in enumerate(items) if item.casefold() == old_name.casefold()), None)
    if index is None:
        raise ValueError("��� ���������� �� �������.")
    if any(item.casefold() == new_name.casefold()
           for values in config["custom_exercises"].values() for item in values
           if item.casefold() != old_name.casefold()):
        raise ValueError("���������� � ����� ��������� ��� ����.")
    if any(item.casefold() == new_name.casefold()
           for values in EXERCISES.values() for item in values):
        raise ValueError("���������� ���������� � ����� ��������� ��� ����.")
    items[index] = new_name
    save_config(config)
    return items


def delete_custom_exercise(category, name):
    config = load_config()
    items = config.setdefault("custom_exercises", {}).get(category, [])
    index = next((i for i, item in enumerate(items) if item.casefold() == name.casefold()), None)
    if index is None:
        raise ValueError("��� ���������� �� �������.")
    del items[index]
    save_config(config)
    return items


def validate_init_data(raw):
    if not BOT_TOKEN:
        raise PermissionError("�� ������� �� �������� Telegram-���.")
    try:
        pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        raise PermissionError("������������ ������ Telegram Mini App.")
    if not pairs or len(dict(pairs)) != len(pairs):
        raise PermissionError("������������ ������ Telegram Mini App.")
    values = dict(pairs)
    received_hash = values.pop("hash", "")
    if not received_hash:
        raise PermissionError("Telegram �� ������� ������� Mini App.")
    check_string = "\n".join(f"{key}={value}" for key, value in sorted(values.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    calculated = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated, received_hash):
        raise PermissionError("������� Telegram ���������������.")
    try:
        auth_date = int(values["auth_date"])
        user = json.loads(values["user"])
    except (KeyError, ValueError, json.JSONDecodeError):
        raise PermissionError("������ ������������ Telegram ����������.")
    if auth_date > time.time() + 60 or time.time() - auth_date > MAX_INIT_AGE:
        raise PermissionError("���� �������� ������ Telegram ����. ������ � ������ Mini App �����.")
    return str(user.get("id", "")), user


def get_weight_history():
    """One body-weight reading per day, taken from the latest training that day."""
    with sqlite3.connect(database_path()) as db:
        sessions = db.execute("""SELECT date,body_weight,session_start,created,id
            FROM training_sessions WHERE body_weight IS NOT NULL""").fetchall()
        legacy = db.execute("""SELECT date,body_weight,session_start,created,id
            FROM workouts WHERE session_id IS NULL AND body_weight IS NOT NULL""").fetchall()
    daily = {}
    for source, rows in ((1, sessions), (0, legacy)):
        for day, weight, start, created, row_id in rows:
            if weight is None or not 0 < weight <= 1000:
                continue
            order = (start or "", created or "", source, row_id)
            if day not in daily or order > daily[day][0]:
                daily[day] = (order, weight)
    return [{"date": day, "weight": daily[day][1]}
            for day in sorted(daily)[-365:]]


def get_profile():
    return dict(load_config().get("profile", {}))


def set_profile(payload):
    name = str(payload.get("name", "")).strip()[:60]
    gender = str(payload.get("gender", ""))
    if gender not in ("Мужской", "Женский", "Другой"):
        raise ValueError("������ ���.")
    try:
        weight = float(payload.get("body_weight", ""))
        height = float(payload.get("height", ""))
    except (TypeError, ValueError):
        raise ValueError("����� ��� � ���� �������.")
    if not name or not 0 < weight <= 1000 or not 80 <= height <= 250:
        raise ValueError("��� �����������; ��� ������ ���� �� 1000 ��, ���� � �� 80 �� 250 ��.")
    config = load_config()
    config["profile"] = {"name": name, "gender": gender, "body_weight": weight, "height": height}
    save_config(config)
    return config["profile"]


def time_pair(start, end):
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", str(start)) or (end and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", str(end))):
        raise ValueError("����� ����� � ������� ��:��.")
    if end and start >= end:
        raise ValueError("����� ��������� ������ ���� ����� ������� ������.")
    return start, end


def session_dict(row):
    if row is None:
        return None
    session_id, day, start, end, body, program_id, program_name = row
    return {"id": session_id, "date": day, "session_start": start,
            "session_end": end, "body_weight": body,
            "program_id": program_id, "program_name": program_name}


def active_session():
    with sqlite3.connect(database_path()) as db:
        row = db.execute("""SELECT id,date,session_start,session_end,body_weight,program_id,program_name
            FROM training_sessions WHERE session_end IS NULL ORDER BY id DESC LIMIT 1""").fetchone()
    return session_dict(row)


def get_sessions():
    with sqlite3.connect(database_path()) as db:
        rows = db.execute("""SELECT id,date,session_start,session_end,body_weight,program_id,program_name
            FROM training_sessions ORDER BY date DESC,id DESC LIMIT 500""").fetchall()
    return [session_dict(row) for row in rows]


def session_by_id(db, session_id):
    row = db.execute("""SELECT id,date,session_start,session_end,body_weight,program_id,program_name
        FROM training_sessions WHERE id=?""", (session_id,)).fetchone()
    return session_dict(row)


def session_fields(payload):
    try:
        day = date.fromisoformat(str(payload.get("date", ""))).isoformat()
        start, _ = time_pair(str(payload.get("session_start", "")), "")
        body = float(payload.get("body_weight", ""))
    except (ValueError, TypeError):
        raise ValueError("������� ����, ����� ������ � ��� ����.")
    if not 0 < body <= 1000:
        raise ValueError("��� ���� ������ ���� �� 0 �� 1000 ��.")
    return day, start, body


def create_session(payload):
    day, start, body = session_fields(payload)
    program_id = str(payload.get("program_id", "")).strip() or None
    program_name = None
    if program_id:
        program = next((p for p in load_config().get("programs", []) if str(p.get("id")) == program_id), None)
        if program is None:
            raise ValueError("Выбранная программа не найдена. Обнови список программ.")
        program_name = str(program.get("name", ""))[:80]
    with sqlite3.connect(database_path()) as db:
        if db.execute("SELECT 1 FROM training_sessions WHERE session_end IS NULL LIMIT 1").fetchone():
            raise ValueError("������� ������� ������� ����������.")
        cur = db.execute("""INSERT INTO training_sessions(date,session_start,body_weight,created,program_id,program_name)
            VALUES(?,?,?,?,?,?)""", (day, start, body, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), program_id, program_name))
        session = session_by_id(db, cur.lastrowid)
    update_profile_weight(body)
    return session


def edit_session(session_id, payload):
    day, start, body = session_fields(payload)
    end = str(payload.get("session_end", "")).strip() or None
    if end:
        time_pair(start, end)
    with sqlite3.connect(database_path()) as db:
        old = session_by_id(db, session_id)
        if old is None:
            raise ValueError("���������� �� �������.")
        if old["session_end"] is not None and end is None:
            raise ValueError("����������� ���������� ������ ����� �������.")
        db.execute("""UPDATE training_sessions SET date=?,session_start=?,session_end=?,body_weight=? WHERE id=?""",
                   (day, start, end, body, session_id))
        db.execute("""UPDATE workouts SET date=?,session_start=?,session_end=?,body_weight=? WHERE session_id=?""",
                   (day, start, end, body, session_id))
        session = session_by_id(db, session_id)
    if old["session_end"] is None:
        update_profile_weight(body)
    return session


def allowed_exercises(category):
    custom = load_custom_exercises()
    if category == OTHER_CATEGORY:
        return list(dict.fromkeys([x for values in EXERCISES.values() for x in values]
                                  + [x for key, values in custom.items() if key != OTHER_CATEGORY for x in values]))
    return list(EXERCISES.get(category, [])) + list(custom.get(category, []))


def get_programs():
    programs = load_config().get("programs", [])
    return programs if isinstance(programs, list) else []


def save_program(payload, program_id=None):
    name = str(payload.get("name", "")).strip()[:80]
    raw_exercises = payload.get("exercises", [])
    if not name or not isinstance(raw_exercises, list) or not raw_exercises or len(raw_exercises) > 40:
        raise ValueError("Укажи название программы и добавь от 1 до 40 упражнений.")
    exercises = []
    for item in raw_exercises:
        category = str(item.get("category", ""))
        exercise = str(item.get("exercise", "")).strip()[:80]
        try:
            sets = int(item.get("sets", 3))
        except (TypeError, ValueError):
            raise ValueError("Количество подходов должно быть от 1 до 100.")
        if category not in CATEGORIES or exercise not in allowed_exercises(category):
            raise ValueError("Выбери упражнение из списка доступных.")
        if not 1 <= sets <= 100:
            raise ValueError("Количество подходов должно быть от 1 до 100.")
        exercises.append({"category": category, "exercise": exercise, "sets": sets})
    config = load_config()
    programs = config.setdefault("programs", [])
    if program_id:
        old = next((item for item in programs if str(item.get("id")) == str(program_id)), None)
        if old is None:
            raise ValueError("Программа не найдена.")
        updated = {"id": str(program_id), "name": name, "exercises": exercises}
        programs[programs.index(old)] = updated
    else:
        updated = {"id": uuid.uuid4().hex, "name": name, "exercises": exercises}
        programs.append(updated)
    save_config(config)
    return updated


def delete_program(program_id):
    config = load_config()
    programs = config.get("programs", [])
    filtered = [item for item in programs if str(item.get("id")) != str(program_id)]
    if len(filtered) == len(programs):
        raise ValueError("Программа не найдена.")
    config["programs"] = filtered
    save_config(config)


def prepare_workout(payload, require_available=True):
    try:
        day = date.fromisoformat(str(payload.get("date", ""))).isoformat()
        category = str(payload.get("category", ""))
        exercise = str(payload.get("exercise", "")).strip()
        body = float(payload.get("body_weight", ""))
        start, end = time_pair(str(payload.get("session_start", "")), str(payload.get("session_end", "")))
    except (ValueError, TypeError):
        raise ValueError("������� ����, ��� ���� � ����� ����������.")
    if not 0 < body <= 1000:
        raise ValueError("��� ���� ������ ���� �� 0 �� 1000 ��.")
    if category not in CATEGORIES or not exercise or len(exercise) > 80:
        raise ValueError("������ ���������� � ����� ��� �������� (�� 80 ��������).")
    if require_available and exercise not in allowed_exercises(category):
        raise ValueError("������ ���������� �� ������.")
    if category == CARDIO_CATEGORY:
        try:
            minutes = float(payload.get("duration_minutes", ""))
            distance_text = str(payload.get("distance_km", "")).strip()
            distance = float(distance_text) if distance_text else None
        except (TypeError, ValueError):
            raise ValueError("����� ������������ � ���������� ���������.")
        if not 0 < minutes <= 10000 or (distance is not None and not 0 <= distance <= 10000):
            raise ValueError("������������ ������ ���� ������ ����, ��������� � �� ������ ����.")
        return {"date": day, "category": category, "exercise": exercise, "stored_exercise": f"{category}: {exercise}",
                "body_weight": body, "session_start": start, "session_end": end,
                "weight": 0, "set_count": 0, "reps": 0, "duration_minutes": minutes,
                "distance_km": distance, "sets": []}

    sets = payload.get("sets", [])
    if not isinstance(sets, list) or not 1 <= len(sets) <= 100:
        raise ValueError("������ �� 1 �� 100 ��������.")
    normalized = []
    for item in sets:
        try:
            weight = float(item.get("weight", ""))
            reps = int(item.get("reps", ""))
        except (TypeError, ValueError, AttributeError):
            raise ValueError("����� ��� � ������� ��� ������� �������.")
        if not 0 <= weight <= 10000 or not 1 <= reps <= 10000:
            raise ValueError("��� �������: �� 10 000 ��; �������: �� 1 �� 10 000.")
        normalized.append((weight, reps, int(bool(item.get("completed", False)))))
    return {"date": day, "category": category, "exercise": exercise, "stored_exercise": f"{category}: {exercise}",
            "body_weight": body, "session_start": start, "session_end": end,
            "weight": normalized[0][0], "set_count": len(normalized), "reps": normalized[0][1],
            "duration_minutes": None, "distance_km": None, "sets": normalized}


def add_workout(payload):
    session_id = payload.get("session_id")
    if session_id is not None:
        try:
            session_id = int(session_id)
        except (ValueError, TypeError):
            raise ValueError("������� ���������� �� �������.")
        with sqlite3.connect(database_path()) as db:
            session = session_by_id(db, session_id)
        if session is None or session["session_end"] is not None:
            raise ValueError("���������� ��� ���������. ����� �����.")
        payload = {**payload, **{key: session[key] for key in
                   ("date", "body_weight", "session_start")}, "session_end": ""}
    record = prepare_workout(payload)
    created = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(database_path()) as db:
        if session_id is not None:
            current = session_by_id(db, session_id)
            if current is None or current["session_end"] is not None:
                raise ValueError("���������� ��� ���������. ����� �����.")
        cur = db.execute("""INSERT INTO workouts(date,exercise,weight,sets,reps,created,body_weight,duration_minutes,distance_km,session_start,session_end,session_id)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (record["date"], record["stored_exercise"], record["weight"], record["set_count"], record["reps"],
                created, record["body_weight"], record["duration_minutes"], record["distance_km"], record["session_start"], record["session_end"], session_id))
        workout_id = cur.lastrowid
        db.executemany("INSERT INTO workout_sets(workout_id,set_number,weight,reps,completed) VALUES(?,?,?,?,?)",
                       [(workout_id, i, weight, reps, done) for i, (weight, reps, done) in enumerate(record["sets"], 1)])
    if session_id is None:
        update_profile_weight(record["body_weight"])
    return workout_id


def update_workout(workout_id, payload):
    with sqlite3.connect(database_path()) as db:
        row = db.execute("SELECT session_id FROM workouts WHERE id=?", (workout_id,)).fetchone()
        if row is None:
            raise ValueError("���������� ��� ������� ��� �� �������.")
        session_id = row[0]
        if session_id is not None:
            session = session_by_id(db, session_id)
            if session is None:
                raise ValueError("���������� �� �������.")
            payload = {**payload, **{key: session[key] for key in
                       ("date", "body_weight", "session_start")}, "session_end": session["session_end"] or ""}
    record = prepare_workout(payload, require_available=False)
    with sqlite3.connect(database_path()) as db:
        cur = db.execute("""UPDATE workouts SET date=?,exercise=?,weight=?,sets=?,reps=?,body_weight=?,duration_minutes=?,distance_km=?,session_start=?,session_end=?
            WHERE id=?""", (record["date"], record["stored_exercise"], record["weight"], record["set_count"], record["reps"],
                record["body_weight"], record["duration_minutes"], record["distance_km"], record["session_start"], record["session_end"], workout_id))
        if cur.rowcount == 0:
            raise ValueError("���������� ��� ������� ��� �� �������.")
        db.execute("DELETE FROM workout_sets WHERE workout_id=?", (workout_id,))
        db.executemany("INSERT INTO workout_sets(workout_id,set_number,weight,reps,completed) VALUES(?,?,?,?,?)",
                       [(workout_id, i, weight, reps, done) for i, (weight, reps, done) in enumerate(record["sets"], 1)])
        latest_weight = db.execute("SELECT body_weight FROM workouts WHERE body_weight IS NOT NULL ORDER BY date DESC,id DESC LIMIT 1").fetchone()
    if session_id is None and latest_weight:
        update_profile_weight(latest_weight[0])
    return workout_id


def delete_workout(workout_id):
    with sqlite3.connect(database_path()) as db:
        row = db.execute("SELECT session_id FROM workouts WHERE id=?", (workout_id,)).fetchone()
        if row is None:
            raise ValueError("���������� ��� ������� ��� �� �������.")
        cur = db.execute("DELETE FROM workouts WHERE id=?", (workout_id,))
        if cur.rowcount == 0:
            raise ValueError("���������� ��� ������� ��� �� �������.")
        db.execute("DELETE FROM workout_sets WHERE workout_id=?", (workout_id,))
        latest_weight = db.execute("SELECT body_weight FROM workouts WHERE body_weight IS NOT NULL ORDER BY date DESC,id DESC LIMIT 1").fetchone()
    if row[0] is None and latest_weight:
        update_profile_weight(latest_weight[0])


def update_profile_weight(weight):
    config = load_config()
    if config.get("profile"):
        weights = get_weight_history()
        config["profile"]["body_weight"] = weights[-1]["weight"] if weights else weight
        save_config(config)


def get_history():
    with sqlite3.connect(database_path()) as db:
        rows = db.execute("""SELECT id,date,exercise,weight,sets,reps,body_weight,duration_minutes,distance_km,session_start,session_end,session_id
            FROM workouts ORDER BY date DESC,id DESC LIMIT 500""").fetchall()
        set_map = {}
        for row in db.execute("""SELECT s.workout_id,s.set_number,s.weight,s.reps,s.completed
            FROM workout_sets s JOIN workouts w ON w.id=s.workout_id ORDER BY s.set_number"""):
            set_map.setdefault(row[0], []).append({"number": row[1], "weight": row[2], "reps": row[3], "completed": bool(row[4])})
    entries = []
    for row in rows:
        workout_id, day, exercise, weight, count, reps, body, minutes, distance, start, end, session_id = row
        entries.append({"id": workout_id, "date": day, "exercise": exercise, "weight": weight,
                        "sets": set_map.get(workout_id, []), "set_count": count, "reps": reps,
                        "body_weight": body, "duration_minutes": minutes, "distance_km": distance,
                         "session_start": start, "session_end": end, "session_id": session_id})
    return entries


class Handler(BaseHTTPRequestHandler):
    server_version = "WorkoutMiniApp/1.0"

    def log_message(self, _format, *_args):
        return

    def send_json(self, status, value):
        raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(raw)

    def user(self):
        if DEV_MODE:
            user_id = self.headers.get("X-Dev-User-Id", "")
            if user_id == DEV_USER_ID:
                return user_id, {"id": int(user_id), "first_name": "������"}
            raise PermissionError("��������� ����� ��� X-Dev-User-Id.")
        raw = self.headers.get("X-Telegram-Init-Data", "")
        user_id, user = validate_init_data(raw)
        if not re.fullmatch(r"\d+", user_id):
            raise PermissionError("�� ������� ���������� Telegram-������������.")
        CURRENT_USER_ID.set(user_id)
        ensure_schema(database_path())
        return user_id, user

    def body(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 1_000_000:
                raise ValueError
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError
            return payload
        except (ValueError, json.JSONDecodeError):
            raise ValueError("�� ������� ��������� ������ �������.")

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/meta":
            return self.send_json(200, {"dev_mode": DEV_MODE, "configured": DEV_MODE or bool(BOT_TOKEN)})
        if parsed.path.startswith("/api/"):
            try:
                _user_id, user = self.user()
                if parsed.path == "/api/bootstrap":
                    custom = load_custom_exercises()
                    exercises = {key: list(values) + list(custom.get(key, [])) for key, values in EXERCISES.items()}
                    exercises[OTHER_CATEGORY] = allowed_exercises(OTHER_CATEGORY)
                    return self.send_json(200, {"user": user, "profile": get_profile(), "exercises": exercises,
                                                "history": get_history(), "sessions": get_sessions(),
                                                "weight_history": get_weight_history(),
                                                "programs": get_programs(),
                                                "active_session": active_session(),
                                                "settings": load_config().get("settings", {})})
                if parsed.path == "/api/history":
                    return self.send_json(200, {"history": get_history()})
                return self.send_json(404, {"error": "����������� �����."})
            except PermissionError as exc:
                return self.send_json(401, {"error": str(exc)})
            except Exception:
                return self.send_json(500, {"error": "�� ������� ��������� ������ ��������."})
        relative = "index.html" if parsed.path in ("/", "") else parsed.path.lstrip("/")
        file_path = (WEB_ROOT / relative).resolve()
        if WEB_ROOT.resolve() not in file_path.parents and file_path != WEB_ROOT / "index.html":
            return self.send_error(404)
        if not file_path.is_file():
            return self.send_error(404)
        content_type = "text/html; charset=utf-8" if file_path.suffix == ".html" else "application/octet-stream"
        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline' https://telegram.org; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors https://web.telegram.org https://*.telegram.org")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if not urlparse(self.path).path.startswith("/api/"):
            return self.send_json(404, {"error": "����������� �����."})
        try:
            self.user()
            payload = self.body()
            path = urlparse(self.path).path
            if path == "/api/workouts":
                workout_id = add_workout(payload)
                return self.send_json(201, {"id": workout_id, "history": get_history()})
            if path == "/api/sessions":
                return self.send_json(201, {"session": create_session(payload)})
            if path == "/api/profile":
                profile = set_profile(payload)
                return self.send_json(200, {"profile": profile})
            if path == "/api/exercises":
                category = str(payload.get("category", ""))
                name = str(payload.get("name", "")).strip()[:80]
                if category not in CATEGORIES or not name:
                    raise ValueError("����� ������ � �������� ����������.")
                exercises = save_custom_exercise(category, name)
                return self.send_json(201, {"category": category, "exercises": exercises})
            if path == "/api/programs":
                return self.send_json(201, {"program": save_program(payload)})
            return self.send_json(404, {"error": "����������� �����."})
        except PermissionError as exc:
            return self.send_json(401, {"error": str(exc)})
        except ValueError as exc:
            return self.send_json(400, {"error": str(exc)})
        except Exception:
            return self.send_json(500, {"error": "�� ������� ��������� ������."})

    def do_PUT(self):
        if not urlparse(self.path).path.startswith("/api/"):
            return self.send_json(404, {"error": "����������� �����."})
        try:
            self.user()
            path = urlparse(self.path).path
            payload = self.body()
            match = re.fullmatch(r"/api/workouts/(\d+)", path)
            if match:
                workout_id = update_workout(int(match.group(1)), payload)
                return self.send_json(200, {"id": workout_id, "history": get_history()})
            match = re.fullmatch(r"/api/sessions/(\d+)", path)
            if match:
                return self.send_json(200, {"session": edit_session(int(match.group(1)), payload),
                                            "history": get_history()})
            if path == "/api/exercises":
                category = str(payload.get("category", ""))
                old_name = str(payload.get("old_name", "")).strip()
                name = str(payload.get("name", "")).strip()[:80]
                if category not in CATEGORIES or not old_name or not name:
                    raise ValueError("����� ������ � ����� �������� ����������.")
                exercises = rename_custom_exercise(category, old_name, name)
                return self.send_json(200, {"category": category, "exercises": exercises})
            match = re.fullmatch(r"/api/programs/([a-f0-9]+)", path)
            if match:
                return self.send_json(200, {"program": save_program(payload, match.group(1))})
            return self.send_json(404, {"error": "����������� �����."})
        except PermissionError as exc:
            return self.send_json(401, {"error": str(exc)})
        except ValueError as exc:
            return self.send_json(400, {"error": str(exc)})
        except Exception:
            return self.send_json(500, {"error": "�� ������� �������� ������."})

    def do_DELETE(self):
        if not urlparse(self.path).path.startswith("/api/"):
            return self.send_json(404, {"error": "����������� �����."})
        try:
            self.user()
            path = urlparse(self.path).path
            match = re.fullmatch(r"/api/workouts/(\d+)", path)
            if match:
                delete_workout(int(match.group(1)))
                return self.send_json(200, {"history": get_history()})
            if path == "/api/exercises":
                payload = self.body()
                category = str(payload.get("category", ""))
                name = str(payload.get("name", "")).strip()
                if category not in CATEGORIES or not name:
                    raise ValueError("����� ������ � ���������� ��� ��������.")
                exercises = delete_custom_exercise(category, name)
                return self.send_json(200, {"category": category, "exercises": exercises})
            match = re.fullmatch(r"/api/programs/([a-f0-9]+)", path)
            if match:
                delete_program(match.group(1))
                return self.send_json(200, {"programs": get_programs()})
            return self.send_json(404, {"error": "����������� �����."})
        except PermissionError as exc:
            return self.send_json(401, {"error": str(exc)})
        except ValueError as exc:
            return self.send_json(400, {"error": str(exc)})
        except Exception:
            return self.send_json(500, {"error": "�� ������� ������� ������."})


def telegram_api(method, payload, timeout=35):
    request = Request(
        f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(f"Telegram API rejected {method}")
    return result.get("result")


def telegram_updates():
    """Keep the /start launch button available without a custom chat menu button."""
    if not APP_URL:
        print("APP_URL/DOMAIN is missing; Telegram launch buttons are not configured yet.")
        return
    markup = {"inline_keyboard": [[{
        "text": "Открыть дневник тренировок",
        "web_app": {"url": APP_URL},
    }]]}
    message = "Открой дневник тренировок кнопкой ниже."
    offset = None
    try:
        telegram_api("deleteWebhook", {"drop_pending_updates": False}, timeout=20)
        telegram_api("setChatMenuButton", {"menu_button": {"type": "default"}}, timeout=20)
    except Exception as exc:
        print(f"Could not configure Telegram menu ({type(exc).__name__}); will retry updates.")

    while True:
        try:
            params = {"timeout": 25, "allowed_updates": ["message"]}
            if offset is not None:
                params["offset"] = offset
            updates = telegram_api("getUpdates", params, timeout=35) or []
            for update in updates:
                offset = max(offset or 0, int(update.get("update_id", 0)) + 1)
                incoming = update.get("message", {})
                chat = incoming.get("chat", {})
                command = str(incoming.get("text", "")).strip().split(" ", 1)[0].split("@", 1)[0]
                if chat.get("type") != "private" or command != "/start":
                    continue
                telegram_api("sendMessage", {
                    "chat_id": chat["id"], "text": message, "reply_markup": markup,
                }, timeout=20)
        except Exception as exc:
            # Error text can contain the API URL, which includes the bot token.
            print(f"Telegram polling retry ({type(exc).__name__}).")
            time.sleep(4)


def run():
    ensure_schema()
    if not DEV_MODE and not BOT_TOKEN:
        raise SystemExit("����� TELEGRAM_BOT_TOKEN. ��� ���������� ��������� ��������� --dev.")
    # BotHost's reverse proxy connects to the container over its private network.
    host = os.environ.get("HOST", "0.0.0.0")
    server = ThreadingHTTPServer((host, PORT), Handler)
    print(f"Mini App backend listening on http://{host}:{PORT}")
    print("Telegram Mini App stores a separate private journal for each Telegram account.")
    if not DEV_MODE:
        threading.Thread(target=telegram_updates, name="telegram-updates", daemon=True).start()
    if DEV_MODE:
        print("WARNING: development authentication is enabled; do not expose this server to the internet.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping Mini App backend.")
    finally:
        server.server_close()


if __name__ == "__main__":
    if "--dev" in sys.argv:
        DEV_MODE = True
    run()
