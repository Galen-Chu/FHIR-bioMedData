"""main_ingest.py — CLI 自動化清洗引擎（核心入口）

將雜亂異構的院所原始資料（JSON/CSV），依 config.py 的欄位對齊規則正規化，
轉換為 HL7 FHIR Patient 資源，經 schema 驗證後以 Upsert 語意寫入
Mock FHIR Server。

三種執行模式，對應「先驗證、再試跑、後落庫」的安全操作習慣：

    python main_ingest.py --hospital A --input bio_data_pool/raw_hospital_A.json --validate-only
    python main_ingest.py --hospital A --input bio_data_pool/raw_hospital_A.json --dry-run
    python main_ingest.py --hospital B --input bio_data_pool/raw_hospital_B.csv

核心管線僅使用 Python 標準函式庫；--server-url 模式亦以 urllib 呼叫 HTTP。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

from config import DEFAULT_SCHEMA_PATH, HOSPITAL_FIELD_MAP, IDENTIFIER_SYSTEM


class ValidationError(Exception):
    """欄位正規化或 schema 驗證失敗。"""


# ---------------------------------------------------------------------------
# 1. 讀取原始檔案
# ---------------------------------------------------------------------------

def load_raw(input_path: str, file_format: str) -> list[dict]:
    path = Path(input_path)
    if not path.exists():
        raise FileNotFoundError(f"找不到輸入檔案：{input_path}")
    if file_format == "json":
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            raise ValueError("JSON 輸入須為病患紀錄陣列")
        return data
    if file_format == "csv":
        with path.open(encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    raise ValueError(f"不支援的檔案格式：{file_format}")


# ---------------------------------------------------------------------------
# 2. 欄位正規化（Normalize）
# ---------------------------------------------------------------------------

def normalize_gender(raw_value: str, gender_dict: dict) -> str:
    """性別字典轉換；查無對應時回傳 FHIR 標準值 unknown。"""
    return gender_dict.get(str(raw_value).strip(), "unknown")


def normalize_birthdate(raw_value: str, date_format: str) -> str:
    """生日格式統一：民國年轉西元（+1911）、補零，輸出 ISO 8601。"""
    raw_value = str(raw_value).strip()
    if not raw_value:
        raise ValidationError("缺少必填欄位 birthDate")
    if date_format.startswith("roc/"):
        parts = raw_value.replace("-", "/").replace(".", "/").split("/")
        if len(parts) != 3:
            raise ValidationError(f"無法解析民國年生日：{raw_value!r}")
        try:
            y, m, d = (int(p) for p in parts)
            return datetime(y + 1911, m, d).strftime("%Y-%m-%d")
        except ValueError as e:
            raise ValidationError(f"無效的民國年生日 {raw_value!r}：{e}") from e
    try:
        return datetime.strptime(raw_value, date_format).strftime("%Y-%m-%d")
    except ValueError as e:
        raise ValidationError(f"生日 {raw_value!r} 不符合格式 {date_format}：{e}") from e


# 台灣身分證字號：首字母區域碼 -> 兩位數字
_NID_LETTER_CODE = {
    "A": 10, "B": 11, "C": 12, "D": 13, "E": 14, "F": 15, "G": 16, "H": 17,
    "I": 34, "J": 18, "K": 19, "L": 20, "M": 21, "N": 22, "O": 35, "P": 23,
    "Q": 24, "R": 25, "S": 26, "T": 27, "U": 28, "V": 29, "W": 32, "X": 30,
    "Y": 31, "Z": 33,
}
_NID_WEIGHTS = (1, 9, 8, 7, 6, 5, 4, 3, 2, 1, 1)


def validate_tw_nid(raw_value: str) -> str:
    """驗證台灣身分證字號格式與檢查碼，回傳正規化後的值。"""
    value = str(raw_value).strip().upper()
    if not re.fullmatch(r"[A-Z][12]\d{8}", value):
        raise ValidationError(f"身分證字號格式錯誤：{raw_value!r}")
    code = _NID_LETTER_CODE[value[0]]
    digits = [code // 10, code % 10] + [int(c) for c in value[1:]]
    if sum(d * w for d, w in zip(digits, _NID_WEIGHTS)) % 10 != 0:
        raise ValidationError(f"身分證字號檢查碼錯誤：{raw_value!r}")
    return value


# ---------------------------------------------------------------------------
# 3. FHIR Mapping
# ---------------------------------------------------------------------------

def remap_fields(raw: dict, field_map: dict) -> dict:
    """依院所 field_map 將原始欄位名轉為標準欄位名。"""
    return {
        std_field: raw[src_field]
        for src_field, std_field in field_map.items()
        if src_field in raw
    }


def to_fhir_patient(raw: dict, rule: dict, hash_id: bool = False) -> dict:
    record = remap_fields(raw, rule["field_map"])
    for required in ("identifier", "name", "gender", "birthDate"):
        if not str(record.get(required, "")).strip():
            raise ValidationError(f"缺少必填欄位 {required}")

    nid = validate_tw_nid(record["identifier"])
    if hash_id:  # PII 去識別化：以 SHA-256 雜湊後儲存
        nid = hashlib.sha256(nid.encode("utf-8")).hexdigest()

    return {
        "resourceType": "Patient",
        "identifier": [{"system": IDENTIFIER_SYSTEM, "value": nid}],
        "name": [{"text": str(record["name"]).strip()}],
        "gender": normalize_gender(record["gender"], rule["gender_dict"]),
        "birthDate": normalize_birthdate(record["birthDate"], rule["date_format"]),
        "managingOrganization": {"reference": f"Organization/{rule['org_code']}"},
    }


# ---------------------------------------------------------------------------
# 4. Schema 驗證（JSON Schema 子集：type / required / properties / items /
#    enum / const / pattern / minItems）
# ---------------------------------------------------------------------------

_JSON_TYPES = {
    "object": dict, "array": list, "string": str,
    "number": (int, float), "integer": int, "boolean": bool,
}


def _validate_node(value, schema: dict, path: str) -> None:
    if "const" in schema and value != schema["const"]:
        raise ValidationError(f"{path}: 應為 {schema['const']!r}，實際為 {value!r}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValidationError(f"{path}: {value!r} 不在允許值 {schema['enum']}")
    if "type" in schema and not isinstance(value, _JSON_TYPES[schema["type"]]):
        raise ValidationError(f"{path}: 型別應為 {schema['type']}，實際為 {type(value).__name__}")
    if "pattern" in schema and isinstance(value, str) and not re.fullmatch(schema["pattern"], value):
        raise ValidationError(f"{path}: {value!r} 不符合格式 {schema['pattern']}")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                raise ValidationError(f"{path}: 缺少必填欄位 {key}")
        for key, sub in schema.get("properties", {}).items():
            if key in value:
                _validate_node(value[key], sub, f"{path}.{key}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            raise ValidationError(f"{path}: 至少需 {schema['minItems']} 個元素")
        if "items" in schema:
            for i, item in enumerate(value):
                _validate_node(item, schema["items"], f"{path}[{i}]")


def validate_against_schema(patient: dict, schema: dict) -> None:
    _validate_node(patient, schema, "Patient")


# ---------------------------------------------------------------------------
# 5. Upsert 落庫（in-process 或 HTTP）
# ---------------------------------------------------------------------------

def upsert_in_process(patient: dict) -> str:
    from server_mock_fhir.routes import upsert
    return upsert(patient)["outcome"]


def upsert_via_http(patient: dict, server_url: str) -> str:
    req = urllib.request.Request(
        f"{server_url.rstrip('/')}/Patient",
        data=json.dumps(patient).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())["outcome"]


# ---------------------------------------------------------------------------
# 管線主流程
# ---------------------------------------------------------------------------

def log_rejected(raw: dict, error: Exception, rejects_path: Path) -> None:
    """清洗失敗資料寫入複核佇列（JSON Lines），供人工複核。"""
    rejects_path.parent.mkdir(parents=True, exist_ok=True)
    with rejects_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"raw": raw, "reason": str(error)}, ensure_ascii=False) + "\n")


def run_ingest(
    hospital: str,
    input_path: str,
    mode: str = "ingest",
    schema_path: str = DEFAULT_SCHEMA_PATH,
    server_url: str | None = None,
    hash_id: bool = False,
    rejects_out: str = "logs/rejected.jsonl",
) -> dict:
    rule = HOSPITAL_FIELD_MAP[hospital]
    with open(schema_path, encoding="utf-8") as f:
        schema = json.load(f)

    raw_records = load_raw(input_path, rule["format"])
    print(f"[INFO] 讀取 {len(raw_records)} 筆病患紀錄（醫院 {hospital}"
          + ("，民國年生日格式偵測）" if rule["date_format"].startswith("roc/") else "）"))
    print(f"[INFO] 清洗規則套用：gender_dict({'/'.join(rule['gender_dict'])}) · "
          f"date_format({rule['date_format']})")

    results = {"created": 0, "updated": 0, "rejected": 0, "validated": 0}
    rejects_path = Path(rejects_out)
    for raw in raw_records:
        try:
            patient = to_fhir_patient(raw, rule, hash_id=hash_id)
            validate_against_schema(patient, schema)   # 4. Schema 驗證
            results["validated"] += 1
            if mode in ("validate-only", "dry-run"):
                continue
            if server_url:                             # 5. Upsert 落庫
                outcome = upsert_via_http(patient, server_url)
            else:
                outcome = upsert_in_process(patient)
            results[outcome] += 1
        except ValidationError as e:
            results["rejected"] += 1
            log_rejected(raw, e, rejects_path)

    if mode == "validate-only":
        print(f"[INFO] Schema 驗證中... {results['validated']} 筆通過、"
              f"{results['rejected']} 筆不合格")
        print("[OK]   驗證完成，未寫入資料庫（--validate-only）")
    elif mode == "dry-run":
        print(f"[INFO] 模擬落庫：通過 {results['validated']} 筆 / "
              f"拒絕 {results['rejected']} 筆（未實際寫入）")
    else:
        print(f"[INFO] Upsert 完成 → 新增 {results['created']} 筆、"
              f"更新 {results['updated']} 筆、拒絕 {results['rejected']} 筆")
        if results["rejected"]:
            print(f"[INFO] 拒絕資料已寫入複核佇列：{rejects_path}")
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="醫療數據自動化清洗與 FHIR 對齊系統 — CLI 清洗引擎")
    parser.add_argument("--hospital", required=True, choices=sorted(HOSPITAL_FIELD_MAP),
                        help="院所代碼（對應 config.py 的對齊規則）")
    parser.add_argument("--input", required=True, help="原始資料檔路徑（JSON/CSV）")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--validate-only", action="store_true",
                            help="僅執行 schema 驗證，不落庫")
    mode_group.add_argument("--dry-run", action="store_true",
                            help="模擬完整流程但不實際寫入")
    parser.add_argument("--schema", default=DEFAULT_SCHEMA_PATH,
                        help="FHIR Patient schema 路徑")
    parser.add_argument("--server-url", default=None,
                        help="Mock FHIR Server 位址（如 http://localhost:8000）；"
                             "未指定時使用行程內記憶體資料庫")
    parser.add_argument("--hash-id", action="store_true",
                        help="PII 去識別化：身分證字號以 SHA-256 雜湊後儲存")
    parser.add_argument("--rejects-out", default="logs/rejected.jsonl",
                        help="清洗失敗資料的複核佇列輸出路徑")
    args = parser.parse_args(argv)

    mode = "validate-only" if args.validate_only else "dry-run" if args.dry_run else "ingest"
    try:
        run_ingest(
            hospital=args.hospital,
            input_path=args.input,
            mode=mode,
            schema_path=args.schema,
            server_url=args.server_url,
            hash_id=args.hash_id,
            rejects_out=args.rejects_out,
        )
    except (FileNotFoundError, ValueError) as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
