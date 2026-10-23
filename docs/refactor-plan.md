# 毕业设计（论文）答辩管理系统 · 业务流程重构方案

> 状态：已由 YG 批准执行（2026-xx）。基线：49 unittest / 141 smoke / 前端 build 三绿，docker mysql 3306 + redis 6380 可用。

## 1. 目标与非目标

目标：让"选题 → 开题 → 中期 → 终期 → 答辩安排 → 正式结论 → 材料归档 → 毕业判定"这条主链在任何边界条件下都是**单向、可判定、可追溯**的。

非目标：不重写数据模型主干、不推翻现有 REST 路径与前端路由（前端只做契约对齐与错误提示统一）。

## 2. 已证实的结构缺陷（重构依据）

| # | 位置 | 缺陷 | 后果 |
|---|---|---|---|
| D1 | `app/shared/cycle.py:29,52` | `ensure_cycle` 新建不归档旧 cycle；`get_active_cycle` 取 id 最大者 | 同一学生跨学年多条 ACTIVE，统计口径错 |
| D2 | `app/shared/cycle.py:62` | `formal_defense_snapshot` 绕开 `get_active_cycle`，按学年二次查 | 学年配置切换后学生查不到答辩结论 |
| D3 | `app/shared/progress.py:76` | 材料 APPROVED 直接判 100% COMPLETED，不读 `DefenseResult` | 未通过答辩也显示已毕业 |
| D4 | `app/shared/workflow.py:166` | `STAGE_ORDER.index(COMPLETED)` → ValueError | 完成态触发 500 |
| D5 | `app/shared/workflow.py` | `on_stage_approved` 手写 progress_percent 与 recompute 双写 | 进度被覆盖 |
| D6 | `app/modules/stages/engine.py:263` | 审核通过写 `FinalDefense.score`，正式成绩在 `DefenseResult.score` | 双轨评分，前端混用 |
| D7 | `app/modules/stages/engine.py:267` | 驳回语义耦合 comment 是否为空 | 无法显式选择"驳回/退回修改" |
| D8 | `app/modules/stages/engine.py:112` | `cycle.id` 空指针 | 边界 500 |
| D9 | `app/modules/stages/engine.py:245` | 仅出题教师可读材料，被指派评委无通路 | 评阅断链 |
| D10 | `app/modules/stages/router_factory.py:88` | 内存过滤在分页之后 | total 失真 |
| D11 | `app/core/response.py:24` | 所有错误 HTTP 200 | 传输层无法区分，拦截器失效 |
| D12 | `app/core/config.py:16` | SECRET_KEY 有公开默认值且无校验 | 可离线伪造 ADMIN token |
| D13 | `app/modules/auth/router.py:120` | 登出不删 REFRESH 键 | 登出后可换新 token |
| D14 | `app/modules/auth/router.py:160` | 改密不作废旧 access token | 旧 token 仍可用 60 分钟 |
| D15 | `app/core/permissions.py:45` | Redis.exists 无降级 | Redis 抖动全站 500 |
| D16 | `app/shared/storage.py:41` | startswith 前缀校验可被同前缀兄弟目录绕过 | 路径穿越 |
| D17 | `app/shared/storage.py:53` | 先整读后判大小 | 内存打满 |
| D18 | `app/core/config.py:32` + `.env:16` | UPLOAD_DIR 相对路径未 resolve | CWD 变化即失联 |
| D19 | `app/modules/defense/router.py:485` | ADMIN 短路进评分写接口 | examiner_id=NULL → 500 |
| D20 | `app/modules/defense/router.py:739` | 发布不校验场次状态 | 已取消场次仍发成绩 |
| D21 | `app/modules/user/router.py:24` | 新建账号默认口令 123456 | 批量弱口令 |
| D22 | `app/modules/user/router.py:106` | 用户增删改无 OperationLog | 特权变更无审计 |
| D23 | `app/modules/audit/router.py:70` | result 字段无写入点，查询兜底 SUCCESS | 审计结论失真 |
| D24 | `app/modules/defense/router.py:658` | 死代码 sp_user，评分齐备无通知 | 学生无感知 |
| D25 | `app/modules/user/router.py:60` | 死代码 _user_list_query | 双份过滤易漂移 |
| D26 | `app/modules/archive/router.py:211-238` | package_detail 只校验"已登录"，不校验归属/授权；姓名、学号、成绩、结论在可见性判断之前就已写入返回体 | **越权读他人私有归档**（改 URL 的 package_id 即可） |
| D27 | `app/modules/selection/router.py:162-181` | 名额复查在 MySQL REPEATABLE READ 下读的是 L162 旧快照，with_for_update 只锁 topic 行锁不住并发审批结果 | **超额录取**（两人可同时通过最后一名） |
| D28 | `app/modules/archive/router.py:136-145` | 唯一约束在 cycle_id，代码只拦 status=="ARCHIVED"，REVOKED 旧行存在时照样 INSERT | 撤回后重新归档必然 500 |
| D29 | `app/modules/archive/router.py:114-121` | 归档前置校验取"最新一条 FINAL 结论"而非"最新一条 PUBLISHED"，where 不含 status | 有新的待发布结论时，历史 PASS 也被判不满足 |
| D30 | `app/modules/material/router.py:243-245` + `shared/workflow.py:156` | 审核通过推进流程用 active cycle 而非被审核行的 m.cycle_id | 审核历史材料把进度写到错误流程实例 |
| D31 | `app/modules/stages/engine.py:256-284` | 审核"读-判断-改-commit"无行锁，非原子 | 并发双审产生两条 ReviewRecord，第二次不报错 |
| D32 | `app/modules/material/router.py:131` + `stages/engine.py:118` | 新建对象 status 为 None（列默认值只在 INSERT 生效），`== "NOT_SUBMITTED"` 永假 | 草稿态不可达 |
| D33 | `app/modules/archive/router.py:197-208` | keyword 在分页后过滤，且把 total 改写成本页命中数 | 跨页检索丢数据、总数错乱 |
| D34 | `app/modules/archive/router.py:169-178,245-263` | 归档包 status 不参与权限判断 | 已撤回的包仍可被检索、仍可被重新授权公开 |
| D35 | `app/modules/defense/service.py:223-231` | 缺项评委仍参与其他项均值（分母不一致）；总分用 Python round() 银行家舍入 | 分数算错，可能跨过/错过 pass_line |
| D36 | `app/modules/selection/router.py:187-189` | 管理员代审时审核记录硬编码 reviewer_role="TEACHER" | 审计链失真 |
| D37 | `app/modules/material/router.py:84,87-88` | 可编辑状态集合与材料中文名各写两份（与 stages EDITABLE_STATUSES、MATERIAL_TYPE_CN 漂移） | 规则双源 |
| D38 | `app/shared/workflow.py:141` | on_selection_approved 调 ensure_cycle 时丢弃已有的 selection.cycle_id | 同一学生第二条 cycle 时数据漂移 |
| D39 | `app/ai/prompts.py:132,169` | PPT_OUTLINE 与 PROGRESS_ANALYSIS 未标 untrusted，学生可控文本直拼 prompt；resolve_system_prompt 用子串短路判断是否追加防护声明 | **prompt 注入**（自定义内容里写一遍声明即可绕过） |
| D40 | `app/modules/material/router.py:144-153` | 论文每次上传都新建 THESIS 的 AIDocument，旧文档及 chunk 不作废，RAG 同时检索新旧两版 | 检索到过期版本，答案错 |
| D41 | `app/modules/ai/router.py:39-42` | 限流 INCR 与 EXPIRE 非原子、无 try/except | Redis 故障 AI 全站 500；进程在两步之间崩溃则该用户永久 42901 |
| D42 | `app/ai/rag/rag_service.py:88` | embedding 仅 `vector_store.mode=="mysql"` 时写入；mode 进程内只判一次 | Redis 模式失效后降级检索召回恒空 |
| D43 | `app/ai/rag/retriever.py:61` + `vector/store.py:130,174` | `in_([student_id, None])` 在 SQL 不匹配 NULL 行，而 Redis 侧用 "0" 代表全局 | 全局知识库在降级路径永不命中，global_scope 是空承诺 |
| D44 | `app/ai/rag/retriever.py:118` | 向量命中后回表取 chunk 不带 student_id 过滤 | 索引残留即跨学生泄露 |
| D45 | `app/ai/rag/rag_service.py:126` | `scalar_one_or_none` 取 APPROVED 报告但不按 cycle 过滤 | 跨学年两份 APPROVED → MultipleResultsFound 500 |
| D46 | `app/ai/defense/service.py:319-323` | 结束判定把追问轮计入 answered | 有追问时正题未答完就提前交卷 |
| D47 | `app/ai/rag/rag_service.py:191-203` | SSE 无 error 事件；AIUsageLog 在流末尾写 | 前端分不清"失败"与"结束"；异常路径丢计费 |
| D48 | `app/ai/defense/service.py:84,436` | generate_questions / generate_ppt_outline 的 extract_json 无兜底 | LLM 返回非 JSON → 500（同文件其它处都有兜底） |
| D49 | `frontend/src/api/request.ts:29` | 拦截器无 401 分支；`api/auth.ts:11` 的 refresh 全仓无调用；`router/index.ts:74` 守卫只判 token 存在不判过期 | access 60 分钟过期后所有页面静默失效 |
| D50 | `app/ai/vector/store.py:132` + `modules/ai/router.py:49` | document_types 未枚举校验就拼进 RediSearch TAG 表达式；list_documents 同一依赖注入两次且无分页 | 查询语法注入/解析错误；学生文档多时全量返回 |
| D51 | `app/ai/defense/service.py:190,255` | seen_types 从不写入，"优先未覆盖题型"恒失效；evaluate_answer 不校验客户端传入 question 与 turn 是否一致 | 题型多样性失效；可提交与题不符的答案 |

