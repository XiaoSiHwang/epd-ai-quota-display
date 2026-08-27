# 轮播模式与股票指数页设计

日期：2026-08-27
状态：已确认（与需求方逐项对齐）
分支：`feature/rotation-stocks-page`

## 1. 背景与目标

现有程序每次运行只渲染一种固定模式（`quota` / `calendar-agenda` / `calendar-sensor`），
由 launchd 每 30 分钟触发一次，内容不变则跳过写屏。

本次改造目标：

1. 新增**股票指数页**：一次展示 7 个全球指数 —— 美股（道琼斯、标普500、纳斯达克）、
   中国（上证、创业板指）、日韩（日经225、韩国KOSPI），布局顺序美 → 中 → 日韩。
2. 引入**页面轮播机制**：按配置的页面清单循环切换显示内容，切换策略智能跳过无变化页。

## 2. 已确认的需求决策

| 决策点 | 结论 |
| --- | --- |
| 轮询间隔 | 5 分钟级别（美股夜盘也要看）；间隔可配置 |
| 参与轮播的页面 | 完全配置化：`rotation.pages` 数组决定页面条目与顺序；默认 `["calendar-agenda", "stocks"]` |
| 涨跌配色 | 红涨黑跌（A 股直觉），红 = 上涨，黑 = 下跌 |
| 页面排版 | 三列全部水平居中（名称 / 当前价 / 涨跌幅）；分区标题两侧虚线夹持居中；右上角 UPD 最后刷新时间；不显示图例行 |
| 数据源 | Yahoo Finance（yfinance 库）。数据源模块可替换，为将来接入其他付费 API 预留接口 |
| 数据拉取失败 | 保留屏幕上一帧不动，日志记录错误（与项目"失败保留上一帧"哲学一致） |
| 轮换策略 | B 变化才切换：轮到某页时先取数比对，内容与其上次写屏状态相同则跳过本轮，屏幕保持该页继续显示 |

### 用户环境注意事项

- 该 Mac 通过 `127.0.0.1:7890` 本地代理访问外网（Yahoo 必须）；launchd 任务不继承终端
  环境变量，因此代理必须显式写入 `config.json` 的 `stocks.proxy` 字段。
- launchd 触发间隔需调整为与 `rotation.interval_seconds` 一致（安装脚本已有
  `EPD_UPDATE_INTERVAL_SECONDS` 支持）。
- 夜间刷新要求 Mac 不睡眠（系统设置或 caffeinate），本设计不含唤醒逻辑。

## 3. 架构设计

### 3.1 配置结构（config.json）

```json
{
  "display_mode": "rotation",
  "rotation": {
    "pages": ["calendar-agenda", "stocks"],
    "interval_seconds": 300
  },
  "stocks": {
    "datasource": "yfinance",
    "proxy": "http://127.0.0.1:7890",
    "indices": [
      {"zone": "US",  "symbol": "^DJI",      "name": "道琼斯"},
      {"zone": "US",  "symbol": "^GSPC",     "name": "标普500"},
      {"zone": "US",  "symbol": "^IXIC",     "name": "纳斯达克"},
      {"zone": "CN",  "symbol": "000001.SS", "name": "上证指数"},
      {"zone": "CN",  "symbol": "399006.SZ", "name": "创业板指"},
      {"zone": "ASIA","symbol": "^N225",     "name": "日经225"},
      {"zone": "ASIA","symbol": "^KS11",     "name": "韩国KOSPI"}
    ]
  }
}
```

规则：

- `display_mode: "rotation"` 为新模式；现有三种模式原样保留、行为不变（向后兼容）。
- `rotation.pages` 缺省时等价于 `["quota"]`（即旧行为）。
- 分区顺序由索引条目的 `zone` 出现顺序自然形成，不做硬编码分区；默认配置即为
  美 → 中 → 日韩。同 zone 连续条目归入同一分区，分区标题渲染为 "US · 美股"
  这样的双语标签（内建映射表，可在条目级覆盖）。
- 所有 CLI 参数仍优先于配置文件（沿用现有 `configured()` 链）。

### 3.2 模块划分

遵循"多个小文件"原则，新逻辑独立成模块，避免继续膨胀 `epd_status.py`：

```
epd-ai-quota-display/
├── epd_status.py            # 入口：新增 rotation 模式分发；state 文件改为按页分存
├── stocks_data.py           # 新增：行情获取层（可替换数据源）
│                            #   fetch_indices(config) -> list[IndexQuote]
│                            #   IndexQuote(name, price, change_pct, zone)
│                            #   yfinance 实现 + 统一错误类型 StocksDataError
│                            #   proxy 传递；超时保护；部分失败降级（见 3.4）
├── stocks_card.py           # 新增：股票页渲染（纯函数 build_stocks_card）
│                            #   黑/红双图层 + 合成预览图，与现有 card 函数签名一致
└── rotation_state.py        # 新增：轮播状态管理
                             #   单一 state 文件内部结构升级：
                             #   {"version":..., "current_page": "stocks",
                             #    "pages": {"calendar-agenda": {...每页可见状态...},
                             #              "stocks": {...}}}
```

