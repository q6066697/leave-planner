"""Streamlit UI for the vacation planner: «Сотрудник», «Руководитель» and «HR-специалист» roles."""
from __future__ import annotations

import dataclasses
import io
from datetime import date, datetime, timedelta

import pandas as pd
import plotly.express as px
import streamlit as st

import ai_parser
import rules_engine as re_
import seed
import storage

PLAN_YEAR = seed.PLAN_YEAR
YEAR_START, YEAR_END = date(PLAN_YEAR, 1, 1), date(PLAN_YEAR, 12, 31)

STATUS_ICONS = {re_.OK: "✅", re_.WARNING: "⚠️", re_.ERROR: "❌"}
SEVERITY_LABELS = {re_.ERROR: "Ошибка", re_.WARNING: "Предупреждение"}
CATEGORY_LABELS = {"law": "ТК РБ", "internal": "Внутренняя политика"}
TYPE_LABELS = re_.ABSENCE_TYPES
MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"]
MONTHS_SHORT = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]

st.set_page_config(page_title="Планировщик отпусков", page_icon="🌴", layout="wide")

if not storage.db_exists():
    seed.seed()


# ----------------------------------------------------------------- shared UI

def render_parts(parts: list[re_.PlannedPart]) -> None:
    rows = [{
        "Часть": i,
        "Начало": re_.fmt(p.start),
        "Дней": p.days,
        "Окончание (факт.)": re_.fmt(p.end),
        "Праздники внутри": ", ".join(re_.fmt(h) for h in p.holidays) or "—",
    } for i, p in enumerate(parts, 1)]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def render_results(report: re_.CheckReport, only_problems: bool = False) -> None:
    for r in report.results:
        if only_problems and r.status == re_.OK:
            continue
        text = f"{STATUS_ICONS[r.status]} **{r.rule_name}.** {r.message}"
        if r.hint:
            text += f"  \n💡 _Как исправить:_ {r.hint}"
        if r.status == re_.ERROR:
            st.error(text)
        elif r.status == re_.WARNING:
            st.warning(text)
        else:
            st.success(text)


def parts_text(parts: list[re_.PartInput]) -> str:
    return "; ".join(f"с {re_.fmt(p.start)} на {p.days} дн." for p in sorted(parts, key=lambda p: p.start))


def original_parts(req: storage.Request) -> list[re_.PartInput] | None:
    if not req.is_transfer:
        return None
    original = storage.get_request(req.original_request_id)
    return original.parts if original else None


def evaluate(req: storage.Request, ctx: re_.CheckContext) -> re_.CheckReport:
    """Re-check a stored request against current rules; deadlines count from the submission date.

    An approved request is compared only with other regularly approved ones, so a later conflicting
    request is highlighted instead of the one HR has already approved.
    """
    rctx = dataclasses.replace(ctx, today=req.created_at.date())
    if req.status in storage.APPROVED_STATUSES:
        rctx.booked = [b for b in ctx.booked if b.status == storage.STATUS_APPROVED]
    return re_.check_request(ctx.employees[req.employee_id], req.parts, rctx, absence_type=req.absence_type,
                             substitute_id=req.substitute_id, pay_amount=req.pay_amount,
                             original_parts=original_parts(req))


def request_caption(req: storage.Request, by_id: dict) -> str:
    e = by_id[req.employee_id]
    sub = by_id.get(req.substitute_id)
    pay = re_.fmt_money(req.pay_amount) if req.pay_amount is not None else "—"
    return (f"{TYPE_LABELS[req.absence_type]} · подана {req.created_at:%d.%m.%Y} · {e['position']}, {e['unit']} · "
            f"руководитель: {e['manager'] or '— (сразу HR-специалисту)'} · замещающий: {sub['name'] if sub else '—'} · "
            f"сумма отпускных: {pay} (оклад {re_.fmt_money(e['salary']) if e['salary'] else '—'})")


def render_request(req: storage.Request, report: re_.CheckReport, by_id: dict) -> None:
    st.caption(request_caption(req, by_id))
    if orig := original_parts(req):
        st.info(f"🔁 Перенос согласованного отсутствия: было {parts_text(orig)} → стало {parts_text(req.parts)}")
    if report.parts:
        render_parts(report.parts)
    if report.errors or report.warnings:
        render_results(report, only_problems=True)
    else:
        st.success("Нарушений нет.")
    if req.manager_comment:
        st.info(f"💬 Комментарий руководителя: {req.manager_comment}")
    if req.hr_comment:
        st.info(f"💬 Комментарий HR-специалиста: {req.hr_comment}")