## 3. 目标架构

```
routers (modules/*/router.py)  —— 只做 HTTP 编解码、鉴权依赖、参数校验
        ↓
domain services (modules/*/service.py | engine.py)  —— 业务规则
        ↓
shared kernel
  ├─ state_machine.py   单一转移表 + transition() 唯一入口
  ├─ cycle.py           cycle 生命周期（唯一 ACTIVE）
  ├─ progress.py        进度与完成判定的唯一真源
  ├─ workflow.py        stage 级编排（调用 state_machine，不手写百分比）
  ├─ permissions.py     cycle 维度授权（学生/导师/评委/管理员）
  └─ audit.py           OperationLog 统一写入（含 result）
        ↓
models / db
```

**核心原则**
1. 任何状态变更必须经 `transition()`；router/service 不得直接赋值 `entity.status`。
2. 任何"分数"只有一个真源：`DefenseResult.score`。
3. 任何"进度/是否完成"只有一个真源：`progress.compute()`。
4. 任何"这个用户能不能对该 cycle 做该动作"只有一个入口：`permissions.can(actor, action, cycle)`。

## 4. 统一状态机

`app/shared/state_machine.py` 定义：

```python
Entity = Literal["TOPIC","SELECTION","OPENING","MIDTERM","FINAL","MATERIAL","SESSION","SEAT","RESULT","ARCHIVE"]

TRANSITIONS: dict[Entity, dict[str, set[str]]]  # entity -> action -> 允许的源状态
```

| 实体 | 动作 | 允许源状态 | 目标状态 | 附加守卫 |
|---|---|---|---|---|
| SELECTION | submit | PENDING | PENDING | 题目 PUBLISHED 且未满 |
| SELECTION | approve | PENDING | APPROVED | 同 cycle 内 active_flag 唯一 |
| SELECTION | reject | PENDING | REJECTED | 需 comment |
| OPENING/MIDTERM/FINAL/MATERIAL | save | NOT_SUBMITTED, DRAFT, REJECTED, RESUBMIT_REQUIRED | DRAFT | — |
| 同上 | submit | DRAFT, REJECTED, RESUBMIT_REQUIRED | SUBMITTED | 必填字段齐全；late_policy |
| 同上 | withdraw | SUBMITTED | DRAFT | 审核未开始 |
| 同上 | approve | SUBMITTED | APPROVED | 权限=导师/管理员 |
| 同上 | reject | SUBMITTED | REJECTED | 需 comment，终态 |
| 同上 | request_revision | SUBMITTED | RESUBMIT_REQUIRED | 需 comment |
| SESSION | publish | DRAFT | PUBLISHED | 至少 1 席位；时间合法 |
| SESSION | start | PUBLISHED | ONGOING | 已到 starts_at |
| SESSION | finish | ONGOING | FINISHED | — |
| SESSION | cancel | DRAFT, PUBLISHED, ONGOING | CANCELLED | 需原因 |
| SEAT | attend | SCHEDULED | ATTENDED | 场次 ONGOING/FINISHED |
| SEAT | absent | SCHEDULED | ABSENT | 场次 FINISHED |
| RESULT | compute | PENDING | READY | 有效评分 ≥ min_examiners |
| RESULT | publish | READY, UNDER_REVIEW | PUBLISHED | 场次 ∈ {ONGOING, FINISHED}；with_for_update |
| RESULT | withdraw | PUBLISHED | WITHDRAWN | 需原因；同步回滚 cycle |
| ARCHIVE | submit | NOT_SUBMITTED, DRAFT | SUBMITTED | **前置：RESULT=PUBLISHED** |
| ARCHIVE | approve | SUBMITTED | APPROVED | — |
| CYCLE | complete | ACTIVE | COMPLETED | **前置：ARCHIVE=APPROVED 且 RESULT.decision=PASS** |

`transition(db, entity, obj, action, actor, *, comment=None, **payload) -> obj`
- 非法转移：抛 `BusinessException(code=40003, message="当前状态 X 不允许动作 Y")`
- 权限不足：抛 `PermissionDeniedException`
- 副作用（版本号 +1、留痕、通知）由 transition 统一触发，业务层不再各写一遍

**向后兼容**：`SUBMITTED` 保留为唯一"待审核"态，不再引入 UNDER_REVIEW 以避迁移成本；如需评审认领，用 `StageTask` 已存在的字段扩展，不改主链。

## 5. cycle 生命周期（D1/D2）

- `ensure_cycle(db, student_id)`：查 (当前学年, student_id) → 无则创建；创建前把该学生其它 `status="ACTIVE"` 的 cycle 置 `COMPLETED`（不动业务数据，仅状态与 completed_at）。
- `get_active_cycle(db, student_id)`：`status=="ACTIVE"` 且取最新，**并断言唯一**（>1 条则 log.error 并取最新，不静默）。
- `formal_defense_snapshot`：改为完全基于 `get_active_cycle`，删掉按学年二次查询的路径。
- 新增 `list_cycles(db, student_id)` 供学生端展示历史学年。

## 6. 评分单轨（D6）

- `DefenseResult.score` = 唯一正式成绩（rubric 加权，服务端复算，`rubric_snapshot` 可复算）。
- `FinalDefense.score` 语义收窄为"阶段材料审核的教师参考分"，字段保留但：
  - `stages/engine.py:approve` 不再写入它；
  - 迁移脚本把已有值搬进 `stage_review_score`（新列）后清空；查询端一律用新列。
- 前端展示成绩只读 `DefenseResult`；阶段页只读 `stage_review_score`。

## 7. 进度与毕业判定（D3/D4/D5）

`progress.compute(db, student_id, cycle)` 返回 `ProgressSnapshot(percent, stage, completed, blockers[])`：

| 阶段 | 权重 | 达成条件 |
|---|---|---|
| 选题 | 10 | SELECTION=APPROVED |
| 开题 | 20 | OPENING=APPROVED |
| 中期 | 25 | MIDTERM=APPROVED |
| 终期 | 25 | FINAL=APPROVED |
| 材料 | 20 | MATERIAL=APPROVED |
| 答辩 | 附加 | RESULT.status=PUBLISHED |

- `completed=True` 仅当 **材料 APPROVED 且 RESULT.decision=PASS 且 ARCHIVE=APPROVED**；否则 `completed=False`，`blockers` 列出未达成项。
- 材料齐全但答辩未过：percent 封顶 `90`，`blockers=["答辩未通过"]`。
- 删掉 `on_stage_approved` 里对 `progress_percent` 的写操作，只调 `recompute`。
- `STAGE_ORDER` 增加 `COMPLETED` 边界保护：`next_stage()` 用 `dict` 映射而非 `index+1`，末尾返回 `None`。

## 8. 权限模型（D9/D19）

`permissions.can(actor, action, cycle_id) -> bool`，动作枚举：

- `STAGE_SAVE / STAGE_SUBMIT`：学生本人（cycle.student_id 对应 profile）
- `STAGE_REVIEW`：cycle.teacher_id 的教师 或 ADMIN
- `STAGE_READ`：学生本人 / 导师 / 该 cycle 所有 `DefenseExaminer` / ADMIN
- `SCORE_WRITE`：仅 TEACHER 且是该场次 `DefenseExaminer`（ADMIN 降级为只读，去掉短路）
- `RESULT_PUBLISH / WITHDRAW`：仅 ADMIN
- `ARCHIVE_APPROVE`：仅 ADMIN
- `ARCHIVE_READ`：学生本人 / 导师 / ADMIN（公开需授权位）

`get_for_review` 增加评委通路：`STAGE_READ` 通过即返回只读视图（`can_edit=False`）。

