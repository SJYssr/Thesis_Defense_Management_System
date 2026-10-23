# 仓库约定

## 提交纪律（强制）

- **每完成一次改动就提交**，不要在工作区积压多个批次。一次改动 = 一个可独立回退的 commit。
- **不留本地备份或回退镜像**：不产生 `.bak` / `.orig` / `.rej` / `*_old` / `*.copy` / `备份目录`。
  需要回退时用 `git revert` / `git checkout -- <path>`，回退能力由版本库提供，不由手抄文件提供。
- 临时调试脚本放 `/tmp`，不要落在仓库里。
- commit message 用中文，说明"改了什么 + 为什么"，不写"update"/"fix bug"。

## 建表纪律

- 建表只走 Alembic：`cd backend && .venv/bin/python -m alembic upgrade head`。
- `AUTO_CREATE_TABLES=true` 仅限本地临时库，会在启动时打 warning。
- 改模型必须配套迁移：`alembic revision --autogenerate -m "..."` 后再手工核对。

## 关键不变量（改业务代码前必读）

1. **状态只能经 `app/shared/state_machine.transition()` 变更**。业务层不得直接给 `status` 赋值；
   需要新动作就扩 `_RULES`，不要绕过。
2. **学生是否毕业只读 `app/shared/progress.compute()`**；成绩只读 `DefenseResult.score`；
   当前学年只读 `app/shared/cycle.get_active_cycle()`。不得另立口径。
3. 列表接口一律"先过滤后分页"：用 `app/shared/query.paginate()`，keyword/日期条件下推 SQL。
4. `ReviewRecord.student_id` 为 NOT NULL，无学生维度的实体走 `operation_log`。

## 四套验收（提交前全绿）

```bash
cd backend && .venv/bin/python -m unittest discover -s tests          # 全量单元测试
cd backend && .venv/bin/python seed.py --force \
  && docker exec graduation-redis redis-cli -n 0 FLUSHDB \
  && .venv/bin/python smoke_test.py                                   # 真库端到端(smoke 非幂等, 必须紧接 seed)
cd frontend && npm run build && npm run check:contract && npm run verify:401
```
