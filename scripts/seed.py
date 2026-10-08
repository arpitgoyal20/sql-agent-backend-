"""Build data/sample.db from data/schema.sql with deterministic fake data.

Usage: python scripts/seed.py
"""

from __future__ import annotations

import random
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

from faker import Faker

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "data" / "schema.sql"
DB_PATH = ROOT / "data" / "sample.db"

N_EMPLOYEES = 200
N_CUSTOMERS = 500
N_ORDERS = 2000
N_TOP_LEVEL = 8  # employees with ManagerID NULL (one head per department)

DEPARTMENTS = [
    ("Engineering", "San Francisco"),
    ("Sales", "New York"),
    ("Marketing", "Chicago"),
    ("Finance", "New York"),
    ("Human Resources", "Austin"),
    ("Customer Support", "Phoenix"),
    ("Operations", "Seattle"),
    ("Product", "San Francisco"),
]

STATES = {
    "California": ["Los Angeles", "San Francisco", "San Diego", "San Jose", "Sacramento"],
    "Texas": ["Houston", "Austin", "Dallas", "San Antonio"],
    "New York": ["New York City", "Buffalo", "Rochester", "Albany"],
    "Florida": ["Miami", "Orlando", "Tampa"],
    "Illinois": ["Chicago", "Springfield", "Naperville"],
    "Washington": ["Seattle", "Spokane", "Tacoma"],
    "Massachusetts": ["Boston", "Cambridge", "Worcester"],
    "Georgia": ["Atlanta", "Savannah"],
    "Colorado": ["Denver", "Boulder", "Colorado Springs"],
    "Arizona": ["Phoenix", "Tucson", "Mesa"],
}
# California, Texas and New York get more customers so filters on them return plenty of rows.
STATE_WEIGHTS = [20, 15, 15, 10, 8, 8, 7, 6, 6, 5]

PRODUCTS = {
    "Electronics": (["Wireless Mouse", "Mechanical Keyboard", "27in Monitor", "USB-C Hub",
                     "Noise-Cancelling Headphones", "Webcam HD", "Portable SSD 1TB",
                     "Bluetooth Speaker", "Smartwatch"], (19, 450)),
    "Books": (["SQL Fundamentals", "Data Modeling Handbook", "Python for Analysts",
               "The Pragmatic Engineer", "Statistics Made Simple", "Designing Data Systems",
               "Leadership Basics", "Mystery at Midnight"], (12, 60)),
    "Home & Kitchen": (["Coffee Maker", "Chef Knife Set", "Air Fryer", "Blender",
                        "Cast Iron Skillet", "Electric Kettle", "Vacuum Cleaner",
                        "Desk Lamp", "Water Filter Pitcher"], (15, 250)),
    "Clothing": (["Running Shoes", "Rain Jacket", "Wool Sweater", "Denim Jeans",
                  "Cotton T-Shirt", "Baseball Cap", "Hiking Boots", "Leather Belt"], (10, 180)),
    "Sports": (["Yoga Mat", "Dumbbell Set", "Tennis Racket", "Basketball", "Cycling Helmet",
                "Camping Tent", "Water Bottle", "Jump Rope"], (8, 300)),
    "Office Supplies": (["Notebook Pack", "Gel Pens (12)", "Ergonomic Chair", "Standing Desk",
                         "Desk Organizer", "Whiteboard", "Paper Shredder", "Label Maker"], (5, 400)),
}

ORDER_STATUSES = ["pending", "shipped", "delivered", "cancelled"]
STATUS_WEIGHTS = [8, 12, 70, 10]
PAYMENT_METHODS = ["card", "upi", "bank_transfer", "cash"]
METHOD_WEIGHTS = [55, 20, 15, 10]


def rand_date(start: date, end: date) -> date:
    return start + timedelta(days=random.randint(0, (end - start).days))


def iso(d: date) -> str:
    return d.isoformat()