## 9. 列表与分页（D10/D25）

统一 `shared/query.py`：

```python
def paginate(db, stmt, *, page, size, keyword=None, keyword_fields=(), filters=()):
    # 所有过滤/关键字下推到 SQL，先 count 后取页
```

所有 `admin_list / teacher_list / student_list` 改用该函数；删除 `_user_list_query` 死代码。

## 10. 安全与配置硬化（D12–D18）

- `core/config.py`：`secret_key` 无默认值 + `@field_validator` 拒绝示例串/长度<32；`upload_dir` 统一 `.resolve()`；`.env.example` 提供生成命令。
- `core/security.py`：新增 `token_version` 参与 payload；`decode_token` 校验版本。
- `sys_user.token_version int default 0`：改密/改角色/登出时 +1，立即作废旧 access token。
- `auth.logout`：删 REFRESH 键 + 黑名单 access jti + token_version+1。
- `permissions.py`：Redis 异常降级（`except RedisError: log.warning` 放行），并用 token_version 做 DB 侧兜底校验。
- `shared/storage.py`：`relative_to` 校验；分块读取（1 MiB）超限立即中断。
- `core/response.py`：错误体加 `http_status` 映射（40100→401、40300→403、40400→404、400xx→400、42200→422、500xx→500），body 结构不变。
- `user/router.py`：新建账号密码必填（≥8，含字母+数字），或 `must_change_password=True` 强制首登改密。
- `defense/router.py`：publish 前校验 `session.status ∈ {ONGOING, FINISHED}` + `with_for_update()`；`submit_score` 修正死代码并补通知。

## 11. 审计（D22/D23）

`shared/audit.py`：`record(db, actor, module, action, **fields)`，自动补 `result/method/path/ip/duration_ms`。
- user 模块 create/update/password/status/delete 全部接入
- 失败分支写 `result="FAIL"`
- `audit/router.py` 查询端改 `row.result or "UNKNOWN"`

## 12. 迁移策略（Alembic）

1. `alembic init` → `env.py` 读 `settings.database_url`，`target_metadata = Base.metadata`。
2. 以当前库结构生成基线 `0001_initial.py`，并在 `alembic_version` 记录（对空库 `create_all` 兼容：main.py 启动只在无 alembic_version 时 `create_all`，否则跳过）。
3. 增量迁移：
   - `0002_add_token_version`：`sys_user.token_version` + `must_change_password`
   - `0003_stage_review_score`：`final_defense.stage_review_score`，数据搬运后清空旧列
4. `seed.py` 保留 `create_all` 路径，用于演示库一键重建；新增 `--alembic` 开关走迁移。

## 13. 前端对齐

- `src/api/request.ts`：按 HTTP 状态 + body code 双判；401 → 清 token + 跳登录；403 → 统一提示；400xx → toast。
- 三端（student/teacher/admin）共用同一套错误码映射表 `src/constants/errorCode.ts`。
- 阶段页/答辩页分数来源改单轨（阶段=stage_review_score，答辩=DefenseResult.score）。
- 进度页读 `blockers` 展示未达成项。

## 14. 任务分解与依赖

| 批次 | 任务 | 边界 | 依赖 |
|---|---|---|---|
| P1-A | 状态机内核 + shared 层 + stages 引擎 | `app/shared/{state_machine,workflow,progress,cycle,query}.py`、`app/modules/stages/**` | 无 |
| P1-B | 安全/配置/存储/审计硬化 + Alembic 基线 | `app/core/**`、`app/shared/{storage,audit}.py`、`app/modules/{auth,user,audit,file}/**`、`app/models/system.py`、`alembic/**` | 无 |
| P2 | 主链闭环：defense（评分单轨/舍入/发布守卫/通知）+ archive（越权/撤回重归档/前置校验/分页/授权）+ selection（名额并发/角色留痕）+ material（cycle 归属/状态常量复用） | `app/modules/{defense,archive,selection,material}/**` | P1-A |
| P3 | AI/RAG 加固：prompt 注入防护、版本唯一、降级一致性、SSE 错误事件、AI 答辩判定 | `app/ai/**`、`app/modules/ai/**` | 无（可与 P2 并行） |
| P4 | 前端契约对齐 + 错误码统一 + token 刷新 | `frontend/src/**` | P1-B（HTTP 状态稳定后） |
## 6quinquies. P2-A 验收记录与我的补表（答辩主链 + 评分单轨）

**P2-A 交付**：`modules/defense/router.py`(+177/−107)、`service.py`(328 行变动)、新建 `seat_state.py`(69 行)、`tests/{test_defense_chain(24),test_defense_scoring(15)}`。全量 **279 项 unittest 全绿**（基线 198），smoke **161 通过**，并发实测 `outcomes=[('ok',85),('err',40003)]` 且库中 PUBLISHED 恰 1 条。

关键实现：
- `service.py:486-531` publish/start/finish/cancel_session 内部只调 `transition(db, SESSION, …)`，补齐原先缺失的 `POST /defense-sessions/{id}/start|finish`。
- `seat_state.py:35-43` 用 sqlalchemy `synonym` 把 `seat_status` 以 `status` 别名注册，从而复用统一状态机的条件更新，**不改 models 即可**；`:46-66` mark_seat 带场次活态守卫与"ABSENT 须场次已结束"约束。
- `service.py:200-250` **D35 重写**：三级样本过滤（SEAT_NOT_ATTENDED > EXAMINER_NOT_ASSIGNED > MISSING_ITEMS，命中即整份剔除并写 `score_detail_json.summary.excluded`），`Decimal` + `ROUND_HALF_UP`；`:234-242` 发布分对**原始精确分单步取整**，避免两段舍入把 59.49 抬成 60；边界用例 59.95→60.0 / 59.94→59.9 / 59.49→59 / 恰等 pass_line→PASS 全部有断言。
- `service.py:587-605` publish_result 守卫链（场次态 → 未到场 → 有效评委数 → 分数非空）全部 40003 且状态保持 READY。
- 评分单轨：defense 侧不再写 `FinalDefense.score`，`test_scoring_is_single_track_final_defense_score_untouched` 断言 publish 后 `DefenseResult.score==80` 而 `FinalDefense.score is None`。

**我补的两组转移规则（P2-A 报备的禁区项，属于装配层收口）**
P2-A 报告里两条"需协调"如果放着不管就是**真回归**：
1. `shared/state_machine.py` 的 SEAT 只有 attend/absent，而 `frontend/src/views/admin/DefenseSchedule.vue:227` 的「延期」按钮传 `POSTPONED`、`frontend/src/views/student/MyDefense.vue:10` 展示 `POSTPONED/RETAKE`——上线即 40003，延期答辩入口当场断掉。已补 `SEAT.postpone{SCHEDULED,ATTENDED}→POSTPONED`、`SEAT.retake{ABSENT,POSTPONED}→RETAKE`、`SEAT.reset{POSTPONED,RETAKE}→SCHEDULED`，并让 `seat_state.SEAT_ACTION_BY_TARGET` 覆盖全部五个目标态、删掉 `SEAT_TARGETS_AWAITING_RULES`。
2. `service.py:621-631` 的 `mark_result_under_review` 是 defense 侧唯一的绕过状态机直写（`result.status = UNDER_REVIEW`）。已补 `RESULT.review{PUBLISHED}→UNDER_REVIEW`，把该函数改成走 `transition(db, RESULT, result, "review", actor, comment=reason)` 并新增 `actor` 形参，`router.py:856` 传 `user`。
3. **两条测试原本断言的是"未定义"这一实现细节**（`test_postponed_not_in_transition_table` 断言 40003、`test_entities_cover_plan` 硬编码规则总数 40），扩表必红。已改为语义断言：前者验证 POSTPONED→RETAKE→SCHEDULED 真实可达 + 未知目标态仍 40003；后者改为"每个实体都有规则 + 关键动作在表内"，不再锁总数。

**保持不改的三项**
- `defense_result.score` 仍是 INT，`DECIDE_ON_PRECISE=False`：0.1 精度判定需要 `DECIMAL(4,1)`+迁移，否则会出现"页面 60 分但结论 FAIL"的自相矛盾。要开精度就改列 + 置 True，两者必须同批。
- `defense/service.py:660-695` 删掉 submit_score 的 `result→PENDING` 回写：转移表无 invalidate，回写会让 publish 前置态丢失。真正的"评分变更→结论失效"应作为 `RESULT.invalidate{READY,UNDER_REVIEW}→PENDING(internal)` 另立规则，暂不引入。
- 审计动作码 `START_SESSION/FINISH_SESSION/CALCULATE_RESULT` 无需白名单：`shared/audit.py` 无动作白名单机制（已 grep 确认），OperationLog.action 是自由文本。

