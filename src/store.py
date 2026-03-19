import sqlite3
import json
from pathlib import Path
from src.models import JournalEntry, ProcessingResult

DEFAULT_DB_PATH = Path(__file__).parent.parent / "gl.db"

class Store:
    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.init_db()

    def init_db(self):
        """Create tables if they don't exist."""
        with self.conn:
            self.conn.executescript('''
                CREATE TABLE IF NOT EXISTS journal_entries (
                    id TEXT PRIMARY KEY,
                    invoice_id TEXT NOT NULL,
                    date TEXT NOT NULL,
                    description TEXT NOT NULL,
                    entry_type TEXT NOT NULL,
                    lines_json TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_journal_entries_invoice ON journal_entries(invoice_id);

                CREATE TABLE IF NOT EXISTS pending_approvals (
                    invoice_id TEXT PRIMARY KEY,
                    result_json TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS corrections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    invoice_id TEXT NOT NULL,
                    line_index INTEGER NOT NULL,
                    original_gl TEXT,
                    corrected_gl TEXT,
                    original_treatment TEXT,
                    corrected_treatment TEXT,
                    reason TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
            ''')

    def has_journal_entries(self, invoice_id: str) -> bool:
        """Check if any journal entries exist for this invoice."""
        cursor = self.conn.execute("SELECT 1 FROM journal_entries WHERE invoice_id = ? LIMIT 1", (invoice_id,))
        return cursor.fetchone() is not None

    def has_pending(self, invoice_id: str) -> bool:
        """Check if a pending approval exists for this invoice."""
        cursor = self.conn.execute("SELECT 1 FROM pending_approvals WHERE invoice_id = ? LIMIT 1", (invoice_id,))
        return cursor.fetchone() is not None

    def save_journal_entries(self, entries: list[JournalEntry]):
        """Insert all journal entries. Each entry is one row."""
        with self.conn:
            for entry in entries:
                lines_json = json.dumps([line.model_dump(mode="json") for line in entry.lines])
                self.conn.execute(
                    "INSERT INTO journal_entries (id, invoice_id, date, description, entry_type, lines_json) VALUES (?, ?, ?, ?, ?, ?)",
                    (entry.id, entry.invoice_id, entry.date.isoformat(), entry.description, entry.entry_type, lines_json)
                )

    def save_pending(self, result: ProcessingResult):
        """Save ProcessingResult as JSON for HITL resume.
        Uses INSERT OR REPLACE to handle re-saves."""
        result_json = result.model_dump_json()
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO pending_approvals (invoice_id, result_json) VALUES (?, ?)",
                (result.invoice_id, result_json)
            )

    def load_pending(self, invoice_id: str) -> ProcessingResult:
        """Load a pending ProcessingResult. Raises ValueError if not found."""
        cursor = self.conn.execute("SELECT result_json FROM pending_approvals WHERE invoice_id = ?", (invoice_id,))
        row = cursor.fetchone()
        if not row:
            raise ValueError(f"Pending approval for invoice {invoice_id} not found in store")
        return ProcessingResult.model_validate_json(row["result_json"])

    def delete_pending(self, invoice_id: str):
        """DELETE FROM pending_approvals WHERE invoice_id = ?"""
        with self.conn:
            self.conn.execute("DELETE FROM pending_approvals WHERE invoice_id = ?", (invoice_id,))

    def save_correction(self, invoice_id: str, line_index: int,
                        original_gl: str, corrected_gl: str,
                        original_treatment: str | None = None,
                        corrected_treatment: str | None = None,
                        reason: str | None = None):
        """Insert a correction row."""
        with self.conn:
            self.conn.execute(
                """INSERT INTO corrections 
                   (invoice_id, line_index, original_gl, corrected_gl, original_treatment, corrected_treatment, reason) 
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (invoice_id, line_index, original_gl, corrected_gl, original_treatment, corrected_treatment, reason)
            )

    def list_corrections(self) -> list[dict]:
        """Return all corrections as dicts."""
        cursor = self.conn.execute("SELECT * FROM corrections ORDER BY created_at DESC")
        return [dict(row) for row in cursor.fetchall()]
