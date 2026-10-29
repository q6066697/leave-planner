from dataclasses import replace
from datetime import date

import pytest

import rules_engine as re_
from rules_engine import (AREA_HEAD, DEPT_HEAD, DEPUTY_COMMERCE, DEPUTY_OPERATIONS, DIRECTOR, HR, SALES_MANAGER,
                          TEAM_LEAD as TL, BookedPart, Blackout, CheckContext, Employee, PartInput)

TODAY = date(2026, 10, 28)
HOLIDAYS = frozenset({date(2027, 5, 1), date(2027, 5, 9), date(2027, 5, 11), date(2027, 7, 3)})
HIRED = date(2020, 1, 1)

# Org structure: divisions 1 and 2; departments 10, 11 (division 1) and 20 (division 2).
DIR = Employee(1, "Директоров", DIRECTOR, HIRED)
DEP_C = Employee(2, "Коммерцев", DEPUTY_COMMERCE, HIRED, manager_id=1)
DEP_O = Employee(3, "Операционов", DEPUTY_OPERATIONS, HIRED, manager_id=1)
AH_A = Employee(4, "Направленко А", AREA_HEAD, HIRED, division_id=1, manager_id=2)
AH_B = Employee(5, "Направленко Б", AREA_HEAD, HIRED, division_id=2, manager_id=2)  # same deputy, other division
AH_C = Employee(6, "Направленко В", AREA_HEAD, HIRED, division_id=1, manager_id=3)  # same division, other boss (operations deputy)
DH_A = Employee(7, "Отделов А", DEPT_HEAD, HIRED, department_id=10, division_id=1, manager_id=4)
DH_B = Employee(8, "Отделов Б", DEPT_HEAD, HIRED, department_id=11, division_id=1, manager_id=4)
DH_C = Employee(9, "Отделов В", DEPT_HEAD, HIRED, department_id=20, division_id=2, manager_id=5)
TL_A = Employee(10, "Группин А", TL, HIRED, department_id=10, division_id=1, manager_id=7)
TL_B = Employee(11, "Группин Б", TL, HIRED, department_id=10, division_id=1, manager_id=7)
TL_C = Employee(12, "Группин В", TL, HIRED, department_id=11, division_id=1, manager_id=8)
SM_A = Employee(13, "Продажин А", SALES_MANAGER, HIRED, department_id=10, division_id=1, manager_id=10, salary=1500)
SM_B = Employee(14, "Продажин Б", SALES_MANAGER, HIRED, department_id=11, division_id=1, manager_id=12, salary=1500)
SM_C = Employee(15, "Продажин В", SALES_MANAGER, HIRED, department_id=20, division_id=2, manager_id=9, salary=1500)
NEWBIE = Employee(16, "Новичок", SALES_MANAGER, date(2026, 9, 15), department_id=20, division_id=2, manager_id=9)
HR_E = Employee(17, "Кадрова", HR, HIRED, manager_id=1)
SUB = Employee(18, "Замещаев", SALES_MANAGER, HIRED, department_id=20, division_id=2, manager_id=9)

STAFF = (DIR, DEP_C, DEP_O, AH_A, AH_B, AH_C, DH_A, DH_B, DH_C, TL_A, TL_B, TL_C, SM_A, SM_B, SM_C, NEWBIE, HR_E, SUB)

GOOD = [PartInput(date(2027, 9, 6), 14), PartInput(date(2027, 10, 4), 10)]
AUG = (date(2027, 9, 8), date(2027, 9, 15))  # inside the first GOOD part


def make_ctx(**overrides) -> CheckContext:
    base = CheckContext(holidays=HOLIDAYS, employees={e.id: e for e in STAFF}, today=TODAY)
    return replace(base, **overrides)


def check(employee, parts=GOOD, ctx=None, **kw):
    kw.setdefault("substitute_id", SUB.id)
    return re_.check_request(employee, parts, ctx or make_ctx(), **kw)


def booked(emp, start=AUG[0], end=AUG[1], **kw) -> BookedPart:
    return BookedPart(emp.id, start, end, **kw)