## 6quater. P1-A 验收记录（状态机内核 + shared 层 + stages，已独立复核）

**结论：7 条验收 + A1/A2/A3 全部落地。** 新建 `state_machine.py`(385 行/40 条转移) 与 `query.py`(56 行)，改 5 文件 +508/−179，新增 4 个测试文件 65 例。全量 unittest **198 项全绿**，smoke **161 通过**。

我本人复核的关键实现：
- `app/shared/state_machine.py:259` `transition()` 是唯一入口：先校验源状态 → 权限 → require_comment → guard → 条件更新 → 副作用/留痕。`:292` `_apply_status` 用 `UPDATE ... WHERE pk=? AND status=<current>` 且 `rowcount != 1` 即抛 40003——**这是并发双审的根治手段，比加锁更可靠（sqlite 上 with_for_update 是 no-op，但条件更新在两库都生效）**。
- `app/shared/cycle.py:33` `_demote_other_active` 用 `synchronize_session='evaluate'`（原来的 False 会让 session 内已加载对象仍显示 ACTIVE，被测试抓到）；`ensure_cycle` 建新前先降级旧的 → 跨学年多 ACTIVE 问题根治。
- `app/shared/progress.py:166-176` `completed = 材料APPROVED and decision=='PASS' and 归档已批准`，否则 `percent = min(percent, 90)` → D3「材料通过即 100% 毕业」彻底修掉，答辩未过现在最多 90 且带 blockers。
- `app/shared/workflow.py:35-45` `NEXT_STAGE` 改 dict 映射，`next_stage(COMPLETED/'COMPLETED'/未知)→None`，不再 ValueError→500；`on_stage_approved` 删掉手写 progress，只调 `recompute(cycle_id=...)`（A3 治理写错流程实例）。
- `app/modules/stages/engine.py:173` 新建对象显式 `status='NOT_SUBMITTED'` 再 flush 后走 `transition('save')`（A1 草稿态可达）；`:386` 不再写 `FinalDefense.score`（评分单轨起点）。

**我做的装配层修正（不属任何批次）**
`state_machine.py:156` 的 SESSION start 目标态原为 `"ONGOING"`，与 `app/models/enums.py:111` 的 `SessionStatus.IN_PROGRESS` 及 `frontend/src/views/admin/DefenseSchedule.vue:19` 的配色键冲突——一旦 defense 接入状态机就会把 `ONGOING` 写进库，前端与查询全部失效。已新增 `SESSION_IN_PROGRESS = "IN_PROGRESS"` 并把 start 指向它，`SESSION_ONGOING` 仅保留历史来源兼容。修后全量 198 项仍全绿。

**遗留三项**
1. `backend/app/modules/material/router.py:244-245` 调 `on_stage_approved/recompute` 未传 `cycle_id`（回退逻辑不报错，但跨学年会写错流程实例）→ 已下发给 P2-B。
2. `FinalDefense.score` 新数据恒 None、`ReviewRequest.score` 入参被忽略 → 已通知 P4 在契约表标注「材料审核不再单独评分，正式成绩读 DefenseResult.score」。
3. `progress.py:55` 的 `ARCHIVE_APPROVED_STATUSES` 含不存在的 `"APPROVED"`（ArchiveStatus 实为 PENDING/ARCHIVED/REVOKED）→ 已下发给 P2-B 做二选一结论。

## 6ter. P1-B 验收记录（安全/配置/存储/审计 + Alembic，已独立复核）

**结论：D11–D18、D21–D23 全部落地，Alembic 双向可逆实测通过。** 改动 9 文件 + 新建 10 文件（含 alembic 全量基线 1062 行），新增 4 个测试文件 54 例。

我本人复核的关键实现（读代码，非采信自述）：
- `app/core/config.py:29`：`secret_key: str` **无默认值**，缺失即 pydantic 启动失败；`:66-78` validator 双重拦截——长度 < 32 直接 ValueError，命中 `WEAK_SECRET_MARKERS`（change-me-in-production / secret-key-change-me / your-secret-key / changeme）也 ValueError。示例串再也无法上生产。
- `app/core/response.py:14-40`：`EXPLICIT_HTTP_STATUS` 显式表 + `code // 100` 推导兜底（40001→400、42201→422），并保留「直接传 HTTP 状态码」的历史兼容分支；`:54-58` `error()` 用 `JSONResponse(status_code=...)`，**body 仍是 {code,message,data}**——前端解析路径无需改动，这一点很关键。
- `app/core/security.py`：payload 增 `TOKEN_VERSION_CLAIM`，`decode_token(..., expected_token_version=)` 版本不一致抛「令牌已失效,请重新登录」；历史无该 claim 的令牌按 0 处理，向后兼容。
- `app/core/permissions.py:46-56`：`_is_blacklisted` 捕 `redis.RedisError` → warning + 降级放行，**Redis 抖动不再全站 500**（这是我之前待验证的遗留点，已确认修好）。版本校验在 DB 侧兜底，所以降级期间旧令牌依然拦得住。
- `app/shared/storage.py:50-55`：`abs_path` 用 `Path.relative_to` 捕获 ValueError 判越界。旧实现用字符串 startswith，`../uploads_evil` 这种兄弟前缀能逃出去；新版拦住（测试里专门有这个用例）。`:65-90` `read_upload` 按 1MiB 分块、累计超限立即抛，不再整读进内存。
- `app/shared/audit.py`：`record()` 用 ContextVar 自动补 method/path/ip，`SQLAlchemyError` 只 warning + rollback 不阻断主流程；user 模块五个动作（create/update/password/status/delete）成功写 SUCCESS、异常分支写 FAIL 带 reason。

**Alembic 实测（真库）**：空库 → `upgrade head` → `downgrade base`（剩余仅 alembic_version）→ 再 `upgrade head`（53 表）→ `seed.py` 成功；`current = 0002_user_token_version (head)`；模型 52 表与库 53 表（含 alembic_version）零漂移。

**遗留四项**
1. `app/main.py:22-29` 的 `AUTO_CREATE_TABLES` 默认 `true` + `Base.metadata.create_all` 仍在，与 Alembic 基线策略直接冲突（照此启动会让迁移基线漂移）。**由我在装配层修正**：默认改 false，启动检测 `alembic_version` 缺失时明确报错并提示 `alembic upgrade head`，仅保留显式 `AUTO_CREATE_TABLES=true` 的开发逃生口。
2. `update_user` 无改角色代码路径（角色由路径决定、不匹配即 404），计划中「改角色也 token_version+1」暂无落点；将来新增该接口时须在此处 +1。
3. `SysUser` 实际定义在 `app/models/user.py` 而非任务书以为的 `models/system.py`，两列落在此处（正确做法）。
4. smoke 非幂等 + 登录限流 10 次/60s 按 IP 计，复现顺序：DROP/CREATE 库 → `alembic upgrade head` → `seed.py` → 删 `rate:login:127.0.0.1` → 跑 smoke。

## 6bis. P3 验收记录（AI/RAG 层，已独立复核）

**结论：D39、D41–D48、D50、D51 全部落地并复核通过。** 改动 6 文件 +319/−113，新增 3 个测试文件 30 例。

我本人复核的关键实现（非采信自述）：
- `app/ai/prompts.py:181-184`：`if spec.untrusted:` → `strip_untrusted_note(system).rstrip() + "\n\n" + UNTRUSTED_NOTE`。子串短路已彻底移除，且额外**剥离正文中预埋的声明原文**再在末尾追加唯一一份——比原计划更强（管理员把声明原文塞进自定义内容再跟一句"忽略以上"也无法短路）。PPT_OUTLINE(:145) 与 PROGRESS_ANALYSIS(:147) 已补 `untrusted=True`，现共 5 个 untrusted 场景。
- `app/ai/vector/store.py:25`：`student_scope_filter()` = `or_(student_id == sid, student_id.is_(None))`，注释明确记录了 `in_([id, None])` 对 NULL 永不成立的历史缺陷；`safe_document_types()` + `_TAG_RE` 白名单 + `safe_student_tag()` 三件套封住 RediSearch TAG 注入。
- `app/ai/rag/rag_service.py:90`：`row.embedding = vec_to_b64(emb)` 已去掉 `if vector_store.mode == "mysql"` 条件，Redis 模式也写降级副本。
- `app/ai/rag/rag_service.py:129-135`：按 `get_active_cycle` 过滤 + `order_by(id.desc()).limit(1).scalars().first()`，不再 `scalar_one_or_none()`。
- `app/ai/rag/retriever.py:124-129`：回表 SQL 追加同一 student 过滤，且 `ranked` 被裁剪为实际取到的行——"有排名无内容"的空占位与计分偏差一并消除。
- `app/modules/ai/router.py:48-57`：`pipeline(transaction=True)` + `SET NX EX 60` 打底 + `INCR` 同事务，`except Exception → warning + 放行`；超阈值仍抛 42901（降级没有吞掉限流）。
- `app/modules/ai/router.py:63-88`：`list_documents` 依赖只注入一次，`page/size(≤100)` 下推 SQL，`ResponseUtil.page` 返回。

