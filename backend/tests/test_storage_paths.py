"""Unit tests for tenant/job path handling and upload persistence."""

import pytest

import storage_paths


@pytest.mark.parametrize("tenant", ["default", "acme", "tenant_1", "a-b-c", "A1" * 32])
def test_validate_tenant_id_accepts_valid(tenant):
    assert storage_paths.validate_tenant_id(tenant) == tenant


@pytest.mark.parametrize(
    "tenant",
    ["", "../etc", "has space", "bad/slash", "x" * 65, "drop;table", "ünïcode"],
)
def test_validate_tenant_id_rejects_invalid(tenant):
    with pytest.raises(ValueError):
        storage_paths.validate_tenant_id(tenant)


def test_validate_job_object_id():
    storage_paths.validate_job_object_id("a" * 24)
    with pytest.raises(ValueError):
        storage_paths.validate_job_object_id("short")
    with pytest.raises(ValueError):
        storage_paths.validate_job_object_id("g" * 24)  # non-hex char


def test_job_raw_dir_blocks_traversal():
    # Path-traversal attempt in the tenant id must be rejected before any I/O.
    with pytest.raises(ValueError):
        storage_paths.job_raw_dir("../../etc", "a" * 24)


def test_save_job_upload_writes_file(tmp_path, monkeypatch):
    monkeypatch.setattr(storage_paths, "UPLOAD_ROOT", tmp_path)
    job_id = "b" * 24
    path = storage_paths.save_job_upload("acme", job_id, b"hello", ".jpg")

    assert path.endswith("source.jpg")
    assert "acme" in path and job_id in path
    with open(path, "rb") as f:
        assert f.read() == b"hello"


def test_save_job_upload_sanitizes_suffix(tmp_path, monkeypatch):
    monkeypatch.setattr(storage_paths, "UPLOAD_ROOT", tmp_path)
    # A malicious suffix should be coerced to the safe default.
    path = storage_paths.save_job_upload("acme", "c" * 24, b"x", "../evil")
    assert path.endswith("source.bin")