def result(report, code):
    return next(r for r in report.results if r.rule_code == code)


def codes(report):
    return {r.rule_code for r in report.results}


def with_rule(code, **changes):
    rules = dict(re_.DEFAULT_RULES)
    rules[code] = replace(rules[code], **changes)
    return rules


def test_valid_request_has_no_errors():
    report = check(SM_C)
    assert not report.has_errors and not report.warnings
    assert all(r.status == re_.OK for r in report.results)


# --- ТК РБ: minimum duration ------------------------------------------------

def test_min_duration_error_when_total_below_norm():
    report = check(SM_C, [PartInput(date(2027, 9, 6), 14), PartInput(date(2027, 10, 4), 7)])
    r = result(report, re_.MIN_DURATION)
    assert r.status == re_.ERROR
    assert "3" in r.hint


def test_min_duration_uses_individual_norm():
    senior = replace(SM_C, annual_days=28)
    assert result(check(senior), re_.MIN_DURATION).status == re_.ERROR


def test_min_duration_warns_when_above_norm():
    assert result(check(SM_C, [PartInput(date(2027, 9, 6), 30)]), re_.MIN_DURATION).status == re_.WARNING


def test_min_duration_uses_rule_value_from_context():
    report = check(SM_C, [PartInput(date(2027, 9, 6), 20)], make_ctx(rules=with_rule(re_.MIN_DURATION, value=20)))
    assert result(report, re_.MIN_DURATION).status == re_.OK


# --- ТК РБ: splitting into parts --------------------------------------------

def test_more_than_two_parts_is_error():
    parts = [PartInput(date(2027, 4, 5), 8), PartInput(date(2027, 9, 6), 8), PartInput(date(2027, 10, 4), 8)]
    report = check(SM_C, parts)
    assert result(report, re_.MAX_PARTS).status == re_.ERROR
    assert result(report, re_.MIN_PART_LENGTH).status == re_.ERROR


def test_no_part_of_14_days_is_error():
    report = check(SM_C, [PartInput(date(2027, 9, 6), 12), PartInput(date(2027, 10, 4), 12)])
    assert result(report, re_.MIN_PART_LENGTH).status == re_.ERROR
    assert result(report, re_.MAX_PARTS).status == re_.OK


def test_overlapping_parts_are_input_error():
    report = check(SM_C, [PartInput(date(2027, 9, 6), 14), PartInput(date(2027, 9, 10), 10)])
    assert report.has_errors and report.results[0].rule_code == re_.INPUT


# --- ТК РБ: holiday extension -----------------------------------------------

def test_compute_end_date_skips_holidays():
    end, skipped = re_.compute_end_date(date(2027, 5, 3), 14, HOLIDAYS)
    assert skipped == (date(2027, 5, 9), date(2027, 5, 11))
    assert end == date(2027, 5, 18)


def test_compute_end_date_without_holidays():
    assert re_.compute_end_date(date(2027, 8, 2), 14)[0] == date(2027, 8, 15)


def test_holiday_extension_reported_and_can_be_disabled():
    parts = [PartInput(date(2027, 4, 26), 14), PartInput(date(2027, 10, 4), 10)]
    report = check(SM_C, parts)
    assert report.parts[0].end == date(2027, 5, 12)  # +1, 9 and 11 May
    assert "01.05.2027" in result(report, re_.HOLIDAY_EXTENSION).message

    report = check(SM_C, parts, make_ctx(rules=with_rule(re_.HOLIDAY_EXTENSION, enabled=False)))
    assert report.parts[0].end == date(2027, 5, 9)
    assert re_.HOLIDAY_EXTENSION not in codes(report)


# --- ТК РБ: first vacation --------------------------------------------------

def test_first_vacation_before_six_months_is_error():
    report = check(NEWBIE, [PartInput(date(2027, 2, 1), 14), PartInput(date(2027, 10, 4), 10)])
    r = result(report, re_.FIRST_VACATION)
    assert r.status == re_.ERROR
    assert "15.03.2027" in r.hint


