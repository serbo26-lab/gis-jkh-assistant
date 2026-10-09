"""Explicit atomic operations across existing repository methods."""

from contextlib import contextmanager
import sqlite3


class AtomicConnection(sqlite3.Connection):
    """Keep legacy method commits inside an enclosing savepoint.

    Ordinary repository calls retain sqlite3's context-manager behaviour. An
    explicit atomic scope owns the commit/rollback of a multi-method operation.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._atomic_depth = 0
        self._atomic_serial = 0

    @contextmanager
    def atomic(self):
        self._atomic_serial += 1
        name = f"operation_{self._atomic_serial}"
        self.execute(f"SAVEPOINT {name}")
        self._atomic_depth += 1
        try:
            yield self
            self.execute(f"RELEASE SAVEPOINT {name}")
        except BaseException:
            try:
                self.execute(f"ROLLBACK TO SAVEPOINT {name}")
                self.execute(f"RELEASE SAVEPOINT {name}")
            except sqlite3.Error:
                # SQLite may already have rolled back the transaction (for
                # example a RAISE(ROLLBACK) trigger). Preserve the original error.
                super().rollback()
            raise
        finally:
            self._atomic_depth -= 1

    def commit(self):
        if not self._atomic_depth:
            super().commit()

    def __exit__(self, exc_type, exc_value, traceback):
        if self._atomic_depth:
            return False
        return super().__exit__(exc_type, exc_value, traceback)

    def executescript(self, sql_script):
        if self._atomic_depth:
            raise RuntimeError("Schema scripts cannot run inside an atomic operation")
        return super().executescript(sql_script)
