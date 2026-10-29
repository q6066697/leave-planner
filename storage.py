"""SQLite storage: org structure, employees, requests, rules, holidays, blackouts, exception log."""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import rules_engine as re_

DB_PATH = Path(os.getenv("VACATION_DB", Path(__file__).with_name("vacation.db")))
SCHEMA_VERSION = 3  # bump when the schema changes: an older vacation.db is re-seeded on start

# Two-stage approval: employee -> line manager -> HR.
STATUS_SUBMITTED = "submitted"  # waiting for the line manager
STATUS_MANAGER_APPROVED = "manager_approved"  # waiting for HR
STATUS_APPROVED = "approved"
STATUS_EXCEPTION = "approved_exception"
STATUS_RETURNED = "returned"
STATUS_REPLACED = "replaced"  # superseded by a newer request of the same type
STATUS_TRANSFERRED = "transferred"  # approved, then moved by an approved transfer request

ACTIVE_STATUSES = (STATUS_SUBMITTED, STATUS_MANAGER_APPROVED, STATUS_APPROVED, STATUS_EXCEPTION)
APPROVED_STATUSES = (STATUS_APPROVED, STATUS_EXCEPTION)
STATUS_LABELS = {
    STATUS_SUBMITTED: "У руководителя",
    STATUS_MANAGER_APPROVED: "У HR-специалиста",
    STATUS_APPROVED: "Согласована",
    STATUS_EXCEPTION: "Согласована как исключение",
    STATUS_RETURNED: "На доработке",
    STATUS_REPLACED: "Заменена новой",
    STATUS_TRANSFERRED: "Перенесена",
}