def test_first_vacation_after_six_months_is_ok():
    report = check(NEWBIE, [PartInput(date(2027, 3, 15), 14), PartInput(date(2027, 10, 4), 10)])
    assert result(report, re_.FIRST_VACATION).status == re_.OK


def test_add_months_clamps_day():
    assert re_.add_months(date(2026, 8, 31), 6) == date(2027, 2, 28)


# --- absence types ----------------------------------------------------------

@pytest.mark.parametrize("kind", [re_.UNPAID, re_.TREATMENT])
def test_labour_code_rules_apply_only_to_labour_vacation(kind):
    # 5 days, one part, newbie, crosses 1 May: no ТК errors and no holiday extension.
    report = check(NEWBIE, [PartInput(date(2027, 4, 29), 5)], absence_type=kind)
    assert not report.has_errors
    assert not codes(report) & set(re_.LAW_RULES)
    assert report.parts[0].end == date(2027, 5, 3)


@pytest.mark.parametrize("kind", [re_.UNPAID, re_.TREATMENT])
def test_concurrency_and_notice_apply_to_all_types(kind):
    ctx = make_ctx(booked=[booked(TL_B)], today=date(2027, 9, 1))
    report = check(TL_A, [PartInput(date(2027, 9, 10), 3)], ctx, absence_type=kind)
    assert result(report, re_.CONC_TEAM_LEAD).status == re_.ERROR
    assert result(report, re_.NOTICE_PERIOD).status == re_.ERROR


def test_colleague_absence_of_other_type_counts_for_concurrency():
    ctx = make_ctx(booked=[booked(TL_B, absence_type=re_.TREATMENT)])
    r = result(check(TL_A, ctx=ctx), re_.CONC_TEAM_LEAD)
    assert r.status == re_.ERROR
    assert "плановое лечение" in r.message


def test_own_absence_of_other_type_must_not_overlap():
    ctx = make_ctx(booked=[booked(SM_C, absence_type=re_.UNPAID)])
    r = result(check(SM_C, ctx=ctx), re_.OWN_OVERLAP)
    assert r.status == re_.ERROR
    assert "без сохранения" in r.message


def test_own_request_of_same_type_is_ignored():
    ctx = make_ctx(booked=[BookedPart(SM_C.id, date(2027, 9, 6), date(2027, 9, 19))])
    assert not check(SM_C, ctx=ctx).has_errors


# --- concurrency by position ------------------------------------------------

def test_sales_limit_two_in_whole_company():
    ctx = make_ctx(booked=[booked(SM_A), booked(SM_B)])  # different departments and divisions
    r = result(check(SM_C, ctx=ctx), re_.CONC_SALES)
    assert r.status == re_.ERROR
    assert "Продажин А" in r.message and "Продажин Б" in r.message
    assert "08.09.2027 – 15.09.2027" in r.message


def test_sales_limit_allows_second_sales_manager():
    assert result(check(SM_C, ctx=make_ctx(booked=[booked(SM_A)])), re_.CONC_SALES).status == re_.OK


def test_sales_limit_is_editable():
    ctx = make_ctx(booked=[booked(SM_A), booked(SM_B)], rules=with_rule(re_.CONC_SALES, value=3))
    assert result(check(SM_C, ctx=ctx), re_.CONC_SALES).status == re_.OK
    ctx = make_ctx(booked=[booked(SM_A)], rules=with_rule(re_.CONC_SALES, value=1))
    assert result(check(SM_C, ctx=ctx), re_.CONC_SALES).status == re_.ERROR


def test_sales_limit_does_not_count_other_positions():
    ctx = make_ctx(booked=[booked(TL_A), booked(DH_A)])
    assert result(check(SM_C, ctx=ctx), re_.CONC_SALES).status == re_.OK


def test_team_lead_vs_team_lead_same_department():
    r = result(check(TL_A, ctx=make_ctx(booked=[booked(TL_B)])), re_.CONC_TEAM_LEAD)
    assert r.status == re_.ERROR
    assert "Группин Б (Руководитель группы)" in r.message and "08.09.2027 – 15.09.2027" in r.message