`epd_status.py` 中新增：

- `rotating_display_state()`：计算本轮应显示哪一页的调度逻辑（3.3）。
- rotation 分支：取下一页 ID → 取该页数据 → 与该页上次状态比对 → 未变化则整体跳过；
  变化则渲染并写屏后更新当前页指针与该页状态。

### 3.3 轮换调度算法（策略 B）

```
本轮运行:
  pages = config.rotation.pages          # 如 [A, B]
  last_page = state.current_page         # 上次实际写在屏上的页
  next_index = (last_page 在 pages 中的下标 + 1) % len(pages)
  candidate = pages[next_index]

  data = 取 candidate 页数据               # 失败 → 见 3.4
  new_state = candidate 页的 display_state(data)

  if new_state == state.pages[candidate]:
      log("no change; keep showing previous page")
      return                              # 不写屏、不改 current_page
  else:
      渲染 → BLE 写屏 → 成功后:
        state.current_page = candidate
        state.pages[candidate] = new_state
```

要点：

- 屏幕上永远只有一页；`current_page` 只在 BLE 写屏成功后推进（复用现有
  "成功才保存状态"语义）。
- 若连续多轮同一页无变化，它一直是屏幕上的画面，后续轮次会不断重取数据比对 ——
  一旦行情变化立即切过去。行为符合"交易时段正常轮播、休市时段停住"的目标。
- 页面从 `pages` 清单中被移除时，其残留状态在下次保存时清理。

### 3.4 错误处理

| 故障 | 行为 |
| --- | --- |
| yfinance 请求失败/超时/被限流 | 记录日志，进程以非零码退出，屏幕保持上一帧（launchd 下轮自动再试） |
| 7 个指数中部分符号无数据 | 缺失的行渲染为"—"+虚线进度样式的占位（沿用配额页 unavailable 视觉语言）；全失败则视为整页失败走上一行策略 |
| config.json 无 stocks 配置而 rotation.pages 含 stocks | 启动即报错退出（fail fast，宁可不用默认行情源也不能用错配置） |
| BLE 写屏失败 | 沿用现有 write_card_with_retry 重试一次；仍失败则 state 不更新 |

### 3.5 股票页渲染规格

画布 400×300，黑白红三色双图层（红色层承载上涨元素）：

```text
GLOBAL INDICES                                              UPD 21:05
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━（粗分隔线）
───────────────── US · 美股 ─────────────────（虚线夹持居中）
     道琼斯            44291.52              ▲ +0.35%   ← 红
     标普500           6032.38               ▲ +0.20%   ← 红
     纳斯达克          19269.39              ▼ −0.25%   ← 黑
───────────────── CN · 中国 ─────────────────
     ... 同上三列 ...
──────────────── ASIA · 日韩 ─────────────────
     日经225 / 韩国KOSPI 两行 ...
```

- 三列水平居中对齐（列中心分别位于 x≈100 / 200 / 300 处）。
- 平铺时不显示任何图例说明文字。
- 上涨（chg_pct > 0）：价格与涨跌幅红色（红色图层），▲ 前缀；下跌：黑色，▼；
  平盘黑色 ±0.00%。
- 数字使用 tabular 字距风格的大字号，与前序页面气质一致；PingFang 渲染
  Y 偏移问题沿用项目已知处理方式。
- UPD 时间为数据成功取得的时刻。

### 3.6 launchd / 自动更新适配

- 安装脚本无需改动；用户执行
  `EPD_UPDATE_INTERVAL_SECONDS=300 ./scripts/install-launchagent.sh`
  即可切到 5 分钟节奏（README 补充说明）。
- `.last-display-state.json` 结构升级为按页分存；首次运行旧格式文件时
  （含顶层 `mode` 字段而无 `pages` 字典）视为未知状态，直接忽略并重建
  —— 单页模式下至多多刷一次屏，代价可接受。

## 4. 测试方案

单元测试（pytest，遵循项目现有 tests/test_epd_status.py 风格）：

- rotation 调度：首轮从 current_page=None 起、循环推进、跳过无变化页、
  移除页残留状态清理。
- 状态兼容：旧单页 state 文件能被识别并重置。
- stocks_data：解析 yfinance 返回结构（mock 网络）、缺符号降级、代理参数传递、
  全失败抛 StocksDataError。
- stocks_card：给定固定输入的图层快照测试（尺寸断言 + 关键文本存在性 +
  涨跌颜色归属正确：上涨画在红层、下跌画在黑层）。
- 配置加载：rotation 缺省回退、stocks 配置缺失报错。

手动验证路径：

1. `.venv/bin/python epd_status.py --mode rotation --dry-run` → 生成预览 PNG。
2. `--force` 强制写屏一轮验证硬件链路。
3. 观察 2 轮周期内"休市时段不再重复刷屏"的表现。

## 5. 明确不做（YAGNI）

- 不做分时段差异化频率（用户拍板统一 5 分钟）。
- 不做成交量、分时图等其他行情维度。
- 不做多设备轮播差异化。
- 不内置除 yfinance 外的具体第二数据源实现，仅保证接口可替换。
