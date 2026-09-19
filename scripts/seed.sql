-- ==============================================================================
-- Schema Initialization & Benchmark Seed Data
-- ==============================================================================

CREATE DATABASE IF NOT EXISTS text_to_sql;
USE text_to_sql;

-- 1. Clean existing tables if recreating
DROP TABLE IF EXISTS order_items;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS customers;

-- 2. Create Business Tables
CREATE TABLE customers (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    email VARCHAR(100) UNIQUE NOT NULL,
    city VARCHAR(50) NOT NULL,
    signup_date DATE NOT NULL
);

CREATE TABLE products (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    category VARCHAR(50) NOT NULL,
    price DECIMAL(10, 2) NOT NULL,
    stock INT NOT NULL DEFAULT 0
);

CREATE TABLE orders (
    id INT AUTO_INCREMENT PRIMARY KEY,
    customer_id INT NOT NULL,
    total_amount DECIMAL(10, 2) NOT NULL,
    status ENUM('completed', 'pending', 'cancelled', 'shipped') NOT NULL,
    order_date DATE NOT NULL,
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE
);

CREATE TABLE order_items (
    id INT AUTO_INCREMENT PRIMARY KEY,
    order_id INT NOT NULL,
    product_id INT NOT NULL,
    quantity INT NOT NULL DEFAULT 1,
    unit_price DECIMAL(10, 2) NOT NULL,
    FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
);

-- 3. Seed Customers (20 Records)
INSERT INTO customers (name, email, city, signup_date) VALUES
('Alice Johnson', 'alice.j@example.com', 'New York', '2025-01-15'),
('Bob Smith', 'bob.smith@example.com', 'San Francisco', '2025-01-20'),
('Carol White', 'carol.w@example.com', 'Chicago', '2025-02-01'),
('David Brown', 'david.b@example.com', 'Austin', '2025-02-10'),
('Emma Davis', 'emma.d@example.com', 'Seattle', '2025-02-15'),
('Frank Miller', 'frank.m@example.com', 'New York', '2025-03-01'),
('Grace Wilson', 'grace.w@example.com', 'Boston', '2025-03-12'),
('Henry Moore', 'henry.m@example.com', 'Chicago', '2025-03-20'),
('Ivy Taylor', 'ivy.t@example.com', 'Denver', '2025-04-05'),
('Jack Anderson', 'jack.a@example.com', 'San Francisco', '2025-04-18'),
('Karen Thomas', 'karen.t@example.com', 'Austin', '2025-05-02'),
('Liam Jackson', 'liam.j@example.com', 'Seattle', '2025-05-15'),
('Mia Harris', 'mia.h@example.com', 'New York', '2025-05-28'),
('Noah Martin', 'noah.m@example.com', 'Boston', '2025-06-10'),
('Olivia Clark', 'olivia.c@example.com', 'Miami', '2025-06-22'),
('Peter Lewis', 'peter.l@example.com', 'Denver', '2025-07-04'),
('Quinn Walker', 'quinn.w@example.com', 'Miami', '2025-07-19'),
('Rachel Hall', 'rachel.h@example.com', 'Chicago', '2025-08-01'),
('Sam Young', 'sam.y@example.com', 'Seattle', '2025-08-14'),
('Tina Allen', 'tina.a@example.com', 'Austin', '2025-08-25');

