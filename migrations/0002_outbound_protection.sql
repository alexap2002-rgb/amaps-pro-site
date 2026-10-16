-- Только доверенный администратор после проверки ОРИГИНАЛОВ может заполнить реестр.
CREATE TABLE IF NOT EXISTS supplier_deal_protection (
 case_id TEXT NOT NULL,supplier_id TEXT NOT NULL,recipient_email TEXT NOT NULL,
 main_document_id TEXT NOT NULL,protection_document_id TEXT NOT NULL,
 main_verified_at TEXT NOT NULL,protection_verified_at TEXT NOT NULL,
 main_evidence_sha256 TEXT NOT NULL,protection_evidence_sha256 TEXT NOT NULL,
 valid_until TEXT NOT NULL,revoked_at TEXT,
 PRIMARY KEY(case_id,supplier_id,recipient_email),CHECK(main_document_id<>protection_document_id)
);
CREATE TABLE IF NOT EXISTS approved_outbound_messages (
 message_id TEXT PRIMARY KEY,case_id TEXT NOT NULL,supplier_id TEXT NOT NULL,recipient_email TEXT NOT NULL,
 subject TEXT NOT NULL,body TEXT NOT NULL,body_sha256 TEXT NOT NULL,
 approved_at TEXT NOT NULL,approved_by TEXT NOT NULL,attachment_manifest TEXT,
 status TEXT NOT NULL DEFAULT 'approved',send_attempts INTEGER DEFAULT 0,
 lock_at TEXT,updated_at TEXT,resend_id TEXT,last_error TEXT
);
