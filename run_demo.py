"""run_demo.py — 一鍵完整示範：兩家院所依序清洗並累積落庫（行程內）。

    python run_demo.py

醫院 B 有 2 筆病患與醫院 A 重複（同身分證字號），會觸發 Upsert 的
「更新」路徑，展示依 identifier 判斷新增或更新的語意。
"""

from main_ingest import run_ingest
from server_mock_fhir.routes import PATIENT_DB, reset

if __name__ == "__main__":
    reset()
    jobs = [
        ("A", "bio_data_pool/raw_hospital_A.json"),
        ("B", "bio_data_pool/raw_hospital_B.csv"),
    ]
    for hospital, input_path in jobs:
        print(f"$ python main_ingest.py --hospital {hospital} --input {input_path}")
        run_ingest(hospital=hospital, input_path=input_path)
        print()
    print(f"[DONE] 總計 {len(jobs)} 家院所 · 累積落庫 {len(PATIENT_DB)} 筆 Patient 資源")
