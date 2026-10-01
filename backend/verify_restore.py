"""Read-only comparison of a restored MySQL database and upload directory.

Usage: python verify_restore.py --restored-db <temporary_db> \
    --source-uploads uploads --restored-uploads <temporary_dir>/uploads
"""
import argparse
import hashlib
import json
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.core.config import settings


def table_fingerprints(engine):
    inspector = inspect(engine)
    fingerprints = {}
    with engine.connect() as connection:
        for table in sorted(inspector.get_table_names()):
            columns = [column["name"] for column in inspector.get_columns(table)]
            primary_key = inspector.get_pk_constraint(table).get("constrained_columns") or columns
            quoted = engine.dialect.identifier_preparer.quote
            sql = f"SELECT * FROM {quoted(table)} ORDER BY {', '.join(quoted(column) for column in primary_key)}"
            digest = hashlib.sha256()
            count = 0
            for row in connection.execute(text(sql)).mappings():
                digest.update(json.dumps(dict(row), sort_keys=True, default=str, ensure_ascii=False).encode("utf-8"))
                digest.update(b"\n")
                count += 1
            fingerprints[table] = {"columns": columns, "rows": count, "sha256": digest.hexdigest()}
    return fingerprints


def file_fingerprints(root: Path):
    fingerprints = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        fingerprints[str(path.relative_to(root))] = digest.hexdigest()
    return fingerprints


def missing_referenced_files(engine, root: Path):
    if "sys_file" not in inspect(engine).get_table_names():
        return []
    root = root.resolve()
    with engine.connect() as connection:
        paths = connection.execute(text("SELECT storage_path FROM sys_file WHERE deleted_at IS NULL")).scalars()
        missing = []
        for stored_path in paths:
            target = (root / stored_path).resolve()
            if not target.is_relative_to(root) or not target.is_file():
                missing.append(stored_path)
    return sorted(missing)


def main():
    parser = argparse.ArgumentParser(description="Compare a restored backup with the current development database")
    parser.add_argument("--restored-db", required=True)
    parser.add_argument("--source-uploads", type=Path, required=True)
    parser.add_argument("--restored-uploads", type=Path, required=True)
    args = parser.parse_args()

    source_url = make_url(settings.sqlalchemy_database_uri)
    source_engine = create_engine(source_url, pool_pre_ping=True)
    restored_engine = create_engine(source_url.set(database=args.restored_db), pool_pre_ping=True)
    try:
        source_tables = table_fingerprints(source_engine)
        restored_tables = table_fingerprints(restored_engine)
        missing_references = missing_referenced_files(restored_engine, args.restored_uploads)
    finally:
        source_engine.dispose()
        restored_engine.dispose()
    source_files = file_fingerprints(args.source_uploads)
    restored_files = file_fingerprints(args.restored_uploads)
    table_mismatches = [table for table in sorted(set(source_tables) | set(restored_tables))
                        if source_tables.get(table) != restored_tables.get(table)]
    file_mismatches = [path for path in sorted(set(source_files) | set(restored_files))
                       if source_files.get(path) != restored_files.get(path)]
    result = {"tables_checked": len(source_tables), "rows_checked": sum(item["rows"] for item in source_tables.values()),
              "files_checked": len(source_files), "table_mismatches": table_mismatches,
              "file_mismatches": file_mismatches, "missing_referenced_files": missing_references,
              "match": not table_mismatches and not file_mismatches and not missing_references}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["match"] else 1)


if __name__ == "__main__":
    main()
