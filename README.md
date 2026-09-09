# RSI_BB 事件合约

`huice` 仓库的 **`rsibb` 分支**。标的仅 BTCUSDT / ETHUSDT。5m 收盘出信号，持仓 30 分钟，结算用 T+30min 那根 1m 收盘价。

赢 **+0.85** / 输 **−1.0** / 平 **0**。正期望胜率门槛 ≈ 54.05%。

实盘：信号推 Telegram，并在 [跟单面板](http://194.233.90.109:3000) 自动下单（默认 50 USDT）。

## 冻结规则（F 组，禁止再改）

只在 **IS** 上用多种方法对照后冻结；**OOS 不得用于改参**。

- 15m **RSI(7)**：≤20 做多，≥80 做空
- 同时 15m 收盘触及布林轨（中轨 SMA20，**k=2.2**）
- 入场只在 5m 收盘；**不跳过资金费窗口**（事件合约无资金费）
- 同标的最多 1 笔，间隔 ≥ 30 分钟

```
做多：RSI(7) ≤ 20 且 close ≤ 下轨
做空：RSI(7) ≥ 80 且 close ≥ 上轨
```

## 回放结果

窗口 UTC。有效成交（非作废）口径。

| 区间 | N | 胜率 | EV | 日均信号 |
|---|---:|---:|---:|---:|
| IS 2023-01-01 ~ 2024-12-31 | 4184 | 59.44% | +0.100 | 5.72 |
| OOS 2025-01-01 ~ 2026-08-31 | 3383 | 57.76% | +0.068 | 5.56 |
| 全样本 ~2026-08-31 | 7567 | 58.69% | +0.086 | **5.65** |
| 过去两年 2024-09 ~ 2026-08 | 4044 | 57.96% | +0.072 | 5.54 |

两币大约各一半。几乎每天都有单。默认 50U 时日均名义仓位约 280U。

检测对齐：`python detect.py --align` 必须打出 IS **N=4184、胜率 59.44%**。

## 安装

Python 3.9+（推荐 3.11）。Ubuntu 上没有 `python` 命令，先用 `python3` 建虚拟环境，激活后再用 `python`。

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -U pip
pip install -r requirements.txt
```

```bash
python -m pytest tests/test_rfa30.py tests/test_live.py tests/test_copybot.py -q
```

## 回放（研究用）

需要本地 1m 数据：`data/<SYMBOL>/1m/YYYY-MM.parquet`。

```bash
python download.py                 # 断点续传月度 1m
python detect.py --align           # 对齐冻结 IS
python detect.py                   # 写出 output/detect_signals.csv
python monthly.py                  # 过去两年逐月 → output/MONTHLY.md
python backtest.py                 # 全策略回放（含对照）
```

不要再跑 `search_rsi_bb.py` 改阈值。RFA-30 原规则未改。

## 实盘

`live.py` 用币安 REST 拉最近已收盘 1m，**不依赖 parquet**。复制 `.env.example` 为 `.env`（不要提交 git）。

```
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

COPYBOT_URL=http://194.233.90.109:3000
COPYBOT_USERNAME=
COPYBOT_PASSWORD=
COPYBOT_ORDER_AMOUNT=50
# COPYBOT_LEADER_ID=
# COPYBOT_ENABLED=1
```

Telegram：[@BotFather](https://t.me/BotFather) 建 bot；给你的 bot 发 `/start`，再打开  
`https://api.telegram.org/bot<token>/getUpdates` 取 `chat.id`。

```bash
python live.py --test              # 向 TG 推一条测试
python live.py --test-copybot      # 登录面板，确认带单账户，不下单
python live.py --once --dry-run    # 扫一轮，只打印
python live.py --once              # 扫一轮
python live.py --loop              # 每根 5m 收盘：推送 + 下单
python live.py --stats             # 推送实盘账本
```

有新信号时下单：标的 BTCUSDT/ETHUSDT，方向看涨/看跌，周期 **30 分钟**，赔付 **0.85**，金额默认 **50**。同一笔成功后不会重复下单。首次启动会把回看里的旧成交记入账本去重，**不刷屏、不计入实盘胜率**。

账本：`output/live_ledger.json`，已结算：`output/live_pnl.csv`。

## 服务器 PM2

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs
sudo npm i -g pm2

git clone -b rsibb https://github.com/louyiwei38-blip/huice.git
cd huice
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
cp .env.example .env
nano .env

python live.py --test-copybot
python live.py --test

mkdir -p logs
pm2 start ecosystem.config.js
pm2 save
pm2 startup
```

```bash
pm2 status
pm2 logs rsibb
pm2 restart rsibb

cd ~/huice
git pull origin rsibb
source .venv/bin/activate
pip install -r requirements.txt
pm2 restart rsibb
```

进程名 `rsibb`，可与 `main` 分支的 `btc-signal` 同时跑。