def employee_label(e: dict) -> str:
    return f"{e['name']} — {e['position']}, {e['unit']}"


# ------------------------------------------------------------- employee role

DEFAULT_PARTS = [re_.PartInput(date(PLAN_YEAR, 9, 6), 14), re_.PartInput(date(PLAN_YEAR, 10, 4), 10)]


def _load_form(emp_id: int, kind: str) -> None:
    req = storage.latest_request(emp_id, PLAN_YEAR, kind)
    parts = req.parts if req else (DEFAULT_PARTS if kind == re_.LABOR else [re_.PartInput(date(PLAN_YEAR, 3, 1), 5)])
    st.session_state.n_parts = 1 if len(parts) == 1 else 2
    for i in (1, 2):
        p = parts[i - 1] if i <= len(parts) else DEFAULT_PARTS[1]
        st.session_state[f"p{i}_start"] = p.start
        st.session_state[f"p{i}_days"] = p.days
    st.session_state.substitute = req.substitute_id if req else None
    st.session_state.pay = req.pay_amount if req else None
    st.session_state.form_key = (emp_id, kind)
    st.session_state.transfer_mode = False
    st.session_state.pop("report", None)


def _on_parse(norm: int, max_parts: int, min_part: int, today: date) -> None:
    st.session_state.pop("ai_error", None)
    st.session_state.pop("ai_comment", None)
    try:
        suggestion = ai_parser.parse_wish(st.session_state.get("ai_text", ""), PLAN_YEAR, norm,
                                          today, max_parts, min_part)
    except ai_parser.AIParserError as exc:
        st.session_state.ai_error = str(exc)
        return
    parts = suggestion.parts[:2]
    st.session_state.n_parts = len(parts)
    for i, p in enumerate(parts, 1):
        st.session_state[f"p{i}_start"] = min(max(p.start, YEAR_START), YEAR_END)
        st.session_state[f"p{i}_days"] = p.days
    st.session_state.ai_comment = suggestion.comment or "Даты подставлены в форму — нажмите «Проверить»."
    st.session_state.pop("report", None)


