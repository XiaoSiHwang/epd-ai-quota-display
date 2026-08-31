# quota_glm 页面设计（Codex + GLM）

日期：2026-08-31
状态：已确认（用户批准，评审修订 v2）

## 背景与目标

现有 `quota` 页面显示 CODEX（上半区）与 CLAUDE CODE（下半区占位）。用户订阅了
GLM Coding Plan（智谱），希望新增一个 `quota_glm` 页面：上半区 CODEX、下半区 GLM，
两个页面并存，`quota` 完全不动。

## 已验证的 GLM 用量接口

- 端点：`GET https://open.bigmodel.cn/api/monitor/usage/quota/limit`
- 认证：`Authorization` 头直接放 API key（不加 Bearer 前缀；已实测三种头部变体均可，
  `X-Client-Secret` 非必需）
- 响应（实测 200）：

```json
{
  "code": 200,
  "msg": "操作成功",
  "success": true,
  "data": {
    "level": "pro",
    "limits": [
      {"type": "TOKENS_LIMIT", "unit": 3, "number": 5, "percentage": 0},
      {"type": "TOKENS_LIMIT", "unit": 6, "number": 1, "percentage": 17,
       "nextResetTime": 1788602435998},
      {"type": "TIME_LIMIT", "unit": 5, "number": 1, "usage": 1000, "…": "…"}
    ]
  }
}
```

字段解读（对照 cc-switch `src-tauri/src/services/coding_plan.rs` 的实现）：

- `unit: 3` → 5 小时滚动窗口；`unit: 6` → 每周窗口（不绑定 `number` 的具体值）
- `percentage` = 该窗口已用百分比
- `nextResetTime` = 毫秒时间戳
- `data.level` = 套餐等级（如 `pro`）
- 只取 `type` 为 `TOKENS_LIMIT` 或 `CREDIT_LIMIT` 的条目；`TIME_LIMIT`（如 web-search
  次数）忽略
- 老套餐只返回 1 条 TOKENS_LIMIT → 降级为只显示 5 小时窗口

## 方案

### 1. 新文件 `glm_data.py`

- `GlmQuotaError(RuntimeError)`：所有失败统一抛此异常，消息友好
- `fetch_glm_quota(api_key: str) -> dict`：
  - 请求端点，超时 15s，复用 certifi CA 包装（见 §5）
  - HTTP 401/403 → "GLM API key 无效或已过期"
  - `success: false` → 取 `msg` 报错
  - 网络异常 / JSON 解析失败 → 包装为 `GlmQuotaError`
  - 解析 `data.limits[]` → `windows` 列表；每个窗口
    `{"label": "5 HOURS"|"7 DAYS", "used": float, "reset_at": int|None}`
  - `used` 与 Codex 路径一致做 clamp：`max(0.0, min(100.0, percentage))`
  - `unit` 缺失或不识别时的兜底启发式（同 cc-switch）：无 `nextResetTime` 的条目优先
    归 5 小时窗口，其余按重置时间升序依次填入空缺槽位
  - `nextResetTime` 毫秒 → 秒（`// 1000`）
  - 返回 `{"level": str|None, "windows": [...]}`；`level` 取 `data.level`
- 国内端点直连，不走 proxy，无重试（每次刷新由 launchd 定时触发，失败留给下次）

### 2. 卡片渲染（`epd_status.py`）

- 将 `build_quota_card` 的内部绘制抽为私有函数
  `_build_dual_quota_card(width, height, codex_windows, glm_provider=None)`：
  - `glm_provider = None` → 下半区维持现状（CLAUDE CODE / NOT CONNECTED / awaiting
    account），即现有 `quota` 输出逐像素不变
  - `glm_provider = {"name": str, "windows": [...], "connected": True}` → 下半区按
    CODEX 区同样式渲染：标题（含等级）+ CONNECTED + 两列进度条 + 重置时间
  - `glm_provider["name"]` 格式：level 存在时 `GLM · {LEVEL大写}`，level 为 None 时
    仅 `GLM`
  - `connected` 信号：`glm_windows is None` → 渲染 NOT CONNECTED（复用现有分支）；
    `glm_windows` 有效但 `glm_level is None` → 仍渲染 CONNECTED，标题仅 `GLM`
  - GLM 窗口缺失（老套餐仅 5 小时）→ 缺失列显示虚线框 `unavailable`
- `build_quota_card(width, height, windows)` 改为一行委托
  `_build_dual_quota_card(width, height, windows, glm_provider=None)`；
  签名与返回值 `(black, red, preview)` 不变
- 新增 `build_quota_glm_card(width, height, codex_windows, glm_windows, glm_level)`：
  组装 `glm_provider` 后委托 `_build_dual_quota_card`
- **像素级回归保障（重构前置步骤）**：`build_quota_card` 的 black 层烙有
  `datetime.now()` 时间戳（UPDATED 行），逐字节对比必须冻结时钟——fixture 生成与
  回归测试**两端都 patch `epd_status.datetime` 为同一固定值**，否则 black 层每分钟
  都不同，测试必然失败。具体步骤：
  1. 重构前，patch `epd_status.datetime`（固定 `datetime.now()` 返回），用固定窗口集
     渲染 `build_quota_card`，把 `pack_monochrome(black)` / `pack_monochrome(red)` 存为
     `tests/golden/quota_black.bin` / `tests/golden/quota_red.bin`；
     `quota_display_state` 输出存为 `tests/golden/quota_state.json`
  2. 新增回归测试：**同样的 patch 下**断言重构后输出与 fixture 逐字节一致
  3. 这是"quota 逐像素不变"约束的验收标准；red 层与 state JSON 无时间依赖，
     但统一在同一冻结时钟下生成与对比，规则单一

### 3. 显示状态去重

