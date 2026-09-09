说明见 [README.md](README.md)。

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python detect.py --align    # IS：N=4184、胜率 59.44%
python live.py --loop
```