def test_team_lead_vs_dept_head_of_own_department():
    r = result(check(TL_A, ctx=make_ctx(booked=[booked(DH_A)])), re_.CONC_TEAM_LEAD)
    assert r.status == re_.ERROR and "Отделов А (Начальник отдела)" in r.message


def test_team_lead_other_department_is_ok():
    report = check(TL_A, ctx=make_ctx(booked=[booked(TL_C), booked(DH_B)]))
    assert result(report, re_.CONC_TEAM_LEAD).status == re_.OK


def test_dept_head_vs_dept_head_and_area_head_of_same_division():
    r = result(check(DH_A, ctx=make_ctx(booked=[booked(DH_B)])), re_.CONC_DEPT_HEAD)
    assert r.status == re_.ERROR and "Отделов Б" in r.message
    r = result(check(DH_A, ctx=make_ctx(booked=[booked(AH_C)])), re_.CONC_DEPT_HEAD)  # area head of the division, any boss
    assert r.status == re_.ERROR and "Направленко В (Руководитель направления)" in r.message


def test_dept_head_other_division_is_ok():
    assert result(check(DH_A, ctx=make_ctx(booked=[booked(DH_C), booked(AH_B)])), re_.CONC_DEPT_HEAD).status == re_.OK


def test_area_head_vs_area_head_with_same_deputy_even_in_other_division():
    r = result(check(AH_A, ctx=make_ctx(booked=[booked(AH_B)])), re_.CONC_AREA_HEAD)
    assert r.status == re_.ERROR and "Направленко Б" in r.message


def test_area_head_vs_own_deputy():
    r = result(check(AH_A, ctx=make_ctx(booked=[booked(DEP_C)])), re_.CONC_AREA_HEAD)
    assert r.status == re_.ERROR and "Коммерцев (Заместитель директора (коммерция))" in r.message


def test_area_head_with_other_boss_is_ok():
    # Направленко В reports to the operations deputy, who is not Направленко А's boss.
    assert result(check(AH_A, ctx=make_ctx(booked=[booked(AH_C), booked(DEP_O)])), re_.CONC_AREA_HEAD).status == re_.OK


def test_deputies_vs_director():
    for boss in (DEP_C, DEP_O):
        r = result(check(boss, ctx=make_ctx(booked=[booked(DIR)])), re_.CONC_DEPUTY)
        assert r.status == re_.ERROR and "Директоров (Директор)" in r.message


def test_director_vs_deputies():
    r = result(check(DIR, ctx=make_ctx(booked=[booked(DEP_C), booked(DEP_O, start=date(2027, 10, 5), end=date(2027, 10, 8))])), re_.CONC_DIRECTOR)
    assert r.status == re_.ERROR
    assert "Коммерцев" in r.message and "Операционов" in r.message and "05.10.2027 – 08.10.2027" in r.message


def test_deputies_may_overlap_each_other():
    report = check(DEP_C, ctx=make_ctx(booked=[booked(DEP_O)]))
    assert not report.has_errors


def test_pair_rules_are_symmetric():
    # An area head is not the subject of the department head rule, but must not overlap a department head of its division either.
    r = result(check(AH_A, ctx=make_ctx(booked=[booked(DH_A)])), re_.CONC_DEPT_HEAD)
    assert r.status == re_.ERROR and "Отделов А" in r.message
    # A department head is a counterpart of the team lead rule.
    assert result(check(DH_A, ctx=make_ctx(booked=[booked(TL_A)])), re_.CONC_TEAM_LEAD).status == re_.ERROR
    # A deputy is a counterpart of the area head rule for its subordinates.
    assert result(check(DEP_C, ctx=make_ctx(booked=[booked(AH_A)])), re_.CONC_AREA_HEAD).status == re_.ERROR


def test_mirrored_pair_reported_once():
    report = check(DEP_C, ctx=make_ctx(booked=[booked(DIR)]))
    assert result(report, re_.CONC_DEPUTY).status == re_.ERROR
    assert re_.CONC_DIRECTOR not in codes(report)
    assert len(report.errors) == 1


