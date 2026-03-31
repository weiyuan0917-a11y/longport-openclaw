import urllib.request
import json

def test_api(url):
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, json.loads(response.read().decode())
    except Exception as e:
        return -1, str(e)

print("Testing /risk/config:")
status, data = test_api("http://localhost:8000/risk/config")
print(f"Status: {status}")
print(f"Data: {data}")

print("\nTesting /setup/config:")
status, data = test_api("http://localhost:8000/setup/config")
print(f"Status: {status}")
print(f"Data: {data}")
