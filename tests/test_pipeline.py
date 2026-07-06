"""端到端管線測試：讀檔 → 正規化 → FHIR Mapping → schema 驗證 → Upsert。"""

import hashlib
import json

import pytest

from config import HOSPITAL_FIELD_MAP
from main_ingest import ValidationError, run_ingest, to_fhir_patient, validate_against_schema
from server_mock_fhir.routes import PATIENT_DB, reset, upsert

SCHEMA_PATH = "bio_data_pool/fhir_patient_schema.json"


@pytest.fixture(autouse=True)
def clean_db():
    reset()
    yield
    reset()


@pytest.fixture()
def schema():
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        return json.load(f)


class TestToFhirPatient:
    def test_hospital_a_record(self, schema):
        raw = {"pid": "K104332183", "sex": "M", "dob": "1990-01-02", "cname": "王小明"}
        patient = to_fhir_patient(raw, HOSPITAL_FIELD_MAP["A"])
        assert patient["resourceType"] == "Patient"
        assert patient["identifier"][0]["value"] == "K104332183"
        assert patient["gender"] == "male"
        assert patient["birthDate"] == "1990-01-02"
        assert patient["managingOrganization"]["reference"] == "Organization/HOSP-A"
        validate_against_schema(patient, schema)

    def test_hospital_b_roc_date(self, schema):
        raw = {"姓名": "王小明", "身分證字號": "K104332183", "性別": "男", "生日": "79/01/02"}
        patient = to_fhir_patient(raw, HOSPITAL_FIELD_MAP["B"])
        assert patient["birthDate"] == "1990-01-02"
        assert patient["managingOrganization"]["reference"] == "Organization/HOSP-B"
        validate_against_schema(patient, schema)

    def test_hash_id_deidentification(self, schema):
        raw = {"pid": "K104332183", "sex": "M", "dob": "1990-01-02", "cname": "王小明"}
        patient = to_fhir_patient(raw, HOSPITAL_FIELD_MAP["A"], hash_id=True)
        expected = hashlib.sha256(b"K104332183").hexdigest()
        assert patient["identifier"][0]["value"] == expected
        validate_against_schema(patient, schema)

    def test_missing_required_field_raises(self):
        raw = {"pid": "K104332183", "sex": "M", "dob": "", "cname": "王小明"}
        with pytest.raises(ValidationError):
            to_fhir_patient(raw, HOSPITAL_FIELD_MAP["A"])


class TestUpsert:
    def test_created_then_updated(self):
        patient = {"identifier": [{"system": "urn:test", "value": "K104332183"}]}
        assert upsert(patient)["outcome"] == "created"
        assert upsert(patient)["outcome"] == "updated"
        assert len(PATIENT_DB) == 1


class TestRunIngest:
    def test_hospital_a_end_to_end(self, tmp_path):
        results = run_ingest(
            hospital="A",
            input_path="bio_data_pool/raw_hospital_A.json",
            rejects_out=str(tmp_path / "rejected.jsonl"),
        )
        # 12 筆：10 筆通過；1 筆缺生日、1 筆檢查碼錯誤被拒絕
        assert results["created"] == 10
        assert results["rejected"] == 2
        rejected_lines = (tmp_path / "rejected.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(rejected_lines) == 2

    def test_cross_hospital_upsert_updates_duplicates(self, tmp_path):
        run_ingest(hospital="A", input_path="bio_data_pool/raw_hospital_A.json",
                   rejects_out=str(tmp_path / "r.jsonl"))
        results_b = run_ingest(hospital="B", input_path="bio_data_pool/raw_hospital_B.csv",
                               rejects_out=str(tmp_path / "r.jsonl"))
        # 8 筆：2 筆與醫院 A 重複（更新）、5 筆新增、1 筆身分證字號無效
        assert results_b["updated"] == 2
        assert results_b["created"] == 5
        assert results_b["rejected"] == 1
        assert len(PATIENT_DB) == 15

    def test_validate_only_writes_nothing(self, tmp_path):
        results = run_ingest(
            hospital="A",
            input_path="bio_data_pool/raw_hospital_A.json",
            mode="validate-only",
            rejects_out=str(tmp_path / "r.jsonl"),
        )
        assert results["validated"] == 10
        assert len(PATIENT_DB) == 0

    def test_dry_run_writes_nothing(self, tmp_path):
        results = run_ingest(
            hospital="A",
            input_path="bio_data_pool/raw_hospital_A.json",
            mode="dry-run",
            rejects_out=str(tmp_path / "r.jsonl"),
        )
        assert results["validated"] == 10
        assert len(PATIENT_DB) == 0
