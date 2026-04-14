"""
Database Hygiene Service — storage management, cleanup, and maintenance.

Handles:
  - SQLite WAL checkpointing + VACUUM
  - Old record purging (bets, signals, logs older than retention window)
  - Table size reporting
  - Index health check
  - PostgreSQL table bloat analysis (if using Postgres)
  - Periodic maintenance scheduling
"""
from __future__ import annotations

import os
import time
from typing import Any


# ── Retention policy ──────────────────────────────────────────────────────────
DEFAULT_RETENTION: dict[str, int] = {
    "bets":         90,    # days — keep last 90 days of bets
    "signals":      30,    # days — signals logs
    "audit_logs":   180,   # days — security audit trail
    "api_logs":     7,     # days — raw API request/response logs
    "candles":      365,   # days — OHLCV price data
    "health_logs":  7,     # days — healthcheck results
}


class DBHygiene:
    """
    Database maintenance and storage hygiene service.
    Works with both SQLite (local) and PostgreSQL (cloud).
    """

    def __init__(self) -> None:
        self._db_url  = os.getenv("DATABASE_URL", "").strip()
        self._db_path = os.getenv("DB_PATH", "./bots.db").strip()
        self._is_postgres = bool(self._db_url)

    def get_storage_report(self) -> dict[str, Any]:
        """Report current storage utilisation."""
        if self._is_postgres:
            return self._postgres_storage_report()
        return self._sqlite_storage_report()

    def run_maintenance(self) -> dict[str, Any]:
        """Run all maintenance tasks. Returns summary."""
        results: dict[str, Any] = {}
        errors:  list[str]      = []

        # Purge old records
        try:
            purge_result = self.purge_old_records()
            results["purge"] = purge_result
        except Exception as exc:
            errors.append(f"purge_failed: {exc}")

        # Vacuum / checkpoint
        try:
            vac_result = self.vacuum()
            results["vacuum"] = vac_result
        except Exception as exc:
            errors.append(f"vacuum_failed: {exc}")

        # Index health
        try:
            idx_result = self.check_index_health()
            results["index_health"] = idx_result
        except Exception as exc:
            errors.append(f"index_check_failed: {exc}")

        results["errors"]    = errors
        results["timestamp"] = time.time()
        results["db_type"]   = "postgres" if self._is_postgres else "sqlite"
        return results

    def purge_old_records(self, retention: dict[str, int] | None = None) -> dict[str, Any]:
        """Delete records older than retention policy."""
        policy  = retention or DEFAULT_RETENTION
        results = {}

        if self._is_postgres:
            conn = self._pg_connect()
            if not conn:
                return {"error": "postgres_not_available"}
            try:
                cur = conn.cursor()
                for table, days in policy.items():
                    cutoff_epoch = time.time() - days * 86400
                    # Try common timestamp column names
                    for ts_col in ("created_at", "timestamp", "ts", "time"):
                        try:
                            cur.execute(
                                f"DELETE FROM {table} WHERE {ts_col} < %s",
                                (cutoff_epoch,),
                            )
                            results[table] = {"deleted": cur.rowcount, "days": days}
                            break
                        except Exception:
                            pass
                conn.commit()
            except Exception as exc:
                results["error"] = str(exc)
            finally:
                conn.close()
        else:
            conn = self._sqlite_connect()
            if not conn:
                return {"error": "sqlite_not_available"}
            try:
                cur = conn.cursor()
                # Check which tables actually exist
                cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
                existing = {r[0] for r in cur.fetchall()}
                for table, days in policy.items():
                    if table not in existing:
                        continue
                    cutoff_epoch = time.time() - days * 86400
                    for ts_col in ("created_at", "timestamp", "ts", "time"):
                        try:
                            cur.execute(
                                f"DELETE FROM {table} WHERE {ts_col} < ?",
                                (cutoff_epoch,),
                            )
                            results[table] = {"deleted": cur.rowcount, "days": days}
                            break
                        except Exception:
                            pass
                conn.commit()
            except Exception as exc:
                results["error"] = str(exc)
            finally:
                conn.close()

        return results

    def vacuum(self) -> dict[str, Any]:
        """Run VACUUM/CHECKPOINT to reclaim space."""
        if self._is_postgres:
            return self._postgres_vacuum()
        return self._sqlite_vacuum()

    def check_index_health(self) -> dict[str, Any]:
        """Check for missing indexes on commonly queried columns."""
        recommended_indexes = [
            ("bets",    "bot_id",    "CREATE INDEX IF NOT EXISTS idx_bets_bot_id ON bets(bot_id)"),
            ("bets",    "timestamp", "CREATE INDEX IF NOT EXISTS idx_bets_ts ON bets(timestamp)"),
            ("signals", "source",    "CREATE INDEX IF NOT EXISTS idx_signals_source ON signals(source)"),
            ("signals", "timestamp", "CREATE INDEX IF NOT EXISTS idx_signals_ts ON signals(timestamp)"),
        ]
        results = []
        if self._is_postgres:
            conn = self._pg_connect()
        else:
            conn = self._sqlite_connect()

        if not conn:
            return {"error": "db_not_available"}

        try:
            cur = conn.cursor()
            if self._is_postgres:
                cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
            else:
                cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
            existing_tables = {r[0] for r in cur.fetchall()}

            for table, col, create_sql in recommended_indexes:
                if table not in existing_tables:
                    results.append({"table": table, "col": col, "status": "table_missing"})
                    continue
                try:
                    cur.execute(create_sql)
                    conn.commit()
                    results.append({"table": table, "col": col, "status": "created_or_exists"})
                except Exception as exc:
                    results.append({"table": table, "col": col, "status": "error", "error": str(exc)})
        finally:
            conn.close()

        return {"indexes": results}

    def get_table_sizes(self) -> dict[str, Any]:
        """Return row counts and approximate sizes for all tables."""
        if self._is_postgres:
            return self._postgres_table_sizes()
        return self._sqlite_table_sizes()

    # ── SQLite helpers ────────────────────────────────────────────────────────

    def _sqlite_connect(self):
        try:
            import sqlite3
            if not os.path.exists(self._db_path):
                return None
            conn = sqlite3.connect(self._db_path, timeout=5)
            return conn
        except Exception:
            return None

    def _sqlite_storage_report(self) -> dict[str, Any]:
        if not os.path.exists(self._db_path):
            return {"status": "db_not_found", "path": self._db_path}
        size_bytes = os.path.getsize(self._db_path)
        wal_path   = self._db_path + "-wal"
        wal_bytes  = os.path.getsize(wal_path) if os.path.exists(wal_path) else 0
        return {
            "db_type":       "sqlite",
            "path":          self._db_path,
            "size_bytes":    size_bytes,
            "size_mb":       round(size_bytes / 1e6, 3),
            "wal_bytes":     wal_bytes,
            "wal_mb":        round(wal_bytes / 1e6, 3),
            "total_mb":      round((size_bytes + wal_bytes) / 1e6, 3),
        }

    def _sqlite_vacuum(self) -> dict[str, Any]:
        conn = self._sqlite_connect()
        if not conn:
            return {"status": "db_not_found"}
        try:
            before = os.path.getsize(self._db_path)
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.execute("VACUUM")
            conn.close()
            after = os.path.getsize(self._db_path)
            return {
                "status":       "ok",
                "before_bytes": before,
                "after_bytes":  after,
                "saved_bytes":  before - after,
                "saved_mb":     round((before - after) / 1e6, 3),
            }
        except Exception as exc:
            return {"status": "error", "error": str(exc)}
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _sqlite_table_sizes(self) -> dict[str, Any]:
        conn = self._sqlite_connect()
        if not conn:
            return {"error": "db_not_available"}
        try:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [r[0] for r in cur.fetchall()]
            sizes  = {}
            for t in tables:
                try:
                    cur.execute(f"SELECT COUNT(*) FROM {t}")
                    sizes[t] = {"rows": cur.fetchone()[0]}
                except Exception:
                    sizes[t] = {"rows": "error"}
            return {"tables": sizes, "table_count": len(tables)}
        finally:
            conn.close()

    # ── PostgreSQL helpers ────────────────────────────────────────────────────

    def _pg_connect(self):
        if not self._db_url:
            return None
        try:
            import psycopg
            return psycopg.connect(self._db_url, connect_timeout=5)
        except Exception:
            return None

    def _postgres_storage_report(self) -> dict[str, Any]:
        conn = self._pg_connect()
        if not conn:
            return {"error": "postgres_not_available"}
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT pg_size_pretty(pg_database_size(current_database())), "
                "pg_database_size(current_database())"
            )
            row = cur.fetchone()
            return {
                "db_type":    "postgres",
                "size_pretty": row[0] if row else "N/A",
                "size_bytes":  row[1] if row else 0,
                "size_mb":     round(row[1] / 1e6, 2) if row else 0,
            }
        finally:
            conn.close()

    def _postgres_vacuum(self) -> dict[str, Any]:
        conn = self._pg_connect()
        if not conn:
            return {"status": "error", "error": "postgres_not_available"}
        try:
            conn.autocommit = True
            conn.execute("VACUUM ANALYZE")
            return {"status": "ok", "action": "VACUUM ANALYZE"}
        except Exception as exc:
            return {"status": "error", "error": str(exc)}
        finally:
            conn.close()

    def _postgres_table_sizes(self) -> dict[str, Any]:
        conn = self._pg_connect()
        if not conn:
            return {"error": "postgres_not_available"}
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT schemaname, tablename,
                       pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)),
                       pg_total_relation_size(schemaname||'.'||tablename)
                FROM pg_tables
                WHERE schemaname = 'public'
                ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC
                LIMIT 20
            """)
            rows = cur.fetchall()
            tables = [{"schema": r[0], "table": r[1], "size": r[2], "bytes": r[3]} for r in rows]
            return {"tables": tables}
        finally:
            conn.close()