def employee_page(today: date) -> None:
    st.title(f"🌴 Мои отсутствия на {PLAN_YEAR} год")
    employees = storage.list_employees()
    by_id = {e["id"]: e for e in employees}
    emp_id = st.selectbox("Выберите себя", list(by_id), format_func=lambda i: employee_label(by_id[i]))
    kind = st.radio("Тип отсутствия", list(TYPE_LABELS), format_func=TYPE_LABELS.get, horizontal=True)
    if st.session_state.get("form_key") != (emp_id, kind):
        _load_form(emp_id, kind)

    ctx = storage.build_context(PLAN_YEAR, today)
    emp_row, employee = by_id[emp_id], ctx.employees[emp_id]
    is_labor = kind == re_.LABOR
    norm = employee.annual_days or ctx.rules[re_.MIN_DURATION].value or 24

    req = storage.latest_request(emp_id, PLAN_YEAR, kind)
    approved = storage.approved_request(emp_id, PLAN_YEAR, kind)
    c = st.columns(5)
    c[0].metric("Норма отпуска", f"{norm} дн." if is_labor else "—")
    c[1].metric("Дата приёма", re_.fmt(employee.hire_date))
    c[2].metric("Оклад", re_.fmt_money(employee.salary) if employee.salary else "—")
    c[3].metric("Руководитель", emp_row["manager"] or "— (сразу HR-специалисту)")
    c[4].metric("Статус заявки", storage.STATUS_LABELS[req.status] if req else "Не подана")
    if req and req.manager_comment:
        st.info(f"💬 Комментарий руководителя: {req.manager_comment}")
    if req and req.hr_comment:
        st.info(f"💬 Комментарий HR-специалиста: {req.hr_comment}")

    # A pending or returned transfer keeps pointing to the approved original.
    pending_transfer = bool(req and req.is_transfer and req.status not in storage.APPROVED_STATUSES)
    transfer_of = approved if approved and (pending_transfer or st.session_state.get("transfer_mode")) else None
    locked = bool(req and req.status in storage.APPROVED_STATUSES) and not transfer_of
    if locked:
        st.success(f"Согласовано: {parts_text(req.parts)}.")
        if st.button("🔁 Перенести согласованные даты"):
            st.session_state.transfer_mode = True
            st.rerun()
    if transfer_of:
        notice = ctx.rules[re_.TRANSFER_NOTICE].value or 14
        st.info(f"🔁 Режим переноса. Согласовано: {parts_text(transfer_of.parts)}. Укажите новые даты — "
                f"перенос подаётся не позднее чем за {notice} дн. до начала исходного отпуска. "
                "До согласования переноса действуют прежние даты.")

    if flash := st.session_state.pop("flash", None):
        st.success(flash)

    if is_labor:
        with st.expander("✨ Опишите пожелание словами", expanded=False):
            available = ai_parser.is_available()
            st.text_area("Пожелание", key="ai_text", placeholder="Например: две недели в сентябре после 5-го и остаток в начале октября",
                         disabled=not available, label_visibility="collapsed")
            if not available:
                st.info(ai_parser.UNAVAILABLE_MESSAGE)
            rules = ctx.rules
            st.button("Разобрать", disabled=not available or locked, on_click=_on_parse,
                      args=(norm, rules[re_.MAX_PARTS].value or 2, rules[re_.MIN_PART_LENGTH].value or 14, today))
            if err := st.session_state.get("ai_error"):
                st.error(err)
            if comment := st.session_state.get("ai_comment"):
                st.success(f"🤖 {comment}")
            st.caption("AI только предлагает даты. Проверку по закону и внутренней политике всегда выполняет движок правил.")

    st.subheader("Даты")
    n_parts = st.radio("Количество частей", [1, 2], key="n_parts", horizontal=True, disabled=locked) if is_labor else 1
    parts: list[re_.PartInput] = []
    holidays = ctx.holidays if is_labor and ctx.rules[re_.HOLIDAY_EXTENSION].enabled else frozenset()
    for i in range(1, n_parts + 1):
        a, b, col = st.columns([2, 1, 2])
        label = f"Часть {i}: начало" if is_labor else "Начало"
        start = a.date_input(label, key=f"p{i}_start", min_value=YEAR_START, max_value=YEAR_END,
                             format="DD.MM.YYYY", disabled=locked)
        days = b.number_input("Календарных дней", key=f"p{i}_days", min_value=1, max_value=60, step=1, disabled=locked)
        end, extra = re_.compute_end_date(start, int(days), holidays)
        note = f" (+{len(extra)} дн. за праздники)" if extra else ""
        col.markdown(f"<br>Окончание: **{re_.fmt(end)}**{note}", unsafe_allow_html=True)
        parts.append(re_.PartInput(start, int(days)))
    if is_labor:
        st.caption(f"Итого: {sum(p.days for p in parts)} из {norm} дней")

    a, b = st.columns(2)
    others = [e["id"] for e in employees if e["id"] != emp_id]
    substitute_id = a.selectbox("Замещающий коллега *", [None, *others], key="substitute", disabled=locked,
                                format_func=lambda i: "— выберите —" if i is None else employee_label(by_id[i]))
    pay = b.number_input("Сумма отпускных (необязательно)", key="pay", min_value=0.0, step=50.0, format="%.2f",
                         disabled=locked or kind == re_.UNPAID,
                         help="Не больше оклада. Для отпуска без сохранения зарплаты не заполняется.")
    pay = None if kind == re_.UNPAID else pay

    options = dict(absence_type=kind, substitute_id=substitute_id, pay_amount=pay,
                   original_parts=transfer_of.parts if transfer_of else None)
    signature = (emp_id, kind, tuple((p.start, p.days) for p in parts), substitute_id, pay,
                 transfer_of.id if transfer_of else None)
    b1, b2, _ = st.columns([1, 1, 4])
    if b1.button("🔍 Проверить", type="primary", disabled=locked):
        st.session_state.report = (signature, re_.check_request(employee, parts, ctx, **options))

    saved = st.session_state.get("report")
    report = saved[1] if saved and saved[0] == signature else None
    can_send = report is not None and not report.has_errors and not locked and substitute_id is not None
    if b2.button("📨 Отправить", disabled=not can_send,
                 help="Сначала нажмите «Проверить». Отправка возможна, если нет ошибок (предупреждения допустимы)."):
        storage.create_request(emp_id, PLAN_YEAR, parts, absence_type=kind, substitute_id=substitute_id, pay_amount=pay,
                               original_request_id=transfer_of.id if transfer_of else None,
                               created_at=datetime.combine(today, datetime.now().time()))
        st.session_state.pop("report", None)
        st.session_state.transfer_mode = False
        target = f"руководителю ({emp_row['manager']})" if emp_row["manager"] else "HR-специалисту"
        st.session_state.flash = f"Заявка отправлена {target} на согласование."
        st.rerun()

    if report:
        if report.has_errors:
            st.markdown(f"### Результат проверки: ❌ ошибок — {len(report.errors)}, ⚠️ предупреждений — {len(report.warnings)}")
        else:
            st.markdown(f"### Результат проверки: ✅ можно отправлять (предупреждений: {len(report.warnings)})")
        if report.parts:
            render_parts(report.parts)
        render_results(report)
    elif saved:
        st.caption("Данные заявки изменились — нажмите «Проверить» ещё раз.")


