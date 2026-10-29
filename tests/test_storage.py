from datetime import date

import pytest

import rules_engine as re_
import seed
import storage
from rules_engine import PartInput


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "test.db"
    seed.seed(path)
    return path


def employee_id(db, name):
    return next(e["id"] for e in storage.list_employees(db) if e["name"] == name)


def test_seed_org_structure(db):
    employees = storage.list_employees(db)
    by_pos = {}
    for e in employees:
        by_pos.setdefault(e["position"], []).append(e)
    assert len(storage.list_divisions(db)) == 2
    assert 6 <= len(by_pos[re_.SALES_MANAGER]) <= 8
    assert len(by_pos[re_.DIRECTOR]) == len(by_pos[re_.DEPUTY_COMMERCE]) == len(by_pos[re_.DEPUTY_OPERATIONS]) == len(by_pos[re_.HR]) == 1
    assert len(by_pos[re_.AREA_HEAD]) >= 2
    for dept in storage.list_departments(db):
        staff = [e for e in employees if e["department_id"] == dept["id"]]
        assert {re_.DEPT_HEAD, re_.TEAM_LEAD} <= {e["position"] for e in staff}
    by_id = {e["id"]: e for e in employees}
    for head in by_pos[re_.AREA_HEAD]:
        assert by_id[head["manager_id"]]["position"] in (re_.DEPUTY_COMMERCE, re_.DEPUTY_OPERATIONS)
    assert all(e["salary"] for e in employees)


def test_old_schema_is_not_accepted(tmp_path):
    path = tmp_path / "old.db"
    with storage.connect(path) as conn:
        conn.execute("CREATE TABLE rules (code TEXT)")
        conn.execute("INSERT INTO rules VALUES ('x')")
    assert not storage.db_exists(path)


def test_two_stage_approval(db):
    emp = employee_id(db, "Демидович Ульяна")
    rid = storage.create_request(emp, 2027, [PartInput(date(2027, 9, 6), 24)], substitute_id=27, db_path=db)
    assert storage.get_request(rid, db).status == storage.STATUS_SUBMITTED
    storage.manager_decision(rid, True, db_path=db)
    assert storage.get_request(rid, db).status == storage.STATUS_MANAGER_APPROVED
    storage.set_request_status(rid, storage.STATUS_APPROVED, db_path=db)
    assert storage.get_request(rid, db).status == storage.STATUS_APPROVED


def test_manager_return_requires_comment(db):
    rid = storage.create_request(26, 2027, [PartInput(date(2027, 9, 6), 24)], substitute_id=27, db_path=db)
    with pytest.raises(ValueError):
        storage.manager_decision(rid, False, "  ", db_path=db)
    storage.manager_decision(rid, False, "Сдвинь на октябрь", db_path=db)
    req = storage.get_request(rid, db)
    assert req.status == storage.STATUS_RETURNED and req.manager_comment == "Сдвинь на октябрь"


def test_employee_without_manager_goes_straight_to_hr(db):
    director = employee_id(db, "Ковалёв Виктор")
    rid = storage.create_request(director, 2027, [PartInput(date(2027, 3, 1), 3)], absence_type=re_.UNPAID,
                                 substitute_id=2, db_path=db)
    assert storage.get_request(rid, db).status == storage.STATUS_MANAGER_APPROVED


def test_new_request_replaces_pending_one_of_same_type_only(db):
    first = storage.create_request(26, 2027, [PartInput(date(2027, 9, 6), 24)], substitute_id=27, db_path=db)
    other = storage.create_request(26, 2027, [PartInput(date(2027, 3, 1), 3)], absence_type=re_.UNPAID,
                                   substitute_id=27, db_path=db)
    second = storage.create_request(26, 2027, [PartInput(date(2027, 10, 4), 24)], substitute_id=27, db_path=db)
    assert storage.get_request(first, db).status == storage.STATUS_REPLACED
    assert storage.get_request(other, db).status == storage.STATUS_SUBMITTED
    assert storage.latest_request(26, 2027, re_.LABOR, db).id == second


def test_approved_transfer_retires_original(db):
    original = storage.approved_request(9, 2027, db_path=db)
    transfer = storage.latest_request(9, 2027, db_path=db)
    assert transfer.is_transfer and transfer.original_request_id == original.id
    storage.set_request_status(transfer.id, storage.STATUS_APPROVED, db_path=db)
    assert storage.get_request(original.id, db).status == storage.STATUS_TRANSFERRED
    assert storage.approved_request(9, 2027, db_path=db).id == transfer.id
    assert original.id not in {r.id for r in storage.list_requests(2027, db_path=db)}


def test_returned_transfer_keeps_original(db):
    original = storage.approved_request(9, 2027, db_path=db)
    transfer = storage.latest_request(9, 2027, db_path=db)
    storage.set_request_status(transfer.id, storage.STATUS_RETURNED, "Нет", db_path=db)
    assert storage.get_request(original.id, db).status == storage.STATUS_APPROVED


def test_exception_requires_comment_and_is_logged(db):
    rid = storage.latest_request(6, 2027, db_path=db).id
    with pytest.raises(ValueError):
        storage.approve_as_exception(rid, " ", [], db_path=db)
    storage.approve_as_exception(rid, "Согласовано с заместителем директора", ["Руководитель направления: пересечение"], db_path=db)
    log = storage.list_exceptions(db)
    assert any(x["request_id"] == rid and x["author"] == "HR-специалист" for x in log)


def test_next_month_list_for_accounting(db):
    rows, month = storage.next_month_absences(2027, date(2027, 6, 20), db)
    assert month == date(2027, 7, 1)
    names = [r["name"] for r in rows]
    assert names == ["Климович Анна", "Ковалёв Виктор", "Кравченко Алексей"]  # sorted by start date
    assert rows[1]["pay_amount"] == 5200 and rows[1]["end"] == date(2027, 8, 1)
    assert rows[0]["type"] == "Плановое лечение"
    # Not yet approved requests starting in July (Ярошевич, Зайцев) are not in the list.
    assert "Ярошевич Кирилл" not in names


def test_next_month_list_december_rolls_over_year(db):
    rows, month = storage.next_month_absences(2027, date(2027, 12, 5), db)
    assert month == date(2028, 1, 1) and rows == []


def test_build_context_uses_request_type_and_substitute(db):
    ctx = storage.build_context(2027, db_path=db)
    treatment = next(b for b in ctx.booked if b.employee_id == 17)
    assert treatment.absence_type == re_.TREATMENT and treatment.substitute_id == 11


def test_rule_params_roundtrip(db):
    storage.update_rule_params(re_.SUMMER_LIMIT, {"start": "06-15", "end": "08-15"}, db)
    assert storage.get_rules(db)[re_.SUMMER_LIMIT].params == {"start": "06-15", "end": "08-15"}