**遗留三项（P5 处理）**
1. `AIUsageLog` 表无 `status` 列（`app/models/ai.py` 不在 P3 边界），`_write_usage_log` 已做"列存在则写、否则只进日志"的兼容；需补列 + Alembic 修订迁移，才能把 SUCCESS/FAILED 真正持久化。
2. `app/ai/rag/evaluation.py` 仍保留 `IN (...)` 形态，因为 `tests/test_rag_evaluation.py` 显式断言字符串 "IN"/"IS NULL"——**这是断言实现细节的坏测试**，应与运行时 `student_scope_filter` 统一后改断言语义而非字面量。
3. 验收基线更新：smoke **161** 项（P1-B 新增第16节 403/401/404/400），unittest **192** 项。

**并发操作教训（写进 P5 执行纪律）**：两个 agent 同时跑 `smoke_test.py` 会互相打爆——种子数据学生 6 只能选 topics[3]（max_students=1），一方把题目选满置 CLOSED 后另一方在 `smoke_test.py:55` 直接 IndexError；共享 Redis 登录限流 `rate:login:127.0.0.1` 10 次/60s 按 IP 计，并发会互相 42901。**P5 验收必须串行，且每批用独立库（`graduation_p5`）+ 独立 `REDIS_DB` + 独立端口。**

| P5 | 回归测试补齐 + 全量验收 | `backend/tests/**`、`backend/smoke_test.py` | P1–P4 |

## 15. 验收矩阵

| 项 | 命令 | 通过标准 |
|---|---|---|
| 单元测试 | `backend/.venv/bin/python -m unittest discover -s tests -v` | 全绿，且新增 D1–D25 对应用例 |
| 冒烟 | `backend/.venv/bin/python smoke_test.py` | 141+ 项全通过（含新增断言） |
| 迁移 | `alembic upgrade head` → `downgrade base` → `upgrade head` | 双向可逆 |
| 演示库 | `python seed.py` | 建表+种子成功，health ok |
| 前端 | `npm run build` | vue-tsc 0 error |
| 手工链路 | 选题→开题→中期→终期→排场→评分→发布→归档→毕业 | 每步状态可解释，非法动作返回 40003 |

## 16. 回归测试清单（每个缺陷一条）

D1 cycle 归档、D2 学年切换取快照、D3 未答辩不得 completed、D4 COMPLETED 不 500、D5 进度不被覆盖、D6 单轨评分、D7 显式动作、D8 空 cycle、D9 评委可读、D10 关键字分页 total、D11 HTTP 状态、D12 弱密钥启动失败、D13 登出后 refresh 失效、D14 改密后旧 access 失效、D15 Redis 故障降级、D16 路径穿越、D17 超限上传中断、D18 CWD 无关、D19 ADMIN 评分 403、D20 取消场次不可发布、D21 弱口令、D22 用户变更留痕、D23 result 正确、D24 通知、D25 死代码删除。

## 7. 装配层收口(建表纪律)

- `backend/app/main.py:31` 新增 `_alembic_head_present()`；lifespan 默认不再 `create_all`，库未迁移直接 `RuntimeError` 并提示执行 `alembic upgrade head`；`AUTO_CREATE_TABLES=true` 仅作本地逃生口（走时打 warning）。
- `backend/seed.py:113` 起新增 `_ensure_schema()`：未迁移先 `command.upgrade(cfg, "head")`；`--force` 分支 drop ORM 表后**额外 `DROP TABLE alembic_version`** 再重跑迁移，避免"表重建了但版本指针还指着旧基线"。
- 验证（真库 `graduation_alembic_check`，跑完已 DROP）：
  1. 空库启动 → `RuntimeError: 数据库未初始化或未迁移: 未找到 alembic_version`
  2. `alembic upgrade head` 后启动 → `LIFESPAN_OK`
  3. `seed.py` 首次 → 打印"库未迁移, 执行 alembic upgrade head ..." 并完成种子（15 chunks）
  4. 再跑 → "种子数据已存在, 跳过"
  5. `seed.py --force` → 重建后 `alembic_version=0002_user_token_version`、sys_user=10、cycle=4、aidoc=1
- `docker-compose.prod.yml:45` `SECRET_KEY` 改为 `${SECRET_KEY:?...}` 必填；删 `AUTO_CREATE_TABLES: "true"`；backend `command` 改为 `alembic upgrade head && exec uvicorn ...`；worker `depends_on` 改 `condition: service_healthy`。
- `README.md:53` 与 `:207` 同步更新为 "Alembic 迁移优先"。

## 17. 口径统一与状态变更触达(D73/D74)

### D73 后台统计口径统一

- 问题：`dashboard/router.py` 的完成率/毕业率/风险数各自现算，部分按全量、部分按学年，同一页面三个数字口径不同；随后 `D71`（去掉 GET 里的惰性重算）又让"已完成数"最长久滞后 24h。
- 收口：全部统计收窄到 `academic_year_for(db)` + `_current_year_cycles(db)`（analytics :169-170/:212/:236/:240、home ADMIN :343-369、home TEACHER :372-403、stats :446-528）。
- 三个响应统一新增 `scope: "current_year"`、`academic_year`、`scope_map: {字段 -> current_year|all_years}`；**故意保留全量**的字段（`student_total` / `no_selection` / `supervision` / `ai_usage` / `cards.*` / `colleges` 等）显式标 `all_years`，前端据此决定文案，不再写死"当前学年"。
- 踩到并修掉的真 bug：month 循环里 `year, month_zero = divmod(...)` 把口径变量重绑定成公历年 → 口径变量改名 `academic_year`。
- 证伪证据（临时删过滤后用例如实红，还原后 13 OK）：高风险 `6 != 1`、home risk `6 != 1`、pass_rates `50.0 != 100.0`、month_series `2 != 1`。

### D74 状态变更必须触达进度重算

- 问题：`shared/progress.py:277-281` 的 `recompute` 逻辑正确（completed → 补写 `completed_at`，否则置 `None`），但**没人调用**。全仓调用点原本只有 workflow / stages / selection / material / ai-progress / risk_scan，**结论域与归档域都缺**：
  - `defense/service.py` 的 `publish_result` / `withdraw_result`
  - `archive/router.py` 的 `revoke_package`
  → 撤回 PASS 后 `graduation_process.completed_at` 仍留着旧时间戳（实测 `decision=PASS` 已变 `WITHDRAWN`，`completed_at = 2026-10-23 10:50:49.719901` 未清空），后台"已毕业人数"虚增。
- 收口（两模块各一份同口径钩子，均延迟 import `ProgressService` 以免模块级环）：
  - `backend/app/modules/defense/service.py:534` `logger`、`:537` `_recompute_progress(db, cycle_id)`；`:629` 在 `_apply_result_payload` 之后调用、`:644` 在 `withdraw_result` 里调用。
  - `backend/app/modules/archive/router.py:445` `logger`、`:448` `_recompute_progress`；`:475` 在 OperationLog 之后、`db.commit()` 之前调用（同事务）。
  - 语义边界：`cycle_id is None` 静默跳过；`cycle` 不存在记 `warning` 不抛（历史数据不该让发布动作失败）。
- 回归测试：`backend/tests/test_progress_recompute_hooks.py` 5 例（publish / withdraw / revoke 各断言 `recompute(db, student_id, cycle_id=...)` 恰好一次；`cycle_id=None` 与 cycle 不存在各断言零调用）。
- 证伪证据：把三条调用行临时删掉 → `FAILED (failures=3)`，`AssertionError: Expected 'recompute' to be called once. Called 0 times.`；还原 → `OK`。
- 全量：`Ran 361 tests in 17.825s / OK (skipped=2)`。


## 18. 收尾批次(P8-A/P8-B + 装配层 D75)

### 18.1 P8-A 列表过滤下推(后端)

- 四个审核列表端点(selection/material × 教师/管理员)由"前端拉全量 + 内存过滤"改为 SQL 下推, 统一走 `shared/query.paginate`:
  `selection/router.py:238 teacher_selections`、`:331 admin_selections`、`material/router.py:324 teacher_materials`、`:404 admin_materials`。
