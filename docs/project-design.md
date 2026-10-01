# 智能毕业设计管理系统：项目设计与原型方案

版本：2026-09-30。依据根目录 `roadmap.md` 的“执行版增量路线”，适用于现有 FastAPI + Vue 3 项目的下一阶段设计。本文是设计稿，不表示新增功能已上线。

## 1. 产品范围

系统服务管理员、指导教师、评委和学生。评委是教师的**业务身份**，由场次分配获得权限，不增加第四种全局登录角色。业务分为五条连续链路：选题与过程材料、阶段任务、正式答辩排期、人工评审与成绩、毕业成果归档。现有 RAG、AI 模拟答辩、AI 进度分析保留为辅助功能；AI 分数不得写入正式成绩。

### 1.1 当前基线

- 已有：三角色、选题、开题/中期/终期材料、毕业材料、教师审核、版本和进度、通知、AI 模拟答辩。
- 待新增：学年流程实例、正式答辩场次、评委分配、独立评审、成绩汇总发布、成果归档与公开授权。
- 现有 `final_defense.status` 与 `score` 只作为旧版终期**材料审核**数据；迁移时不推断它代表现场答辩结论。

### 1.2 核心业务规则

1. 一个学生可跨学年拥有多个流程实例，同一学年最多一个活动实例。
2. 材料提交和审核、场次安排、现场评审是三个独立状态域。材料通过不能自动生成正式答辩成绩。
3. 已发布场次改期必须留下前后时间、操作者和原因，并触发站内通知。
4. 评委只能查看自己被指派场次中、评审所需的学生材料；指导教师维持现有指导关系权限。
5. 正式成绩由服务端根据该学年配置和有效评委评分计算；评委人数不足、评分项不完整时不得发布。
6. 毕业归档必须同时满足：正式结论通过、必交材料审核通过、最终文件版本确定。

## 2. 信息架构与页面原型

保留当前三角色导航与蓝白视觉基线。新增页面按下表先制作桌面与手机两种尺寸的低保真原型，再进入视觉稿。

| 角色 | 新入口 | 页面主要区域 | 关键操作 |
| --- | --- | --- | --- |
| 学生 | 我的任务 | 截止日期、所需材料、提交状态、审核反馈 | 查看任务、进入对应材料页 |
| 学生 | 我的答辩 | 场次时间/地点、评委名单、材料准备、改期记录 | 查看详情、确认已读、查看发布后的结论 |
| 学生 | 毕业成果 | 归档清单、最终版本、公开范围 | 申请公开、撤回公开、下载本人资料 |
| 教师 | 指导任务 | 按学年筛选的任务、学生完成情况 | 创建任务、提醒学生 |
| 教师 | 评阅与答辩 | 指派给我的场次/学生、材料、评分表 | 提交评语与评分、查看自己的提交历史 |
| 管理员 | 答辩排期 | 日历/列表、未排学生、教室与评委冲突提示 | 创建、调整、发布场次 |
| 管理员 | 成绩复核 | 评委提交矩阵、汇总预览、异常提示 | 复核、发布、撤回发布并留痕 |
| 管理员 | 成果归档 | 待归档/已归档、权限和下载日志 | 归档、检索、导出清单 |

### 2.1 三个关键页面的低保真布局

**答辩排期（管理员）**

```text
页头：学年 [选择]  阶段 [开题/中期/终期]   [+ 新建场次]
统计：未排人数 | 未分配评委 | 时间冲突 | 已发布场次
主区：左侧场次日历/列表 ┃ 右侧待排学生与可用评委
场次详情抽屉：时间、地点、容量、学生、主席/评委、冲突说明、发布记录
底部动作：保存草稿 | 校验冲突 | 发布/改期
```

**评审工作台（被指派教师）**

