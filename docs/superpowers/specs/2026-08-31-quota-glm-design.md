# quota_glm 页面设计（Codex + GLM）

日期：2026-08-31
状态：已确认（用户批准）

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
    `{"label": "5 HOURS"|"7 DAYS", "used": float(0-100), "reset_at": int|None}`
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
  - `glm_provider = {"name": "GLM · PRO", "windows": [...], "connected": True}` →
    下半区按 CODEX 区同样式渲染：标题（含等级）+ CONNECTED + 两列进度条 + 重置时间
  - GLM 窗口缺失（老套餐仅 5 小时）→ 缺失列显示虚线框 `unavailable`
  - `connected: False` → 下半区渲染 NOT CONNECTED / awaiting account（复用现有分支）
- `build_quota_card(width, height, windows)` 改为一行委托
  `_build_dual_quota_card(width, height, windows, glm_provider=None)`；
  签名与返回值 `(black, red, preview)` 不变，现有测试保证行为不变
- 新增 `build_quota_glm_card(width, height, codex_windows, glm_windows, glm_level)`：
  - 组装 `glm_provider` 后委托 `_build_dual_quota_card`
  - `glm_windows is None` 或 `glm_level is None` 之外的失败场景由调用方降级：
    fetch 抛异常时打印原因，以 `connected: False` 渲染（页面仍展示 Codex 数据）

### 3. 显示状态去重

- 新增 `quota_glm_display_state(codex_windows, glm_windows, glm_level) -> dict`：
  `mode: "quota-glm"`，包含 codex 两窗口的 `remaining/reset` 与 glm 两窗口的
  `remaining/reset` + `level`；沿用 `quota_display_state` 的字段粒度
- 复用现有 `unchanged()` 机制：无可见变化跳过 BLE 写入

### 4. CLI / 轮播接入

- `--mode` choices 增加 `quota_glm`；`display_mode` 合法值同步
- rotation 分支：候选页 `quota_glm` → 走对应数据路径（scope 为完整轮播页清单）
- `rotation_state.py`：
  - `VALID_PAGE_IDS` 增加 `"quota_glm"`
  - `validate_rotation_config`：`rotation.pages` 含 `quota_glm` 时，`config["glm"]["api_key"]`
    必须非空，否则启动即报错
- `main()` 的单模式分支 `mode == "quota_glm"`：
  1. `glm_api_key = configured(None, "EPD_GLM_API_KEY", glm_config.get("api_key"))`，
     缺失 → 报错退出
  2. `windows = fetch_codex_quota()`（复用现有）
  3. 尝试 `glm = fetch_glm_quota(glm_api_key)`；失败 → 打印原因，`glm_windows=None`、
     `glm_level=None`，渲染 connected=False 的下半区
  4. 组装显示状态、`unchanged()` 检查、渲染发送（与 `quota` 分支同构）

### 5. 证书工具迁移

- `epd_status.py` 的 `_https_urlopen` 移入新文件 `http_utils.py`
- `epd_status.py` 顶部 `from http_utils import https_urlopen` 并保留
  `_https_urlopen = https_urlopen` 别名（调用点零改动；测试如有 patch 同样兼容）
- `glm_data.py` 复用 `http_utils.https_urlopen`

### 6. 配置

- `config.json`（本地，不入库）：新增 `"glm": {"api_key": "<真实key>"}`
- `config.example.json`（入库）：新增 `"glm": {"api_key": "YOUR_BIGMODEL_API_KEY"}`
- README 的配置说明同步补充

## 错误处理汇总

| 场景 | 行为 |
|------|------|
| `glm.api_key` 未配置 | rotation 校验直接报错；单模式分支报错退出 |
| HTTP 401/403 | 打印 "GLM API key 无效或已过期"，下半区渲染 NOT CONNECTED |
| `success: false` | 打印 `msg`，下半区渲染 NOT CONNECTED |
| 网络超时/解析失败 | 打印原因，下半区渲染 NOT CONNECTED |
| 仅 1 条 TOKENS_LIMIT | 7 DAYS 列渲染虚线框 unavailable |
| GLM 全失败 | 页面仍发送（Codex 数据有效），状态去重仍生效 |

## 测试计划（pytest，沿用现有风格）

`tests/test_glm_data.py`：

- `parse` 单元：unit 3/6 分类、毫秒→秒、level 提取、TIME_LIMIT 忽略、老套餐单条降级、
  unit 缺失兜底启发式、`success:false` 报错、401/403 报错、percentage 缺失按 0
- fetch 层：mock opener，验证 Authorization 头原样携带、超时包装为 GlmQuotaError

`tests/test_epd_status.py` 增补：

- `build_quota_glm_card` 渲染 400x300、双供应商、GLM 缺窗口虚线框
- `quota_glm_display_state` 仅可见字段参与对比
- `build_quota_card` 回归：迁移后输出与迁移前一致（现有测试覆盖）

`tests/test_rotation_stocks.py` 增补：

- `quota_glm` 在 VALID_PAGE_IDS、缺 `glm.api_key` 时校验报错、配置齐全时通过

验收：现有全部测试保持绿色（证明 quota 未动）。

## 非目标

- 不改 `quota` 页面的任何渲染/行为
- 不做 GLM 请求重试与代理支持
- 不在状态文件中存 GLM key；不打印 key 或完整响应