- `KEYWORD_FIELDS = (SysUser.real_name, StudentProfile.student_no, GraduationTopic.title)`; 关联路径 `student_id → student_profile → sys_user`、`topic_id → graduation_topic`(不走 `cycle_id`: 多一跳且可空); 全 outerjoin, profile/topic 缺失不静默丢记录。
- 时间语义列: selection 用 `created_at`(申请时间), material 用 `submitted_at`(提交时间, 可空 → 给日期边界时 NULL 草稿不命中)。
- 去重收口(装配层): `shared/query.py` 新增 `parse_iso_arg` / `time_range_conditions` 单源实现, 两 router 的本地副本与其失效 import 已删。
- 验收: `tests/test_list_filters.py` 16 例全过 —— 命中第 25 位 → `total=1`(不是 30); 非法日期 → 40002; `page=2&page_size=5 → total=9`; 教师归属不可绕(keyword=别组学生 → teacher total=0 / admin total=1)。

### 18.2 P8-B 前端收口

- `utils/reviewList.ts:17-43`: worker pool(并发 4 × page_size 100)整体删除, 改 `buildReviewParams` 单请求; 学号/题目合并进 `keyword`; 阶段页无日期参数 → 控件 `disabled` + 显式提示(不静默忽略)。
- `views/admin/AnnouncementAdmin.vue`: 693 行纯 mock(`RAW_ROWS` 10 条假数据)改为接真接口; 删除后端不存在的 `type/views/scope` 三列; 增草稿开关(DRAFT/PUBLISHED 单选)、下线/重新上线、状态中文映射。
- `views/teacher/ScoreReview.vue:52-61`: 复算后自动 `loadResults()` + 刷新预览, 不再需要手动再点。
- dashboard 口径: 新增 `utils/dashboardScope.ts`, 两页(AdminHome/AdminStatistics)全部改读 `scope_map`, 模板里不再写死"当前学年"。
- 契约脚本 13 → **16 条响应形状断言**(新增: 公告页无假数据/无假字段、reviewList 单请求下推), 目标是后端改响应形状时脚本能红。

### 18.3 D75: 配置缓存不随表重建失效

- 现象: `shared/sysconfig.py:15 CACHE_KEY` TTL 300s, 但 `seed.py --force` 重建 `system_config` 表后不清缓存 → 读上一个库残留值(`ai_defense_enabled=false` 直接关掉整个 AI 域, smoke 第 6 节连带崩)。
- 修法: `backend/seed.py` 新增 `_flush_config_caches()`(sysconfig 键 + 全部 `PROMPT_CACHE_KEY`), 在"种子数据完成"之前调用; Redis 异常只打印不阻断。
- 不加库指纹: 并行验收本来就靠不同 `REDIS_DB` 隔离(P2-A 用 3、P2-B 用 5), 加指纹属推测需求。

### 18.4 公告发布人回填

- 现象: `announcement/router.py` 的列表从不对 `publisher_name` 求值(详情有、列表没有) → 管理端"发布人"列恒空。
- 修法: 新增 `_publisher_names(db, rows)` 批量查(一次 IN 查询, 不做 N+1), 学生列表与管理员列表都回填; 发布人不存在时给 null 不抛。
- 回归: `backend/tests/test_publisher_name_and_cache.py` 6 例(两个列表回填 / 发布人缺失为 null / 批量只查一次 / 缓存清理键集合 / Redis 故障不阻断)。

### 18.5 D69 语义同步(smoke 断言)

- P7-C 的 D69 修复让"编辑/重置 Prompt 不静默启用"; `smoke_test.py` 旧断言仍期望重置后 `source == "DB"`, 与该场景已停用的事实冲突。
- 改法: 断言 `source == "BUILT_IN"`(停用态回退内置) + 追加两步显式启用路径(启用后 `source == "DB"` 且内容为重置后文本)。不回退 D69。

### 18.6 四套验收最终结果

| 验收 | 结果 |
|---|---|
| 真库 Alembic + seed | `upgrade head`(0001→0002) 成功; `seed.py --force` 打印"配置缓存已清理: 8 个键", 15 chunks 索引完成 |
| 全量 unittest | `Ran 383 tests in 15.7s / OK (skipped=2)` |
| 真库 smoke | `结果: 164 通过, 0 失败`(161 基线 + 3 条 D69 新断言) |
| 前端 | `npm run build` 绿; `check:contract` 16 条形状断言 PASS(路由命中 164/164, 未注册 0); `verify:401` 34/34 PASS |

### 18.7 已知遗留(交付时明确未做)

1. `SysFile` 缺 `business_type/business_id` 列, 上传归属只落日志与响应体(P7-C 遗留②); `material` 直写 `SysFile` 不走配额(P7-C 遗留③)。
2. `defense_result.score` 仍为 INT 且 `DECIDE_ON_PRECISE=False`; 0.1 精度判定需 `DECIMAL(4,1)` + 迁移。
3. `stage-task` 无 delete 端点(D58 的 delete 分支无落点, 新增属新功能)。
4. `schedule` 的 `is_active` 互斥仍是应用层循环 UPDATE, 未下推为单条原子语句。
5. `ai/rag/evaluation.py` 仍用 `IN (...)` 形态, 对应测试断言实现细节而非语义。
6. `AIUsageLog` 无 `status` 列, usage 日志仅写应用日志(P3 遗留①)。
7. `selection` 的"名额满即 CLOSED"使提交路径只能靠 PUBLISHED 复核兜底(P2-B 遗留②)。


## 19. 上传链路修复(白名单单源 / 配额单源 / 业务归属落库)

### 19.1 实际故障(真库实测)

| 现象 | 证据 |
| --- | --- |
| 演示视频整类材料永远传不上去 | 前端 `MaterialView.vue` 的 accept 写 `.mp4,.zip`, 后端 `POST /files/upload` 传 `b.mp4` 返回 `{"code":40010,"message":"不支持的文件类型: .mp4"}` |
| 材料上传无视后台设置的单文件上限 | `material/router.py` 调 `read_upload(file)` 不传上限 → 走环境变量默认 50MB, 而系统设置里的 `max_upload_size_mb` 形同虚设 |
| 材料上传可绕过日配额 | `/files/upload` 有 `max_upload_daily_mb` 校验, `/student/material/files` 完全没有 → 最常用的上传入口反而无配额 |
| 配额统计跨时区漏计 | 材料写 `SysFile` 时不给 `created_at`, 落到 server_default(UTC), 与本机 `now()` 差 8 小时, 00:00–08:00 区间不计入当日已用 |
| 业务归属"白传" | 前端 `api/business.ts` 往 FormData 塞 `business_type/business_id`, 后端签名不接收; `sys_file` 也没有这两列, 孤儿文件无从定位 |
| 前端 accept 非法值 | `DESIGN`/`OTHER` 的 `accept="*"` 不是合法 HTML accept 值, 等于不限制 |

### 19.2 修法

1. **白名单分组 + 单源下发**: `shared/storage.py` 拆 `DOCUMENT/ARCHIVE/IMAGE/MEDIA_EXTENSIONS` 四组(并集即 `ALLOWED_EXTENSIONS`), 新增 `reject_unsupported()`(提示带允许类型) 与 `allowed_extensions_text()`; 故意不含 svg/html/js/exe/sh/bat 这类可渲染或可执行类型。
2. **`GET /files/upload-policy`**: 返回 `{allowed_extensions, by_kind, max_upload_size_mb, max_upload_daily_mb}`, 前端 `accept` 与上传前校验全部据此生成, 前端不再维护扩展名清单。
3. **`shared/upload_policy.py` 单源**: `max_upload_size_mb(db)`/`daily_limit_mb(db)` 读系统配置, `used_today(db,user_id)` 按本地日历日汇总, `ensure_daily_quota()` 超限抛 `UPLOAD_QUOTA_EXCEEDED=40013`, `normalize_business()` 规整归属。两个上传端点共用。
4. **material 上传顺序修正**: 先"只读校验"(读流 → 单文件上限 → 日配额), 再 `sm.transition`(状态迁移), 最后落盘。配额不足时不会留下已迁移的草稿态。
5. **`sys_file` 补业务归属列**(`business_type`/`business_id` + 两个索引), 迁移 `0003_sys_file_business_owner`; 材料上传未显式给 `business_id` 时回填材料 id。
6. **前端** `MaterialView.vue`: `MATERIAL_TYPES` 改为"扩展名分组"(kinds), `accept` 由 `policy.by_kind` 拼出; 上传前 `precheck()` 校验扩展名与大小并给出具体原因; 页面显示"支持 N 种格式 / 单文件 ≤ X MB / 每日合计 ≤ Y MB"。

### 19.3 验收

