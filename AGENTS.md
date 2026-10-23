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
5. **上传口径只经 `app/shared/upload_policy.py` + `app/shared/storage.py`**：扩展名白名单、
   单文件上限、日配额、业务归属全部单源；前端 `accept` 由 `GET /files/upload-policy` 生成，
   不得在前端或某个 router 里另写扩展名清单 / 默认上限。新增上传入口必须复用
   `read_upload(file, max_upload_size_mb(db))` + `ensure_daily_quota()`，且**先校验后迁移状态**。

6. **状态机的副作用列(`archived_at` / `reviewed_at` / `publish_time` 等)改完必须落库**，`transition()` 已在 `_apply_effects` 后 `db.flush()`；新增副作用不要只改内存对象——任何 `expire_all()` / refresh 都会丢弃它，归档率与时间轴会谎报。
7. **AI 配置只有一个来源：数据库**。供应商行在 `ai_provider` 表，能力绑定在 `ai_chat_provider_id` /
   `ai_embedding_provider_id` / `ai_rerank_provider_id` + 对应 `*_model` 键，唯一解析入口是
   `app/ai/runtime_config.resolve_ai_config()`，能力实例只经 `LLMFactory` / `EmbeddingFactory` /
   `get_reranker()` 获取。**不要再引入任何 `LLM_*` / `EMBEDDING_*` / `OLLAMA_*` 环境变量**：
   唯一的环境开关是 `AI_TEST_MODE`（mock/hash 测试替身，`APP_ENV=prod` 启动即失败）。
   改配置写库后必须调 `invalidate_ai_config()`。密钥**只写不读**：`view_of()` 只回 `has_key`/`key_hint`，
   明文只经 `app/ai/crypto.encrypt_api_key()` 落库、只在构造客户端时 `decrypt_api_key()`。
   新增 AI 能力（如语音）也走"供应商 + 绑定键"，不要开新的配置输入框。
8. **探测回环地址必须直连**（`app/ai/net.py`，`trust_env=False`）——系统代理会把"本地服务没起来"
   伪装成网关 502；**辅助动作**（索引、通知、指纹标记）失败只降级 + 记录，不 rollback 主事务
   （`seed.py` 曾因演示论文索引失败把整个种子数据流程打死）。
9. **迁移把列收紧成 NOT NULL 时，必须同步修正全部 ORM 构造点**(`seed.py`、router、测试辅助)，并为关键表加 AST 守卫测试(参见 `backend/tests/test_archive_owner_columns.py`)。原因：测试库按 ORM `create_all` 建表(可空)，单测会全绿而真库 INSERT 直接炸。

## 四套验收（提交前全绿）

```bash
cd backend && .venv/bin/python -m unittest discover -s tests          # 全量单元测试
# smoke 用**显式**测试替身跑: 本机默认没有模型也没有 Key, 不显式指定会走 50301"未配置"分支。
# AI_TEST_MODE=mock 同时把会话替身成模板拼接、把向量替身成本地哈希; 只影响质量, 不影响接口契约;
# APP_ENV=prod 时启动即报错(防误上生产)。要跑真实模型: 在管理端「系统设置 → AI 服务」添加供应商并绑定,
# 不要用环境变量(后端已不读任何 AI 供应商环境变量)。
cd backend && AI_TEST_MODE=mock .venv/bin/python seed.py --force \
  && docker exec graduation-redis redis-cli -n 0 FLUSHDB \
  && AI_TEST_MODE=mock .venv/bin/python smoke_test.py   # 真库端到端(非幂等, 必须紧接 seed)
cd frontend && npm run build && npm run check:contract && npm run verify:401
```
