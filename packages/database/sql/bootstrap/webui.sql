\getenv webui_password POSTGRES_WEBUI_PASSWORD
\ir password-session.sql
ALTER ROLE "webui-user" PASSWORD :'webui_password' LOGIN;