| 套件 | 结果 |
| --- | --- |
| 真库 Alembic | `upgrade head` → 0003; `alembic check` = No new upgrade operations detected |
| 全量 unittest | `Ran 398 tests / OK (skipped=2)`(新增 `tests/test_upload_policy.py` 15 例) |
| 真库 smoke | 隔离库 `graduation_upload_smoke`+`REDIS_DB=7`+端口 8133: **168 通过 0 失败**(第 18 节新增 4 条: 策略形状 / mp4 在白名单 / 媒体上传回写归属 / svg 被拒 40010) |
| 前端 | `build` 绿; `check:contract` **22 条**形状断言 PASS(新增 2 条: accept 由策略生成、上传前预校验); `verify:401` 34/34 |

### 19.4 已知遗留

1. 上传目录(`UPLOAD_DIR`)中途变更会使**历史文件全部 404**(`storage_path` 是相对路径, 但 base_dir 变了)。运维约束: 迁移目录必须整体搬移 `uploads/`。彻底解需要把绝对路径或对象存储 key 与 base_dir 解耦。
2. `sys_file` 有了归属列, 但**孤儿清理任务**还没写(需按 `business_type is null and created_at < now()-7d` 定期清理)。
3. 归档上传仍无学生入口: `create_archive` 是 `require_admin` 且要 `body.files` 的 `file_id`, 学生只能先 `/files/upload` 再交给管理员, 交互上不通。

## 20. P9: 归档自助闭环 + 孤儿文件清理(2026-10)

### 20.1 缺口与根因

| # | 缺口 | 根因 |
| --- | --- | --- |
| D76 | 学生无法自助提交毕业成果归档 | `archive/router.py` 唯一入口 `POST /archive-packages` 是 `require_admin`，且在同一事务里 submit → approve → ARCHIVED；学生侧只有只读列表 |
| D77 | 归档包无归属，状态机归属校验被整体跳过 | `ArchivePackage` 只有 `cycle_id`，而 `state_machine._assert_permission` 的 STUDENT 分支读 `getattr(obj, "student_id", None)`，拿到 None 即视为"无归属 → 不校验"，学生甲可 submit 学生乙的 PENDING 包 |
| D78 | 管理员无从确认学生提交的归档 | 没有 `approve` 端点；`search_packages` 也没有 `status` 过滤参数，PENDING 包在管理端混在列表里看不出来 |
| D79 | 孤儿文件永久占用磁盘 | 迁移 0003 给了 `sys_file.business_type/business_id`，但没有任何清理机制；且 `shared/fileperm.py` 只允许上传者下载，孤儿文件连作者都不会再访问 |
| D80 | 副作用列只改内存不落库 | `state_machine._apply_effects` 对 `archived_at / reviewed_at / publish_time` 是纯属性赋值，`_apply_status` 却走独立 UPDATE；一旦 `db.expire_all()` 或 refresh，这部分改动被丢弃并读回旧值 → 撤回后 `archived_at` 仍残留（归档率与时间轴会谎报） |

### 20.2 修法

**归档归属（D77，根因级）**
- `models/defense.py:219-223` `ArchivePackage` 新增 `student_id` / `teacher_id`（`BigInteger, index=True`）。补列后状态机同一处校验自动生效，无需改 state_machine。
- `alembic/versions/0004_archive_package_owner.py`：加两列（nullable）→ 建两索引 → **纯 SQL** `UPDATE archive_package p JOIN graduation_cycle c ON c.id = p.cycle_id SET p.student_id = c.student_id, p.teacher_id = c.teacher_id` 回填 → 再把 `student_id` 收成 `NOT NULL`。downgrade 反序 drop。
- ORM 侧刻意保持 `int | None`：测试库按 ORM `create_all` 建表（可空），真库是 NOT NULL —— 这个偏差必须由**构造点纪律**兜住，见 20.4。

**学生自助 + 学院确认（D76/D78）**
- `GET /student/archive/eligibility`（`require_student`）：cycle 取 `shared/cycle.get_active_cycle(db, user.profile_id)`；`eligible/reason` 直接复用 `_validate_archive_eligible`（不复制判断），`reason` 取 `BusinessException.message`。
- `POST /student/archive/packages`（`require_student`）：**只 submit → PENDING，不自动 approve**。归档一旦 ARCHIVED 就进检索库、可被授权下载，且 `progress.compute()` 的 `completed` 依赖它 —— 学生自审自过等于自己宣布毕业完成。
- 校验顺序：本人 cycle → 归档前置条件 → **包状态** → 文件归属 → 才迁移状态。包状态放在文件归属之前，避免对一个已归档流程回显"文件#N 是否他人上传"（存在/不存在分别 403/404，本身就是探测面）。
- `POST /archive-packages/{id}/approve`（`require_admin`）：`archive_transition(..., "approve")` → `_recompute_progress` → `OperationLog`。
- `GET /archive-packages?status=` 下推 SQL（复用 `shared/query.paginate` 的 filters），不做内存过滤。

**孤儿清理（D79）**
- `app/tasks/file_gc.py`：唯一实现 `collect(db, retention_days=7, dry_run=True)`。候选口径 = `business_type IS NULL AND deleted_at IS NULL AND created_at < cutoff` 且未被 `ai_document / graduation_material_file / archive_file / topic_attachment` 引用（`referenced_exists()` 一个相关 EXISTS 子查询，四表走 OR）。
- `dry_run` 三段分类列出 `too_recent / referenced / deletable`，让管理员核对将删什么；实删时同一条只以 `reason="deleted"` 出现一次。keyset 分批（`GC_BATCH_SIZE=200`）每批独立 commit，与 `risk_scan` 同范式。
- `celery_app.py` beat 加 `daily-file-gc`（02:30，错开 01:00 的风险扫描）；`POST /admin/files/gc`（`require_admin` + `OperationLog`）供手动触发。

**副作用落库（D80）**
- `state_machine.transition` 在 `_apply_effects` 之后加 `db.flush()`。副作用的标量列必须立刻与库一致，否则任何 `expire_all`/refresh 都会把它丢掉。

### 20.3 验收（四套全绿）

```
真库 alembic upgrade head      → 0004_archive_package_owner (head)；downgrade 0003 → 列 0，再 upgrade → 列 2
真库 seed.py --force           → 成功；archive_package 行数 1，student_id IS NULL 计数 0
全量 unittest                  → Ran 434 tests / OK (skipped=2)
隔离库 smoke（168 → 174）      → 结果: 174 通过, 0 失败（新增第 19 节 6 条）
前端                           → build EXIT=0 / check:contract 26 条断言 PASS / verify:401 34/34
```

越权根因回归证据：补列后 `archive_transition(学生甲 → 学生乙的 PENDING 包)` 抛 `PermissionDeniedException: 只能操作本人的业务记录`（此前静默通过）。

### 20.4 由本轮暴露、已固化的两条纪律

1. **迁移收紧 NOT NULL 必须同步全部构造点**。`ArchivePackage` 的三个构造点（`seed.py:362`、`archive/router.py` 管理端与学生端）最初都漏传 `student_id`，sqlite 单测全绿而真库 `seed.py` 直接 `IntegrityError (1048, "Column student_id cannot be null")`。现由 `tests/test_archive_owner_columns.py` 用 AST 扫描 `app/**` 与 `seed.py` 内所有 `ArchivePackage(` 构造，缺 `student_id=` 即红；同时断言迁移里回填语句在 `nullable=False` 之前。
2. **状态机的副作用列必须 flush**。见 D80。

### 20.5 遗留

1. `eligibility.files` 目前只列"未被任何业务记录引用"的本人文件 → 材料页已审核通过的论文反而不在清单里，学生会被迫重传，归档文件 ≠ 审核文件（审计裂缝）。待改为列本人全部文件并标注来源 `source ∈ {MATERIAL, TOPIC, UNREFERENCED}`。
2. `sys_file` 有归属列但归档上传仍无学生入口时（历史数据）的孤儿清理依赖 `business_type IS NULL`，历史行没有该值，会按"未引用且超期"被清 —— 上线前应先跑一次 `dry_run` 人工确认清单。
3. `UPLOAD_DIR` 变更会让历史文件全部 404（seed 与 uvicorn 必须同值），根治需把存储键改成相对对象 key。
4. 归档导出 `exportList` 前端直接当 Blob 用，未校验 responseType，非 200 时会把错误 JSON 存成 csv。

## 21. P10: AI 链路真实化(2026-10)

### 21.1 缺口与根因(审计结论: 此前"AI 是假调用")

