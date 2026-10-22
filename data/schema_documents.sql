-- Employee documents (CVs, contracts, offer letters, payslips, reviews). HR-admin only.
-- The file AND its name are encrypted at rest (a filename often contains the person's
-- name). Only data/documents.py reads or writes this table; every access is audited.
-- IF NOT EXISTS so data.db.migrate() can add it to an existing database.
CREATE TABLE IF NOT EXISTS employee_documents (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_token TEXT NOT NULL REFERENCES employees(token),
    doc_type       TEXT NOT NULL CHECK (doc_type IN ('cv', 'contract', 'offer', 'payslip',
                                                      'review', 'other')),
    filename_enc   BLOB NOT NULL,
    mime           TEXT NOT NULL,
    size_bytes     INTEGER NOT NULL,
    sha256         TEXT NOT NULL,
    content_enc    BLOB NOT NULL,
    uploaded_by    TEXT NOT NULL,
    uploaded_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_employee_documents_token ON employee_documents(employee_token);
