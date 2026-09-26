from core import business_rules as rules


def test_hotel_and_transport_caps_applied():
    r = rules.calculate_reimbursement("Mumbai", 3, airfare_inr=14200, hotel_per_night_inr=9500,
                                      local_transport_inr=3000)
    assert r["city_tier"] == 1
    assert r["breakdown"]["hotel"] == 8000 * 3
    assert r["breakdown"]["meals_per_diem"] == 3000 * 4
    assert r["eligible_total_inr"] == 14200 + 24000 + 12000 + 3000
    assert r["requires_manager_approval"] is True


def test_unknown_city_is_tier_3():
    r = rules.calculate_reimbursement("Mysuru", 1, hotel_per_night_inr=5000)
    assert r["city_tier"] == 3 and r["breakdown"]["hotel"] == 4000


def test_per_person_expense_cap():
    c = rules.check_expense("client_entertainment", 18000, 3)
    assert c["within_limit"] is False and c["max_reimbursable_inr"] == 15000


def test_flat_cap_and_thresholds():
    c = rules.check_expense("training", 72000)
    assert c["within_limit"] is False and c["requires_manager_approval"] is True


def test_policy_documents_state_the_same_thresholds():
    from rag.ingestion import CORPUS_DIR
    expense = (CORPUS_DIR / "expense-policy.md").read_text()
    hardware = (CORPUS_DIR / "it-hardware-policy.md").read_text()
    assert "INR 50,000" in expense and "INR 2,00,000" in expense
    assert "INR 75,000" in hardware and "36 months" in hardware
