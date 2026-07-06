"""config.py — 院所欄位對齊指針（矩陣配置）

新院所加入時，只需在 HOSPITAL_FIELD_MAP 新增一組規則；
核心引擎（main_ingest.py）與 Mock Server 邏輯零異動。

每組規則的欄位說明：
    format       原始檔格式（json / csv），CLI 據此選擇讀取器
    field_map    原始欄位名 -> 標準欄位名（identifier / gender / birthDate / name）
    gender_dict  原始性別值 -> FHIR 標準值（male / female / other / unknown）
    date_format  生日格式；以 "roc/" 開頭代表民國年，需 +1911 轉西元
    org_code     院所代碼，映射為 Patient.managingOrganization 參照
"""

HOSPITAL_FIELD_MAP = {
    "A": {
        "format": "json",
        "field_map": {
            "pid": "identifier",
            "sex": "gender",
            "dob": "birthDate",
            "cname": "name",
        },
        "gender_dict": {
            "M": "male",
            "F": "female",
            "Male": "male",
            "Female": "female",
        },
        "date_format": "%Y-%m-%d",
        "org_code": "HOSP-A",
    },
    "B": {
        "format": "csv",
        "field_map": {
            "身分證字號": "identifier",
            "性別": "gender",
            "生日": "birthDate",
            "姓名": "name",
        },
        "gender_dict": {
            "男": "male",
            "女": "female",
        },
        "date_format": "roc/%y/%m/%d",  # 民國年格式，需 +1911 轉換
        "org_code": "HOSP-B",
    },
}

# FHIR identifier system：台灣身分證字號
IDENTIFIER_SYSTEM = "urn:oid:2.16.886.101.20003.20001"  # tw-nid

# 預設的 Patient schema 路徑
DEFAULT_SCHEMA_PATH = "bio_data_pool/fhir_patient_schema.json"