# -------------------------------------------------------------- manager role

def manager_page(today: date) -> None:
    st.title("👔 Руководитель: заявки подчинённых")
    employees = storage.list_employees()
    by_id = {e["id"]: e for e in employees}
    managers = [e["id"] for e in employees if any(x["manager_id"] == e["id"] for x in employees)]
    mgr_id = st.selectbox("Выберите себя", managers, format_func=lambda i: employee_label(by_id[i]))
    team = {e["id"] for e in employees if e["manager_id"] == mgr_id}

    ctx = storage.build_context(PLAN_YEAR, today)
    requests = [r for r in storage.list_requests(PLAN_YEAR) if r.employee_id in team]
    reports = {r.id: evaluate(r, ctx) for r in requests}
    pending = [r for r in requests if r.status == storage.STATUS_SUBMITTED]

    c = st.columns(4)
    c[0].metric("Подчинённых", len(team))
    c[1].metric("Ждут вашего решения", len(pending))
    c[2].metric("У HR-специалиста", sum(r.status == storage.STATUS_MANAGER_APPROVED for r in requests))
    c[3].metric("Согласовано", sum(r.status in storage.APPROVED_STATUSES for r in requests))

    st.markdown("#### Ждут вашего решения")
    st.caption("Согласованная вами заявка уходит HR-специалисту на проверку по внутренней политике. Ошибки видны и HR-специалисту: "
               "он(а) решает, согласовать ли исключение.")
    if not pending:
        st.success("Новых заявок нет.")
    for req in pending:
        report, e = reports[req.id], by_id[req.employee_id]
        icon = "❌" if report.has_errors else "⚠️" if report.warnings else "✅"
        with st.expander(f"{icon} {e['name']} — {TYPE_LABELS[req.absence_type]} — {parts_text(req.parts)}"):
            render_request(req, report, by_id)
            comment = st.text_area("Комментарий руководителя", key=f"mcomment_{req.id}",
                                   placeholder="Обязателен при возврате")
            a, b, _ = st.columns([1, 1, 2])
            if a.button("✅ Согласовать и передать HR-специалисту", key=f"mapprove_{req.id}"):
                storage.manager_decision(req.id, True, comment.strip() or None)
                st.rerun()
            if b.button("↩️ Вернуть с комментарием", key=f"mreturn_{req.id}"):
                if not comment.strip():
                    st.error("Укажите комментарий: что нужно исправить.")
                else:
                    storage.manager_decision(req.id, False, comment.strip())
                    st.rerun()

    st.markdown("#### Все заявки команды")
    rows = [{"Сотрудник": by_id[r.employee_id]["name"], "Должность": by_id[r.employee_id]["position"],
             "Тип": TYPE_LABELS[r.absence_type], "Даты": parts_text(r.parts), "Статус": storage.STATUS_LABELS[r.status],
             "Ошибок": len(reports[r.id].errors)} for r in requests]
    filed = {r.employee_id for r in requests if r.absence_type == re_.LABOR}
    rows += [{"Сотрудник": by_id[i]["name"], "Должность": by_id[i]["position"], "Тип": TYPE_LABELS[re_.LABOR],
              "Даты": "—", "Статус": "Не подана", "Ошибок": 0} for i in sorted(team - filed)]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


# ------------------------------------------------------------------ HR role

def request_state(req: storage.Request, report: re_.CheckReport) -> str:
    if req.status == storage.STATUS_EXCEPTION:
        return "Исключение"
    if req.status == storage.STATUS_RETURNED:
        return "На доработке"
    if report.has_errors:
        return "Нарушения / конфликт"
    if report.warnings:
        return "Предупреждения"
    if req.status == storage.STATUS_APPROVED:
        return "Согласована"
    return "У HR-специалиста, без нарушений" if req.status == storage.STATUS_MANAGER_APPROVED else "У руководителя, без нарушений"


STATE_COLORS = {
    "Согласована": "#2e7d32",
    "У HR-специалиста, без нарушений": "#1e88e5",
    "У руководителя, без нарушений": "#90caf9",
    "Предупреждения": "#fb8c00",
    "Нарушения / конфликт": "#e53935",
    "Исключение": "#8e24aa",
    "На доработке": "#9e9e9e",
}


