# 运动日历缓存 SQLite 化实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将运动日历页的本地缓存从 JSON 文件(`.workout-cache.json`)改为 SQLite 数据库(`.workout-cache.db`),保持对外接口语义不变(调用方 `epd_status.py` 无感知),API 失败降级读缓存的行为不变。

**Architecture:** `workout_data.py` 内新增私有 SQLite 存储层(`_open_db` / `_init_schema`),公开函数 `load_month_cache` / `save_month_cache` / `merge_activities_into_cache` 签名与语义完全不变(仍是 `Path` 入参、返回 `list[WorkoutActivity]`),仅底层实现从 JSON 换成单表 SQLite。调用方零改动。旧 JSON 缓存文件一次性自动迁移:首次打开 DB 时若检测到旧 JSON 存在则导入并重命名留档。

**Tech Stack:** Python 标准库 `sqlite3`(3.45.3 已确认可用),零新依赖。pytest(unittest 风格,遵循项目现有测试)。

---

## 设计要点(评审关注项)

1. **单表结构:**
   ```sql
   CREATE TABLE IF NOT EXISTS activities (
       id          TEXT PRIMARY KEY,          -- intervals.icu 活动 ID
       day         TEXT NOT NULL,             -- ISO 日期 'YYYY-MM-DD'(start_date_local 的日期部分)
       type        TEXT NOT NULL DEFAULT '',
       name        TEXT NOT NULL DEFAULT '',
       moving_time INTEGER NOT NULL DEFAULT 0,
       distance    REAL NOT NULL DEFAULT 0.0
   );
   ```
   - `id` 主键天然去重,`merge` 语义 = `INSERT OR REPLACE`(fresh 覆盖 existing,与现 JSON 行为一致)。
   - 无 `month` 列:按月查询用 `WHERE day BETWEEN ? AND ?`(月首~月末),月内日期本身就落在同一月,无需冗余分区键。
2. **无 ID 活动的兜底键:** 现 JSON 实现对缺 ID 的活动生成合成键 `f"{day}-{type}-{moving_time}"`;SQLite 版沿用同一合成键作为主键(在 `merge_activities_into_cache` 层处理,存储层只见字符串主键)。
3. **原子性与并发:** SQLite 单文件、事务原子;每次写操作独立 `with conn:` 事务。`PRAGMA journal_mode=WAL` 不必要(单进程低频写,默认 rollback journal 足够,YAGNI)。
4. **迁移策略:** `load_month_cache` 首次打开时,若同目录存在旧 `.workout-cache.json` 且 DB 为新建库 → 解析 JSON 导入全部月份 → 将 JSON 重命名为 `.workout-cache.json.migrated` 留档。解析失败则仅重命名(数据视为废弃,重建空库,与现 JSON "corrupt 即重建" 语义一致)。
5. **`.gitignore`:** 增加 `.workout-cache.db`、`.workout-cache.db-journal`、`.workout-cache.json.migrated`。
6. **文件路径入参兼容:** 公开函数仍接受 `Path`;SQLite 版将 `path` 后缀 `.json` 替换为 `.db`?**不做路径魔法**——调用方直接改传 `.workout-cache.db` 路径(仅 `epd_status.py` 两处字面量),存储函数按传入路径原样打开。这样测试与其他调用者不受隐式改名影响。

## 文件结构

| 文件 | 动作 | 职责 |
| --- | --- | --- |
| `workout_data.py` | 修改 | 存储层 JSON→SQLite;公开函数签名不变 |
| `tests/test_workout.py` | 修改 | CacheTests / MergeCacheTests 改为对 DB 断言;新增迁移测试 |
| `epd_status.py` | 修改 | 两处 `.workout-cache.json` 字面量 → `.workout-cache.db` |
| `.gitignore` | 修改 | 新增 db 相关忽略项 |
| `README.md` | 修改 | 缓存文件说明(JSON→SQLite) |

---

### Task 1: SQLite 存储层(替换 JSON 实现)

