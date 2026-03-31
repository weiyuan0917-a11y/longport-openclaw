import urllib.request
import json
import sys

def post_api(url, data=None):
    try:
        req = urllib.request.Request(url, method="POST")
        req.add_header('Content-Type', 'application/json')
        if data:
            req.data = json.dumps(data).encode()
        with urllib.request.urlopen(req, timeout=60) as response:
            return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        try:
            error_json = json.loads(error_body)
            return e.code, error_json
        except:
            return e.code, error_body
    except Exception as e:
        return -1, str(e)

print("测试手动扫描...")
status, data = post_api("http://localhost:8000/auto-trader/scan/run", {})
print(f"Status: {status}")
print(f"Response: {json.dumps(data, indent=2, ensure_ascii=False)}")