def hr_summary(employees, requests, reports) -> None:
    active = [r for r in requests if r.status in storage.ACTIVE_STATUSES]
    c = st.columns(6)
    c[0].metric("Сотрудников", len(employees))
    c[1].metric("Заявок в работе", len(active))
    c[2].metric("У руководителей", sum(r.status == storage.STATUS_SUBMITTED for r in active))
    c[3].metric("Ждут HR-специалиста", sum(r.status == storage.STATUS_MANAGER_APPROVED for r in active))
    c[4].metric("С ошибками", sum(reports[r.id].has_errors for r in active))
    c[5].metric("Согласовано", sum(r.status in storage.APPROVED_STATUSES for r in active))

    labor = [r for r in requests if r.absence_type == re_.LABOR]
    filed = {r.employee_id for r in labor if r.status in storage.ACTIVE_STATUSES}
    returned = {r.employee_id for r in labor if r.status == storage.STATUS_RETURNED}
    left, right = st.columns(2)
    with left:
        st.markdown("#### Кто ещё не подал трудовой отпуск в график")
        missing = [e for e in employees if e["id"] not in filed]
        if missing:
            st.dataframe(pd.DataFrame([{
                "Сотрудник": e["name"], "Должность": e["position"], "Подразделение": e["unit"],
                "Состояние": "На доработке" if e["id"] in returned else "Не подавал(а)",
            } for e in missing]), hide_index=True, width="stretch")
        else:
            st.success("Все сотрудники подали заявки.")
    with right:
        st.markdown("#### Нарушения по правилам")
        counts: dict[str, list[int]] = {}
        for r in active:
            for res in reports[r.id].results:
                if res.status != re_.OK:
                    counts.setdefault(res.rule_name, [0, 0])[0 if res.status == re_.ERROR else 1] += 1
        if counts:
            st.dataframe(pd.DataFrame([{"Правило": k, "Ошибок": v[0], "Предупреждений": v[1]} for k, v in counts.items()]),
                         hide_index=True, width="stretch")
        else:
            st.success("Нарушений нет.")

    st.markdown("#### По подразделениям")
    rows = {}
    for e in employees:
        row = rows.setdefault(e["unit"], {"Подразделение": e["unit"], "Сотрудников": 0, "Подали отпуск": 0,
                                          "Согласовано": 0, "С ошибками": 0})
        row["Сотрудников"] += 1
        mine = [r for r in active if r.employee_id == e["id"]]
        row["Подали отпуск"] += e["id"] in filed
        row["Согласовано"] += sum(r.status in storage.APPROVED_STATUSES for r in mine)
        row["С ошибками"] += sum(reports[r.id].has_errors for r in mine)
    st.dataframe(pd.DataFrame(list(rows.values())), hide_index=True, width="stretch")


def hr_calendar(employees, requests, reports, ctx) -> None:
    by_id = {e["id"]: e for e in employees}
    order_index = {e["id"]: i for i, e in enumerate(employees)}
    rows = []
    for req in requests:
        report = reports[req.id]
        e = by_id[req.employee_id]
        problems = "<br>".join(f"{STATUS_ICONS[r.status]} {r.rule_name}" for r in report.results if r.status != re_.OK) or "—"
        for p in report.parts:
            rows.append({
                "Сотрудник": f"{e['unit']} · {e['name']} ({e['position']})", "order": order_index[e["id"]],
                "Начало": p.start, "Конец": p.end + timedelta(days=1), "Окончание": re_.fmt(p.end),
                "Дней": p.days, "Тип": TYPE_LABELS[req.absence_type] + (" (перенос)" if req.is_transfer else ""),
                "Состояние": request_state(req, report), "Статус": storage.STATUS_LABELS[req.status], "Замечания": problems,
            })
    if not rows:
        st.info("Заявок пока нет.")
        return
    df = pd.DataFrame(rows).sort_values("order")
    order = list(dict.fromkeys(df["Сотрудник"]))
    fig = px.timeline(df, x_start="Начало", x_end="Конец", y="Сотрудник", color="Состояние", pattern_shape="Тип",
                      color_discrete_map=STATE_COLORS, category_orders={"Сотрудник": order},
                      hover_data={"Окончание": True, "Дней": True, "Статус": True, "Замечания": True,
                                  "Конец": False, "Сотрудник": False})
    for b in ctx.blackouts:
        fig.add_vrect(x0=b.start, x1=b.end + timedelta(days=1), fillcolor="#757575", opacity=0.15, line_width=0,
                      annotation_text=b.name, annotation_position="top left", annotation_font_size=10)
    for h in ctx.holidays:
        fig.add_vline(x=pd.Timestamp(h).value / 1e6, line_dash="dot", line_color="#bdbdbd", line_width=1)
    fig.update_layout(height=140 + 30 * len(order), xaxis_range=[YEAR_START, YEAR_END + timedelta(days=1)],
                      legend_title_text="", margin=dict(l=10, r=10, t=40, b=10))
    fig.update_yaxes(title=None, autorange="reversed")
    # Plotly formats dates in English only, so month labels are set explicitly.
    ticks = [date(PLAN_YEAR, m, 1) for m in range(1, 13)] + [date(PLAN_YEAR + 1, 1, 1)]
    fig.update_xaxes(title=None, tickvals=ticks, ticktext=[MONTHS_SHORT[d.month - 1] for d in ticks])
    st.plotly_chart(fig, width="stretch")
    st.caption("Серые полосы — запретные периоды, пунктир — государственные праздники, штриховка — тип отсутствия. "
               "Красным подсвечены заявки с нарушениями и пересечениями (наведите курсор, чтобы увидеть детали).")


