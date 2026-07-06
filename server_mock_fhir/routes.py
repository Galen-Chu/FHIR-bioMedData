"""routes.py — Mock FHIR Server：記憶體內 Upsert 邏輯

核心的 upsert() 為純函式，CLI 可直接 import 在行程內落庫（免起 server）；
若已安裝 FastAPI，亦可用 uvicorn 起一個 HTTP Mock Server：

    uvicorn server_mock_fhir.routes:app --port 8000

記憶體儲存僅供 Demo；正式環境應替換為持久化資料庫或真實 FHIR Server
（如 HAPI FHIR），Upsert 邏輯可原樣沿用。
"""

# in-memory：identifier -> Patient resource
PATIENT_DB: dict[str, dict] = {}


def upsert(patient: dict) -> dict:
    """依 identifier 判斷新增或更新（Upsert 語意）。"""
    key = patient["identifier"][0]["value"]
    outcome = "updated" if key in PATIENT_DB else "created"
    PATIENT_DB[key] = patient
    return {"outcome": outcome, "id": key}


def reset() -> None:
    """清空記憶體資料庫（測試用）。"""
    PATIENT_DB.clear()


# ---- FastAPI HTTP 介面（選用；未安裝 fastapi 時核心管線仍可運作） ----
try:
    from fastapi import FastAPI, HTTPException

    app = FastAPI(title="Mock FHIR Server", version="1.0")

    @app.post("/Patient")
    def upsert_patient(patient: dict):
        if patient.get("resourceType") != "Patient" or not patient.get("identifier"):
            raise HTTPException(status_code=422, detail="not a valid Patient resource")
        return upsert(patient)

    @app.get("/Patient")
    def list_patients():
        return {"total": len(PATIENT_DB), "entry": list(PATIENT_DB.values())}

    @app.get("/Patient/{identifier}")
    def get_patient(identifier: str):
        if identifier not in PATIENT_DB:
            raise HTTPException(status_code=404, detail="Patient not found")
        return PATIENT_DB[identifier]

except ImportError:  # pragma: no cover
    app = None