**Files:**
- Modify: `workout_data.py`
- Test: `tests/test_workout.py`

- [ ] **Step 1: 改写 CacheTests 为 DB 语义并新增失败测试**

将 `tests/test_workout.py` 中 `CacheTests` / `MergeCacheTests` 的 setUp 缓存路径改为 `.workout-cache.db`。新增迁移测试:

```python
class CacheMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / ".workout-cache.db"
        self.legacy_path = self.db_path.with_suffix(".json")  # .workout-cache.json

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_legacy_json_is_imported_on_first_open(self):
        from workout_data import parse_activities, save_month_cache
        # 先用旧 JSON API 语义手工构造一个 legacy 文件
        self.legacy_path.write_text(json.dumps({
            "version": 1,
            "months": {"2026-08": [
                {"id": "a1", "day": "2026-08-03", "type": "Run",
                 "name": "Morning", "moving_time": 1800, "distance": 5000.0},
            ]},
        }))
        acts = load_month_cache(self.db_path, 2026, 8)
        self.assertEqual(len(acts), 1)
        self.assertEqual(acts[0].id, "a1")
        # 迁移后 JSON 被重命名留档,不会二次导入
        self.assertTrue(self.legacy_path.with_suffix(".json.migrated").exists())

    def test_corrupt_legacy_json_is_renamed_not_fatal(self):
        self.legacy_path.write_text("not json")
        self.assertEqual(load_month_cache(self.db_path, 2026, 8), [])
        self.assertTrue(self.legacy_path.with_suffix(".json.migrated").exists())

    def test_no_legacy_json_means_empty_fresh_db(self):
        self.assertEqual(load_month_cache(self.db_path, 2026, 8), [])
        self.assertFalse(self.legacy_path.with_suffix(".json.migrated").exists())
```

同时为现有 `CacheTests` 增加一条 DB 特有断言(任一公开函数调用后 db 文件存在且表结构已建)。

- [ ] **Step 2: 运行测试确认 RED**

Run: `.venv/bin/python -m pytest tests/test_workout.py -q`
Expected: FAIL/ERROR — `load_month_cache` 当前仍是 JSON 实现(找的是 `.json` 路径内容,DB 路径不存在返回空,迁移测试失败)。

- [ ] **Step 3: 实现 SQLite 存储层**

在 `workout_data.py` 中替换存储实现(公开签名不变):

```python
_SCHEMA = """
CREATE TABLE IF NOT EXISTS activities (
    id          TEXT PRIMARY KEY,
    day         TEXT NOT NULL,
    type        TEXT NOT NULL DEFAULT '',
    name        TEXT NOT NULL DEFAULT '',
    moving_time INTEGER NOT NULL DEFAULT 0,
    distance    REAL NOT NULL DEFAULT 0.0
)
"""

def _migrate_legacy_json(db_path: Path):
    """One-time import of the pre-SQLite JSON cache, then rename it aside."""
    legacy = db_path.with_suffix(".json")
    marker = legacy.with_suffix(".json.migrated")
    if not legacy.exists() or marker.exists():
        return
    try:
        payload = json.loads(legacy.read_text())
        months = payload.get("months", {}) if isinstance(payload, dict) else {}
        with sqlite3.connect(db_path) as conn:
            conn.executescript(_SCHEMA)
            for entries in months.values():
                for entry in entries if isinstance(entries, list) else []:
                    act = WorkoutActivity.from_cache_dict(entry)
                    if act is not None:
                        conn.execute(
                            "INSERT OR REPLACE INTO activities VALUES (?,?,?,?,?,?)",
                            (act.id, act.day.isoformat(), act.type, act.name,
                             act.moving_time, act.distance))
        legacy.replace(marker)
    except (OSError, json.JSONDecodeError, sqlite3.Error) as exc:
        print(f"Legacy workout JSON cache discarded during migration: {exc}")
        try:
            legacy.replace(marker)
        except OSError:
            pass

def _open_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(_SCHEMA)
    _migrate_legacy_json(db_path)
    return conn
```