- 新增 `quota_glm_display_state(codex_windows, glm_windows, glm_level) -> dict`，模式
  固定为：

```python
{
    "version": DISPLAY_STATE_VERSION,
    "mode": "quota-glm",
    "codex": [{"label", "remaining", "reset"} | None, ...],   # 同 quota_display_state 条目
    "glm":   [{"label", "remaining", "reset"} | None, ...],   # glm 失败 → [None, None]
    "glm_level": str | None,
}
```

- GLM 失败时 `glm` 为 `[None, None]`、`glm_level` 为 `None`，与 GLM 恢复后的状态必然
  不同，保证 `unchanged()` 去重在失败→恢复切换时触发刷新（需有对应测试）
- 复用现有 `unchanged()` 机制：无可见变化跳过 BLE 写入
- 命名说明：页面 ID 为 `quota_glm`（Python 标识符友好），显示状态 mode 为
  `"quota-glm"`（与卡片标题风格一致）；二者是有意区分的命名空间，不做统一

### 4. CLI / 轮播接入

- `--mode` choices 增加 `quota_glm`；`display_mode` 合法值同步
- rotation 分支：**在 calendar-sensor 的全捕获 `else:` 之前**插入显式
  `elif candidate == "quota_glm":` 分支（否则会静默落入 sensor 路径）；新增调度回归
  测试断言轮播候选 `quota_glm` 构建双配额卡而非 sensor 卡
- `rotation_state.py`：
  - `VALID_PAGE_IDS` 增加 `"quota_glm"`
  - `validate_rotation_config`：`rotation.pages` 含 `quota_glm` 时，`config["glm"]["api_key"]`
    必须非空，否则启动即报错
- `main()` 的单模式分支与轮播分支共用数据路径：
  1. `glm_config = config.get("glm") or {}`（含 `isinstance(dict)` 防御，对齐
     `workout_config` 等既有模式）
  2. `glm_api_key = configured(None, "EPD_GLM_API_KEY", glm_config.get("api_key"))`，
     缺失 → 报错退出
  3. `windows = fetch_codex_quota()` —— **无 try/except，失败即中止本次运行**（与现有
     `quota` 模式一致）；错误表中"页面仍发送"仅指 GLM 失败场景
  4. 尝试 `glm = fetch_glm_quota(glm_api_key)`；失败 → 打印原因，
     `glm_windows=None`、`glm_level=None`，渲染 NOT CONNECTED 下半区
  5. 组装显示状态、`unchanged()` 检查、渲染发送（与 `quota` 分支同构）

### 5. 证书工具迁移

- `epd_status.py` 的 `_https_urlopen` 移入新文件 `http_utils.py`
- `epd_status.py` 顶部 `from http_utils import https_urlopen as _https_urlopen`
  （调用点零改动；测试如有 patch 同样兼容）
- `glm_data.py` 必须使用 `from http_utils import https_urlopen` 形式导入（名字绑定进
  `glm_data` 命名空间），使测试计划中 patch `glm_data.https_urlopen` 的 seam 生效

### 6. 配置

- `config.json`（本地，不入库）：新增 `"glm": {"api_key": "<真实key>"}`
- `config.example.json`（入库）：新增 `"glm": {"api_key": "YOUR_BIGMODEL_API_KEY"}`
- README 配置说明同步补充 `glm` 块与 `EPD_GLM_API_KEY` 环境变量

## 错误处理汇总

| 场景 | 行为 |
|------|------|
| `glm.api_key` 未配置 | rotation 校验直接报错；单模式分支报错退出 |
| HTTP 401/403 | 打印 "GLM API key 无效或已过期"，下半区渲染 NOT CONNECTED |
| `success: false` | 打印 `msg`，下半区渲染 NOT CONNECTED |
| 网络超时/解析失败 | 打印原因，下半区渲染 NOT CONNECTED |
| 仅 1 条 TOKENS_LIMIT | 7 DAYS 列渲染虚线框 unavailable |
| GLM 失败 | 页面仍发送（Codex 数据有效），状态去重仍生效 |
| Codex 获取失败 | 本次运行中止（与现有 `quota` 模式一致），GLM 不单独兜底 |

## 测试计划（pytest，沿用现有风格）

`tests/test_glm_data.py`：

- `parse` 单元：unit 3/6 分类、毫秒→秒、level 提取、TIME_LIMIT 忽略、老套餐单条降级、
  unit 缺失兜底启发式、`success:false` 报错、401/403 报错、percentage 缺失按 0、
  used clamp（>100 / <0）
- fetch 层：**patch `glm_data.https_urlopen`**（mock seam 固定于此，防止真实网络调用），
  验证 Authorization 头原样携带（无 Bearer）、超时包装为 GlmQuotaError

`tests/test_epd_status.py` 增补：

- **golden fixture 回归**（重构前置生成）：`build_quota_card` 输出字节与
  `tests/golden/quota_*.bin` 一致；`quota_display_state` 与 golden JSON 一致
- `build_quota_glm_card` 渲染 400x300、双供应商、GLM 缺窗口虚线框、glm_level=None
  时标题仅 `GLM`
- `quota_glm_display_state` 仅可见字段参与对比；glm 失败状态 ≠ glm 恢复状态
- 调度回归：轮播候选 `quota_glm` 走双配额卡路径（非 sensor 路径）

`tests/test_rotation_stocks.py` 增补：

- `quota_glm` 在 VALID_PAGE_IDS、缺 `glm.api_key` 时校验报错、配置齐全时通过

验收：现有全部测试保持绿色 + golden fixture 逐字节一致（共同证明 quota 未动）。

## 非目标

- 不改 `quota` 页面的任何渲染/行为
- 不做 GLM 请求重试与代理支持
- 不在状态文件中存 GLM key；不打印 key 或完整响应
