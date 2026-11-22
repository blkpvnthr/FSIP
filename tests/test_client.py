"""Unit tests for the HTTP client, retries, polling and safe extraction.

All HTTP is mocked with the ``responses`` library; no network is touched.
"""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import pytest
import responses
from ingestion.client import (
    UsaspendingClient,
    safe_extract_zip,
    sha256_file,
)
from ingestion.config import Settings
from ingestion.exceptions import (
    DownloadJobError,
    DownloadJobTimeout,
    PermanentAPIError,
    TransientAPIError,
    UnsafeArchiveError,
)

BASE = "https://api.usaspending.gov"
STATUS_URL = f"{BASE}/api/v2/download/status?file_name=x.zip"
FILE_URL = "https://files.usaspending.gov/generated_downloads/x.zip"


def _client(settings: Settings) -> UsaspendingClient:
    # Inject a no-op sleep so retry/poll loops never actually wait.
    calls = {"sleeps": 0}

    def fake_sleep(_seconds):
        calls["sleeps"] += 1

    client = UsaspendingClient(settings, sleep=fake_sleep)
    client._sleep_calls = calls  # type: ignore[attr-defined]
    return client


@responses.activate
def test_submit_bulk_download_returns_job_descriptor(settings: Settings):
    responses.post(
        f"{BASE}/api/v2/bulk_download/awards/",
        json={"status_url": STATUS_URL, "file_name": "x.zip", "file_url": FILE_URL},
        status=200,
    )
    client = _client(settings)
    data = client.submit_bulk_download({"filters": {}})
    assert data["file_name"] == "x.zip"
    assert data["status_url"] == STATUS_URL


@responses.activate
def test_request_retries_on_5xx_then_succeeds(settings: Settings):
    url = f"{BASE}/api/v2/bulk_download/awards/"
    responses.post(url, status=503)
    responses.post(url, status=503)
    responses.post(url, json={"file_name": "x.zip"}, status=200)
    client = _client(settings)
    data = client.submit_bulk_download({})
    assert data["file_name"] == "x.zip"
    assert client._sleep_calls["sleeps"] == 2  # two backoffs before success


@responses.activate
def test_request_gives_up_after_max_retries(settings: Settings):
    url = f"{BASE}/api/v2/bulk_download/awards/"
    for _ in range(settings.max_retries):
        responses.post(url, status=500)
    client = _client(settings)
    with pytest.raises(TransientAPIError):
        client.submit_bulk_download({})


@responses.activate
def test_permanent_4xx_is_not_retried(settings: Settings):
    url = f"{BASE}/api/v2/bulk_download/awards/"
    responses.post(url, status=400, body="bad filters")
    client = _client(settings)
    with pytest.raises(PermanentAPIError):
        client.submit_bulk_download({})
    assert client._sleep_calls["sleeps"] == 0  # no backoff on permanent error


@responses.activate
def test_poll_runs_until_finished(settings: Settings):
    responses.get(STATUS_URL, json={"status": "running"}, status=200)
    responses.get(STATUS_URL, json={"status": "running"}, status=200)
    responses.get(
        STATUS_URL,
        json={"status": "finished", "total_rows": 196, "file_url": FILE_URL},
        status=200,
    )
    client = _client(settings)
    final = client.poll_download(STATUS_URL)
    assert final["status"] == "finished"
    assert final["total_rows"] == 196


@responses.activate
def test_poll_raises_on_failed(settings: Settings):
    responses.get(STATUS_URL, json={"status": "failed", "message": "boom"}, status=200)
    client = _client(settings)
    with pytest.raises(DownloadJobError):
        client.poll_download(STATUS_URL)


@responses.activate
def test_poll_times_out(settings: Settings):
    responses.get(STATUS_URL, json={"status": "running"}, status=200)
    # monotonic that jumps past the deadline on the second read.
    ticks = iter([0.0, 0.0, 1000.0, 1000.0, 1000.0])
    client = UsaspendingClient(settings, sleep=lambda _s: None, monotonic=lambda: next(ticks))
    with pytest.raises(DownloadJobTimeout):
        client.poll_download(STATUS_URL)


@responses.activate
def test_download_file_streams_and_checksums(settings: Settings, tmp_path: Path):
    payload = b"col1,col2\n1,2\n" * 1000
    responses.get(FILE_URL, body=payload, status=200)
    client = _client(settings)
    dest = tmp_path / "dl.zip"
    size, digest = client.download_file(FILE_URL, dest)
    assert size == len(payload)
    assert digest == hashlib.sha256(payload).hexdigest()
    assert dest.read_bytes() == payload


# ------------------------------------------------------------------ #
# Safe extraction
# ------------------------------------------------------------------ #
def test_safe_extract_happy_path(sample_zip: Path, tmp_path: Path, settings: Settings):
    out = tmp_path / "ex"
    extracted = safe_extract_zip(sample_zip, out, settings)
    assert len(extracted) == 1
    assert extracted[0].suffix == ".csv"
    assert extracted[0].exists()


def test_safe_extract_rejects_path_traversal(tmp_path: Path, settings: Settings):
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../../escape.txt", "pwned")
    with pytest.raises(UnsafeArchiveError):
        safe_extract_zip(evil, tmp_path / "ex", settings)


def test_safe_extract_rejects_too_many_files(tmp_path: Path):
    small = Settings(data_dir=tmp_path / "d", max_extract_files=2)
    many = tmp_path / "many.zip"
    with zipfile.ZipFile(many, "w") as zf:
        for i in range(5):
            zf.writestr(f"f{i}.txt", "x")
    with pytest.raises(UnsafeArchiveError):
        safe_extract_zip(many, tmp_path / "ex", small)


def test_safe_extract_rejects_zip_bomb_size(tmp_path: Path):
    small = Settings(data_dir=tmp_path / "d", max_extract_bytes=10)
    bomb = tmp_path / "bomb.zip"
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("big.txt", "A" * 10_000)  # far over the 10-byte cap
    with pytest.raises(UnsafeArchiveError):
        safe_extract_zip(bomb, tmp_path / "ex", small)


def test_sha256_file_matches_hashlib(tmp_path: Path):
    p = tmp_path / "f.bin"
    data = b"hello world" * 100
    p.write_bytes(data)
    assert sha256_file(p) == hashlib.sha256(data).hexdigest()
