import urllib.request
import json

def test_api(url):
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, json.loads(response.read().decode())
    except Exception as e:
        return -1, str(e)

print("Testing /auto-trader/status:")
status, data = test_api("http://localhost:8000/auto-trader/status")
print(f"Status: {status}")
if status == 200:
    print(f"Keys: {list(data.keys())}")
    print(f"last_scan_summary: {data.get('last_scan_summary')}")
else:
    print(f"Error: {data}")
