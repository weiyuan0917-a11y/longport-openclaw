import urllib.request
import json

def test_api(url):
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, json.loads(response.read().decode())
    except Exception as e:
        return -1, str(e)

print("Testing /setup/services/status:")
status, data = test_api("http://localhost:8000/setup/services/status")
print(f"Status: {status}")
print(f"Data: {data}")
