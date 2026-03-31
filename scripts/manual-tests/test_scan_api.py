import urllib.request
import json

def post_api(url, data=None):
    try:
        req = urllib.request.Request(url, method="POST")
        req.add_header('Content-Type', 'application/json')
        if data:
            req.data = json.dumps(data).encode()
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.status, json.loads(response.read().decode())
    except Exception as e:
        return -1, str(e)

def get_api(url):
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, json.loads(response.read().decode())
    except Exception as e:
        return -1, str(e)

print("执行手动扫描...")
status, data = post_api("http://localhost:8000/auto-trader/scan/run", {})
print(f"Status: {status}")
print(f"Response: {json.dumps(data, indent=2)}")

print("\n再次获取状态...")
status, data = get_api("http://localhost:8000/auto-trader/status")
print(f"Status: {status}")
if status == 200:
    print(f"last_scan_summary: {json.dumps(data.get('last_scan_summary'), indent=2)}")
