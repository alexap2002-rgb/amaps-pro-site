-- АМАПС: закрытый журнал оригинальных обращений с сайта.
-- Выполнить на выделенной Cloudflare D1 до публикации нового обработчика.
-- Доступ к базе только из Worker; не публиковать наружу через открытый API.

CREATE TABLE IF NOT EXISTS amaps_leads (
  id TEXT PRIMARY KEY NOT NULL,
  fingerprint TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('Заказчик','Исполнитель')),
  company TEXT NOT NULL,
  person TEXT NOT NULL,
  contact TEXT NOT NULL,
  task TEXT NOT NULL,
  attachment_key TEXT NOT NULL DEFAULT '',
  attachment_name TEXT NOT NULL DEFAULT '',
  attachment_type TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  attio_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (attio_status IN ('pending','confirmed')),
  attio_record_id TEXT,
  mail_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (mail_status IN ('pending','accepted','uncertain')),
  resend_id TEXT,
  lease_until INTEGER NOT NULL DEFAULT 0,
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT
);

CREATE INDEX IF NOT EXISTS idx_amaps_leads_queue
ON amaps_leads(attio_status,mail_status,created_at);
CREATE INDEX IF NOT EXISTS idx_amaps_leads_errors
ON amaps_leads(mail_status,created_at);

-- Контроль, только для администратора, не для публичного маршрута:
-- SELECT id,created_at,attio_status,mail_status,attempts,last_error
-- FROM amaps_leads ORDER BY created_at DESC LIMIT 30;