```text
页头：我的待评阅 / 待答辩 / 已提交
左侧：学生与场次列表、截止时间、材料版本
中间：论文/附件预览、指导意见、版本轨迹
右侧：评分项、各项分值、评语、暂存与正式提交
提交后：只读评分版本；如需修改，走“申请重开/管理员复核”
```

**我的答辩（学生）**

```text
进度条：材料审核 → 已排期 → 已答辩 → 评审中 → 结论已发布
场次卡：日期、地点、注意事项、评委、改期记录
材料卡：最终提交版本、状态、待补项
结论卡：发布前显示“评审中”；发布后显示结论与可见反馈
辅助入口：AI 模拟答辩（明确标注“练习，不计入正式成绩”）
```

### 2.2 交互状态

- 空：无任务/未排期时，显示下一步责任方与预计操作，不显示空白表格。
- 冲突：创建场次时列出教师、学生或场地的冲突对象和时间，不允许发布。
- 逾期：任务列表与首页同时标记；允许补交与否由任务配置决定，保留提交时间。
- 已发布成绩：学生只读；管理员撤回时必须填写原因，学生端重新显示“复核中”。
- 手机端：排期列表改为日期分组卡片；评分表纵向排列，固定底部提交按钮。

## 3. 状态与流程设计

| 业务对象 | 状态集合 | 允许的主要转移 |
| --- | --- | --- |
| 材料 | `NOT_SUBMITTED/DRAFT/SUBMITTED/APPROVED/REJECTED` | 保存、提交、审核、驳回后重交；复用现有引擎 |
| 场次 | `DRAFT/READY/PUBLISHED/IN_PROGRESS/FINISHED/CANCELLED` | 草稿通过冲突校验到待发布，发布后可改期并留痕 |
| 学生答辩席位 | `SCHEDULED/ATTENDED/ABSENT/POSTPONED/RETAKE` | 签到或缺席，延期/重答辩必须关联原因与新场次 |
| 评委评分 | `DRAFT/SUBMITTED/REOPENED/SUPERSEDED` | 提交后锁定；复核重开产生新版本，旧版保留 |
| 正式结论 | `PENDING/READY/UNDER_REVIEW/PUBLISHED/WITHDRAWN` | 评分齐备后复核、发布；撤回需原因 |
| 归档包 | `PENDING/ARCHIVED/REVOKED` | 条件齐备后归档；撤回公开只改公开授权，不删除归档记录 |

**流程关系**：选题通过 → 阶段材料审核 → 安排正式答辩 → 评委评分 → 人工复核发布结论 → 毕业材料审核 → 成果归档。学校若要求开题与中期也有现场答辩，复用同一场次/评分模型，以 `stage` 区分，不创建三套表。

## 4. 数据设计

所有新增表沿用现有 `sys_user`、`student_profile`、`teacher_profile`、`sys_file`、`review_record`、`notification`。关键外键全部指向稳定主键，时间存 UTC 或明确时区，界面按学校时区显示。

| 表 | 关键字段 | 约束与用途 |
| --- | --- | --- |
| `graduation_cycle` | `id, academic_year, student_id, topic_id, teacher_id, status` | `UNIQUE(academic_year, student_id)`；一届一流程 |
| `stage_task` | `id, cycle_scope, stage, title, requirements_json, opens_at, due_at, late_policy` | 按学年/学院/专业/班级指定范围；版本化修改 |
| `defense_session` | `id, academic_year, stage, starts_at, ends_at, location, capacity, status, revision` | `starts_at < ends_at`；发布前做冲突校验 |
| `defense_assignment` | `id, session_id, cycle_id, seat_status, attendance_status` | 一场次一学生一个有效席位；重答辩可新建席位 |
| `defense_examiner` | `id, session_id, teacher_id, examiner_role` | 一场次教师不重复；角色含 `CHAIR/EXAMINER/REVIEWER` |
| `review_rubric` | `id, academic_year, college_id, stage, version, min_examiners, weights_json` | 启用后冻结版本；权重合计 100% |
| `examiner_score` | `id, assignment_id, examiner_id, rubric_version, scores_json, comment, status, version` | 每评委每席位仅一个有效提交版本；保留旧版 |
| `defense_result` | `id, assignment_id, score, decision, status, published_at, rubric_snapshot` | 汇总可复算；发布、撤回留操作日志 |
| `archive_package` | `id, cycle_id, result_id, status, archived_at, visibility` | 一流程一个有效归档包；默认私有 |
| `archive_file` | `id, package_id, file_id, file_role, version, sha256` | 固定最终文件快照；下载统一走授权接口 |
| `archive_consent` | `id, package_id, student_user_id, visibility, consented_at, revoked_at` | 公开授权单独记录，可撤回 |

