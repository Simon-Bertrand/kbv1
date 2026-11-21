-- Apply as administrator after 35_security.sql. No client can select another principal.
BEGIN;
ALTER TABLE webui.conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE webui.conversations FORCE ROW LEVEL SECURITY;
ALTER TABLE webui.messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE webui.messages FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS webui_owner ON webui.conversations;
CREATE POLICY webui_owner ON webui.conversations TO "webui-user"
 USING (user_id = NULLIF(current_setting('webui.user_id', true), ''))
 WITH CHECK (user_id = NULLIF(current_setting('webui.user_id', true), ''));
DROP POLICY IF EXISTS webui_owner ON webui.messages;
CREATE POLICY webui_owner ON webui.messages TO "webui-user"
 USING (user_id = NULLIF(current_setting('webui.user_id', true), ''))
 WITH CHECK (user_id = NULLIF(current_setting('webui.user_id', true), ''));
COMMIT;