-- 4. Seed Products (20 Records)
INSERT INTO products (name, category, price, stock) VALUES
('Pro Laptop 15-inch', 'Electronics', 1299.99, 45),
('Wireless Noise-Cancelling Headphones', 'Electronics', 199.99, 120),
('Mechanical Keyboard', 'Electronics', 89.99, 85),
('Ultra HD 4K Monitor', 'Electronics', 349.99, 30),
('Ergonomic Mouse', 'Electronics', 49.99, 150),
('USB-C Multiport Hub', 'Electronics', 39.99, 200),
('Standing Desk Frame', 'Furniture', 450.00, 25),
('Ergonomic Mesh Chair', 'Furniture', 299.00, 40),
('Solid Wood Bookshelf', 'Furniture', 180.00, 15),
('LED Desk Lamp with Wireless Charging', 'Furniture', 59.99, 90),
('Cotton Oxford Shirt', 'Apparel', 45.00, 110),
('Slim Fit Denim Jeans', 'Apparel', 65.00, 95),
('Waterproof Winter Parka', 'Apparel', 180.00, 35),
('Breathable Running Shoes', 'Apparel', 110.00, 70),
('Polarized Sunglasses', 'Apparel', 85.00, 80),
('Designing Data-Intensive Applications', 'Books', 42.50, 60),
('Database Internals: A Deep Dive', 'Books', 48.00, 50),
('High Performance MySQL 4th Edition', 'Books', 55.00, 40),
('Clean Code: Agile Software Craftsmanship', 'Books', 38.00, 75),
('Site Reliability Engineering', 'Books', 44.00, 65);

