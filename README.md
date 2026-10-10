# RSI_BB 事件合约

仓库 [`huice`](https://github.com/louyiwei38-blip/huice) 的 **`rsibb` 分支**。标的仅 **BTCUSDT / ETHUSDT**。

5m 收盘判定开仓，持仓 **30 分钟**，结算价为 **T+30min 那根 1m 的 close**。赢 **+0.85** / 输 **−1.0** / 平 **0**（相对 1 单位本金）。正期望胜率门槛 ≈ **54.05%**（`1/1.85`）。

实盘：每根 5m 收盘扫信号 → 推 [Telegram](https://telegram.org/) → POST [跟单 webhook](http://8.210.132.210:3000/api/webhook/copy-signal) 下单。金额由 `.env` 的 `COPYBOT_ORDER_AMOUNT` 决定（示例默认 100U；服务器上可改成 20U 等）。

进程名 **`rsibb`**（PM2）。可与同机其它进程（如 `bec-bot`）同时跑。

## 冻结规则（禁止再改阈值）

只在 **IS** 上对照后冻结；**OOS 不得用于改 RSI/布林参数**。RSI 与布林必须同时满足。

```
做多：15m RSI(7) ≤ 20 且 15m close ≤ 布林下轨
做空：15m RSI(7) ≥ 80 且 15m close ≥ 布林上轨
布林：SMA(20) ± 2.2σ（ddof=0）
```

- 入场只在 **5m 收盘**；入场价 = 该 5m close
- **不跳过资金费窗口**（事件合约无资金费）
- **同标的最多 1 笔**，开仓间隔 ≥ 30 分钟；两币独立，故同时最多 **2 仓**（BTC + ETH）
- 指标 NaN（预热不足）或同一根多空都成立 → 空仓
- 缺 T+30 的 1m：结算时刻未到算持仓中；已过结算时刻仍缺 K 才作废

### 实盘额外过滤（冻结回放对齐时不加）

- **周一至周五跳过北京 20:00–23:00**（UTC 12:00–15:00）不开仓；**周六日该时段不跳过**  
  `scan_rsi_bb` / `live.py` 默认开启；`python detect.py --align` 校验 IS 时 **不叠加** 此时段过滤
- 正在走的 1m 不用；5m 收盘后再等 **20 秒**（等币安 K 线落库）
- 入场/结算超过 **12 分钟** 只记账，不推 Telegram、不下单
- 同一 `(标的, 入场时间)` 成功下过不再下
- `void`、`COPYBOT_ENABLED=0`、或没有 `COPYBOT_WEBHOOK_TOKEN` → 不下单

Telegram 账本里的金额 = 单位盈亏 × 该笔 `orderAmount`（没有回执则用环境默认金额）。

## 回放结果

窗口 **UTC**。有效成交（非作废）。资金费窗口已关。数据截止 **2026-08-31**。

### 冻结口径（不含北京 20–23 过滤）

`python detect.py --align` 必须打出 IS **N=4184、胜率 59.44%**。

| 区间 | N | 胜率 | EV | 日均信号 |
|---|---:|---:|---:|---:|
| IS 2023-01-01 ~ 2024-12-31 | 4184 | 59.44% | +0.100 | 5.72 |
| OOS 2025-01-01 ~ 2026-08-31 | 3383 | 57.76% | +0.068 | 5.56 |
| 全样本 ~2026-08-31 | 7567 | 58.69% | +0.086 | 5.65 |
| 过去两年 2024-09 ~ 2026-08 | 4044 | 57.96% | +0.072 | 5.54 |

### 当前实盘口径（周一至周五跳过北京 20:00–23:00；周末不跳）

| 区间 | N | 胜率 | EV | 累计（1 单位） | 日均信号 |
|---|---:|---:|---:|---:|---:|
| IS | 3494 | 60.22% | +0.115 | +400.40 | 4.78 |
| OOS | 2652 | 58.90% | +0.090 | +237.70 | 4.37 |
| 全样本 | 6146 | 59.65% | +0.104 | +638.10 | 4.59 |
| 过去两年 | 3169 | 59.36% | +0.098 | +310.85 | 4.34 |

全样本实盘口径：BTC N=2958 胜率 58.99%；ETH N=3188 胜率 60.26%。按 100U/笔，过去两年约 +31,085U（历史回放，不是实盘保证）。

## 安装

Python **3.9+**（推荐 3.11）。Ubuntu 没有 `python`：用 `python3` 建 venv，**激活后再用 `python`**。

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -U pip
pip install -r requirements.txt
python -m pytest tests/test_rfa30.py tests/test_live.py tests/test_copybot.py -q
```

## 回放（研究用）

需要 `data/<SYMBOL>/1m/YYYY-MM.parquet`。不要再跑 `search_rsi_bb.py` 改阈值。

```bash
python download.py                 # 断点续传月度 1m
python detect.py --align           # 对齐冻结 IS（不跳过北京 20–23）
python detect.py                   # 日常扫描（默认周一至周五跳过北京 20–23）→ output/detect_signals.csv
python monthly.py                  # 过去两年逐月 → output/MONTHLY.md
python backtest.py                 # 全策略回放（含对照；RFA-30 未改）
```

## 实盘

`live.py --loop` 用币安 REST 拉最近已收盘 1m，**不依赖 parquet**。首次回看 **7 天**，之后 **36 小时**。复制 `.env.example` 为 `.env`（**不要提交 git**）。

```
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

COPYBOT_URL=http://8.210.132.210:3000
COPYBOT_WEBHOOK_TOKEN=
COPYBOT_ORDER_AMOUNT=100
# COPYBOT_ENABLED=1
```

- Telegram：[@BotFather](https://t.me/BotFather) 建 bot → 给 bot 发 `/start` → `https://api.telegram.org/bot<token>/getUpdates` 取 `chat.id`
- 下单：`POST {COPYBOT_URL}/api/webhook/copy-signal`  
  Header：`Authorization: Bearer <COPYBOT_WEBHOOK_TOKEN>`  
  Body：`{"symbolName":"BTCUSDT","direction":"LONG","timeIncrements":"THIRTY_MINUTE","orderAmount":"100"}`  
  `direction` 由信号 bias 决定（多 LONG / 空 SHORT）；`orderAmount` 来自环境变量
- 面板 [http://8.210.132.210:3000](http://8.210.132.210:3000) 必须有 **已启用、凭证有效的带单/跟单账户**（如 `xw`）去执行。Webhook 返回 `success` 但带 `无已启用的跟单节点` / `本机未下单` 时，只记了信号、**账户不会开仓**

```bash
python live.py --test              # 向 TG 推一条测试
python live.py --test-copybot      # 检查 webhook URL / token / 金额（不下单）
python live.py --once --dry-run    # 扫一轮，只打印
python live.py --once              # 扫一轮（会真下单）
python live.py --loop              # 每根 5m 收盘：推送 + 下单
python live.py --stats             # 推送实盘账本
```

手工测一单（真下，与策略无关）：

```bash
curl -X POST 'http://8.210.132.210:3000/api/webhook/copy-signal' \
  -H 'Authorization: Bearer <COPYBOT_WEBHOOK_TOKEN>' \
  -H 'Content-Type: application/json' \
  -d '{"symbolName":"BTCUSDT","direction":"LONG","timeIncrements":"THIRTY_MINUTE","orderAmount":"20"}'
```

成功应无 `"skipped": true`，并出现订单号。

首次启动会把回看里的旧成交记入账本去重（`seed`），**不刷屏、不计入实盘胜率**。账本：`output/live_ledger.json`，已结算：`output/live_pnl.csv`。

日志带 UTC，例如：`2026-10-09 13:44:16 UTC RSI_BB 实盘循环  RSI(7) 20/80 k=2.2  跳过周一至周五北京 20:00–23:00（UTC 12:00–15:00；周末不跳过）`。

## 服务器 PM2

Ubuntu 没有 `python`，一律 `python3` 建环境，之后用 `.venv`。

### 新机器

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
mkdir -p logs
cp .env.example .env
nano .env                          # 填 Telegram + COPYBOT_WEBHOOK_TOKEN + 金额

python live.py --test-copybot      # 应 enabled=True、ping=200
python live.py --test

pm2 start ecosystem.config.js      # 进程名 rsibb：.venv/bin/python -u live.py --loop
pm2 save
pm2 startup
```

`git log -1 --oneline` 必须在 **`/root/huice`**（或项目目录）里跑，家目录会报 not a git repository。

### 已有目录（更新）

```bash
cd /root/huice
git fetch origin
git checkout rsibb
git pull origin rsibb
source .venv/bin/activate
pip install -r requirements.txt
pm2 restart rsibb --update-env     # 改过 .env 必须加 --update-env
```

已有 `rsibb` 进程时不要再 `pm2 start`，用 `restart`。

### 日常

```bash
pm2 status
pm2 logs rsibb --lines 30
tail -f /root/huice/logs/rsibb-out.log
pm2 save
```

正常日志：扫 BTCUSDT / ETHUSDT，无信号时 `本轮无新开仓/结算`，然后 `sleep …s → 下一根 5m`。
