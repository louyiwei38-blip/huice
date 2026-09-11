# Range MR V1.1 信号机器人

币安 U 本位合约 **BTCUSDT + ETHUSDT** 的 30 分钟盘整回归 / 突破回踩信号检测。

可推送 Telegram，并可对接 [事件合约跟单面板](http://194.233.90.109:3000) 自动下单：固定 **50 USDT**、周期 **30 分钟**，标的和方向跟信号走。回放统计默认每注本金 250。

- 行情：`https://fapi.binance.com` 公开接口（K 线 + 标记价格）
- 结构周期：30m
- 触发：回放用 1m K 线；实时用标记价格 tick（约每 3 秒轮询）
- 结算：信号后 30 分钟，用当时价格判定胜负
- 时区：展示用 `Asia/Shanghai`

## 策略说明

先用最近 48 根已收盘 30m K 线算箱体（去掉一根极端高低点）。近 6 根成交量低于窗口中位数的 75% 时仍会标记 `is_ranging`，但 **默认不再作为发信号条件**（`require_ranging=False`）。`BOX_EDGE` / `SWING` 在趋势里也会发。

默认启用三种逻辑（`HVN` 代码里有，但未打开）：

| 逻辑 | 场景 | 方向 |
| --- | --- | --- |
| `BOX_EDGE` | 价格进入箱体上沿 / 下沿带 | 上沿空、下沿多 |
| `SWING` | 触及阶段性前高 / 前低 | 前高空、前低多 |
| `SR_FLIP` | 30m 收盘确认突破后，回踩旧阻力/支撑 | 向上突破回踩做多，向下跌破回抽做空。突破窗不再挡住 BOX_EDGE / SWING |

质量过滤（V1.1）：

- 要求价格从带外穿入（`require_cross`）
- 要求当根方向与信号同向（`require_with_bar`）
- 盘整信号 Kaufman ER 超过 0.35 丢弃（过滤单边趋势）
- 追价超过 `0.25 * ATR` 丢弃
- 同一逻辑 + 方向冷却 4 根 30m
- 同一时刻 BOX/SWING 多空同时触发记为冲突，不发信号；SR_FLIP 与它们反向时只丢 SR_FLIP

参数都在 `range_mr/config.py` 的 `Config` 里。

## 目录结构

```
huice/
├── main.py                 # 本地入口：replay / live
├── run_server.py           # 服务器入口：live 异常后自动重启
├── ecosystem.config.js     # PM2 配置
├── tune_params.py          # 用缓存 K 线扫过滤参数（需先有 data/*.csv）
├── requirements.txt
├── range_mr/               # 核心库
│   ├── config.py           # 参数
│   ├── detector.py         # 信号检测
│   ├── structure.py        # 箱体 / 成交量分布 / 摆动点
│   ├── live.py             # 实时轮询 + Telegram + 自动下单
│   ├── trade.py            # 事件合约跟单面板下单
│   ├── replay.py           # 历史回放与结算
│   ├── binance_data.py     # 币安行情
│   └── telegram_notify.py
├── data/
│   ├── telegram.json.example
│   └── trade.json.example
└── deploy/
    ├── range-mr.service    # systemd 备选
    └── start_live.ps1      # Windows 直接跑
```

## 本地运行

依赖：Python 3.10+（用到 `zoneinfo`）。

```bash
git clone -b xinhao https://github.com/louyiwei38-blip/huice.git
cd huice
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 配置 Telegram

```bash
cp data/telegram.json.example data/telegram.json
```

```json
{
  "bot_token": "BotFather 发的 token",
  "chat_id": "数字 chat_id"
}
```

也可改用环境变量：`TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID`。不配也能跑，信号只打印在终端。

先试推一条：

```bash
python -c "from range_mr.config import Config; from range_mr.telegram_notify import load_telegram, send_telegram; t,c=load_telegram(Config()); send_telegram(t,c,'部署成功')"
```

### 配置自动下单

对接事件合约跟单面板 `http://194.233.90.109:3000`。有信号时调用 `POST /api/orders/place`：

- 金额固定 **50 USDT**
- 周期固定 **30 分钟**（`THIRTY_MINUTE`，赔付比 0.85）
- 币种 / 方向来自信号：`BTCUSDT`/`ETHUSDT` + `LONG`(看涨) / `SHORT`(看跌)

```bash
cp data/trade.json.example data/trade.json
nano data/trade.json
```

```json
{
  "enabled": true,
  "base_url": "http://194.233.90.109:3000",
  "username": "面板登录用户名",
  "password": "面板登录密码",
  "leader_account_id": null,
  "order_amount": 50,
  "time_increments": "THIRTY_MINUTE",
  "payout_ratio": "0.85"
}
```

`leader_account_id` 留空则自动选第一个启用的带单用户。`data/trade.json` 不要提交到 git。

先测登录（不会下单）：

```bash
python -c "from range_mr.trade import TradeBot; b=TradeBot.from_config(); print(b.connect() if b else '未开启')"
```

应打印带单账户 id。然后再跑 live。

### 历史回放

默认回放 **BTCUSDT + ETHUSDT**，每注本金 250。会拉币安 30m / 1m（写入 `data/` 缓存，再次回放可复用）。

```bash
python main.py replay --days 7 --out output/signals.csv
python main.py replay --days 730 --out output/signals_2y.csv
python main.py replay --start 2024-09-08 --end 2026-09-08 --symbol ETHUSDT
```

- `--days`：最近 N 天（默认 7；两年用 `730`）
- `--start` / `--end`：本地时区日期，`YYYY-MM-DD`
- `--stake`：每注本金（默认 250）
- `--symbol`：只回放一个标的；不填则 BTC+ETH
- `--print-signals`：逐条打印信号和冲突
- 输出：`output/signals.csv` 和同名 `_stats.txt`

两年 1m K 线首次拉取大约需要 10–20 分钟。

### 实时检测（前台）

同时盯 BTCUSDT + ETHUSDT。出信号后推 Telegram，并按配置自动在跟单面板下单。

```bash
python main.py live
```

Windows 也可用：`deploy/start_live.ps1`。

服务器请用下面的 PM2，不要长期挂前台。

## 服务器部署（Ubuntu + PM2）

默认路径 `/opt/huice`，进程名 `range-mr`。

### 1. 安装依赖并拉代码

```bash
apt update
apt install -y python3 python3-venv python3-pip git

mkdir -p /opt/huice
git clone -b xinhao https://github.com/louyiwei38-blip/huice.git /opt/huice
cd /opt/huice
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 配置 Telegram 并试推

```bash
cp data/telegram.json.example data/telegram.json
nano data/telegram.json
python -c "from range_mr.config import Config; from range_mr.telegram_notify import load_telegram, send_telegram; t,c=load_telegram(Config()); send_telegram(t,c,'服务器部署成功')"
```

配置自动下单（面板账号不要进 git）：

```bash
cp data/trade.json.example data/trade.json
nano data/trade.json
python -c "from range_mr.trade import TradeBot; b=TradeBot.from_config(); print(b.connect() if b else '未开启')"
```

### 3. 安装 PM2

```bash
curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
apt install -y nodejs
npm install -g pm2
```

已安装可跳过：`node -v`、`pm2 -v`

### 4. 停掉旧进程（避免双开）

```bash
ps aux | grep -E 'run_server.py|main.py|range-mr' | grep -v grep
kill <旧进程PID>

# 若之前用 systemd
systemctl stop range-mr
systemctl disable range-mr
```

确认没有输出后再启动 PM2。

### 5. 启动并开机自启

```bash
cd /opt/huice
mkdir -p logs
pm2 start ecosystem.config.js
pm2 save
pm2 startup
```

`pm2 startup` 会打印一条 `sudo env PATH=...` 命令，复制执行一次。

PM2 跑的是 `.venv` 里的 Python + `run_server.py`。`run_server.py` 内部也会在异常退出后 5 秒重启；进程彻底挂掉则由 PM2 拉起。

### 6. 确认

```bash
pm2 status
pm2 logs range-mr --lines 50
tail -f /opt/huice/logs/live.log
```

`range-mr` 为 `online`，Telegram 会收到「Range MR V1.1 已启动」。

## 日常操作

```bash
pm2 status                 # 状态
pm2 logs range-mr          # PM2 日志
pm2 restart range-mr       # 重启
pm2 stop range-mr          # 停止
pm2 delete range-mr        # 从列表删除
```

更新代码：

```bash
cd /opt/huice
git pull origin xinhao
pm2 restart range-mr
```

日志：

- 业务：`logs/live.log`（启动 / 异常）
- PM2：`logs/pm2-out.log`、`logs/pm2-error.log`
- 实时信号也会打到 stdout，可用 `pm2 logs` 看

## 主要参数

改 `range_mr/config.py` 后需要 `pm2 restart range-mr`。

| 参数 | 默认 | 含义 |
| --- | --- | --- |
| `symbols` | BTCUSDT, ETHUSDT | 实时盯的标的 |
| `symbol` | ETHUSDT | 单标的回放 / 结构计算用的当前标的 |
| `lookback` | 48 | 箱体回看 30m 根数 |
| `volume_ratio` | 0.75 | 近端量 / 中位量，低于此视为盘整（仅标记，默认不拦截信号） |
| `require_ranging` | False | 为 True 时 BOX_EDGE/SWING 只在盘整中发 |
| `edge_frac` | 0.20 | 箱体上下沿带宽（箱体高度的 20%） |
| `edge_wide_pct` | None | 带宽/价格过宽阈值；None 为关闭 |
| `max_edge_pct` | None | 过宽时带宽上限（占价格）；需与 edge_wide_pct 一起开 |
| `enabled_logics` | BOX_EDGE, SWING, SR_FLIP | 启用的逻辑 |
| `cooldown_bars` | 4 | 同逻辑同方向冷却（30m 根数） |
| `max_er` | 0.35 | 盘整信号 ER 上限 |
| `skip_chase_atr` | 0.25 | 追价过滤 |
| `flip_bars` | 8 | 突破回踩窗口（30m 根数） |
| `live_poll_sec` | 3 | 实时轮询间隔（秒） |
| `payout_rate` | 0.85 | 赢的支付率 |
| `stake` | 250 | 回放结算本金（赢 +212.5 / 输 -250） |
| `trade_amount` | 50 | 实盘自动下单金额（USDT） |
| `trade_period` | THIRTY_MINUTE | 实盘固定 30 分钟周期 |

## 回放参数扫描

`tune_params.py` 依赖 `data/btcusdt_30m.csv` 和 `data/btcusdt_1m.csv`，仓库不带这两份文件。有缓存后再跑：

```bash
python tune_params.py
```
