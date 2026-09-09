回放、实盘、PM2 说明已写在 [README.md](README.md)。本文件保留以免旧命令失效。

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python detect.py --align    # IS：N=4184、胜率 59.44%
python live.py --loop
```
