import urllib.request
import json

def post_api(url, data=None):
    try:
        req = urllib.request.Request(url, method="POST")
        req.add_header('Content-Type', 'application/json')
        if data:
            req.data = json.dumps(data).encode()
        with urllib.request.urlopen(req, timeout=60) as response:
            return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except Exception as e:
        return -1, str(e)

print("测试手动扫描...")
status, data = post_api("http://localhost:8000/auto-trader/scan/run", {})
print(f"Status: {status}")
if status == 200:
    print(f"成功! 扫描到 {data.get('strong_count', 0)} 只强势股")
    print(f"Created signals: {data.get('created_signals', 0)}")
else:
    print(f"错误: {data}")
