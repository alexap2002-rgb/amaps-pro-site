-- Очередь входящих обращений. Подписанные договоры и разрешения на отправку хранятся отдельно.
CREATE TABLE IF NOT EXISTS site_submissions (
 id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 role TEXT NOT NULL CHECK(role IN ('Заказчик','Исполнитель')),
 company TEXT NOT NULL, person TEXT NOT NULL, contact TEXT NOT NULL,
 description TEXT NOT NULL, attachment_name TEXT, attachment_key TEXT, attachment_mime TEXT,
 attachment_ready INTEGER NOT NULL DEFAULT 0 CHECK(attachment_ready IN (0,1)),
 mail_status TEXT NOT NULL DEFAULT 'new' CHECK(mail_status IN ('new','sending','retry','sent','exhausted')),
 mail_attempts INTEGER NOT NULL DEFAULT 0, locked_at TEXT, email_id TEXT, last_error TEXT
);
CREATE INDEX IF NOT EXISTS site_submissions_mail_idx ON site_submissions(mail_status,created_at);