| # | 缺口 | 根因 |
| --- | --- | --- |
| D81 | 问答/答辩追问不是真实模型输出 | `LLM_API_KEY` 为空即静默切 `MockLLM`(模板拼字符串), 无告警、无健康状态、前端无从判断 |
| D82 | "向量检索"没有语义 | `LocalHashEmbedding`: md5 哈希桶 + 中文二元组, 相似度与语义无关, 只是把字面重合度伪装成向量 |
| D83 | 无精排层, 关键词召回是假的 | `rag/retriever.py` keyword 分支 `select ... limit(3000)` 拉行到内存算 ngram, 不是 BM25 也没有 IDF |
| D84 | 换模型/换供应商必须重启 | AI 配置只读 `settings`(环境变量), 前端没有任何入口; 管理员改了 .env 不重启不生效 |
| D85 | 本地模型探测被误判不可达 | 本机系统代理 `http://127.0.0.1:12000` 被 urllib/httpx 默认读取, 探测 `127.0.0.1:11434` 返回 `HTTP Error 502`(1782ms), 真实 ollama 被判死 |
| D86 | 小语料下 BM25 整条查询返回空 | `rank_bm25` 的 IDF 可到 0 或负, 在 3 篇文档的语料里几乎所有词项 IDF ≤ 0, 乘积被钳成 0 或负数, 排序结果全被过滤 |
| D87 | 无向量模型时 `seed.py` 整体崩溃 | `seed.py:443` 直接 `RAGService(db).index_document(doc.id)`, 未捕获 `AIServiceUnavailable`; 演示论文索引是"锦上添花"却把种子数据流程打成硬失败(离线环境/未配 Key 是常态) |
| D88 | smoke 把 mock 的"生效模型"当成覆盖值 | `smoke_test.py:559` 断言 `llm_model_effective == "glm-4.7-air"`, 而 mock provider 下生效模型就是 `mock` 本身 —— 断言在离线环境下必然为假 |

### 21.2 修法

**配置单源(D84)**

- `app/ai/runtime_config.py`: 唯一裁决处 `resolve_ai_config(db=None)` → `EffectiveAIConfig`。优先级 **DB(系统设置) > 环境变量**, 空串一律回退(不是覆盖成空); 5s 进程内加锁缓存, 键含 `settings` 签名, 环境变量变了自动失效。
- `invalidate_ai_config()` 同时 reset `LLMFactory`/`EmbeddingFactory` 单例与 `OllamaProbe` 探测缓存; `modules/sysconfig/router.py` 写入成功后调用它 —— 保存即生效, 无需重启。
- `shared/sysconfig.py` 新增 16 个 `ai_*` 键 + `SECRET_FIELDS` + `mask_secret/mask_secrets`。**密钥只写不读**: GET 任何响应只回 `***后四位`, 配套 `has_keys` 告诉前端"是否已配置"。

**健康探测(D81)**

- `app/ai/health.py`(378 行): 五态 `ok / degraded / not_configured / down / disabled`; `probe_all(db)` 的 llm/embedding/rerank/ollama/vector_store 五块**全部过 `_guard`**, 任何异常都只落进 `detail` 而不外抛(健康检查自己 500 是最坏的可用性问题)。
- `/health` 保留原有字段(兼容既有调用方), 新增 `/health/ai`。`not_configured` 时必须给出 `FIX_HINT`(配 Key 或起 ollama), 不允许只报"不可用"。
- `embedding_needs_reindex(db)`: 比对当前 embedding 指纹与 Redis 键 `ai:embedding:fingerprint`, 换模型/换维度后提示重建知识库 —— 否则新旧向量混在一个索引里, 召回质量静默劣化。

**回环直连(D85)**

- `app/ai/net.py`: `is_loopback_url / direct_opener / open_url / openai_http_client`。对回环地址强制 `trust_env=False`, 绕开系统代理。实测由 `HTTP Error 502 / 1782ms` 变为 `Connection refused / 3ms` —— 否则运维只要开着一个全局代理, 本地 ollama 就永远"不可达"。

**混合召回与精排(D82/D83/D86)**

- `app/ai/rag/bm25.py`: `jieba.lcut_for_search` + `rank_bm25.BM25Okapi`, 进程级 LRU 缓存(键含 bind/student_id/doc_types), 指纹 `(count(id), max(id))` 变化即重建。
- `_with_positive_idf`: 把非正 IDF 钳到 `1e-3`。这是 D86 的根因修复 —— 不能改成"过滤掉这些词项", 那会让小语料查询直接空集。
- `app/ai/rerank/factory.py`: `OnnxCrossEncoderReranker` 延迟加载, 缺 `model.onnx`/`tokenizer.json` 时 `available=False` **不抛异常**, 降级 `NoopReranker`; `get_reranker()` 按 `(provider, model_dir)` 缓存。
- `rag/retriever.py`: `retrieve(..., rerank=True)`, 召回宽度 `recall = max(rerank_recall, top_k)` 先宽后窄; `RetrievedChunk` 增 `rerank_score` 与 `stage ∈ {fused, reranked, fallback}`, 让"这次到底走没走精排"可观测。

**多步 Agent**

- `app/ai/agent/{tools,loop,trace}.py`: 6 个**只读**工具(search_knowledge / search_regulations / get_my_progress / get_my_topic / get_my_stage_status / get_rubric)。
- 用 JSON 决策协议而不是 tool-calling: 兼容 ollama 与任意 OpenAI 兼容端点, 不绑定某家模型能力。
- `IDENTITY_ARG_NAMES`: 拒绝 LLM 传入的 `student_id/user_id` 等身份参数, 每处一律用 `ctx.student_id` 下推 —— 模型是自己拼参数的, 它没有权限概念。
- 上限: `agent_max_steps(6)` / `agent_max_retrieval_rounds(3)` / `agent_timeout_seconds(120)` / `MAX_CONTEXT_CHARS=12000`; 触顶产出 `kind=forced` 的收敛回答, 不是无声截断。
- `answer_agent_stream` 事件序 `meta → step* → delta* → citations → done`; 引用越界重生成一次, 二次仍越界则剥除引用而不是编造。
- 三个新 prompt 场景(AGENT_PLAN / AGENT_SUFFICIENCY / AGENT_CITATION_CHECK)全部 `untrusted=True`, 走既有注入防护。

**种子与 smoke(D87/D88)**

- `seed.py`: 演示论文索引包 `try/except AIServiceUnavailable`, 降级打印"跳过 + 如何开启"; 其它异常也只标记文档 `FAILED` 后继续。种子数据的职责是"造出可演示的数据", 不能被外部服务可用性绑架。
- `smoke_test.py:559`: 覆盖值断言按 provider 分支 —— `mock` 时期望 `llm_model_effective == "mock"`, 真实 provider 时期望等于覆盖值。持久不变量只有一条: `ai_llm_model` 落库并回显。

### 21.3 验收(四套全绿)

```
全量 unittest                                  → Ran 537 tests / OK (skipped=3)
真库 seed.py --force(无任何 AI env)            → 打印"演示论文索引跳过", 种子数据完成
真库 seed.py --force(mock/hash) + smoke        → 结果: 174 通过, 0 失败
前端 build + check:contract + verify:401       → PASS / 32 条断言 / 34-34
/health (无 Key)                               → status=not_configured, llm/embedding configured=false + FIX_HINT, rerank=down, vector_store=redis
/health (mock/hash)                            → status=degraded, llm/embedding disabled
```

### 21.4 由本轮固化的纪律

1. **AI 能力只经 `resolve_ai_config()` 与两个工厂获取**, 业务代码不得直接读 `os.environ` 或 `settings.llm_*` —— 否则"管理端改了不生效"会以最难查的方式复发。
2. **密钥只写不读**: 任何 GET 只回掩码; PUT 的掩码与空串一律视为"不修改", 真清空必须用 `__CLEAR__`。
3. **探测回环地址必须直连**(`trust_env=False`), 否则系统代理会把"本地服务没起来"伪装成"网关 502"。
4. **辅助动作不许打死主流程**: 索引、通知、指纹标记这类副作用失败只降级 + 记录, 不 rollback 主事务(见 D87)。
5. **健康检查不得抛异常**: 每块都过 `_guard`, 宁可报 `down` 也不 500。

### 21.5 遗留

1. 本机已按要求删除全部本地模型(ollama 两个模型 + ONNX 精排目录), 生产使用需自备: 起 `ollama` 拉 `qwen2.5:7b-instruct`/`bge-m3`, 或在系统设置里填第三方 OpenAI 兼容 Base URL + Key; 精排需把 ONNX 目录放回 `RERANK_MODEL_DIR`(缺省自动降级 Noop, 不报错)。
2. Agent 用 JSON 决策协议而非原生 tool-calling, 换到强 tool-calling 模型时可再评估是否切换; 当前取舍是"任何 OpenAI 兼容端点都能跑"。
3. `ai_embedding_dim` 改动只提示 `needs_reindex`, 不会自动重建; 重建入口仍是既有知识库管理页, 大批量重建缺进度反馈。
4. smoke 的 AI 段依赖 `LLM_PROVIDER=mock EMBEDDING_PROVIDER=hash`(见 AGENTS.md 验收命令); 真实 provider 的端到端问答未纳入自动验收(需外部服务与配额)。
