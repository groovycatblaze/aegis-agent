"""Deterministic business rules of the (simulated) enterprise systems of record.

These are what the expense and IT systems themselves compute. The policy
documents in rag/corpus describe the same rules in prose for humans and for the
agent; the systems of record are the source of truth.
"""
from __future__ import annotations

CITY_TIERS: dict[str, int] = {
    # Tier 1
    "mumbai": 1, "delhi": 1, "new delhi": 1, "bengaluru": 1, "bangalore": 1, "chennai": 1,
    "hyderabad": 1, "kolkata": 1, "pune": 1, "gurugram": 1, "noida": 1,
    # Tier 2
    "jaipur": 2, "kochi": 2, "ahmedabad": 2, "lucknow": 2, "chandigarh": 2, "indore": 2,
    "coimbatore": 2, "nagpur": 2, "bhubaneswar": 2, "visakhapatnam": 2, "goa": 2,
}

HOTEL_CAP_PER_NIGHT = {1: 8000, 2: 5500, 3: 4000}
MEAL_PER_DIEM = {1: 3000, 2: 2200, 3: 1600}
LOCAL_TRANSPORT_CAP_PER_DAY = 1500

EXPENSE_MANAGER_APPROVAL_ABOVE = 50_000     # INR, per claim
EXPENSE_FINANCE_APPROVAL_ABOVE = 200_000    # INR, per claim

PER_PERSON_CAPS = {
    "meal": 1500,
    "team_event": 2500,
    "client_entertainment": 5000,
}
FLAT_CAPS = {
    "training": 60_000,
    "equipment": 15_000,
    "internet": 1_500,
}

LAPTOP_REPLACEMENT_AGE_MONTHS = 36
LAPTOP_TIERS = {
    "standard": {"model": "Latitude 7450 (16GB/512GB)", "cost_inr": 68_000},
    "performance": {"model": "ThinkPad P1 Gen 7 (32GB/1TB)", "cost_inr": 92_000},
}
PERFORMANCE_TIER_DEPARTMENTS = {"Engineering"}
HARDWARE_MANAGER_APPROVAL_ABOVE = 75_000


def city_tier(city: str) -> int:
    return CITY_TIERS.get(city.strip().lower(), 3)


def laptop_tier_for(department: str) -> str:
    return "performance" if department in PERFORMANCE_TIER_DEPARTMENTS else "standard"


def calculate_reimbursement(city: str, nights: int, airfare_inr: float = 0,
                            hotel_per_night_inr: float = 0, local_transport_inr: float = 0) -> dict:
    if nights < 0 or nights > 60:
        raise ValueError("nights must be between 0 and 60")
    tier = city_tier(city)
    days = nights + 1
    hotel_cap = HOTEL_CAP_PER_NIGHT[tier]
    hotel_eligible = min(hotel_per_night_inr, hotel_cap) * nights
    meals = MEAL_PER_DIEM[tier] * days
    local_eligible = min(local_transport_inr, LOCAL_TRANSPORT_CAP_PER_DAY * days)
    claimed = airfare_inr + hotel_per_night_inr * nights + local_transport_inr + meals
    eligible = airfare_inr + hotel_eligible + meals + local_eligible
    notes = []
    if hotel_per_night_inr > hotel_cap:
        notes.append(f"Hotel capped at INR {hotel_cap:,}/night for tier-{tier} city.")
    if local_transport_inr > LOCAL_TRANSPORT_CAP_PER_DAY * days:
        notes.append(f"Local transport capped at INR {LOCAL_TRANSPORT_CAP_PER_DAY:,}/day.")
    return {
        "city": city.title(),
        "city_tier": tier,
        "nights": nights,
        "days": days,
        "breakdown": {
            "airfare": round(airfare_inr),
            "hotel": round(hotel_eligible),
            "meals_per_diem": round(meals),
            "local_transport": round(local_eligible),
        },
        "claimed_total_inr": round(claimed),
        "eligible_total_inr": round(eligible),
        "requires_manager_approval": eligible > EXPENSE_MANAGER_APPROVAL_ABOVE,
        "requires_finance_approval": eligible > EXPENSE_FINANCE_APPROVAL_ABOVE,
        "notes": notes,
    }


def check_expense(category: str, amount_inr: float, attendees: int = 1) -> dict:
    category = category.lower().strip()
    attendees = max(1, int(attendees))
    result: dict = {"category": category, "amount_inr": round(amount_inr), "attendees": attendees}
    if category in PER_PERSON_CAPS:
        cap = PER_PERSON_CAPS[category]
        per_person = amount_inr / attendees
        result.update(per_person_inr=round(per_person), cap_per_person_inr=cap,
                      within_limit=per_person <= cap,
                      max_reimbursable_inr=round(min(amount_inr, cap * attendees)))
    elif category in FLAT_CAPS:
        cap = FLAT_CAPS[category]
        result.update(cap_inr=cap, within_limit=amount_inr <= cap,
                      max_reimbursable_inr=round(min(amount_inr, cap)))
    elif category == "travel":
        result.update(within_limit=True, max_reimbursable_inr=round(amount_inr),
                      note="Travel claims are validated line-by-line with calculate_reimbursement.")
    else:
        result.update(within_limit=False, max_reimbursable_inr=0,
                      note="Category not covered by the expense policy; needs Finance review.")
    result["requires_manager_approval"] = amount_inr > EXPENSE_MANAGER_APPROVAL_ABOVE
    result["requires_finance_approval"] = amount_inr > EXPENSE_FINANCE_APPROVAL_ABOVE
    return result