def test_counterpart_rules_hidden_without_conflict():
    report = check(AH_A)
    assert result(report, re_.CONC_AREA_HEAD).status == re_.OK
    assert re_.CONC_DEPT_HEAD not in codes(report)


def test_pair_rule_can_be_disabled():
    ctx = make_ctx(booked=[booked(TL_B)], rules=with_rule(re_.CONC_TEAM_LEAD, enabled=False))
    report = check(TL_A, ctx=ctx)
    assert re_.CONC_TEAM_LEAD not in codes(report) and not report.has_errors


def test_pair_rule_severity_is_configurable():
    ctx = make_ctx(booked=[booked(DH_B)], rules=with_rule(re_.CONC_DEPT_HEAD, severity=re_.WARNING))
    report = check(DH_A, ctx=ctx)
    assert result(report, re_.CONC_DEPT_HEAD).status == re_.WARNING and not report.has_errors


def test_hr_has_no_concurrency_rules():
    report = check(HR_E, ctx=make_ctx(booked=[booked(e) for e in STAFF if e not in (HR_E, SUB)]))
    assert not codes(report) & {re_.CONC_SALES, *re_.PAIR_RULES}


# --- substitute -------------------------------------------------------------

def test_substitute_is_required():
    r = result(check(SM_C, substitute_id=None), re_.SUBSTITUTE)
    assert r.status == re_.ERROR and "обязательное" in r.message


def test_substitute_cannot_be_self():
    assert result(check(SM_C, substitute_id=SM_C.id), re_.SUBSTITUTE).status == re_.ERROR


def test_substitute_on_leave_is_error():
    r = result(check(SM_C, ctx=make_ctx(booked=[booked(SUB, absence_type=re_.UNPAID)])), re_.SUBSTITUTE)
    assert r.status == re_.ERROR
    assert "Замещаев (ваш замещающий)" in r.message and "08.09.2027 – 15.09.2027" in r.message


def test_cannot_rest_while_substituting_someone():
    ctx = make_ctx(booked=[booked(SM_A, substitute_id=SM_C.id)])
    r = result(check(SM_C, ctx=ctx), re_.SUBSTITUTE)
    assert r.status == re_.ERROR and "вы замещаете Продажин А" in r.message


def test_substitute_free_is_ok():
    ctx = make_ctx(booked=[booked(SUB, start=date(2027, 11, 1), end=date(2027, 11, 10))])
    assert result(check(SM_C, ctx=ctx), re_.SUBSTITUTE).status == re_.OK


# --- summer limit -----------------------------------------------------------

def test_summer_more_than_14_days_is_error():
    r = result(check(SM_C, [PartInput(date(2027, 7, 5), 24)]), re_.SUMMER_LIMIT)
    assert r.status == re_.ERROR
    assert "24" in r.message and "10" in r.hint


def test_summer_exactly_14_days_is_ok():
    report = check(SM_C, [PartInput(date(2027, 7, 12), 14), PartInput(date(2027, 10, 4), 10)])
    assert result(report, re_.SUMMER_LIMIT).status == re_.OK


def test_summer_counts_only_days_inside_period():
    # 20.08–02.09 + 10 days in October: only 12 days fall within summer.
    report = check(SM_C, [PartInput(date(2027, 8, 20), 14), PartInput(date(2027, 10, 4), 10)])
    assert "12 дн." in result(report, re_.SUMMER_LIMIT).message


def test_summer_sums_both_parts():
    report = check(SM_C, [PartInput(date(2027, 6, 7), 14), PartInput(date(2027, 8, 2), 10)])
    assert result(report, re_.SUMMER_LIMIT).status == re_.ERROR


def test_summer_excludes_holidays():
    # 28.06 + 14 days spans 3 July, so it ends on 12.07 but has only 14 vacation days.
    report = check(SM_C, [PartInput(date(2027, 6, 28), 14), PartInput(date(2027, 10, 4), 10)])
    assert result(report, re_.SUMMER_LIMIT).status == re_.OK


