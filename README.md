# FHIR-bioMedData — 醫療數據自動化清洗與 FHIR 對齊系統

Heterogeneous Clinical Data Cleansing & HL7 FHIR Alignment Pipeline

以「CLI 驅動的自動化清洗引擎」為核心，將雜亂異構的院所原始資料（JSON/CSV）
依可配置的欄位對齊規則正規化，轉換為 HL7 FHIR **Patient** 資源，
經 schema 驗證後以 **Upsert** 語意寫入（模擬的）中央資料庫，
達成「一次配置、多院所適用」的自動化落庫流程。

> 本專案使用合成測試資料，不含任何真實 PHI。

## 系統架構

```
原始資料來源                    CLI 自動化清洗引擎              Mock FHIR Server
─────────────────            ─────────────────────          ──────────────────
raw_hospital_A.json    →     main_ingest.py           →     routes.py
(M/F, 1990-01-02)            ├ config.py 欄位對齊指針        POST /Patient
raw_hospital_B.csv           └ fhir_patient_schema.json     Upsert by identifier
(男/女, 79/01/02)                                            記憶體病患資料庫
                                                             { identifier: Patient }
```

`config.py` 是唯一需要因「新院所加入」而修改的檔案——新增一組欄位對齊規則即可，
核心引擎與 Server 邏輯零異動。

## 清洗流程

1. **讀取原始檔案** — CLI 依 `--hospital` 參數載入對應 JSON/CSV，套用該院所的欄位對照表
2. **欄位正規化** — 性別字典轉換（M/F、Male/Female、男/女 → male/female）、
   生日格式統一（民國年 +1911 轉西元、補零、ISO 8601）、身分證字號格式與檢查碼驗證
3. **FHIR Mapping** — 映射為 Patient 資源（name / gender / birthDate / identifier / managingOrganization）
4. **Schema 驗證** — 以 `fhir_patient_schema.json` 驗證必填欄位與型別；
   不合格資料寫入複核佇列（`logs/rejected.jsonl`）並跳過，不中斷整批流程
5. **Upsert 落庫** — 依 identifier 判斷資料庫中是否已存在：存在則更新、不存在則新增

## 快速開始

核心管線僅使用 Python 標準函式庫（Python 3.10+），免安裝任何套件即可執行：

```bash
# 一鍵完整示範：兩家院所依序清洗並累積落庫
python run_demo.py

# 三種執行模式，對應「先驗證、再試跑、後落庫」的安全操作習慣
python main_ingest.py --hospital A --input bio_data_pool/raw_hospital_A.json --validate-only
python main_ingest.py --hospital A --input bio_data_pool/raw_hospital_A.json --dry-run
python main_ingest.py --hospital B --input bio_data_pool/raw_hospital_B.csv

# PII 去識別化：身分證字號以 SHA-256 雜湊後儲存
python main_ingest.py --hospital A --input bio_data_pool/raw_hospital_A.json --hash-id
```

### 搭配 HTTP Mock FHIR Server（選用）

單獨執行 CLI 時使用行程內記憶體資料庫；若要讓多次 CLI 執行共用同一個資料庫
（跨院所展示 Upsert 更新），可起一個 HTTP Mock Server：

```bash
pip install -r requirements.txt          # fastapi + uvicorn
uvicorn server_mock_fhir.routes:app --port 8000

# 另開終端機，指定 --server-url 即改走 HTTP 落庫
python main_ingest.py --hospital A --input bio_data_pool/raw_hospital_A.json --server-url http://localhost:8000
python main_ingest.py --hospital B --input bio_data_pool/raw_hospital_B.csv  --server-url http://localhost:8000
curl http://localhost:8000/Patient        # 查看累積落庫結果
```

## 測試

```bash
pip install pytest
pytest -v
```

測資刻意涵蓋邊界案例：缺少必填欄位、身分證字號檢查碼錯誤、
民國年未補零、跨院所重複病患（觸發 Upsert 更新路徑）。

## 專案目錄結構

```
FHIR-bioMedData/
├── config.py                     # 矩陣配置：院所欄位對齊指針
├── main_ingest.py                # 核心自動化引擎（CLI 入口）
├── run_demo.py                   # 一鍵完整示範
├── bio_data_pool/
│   ├── raw_hospital_A.json       # 醫院 A 原始測資（西元生日、M/F）
│   ├── raw_hospital_B.csv        # 醫院 B 原始測資（民國年生日、男/女）
│   └── fhir_patient_schema.json  # HL7 FHIR Patient 標準規範（子集）
├── server_mock_fhir/
│   ├── __init__.py
│   └── routes.py                 # Mock FHIR Server：Upsert 邏輯
└── tests/
    ├── test_normalize.py         # 正規化單元測試
    └── test_pipeline.py          # 端到端管線測試
```

## FHIR Patient Mapping 對照表

| 原始欄位（範例值） | FHIR Path | 轉換規則 |
|---|---|---|
| name（王小明） | `Patient.name[0].text` | 直接對應 |
| gender（M/F、男/女） | `Patient.gender` | 依 config.py 院所字典轉為 male / female / other / unknown |
| 身分證字號（A123456789） | `Patient.identifier[].value` | regex + 檢查碼驗證；Upsert 唯一鍵；可選 SHA-256 雜湊去識別化 |
| 出生日期（1990-01-02 / 79/01/02） | `Patient.birthDate` | 民國年 +1911、統一分隔符，輸出 ISO 8601 |
| 就診院所代碼（A / B） | `Patient.managingOrganization.reference` | 查 config.py 院所代碼表，轉為 `Organization/{id}` 參照 |

## 風險、限制與後續規劃

- **資安與去識別化**：MVP 使用合成測資；若接觸真實 PHI，須啟用 `--hash-id`
  雜湊並加上存取控制與稽核日誌
- **欄位覆蓋度**：目前僅示範 Patient 單一資源；Observation（檢驗值）、
  Encounter（就診紀錄）為既定擴充路徑
- **Mock Server 的侷限**：記憶體儲存僅供 Demo；正式環境應替換為持久化資料庫
  或真實 FHIR Server（如 HAPI FHIR），Upsert 邏輯可原樣沿用
