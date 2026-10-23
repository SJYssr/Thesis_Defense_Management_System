"""归档包归属列的构造点守卫(P9 装配层收口).

迁移 0004 把 archive_package.student_id 收紧为 NOT NULL, 而 ORM 侧保持可空(兼容历史行)。
两边不一致意味着: sqlite 单测(按 ORM create_all 建表)全绿, 真库 MySQL 却会在 INSERT 时报
IntegrityError: Column 'student_id' cannot be null —— 2026-10 的 seed.py 就是这样炸的。

本测试用 AST 扫描所有 ArchivePackage(...) 构造点, 要求显式传 student_id=,
把"漏传"变成单测失败而不是上线后的 500。
"""
import ast
import pathlib
import unittest

BASE = pathlib.Path(__file__).resolve().parent.parent
TARGETS = sorted((BASE / "app").rglob("*.py")) + [BASE / "seed.py"]


def constructions(path: pathlib.Path) -> list[ast.Call]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name == "ArchivePackage":
                found.append(node)
    return found


class ArchiveOwnerConstructionTest(unittest.TestCase):
    def test_every_construction_sets_student_id(self):
        offenders = []
        total = 0
        for path in TARGETS:
            for call in constructions(path):
                total += 1
                kwargs = {kw.arg for kw in call.keywords}
                if "student_id" not in kwargs:
                    rel = path.relative_to(BASE)
                    offenders.append(f"{rel}:{call.lineno}")
        self.assertGreater(total, 0, "没扫到任何 ArchivePackage 构造点, 说明扫描逻辑失效")
        self.assertEqual(offenders, [], f"以下构造点未传 student_id(迁移 0004 后真库会 NOT NULL 报错): {offenders}")

    def test_migration_relaxes_only_after_backfill(self):
        """回填必须发生在收紧 NOT NULL 之前, 否则历史行会让 alter_column 直接失败."""
        text = (BASE / "alembic/versions/0004_archive_package_owner.py").read_text(encoding="utf-8")
        backfill = text.index("UPDATE archive_package")
        tighten = text.index("nullable=False")
        self.assertLess(backfill, tighten)


if __name__ == "__main__":
    unittest.main()
