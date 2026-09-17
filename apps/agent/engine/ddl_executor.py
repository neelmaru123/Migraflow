"""
Target database DDL statement execution module for agent engine.
"""

import logging
from typing import List
from sqlalchemy import text
from .db import _get_engine

logger = logging.getLogger("docker-agent-execution")


class DDLExecutor:
    """Executes target database DDL statements (pre_migration_ddl and post_migration_ddl)."""

    @staticmethod
    def _ensure_database_exists(db_url: str):
        """Ensures target database exists across PostgreSQL, MySQL, and MongoDB before introspection or DDL execution."""
        from urllib.parse import urlparse
        if not db_url or not db_url.strip():
            return

        db_url_lower = db_url.lower()

        # 1. MongoDB Target Database Verification / Creation
        if "mongo" in db_url_lower or db_url.startswith("mongodb://") or db_url.startswith("mongodb+srv://"):
            try:
                import importlib
                pymongo = importlib.import_module("pymongo")
                clean_url = db_url.split("?")[0]
                db_name = clean_url.rsplit("/", 1)[-1] if "/" in clean_url else "target_db"
                if not db_name or db_name.startswith("mongodb"):
                    db_name = "target_db"
                try:
                    client = pymongo.MongoClient(db_url, serverSelectionTimeoutMS=4000)
                    client.admin.command('ping')
                except Exception:
                    clean_conn = db_url.split("@")[-1] if "@" in db_url else db_url
                    if not clean_conn.startswith("mongodb://") and not clean_conn.startswith("mongodb+srv://"):
                        clean_conn = f"mongodb://{clean_conn}"
                    clean_conn = clean_conn.split("?")[0]
                    client = pymongo.MongoClient(clean_conn, serverSelectionTimeoutMS=4000)
                    client.admin.command('ping')
                # Access database namespace so it is registered
                _ = client[db_name]
                logger.info(f"Target MongoDB database '{db_name}' verified and ready.")
            except Exception as mongo_exc:
                logger.warning(f"Notice during MongoDB target database verification: {mongo_exc}")
            return

        # 2. SQLite Target Database (file-based: automatically created)
        if "sqlite" in db_url_lower:
            return

        # 3. PostgreSQL & MySQL SQL Target Databases
        try:
            engine = _get_engine(db_url)
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception as exc:
            exc_str = str(exc)
            if (
                "1049" in exc_str
                or "database" in exc_str.lower() and "does not exist" in exc_str.lower()
                or "FATAL:  database" in exc_str
                or "Unknown database" in exc_str
            ):
                from urllib.parse import urlparse
                parsed = urlparse(db_url)
                db_name = parsed.path.lstrip('/')
                logger.info(f"Target database '{db_name}' does not exist yet. Attempting auto-creation...")

                try:
                    if "mysql" in db_url_lower:
                        admin_url = db_url.rsplit('/', 1)[0] + '/'
                        adm_engine = _get_engine(admin_url)
                        with adm_engine.connect() as conn:
                            conn.execute(text(f"CREATE DATABASE IF NOT EXISTS `{db_name}` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"))
                            logger.info(f"Auto-created missing MySQL target database '{db_name}'.")
                    elif "postgres" in db_url_lower:
                        admin_url = db_url.rsplit('/', 1)[0] + '/postgres'
                        adm_engine = _get_engine(admin_url)
                        with adm_engine.execution_options(isolation_level="AUTOCOMMIT").connect() as conn:
                            chk_res = conn.execute(
                                text("SELECT 1 FROM pg_database WHERE datname = :dbname"),
                                {"dbname": db_name},
                            )
                            if not chk_res.scalar():
                                conn.execute(text(f'CREATE DATABASE "{db_name}"'))
                                logger.info(f"Auto-created missing PostgreSQL target database '{db_name}'.")
                except Exception as create_exc:
                    logger.warning(f"Could not auto-create target database '{db_name}': {create_exc}")

    @staticmethod
    def get_existing_tables_and_counts(db_url: str, engine_type: str = "postgresql") -> List[dict]:
        """
        Inspects the target database live to find all existing user tables/collections and their row counts.
        """
        results: List[dict] = []
        if not db_url:
            return results

        clean_type = (engine_type or "postgresql").lower().strip()

        # MongoDB Collection Inspection
        if clean_type in ("mongodb", "mongo") or "mongo" in db_url.lower():
            try:
                import importlib
                pymongo = importlib.import_module("pymongo")
                clean_url = db_url.split("?")[0]
                db_name = clean_url.rsplit("/", 1)[-1] if "/" in clean_url else "target_db"
                try:
                    client = pymongo.MongoClient(db_url, serverSelectionTimeoutMS=3000)
                    client.admin.command('ping')
                except Exception:
                    clean_conn = db_url.split("@")[-1] if "@" in db_url else db_url
                    if not clean_conn.startswith("mongodb://") and not clean_conn.startswith("mongodb+srv://"):
                        clean_conn = f"mongodb://{clean_conn}"
                    clean_conn = clean_conn.split("?")[0]
                    client = pymongo.MongoClient(clean_conn, serverSelectionTimeoutMS=3000)
                db = client[db_name]
                for coll_name in db.list_collection_names():
                    if coll_name.startswith("system."):
                        continue
                    try:
                        cnt = db[coll_name].count_documents({})
                    except Exception:
                        cnt = db[coll_name].estimated_document_count()
                    results.append({"table_name": coll_name, "row_count": cnt})
            except Exception as exc:
                logger.warning(f"Could not inspect existing MongoDB collections: {exc}")
            return results

        # SQL Target Databases (PostgreSQL, MySQL, SQLite)
        try:
            engine = _get_engine(db_url)
            with engine.connect() as conn:
                if "postgres" in clean_type or "postgres" in db_url.lower():
                    stmt = text("""
                        SELECT table_name 
                        FROM information_schema.tables 
                        WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
                        ORDER BY table_name;
                    """)
                    tbl_rows = conn.execute(stmt).fetchall()
                    for r in tbl_rows:
                        t_name = r[0]
                        try:
                            c_res = conn.execute(text(f'SELECT COUNT(*) FROM "{t_name}"'))
                            cnt = c_res.scalar() or 0
                        except Exception:
                            cnt = 0
                        results.append({"table_name": t_name, "row_count": cnt})

                elif "mysql" in clean_type or "mysql" in db_url.lower():
                    from urllib.parse import urlparse
                    db_name = urlparse(db_url).path.lstrip('/')
                    stmt = text("""
                        SELECT table_name 
                        FROM information_schema.tables 
                        WHERE table_schema = :db_name AND table_type = 'BASE TABLE'
                        ORDER BY table_name;
                    """)
                    tbl_rows = conn.execute(stmt, {"db_name": db_name}).fetchall()
                    for r in tbl_rows:
                        t_name = r[0]
                        try:
                            c_res = conn.execute(text(f'SELECT COUNT(*) FROM `{t_name}`'))
                            cnt = c_res.scalar() or 0
                        except Exception:
                            cnt = 0
                        results.append({"table_name": t_name, "row_count": cnt})

                elif "sqlite" in clean_type or "sqlite" in db_url.lower():
                    stmt = text("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
                    tbl_rows = conn.execute(stmt).fetchall()
                    for r in tbl_rows:
                        t_name = r[0]
                        try:
                            c_res = conn.execute(text(f'SELECT COUNT(*) FROM "{t_name}"'))
                            cnt = c_res.scalar() or 0
                        except Exception:
                            cnt = 0
                        results.append({"table_name": t_name, "row_count": cnt})

        except Exception as exc:
            logger.warning(f"Could not inspect existing target tables: {exc}")

        return results

    @staticmethod
    def clean_wipe_target_database(db_url: str, engine_type: str = "postgresql") -> List[str]:
        """
        Completely drops all existing tables or collections in the target database.
        Returns the list of dropped table/collection names.
        """
        dropped_tables: List[str] = []
        if not db_url:
            return dropped_tables

        clean_type = (engine_type or "postgresql").lower().strip()

        # 1. MongoDB: Drop collections
        if clean_type in ("mongodb", "mongo") or "mongo" in db_url.lower():
            try:
                import importlib
                pymongo = importlib.import_module("pymongo")
                clean_url = db_url.split("?")[0]
                db_name = clean_url.rsplit("/", 1)[-1] if "/" in clean_url else "target_db"
                client = pymongo.MongoClient(db_url, serverSelectionTimeoutMS=5000)
                db = client[db_name]
                for coll_name in db.list_collection_names():
                    if coll_name.startswith("system."):
                        continue
                    db.drop_collection(coll_name)
                    dropped_tables.append(coll_name)
                    logger.info(f"[Clean Wipe] Dropped MongoDB collection '{coll_name}'.")
            except Exception as exc:
                logger.error(f"Error during MongoDB target clean wipe: {exc}")
                raise RuntimeError(f"Clean wipe failed for MongoDB target: {exc}")
            return dropped_tables

        # 2. SQL Databases (PostgreSQL, MySQL, SQLite)
        DDLExecutor._ensure_database_exists(db_url)
        engine = _get_engine(db_url)

        try:
            if "postgres" in clean_type or "postgres" in db_url.lower():
                with engine.execution_options(isolation_level="AUTOCOMMIT").connect() as conn:
                    fetch_stmt = text("""
                        SELECT table_name 
                        FROM information_schema.tables 
                        WHERE table_schema = 'public' AND table_type = 'BASE TABLE';
                    """)
                    rows = conn.execute(fetch_stmt).fetchall()
                    for r in rows:
                        t_name = r[0]
                        conn.execute(text(f'DROP TABLE IF EXISTS "{t_name}" CASCADE;'))
                        dropped_tables.append(t_name)
                        logger.info(f"[Clean Wipe] Dropped PostgreSQL table '{t_name}' CASCADE.")

            elif "mysql" in clean_type or "mysql" in db_url.lower():
                from urllib.parse import urlparse
                db_name = urlparse(db_url).path.lstrip('/')
                with engine.connect() as conn:
                    conn.execute(text("SET FOREIGN_KEY_CHECKS = 0;"))
                    fetch_stmt = text("""
                        SELECT table_name 
                        FROM information_schema.tables 
                        WHERE table_schema = :db_name AND table_type = 'BASE TABLE';
                    """)
                    rows = conn.execute(fetch_stmt, {"db_name": db_name}).fetchall()
                    for r in rows:
                        t_name = r[0]
                        conn.execute(text(f'DROP TABLE IF EXISTS `{t_name}`;'))
                        dropped_tables.append(t_name)
                        logger.info(f"[Clean Wipe] Dropped MySQL table '{t_name}'.")
                    conn.execute(text("SET FOREIGN_KEY_CHECKS = 1;"))
                    try:
                        conn.commit()
                    except Exception:
                        pass

            elif "sqlite" in clean_type or "sqlite" in db_url.lower():
                with engine.begin() as conn:
                    conn.execute(text("PRAGMA foreign_keys = OFF;"))
                    rows = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")).fetchall()
                    for r in rows:
                        t_name = r[0]
                        conn.execute(text(f'DROP TABLE IF EXISTS "{t_name}";'))
                        dropped_tables.append(t_name)
                        logger.info(f"[Clean Wipe] Dropped SQLite table '{t_name}'.")
                    conn.execute(text("PRAGMA foreign_keys = ON;"))

        except Exception as exc:
            logger.error(f"Error during target database clean wipe: {exc}")
            raise RuntimeError(f"Clean wipe failed for target database: {exc}")

        return dropped_tables

    @staticmethod
    def _sanitize_ddl_statement(stmt: str, db_url: str) -> str:
        """Sanitizes DDL statements for dialect compatibility across target databases (MySQL, SQLite, PostgreSQL)."""
        import re
        cleaned = stmt.strip()
        url_lower = db_url.lower()

        if "postgres" in url_lower:
            # Replace invalid uuid_v4() / uuidv4() function calls with native gen_random_uuid()
            cleaned = re.sub(
                r'\bDEFAULT\s+(?:uuid_v4|uuidv4)\(\)',
                'DEFAULT gen_random_uuid()',
                cleaned,
                flags=re.IGNORECASE,
            )
            cleaned = re.sub(
                r'\b(?:uuid_v4|uuidv4)\(\)',
                'gen_random_uuid()',
                cleaned,
                flags=re.IGNORECASE,
            )

        elif "mysql" in url_lower:
            # Strip PostgreSQL typecast operators e.g. ::jsonb, ::JSON, ::text
            cleaned = re.sub(r'::[a-zA-Z0-9_]+', '', cleaned, flags=re.IGNORECASE)
            # Convert PostgreSQL Array types e.g. TEXT[], VARCHAR(255)[], INT[] -> JSON
            cleaned = re.sub(r'\b(TEXT|VARCHAR(?:\(\d+\))?|INT|INTEGER|BIGINT|FLOAT|DOUBLE|BOOLEAN)\[\]', 'JSON', cleaned, flags=re.IGNORECASE)
            # Convert DOUBLE PRECISION -> DOUBLE
            cleaned = re.sub(r'\bDOUBLE\s+PRECISION\b', 'DOUBLE', cleaned, flags=re.IGNORECASE)
            # Convert MySQL invalid JSON/TEXT defaults e.g. DEFAULT '[]' -> DEFAULT ('[]')
            cleaned = re.sub(r"DEFAULT\s+'(\[\]|\{\})'", r"DEFAULT ('\1')", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(
                r'\bUUID\s+PRIMARY\s+KEY\s+DEFAULT\s+(?:gen_random_uuid|uuid_generate_v4|uuid_v4|uuidv4)\(\)',
                'VARCHAR(36) PRIMARY KEY',
                cleaned,
                flags=re.IGNORECASE,
            )
            cleaned = re.sub(
                r'\bDEFAULT\s+(?:gen_random_uuid|uuid_generate_v4|uuid_v4|uuidv4)\(\)',
                '',
                cleaned,
                flags=re.IGNORECASE,
            )
            cleaned = re.sub(r'\bUUID\b', 'VARCHAR(36)', cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r'\bTIMESTAMPTZ\b', 'DATETIME', cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(
                r'\bTIMESTAMP\s+WITH\s+TIME\s+ZONE\b',
                'DATETIME',
                cleaned,
                flags=re.IGNORECASE,
            )
            cleaned = re.sub(r'\bJSONB\b', 'JSON', cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r'\bSERIAL\b', 'BIGINT AUTO_INCREMENT', cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r'\bBIGSERIAL\b', 'BIGINT AUTO_INCREMENT', cleaned, flags=re.IGNORECASE)

        elif "sqlite" in url_lower:
            cleaned = re.sub(
                r'\bUUID\s+PRIMARY\s+KEY\s+DEFAULT\s+(?:gen_random_uuid|uuid_generate_v4|uuid_v4|uuidv4)\(\)',
                'TEXT PRIMARY KEY',
                cleaned,
                flags=re.IGNORECASE,
            )
            cleaned = re.sub(
                r'\bDEFAULT\s+(?:gen_random_uuid|uuid_generate_v4|uuid_v4|uuidv4)\(\)',
                '',
                cleaned,
                flags=re.IGNORECASE,
            )
            cleaned = re.sub(r'\bUUID\b', 'TEXT', cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r'\bTIMESTAMPTZ\b', 'TEXT', cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(
                r'\bTIMESTAMP\s+WITH\s+TIME\s+ZONE\b',
                'TEXT',
                cleaned,
                flags=re.IGNORECASE,
            )
            cleaned = re.sub(r'\bJSONB\b', 'TEXT', cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r'\bJSON\b', 'TEXT', cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r'\bSERIAL\b', 'INTEGER PRIMARY KEY AUTOINCREMENT', cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r'\bBIGSERIAL\b', 'INTEGER PRIMARY KEY AUTOINCREMENT', cleaned, flags=re.IGNORECASE)

        return cleaned

    @staticmethod
    def execute_ddl_list(db_url: str, ddl_statements: List[str], stage_label: str = "Pre-Migration DDL"):
        if not ddl_statements:
            logger.info(f"No {stage_label} statements to execute.")
            return

        if db_url.startswith("mongodb://") or db_url.startswith("mongodb+srv://") or "mongo" in db_url.lower():
            logger.info(f"MongoDB target database detected — skipping SQL DDL execution for {stage_label}.")
            return

        DDLExecutor._ensure_database_exists(db_url)

        logger.info(f"Executing {len(ddl_statements)} {stage_label} statements...")
        engine = _get_engine(db_url)

        for stmt in ddl_statements:
            stmt_clean = stmt.strip()
            if "mysql" in db_url.lower() and "create extension" in stmt_clean.lower():
                logger.warning(f"Skipping PostgreSQL-specific DDL statement on MySQL target: '{stmt_clean}'")
                continue

            if "sqlite" in db_url.lower() and "add constraint" in stmt_clean.lower():
                logger.warning(f"Skipping ALTER TABLE ADD CONSTRAINT statement on SQLite target: '{stmt_clean}'")
                continue

            stmt_sanitized = DDLExecutor._sanitize_ddl_statement(stmt_clean, db_url)

            try:
                with engine.begin() as conn:
                    conn.execute(text(stmt_sanitized))
                logger.info(f"Successfully executed DDL: {stmt_sanitized[:80]}...")
            except Exception as exc:
                exc_str = str(exc).lower()

                # Auto-healing for undefined uuid_v4() function on PostgreSQL / MySQL / SQLite
                if "uuid_v4" in exc_str or "uuidv4" in exc_str:
                    logger.info(f"Attempting DDL auto-healing for uuid_v4 in statement: '{stmt_sanitized[:80]}...'")
                    try:
                        import re
                        healed_stmt = re.sub(
                            r'\bDEFAULT\s+(?:uuid_v4|uuidv4)\(\)',
                            'DEFAULT gen_random_uuid()',
                            stmt_sanitized,
                            flags=re.IGNORECASE,
                        )
                        healed_stmt = re.sub(
                            r'\b(?:uuid_v4|uuidv4)\(\)',
                            'gen_random_uuid()',
                            healed_stmt,
                            flags=re.IGNORECASE,
                        )
                        with engine.begin() as conn:
                            conn.execute(text(healed_stmt))
                        logger.info(f"Successfully auto-healed and executed DDL: {healed_stmt[:80]}...")
                        continue
                    except Exception as retry_exc:
                        logger.warning(f"DDL auto-healing retry failed: {retry_exc}")
                        exc = retry_exc
                        exc_str = str(exc).lower()

                # Benign warning keywords (MUST NOT contain 'if not exists', which would match the SQL text!)
                benign_keywords = [
                    "already exists",
                    "duplicate table",
                    "duplicate column",
                    "multiple primary keys",
                    "foreignkeyviolation",
                    "foreign key constraint",
                    "violates foreign key constraint",
                    "referential integrity constraint violation",
                    "cannot add or update a child row",
                ]
                if any(kw in exc_str for kw in benign_keywords):
                    logger.warning(f"{stage_label} notice/warning for statement '{stmt_clean}': {exc}")
                else:
                    logger.error(f"{stage_label} error for statement '{stmt_clean}': {exc}")
                    raise RuntimeError(f"{stage_label} failed for statement '{stmt_clean}': {exc}")


