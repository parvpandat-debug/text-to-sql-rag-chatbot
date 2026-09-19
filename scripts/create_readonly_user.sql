-- ==============================================================================
-- Provision Dedicated Read-Only User for Text-to-SQL Chatbot Query Engine
-- ==============================================================================
-- Principle of Least Privilege:
-- The chatbot query engine MUST only be granted SELECT privileges on business tables.
-- It is strictly denied any access to internal security tables: 'users' and 'chat_logs'.
-- ==============================================================================

-- 1. Create or update the read-only user
CREATE USER IF NOT EXISTS 'chatbot_readonly'@'%' IDENTIFIED BY 'readonly_secret_pass';
ALTER USER 'chatbot_readonly'@'%' IDENTIFIED BY 'readonly_secret_pass';

-- 2. Revoke all global/database-level permissions first (Fail-Closed)
REVOKE ALL PRIVILEGES, GRANT OPTION FROM 'chatbot_readonly'@'%';

-- 3. Grant SELECT strictly on business tables within the target database
-- (Adjust database name if different from text_to_sql)
GRANT SELECT ON text_to_sql.customers TO 'chatbot_readonly'@'%';
GRANT SELECT ON text_to_sql.products TO 'chatbot_readonly'@'%';
GRANT SELECT ON text_to_sql.orders TO 'chatbot_readonly'@'%';
GRANT SELECT ON text_to_sql.order_items TO 'chatbot_readonly'@'%';

-- Note: 'users' and 'chat_logs' are intentionally NOT granted.
-- Any SELECT or mutation attempt on 'users' or 'chat_logs' will be rejected
-- directly by the MySQL server with ERROR 1142 (42000): SELECT command denied.

-- 4. Apply privilege changes
FLUSH PRIVILEGES;
