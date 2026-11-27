\getenv reranker_password POSTGRES_KM_RERANKER_PASSWORD
\ir password-session.sql
ALTER ROLE km_reranker PASSWORD :'reranker_password' LOGIN;