def test_summer_period_and_limit_are_configurable():
    rules = with_rule(re_.SUMMER_LIMIT, value=7, params={"start": "09-01", "end": "09-30"})
    r = result(check(SM_C, ctx=make_ctx(rules=rules)), re_.SUMMER_LIMIT)
    assert r.status == re_.ERROR and "01.09 – 30.09" in r.message


def test_summer_limit_only_for_labour_vacation():
    assert re_.SUMMER_LIMIT not in codes(check(SM_C, [PartInput(date(2027, 7, 1), 30)], absence_type=re_.UNPAID))


# --- vacation pay -----------------------------------------------------------

def test_pay_not_above_salary():
    assert result(check(SM_C, pay_amount=1500), re_.VACATION_PAY).status == re_.OK
    r = result(check(SM_C, pay_amount=1500.01), re_.VACATION_PAY)
    assert r.status == re_.ERROR and "1 500.00" in r.message


def test_pay_is_optional():
    assert result(check(SM_C), re_.VACATION_PAY).status == re_.OK


def test_pay_for_unpaid_leave_is_error():
    report = check(SM_C, [PartInput(date(2027, 9, 6), 3)], absence_type=re_.UNPAID, pay_amount=100)
    assert result(report, re_.VACATION_PAY).status == re_.ERROR


# --- blackout periods -------------------------------------------------------

def test_blackout_period_is_error():
    ctx = make_ctx(blackouts=[Blackout(date(2027, 9, 24), date(2027, 9, 30), "Закрытие квартала")])
    report = check(SM_C, [PartInput(date(2027, 9, 20), 14), PartInput(date(2027, 11, 8), 10)], ctx)
    r = result(report, re_.BLACKOUT)
    assert r.status == re_.ERROR
    assert "Закрытие квартала" in r.message


def test_blackout_severity_is_configurable():
    ctx = make_ctx(rules=with_rule(re_.BLACKOUT, severity=re_.WARNING),
                   blackouts=[Blackout(date(2027, 9, 7), date(2027, 9, 9), "Инвентаризация")])
    report = check(SM_C, ctx=ctx)
    assert result(report, re_.BLACKOUT).status == re_.WARNING
    assert not report.has_errors


# --- notice period: 30 days -------------------------------------------------

def test_notice_period_is_30_days():
    r = result(check(SM_C, ctx=make_ctx(today=date(2027, 8, 20))), re_.NOTICE_PERIOD)
    assert r.status == re_.ERROR
    assert "30" in r.message and "19.09.2027" in r.hint


@pytest.mark.parametrize("today, status", [(date(2027, 8, 7), re_.OK), (date(2027, 8, 8), re_.ERROR)])
def test_notice_period_boundary(today, status):
    # Start 06.09.2027: 07.08 is exactly 30 days before.
    assert result(check(SM_C, ctx=make_ctx(today=today)), re_.NOTICE_PERIOD).status == status


def test_disabled_rule_is_skipped():
    ctx = make_ctx(rules=with_rule(re_.NOTICE_PERIOD, enabled=False), today=date(2027, 8, 30))
    report = check(SM_C, ctx=ctx)
    assert re_.NOTICE_PERIOD not in codes(report)


# --- half-year schedule deadline ---------------------------------------------

@pytest.mark.parametrize("today, start, status", [
    (date(2027, 1, 15), date(2027, 4, 5), re_.OK),        # I half-year, on the deadline
    (date(2027, 1, 16), date(2027, 4, 5), re_.WARNING),   # I half-year, late
    (date(2027, 7, 15), date(2027, 9, 6), re_.OK),        # II half-year, on the deadline
    (date(2027, 7, 16), date(2027, 9, 6), re_.WARNING),   # II half-year, late
    (date(2027, 1, 20), date(2027, 9, 6), re_.OK),        # late for I, but the part is in II
])
def test_schedule_deadline(today, start, status):
    rules = with_rule(re_.NOTICE_PERIOD, enabled=False)
    report = check(SM_C, [PartInput(start, 24)], make_ctx(today=today, rules=rules))
    assert result(report, re_.SCHEDULE_DEADLINE).status == status


