"""Vacation request validation engine.

Pure functions only: no Streamlit, no database, no LLM. Every rule returns a
CheckResult with a status, an employee-friendly explanation and a hint on how
to fix the request. All rule parameters come from CheckContext.rules, so HR
changes in the database immediately affect validation.

Rules of the Labour Code of Belarus (24 days, parts, holidays, first vacation)
apply only to the labour vacation; concurrency and submission deadlines apply
to every absence type.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta

OK = "ok"
ERROR = "error"
WARNING = "warning"

# Absence types.
LABOR = "labor"
UNPAID = "unpaid"
TREATMENT = "treatment"
ABSENCE_TYPES = {
    LABOR: "Трудовой отпуск",
    UNPAID: "Отпуск без сохранения зарплаты",
    TREATMENT: "Плановое лечение",
}

# Positions of the example org structure (top-down).
SALES_MANAGER = "Менеджер по продажам"
TEAM_LEAD = "Руководитель группы"
DEPT_HEAD = "Начальник отдела"
AREA_HEAD = "Руководитель направления"
DEPUTY_COMMERCE = "Заместитель директора (коммерция)"
DEPUTY_OPERATIONS = "Заместитель директора (операции)"
DIRECTOR = "Директор"
HR = "HR-специалист"
POSITIONS = (SALES_MANAGER, TEAM_LEAD, DEPT_HEAD, AREA_HEAD, DEPUTY_COMMERCE, DEPUTY_OPERATIONS, DIRECTOR, HR)

# Rule codes (stored in the `rules` table).
MIN_DURATION = "min_duration"
MAX_PARTS = "max_parts"
MIN_PART_LENGTH = "min_part_length"
HOLIDAY_EXTENSION = "holiday_extension"
FIRST_VACATION = "first_vacation"
CONC_SALES = "conc_sales"
CONC_TEAM_LEAD = "conc_team_lead"
CONC_DEPT_HEAD = "conc_dept_head"
CONC_AREA_HEAD = "conc_area_head"
CONC_DEPUTY = "conc_deputy"
CONC_DIRECTOR = "conc_director"
SUBSTITUTE = "substitute"
SUMMER_LIMIT = "summer_limit"
VACATION_PAY = "vacation_pay"
BLACKOUT = "blackout"
NOTICE_PERIOD = "notice_period"
SCHEDULE_DEADLINE = "schedule_deadline"
TRANSFER_NOTICE = "transfer_notice"
# Always-on checks (not configurable).
INPUT = "input"
OWN_OVERLAP = "own_overlap"

LAW_RULES = (MIN_DURATION, MAX_PARTS, MIN_PART_LENGTH, HOLIDAY_EXTENSION, FIRST_VACATION)
PAIR_RULES = (CONC_TEAM_LEAD, CONC_DEPT_HEAD, CONC_AREA_HEAD, CONC_DEPUTY, CONC_DIRECTOR)


@dataclass(frozen=True)
class RuleConfig:
    code: str
    name: str
    enabled: bool = True
    value: int | None = None
    severity: str = ERROR  # status used when the rule is violated
    category: str = "law"  # "law" | "internal"
    params: dict = field(default_factory=dict, compare=False, hash=False)


# Defaults are used by the seed script and by tests.
DEFAULT_RULES: dict[str, RuleConfig] = {
    r.code: r
    for r in [
        RuleConfig(MIN_DURATION, "Минимальная продолжительность отпуска", True, 24, ERROR, "law"),
        RuleConfig(MAX_PARTS, "Максимальное число частей отпуска", True, 2, ERROR, "law"),
        RuleConfig(MIN_PART_LENGTH, "Минимальная длина одной из частей", True, 14, ERROR, "law"),
        RuleConfig(HOLIDAY_EXTENSION, "Продление отпуска на государственные праздники", True, None, ERROR, "law"),
        RuleConfig(FIRST_VACATION, "Первый отпуск — после N месяцев работы", True, 6, ERROR, "law"),
        RuleConfig(CONC_SALES, "Менеджеры по продажам: не более N одновременно во всей компании", True, 2, ERROR, "internal"),
        RuleConfig(CONC_TEAM_LEAD, "Руководитель группы: без пересечений с руководителями групп и начальником своего отдела", True, None, ERROR, "internal"),
        RuleConfig(CONC_DEPT_HEAD, "Начальник отдела: без пересечений с начальниками отделов и руководителями направлений своего отделения", True, None, ERROR, "internal"),
        RuleConfig(CONC_AREA_HEAD, "Руководитель направления: без пересечений с руководителями направлений того же заместителя директора и с самим заместителем", True, None, ERROR, "internal"),
        RuleConfig(CONC_DEPUTY, "Заместители директора: без пересечений с директором", True, None, ERROR, "internal"),
        RuleConfig(CONC_DIRECTOR, "Директор: без пересечений с заместителями директора", True, None, ERROR, "internal"),
        RuleConfig(SUBSTITUTE, "Замещающий коллега", True, None, ERROR, "internal"),
        RuleConfig(SUMMER_LIMIT, "Летний лимит отпуска, дней", True, 14, ERROR, "internal",
                   {"start": "06-01", "end": "08-31"}),
        RuleConfig(VACATION_PAY, "Сумма отпускных не больше оклада", True, None, ERROR, "internal"),
        RuleConfig(BLACKOUT, "Запретные периоды", True, None, ERROR, "internal"),
        RuleConfig(NOTICE_PERIOD, "Срок подачи заявления (дней до начала)", True, 30, ERROR, "internal"),
        RuleConfig(SCHEDULE_DEADLINE, "Срок подачи графика на полугодие", True, None, WARNING, "internal",
                   {"h1": "01-15", "h2": "07-15"}),
        RuleConfig(TRANSFER_NOTICE, "Срок подачи переноса (дней до начала исходного отпуска)", True, 14, ERROR, "internal"),
    ]
}


@dataclass(frozen=True)
class Employee:
    id: int
    name: str
    position: str = SALES_MANAGER
    hire_date: date = date(2020, 1, 1)
    department_id: int | None = None  # отдел
    division_id: int | None = None  # отделение
    manager_id: int | None = None  # непосредственный руководитель
    salary: float | None = None  # оклад
    annual_days: int | None = None  # individual norm; None -> rule default


@dataclass(frozen=True)
class PartInput:
    """What the employee enters: start date and number of days."""
    start: date
    days: int


@dataclass(frozen=True)
class PlannedPart:
    """A part with the actual end date after holiday extension."""
    start: date
    days: int
    end: date
    holidays: tuple[date, ...] = ()


@dataclass(frozen=True)
class BookedPart:
    """A part of another request that is already submitted or approved."""
    employee_id: int
    start: date
    end: date
    request_id: int | None = None
    status: str = "submitted"
    absence_type: str = LABOR
    substitute_id: int | None = None


@dataclass(frozen=True)
class Blackout:
    start: date
    end: date
    name: str


@dataclass
class CheckContext:
    rules: dict[str, RuleConfig] = field(default_factory=lambda: dict(DEFAULT_RULES))
    holidays: frozenset[date] = frozenset()
    blackouts: list[Blackout] = field(default_factory=list)
    employees: dict[int, Employee] = field(default_factory=dict)
    booked: list[BookedPart] = field(default_factory=list)
    today: date = field(default_factory=date.today)


@dataclass(frozen=True)
class CheckResult:
    rule_code: str
    rule_name: str
    status: str
    message: str
    hint: str = ""


@dataclass
class CheckReport:
    parts: list[PlannedPart]
    results: list[CheckResult]

    @property
    def errors(self) -> list[CheckResult]:
        return [r for r in self.results if r.status == ERROR]

    @property
    def warnings(self) -> list[CheckResult]:
        return [r for r in self.results if r.status == WARNING]

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)


# ---------------------------------------------------------------- helpers

def fmt(d: date) -> str:
    return d.strftime("%d.%m.%Y")


def fmt_range(start: date, end: date) -> str:
    return f"{fmt(start)} – {fmt(end)}"


def fmt_money(x: float) -> str:
    return f"{x:,.2f}".replace(",", " ")


def add_months(d: date, months: int) -> date:
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def month_day(value: str, year: int) -> date:
    """'MM-DD' -> date in the given year (29 Feb is clamped in non-leap years)."""
    month, day = (int(x) for x in value.split("-"))
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def overlap(a_start: date, a_end: date, b_start: date, b_end: date) -> tuple[date, date] | None:
    start, end = max(a_start, b_start), min(a_end, b_end)
    return (start, end) if start <= end else None


def compute_end_date(start: date, days: int, holidays: frozenset[date] | set[date] = frozenset()) -> tuple[date, tuple[date, ...]]:
    """Return the actual last day of vacation and the holidays that extended it.

    Public holidays falling inside the vacation are not counted as vacation
    days, so the vacation is extended by that many days.
    """
    if days < 1:
        raise ValueError("days must be >= 1")
    current = start
    counted = 0
    skipped: list[date] = []
    while True:
        if current in holidays:
            skipped.append(current)
        else:
            counted += 1
            if counted == days:
                return current, tuple(skipped)
        current += timedelta(days=1)


def plan_parts(parts: list[PartInput], ctx: CheckContext, absence_type: str = LABOR) -> list[PlannedPart]:
    """Holidays extend only the labour vacation; other absences are plain calendar days."""
    extend = absence_type == LABOR and _enabled(ctx, HOLIDAY_EXTENSION)
    holidays = ctx.holidays if extend else frozenset()
    planned = []
    for p in sorted(parts, key=lambda p: p.start):
        end, skipped = compute_end_date(p.start, p.days, holidays)
        planned.append(PlannedPart(p.start, p.days, end, skipped))
    return planned


def changed_parts(parts: list[PartInput], original: list[PartInput]) -> tuple[list[PartInput], list[PartInput]]:
    """For a transfer: (new parts that differ from the original, original parts that are being moved)."""
    new = [p for p in parts if p not in original]
    moved = [o for o in original if o not in parts]
    return new, moved


def _rule(ctx: CheckContext, code: str) -> RuleConfig:
    return ctx.rules.get(code) or DEFAULT_RULES[code]


def _enabled(ctx: CheckContext, code: str) -> bool:
    return _rule(ctx, code).enabled


def _value(ctx: CheckContext, code: str) -> int:
    rule = _rule(ctx, code)
    return rule.value if rule.value is not None else DEFAULT_RULES[code].value


def _param(ctx: CheckContext, code: str, key: str) -> str:
    return _rule(ctx, code).params.get(key) or DEFAULT_RULES[code].params[key]


def _ok(rule: RuleConfig, message: str) -> CheckResult:
    return CheckResult(rule.code, rule.name, OK, message)


def _fail(rule: RuleConfig, message: str, hint: str) -> CheckResult:
    return CheckResult(rule.code, rule.name, rule.severity, message, hint)


def _days_range(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def _group_ranges(days: list[date]) -> list[tuple[date, date]]:
    ranges: list[tuple[date, date]] = []
    for d in sorted(set(days)):
        if ranges and ranges[-1][1] + timedelta(days=1) == d:
            ranges[-1] = (ranges[-1][0], d)
        else:
            ranges.append((d, d))
    return ranges


def _who(e: Employee) -> str:
    return f"{e.name} ({e.position})"


def _absence(b: BookedPart) -> str:
    return ABSENCE_TYPES.get(b.absence_type, b.absence_type).lower()


# ------------------------------------------------------------------ rules

def check_input(parts: list[PartInput], original: list[PartInput] | None = None) -> list[CheckResult]:
    name = "Корректность ввода"
    results = []
    if not parts:
        return [CheckResult(INPUT, name, ERROR, "Не указано ни одного периода.", "Добавьте хотя бы один период отсутствия.")]
    for i, p in enumerate(parts, 1):
        if p.days < 1:
            results.append(CheckResult(INPUT, name, ERROR, f"Часть {i}: число дней должно быть больше нуля.", "Укажите количество календарных дней."))
    if results:
        return results
    ordered = sorted(parts, key=lambda p: p.start)
    for a, b in zip(ordered, ordered[1:]):
        a_end = a.start + timedelta(days=a.days - 1)
        if b.start <= a_end:
            results.append(CheckResult(INPUT, name, ERROR, "Части отпуска пересекаются между собой.", f"Начните вторую часть не раньше {fmt(a_end + timedelta(days=1))}."))
    if original is not None and sorted(parts, key=lambda p: p.start) == sorted(original, key=lambda p: p.start):
        results.append(CheckResult(INPUT, name, ERROR, "Даты переноса совпадают с уже согласованными.", "Измените даты хотя бы одной части."))
    return results


def check_min_duration(employee: Employee, parts: list[PlannedPart], ctx: CheckContext) -> CheckResult:
    rule = _rule(ctx, MIN_DURATION)
    norm = employee.annual_days or _value(ctx, MIN_DURATION)
    total = sum(p.days for p in parts)
    if total < norm:
        return _fail(rule, f"Запланировано {total} дн., а ваша норма — {norm} календарных дней.",
                     f"Добавьте ещё {norm - total} дн. к одной из частей отпуска.")
    if total > norm:
        return CheckResult(rule.code, rule.name, WARNING,
                           f"Запланировано {total} дн. — больше нормы ({norm}) на {total - norm} дн.",
                           "Дни сверх нормы потребуют отдельного согласования с HR-специалистом. Уменьшите длительность, если это не нужно.")
    return _ok(rule, f"Запланировано {total} из {norm} дней — норма выбрана полностью.")


def check_max_parts(parts: list[PlannedPart], ctx: CheckContext) -> CheckResult:
    rule = _rule(ctx, MAX_PARTS)
    limit = _value(ctx, MAX_PARTS)
    if len(parts) > limit:
        return _fail(rule, f"Отпуск разделён на {len(parts)} части, а допускается не более {limit}.",
                     f"Объедините части, чтобы их было не больше {limit}.")
    return _ok(rule, f"Частей отпуска: {len(parts)} (допустимо до {limit}).")


def check_min_part_length(parts: list[PlannedPart], ctx: CheckContext) -> CheckResult:
    rule = _rule(ctx, MIN_PART_LENGTH)
    min_len = _value(ctx, MIN_PART_LENGTH)
    longest = max(p.days for p in parts)
    if longest < min_len:
        return _fail(rule, f"Самая длинная часть — {longest} дн. По закону одна из частей должна быть не менее {min_len} дней.",
                     f"Увеличьте одну из частей до {min_len} дней за счёт другой.")
    return _ok(rule, f"Самая длинная часть — {longest} дн. (требуется не менее {min_len}).")


def check_holiday_extension(parts: list[PlannedPart], ctx: CheckContext) -> CheckResult:
    rule = _rule(ctx, HOLIDAY_EXTENSION)
    extended = [(p, h) for p in parts for h in p.holidays]
    if not extended:
        return _ok(rule, "Государственные праздники на период отпуска не выпадают.")
    days = ", ".join(fmt(h) for _, h in extended)
    ends = "; ".join(f"часть с {fmt(p.start)} заканчивается {fmt(p.end)}" for p in parts if p.holidays)
    return _ok(rule, f"На отпуск выпадают праздники ({days}) — они не входят в отпуск, отпуск продлён: {ends}.")


def check_first_vacation(employee: Employee, parts: list[PlannedPart], ctx: CheckContext) -> CheckResult:
    rule = _rule(ctx, FIRST_VACATION)
    months = _value(ctx, FIRST_VACATION)
    earliest = add_months(employee.hire_date, months)
    early = [p for p in parts if p.start < earliest]
    if early:
        return _fail(rule, f"Вы приняты {fmt(employee.hire_date)}. Первый отпуск возможен не ранее чем через {months} мес. работы — с {fmt(earliest)}.",
                     f"Перенесите начало отпуска на {fmt(earliest)} или позже.")
    return _ok(rule, f"Стаж для отпуска достаточен (отпуск доступен с {fmt(earliest)}).")


def check_own_overlap(parts: list[PlannedPart], own: list[BookedPart]) -> CheckResult | None:
    hits = [(b, o) for p in parts for b in own if (o := overlap(p.start, p.end, b.start, b.end))]
    if not hits:
        return None
    text = "; ".join(f"{_absence(b)} {fmt_range(b.start, b.end)}" for b, _ in hits)
    return CheckResult(OWN_OVERLAP, "Пересечение с другими вашими отсутствиями", ERROR,
                       f"На эти даты у вас уже есть заявка: {text}.", "Выберите даты, свободные от других ваших отсутствий.")


def check_sales_limit(employee: Employee, parts: list[PlannedPart], ctx: CheckContext) -> CheckResult | None:
    """No more than N sales managers of the whole company on leave on the same day."""
    if employee.position != SALES_MANAGER:
        return None
    rule = _rule(ctx, CONC_SALES)
    limit = _value(ctx, CONC_SALES)
    booked = [b for b in ctx.booked if (e := ctx.employees.get(b.employee_id)) and e.position == SALES_MANAGER]
    over_days: list[date] = []
    who: dict[int, list[BookedPart]] = {}
    for p in parts:
        for day in _days_range(p.start, p.end):
            off = [b for b in booked if b.start <= day <= b.end]
            if len({b.employee_id for b in off}) + 1 > limit:
                over_days.append(day)
                for b in off:
                    if b not in who.setdefault(b.employee_id, []):
                        who[b.employee_id].append(b)
    if not over_days:
        return _ok(rule, f"Лимит менеджеров по продажам в отпуске одновременно ({limit} чел. по компании) не превышен.")
    periods = ", ".join(fmt_range(s, e) for s, e in _group_ranges(over_days))
    names = "; ".join(f"{ctx.employees[eid].name} ({', '.join(fmt_range(b.start, b.end) for b in bs)})" for eid, bs in who.items())
    free_from = max(b.end for bs in who.values() for b in bs) + timedelta(days=1)
    return _fail(rule, f"В периоды {periods} в компании будет больше {limit} менеджеров по продажам в отсутствии одновременно. Уже запланировали: {names}.",
                 f"Сдвиньте даты, например начните с {fmt(free_from)}, или договоритесь с коллегами.")


def _is_subject(code: str, e: Employee) -> bool:
    return e.position in {
        CONC_TEAM_LEAD: (TEAM_LEAD,),
        CONC_DEPT_HEAD: (DEPT_HEAD,),
        CONC_AREA_HEAD: (AREA_HEAD,),
        CONC_DEPUTY: (DEPUTY_COMMERCE, DEPUTY_OPERATIONS),
        CONC_DIRECTOR: (DIRECTOR,),
    }[code]


def forbidden(code: str, a: Employee, b: Employee, employees: dict[int, Employee]) -> bool:
    """Does rule `code` (formulated for a's position) forbid a and b to be absent at the same time?"""
    if a.id == b.id or not _is_subject(code, a):
        return False
    if code == CONC_TEAM_LEAD:
        return a.department_id is not None and b.department_id == a.department_id and b.position in (TEAM_LEAD, DEPT_HEAD)
    if code == CONC_DEPT_HEAD:
        return a.division_id is not None and b.division_id == a.division_id and b.position in (DEPT_HEAD, AREA_HEAD)
    if code == CONC_AREA_HEAD:
        boss = employees.get(a.manager_id) if a.manager_id else None
        if not boss or boss.position not in (DEPUTY_COMMERCE, DEPUTY_OPERATIONS):
            return False
        return b.id == boss.id or (b.position == AREA_HEAD and b.manager_id == boss.id)
    if code == CONC_DEPUTY:
        return b.position == DIRECTOR
    if code == CONC_DIRECTOR:
        return b.position in (DEPUTY_COMMERCE, DEPUTY_OPERATIONS)
    return False


def check_pair_rule(code: str, employee: Employee, parts: list[PlannedPart], ctx: CheckContext,
                    reported: set) -> CheckResult | None:
    """Position-based "must not overlap" rule.

    Constraints are symmetric: an area head is checked against department heads of the
    division because the department head rule forbids that pair. A rule the employee is only a counterpart of is
    shown only when it finds a conflict not already reported by an earlier rule.
    """
    rule = _rule(ctx, code)
    subject = _is_subject(code, employee)
    conflicts = []
    for b in ctx.booked:
        other = ctx.employees.get(b.employee_id)
        if not other or not (forbidden(code, employee, other, ctx.employees) or forbidden(code, other, employee, ctx.employees)):
            continue
        for p in parts:
            if (o := overlap(p.start, p.end, b.start, b.end)) and (key := (b, o)) not in reported:
                reported.add(key)
                conflicts.append((b, o))
    if not conflicts:
        return _ok(rule, "Пересечений с коллегами, с которыми нельзя отдыхать одновременно, нет.") if subject else None
    text = "; ".join(f"{_who(ctx.employees[b.employee_id])} — {_absence(b)} {fmt_range(b.start, b.end)}, общие даты {fmt_range(*o)}"
                     for b, o in conflicts)
    free_from = max(b.end for b, _ in conflicts) + timedelta(days=1)
    return _fail(rule, f"Пересечение: {text}.",
                 f"Выберите даты без пересечения, например с {fmt(free_from)}, или согласуйте даты с коллегой.")


def check_substitute(employee: Employee, parts: list[PlannedPart], substitute_id: int | None,
                     ctx: CheckContext) -> CheckResult:
    rule = _rule(ctx, SUBSTITUTE)
    if not substitute_id:
        return _fail(rule, "Не указан замещающий коллега — это обязательное поле.", "Выберите замещающего из списка сотрудников.")
    if substitute_id == employee.id:
        return _fail(rule, "Нельзя указать замещающим самого себя.", "Выберите другого сотрудника.")
    sub = ctx.employees.get(substitute_id)
    if not sub:
        return _fail(rule, "Замещающий не найден среди сотрудников.", "Выберите замещающего из списка.")
    conflicts = []
    for p in parts:
        for b in ctx.booked:
            if not (o := overlap(p.start, p.end, b.start, b.end)):
                continue
            if b.employee_id == substitute_id:
                conflicts.append(f"{sub.name} (ваш замещающий) — {_absence(b)} {fmt_range(b.start, b.end)}, общие даты {fmt_range(*o)}")
            elif b.substitute_id == employee.id:
                conflicts.append(f"вы замещаете {ctx.employees[b.employee_id].name} в период {fmt_range(b.start, b.end)}, общие даты {fmt_range(*o)}")
    if conflicts:
        return _fail(rule, f"Замещение невозможно: {'; '.join(conflicts)}.",
                     "Выберите другого замещающего или другие даты.")
    return _ok(rule, f"Замещающий — {sub.name}, в эти даты он(а) на месте; вы никого не замещаете в эти даты.")


def check_summer_limit(parts: list[PlannedPart], ctx: CheckContext) -> CheckResult:
    rule = _rule(ctx, SUMMER_LIMIT)
    limit = _value(ctx, SUMMER_LIMIT)
    start_s, end_s = _param(ctx, SUMMER_LIMIT, "start"), _param(ctx, SUMMER_LIMIT, "end")
    summer_days = []
    for p in parts:
        for d in _days_range(p.start, p.end):
            if d not in p.holidays and month_day(start_s, d.year) <= d <= month_day(end_s, d.year):
                summer_days.append(d)
    window = f"{start_s[3:]}.{start_s[:2]} – {end_s[3:]}.{end_s[:2]}"
    if len(summer_days) > limit:
        return _fail(rule, f"На летний период ({window}) приходится {len(summer_days)} дн. отпуска, а допускается не более {limit}.",
                     f"Перенесите {len(summer_days) - limit} дн. на другое время года.")
    return _ok(rule, f"На летний период ({window}) приходится {len(summer_days)} дн. отпуска (допустимо до {limit}).")


def check_vacation_pay(employee: Employee, pay_amount: float | None, absence_type: str, ctx: CheckContext) -> CheckResult:
    rule = _rule(ctx, VACATION_PAY)
    if pay_amount is None:
        return _ok(rule, "Сумма отпускных не указана (поле необязательное).")
    if pay_amount < 0:
        return _fail(rule, "Сумма отпускных не может быть отрицательной.", "Исправьте сумму или оставьте поле пустым.")
    if absence_type == UNPAID and pay_amount > 0:
        return _fail(rule, "Для отпуска без сохранения зарплаты отпускные не начисляются.", "Оставьте поле суммы пустым.")
    if employee.salary is None:
        return _ok(rule, "Оклад сотрудника не указан — сравнить сумму не с чем.")
    if pay_amount > employee.salary:
        return _fail(rule, f"Сумма отпускных {fmt_money(pay_amount)} больше оклада ({fmt_money(employee.salary)}).",
                     "Укажите сумму не больше оклада или оставьте поле пустым.")
    return _ok(rule, f"Сумма отпускных {fmt_money(pay_amount)} не превышает оклад ({fmt_money(employee.salary)}).")


def check_blackout(parts: list[PlannedPart], ctx: CheckContext) -> CheckResult:
    rule = _rule(ctx, BLACKOUT)
    hits = [(bo, o) for p in parts for bo in ctx.blackouts if (o := overlap(p.start, p.end, bo.start, bo.end))]
    if not hits:
        return _ok(rule, "Отсутствие не попадает в запретные периоды.")
    text = "; ".join(f"«{bo.name}» ({fmt_range(bo.start, bo.end)})" for bo, _ in hits)
    return _fail(rule, f"Отсутствие попадает в запретный период: {text}.",
                 "Перенесите даты так, чтобы они заканчивались до начала или начинались после окончания запретного периода.")


def check_notice_period(parts: list[PartInput] | list[PlannedPart], ctx: CheckContext) -> CheckResult:
    rule = _rule(ctx, NOTICE_PERIOD)
    notice = _value(ctx, NOTICE_PERIOD)
    earliest = ctx.today + timedelta(days=notice)
    late = [p for p in parts if p.start < earliest]
    if late:
        starts = ", ".join(fmt(p.start) for p in late)
        return _fail(rule, f"Заявление нужно подать не позднее чем за {notice} дн. до начала. Начало {starts} — слишком скоро.",
                     f"Выберите дату начала не раньше {fmt(earliest)}.")
    return _ok(rule, f"Заявление подаётся заблаговременно (не менее чем за {notice} дн.).")


def schedule_deadline(start: date, ctx: CheckContext) -> date:
    """Deadline of the half-year schedule that contains `start`."""
    key = "h1" if start.month <= 6 else "h2"
    return month_day(_param(ctx, SCHEDULE_DEADLINE, key), start.year)


def check_schedule_deadline(parts: list[PlannedPart], ctx: CheckContext) -> CheckResult:
    rule = _rule(ctx, SCHEDULE_DEADLINE)
    late = [(p, dl) for p in parts if ctx.today > (dl := schedule_deadline(p.start, ctx))]
    if late:
        text = "; ".join(f"часть с {fmt(p.start)} — {'I' if p.start.month <= 6 else 'II'} полугодие, срок до {fmt(dl)}"
                         for p, dl in late)
        return _fail(rule, f"Заявка в график подана после срока: {text}.",
                     "Заявка будет рассмотрена, но HR-специалист вправе её не включить в график — согласуйте даты с руководителем.")
    deadlines = ", ".join(sorted({fmt(schedule_deadline(p.start, ctx)) for p in parts}))
    return _ok(rule, f"Заявка в график подана в срок (до {deadlines}).")


def check_transfer_notice(moved: list[PartInput], ctx: CheckContext) -> CheckResult:
    rule = _rule(ctx, TRANSFER_NOTICE)
    notice = _value(ctx, TRANSFER_NOTICE)
    first = min(o.start for o in moved)
    last_day = first - timedelta(days=notice)
    if ctx.today > last_day:
        return _fail(rule, f"Перенос согласованного отпуска с {fmt(first)} нужно подать не позднее чем за {notice} дн. "
                           f"до его начала, то есть до {fmt(last_day)} включительно.",
                     "Срок подачи переноса пропущен — обсудите перенос с руководителем и HR-специалистом (возможно исключение).")
    return _ok(rule, f"Перенос подаётся в срок: не позднее {fmt(last_day)} (за {notice} дн. до начала исходного отпуска {fmt(first)}).")


# ----------------------------------------------------------------- engine

def check_request(employee: Employee, parts: list[PartInput], ctx: CheckContext, *,
                  absence_type: str = LABOR, substitute_id: int | None = None, pay_amount: float | None = None,
                  original_parts: list[PartInput] | None = None) -> CheckReport:
    """Validate a request against all enabled rules.

    Other people's absences are taken from ctx.booked (submitted and approved
    requests). The employee's own booked parts of the same absence type are
    ignored (they are replaced by this request, or are the original of a
    transfer); own parts of other types must not overlap.

    `original_parts` turns the request into a transfer of an approved one: the
    notice period applies only to changed parts, plus the transfer deadline.
    """
    input_errors = check_input(parts, original_parts)
    if input_errors:
        return CheckReport([], input_errors)

    planned = plan_parts(parts, ctx, absence_type)
    own = [b for b in ctx.booked if b.employee_id == employee.id and b.absence_type != absence_type]
    others = CheckContext(**{**ctx.__dict__, "booked": [b for b in ctx.booked if b.employee_id != employee.id]})
    is_labor = absence_type == LABOR

    # In a transfer, unchanged parts are already approved: conflicts and the notice
    # period are checked only for the new dates; Labour Code rules and summer — for the whole vacation.
    new_parts: list[PlannedPart] = planned
    moved: list[PartInput] = []
    if original_parts is not None:
        new, moved = changed_parts(parts, original_parts)
        new_parts = [p for p in planned if PartInput(p.start, p.days) in new]

    reported: set = set()
    checks = [
        (MIN_DURATION, is_labor, lambda: check_min_duration(employee, planned, others)),
        (MAX_PARTS, is_labor, lambda: check_max_parts(planned, others)),
        (MIN_PART_LENGTH, is_labor, lambda: check_min_part_length(planned, others)),
        (HOLIDAY_EXTENSION, is_labor, lambda: check_holiday_extension(planned, others)),
        (FIRST_VACATION, is_labor, lambda: check_first_vacation(employee, planned, others)),
        (OWN_OVERLAP, True, lambda: check_own_overlap(new_parts, own)),
        (CONC_SALES, True, lambda: check_sales_limit(employee, new_parts, others)),
        # Rules the employee is the subject of go first, so a mirrored pair is reported once.
        *[(code, True, (lambda c=code: check_pair_rule(c, employee, new_parts, others, reported)))
          for code in sorted(PAIR_RULES, key=lambda c: not _is_subject(c, employee))],
        (SUBSTITUTE, True, lambda: check_substitute(employee, new_parts, substitute_id, others)),
        (SUMMER_LIMIT, is_labor, lambda: check_summer_limit(planned, others)),
        (VACATION_PAY, True, lambda: check_vacation_pay(employee, pay_amount, absence_type, others)),
        (BLACKOUT, True, lambda: check_blackout(new_parts, others)),
        (NOTICE_PERIOD, bool(new_parts), lambda: check_notice_period(new_parts, others)),
        (SCHEDULE_DEADLINE, original_parts is None, lambda: check_schedule_deadline(planned, others)),
        (TRANSFER_NOTICE, bool(moved), lambda: check_transfer_notice(moved, others)),
    ]
    results = []
    for code, applies, run in checks:
        if applies and (code == OWN_OVERLAP or _enabled(ctx, code)) and (res := run()) is not None:
            results.append(res)
    return CheckReport(planned, results)
