CREATE TABLE Departments (
  DepartmentID INTEGER PRIMARY KEY,
  Name         TEXT NOT NULL,
  Location     TEXT
);

CREATE TABLE Employees (
  EmployeeID   INTEGER PRIMARY KEY,
  FirstName    TEXT NOT NULL,
  LastName     TEXT NOT NULL,
  Email        TEXT UNIQUE,
  HireDate     TEXT NOT NULL,                 -- ISO 'YYYY-MM-DD'
  Salary       REAL,
  DepartmentID INTEGER REFERENCES Departments(DepartmentID),
  ManagerID    INTEGER REFERENCES Employees(EmployeeID)
);

CREATE TABLE Customers (
  CustomerID   INTEGER PRIMARY KEY,
  Name         TEXT NOT NULL,
  Email        TEXT,
  City         TEXT,
  State        TEXT,                          -- full US state name, e.g. 'California'
  SignupDate   TEXT                           -- ISO 'YYYY-MM-DD'
);

CREATE TABLE Products (
  ProductID    INTEGER PRIMARY KEY,
  Name         TEXT NOT NULL,
  Category     TEXT,
  Price        REAL NOT NULL
);

CREATE TABLE Orders (
  OrderID      INTEGER PRIMARY KEY,
  CustomerID   INTEGER NOT NULL REFERENCES Customers(CustomerID),
  EmployeeID   INTEGER REFERENCES Employees(EmployeeID),   -- sales rep
  OrderDate    TEXT NOT NULL,                              -- ISO 'YYYY-MM-DD'
  Status       TEXT CHECK (Status IN ('pending','shipped','delivered','cancelled'))
);

CREATE TABLE OrderItems (
  OrderItemID  INTEGER PRIMARY KEY,
  OrderID      INTEGER NOT NULL REFERENCES Orders(OrderID),
  ProductID    INTEGER NOT NULL REFERENCES Products(ProductID),
  Quantity     INTEGER NOT NULL,
  UnitPrice    REAL NOT NULL
);

CREATE TABLE Payments (
  PaymentID    INTEGER PRIMARY KEY,
  OrderID      INTEGER NOT NULL REFERENCES Orders(OrderID),
  Amount       REAL NOT NULL,
  Method       TEXT,                          -- 'card','upi','bank_transfer','cash'
  PaidAt       TEXT                           -- ISO 'YYYY-MM-DD'
);

CREATE INDEX idx_orders_customer ON Orders(CustomerID);
CREATE INDEX idx_items_order     ON OrderItems(OrderID);
-- OrderDate, Status, HireDate intentionally NOT indexed so the optimizer has real index advice to give.