def test_schedule_deadline_is_warning_and_does_not_block():
    report = check(SM_C, ctx=make_ctx(today=date(2027, 7, 20)))
    r = result(report, re_.SCHEDULE_DEADLINE)
    assert r.status == re_.WARNING and "15.07.2027" in r.message
    assert not report.has_errors


def test_schedule_deadline_dates_are_configurable():
    rules = with_rule(re_.SCHEDULE_DEADLINE, params={"h1": "01-15", "h2": "06-01"})
    report = check(SM_C, ctx=make_ctx(today=date(2027, 6, 10), rules=rules))
    assert result(report, re_.SCHEDULE_DEADLINE).status == re_.WARNING


# --- transfer of an approved vacation ----------------------------------------

ORIGINAL = [PartInput(date(2027, 9, 6), 14), PartInput(date(2027, 11, 1), 10)]


def transfer(parts, today, **kw):
    ctx = make_ctx(today=today, booked=[BookedPart(SM_C.id, date(2027, 9, 6), date(2027, 9, 19), status="approved")])
    return check(SM_C, parts, ctx, original_parts=ORIGINAL, **kw)


@pytest.mark.parametrize("today, status", [(date(2027, 10, 18), re_.OK), (date(2027, 10, 19), re_.ERROR)])
def test_transfer_notice_14_days_before_original_start(today, status):
    # Only the November part moves; its original start is 01.11 -> deadline 18.10.
    report = transfer([ORIGINAL[0], PartInput(date(2027, 12, 6), 10)], today)
    r = result(report, re_.TRANSFER_NOTICE)
    assert r.status == status
    if status == re_.ERROR:
        assert "01.11.2027" in r.message and "18.10.2027" in r.message


def test_transfer_unchanged_part_ignores_notice_period():
    # 25.08: the September part (unchanged) starts in 12 days, the moved part is far ahead.
    report = transfer([ORIGINAL[0], PartInput(date(2027, 12, 6), 10)], date(2027, 8, 25))
    assert result(report, re_.NOTICE_PERIOD).status == re_.OK
    assert result(report, re_.TRANSFER_NOTICE).status == re_.OK


def test_transfer_new_dates_still_need_30_days():
    report = transfer([PartInput(date(2027, 10, 18), 14), ORIGINAL[1]], date(2027, 9, 28))
    assert result(report, re_.NOTICE_PERIOD).status == re_.ERROR


def test_transfer_has_no_schedule_deadline_warning():
    report = transfer([ORIGINAL[0], PartInput(date(2027, 12, 6), 10)], date(2027, 8, 1))
    assert re_.SCHEDULE_DEADLINE not in codes(report)


def test_transfer_with_same_dates_is_input_error():
    report = transfer(list(reversed(ORIGINAL)), date(2027, 1, 10))
    assert report.has_errors and report.results[0].rule_code == re_.INPUT


def test_transfer_ignores_own_original_request():
    report = transfer([PartInput(date(2027, 9, 13), 14), ORIGINAL[1]], date(2027, 1, 10))
    assert not report.has_errors


def test_transfer_checks_conflicts_only_for_new_dates():
    # A colleague later booked dates over the unchanged (already approved) September part.
    ctx = make_ctx(today=date(2027, 1, 10), booked=[booked(SM_A), booked(SM_B)])
    report = check(SM_C, [ORIGINAL[0], PartInput(date(2027, 12, 6), 10)], ctx, original_parts=ORIGINAL)
    assert result(report, re_.CONC_SALES).status == re_.OK
    # ...but the new dates are checked.
    ctx = make_ctx(today=date(2027, 1, 10), booked=[booked(SM_A, date(2027, 12, 6), date(2027, 12, 10)), booked(SM_B, date(2027, 12, 6), date(2027, 12, 10))])
    report = check(SM_C, [ORIGINAL[0], PartInput(date(2027, 12, 6), 10)], ctx, original_parts=ORIGINAL)
    assert result(report, re_.CONC_SALES).status == re_.ERROR


def test_regular_request_has_no_transfer_rule():
    assert re_.TRANSFER_NOTICE not in codes(check(SM_C))
