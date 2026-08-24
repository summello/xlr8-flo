SELECT 'CREATE DATABASE flo_test'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'flo_test')\gexec