def hr_requests(employees, requests, reports) -> None:
    by_id = {e["id"]: e for e in employees}
    labels = storage.STATUS_LABELS
    statuses = st.multiselect("Статусы", [s for s in labels if s not in (storage.STATUS_REPLACED, storage.STATUS_TRANSFERRED)],
                              default=[storage.STATUS_MANAGER_APPROVED], format_func=labels.get)
    only_problems = st.toggle("Только с нарушениями", value=False)
    shown = [r for r in requests if r.status in statuses and (not only_problems or reports[r.id].has_errors or reports[r.id].warnings)]
    shown.sort(key=lambda r: (not reports[r.id].has_errors, r.created_at))
    if not shown:
        st.info("Нет заявок по выбранному фильтру.")
    for req in shown:
        report, e = reports[req.id], by_id[req.employee_id]
        icon = "❌" if report.has_errors else "⚠️" if report.warnings else "✅"
        kind = TYPE_LABELS[req.absence_type] + (", перенос" if req.is_transfer else "")
        title = (f"{icon} {e['name']} ({e['position']}) — {kind} — {labels[req.status]} "
                 f"(ошибок: {len(report.errors)}, предупреждений: {len(report.warnings)})")
        with st.expander(title):
            render_request(req, report, by_id)
            if req.status != storage.STATUS_MANAGER_APPROVED:
                if req.status == storage.STATUS_SUBMITTED:
                    st.caption("Заявка ещё у руководителя — HR-специалист принимает решение после него.")
                continue
            comment = st.text_area("Комментарий HR-специалиста", key=f"comment_{req.id}",
                                   placeholder="Обязателен для возврата и согласования как исключения")
            a, b, c = st.columns(3)
            if a.button("✅ Согласовать", key=f"approve_{req.id}", disabled=report.has_errors,
                        help="Недоступно при ошибках — используйте «Согласовать исключение»." if report.has_errors else None):
                storage.set_request_status(req.id, storage.STATUS_APPROVED, comment.strip() or None)
                st.rerun()
            if b.button("↩️ Вернуть на доработку", key=f"return_{req.id}"):
                if not comment.strip():
                    st.error("Укажите комментарий: что нужно исправить.")
                else:
                    storage.set_request_status(req.id, storage.STATUS_RETURNED, comment.strip())
                    st.rerun()
            if c.button("⭐ Согласовать исключение", key=f"exception_{req.id}"):
                if not comment.strip():
                    st.error("Для исключения комментарий обязателен — он сохраняется в журнал.")
                else:
                    violations = [f"{r.rule_name}: {r.message}" for r in report.results if r.status != re_.OK]
                    storage.approve_as_exception(req.id, comment.strip(), violations)
                    st.rerun()


def accounting_frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([{
        "ФИО": r["name"], "Тип отсутствия": r["type"], "Начало": re_.fmt(r["start"]), "Окончание": re_.fmt(r["end"]),
        "Дней": r["days"], "Сумма отпускных": r["pay_amount"],
    } for r in rows], columns=["ФИО", "Тип отсутствия", "Начало", "Окончание", "Дней", "Сумма отпускных"])


def to_excel(df: pd.DataFrame, sheet: str) -> bytes | None:
    try:
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name=sheet)
        return buffer.getvalue()
    except ImportError:  # openpyxl is optional: CSV is always available
        return None


