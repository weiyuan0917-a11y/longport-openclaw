import requests
import json

try:
    r = requests.get('http://localhost:8000/setup/config')
    print(f"Status: {r.status_code}")
    print(f"Response: {json.dumps(r.json(), indent=2)}")
except Exception as e:
    print(f"Error: {e}")
