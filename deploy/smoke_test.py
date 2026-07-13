"""End-to-end smoke test for the deployed demo journey."""
import json
import urllib.request

BASE = "http://127.0.0.1:3100/api/v1"
token = ""


def call(path: str, method: str = "GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(BASE + path, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)["data"]


login = call("/auth/login", "POST", {"facility": "demo", "username": "doctor", "password": "Doctor123!"})
token = login["access_token"]
assert call("/me")["role"] == "doctor"
patient = call("/patients")[0]
template = call("/templates")[0]
visit = call("/visits", "POST", {"patient_id": patient["id"], "template_id": template["id"]})
call(f"/visits/{visit['id']}/recording/start", "POST")
call(f"/visits/{visit['id']}/recording/stop", "POST")
summary = call(f"/visits/{visit['id']}/summary")
for section in summary["sections"]:
    for guidance in section["guidance"]:
        call(f"/guidance-items/{guidance['id']}", "PATCH", {"status": "accepted"})
approval = call(f"/visits/{visit['id']}/approve", "POST")
assert approval["status"] == "confirmed"
assert call(f"/visits/{visit['id']}/upload-status")["status"] == "confirmed"
print("SMOKE_OK", visit["id"], approval["upload"]["bundle_id"])
