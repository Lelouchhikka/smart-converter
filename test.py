import requests
try:
    response = requests.get("http://localhost:9997/v3/config/paths/get/test")
    print(response.json())
except Exception as e:
    print(f"Ошибка при запросе к MediaMTX API: {e}")
