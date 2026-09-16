-- Runs after 01-schema.sql on both db-blue and db-green.
-- app_user must never see rows outside RLS policy scope, and must never
-- be able to grant itself around RLS, so no CREATEROLE / superuser here.
ALTER ROLE app_user SET row_security = on;
