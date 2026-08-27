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
| 轮换策略 | B 变化才切换：轮到某页时先取数比对，内容与其上次写屏状态相同则跳过本轮，屏幕保持当前画面不变 |

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
- **缺省键回退**：`rotation.pages` 缺省时等价于 `["quota"]`（即旧行为）；
  第 2 节所称"默认 `["calendar-agenda", "stocks"]`"仅指新装用户的
  `config.example.json` 示例写法，不是缺省回退值。
- 分区顺序由索引条目的 `zone` 出现顺序自然形成，不做硬编码分区；默认配置即为
  美 → 中 → 日韩。同 zone 连续条目归入同一分区，分区标题渲染为 "US · 美股"
  这样的双语标签（内建映射表；条目可加可选字段 `"zone_label": "自定义标题"`
  覆盖所在分区的显示标题）。
- 所有 CLI 参数仍优先于配置文件（沿用现有 `configured()` 链）。

**配置校验（启动时 fail fast）**：

- 合法页面 ID 仅限：`quota` / `calendar-agenda` / `calendar-sensor` / `stocks`；
- `pages` 为空数组、含未知 ID 或含重复 ID → 报错退出；
- `rotation.pages` 含 `stocks` 而 `stocks.indices` 缺失/为空 → 报错退出；
- `rotation.interval_seconds` 存在且 < 60 → 报错退出（与安装脚本下限一致）。

### 3.2 模块划分

遵循"多个小文件"原则，新逻辑独立成模块，避免继续膨胀 `epd_status.py`：

```
epd-ai-quota-display/
├── epd_status.py            # 入口：新增 rotation 模式分发
├── stocks_data.py           # 新增：行情获取层（可替换数据源）
│                            #   fetch_indices(config) -> list[IndexQuote]
│                            #   IndexQuote(name, price, change_pct, zone)
│                            #   yfinance 实现 + 统一错误类型 StocksDataError
│                            #   优先一次批量 download() 取全部符号；
│                            #   总超时预算默认 60s（可配 stocks.timeout_seconds）。
├── stocks_card.py           # 新增：股票页渲染（纯函数 build_stocks_card）
│                            #   黑/红双图层 + 合成预览图，与现有 card 函数签名一致
└── rotation_state.py        # 新增：轮播状态管理（按页分存的 state 文件读写）
                             #   select_next_page(state, pages) -> str：纯函数调度器
                             #     - current_page 为 None 或不在 pages 中 → pages[0]
                             #     - 正常情况 → (当前下标 + 1) % len(pages)
                             #     - pages 为空已由配置校验拦截，此处不再防
```

**状态文件统一升级**（评审意见采纳）：

- 四种模式全部迁移到嵌套结构；旧的 `quota_display_state()` 等返回值成为
  `pages[<mode>]` 的载荷，键名保持原样以便比对面逻辑不变：
  ```json
  {"version": 2, "current_page": "stocks",
   "pages": {"calendar-agenda": {...}, "stocks": {...}}}
  ```
- 单模式分支读 `state["pages"].get(<mode>)`、写回时保留仍处于本分支管辖的
  其他页条目并更新
  `current_page = <mode>`；三种旧模式的"内容未变跳过"判定改为对该页条目的比对，
  行为语义与现在完全一致。
- 兼容读取：加载时若发现顶层含 `mode` 而无 `pages`（v1 旧格式），整体视为未知
  状态丢弃重建——单模式下至多多刷一次屏，代价可接受。

### 3.3 轮换调度算法（策略 B）

```
本轮运行:
  pages = config.rotation.pages          # 如 [A, B]，已过配置校验
  candidate = select_next_page(state, pages)
        # 首轮(current_page=None)或残留页已被移除 → pages[0]
        # 正常 → (last_page 下标 + 1) % len(pages)

  data = 取 candidate 页数据               # 失败 → 见 3.5
  new_state = candidate 页的 display_state(data)

  if new_state == state.pages[candidate]:
      log("no change; keep showing previous page")
      return                              # 不写屏、不改 current_page、不动 state 文件
  else:
      渲染 → BLE 写屏 → 成功后:
        state.current_page = candidate
        state.pages[candidate] = new_state
```

要点：

- 屏幕上永远只有一页；`current_page` 只在 BLE 写屏成功后推进（复用现有
  "成功才保存状态"语义）——写屏失败时状态文件必须保持原样。
- 若连续多轮同一页无变化，它一直是屏幕上的画面，后续轮次会不断重取数据比对 ——
  一旦行情变化立即切过去。行为符合"交易时段正常轮播、休市时段停住"的目标。
- `--dry-run` 只渲染不涉及 state：current_page 与各页条目均不读写，预览输出
  到 `--output`（缺省文件名含页面名，如 `preview-stocks.png`，避免覆盖固定
  test-card.png）。
