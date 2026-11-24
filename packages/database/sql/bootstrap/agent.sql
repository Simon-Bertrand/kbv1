\getenv agent_password POSTGRES_AGENT_PASSWORD
\ir password-session.sql
ALTER ROLE agent PASSWORD :'agent_password' LOGIN;
