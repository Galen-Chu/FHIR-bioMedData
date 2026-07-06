"""欄位正規化單元測試：性別字典、民國年轉換、身分證字號檢查碼。"""

import pytest

from main_ingest import (
    ValidationError,
    normalize_birthdate,
    normalize_gender,
    validate_tw_nid,
)


class TestNormalizeGender:
    def test_hospital_a_dict(self):
        gender_dict = {"M": "male", "F": "female", "Male": "male", "Female": "female"}
        assert normalize_gender("M", gender_dict) == "male"
        assert normalize_gender("Female", gender_dict) == "female"
        assert normalize_gender(" F ", gender_dict) == "female"

    def test_hospital_b_dict(self):
        gender_dict = {"男": "male", "女": "female"}
        assert normalize_gender("男", gender_dict) == "male"
        assert normalize_gender("女", gender_dict) == "female"

    def test_unmapped_value_falls_back_to_unknown(self):
        assert normalize_gender("不詳", {"男": "male"}) == "unknown"
        assert normalize_gender("", {}) == "unknown"


class TestNormalizeBirthdate:
    def test_western_format_passthrough(self):
        assert normalize_birthdate("1990-01-02", "%Y-%m-%d") == "1990-01-02"

    def test_roc_year_conversion(self):
        assert normalize_birthdate("79/01/02", "roc/%y/%m/%d") == "1990-01-02"
        assert normalize_birthdate("102/08/19", "roc/%y/%m/%d") == "2013-08-19"

    def test_roc_zero_padding(self):
        assert normalize_birthdate("79/1/2", "roc/%y/%m/%d") == "1990-01-02"

    def test_invalid_date_raises(self):
        with pytest.raises(ValidationError):
            normalize_birthdate("1990/01/02", "%Y-%m-%d")
        with pytest.raises(ValidationError):
            normalize_birthdate("79/13/45", "roc/%y/%m/%d")
        with pytest.raises(ValidationError):
            normalize_birthdate("", "%Y-%m-%d")


class TestValidateTwNid:
    def test_valid_nid(self):
        assert validate_tw_nid("A123456789") == "A123456789"
        assert validate_tw_nid("K104332183") == "K104332183"

    def test_lowercase_is_normalized(self):
        assert validate_tw_nid("a123456789") == "A123456789"

    def test_bad_checksum_raises(self):
        with pytest.raises(ValidationError):
            validate_tw_nid("A123456780")

    def test_bad_format_raises(self):
        with pytest.raises(ValidationError):
            validate_tw_nid("1234567890")
        with pytest.raises(ValidationError):
            validate_tw_nid("A32345678")
        with pytest.raises(ValidationError):
            validate_tw_nid("A3234567890")