- 页面从 `pages` 清单中被移除时，其残留状态在下次保存时清理。

### 3.4 股票页可比状态定义

跳过逻辑依赖精确的可比载荷；以下字段进入 `stocks_display_state()`：
**每个指数条目的 (name, zone 分区标题, 格式化价格字符串, 四舍五入到两位的
涨跌幅字符串, 数据可用标志)**。缺失符号以 `"unavailable": true` 参与
比对 —— 可用性翻转本身算作可见变化。

明确排除项：数据获取时间戳不参与比对（否则每轮必判变化，跳过机制失效），
但渲染时写入 UPD 区域；跳过轮次屏幕保留上一帧的旧 UPD 时间是预期行为
（UPD 实际含义 = 最后写屏时刻的数据取得时间）。

### 3.5 错误处理

| 故障 | 行为 |
| --- | --- |
| yfinance 请求失败/超时/被限流 | 记录日志，进程以非零码退出，屏幕保持上一帧（launchd 下轮自动再试） |
| 7 个指数中部分符号无数据 | 缺失的行渲染为"—"占位（沿用配额页 unavailable 视觉语言），可用标志参与状态比对；全失败则视为整页失败走上一行策略 |
| config.json 无 stocks 配置而 rotation.pages 含 stocks | 启动即报错退出（fail fast，宁可不用默认行情源也不能用错配置） |
| BLE 写屏失败 | 沿用现有 write_card_with_retry 重试一次；仍失败则 state 文件完全不更新 |

### 3.6 代理与依赖

- yfinance 无一等公民 proxy 参数；实现采用**预导入注入环境变量**方案：
  在 `stocks_data` 模块内于导入 yfinance 前设置 `HTTP_PROXY` / `HTTPS_PROXY`
  （取自 `stocks.proxy`，未配置时不设）。注入是进程全局副作用，此处明确接受：
  该机器所有外部流量本就经此代理路由，同进程的其他网络调用（配额/传感器）
  不受实质影响。此机制必须在 launchd 环境下实测（launchd 不继承终端环境变量）。
- `requirements.txt` 新增 `yfinance`；`config.example.json` 增加 `rotation`
  与 `stocks` 示例段（含注释性默认值）。

### 3.7 股票页渲染规格

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

- 三列水平居中对齐（列中心分别位于 x≈100 / 200 / 310 处）。
- 平铺时不显示任何图例说明文字。
- 上涨（chg_pct > 0）：价格与涨跌幅红色（红色图层），▲ 前缀；下跌：黑色，▼；
  平盘黑色 ±0.00%。
- 数字使用 tabular 字距风格的大字号，与前序页面气质一致；PingFang 渲染
  Y 偏移问题沿用项目已知处理方式。
- UPD 时间为数据成功取得的时刻。

### 3.8 launchd / 自动更新适配

- 安装脚本无需改动；用户执行
  `EPD_UPDATE_INTERVAL_SECONDS=300 ./scripts/install-launchagent.sh`
  即可切到 5 分钟节奏（README 补充说明）。
- 状态文件格式升级与兼容读取见 3.2。

## 4. 测试方案

单元测试（pytest，遵循项目现有 tests/test_epd_status.py 风格，目标覆盖率 ≥80%）：

- **调度器 select_next_page（纯函数）**：首轮 current_page=None → pages[0]；
  正常循环推进；current_page 指向已移除页 → pages[0]。
- **状态兼容**：v1 旧单页 state 文件被识别并整体重置；嵌套 v2 结构读写往返一致。
- **未变跳过**：display_state 相同 → 不写屏、state 文件字节不变；
  不同 → 写屏成功才更新 current_page 与该页条目。
- **写屏失败不变式**：模拟 BLE 抛错 → state 文件保持调用前原样。
- **stocks_data**：解析 yfinance 返回结构（mock 网络）、缺符号降级为
  unavailable、代理环境变量注入、超时预算生效、全失败抛 StocksDataError。
- **stocks_card**：固定输入的图层断言（尺寸、关键文本存在性、涨跌颜色归属：
  上涨画在红层、下跌画在黑层）；UPD 时间戳不进入可比状态载荷。
- **配置校验**：缺省回退 ["quota"]；未知页 ID / 重复 ID / 空数组 /
  stocks 缺 indices / interval_seconds < 60 各自报错。

手动验证路径：

1. `.venv/bin/python epd_status.py --mode rotation --dry-run` → 生成预览 PNG
   （state 文件不被动）。
2. `--force` 强制写屏一轮验证硬件链路。
3. 观察 2 轮周期内"休市时段不再重复刷屏"的表现。

## 5. 明确不做（YAGNI）

- 不做分时段差异化频率（用户拍板统一 5 分钟）。
- 不做成交量、分时图等其他行情维度。
- 不做多设备轮播差异化。
- 不内置除 yfinance 外的具体第二数据源实现，仅保证接口可替换。