def hr_accounting(today: date) -> None:
    st.markdown("#### Список для бухгалтерии")
    st.caption("Согласованные отсутствия (включая исключения), которые начинаются в следующем месяце "
               "относительно текущей даты в сайдбаре.")
    if st.button("📄 Список на следующий месяц", type="primary"):
        st.session_state.accounting = True
    if not st.session_state.get("accounting"):
        return
    rows, month = storage.next_month_absences(PLAN_YEAR, today)
    title = f"{MONTHS[month.month - 1]} {month.year}"
    st.markdown(f"**Отсутствия, начинающиеся в месяце: {title}** — {len(rows)}")
    df = accounting_frame(rows)
    if df.empty:
        st.info("В следующем месяце согласованных отсутствий нет. Для демо поменяйте текущую дату в сайдбаре, например на 20.06.2027.")
        return
    st.dataframe(df, hide_index=True, width="stretch",
                 column_config={"Сумма отпускных": st.column_config.NumberColumn(format="%.2f")})
    name = f"otsutstviya_{month:%Y_%m}"
    a, b, _ = st.columns([1, 1, 3])
    a.download_button("⬇️ CSV", df.to_csv(sep=";", index=False).encode("utf-8-sig"), f"{name}.csv", "text/csv")
    if (xlsx := to_excel(df, title)) is not None:
        b.download_button("⬇️ Excel", xlsx, f"{name}.xlsx",
                          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    else:
        b.caption("Для Excel установите openpyxl.")


def _reset_editor(key: str) -> None:
    st.session_state.pop(key, None)
    st.session_state.rules_flash = "Изменения сохранены и уже применяются к проверке."
    st.rerun()


def _to_date(value) -> date | None:
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).date()


def _md(d: date) -> str:
    return d.strftime("%m-%d")


def hr_rules() -> None:
    if flash := st.session_state.pop("rules_flash", None):
        st.success(flash)

    st.markdown("#### Правила проверки")
    st.caption("Правила ТК РБ применяются только к трудовому отпуску. Правила одновременности, замещения и сроков подачи — "
               "ко всем типам отсутствия.")
    rules = storage.list_rules()
    df = pd.DataFrame([{
        "code": r["code"], "Тип": CATEGORY_LABELS[r["category"]], "Правило": r["name"], "Вкл.": bool(r["enabled"]),
        "Параметр": r["value_label"] or "—", "Значение": r["value"], "При нарушении": SEVERITY_LABELS[r["severity"]],
        "Описание": r["description"],
    } for r in rules]).set_index("code")
    edited = st.data_editor(
        df, key="rules_editor", width="stretch", hide_index=True,
        disabled=["Тип", "Правило", "Параметр", "Описание"],
        column_config={
            "Значение": st.column_config.NumberColumn(min_value=0, max_value=366, step=1),
            "При нарушении": st.column_config.SelectboxColumn(options=list(SEVERITY_LABELS.values()), required=True),
            "Описание": st.column_config.TextColumn(width="large"),
        },
    )
    if st.button("💾 Сохранить правила"):
        severity_codes = {v: k for k, v in SEVERITY_LABELS.items()}
        for code, row in edited.iterrows():
            has_value = df.loc[code, "Параметр"] != "—"
            value = int(row["Значение"]) if has_value and not pd.isna(row["Значение"]) else None
            if has_value and value is None:
                value = re_.DEFAULT_RULES[code].value
            storage.update_rule(code, bool(row["Вкл."]), value, severity_codes[row["При нарушении"]])
        _reset_editor("rules_editor")

    st.markdown("#### Даты периодов")
    st.caption("Год в датах не важен — используются только день и месяц.")
    params = {r["code"]: r["params"] for r in rules}
    summer, deadline = params[re_.SUMMER_LIMIT], params[re_.SCHEDULE_DEADLINE]
    c = st.columns(4)
    s_start = c[0].date_input("Лето: начало", re_.month_day(summer["start"], PLAN_YEAR), format="DD.MM.YYYY")
    s_end = c[1].date_input("Лето: окончание", re_.month_day(summer["end"], PLAN_YEAR), format="DD.MM.YYYY")
    h1 = c[2].date_input("График на I полугодие — до", re_.month_day(deadline["h1"], PLAN_YEAR), format="DD.MM.YYYY")
    h2 = c[3].date_input("График на II полугодие — до", re_.month_day(deadline["h2"], PLAN_YEAR), format="DD.MM.YYYY")
    if st.button("💾 Сохранить даты периодов"):
        if _md(s_start) > _md(s_end):
            st.error("Начало летнего периода должно быть раньше окончания (в пределах одного года).")
        else:
            storage.update_rule_params(re_.SUMMER_LIMIT, {"start": _md(s_start), "end": _md(s_end)})
            storage.update_rule_params(re_.SCHEDULE_DEADLINE, {"h1": _md(h1), "h2": _md(h2)})
            st.session_state.rules_flash = "Изменения сохранены и уже применяются к проверке."
            st.rerun()

    left, right = st.columns(2)
    with left:
        st.markdown("#### Государственные праздники")
        st.caption("Праздники не входят в дни трудового отпуска — отпуск продлевается. Дату Радуницы сверьте с официальным календарём.")
        hdf = pd.DataFrame(storage.list_holidays()).rename(columns={"date": "Дата", "name": "Название"})
        hedited = st.data_editor(hdf, key="holiday_editor", num_rows="dynamic", hide_index=True, width="stretch",
                                 column_config={"Дата": st.column_config.DateColumn(format="DD.MM.YYYY", required=True)})
        if st.button("💾 Сохранить праздники"):
            rows = [(d, str(r["Название"] or "Праздник")) for _, r in hedited.iterrows() if (d := _to_date(r["Дата"]))]
            storage.replace_holidays(rows)
            _reset_editor("holiday_editor")
    with right:
        st.markdown("#### Запретные периоды")
        st.caption("Например, закрытие квартала — отсутствие не должно пересекаться с этими датами.")
        bdf = pd.DataFrame(storage.list_blackouts()).rename(columns={"start": "Начало", "end": "Окончание", "name": "Название"})
        bedited = st.data_editor(bdf, key="blackout_editor", num_rows="dynamic", hide_index=True, width="stretch",
                                 column_config={"Начало": st.column_config.DateColumn(format="DD.MM.YYYY", required=True),
                                                "Окончание": st.column_config.DateColumn(format="DD.MM.YYYY", required=True)})
        if st.button("💾 Сохранить запретные периоды"):
            rows, bad = [], False
            for _, r in bedited.iterrows():
                s, e = _to_date(r["Начало"]), _to_date(r["Окончание"])
                if s and e:
                    bad |= s > e
                    rows.append((s, e, str(r["Название"] or "Запретный период")))
            if bad:
                st.error("Дата начала периода не может быть позже даты окончания.")
            else:
                storage.replace_blackouts(rows)
                _reset_editor("blackout_editor")