RULE_DESCRIPTIONS = {
    re_.MIN_DURATION: ("Норма отпуска, дней", "ТК РБ (только трудовой отпуск): не менее 24 календарных дней. Индивидуальная норма сотрудника имеет приоритет."),
    re_.MAX_PARTS: ("Макс. частей", "ТК РБ (только трудовой отпуск): отпуск можно разделить на части (по умолчанию не более 2)."),
    re_.MIN_PART_LENGTH: ("Мин. длина части, дней", "ТК РБ (только трудовой отпуск): одна из частей — не менее 14 календарных дней."),
    re_.HOLIDAY_EXTENSION: (None, "ТК РБ (только трудовой отпуск): государственные праздники в период отпуска не включаются в число его дней."),
    re_.FIRST_VACATION: ("Месяцев работы", "ТК РБ (только трудовой отпуск): первый отпуск — не ранее чем через 6 месяцев работы."),
    re_.CONC_SALES: ("Макс. менеджеров по продажам одновременно", "Внутренняя политика: одновременно в отсутствии не более N менеджеров по продажам во всей компании. Все типы отсутствия."),
    re_.CONC_TEAM_LEAD: (None, "Внутренняя политика: руководитель группы не пересекается с другими руководителями групп и с начальником своего отдела."),
    re_.CONC_DEPT_HEAD: (None, "Внутренняя политика: начальник отдела не пересекается с другими начальниками отделов и с руководителями направлений своего отделения."),
    re_.CONC_AREA_HEAD: (None, "Внутренняя политика: руководитель направления не пересекается с другими руководителями направлений, подчинёнными тому же заместителю директора, и с этим заместителем."),
    re_.CONC_DEPUTY: (None, "Внутренняя политика: заместители директора не пересекаются с директором. Пара «заместитель — директор» снимается, только если выключены оба правила (для заместителей и для директора)."),
    re_.CONC_DIRECTOR: (None, "Внутренняя политика: директор не пересекается с заместителями директора."),
    re_.SUBSTITUTE: (None, "Внутренняя политика: в заявке обязателен замещающий коллега; он не может отсутствовать в эти даты (и сам сотрудник не может отдыхать, когда замещает кого-то)."),
    re_.SUMMER_LIMIT: ("Макс. дней летом", "Внутренняя политика (трудовой отпуск): часть отпуска в летний период — не более N календарных дней. Даты периода настраиваются ниже."),
    re_.VACATION_PAY: (None, "Внутренняя политика: необязательное поле «сумма отпускных» — не больше оклада сотрудника."),
    re_.BLACKOUT: (None, "Внутренняя политика: отсутствие не должно попадать в запретные периоды (закрытие квартала и т. п.)."),
    re_.NOTICE_PERIOD: ("Дней до начала", "Внутренняя политика: заявление подаётся не позднее чем за N дней до начала. Все типы отсутствия."),
    re_.SCHEDULE_DEADLINE: (None, "Внутренняя политика: график на I полугодие подаётся до заданной даты (по умолчанию 15 января), на II — до 15 июля. Даты настраиваются ниже."),
    re_.TRANSFER_NOTICE: ("Дней до начала", "Внутренняя политика: перенос согласованного отпуска — не позднее чем за N дней до начала исходного отпуска."),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS divisions (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS departments (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    division_id INTEGER NOT NULL REFERENCES divisions(id)
);
CREATE TABLE IF NOT EXISTS employees (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    position TEXT NOT NULL,
    department_id INTEGER REFERENCES departments(id),
    division_id INTEGER REFERENCES divisions(id),
    manager_id INTEGER REFERENCES employees(id),
    hire_date TEXT NOT NULL,
    salary REAL,
    annual_days INTEGER
);
CREATE TABLE IF NOT EXISTS rules (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    description TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    value INTEGER,
    value_label TEXT,
    severity TEXT NOT NULL DEFAULT 'error',
    params TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS holidays (
    date TEXT PRIMARY KEY,
    name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS blackout_periods (
    id INTEGER PRIMARY KEY,
    start TEXT NOT NULL,
    end TEXT NOT NULL,
    name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY,
    employee_id INTEGER NOT NULL REFERENCES employees(id),
    year INTEGER NOT NULL,
    absence_type TEXT NOT NULL DEFAULT 'labor',
    status TEXT NOT NULL,
    substitute_id INTEGER REFERENCES employees(id),
    pay_amount REAL,
    original_request_id INTEGER REFERENCES requests(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    manager_comment TEXT,
    hr_comment TEXT
);
CREATE TABLE IF NOT EXISTS request_parts (
    id INTEGER PRIMARY KEY,
    request_id INTEGER NOT NULL REFERENCES requests(id) ON DELETE CASCADE,
    part_no INTEGER NOT NULL,
    start TEXT NOT NULL,
    days INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS exceptions_log (
    id INTEGER PRIMARY KEY,
    request_id INTEGER NOT NULL REFERENCES requests(id),
    employee_id INTEGER NOT NULL REFERENCES employees(id),
    comment TEXT NOT NULL,
    violations TEXT,
    author TEXT,
    created_at TEXT NOT NULL
);
"""


@contextmanager
def connect(db_path: Path | str | None = None):
    conn = sqlite3.connect(db_path or DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path=None) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def db_exists(db_path=None) -> bool:
    """True if the database has the current schema and is filled with rules."""
    path = Path(db_path or DB_PATH)
    if not path.exists():
        return False
    with connect(path) as conn:
        if conn.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
            return False
        row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='rules'").fetchone()
        return row is not None and conn.execute("SELECT COUNT(*) FROM rules").fetchone()[0] > 0


def _d(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ------------------------------------------------------------- reference data

def list_divisions(db_path=None) -> list[dict]:
    with connect(db_path) as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM divisions ORDER BY id")]


def list_departments(db_path=None) -> list[dict]:
    with connect(db_path) as conn:
        return [dict(r) for r in conn.execute(
            """SELECT d.*, v.name AS division FROM departments d JOIN divisions v ON v.id = d.division_id
               ORDER BY d.division_id, d.id""")]


POSITION_ORDER = {p: i for i, p in enumerate((re_.DIRECTOR, re_.DEPUTY_COMMERCE, re_.DEPUTY_OPERATIONS, re_.HR, re_.AREA_HEAD, re_.DEPT_HEAD, re_.TEAM_LEAD, re_.SALES_MANAGER))}


def list_employees(db_path=None) -> list[dict]:
    """Employees with org-structure names, ordered top-down by the hierarchy."""
    with connect(db_path) as conn:
        rows = [dict(r) for r in conn.execute(
            """SELECT e.*, d.name AS department, v.name AS division, m.name AS manager, m.position AS manager_position
               FROM employees e
               LEFT JOIN departments d ON d.id = e.department_id
               LEFT JOIN divisions v ON v.id = e.division_id
               LEFT JOIN employees m ON m.id = e.manager_id""")]
    for r in rows:
        r["unit"] = " · ".join(x for x in (r["division"], r["department"]) if x) or "Руководство"
    rows.sort(key=lambda r: (r["division_id"] or 0, r["department_id"] or 0, POSITION_ORDER.get(r["position"], 99), r["name"]))
    return rows


def to_engine_employee(row: dict) -> re_.Employee:
    return re_.Employee(row["id"], row["name"], row["position"], _d(row["hire_date"]),
                        department_id=row["department_id"], division_id=row["division_id"],
                        manager_id=row["manager_id"], salary=row["salary"], annual_days=row["annual_days"])


# ---------------------------------------------------------------------- rules

def list_rules(db_path=None) -> list[dict]:
    with connect(db_path) as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM rules ORDER BY sort_order")]
    for r in rows:
        r["params"] = json.loads(r["params"]) if r["params"] else {}
    return rows


def get_rules(db_path=None) -> dict[str, re_.RuleConfig]:
    return {
        r["code"]: re_.RuleConfig(r["code"], r["name"], bool(r["enabled"]), r["value"], r["severity"], r["category"], r["params"])
        for r in list_rules(db_path)
    }


def update_rule(code: str, enabled: bool, value: int | None, severity: str, db_path=None) -> None:
    with connect(db_path) as conn:
        conn.execute("UPDATE rules SET enabled = ?, value = ?, severity = ? WHERE code = ?",
                     (int(enabled), value, severity, code))


def update_rule_params(code: str, params: dict, db_path=None) -> None:
    with connect(db_path) as conn:
        conn.execute("UPDATE rules SET params = ? WHERE code = ?", (json.dumps(params), code))


def upsert_default_rules(conn: sqlite3.Connection) -> None:
    for order, rule in enumerate(re_.DEFAULT_RULES.values()):
        label, description = RULE_DESCRIPTIONS[rule.code]
        conn.execute(
            """INSERT OR REPLACE INTO rules (code, name, category, description, enabled, value, value_label, severity, params, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (rule.code, rule.name, rule.category, description, int(rule.enabled), rule.value, label, rule.severity,
             json.dumps(rule.params) if rule.params else None, order),
        )


def list_holidays(db_path=None) -> list[dict]:
    with connect(db_path) as conn:
        return [{"date": _d(r["date"]), "name": r["name"]} for r in conn.execute("SELECT * FROM holidays ORDER BY date")]


def replace_holidays(rows: list[tuple[date, str]], db_path=None) -> None:
    with connect(db_path) as conn:
        conn.execute("DELETE FROM holidays")
        conn.executemany("INSERT OR REPLACE INTO holidays (date, name) VALUES (?, ?)",
                         [(d.isoformat(), name) for d, name in rows])


def list_blackouts(db_path=None) -> list[dict]:
    with connect(db_path) as conn:
        return [{"start": _d(r["start"]), "end": _d(r["end"]), "name": r["name"]}
                for r in conn.execute("SELECT * FROM blackout_periods ORDER BY start")]


def replace_blackouts(rows: list[tuple[date, date, str]], db_path=None) -> None:
    with connect(db_path) as conn:
        conn.execute("DELETE FROM blackout_periods")
        conn.executemany("INSERT INTO blackout_periods (start, end, name) VALUES (?, ?, ?)",
                         [(s.isoformat(), e.isoformat(), name) for s, e, name in rows])


# ------------------------------------------------------------------- requests

@dataclass
class Request:
    id: int
    employee_id: int
    year: int
    absence_type: str
    status: str
    substitute_id: int | None
    pay_amount: float | None
    original_request_id: int | None
    created_at: datetime
    updated_at: datetime
    manager_comment: str | None
    hr_comment: str | None
    parts: list[re_.PartInput] = field(default_factory=list)

    @property
    def is_transfer(self) -> bool:
        return self.original_request_id is not None


def _load_requests(conn, where: str = "", params: tuple = ()) -> list[Request]:
    rows = conn.execute(f"SELECT * FROM requests {where} ORDER BY created_at, id", params).fetchall()
    result = []
    for r in rows:
        parts = [re_.PartInput(_d(p["start"]), p["days"]) for p in
                 conn.execute("SELECT * FROM request_parts WHERE request_id = ? ORDER BY part_no", (r["id"],))]
        result.append(Request(r["id"], r["employee_id"], r["year"], r["absence_type"], r["status"], r["substitute_id"],
                              r["pay_amount"], r["original_request_id"],
                              datetime.fromisoformat(r["created_at"]), datetime.fromisoformat(r["updated_at"]),
                              r["manager_comment"], r["hr_comment"], parts))
    return result


def list_requests(year: int, statuses: tuple[str, ...] | None = None, db_path=None) -> list[Request]:
    with connect(db_path) as conn:
        if statuses:
            marks = ",".join("?" * len(statuses))
            return _load_requests(conn, f"WHERE year = ? AND status IN ({marks})", (year, *statuses))
        return _load_requests(conn, "WHERE year = ? AND status NOT IN (?, ?)", (year, STATUS_REPLACED, STATUS_TRANSFERRED))


def get_request(request_id: int, db_path=None) -> Request | None:
    with connect(db_path) as conn:
        found = _load_requests(conn, "WHERE id = ?", (request_id,))
        return found[0] if found else None


def latest_request(employee_id: int, year: int, absence_type: str = re_.LABOR, db_path=None) -> Request | None:
    """The employee's current request of this type (a pending transfer wins over the approved original)."""
    with connect(db_path) as conn:
        found = _load_requests(conn, "WHERE employee_id = ? AND year = ? AND absence_type = ? AND status NOT IN (?, ?)",
                               (employee_id, year, absence_type, STATUS_REPLACED, STATUS_TRANSFERRED))
        return found[-1] if found else None


def approved_request(employee_id: int, year: int, absence_type: str = re_.LABOR, db_path=None) -> Request | None:
    with connect(db_path) as conn:
        found = _load_requests(conn, "WHERE employee_id = ? AND year = ? AND absence_type = ? AND status IN (?, ?)",
                               (employee_id, year, absence_type, *APPROVED_STATUSES))
        return found[-1] if found else None


def initial_status(employee_id: int, db_path=None) -> str:
    """A request of an employee without a line manager goes straight to HR."""
    with connect(db_path) as conn:
        manager = conn.execute("SELECT manager_id FROM employees WHERE id = ?", (employee_id,)).fetchone()[0]
    return STATUS_SUBMITTED if manager else STATUS_MANAGER_APPROVED


def create_request(employee_id: int, year: int, parts: list[re_.PartInput], *, absence_type: str = re_.LABOR,
                   substitute_id: int | None = None, pay_amount: float | None = None,
                   original_request_id: int | None = None, status: str | None = None,
                   created_at: datetime | None = None, manager_comment: str | None = None,
                   hr_comment: str | None = None, db_path=None) -> int:
    """Create a request; previous not yet approved requests of the same type for the year are replaced."""
    ts = (created_at or datetime.now()).isoformat(timespec="seconds")
    status = status or initial_status(employee_id, db_path)
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE requests SET status = ?, updated_at = ? WHERE employee_id = ? AND year = ? AND absence_type = ? AND status IN (?, ?, ?)",
            (STATUS_REPLACED, ts, employee_id, year, absence_type, STATUS_SUBMITTED, STATUS_MANAGER_APPROVED, STATUS_RETURNED))
        cur = conn.execute(
            """INSERT INTO requests (employee_id, year, absence_type, status, substitute_id, pay_amount, original_request_id,
                                     created_at, updated_at, manager_comment, hr_comment)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (employee_id, year, absence_type, status, substitute_id, pay_amount, original_request_id, ts, ts,
             manager_comment, hr_comment))
        request_id = cur.lastrowid
        conn.executemany("INSERT INTO request_parts (request_id, part_no, start, days) VALUES (?, ?, ?, ?)",
                         [(request_id, i, p.start.isoformat(), p.days) for i, p in enumerate(parts, 1)])
        return request_id


def manager_decision(request_id: int, approve: bool, comment: str | None = None, db_path=None) -> None:
    """Stage 1: the line manager forwards the request to HR or returns it with a comment."""
    if not approve and not (comment or "").strip():
        raise ValueError("Комментарий обязателен при возврате")
    with connect(db_path) as conn:
        conn.execute("UPDATE requests SET status = ?, manager_comment = COALESCE(?, manager_comment), updated_at = ? WHERE id = ?",
                     (STATUS_MANAGER_APPROVED if approve else STATUS_RETURNED, comment or None, _now(), request_id))


def _finish_transfer(conn, request_id: int) -> None:
    original = conn.execute("SELECT original_request_id FROM requests WHERE id = ?", (request_id,)).fetchone()[0]
    if original:
        conn.execute("UPDATE requests SET status = ?, updated_at = ? WHERE id = ?", (STATUS_TRANSFERRED, _now(), original))


def set_request_status(request_id: int, status: str, comment: str | None = None, db_path=None) -> None:
    """Stage 2 (HR): approve or return. Approving a transfer marks the original as transferred."""
    with connect(db_path) as conn:
        conn.execute("UPDATE requests SET status = ?, hr_comment = COALESCE(?, hr_comment), updated_at = ? WHERE id = ?",
                     (status, comment, _now(), request_id))
        if status in APPROVED_STATUSES:
            _finish_transfer(conn, request_id)


def approve_as_exception(request_id: int, comment: str, violations: list[str], author: str = "HR-специалист", db_path=None) -> None:
    if not comment.strip():
        raise ValueError("Комментарий обязателен")
    with connect(db_path) as conn:
        emp = conn.execute("SELECT employee_id FROM requests WHERE id = ?", (request_id,)).fetchone()[0]
        conn.execute("UPDATE requests SET status = ?, hr_comment = ?, updated_at = ? WHERE id = ?",
                     (STATUS_EXCEPTION, comment, _now(), request_id))
        conn.execute("INSERT INTO exceptions_log (request_id, employee_id, comment, violations, author, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                     (request_id, emp, comment, "\n".join(violations), author, _now()))
        _finish_transfer(conn, request_id)


def list_exceptions(db_path=None) -> list[dict]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """SELECT x.*, e.name AS employee FROM exceptions_log x
               JOIN employees e ON e.id = x.employee_id ORDER BY x.created_at DESC""")
        return [dict(r) for r in rows]


# ------------------------------------------------------------ engine context

def build_context(year: int, today: date | None = None, db_path=None) -> re_.CheckContext:
    """Assemble everything the rules engine needs from the database."""
    rules = get_rules(db_path)
    holidays = frozenset(h["date"] for h in list_holidays(db_path))
    ctx = re_.CheckContext(
        rules=rules,
        holidays=holidays,
        blackouts=[re_.Blackout(b["start"], b["end"], b["name"]) for b in list_blackouts(db_path)],
        employees={e["id"]: to_engine_employee(e) for e in list_employees(db_path)},
        today=today or date.today(),
    )
    booked = []
    for req in list_requests(year, ACTIVE_STATUSES, db_path):
        for p in re_.plan_parts(req.parts, ctx, req.absence_type):
            booked.append(re_.BookedPart(req.employee_id, p.start, p.end, req.id, req.status,
                                         req.absence_type, req.substitute_id))
    ctx.booked = booked
    return ctx


def next_month_absences(year: int, today: date, db_path=None) -> tuple[list[dict], date]:
    """For accounting: approved absences (parts) starting in the month after `today`, and that month."""
    first = date(today.year + today.month // 12, today.month % 12 + 1, 1)
    last = re_.add_months(first, 1).replace(day=1)
    ctx = build_context(year, today, db_path)
    rows = []
    for y in {first.year, year}:
        for req in list_requests(y, APPROVED_STATUSES, db_path):
            for p in re_.plan_parts(req.parts, ctx, req.absence_type):
                if first <= p.start < last:
                    rows.append({"name": ctx.employees[req.employee_id].name,
                                 "type": re_.ABSENCE_TYPES[req.absence_type],
                                 "start": p.start, "end": p.end, "days": p.days, "pay_amount": req.pay_amount})
    rows.sort(key=lambda r: (r["start"], r["name"]))
    return rows, first