`load_month_cache(db_path, year, month)` → 查询 `day BETWEEN 月首 AND 月末`,`ORDER BY day, moving_time`,经 `WorkoutActivity.from_cache_dict` 同款字段构造(可直接查列组 tuple)。表不存在/库损坏由 `_open_db` 建表兜底;查询异常打印并返回 `[]`(沿用"缓存坏 → 空"语义)。
`save_month_cache(db_path, year, month, activities)` → `DELETE FROM activities WHERE day BETWEEN ? AND ?` 后批量 `executemany INSERT OR REPLACE`,`with conn:` 提交;**save 同样以 try/except 包裹,`sqlite3.DatabaseError` 时打印并放弃本次写入**(与 JSON 版"corrupt 即重建"语义对齐,异常不得穿透到 `epd_status.py`)。所有连接用 `try/finally: conn.close()` 显式关闭。
`merge_activities_into_cache(...)` → 现有"按 id 合成键去重 + 排序"逻辑保留,写库改调 `save_month_cache`(幂等,天然实现 INSERT OR REPLACE 覆盖语义)。

实现注意:`workout_data.py` 顶部新增 `import sqlite3`(`json` 保留供迁移用)。

同步删除旧 JSON 实现的 `CACHE_VERSION`/JSON 读写代码,模块 docstring 更新为 SQLite 描述。

- [ ] **Step 4: 运行测试确认 GREEN**

Run: `.venv/bin/python -m pytest tests/test_workout.py tests/test_workout_card.py -q`
Expected: 全部 PASS(含迁移测试)。

- [ ] **Step 5: Commit**

```bash
git add workout_data.py tests/test_workout.py
git commit -m "feat: switch workout cache from JSON to SQLite"
```

### Task 2: 调用方切换到 .db 路径

**Files:**
- Modify: `epd_status.py`(两处 `Path(__file__).with_name(".workout-cache.json")`)
- Modify: `.gitignore`
- Test: 全量回归

- [ ] **Step 1: 修改 epd_status.py 两处缓存路径字面量**

`.workout-cache.json` → `.workout-cache.db`(单模式分支与 rotation 分支各一处)。

- [ ] **Step 2: 更新 .gitignore**

追加:
```
.workout-cache.db
.workout-cache.db-journal
.workout-cache.json.migrated
```
(原 `.workout-cache.json`/`.json.tmp` 两行保留,照顾未迁移用户。)

- [ ] **Step 3: 全量测试 + 真实 dry-run 冒烟**

Run: `.venv/bin/python -m pytest tests/ -q` → 期望 90+ 全 PASS。
Run: `.venv/bin/python epd_status.py --config <含 workout 段的 config> --mode workout --dry-run` → 期望日志出现 `Workout summary:` 且项目根生成 `.workout-cache.db`,不再生成 `.workout-cache.json`。

- [ ] **Step 4: Commit**

```bash
git add epd_status.py .gitignore
git commit -m "chore: point workout page at SQLite cache file"
```

### Task 3: 文档同步

**Files:**
- Modify: `README.md`(缓存文件提及处)
- Test: 无(纯文档)

- [ ] **Step 1: 更新 README 缓存说明**

当前分支 README 尚无 workout 缓存描述(仅 `.gitignore` 提及),因此是**新增**一节:在功能表后补充运动日历页说明——数据来自 intervals.icu,本地缓存为 SQLite(`.workout-cache.db`):按月查询、活动 ID 去重、API 失败自动降级读库、旧 JSON 缓存首次运行自动迁移并留档为 `.json.migrated`。

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: describe SQLite workout cache and legacy migration"
```

---

## 明确不做(YAGNI)

- 不引入 ORM / SQLAlchemy——标准库 `sqlite3` 足够。
- 不加 WAL 模式、连接池、多线程锁——单进程 launchd 低频写。
- 不做 DB 压缩/清理任务——单月数据量级为个位~两位数行。
- 不改公开函数签名——调用方(除路径字面量外)零改动。