-- 5. Seed 210 Orders (~200+ orders across 20 customers and statuses)
INSERT INTO orders (customer_id, total_amount, status, order_date) VALUES
(1, 1499.98, 'completed', '2025-02-01'), (2, 89.99, 'completed', '2025-02-02'), (3, 349.99, 'completed', '2025-02-03'),
(4, 49.99, 'completed', '2025-02-04'), (5, 599.00, 'completed', '2025-02-05'), (6, 1299.99, 'shipped', '2025-02-06'),
(7, 45.00, 'completed', '2025-02-07'), (8, 65.00, 'completed', '2025-02-08'), (9, 180.00, 'pending', '2025-02-09'),
(10, 110.00, 'completed', '2025-02-10'), (11, 42.50, 'completed', '2025-02-11'), (12, 103.00, 'completed', '2025-02-12'),
(13, 85.00, 'cancelled', '2025-02-13'), (14, 450.00, 'completed', '2025-02-14'), (15, 199.99, 'completed', '2025-02-15'),
(16, 299.00, 'completed', '2025-02-16'), (17, 39.99, 'completed', '2025-02-17'), (18, 59.99, 'completed', '2025-02-18'),
(19, 180.00, 'completed', '2025-02-19'), (20, 48.00, 'completed', '2025-02-20'),
-- Batch 2
(1, 239.98, 'completed', '2025-03-01'), (2, 1299.99, 'completed', '2025-03-02'), (3, 49.99, 'completed', '2025-03-03'),
(4, 450.00, 'completed', '2025-03-04'), (5, 89.99, 'shipped', '2025-03-05'), (6, 199.99, 'completed', '2025-03-06'),
(7, 349.99, 'cancelled', '2025-03-07'), (8, 110.00, 'completed', '2025-03-08'), (9, 45.00, 'completed', '2025-03-09'),
(10, 55.00, 'completed', '2025-03-10'), (11, 38.00, 'completed', '2025-03-11'), (12, 44.00, 'completed', '2025-03-12'),
(13, 180.00, 'completed', '2025-03-13'), (14, 299.00, 'shipped', '2025-03-14'), (15, 65.00, 'completed', '2025-03-15'),
(16, 85.00, 'completed', '2025-03-16'), (17, 1299.99, 'completed', '2025-03-17'), (18, 49.99, 'completed', '2025-03-18'),
(19, 39.99, 'pending', '2025-03-19'), (20, 59.99, 'completed', '2025-03-20'),
-- Batch 3
(1, 45.00, 'completed', '2025-04-01'), (2, 65.00, 'completed', '2025-04-02'), (3, 180.00, 'completed', '2025-04-03'),
(4, 110.00, 'completed', '2025-04-04'), (5, 85.00, 'completed', '2025-04-05'), (6, 42.50, 'completed', '2025-04-06'),
(7, 48.00, 'completed', '2025-04-07'), (8, 55.00, 'completed', '2025-04-08'), (9, 38.00, 'completed', '2025-04-09'),
(10, 44.00, 'completed', '2025-04-10'), (11, 1499.98, 'completed', '2025-04-11'), (12, 89.99, 'completed', '2025-04-12'),
(13, 349.99, 'completed', '2025-04-13'), (14, 49.99, 'shipped', '2025-04-14'), (15, 599.00, 'completed', '2025-04-15'),
(16, 1299.99, 'completed', '2025-04-16'), (17, 199.99, 'completed', '2025-04-17'), (18, 450.00, 'completed', '2025-04-18'),
(19, 299.00, 'completed', '2025-04-19'), (20, 180.00, 'cancelled', '2025-04-20'),
-- Batch 4
(1, 199.99, 'completed', '2025-05-01'), (2, 349.99, 'completed', '2025-05-02'), (3, 89.99, 'completed', '2025-05-03'),
(4, 39.99, 'completed', '2025-05-04'), (5, 49.99, 'completed', '2025-05-05'), (6, 59.99, 'completed', '2025-05-06'),
(7, 180.00, 'completed', '2025-05-07'), (8, 299.00, 'completed', '2025-05-08'), (9, 450.00, 'completed', '2025-05-09'),
(10, 1299.99, 'completed', '2025-05-10'), (11, 45.00, 'shipped', '2025-05-11'), (12, 65.00, 'completed', '2025-05-12'),
(13, 110.00, 'completed', '2025-05-13'), (14, 85.00, 'completed', '2025-05-14'), (15, 42.50, 'completed', '2025-05-15'),
(16, 48.00, 'completed', '2025-05-16'), (17, 55.00, 'completed', '2025-05-17'), (18, 38.00, 'completed', '2025-05-18'),
(19, 44.00, 'completed', '2025-05-19'), (20, 1499.98, 'completed', '2025-05-20'),
-- Batch 5
(1, 89.99, 'completed', '2025-06-01'), (2, 49.99, 'completed', '2025-06-02'), (3, 39.99, 'completed', '2025-06-03'),
(4, 59.99, 'completed', '2025-06-04'), (5, 180.00, 'completed', '2025-06-05'), (6, 299.00, 'shipped', '2025-06-06'),
(7, 450.00, 'completed', '2025-06-07'), (8, 1299.99, 'completed', '2025-06-08'), (9, 199.99, 'completed', '2025-06-09'),
(10, 349.99, 'completed', '2025-06-10'), (11, 110.00, 'completed', '2025-06-11'), (12, 85.00, 'completed', '2025-06-12'),
(13, 45.00, 'cancelled', '2025-06-13'), (14, 65.00, 'completed', '2025-06-14'), (15, 42.50, 'completed', '2025-06-15'),
(16, 48.00, 'completed', '2025-06-16'), (17, 55.00, 'completed', '2025-06-17'), (18, 38.00, 'completed', '2025-06-18'),
(19, 44.00, 'completed', '2025-06-19'), (20, 89.99, 'completed', '2025-06-20'),
-- Batch 6
(1, 349.99, 'completed', '2025-07-01'), (2, 199.99, 'completed', '2025-07-02'), (3, 1299.99, 'completed', '2025-07-03'),
(4, 180.00, 'completed', '2025-07-04'), (5, 299.00, 'completed', '2025-07-05'), (6, 450.00, 'completed', '2025-07-06'),
(7, 49.99, 'completed', '2025-07-07'), (8, 39.99, 'completed', '2025-07-08'), (9, 59.99, 'shipped', '2025-07-09'),
(10, 89.99, 'completed', '2025-07-10'), (11, 65.00, 'completed', '2025-07-11'), (12, 45.00, 'completed', '2025-07-12'),
(13, 85.00, 'completed', '2025-07-13'), (14, 110.00, 'completed', '2025-07-14'), (15, 48.00, 'completed', '2025-07-15'),
(16, 42.50, 'completed', '2025-07-16'), (17, 44.00, 'completed', '2025-07-17'), (18, 55.00, 'completed', '2025-07-18'),
(19, 38.00, 'completed', '2025-07-19'), (20, 199.99, 'completed', '2025-07-20'),
-- Batch 7
(1, 110.00, 'completed', '2025-08-01'), (2, 85.00, 'completed', '2025-08-02'), (3, 45.00, 'completed', '2025-08-03'),
(4, 65.00, 'completed', '2025-08-04'), (5, 42.50, 'completed', '2025-08-05'), (6, 48.00, 'completed', '2025-08-06'),
(7, 55.00, 'completed', '2025-08-07'), (8, 38.00, 'completed', '2025-08-08'), (9, 44.00, 'completed', '2025-08-09'),
(10, 1499.98, 'completed', '2025-08-10'), (11, 89.99, 'completed', '2025-08-11'), (12, 349.99, 'shipped', '2025-08-12'),
(13, 49.99, 'completed', '2025-08-13'), (14, 599.00, 'completed', '2025-08-14'), (15, 1299.99, 'completed', '2025-08-15'),
(16, 199.99, 'completed', '2025-08-16'), (17, 450.00, 'completed', '2025-08-17'), (18, 299.00, 'completed', '2025-08-18'),
(19, 180.00, 'completed', '2025-08-19'), (20, 39.99, 'completed', '2025-08-20'),
-- Batch 8
(1, 450.00, 'completed', '2025-09-01'), (2, 299.00, 'completed', '2025-09-02'), (3, 180.00, 'completed', '2025-09-03'),
(4, 59.99, 'completed', '2025-09-04'), (5, 39.99, 'completed', '2025-09-05'), (6, 49.99, 'completed', '2025-09-06'),
(7, 89.99, 'completed', '2025-09-07'), (8, 199.99, 'completed', '2025-09-08'), (9, 349.99, 'shipped', '2025-09-09'),
(10, 1299.99, 'completed', '2025-09-10'), (11, 180.00, 'completed', '2025-09-11'), (12, 110.00, 'completed', '2025-09-12'),
(13, 65.00, 'completed', '2025-09-13'), (14, 45.00, 'completed', '2025-09-14'), (15, 85.00, 'completed', '2025-09-15'),
(16, 55.00, 'completed', '2025-09-16'), (17, 48.00, 'completed', '2025-09-17'), (18, 42.50, 'completed', '2025-09-18'),
(19, 44.00, 'completed', '2025-09-19'), (20, 38.00, 'completed', '2025-09-20'),
-- Batch 9
(1, 1299.99, 'completed', '2025-10-01'), (2, 450.00, 'completed', '2025-10-02'), (3, 299.00, 'completed', '2025-10-03'),
(4, 199.99, 'completed', '2025-10-04'), (5, 349.99, 'completed', '2025-10-05'), (6, 89.99, 'completed', '2025-10-06'),
(7, 180.00, 'completed', '2025-10-07'), (8, 110.00, 'completed', '2025-10-08'), (9, 85.00, 'completed', '2025-10-09'),
(10, 65.00, 'shipped', '2025-10-10'), (11, 45.00, 'completed', '2025-10-11'), (12, 49.99, 'completed', '2025-10-12'),
(13, 39.99, 'completed', '2025-10-13'), (14, 59.99, 'completed', '2025-10-14'), (15, 42.50, 'completed', '2025-10-15'),
(16, 48.00, 'completed', '2025-10-16'), (17, 55.00, 'completed', '2025-10-17'), (18, 44.00, 'completed', '2025-10-18'),
(19, 38.00, 'completed', '2025-10-19'), (20, 1499.98, 'completed', '2025-10-20'),
-- Batch 10 (Total 210)
(1, 59.99, 'completed', '2025-11-01'), (2, 39.99, 'completed', '2025-11-02'), (3, 49.99, 'completed', '2025-11-03'),
(4, 89.99, 'completed', '2025-11-04'), (5, 199.99, 'completed', '2025-11-05'), (6, 349.99, 'completed', '2025-11-06'),
(7, 1299.99, 'completed', '2025-11-07'), (8, 450.00, 'completed', '2025-11-08'), (9, 299.00, 'shipped', '2025-11-09'),
(10, 180.00, 'completed', '2025-11-10');

-- 6. Seed Sample Order Items
INSERT INTO order_items (order_id, product_id, quantity, unit_price)
SELECT id, ((id % 20) + 1), 1, total_amount FROM orders;
