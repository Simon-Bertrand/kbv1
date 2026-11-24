\getenv reader_password POSTGRES_KM_READER_PASSWORD
\getenv writer_password POSTGRES_KM_WRITER_PASSWORD
\ir password-session.sql
ALTER ROLE km_reader PASSWORD :'reader_password' LOGIN;
ALTER ROLE km_writer PASSWORD :'writer_password' LOGIN;
