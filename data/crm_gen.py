"""Deterministic mock CRM: 500 customers, 2,000 orders, and the refunds already issued.

Same seed -> same rows, every run, on every machine. Only the standard library is used
(no Faker), so there is nothing to pin and nothing that can change the output.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

SEED = 20261117
N_CUSTOMERS = 500
N_ORDERS = 2000

FIRST_NAMES = [
    "Maria", "James", "Aisha", "Wei", "Sofia", "Daniel", "Priya", "Lucas", "Fatima", "Noah",
    "Elena", "Omar", "Hannah", "Mateo", "Yuki", "Grace", "Ivan", "Chloe", "Kwame", "Lena",
    "Diego", "Amara", "Ethan", "Sara", "Tomas", "Mei", "Oliver", "Zara", "Lukas", "Nora",
    "Ahmed", "Julia", "Ravi", "Emma", "Pedro", "Leila", "Samuel", "Ines", "Felix", "Ana",
]
LAST_NAMES = [
    "Lopez", "Smith", "Okafor", "Chen", "Rossi", "Muller", "Patel", "Silva", "Haddad", "Kim",
    "Novak", "Garcia", "Johnson", "Dubois", "Tanaka", "Brown", "Petrov", "Martin", "Mensah",
    "Schmidt", "Fernandez", "Nguyen", "Taylor", "Costa", "Weber", "Ali", "Moreau", "Kowalski",
    "Andersen", "Bianchi", "Reyes", "Fischer", "Sato", "Walker", "Herrera", "Ibrahim",
    "Lambert", "Popescu", "Santos", "Wright",
]
DOMAINS = ["example.com", "example.org", "example.net"]

# (item, typical price in USD). The price is jittered per order.
PRODUCTS = [
    ("Trailhead 2 tent", 189), ("Summit 4 tent", 449), ("Down sleeping bag", 279),
    ("Synthetic sleeping bag", 119), ("Inflatable sleeping pad", 89), ("Camp stove", 59),
    ("Titanium cook set", 74), ("Water filter", 39), ("35L daypack", 95), ("65L backpack", 259),
    ("Rain shell jacket", 169), ("Insulated parka", 329), ("Fleece pullover", 69),
    ("Hiking boots", 159), ("Trail runners", 129), ("Merino socks (3-pack)", 36),
    ("Trekking poles", 79), ("Headlamp", 45), ("GPS watch", 399), ("Camp chair", 65),
    ("Hammock", 55), ("Climbing harness", 85), ("Climbing rope 60m", 239), ("Bear canister", 79),
    ("Kayak paddle", 189), ("Dry bag set", 42), ("Solar charger", 99), ("Binoculars", 219),
    ("Snowshoes", 199), ("Ski goggles", 119),
]

STATUSES = ["processing", "shipped", "delivered", "cancelled", "refunded"]
STATUS_WEIGHTS = [15, 20, 50, 7, 8]
FIRST_ORDER_DATE = date(2026, 6, 1)


def generate() -> tuple[list[tuple], list[tuple], list[tuple]]:
    """Return (customers, orders, refunds) as lists of row tuples, matching db/init columns.

    customers: (id, email, name, tier)
    orders:    (id, customer_id, status, amount_cents, item, ordered_on)
    refunds:   (order_id, amount_cents, status, created_on)
    """
    rng = random.Random(SEED)

    customers = []
    for cid in range(1, N_CUSTOMERS + 1):
        first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
        email = f"{first}.{last}{cid}@{rng.choice(DOMAINS)}".lower()
        roll = rng.random()
        tier = "vip" if roll < 0.05 else "plus" if roll < 0.20 else "standard"
        customers.append((cid, email, f"{first} {last}", tier))

    order_numbers = rng.sample(range(100_000, 1_000_000), N_ORDERS)  # unique, not guessable
    orders, refunds = [], []
    for number in order_numbers:
        order_id = f"LS-{number}"
        customer_id = rng.randint(1, N_CUSTOMERS)
        status = rng.choices(STATUSES, STATUS_WEIGHTS)[0]
        item, price = rng.choice(PRODUCTS)
        amount_cents = round(price * rng.uniform(0.9, 1.1) * 100)
        ordered_on = FIRST_ORDER_DATE + timedelta(days=rng.randint(0, 150))
        orders.append((order_id, customer_id, status, amount_cents, item, ordered_on))
        if status == "refunded":
            refunds.append((order_id, amount_cents, "completed",
                            ordered_on + timedelta(days=rng.randint(5, 30))))
    return customers, orders, refunds


if __name__ == "__main__":
    c, o, r = generate()
    print(f"{len(c)} customers, {len(o)} orders, {len(r)} refunds")
