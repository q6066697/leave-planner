"""Demo data: 2 divisions, 4 departments, 28 employees, 2027 holidays, blackouts and requests.

Run `python seed.py` to (re)create vacation.db from scratch.
Several requests intentionally violate rules or overlap with each other.
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import rules_engine as re_
import storage
from rules_engine import DIRECTOR, SALES_MANAGER, DEPT_HEAD, AREA_HEAD, HR, TEAM_LEAD as TL, DEPUTY_COMMERCE, DEPUTY_OPERATIONS

PLAN_YEAR = 2027

DIVISIONS = [(1, "Отделение 1"), (2, "Отделение 2")]

# id, name, division
DEPARTMENTS = [
    (1, "Отдел бытовой техники", 1),
    (2, "Отдел электроники", 1),
    (3, "Отдел мебели", 2),
    (4, "Отдел товаров для дома", 2),
]

# id, name, position, department, division, manager, hire date, salary (оклад), individual norm
EMPLOYEES = [
    (1, "Ковалёв Виктор", DIRECTOR, None, None, None, "2012-03-01", 5500, 28),
    (2, "Мельник Андрей", DEPUTY_COMMERCE, None, None, 1, "2015-02-20", 4200, 28),
    (3, "Кравченко Алексей", DEPUTY_OPERATIONS, None, None, 1, "2016-10-03", 4200, 28),
    (4, "Ковалёва Ольга", HR, None, None, 1, "2018-03-12", 3000, None),
    # Area heads: two in each division; Лукашевич and Бондарь sit in different divisions but share deputy Мельник
    (5, "Гринкевич Николай", AREA_HEAD, None, 1, 2, "2015-05-25", 3200, None),
    (6, "Лукашевич Павел", AREA_HEAD, None, 1, 2, "2019-03-16", 3200, None),
    (7, "Бондарь Виктория", AREA_HEAD, None, 2, 2, "2020-09-05", 3200, None),
    (8, "Жук Максим", AREA_HEAD, None, 2, 3, "2019-08-26", 3200, None),
    (9, "Сидоренко Наталья", DEPT_HEAD, 1, 1, 5, "2021-06-01", 2400, None),
    (10, "Ткачук Светлана", DEPT_HEAD, 2, 1, 6, "2021-12-06", 2400, None),
    (11, "Романович Елена", DEPT_HEAD, 3, 2, 7, "2020-05-13", 2400, None),
    (12, "Шевчук Дмитрий", DEPT_HEAD, 4, 2, 8, "2022-01-09", 2400, None),
    (13, "Новик Сергей", TL, 1, 1, 9, "2020-11-02", 1900, None),
    (14, "Козлова Татьяна", TL, 1, 1, 9, "2022-04-18", 1900, None),
    (15, "Василевский Игорь", TL, 2, 1, 10, "2021-02-10", 1900, None),
    (16, "Павлович Ирина", TL, 2, 1, 10, "2023-03-06", 1900, None),
    (17, "Климович Анна", TL, 3, 2, 11, "2019-04-22", 1900, None),
    (18, "Зайцев Олег", TL, 3, 2, 11, "2022-07-11", 1900, None),
    (19, "Лис Марина", TL, 4, 2, 12, "2021-09-01", 1900, None),
    (20, "Петрович Артём", TL, 4, 2, 12, "2024-02-05", 1900, None),
    (21, "Савицкий Денис", SALES_MANAGER, 1, 1, 13, "2022-03-14", 1500, None),
    (22, "Юрченко Алина", SALES_MANAGER, 1, 1, 14, "2023-05-02", 1500, None),
    (23, "Кузьмич Роман", SALES_MANAGER, 2, 1, 15, "2021-10-18", 1500, None),
    (24, "Белова Дарья", SALES_MANAGER, 2, 1, 16, "2026-09-15", 1500, None),
    (25, "Ярошевич Кирилл", SALES_MANAGER, 3, 2, 17, "2020-01-20", 1500, None),
    (26, "Демидович Ульяна", SALES_MANAGER, 3, 2, 18, "2024-06-03", 1500, None),
    (27, "Михалевич Глеб", SALES_MANAGER, 4, 2, 19, "2023-11-13", 1500, None),
    (28, "Соловей Полина", SALES_MANAGER, 4, 2, 20, "2025-04-07", 1500, None),
]

HOLIDAYS_2027 = [
    ("2027-01-01", "Новый год"),
    ("2027-01-02", "Новый год"),
    ("2027-01-07", "Рождество Христово (православное)"),
    ("2027-03-08", "День женщин"),
    ("2027-05-01", "Праздник труда"),
    ("2027-05-09", "День Победы"),
    ("2027-05-11", "Радуница (сверить с официальным календарём)"),
    ("2027-07-03", "День Независимости"),
    ("2027-11-07", "День Октябрьской революции"),
    ("2027-12-25", "Рождество Христово (католическое)"),
]

BLACKOUTS = [
    ("2027-03-25", "2027-03-31", "Закрытие I квартала"),
    ("2027-06-25", "2027-06-30", "Закрытие II квартала"),
    ("2027-09-24", "2027-09-30", "Закрытие III квартала"),
    ("2027-12-20", "2027-12-31", "Годовая отчётность"),
]

S = storage
L, U, T = re_.LABOR, re_.UNPAID, re_.TREATMENT

# key, employee, type, status, created_at, parts [(start, days)], substitute, pay, manager comment, HR comment
REQUESTS = [
    ("director", 1, L, S.STATUS_APPROVED, "2026-10-02", [("2027-07-19", 14), ("2027-10-11", 14)], 2, 5200, None, None),
    ("dep_c", 2, L, S.STATUS_APPROVED, "2026-10-03", [("2027-08-02", 14), ("2027-11-08", 14)], 4, 4000, None, None),
    # overlaps with the director -> approved as an exception below
    ("dep_o", 3, L, S.STATUS_MANAGER_APPROVED, "2026-10-04", [("2027-07-26", 14), ("2027-11-22", 14)], 8, 4000, None, None),
    ("hr", 4, L, S.STATUS_APPROVED, "2026-10-04", [("2027-04-05", 14), ("2027-09-06", 10)], 8, None, None, None),
    ("ah5", 5, L, S.STATUS_APPROVED, "2026-10-05", [("2027-05-17", 14), ("2027-10-04", 10)], 9, None, None, None),
    # area head under the same deputy as Гринкевич, overlaps him in May
    ("ah6", 6, L, S.STATUS_MANAGER_APPROVED, "2026-10-12", [("2027-05-24", 14), ("2027-10-18", 10)], 10, None, None, None),
    # overlaps with her own deputy Мельник in August
    ("ah7", 7, L, S.STATUS_MANAGER_APPROVED, "2026-10-13", [("2027-08-09", 14), ("2027-11-22", 10)], 11, None, None, None),
    ("dh9", 9, L, S.STATUS_APPROVED, "2026-10-05", [("2027-08-16", 14), ("2027-12-06", 10)], 13, 2100, None, None),
    # transfer of Сидоренко's approved vacation: the second part moves from December to November
    ("dh9t", 9, L, S.STATUS_MANAGER_APPROVED, "2026-10-20", [("2027-08-16", 14), ("2027-11-15", 10)], 13, 2100, None, None),
    ("dh10", 10, U, S.STATUS_SUBMITTED, "2026-10-21", [("2027-03-01", 5)], 15, None, None, None),
    ("dh11", 11, L, S.STATUS_MANAGER_APPROVED, "2026-10-14", [("2027-09-06", 14), ("2027-11-08", 10)], 17, 2300, None, None),
    ("dh12", 12, L, S.STATUS_RETURNED, "2026-10-15", [("2027-06-28", 14), ("2027-10-04", 10)], 19, None,
     "Конец июня — пик сезона в отделе. Перенеси первую часть на сентябрь.", None),
    ("tl13", 13, L, S.STATUS_APPROVED, "2026-10-06", [("2027-06-07", 14), ("2027-09-13", 10)], 14, 1800, None, None),
    # same department as Новик, overlaps him in June; Новик also named her as his substitute
    ("tl14", 14, L, S.STATUS_SUBMITTED, "2026-10-16", [("2027-06-10", 14), ("2027-10-11", 10)], 9, None, None, None),
    ("tl15", 15, L, S.STATUS_SUBMITTED, "2026-10-17", [("2027-04-12", 14), ("2027-10-04", 10)], 16, 1700, None, None),
    ("tl16", 16, L, S.STATUS_RETURNED, "2026-10-09", [("2027-09-20", 14), ("2027-12-13", 10)], 15, None, "Согласовано",
     "Первая часть попадает на закрытие III квартала. Сдвинь на октябрь."),
    ("tl17", 17, T, S.STATUS_APPROVED, "2026-10-07", [("2027-07-05", 10)], 11, None, None, None),
    # labour vacation overlaps a team lead colleague's planned treatment: rules apply to all types
    ("tl18", 18, L, S.STATUS_SUBMITTED, "2026-10-18", [("2027-07-12", 14), ("2027-10-18", 10)], 26, None, None, None),
    ("sm21", 21, L, S.STATUS_APPROVED, "2026-10-08", [("2027-06-07", 14), ("2027-10-18", 10)], 15, 1400, None, None),
    # 3rd sales manager of the company in June
    ("sm22", 22, L, S.STATUS_MANAGER_APPROVED, "2026-10-19", [("2027-06-09", 14), ("2027-10-04", 10)], 27, None, None, None),
    ("sm23", 23, L, S.STATUS_APPROVED, "2026-10-08", [("2027-06-10", 14), ("2027-11-15", 10)], 15, 1450, None, None),
    # hired 2026-09-15: first vacation not before 2027-03-15
    ("sm24", 24, L, S.STATUS_SUBMITTED, "2026-10-22", [("2027-02-01", 14), ("2027-08-02", 10)], 23, None, None, None),
    # 24 days in summer (limit 14) and vacation pay above the salary
    ("sm25", 25, L, S.STATUS_MANAGER_APPROVED, "2026-10-20", [("2027-07-05", 24)], 26, 1700, None, None),
]

EXCEPTION_COMMENT = ("Директор согласовал: в конце июля заместитель директора (операции) на связи, "
                     "текущие вопросы закрывает заместитель директора (коммерция). Одобрено как исключение.")


def seed(db_path: Path | str | None = None) -> None:
    path = Path(db_path or storage.DB_PATH)
    if path.exists():
        path.unlink()
    storage.init_db(path)
    with storage.connect(path) as conn:
        conn.executemany("INSERT INTO divisions (id, name) VALUES (?, ?)", DIVISIONS)
        conn.executemany("INSERT INTO departments (id, name, division_id) VALUES (?, ?, ?)", DEPARTMENTS)
        # Managers are inserted before their subordinates (the list is ordered top-down).
        conn.executemany(
            """INSERT INTO employees (id, name, position, department_id, division_id, manager_id, hire_date, salary, annual_days)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", EMPLOYEES)
        conn.executemany("INSERT INTO holidays (date, name) VALUES (?, ?)", HOLIDAYS_2027)
        conn.executemany("INSERT INTO blackout_periods (start, end, name) VALUES (?, ?, ?)", BLACKOUTS)
        storage.upsert_default_rules(conn)

    ids: dict[str, int] = {}
    for key, emp_id, kind, status, created, parts, sub, pay, m_comment, hr_comment in REQUESTS:
        # The transfer must not replace its approved original, so it is created after it with a link.
        ids[key] = storage.create_request(
            emp_id, PLAN_YEAR, [re_.PartInput(date.fromisoformat(s), d) for s, d in parts],
            absence_type=kind, substitute_id=sub, pay_amount=pay, status=status,
            original_request_id=ids["dh9"] if key == "dh9t" else None,
            created_at=datetime.fromisoformat(created + "T10:00:00"),
            manager_comment=m_comment, hr_comment=hr_comment, db_path=path,
        )

    # Approve the operations deputy's request as an exception, logging the actual violations.
    req = storage.get_request(ids["dep_o"], db_path=path)
    ctx = storage.build_context(PLAN_YEAR, today=req.created_at.date(), db_path=path)
    report = re_.check_request(ctx.employees[req.employee_id], req.parts, ctx, absence_type=req.absence_type,
                               substitute_id=req.substitute_id, pay_amount=req.pay_amount)
    violations = [f"{r.rule_name}: {r.message}" for r in report.results if r.status != re_.OK]
    storage.approve_as_exception(ids["dep_o"], EXCEPTION_COMMENT, violations, db_path=path)


if __name__ == "__main__":
    seed()
    print(f"Demo database created: {storage.DB_PATH}")
