-- Bootstrap roles and databases for local docker-compose.
-- Mounted into /docker-entrypoint-initdb.d/ so it runs once on first start.

CREATE USER baseline WITH PASSWORD 'baseline' CREATEDB;
CREATE DATABASE baseline OWNER baseline;
CREATE DATABASE baseline_test OWNER baseline;

\connect baseline
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
GRANT ALL ON SCHEMA public TO baseline;

\connect baseline_test
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
GRANT ALL ON SCHEMA public TO baseline;