**迁移顺序**：先加 `graduation_cycle` 并为现有学生创建当前学年实例；再为现有业务表补 `cycle_id`、回填与复核；之后切换读取和唯一约束；最后加入排期/评审/归档表。迁移期间保留旧字段读取兼容，确认数据一致后才移除旧唯一约束。历史 `final_defense.score` 作为旧材料评分保留，不写入 `defense_result`。

## 5. API 草案

路径均以 `/api/v1` 开头，沿用现有统一响应格式。列表支持 `academic_year, stage, status, college_id, keyword, page, size`。

| 方法与路径 | 调用方 | 作用 |
| --- | --- | --- |
| `GET/POST /stage-tasks`、`GET /stage-tasks/mine` | 管理员/教师；学生查询 | 任务配置与我的待办 |
| `GET/POST /defense-sessions`、`PATCH /defense-sessions/{id}` | 管理员 | 场次草稿与修改 |
| `POST /defense-sessions/{id}/validate`、`POST /defense-sessions/{id}/publish` | 管理员 | 冲突检查与发布 |
| `GET /defense-sessions/mine` | 学生/教师 | 仅返回本人或被指派场次 |
| `GET/PUT /defense-assignments/{id}/review` | 被指派评委 | 查看授权材料、暂存评审 |
| `POST /defense-assignments/{id}/scores/submit` | 被指派评委 | 提交并锁定评分版本 |
| `GET /defense-results/{id}/preview`、`POST /defense-results/{id}/publish` | 管理员 | 复算汇总、发布结论 |
| `POST /archive-packages`、`GET /archive-packages` | 管理员/授权用户 | 创建、检索归档 |
| `POST /archive-packages/{id}/consent`、`DELETE /archive-packages/{id}/consent` | 学生本人 | 授权或撤回公开 |
| `GET /archive-files/{id}/download` | 授权用户 | 权限过滤后下载并记日志 |

提交类接口携带幂等键；并发修改场次用 `revision` 乐观锁。`validate` 只供预览，`publish` 在同一事务中**再次检查**冲突、容量和评委人数。分页查询和文件下载必须在服务端按对象权限过滤。

## 6. 交付顺序和验收

| 里程碑 | 交付物 | 必须通过的场景 |
| --- | --- | --- |
| P0 | 流程实例、状态口径、迁移脚本、旧页面文案调整 | 旧数据可读；材料通过不显示正式答辩通过 |
| P1 | 任务中心、排期台、我的答辩、通知 | 教师/学生/场地时间冲突不能发布；改期可追溯 |
| P2 | 评审工作台、评分配置、成绩复核与发布 | 人数不足不能发布；复算一致；越权评委访问失败 |
| P3 | 归档检索、授权与撤回、下载日志 | 私有论文无法直链下载；撤回公开即时生效 |
| P4 | 移动端适配、统计、备份恢复与回归测试 | 学生从选题到归档完整走通；关键权限与状态自动验证 |

正式开发前须与学校确认评分标准、每场最少评委数、指导教师是否回避、材料补交政策、论文公开授权文案。这些是配置或制度输入，不能从参考仓库推断。