def build(conn: sqlite3.Connection) -> dict[str, int]:
    fake = Faker("en_US")
    cur = conn.cursor()

    # Departments
    cur.executemany(
        "INSERT INTO Departments (DepartmentID, Name, Location) VALUES (?, ?, ?)",
        [(i + 1, name, loc) for i, (name, loc) in enumerate(DEPARTMENTS)],
    )

    # Employees: ~20% hired 2024-01-01 or later, the rest 2018-2023.
    employees = []
    dept_heads: dict[int, int] = {}
    dept_members: dict[int, list[int]] = {d: [] for d in range(1, len(DEPARTMENTS) + 1)}
    for emp_id in range(1, N_EMPLOYEES + 1):
        first, last = fake.first_name(), fake.last_name()
        if emp_id <= N_TOP_LEVEL:
            dept_id = emp_id
            hire = rand_date(date(2018, 1, 1), date(2019, 12, 31))
            salary = round(random.uniform(150_000, 220_000), -2)
            manager = None
            dept_heads[dept_id] = emp_id
        else:
            dept_id = random.randint(1, len(DEPARTMENTS))
            if random.random() < 0.20:
                hire = rand_date(date(2024, 1, 1), date(2025, 12, 31))
            else:
                hire = rand_date(date(2018, 1, 1), date(2023, 12, 31))
            salary = round(random.uniform(45_000, 145_000), -2)
            # Manager is the department head or an earlier member of the same department.
            pool = [dept_heads[dept_id]] + dept_members[dept_id][:5]
            manager = random.choice(pool)
        dept_members[dept_id].append(emp_id)
        email = f"{first}.{last}{emp_id}@acme.example".lower()
        employees.append((emp_id, first, last, email, iso(hire), salary, dept_id, manager))
    cur.executemany(
        "INSERT INTO Employees (EmployeeID, FirstName, LastName, Email, HireDate, Salary, "
        "DepartmentID, ManagerID) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        employees,
    )
    sales_reps = dept_members[2]  # department 2 is Sales

    # Customers
    customers = []
    signup_dates: dict[int, date] = {}
    state_names = list(STATES)
    for cust_id in range(1, N_CUSTOMERS + 1):
        state = random.choices(state_names, weights=STATE_WEIGHTS)[0]
        city = random.choice(STATES[state])
        name = fake.name()
        signup = rand_date(date(2022, 1, 1), date(2025, 10, 31))
        signup_dates[cust_id] = signup
        email = fake.email()
        customers.append((cust_id, name, email, city, state, iso(signup)))
    cur.executemany(
        "INSERT INTO Customers (CustomerID, Name, Email, City, State, SignupDate) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        customers,
    )

    # Products: 50 across 6 categories.
    products = []
    prod_id = 0
    for category, (names, (lo, hi)) in PRODUCTS.items():
        for name in names:
            prod_id += 1
            products.append((prod_id, name, category, round(random.uniform(lo, hi), 2)))
    assert len(products) == 50, len(products)
    cur.executemany(
        "INSERT INTO Products (ProductID, Name, Category, Price) VALUES (?, ?, ?, ?)", products
    )
    price_of = {p[0]: p[3] for p in products}

    # Orders, items and payments.
    orders, items, payments = [], [], []
    item_id = payment_id = 0
    order_start, order_end = date(2023, 1, 1), date(2025, 12, 31)
    for order_id in range(1, N_ORDERS + 1):
        cust_id = random.randint(1, N_CUSTOMERS)
        start = max(order_start, signup_dates[cust_id])
        order_date = rand_date(start, order_end)
        status = random.choices(ORDER_STATUSES, weights=STATUS_WEIGHTS)[0]
        rep = random.choice(sales_reps) if random.random() < 0.9 else None
        orders.append((order_id, cust_id, rep, iso(order_date), status))

        total = 0.0
        for product_id in random.sample(range(1, len(products) + 1), random.randint(1, 5)):
            item_id += 1
            qty = random.randint(1, 4)
            # Occasional discount so UnitPrice is not always the list price.
            unit = round(price_of[product_id] * random.choice([1, 1, 1, 0.9, 0.85]), 2)
            total += qty * unit
            items.append((item_id, order_id, product_id, qty, unit))

        if status != "cancelled":
            payment_id += 1
            paid = min(order_date + timedelta(days=random.randint(0, 3)), order_end)
            method = random.choices(PAYMENT_METHODS, weights=METHOD_WEIGHTS)[0]
            payments.append((payment_id, order_id, round(total, 2), method, iso(paid)))

    cur.executemany(
        "INSERT INTO Orders (OrderID, CustomerID, EmployeeID, OrderDate, Status) "
        "VALUES (?, ?, ?, ?, ?)",
        orders,
    )
    cur.executemany(
        "INSERT INTO OrderItems (OrderItemID, OrderID, ProductID, Quantity, UnitPrice) "
        "VALUES (?, ?, ?, ?, ?)",
        items,
    )
    cur.executemany(
        "INSERT INTO Payments (PaymentID, OrderID, Amount, Method, PaidAt) VALUES (?, ?, ?, ?, ?)",
        payments,
    )
    return {
        "Departments": len(DEPARTMENTS),
        "Employees": len(employees),
        "Customers": len(customers),
        "Products": len(products),
        "Orders": len(orders),
        "OrderItems": len(items),
        "Payments": len(payments),
    }


def main(db_path: Path = DB_PATH) -> None:
    Faker.seed(42)
    random.seed(42)

    db_path.unlink(missing_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(SCHEMA.read_text())
        counts = build(conn)
        conn.commit()
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            sys.exit(f"Foreign key violations: {violations[:5]}")
        conn.execute("VACUUM")
    finally:
        conn.close()

    print(f"Wrote {db_path.relative_to(ROOT)}")
    for table, n in counts.items():
        print(f"  {table:<12} {n:>6}")


if __name__ == "__main__":
    main()
