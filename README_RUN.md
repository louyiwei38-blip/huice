# RFA-30 一键复现

本机若只有 3.9，可用 `python3 -m venv .venv && source .venv/bin/activate`（代码已用 `from __future__ import annotations`，3.9+ 可跑）。推荐 3.11+。

## 安装

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## 一键下载 + 回放

```bash
python download.py && python search_rsi_bb.py && python backtest.py
```

- 下载按月写入 `data/<SYMBOL>/1m/YYYY-MM.parquet`，已存在且完整的月份会跳过（断点续传）。
- `search_rsi_bb.py` 只在 IS 上网格搜索 RSI+布林阈值，写入 `output/rsi_bb_winner.json`。
- 回放默认区间：2023-01-01 UTC 至 2026-09-01 UTC（多取约 2 小时 1m 供最后一笔结算）。
- 产出在 `output/`：`trades.csv`、`metrics.json`、`equity_curve.csv`、`REPORT.md`、`signals.csv`（每根 5m）。

## 逐月回放（过去两年）

```bash
python monthly.py    # 2024-09 ~ 2026-08，写出 output/MONTHLY.md
```

```bash
python detect.py --align     # 必须打出 IS：N=3704、胜率 59.69%
python detect.py             # 写出 output/detect_signals.csv
python detect.py --latest    # 最近一根 5m 是否触发
python detect.py --is-only   # 只导出 IS 区间信号
```

检测与 `backtest.py` 共用 `rsi_bb.py` 的 `scan_rsi_bb`，参数冻结为 RSI(7) 20/80、布林 k=2.2。

## Telegram 信号 + 盈亏账本

规则与回放相同（RSI(7) 20/80、k=2.2）。只推**新开仓**和 **T+30m 结算**；首次启动会把回看窗口里的旧成交记入账本，不刷屏。

1. 找 [@BotFather](https://t.me/BotFather) 建 bot，拿到 token。
2. 给你的 bot 发一条消息，再打开 `https://api.telegram.org/bot<token>/getUpdates` 读 `chat.id`（群需把 bot 拉进群）。
3. 配置环境：

```bash
cp .env.example .env
# 编辑 .env：TELEGRAM_BOT_TOKEN、TELEGRAM_CHAT_ID
```

```bash
python live.py --test            # 测 Telegram 连通
python live.py --test-copybot    # 登录跟单面板，确认带单账户，不下单
python live.py --once --dry-run  # 扫一轮，只打印不发送/不下单
python live.py --once            # 扫一轮（可挂 cron）
python live.py --loop            # 每根 5m 收盘后推送并下单（长期跑）
python live.py --stats           # 推送当前账本：N / 胜率 / EV / 今日 / 本月
```

账本：`output/live_ledger.json`，已结算明细：`output/live_pnl.csv`。赢 +0.85 / 输 −1.0 / 平 0，与回放一致。统计只计入启动后真正盯盘的成交；回看窗口里的旧单只用于去重，不算进胜率。

## 跟单面板自动下单

有新信号时，按标的 + 方向调用面板 [带单下单](http://194.233.90.109:3000) 的 `/api/orders/place`（30 分钟、赔付 0.85）。默认金额 **50 USDT**。

在 `.env` 增加登录账号（与网页登录相同）：

```
COPYBOT_URL=http://194.233.90.109:3000
COPYBOT_USERNAME=你的用户名
COPYBOT_PASSWORD=你的密码
COPYBOT_ORDER_AMOUNT=50
```

管理员若有多个带单账户，可设 `COPYBOT_LEADER_ID`。临时关闭下单：`COPYBOT_ENABLED=0`。

```bash
python -m pytest tests/test_rfa30.py tests/test_live.py tests/test_copybot.py -q
```

## 口径（固定，禁止改参）

- 标的仅 BTCUSDT、ETHUSDT。
- 入场只在 5m 收盘；结算为 T+30min 那根 1m 的 close（缺 K 作废）。
- 赢 +0.85 / 输 -1.0 / 平 0；不再扣手续费滑点。
- IS：2023-01-01 ~ 2024-12-31；OOS：2025-01-01 ~ 2026-09-01。OOS 不用于改规则。

对照：`RSI_BB`（IS 冻结：RSI(7) 20/80 + 布林 k=2.2）/ `BASE_LONG` / `BASE_MOM` / `BASE_1H`。RFA-30 原规则未改。OOS 不用于改 RSI_BB 阈值。

## 服务器 PM2 部署（rsibb 分支）

实盘不依赖本地 parquet，`live.py --loop` 用币安 REST 拉最近 K 线。`.env` 不要提交。

```bash
# 1. 机器依赖
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git
# Node 仅用于 PM2
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs
sudo npm i -g pm2

# 2. 拉 rsibb 分支
git clone -b rsibb https://github.com/louyiwei38-blip/huice.git
cd huice

# 3. Python 环境
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt

# 4. 密钥（与面板登录、Telegram 相同）
cp .env.example .env
nano .env
# 必填：COPYBOT_USERNAME / COPYBOT_PASSWORD
# 可选：TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID
# COPYBOT_ORDER_AMOUNT=50

# 5. 先测登录（不下单）
python live.py --test-copybot

# 6. PM2 拉起
mkdir -p logs
pm2 start ecosystem.config.js
pm2 save
pm2 startup    # 按提示把开机命令执行一次
```

常用：

```bash
pm2 status
pm2 logs rsibb
pm2 restart rsibb

# 更新代码
cd huice
git pull origin rsibb
source .venv/bin/activate
pip install -r requirements.txt
pm2 restart rsibb
```
