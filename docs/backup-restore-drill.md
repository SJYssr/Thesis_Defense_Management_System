# 本地备份恢复演练

日期：2026-10-01（Asia/Shanghai）

## 范围与方法

本次在开发环境中备份 `graduation_system` MySQL 数据库和 `backend/uploads/`，恢复到新建的隔离数据库与临时目录。原数据库和上传目录未被覆盖。数据库使用 `mysqldump --single-transaction`，上传文件单独打包；演练期间未执行写入业务操作。

恢复后运行 `backend/verify_restore.py`，按主键顺序计算每张表的行指纹，并逐文件比较 SHA-256，同时检查恢复库中有效 `sys_file` 记录指向的文件是否存在。脚本只读两套数据。

| 检查项 | 结果 |
| --- | ---: |
| 数据表 | 50 张，全部一致 |
| 数据行 | 174 行，全部一致 |
| 上传文件 | 7 个，全部一致 |
| 有效文件记录的缺失目标 | 0 |
| 数据库转储 SHA-256 | `9aa0190ee7146b052839ef798d3370b4d677b3a69dfe5133b67a1e067f5be1cb` |
| 上传文件包 SHA-256 | `c0292be307a9883cb476433189276d6a204609ac246ecc9bff904b2a3331c59c` |

## 复演步骤

以下步骤在仓库根目录执行。`RESTORE_DB` 必须是新名称；若创建失败，应停止，不得导入到现有业务库。MySQL 口令从容器环境读取，不写入命令行参数或文档。

```sh
DRILL_DIR="$(mktemp -d /tmp/tdms-restore-drill.XXXXXX)"
RESTORE_DB="graduation_restore_drill_$(basename "$DRILL_DIR" | tr '[:upper:]' '[:lower:]' | tr -cd '[:alnum:]')"
docker exec graduation-mysql sh -c 'MYSQL_PWD="$MYSQL_PASSWORD" exec mysqldump -u"$MYSQL_USER" --single-transaction --routines --triggers --events --no-tablespaces --set-gtid-purged=OFF graduation_system' > "$DRILL_DIR/database.sql"
tar -czf "$DRILL_DIR/uploads.tar.gz" -C backend uploads
docker exec graduation-mysql sh -c "MYSQL_PWD=\"\$MYSQL_ROOT_PASSWORD\" exec mysql -uroot -e 'CREATE DATABASE $RESTORE_DB CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci'"
docker exec -i graduation-mysql sh -c "MYSQL_PWD=\"\$MYSQL_ROOT_PASSWORD\" exec mysql -uroot $RESTORE_DB" < "$DRILL_DIR/database.sql"
docker exec graduation-mysql sh -c "MYSQL_PWD=\"\$MYSQL_ROOT_PASSWORD\" exec mysql -uroot -e \"GRANT SELECT ON $RESTORE_DB.* TO 'graduation'@'%'\""
mkdir -p "$DRILL_DIR/restored"
tar -xzf "$DRILL_DIR/uploads.tar.gz" -C "$DRILL_DIR/restored"
cd backend
.venv/bin/python verify_restore.py --restored-db "$RESTORE_DB" --source-uploads uploads --restored-uploads "$DRILL_DIR/restored/uploads"
```

验证完成后，用 MySQL 管理账号删除本次创建的隔离库，并清理临时目录。清理前先核对 `RESTORE_DB` 和 `DRILL_DIR` 均为本次演练生成的值。

## 上线前仍需处理

本次只验证开发环境的数据库与上传文件。生产环境须建立定期备份、异地保留与恢复权限流程；若恢复时仍允许写入，应使用一致性快照或短暂停写，保证数据库记录与文件来自同一时点。Redis 中的 RAG 索引应纳入备份或明确重建步骤。还需在目标部署环境完成一次端到端恢复与登录、下载、答辩主链路验收。
