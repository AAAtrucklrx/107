# 2026-08-05 irreversible-change-surface（幂等同步 + 重建备份）

## 目标

finding: `irreversible-change-surface`（版本控制与幂等同步部分）。
- 覆盖式写库（先删后插）改为单事务原子替换，失败整体回滚
- 手动重建入口（rebuild_kb.py）带 `--yes` 确认与时间戳备份

## 改动文件

- `database/db_manager.py`：新增 `transaction()` 上下文管理器（嵌套安全，仅最外层 commit/rollback）
- `tools/course_tools.py`：`_sync_courses_to_db`/`_sync_grades_to_db` 改为 `with db.transaction() as conn` 内直接 `conn.execute`（避开 `db.execute` 的逐条 commit）
- `rebuild_kb.py`：无 `--yes` 时拒绝执行；`--yes` 时先备份 `chroma_db/` 到 `knowledge_backup_<时间戳>/`，重建失败提示恢复路径
- `docs/dev-log/README.md`：本目录与记录约定（随本提交建立）

## 验证

- `py rebuild_kb.py`（无 --yes）→ 打印提示并 exit 1，无删除动作：通过
- 临时数据库事务测试：
  - 事务内 DELETE+INSERT 后抛异常 → 数据完整回滚（保留原 2 行）：通过
  - 事务正常结束 → DELETE+INSERT 全部提交（新 2 行生效）：通过
- `git check-ignore` 确认 `knowledge_backup_*/` 模式已忽略（备份目录不入库）

## 遗留问题

- `rebuild_kb.py --yes` 的全量重建（需加载 embedding 模型）未在本次执行，待修复 5
  （embedding 配置统一）完成后随知识库重建验证
- git 身份未配置，提交使用 `-c user.name/-c user.email` 临时参数（不落盘）
