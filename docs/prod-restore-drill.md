# 生产环境部署与备份恢复验收

日期：2026-10-10（Asia/Shanghai）

## 范围与方法

在目标部署形态（`docker-compose.prod.yml` 五容器：nginx 前端 / backend ×2 workers / celery-worker / mysql8 / redis-stack）完成首次完整构建部署，经 nginx（`:8080`）执行端到端业务验收；随后按上线口径备份生产数据库与上传卷，恢复到隔离数据库与临时目录，用 `verify_restore.py` 做行级指纹与文件 SHA-256 比对。演练期间生产库无并发写入，`mysqldump --single-transaction` 保证一致性快照。

## 部署过程发现并修复的问题

生产前端镜像首次构建失败：`vue-tsc -b`（构建模式，含 `noUnusedLocals` 等严格检查）暴露 `frontend/src/views/teacher/AiProgress.vue` 三处既有类型错误（`v-for` 索引按 `string | number` 推断导致算术运算报错 ×2、未使用常量 ×1）。本地 `vue-tsc --noEmit` 不启用了项目引用的严格配置故未拦截。已修复并重建，构建通过。**结论：生产镜像构建应作为发布前强制关卡。**

## 端到端验收（经 nginx :8080，全部通过）

| 检查项 | 结果 |
| --- | ---:|
| 管理员/教师/学生三角色登录 | ✓ |
| 题目中心列表（学生） | ✓ |
| 学生毕设进度 | ✓ |
| 答辩运营统计（管理员 `/dashboard/defense-stats`） | ✓ |
| 教师首页数据（`/dashboard/teacher-activity`） | ✓ |
| 我的答辩场次（学生 `/defense-sessions/mine`，1 场） | ✓ |
| RAG 检索（Top1 命中「3.2 毕业设计状态机」） | ✓ |
| 授权用户文件下载（`/files/{id}` 流式 200） | ✓ |
| 管理员操作日志查询 | ✓ |
| 前端首页 HTML 经 nginx 提供且 `/api` 反代正常 | ✓ |

## 备份与恢复校验

| 检查项 | 结果 |
| --- | ---:|
| 数据表 | 50 张，全部一致 |
| 数据行 | 89 行，全部一致 |
| 上传文件 | 1 个，SHA-256 全部一致 |
| 恢复库中有效 `sys_file` 记录指向的文件缺失 | 0 |
| 数据库转储 SHA-256 | `819f25d1093c7420a883bbfe99fff89f4615c4cf0263078c89d062f2d6ce7170` |
| 上传文件包 SHA-256 | `fd12084f5986831e66e1fa857fb7c8acb0c2fe806aeecee5d332a8c0fe1b1739` |

## 复演步骤

```sh
# 1. 部署(仓库根目录)
cp .env.prod.example .env.prod && 修改密钥
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml exec backend python seed.py   # 首次初始化

# 2. 备份(口令从容器环境读取, 不落命令行)
DRILL_DIR="$(mktemp -d /tmp/tdms-prod-restore.XXXXXX)"
docker exec gs-mysql sh -c 'MYSQL_PWD="$MYSQL_PASSWORD" exec mysqldump -u"$MYSQL_USER" --single-transaction --routines --triggers --events --no-tablespaces graduation_system' > "$DRILL_DIR/database.sql"
docker run --rm -v graduation-system-prod_gs-uploads:/data:ro -v "$DRILL_DIR":/backup alpine tar -czf /backup/uploads.tar.gz -C /data .

# 3. 恢复到隔离库(RESTORE_DB 必须是新名称, 禁止导入现有业务库)
RESTORE_DB="graduation_restore_drill_$(basename "$DRILL_DIR" | tr -cd '[:alnum:]' | tr '[:upper:]' '[:lower:]')"
docker exec gs-mysql sh -c "MYSQL_PWD=\"\$MYSQL_ROOT_PASSWORD\" exec mysql -uroot -e 'CREATE DATABASE $RESTORE_DB CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci'"
docker exec -i gs-mysql sh -c "MYSQL_PWD=\"\$MYSQL_ROOT_PASSWORD\" exec mysql -uroot $RESTORE_DB" < "$DRILL_DIR/database.sql"
docker exec gs-mysql sh -c "MYSQL_PWD=\"\$MYSQL_ROOT_PASSWORD\" exec mysql -uroot -e \"GRANT SELECT ON $RESTORE_DB.* TO 'graduation'@'%'\""
mkdir -p "$DRILL_DIR/restored" && tar -xzf "$DRILL_DIR/uploads.tar.gz" -C "$DRILL_DIR/restored"

# 4. 在 backend 容器内只读比对(生产 mysql 不发布宿主端口)
docker exec gs-backend mkdir -p /tmp/drill-restored
docker cp "$DRILL_DIR/restored/." gs-backend:/tmp/drill-restored/
docker exec gs-backend python verify_restore.py --restored-db "$RESTORE_DB" --source-uploads /data/uploads --restored-uploads /tmp/drill-restored

# 5. 清理(核对 RESTORE_DB/DRILL_DIR 为本次生成值后执行)
docker exec gs-mysql sh -c "MYSQL_PWD=\"\$MYSQL_ROOT_PASSWORD\" exec mysql -uroot -e 'DROP DATABASE $RESTORE_DB'"
docker exec gs-backend rm -rf /tmp/drill-restored && rm -rf "$DRILL_DIR"
```

## Redis RAG 索引的恢复口径

Redis 向量索引未纳入本次备份（缓存性质，可重建）。灾难恢复后执行 `POST /ai/documents/{id}/index`（或在学生端「AI 知识准备」点重建索引）即可按 MySQL 中的 chunk 元数据与文档原文重建全部向量；重建期间 RAG 检索自动降级 MySQL 余弦相似度，不中断服务。

## 结论

生产编排五容器可完整构建部署，核心业务链路经 nginx 验收通过；数据库与上传文件的备份-恢复-校验闭环成立。与开发环境演练（`docs/backup-restore-drill.md`）共同覆盖两套环境。后续上线仍需：定期备份计划（cron + 异地保留）、`.env.prod` 强密钥替换演练口径默认值。