def hr_journal() -> None:
    log = storage.list_exceptions()
    if not log:
        st.info("Исключений пока не было.")
        return
    st.dataframe(pd.DataFrame([{
        "Дата": x["created_at"].replace("T", " "), "Сотрудник": x["employee"], "Кто согласовал": x["author"],
        "Комментарий": x["comment"], "Нарушения на момент согласования": x["violations"],
    } for x in log]), hide_index=True, width="stretch")


def hr_page(today: date) -> None:
    st.title(f"🗂️ HR-специалист: график отсутствий на {PLAN_YEAR} год")
    ctx = storage.build_context(PLAN_YEAR, today)
    employees = storage.list_employees()
    requests = storage.list_requests(PLAN_YEAR)
    reports = {r.id: evaluate(r, ctx) for r in requests}

    tabs = st.tabs(["📊 Сводка", "📅 Календарь", "📋 Заявки", "💰 Бухгалтерия", "⚙️ Правила", "📝 Журнал исключений"])
    with tabs[0]:
        hr_summary(employees, requests, reports)
    with tabs[1]:
        hr_calendar(employees, requests, reports, ctx)
    with tabs[2]:
        hr_requests(employees, requests, reports)
    with tabs[3]:
        hr_accounting(today)
    with tabs[4]:
        hr_rules()
    with tabs[5]:
        hr_journal()


# ---------------------------------------------------------------------- main

with st.sidebar:
    st.header("Планировщик отпусков")
    role = st.radio("Роль", ["Сотрудник", "Руководитель", "HR-специалист"])
    today = st.date_input("Текущая дата (для демо)", date.today(), format="DD.MM.YYYY", key="today",
                          help="От неё считаются сроки подачи и «следующий месяц» для бухгалтерии.")
    st.caption(f"Год планирования: {PLAN_YEAR}")
    st.caption("AI-разбор: " + ("подключён" if ai_parser.is_available() else "выключен (нет OPENAI_API_KEY)"))
    st.divider()
    if st.button("🔄 Сбросить демо-данные"):
        seed.seed()
        for key in list(st.session_state):
            del st.session_state[key]
        st.rerun()

if role == "Сотрудник":
    employee_page(today)
elif role == "Руководитель":
    manager_page(today)
else:
    hr_page(today)
