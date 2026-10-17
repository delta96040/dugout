import pytest
from dugout_pipeline import ids


class Broken(Exception):
    pass


def _broken_sync():
    raise Broken("latest.json has no valid stamp.")


def test_broken_upstream_release_keeps_the_cached_catalogue(tmp_path, monkeypatch):
    import reep

    cached = tmp_path / "reep.duckdb"
    cached.write_bytes(b"x")
    monkeypatch.setattr(reep.catalog, "sync", _broken_sync)
    monkeypatch.setattr(ids, "catalogue_path", lambda: cached)
    assert ids.sync_catalogue() == "cached"


def test_broken_upstream_release_without_a_cache_still_fails(tmp_path, monkeypatch):
    import reep

    monkeypatch.setattr(reep.catalog, "sync", _broken_sync)
    monkeypatch.setattr(ids, "catalogue_path", lambda: tmp_path / "missing.duckdb")
    with pytest.raises(Broken):
        ids.sync_catalogue()
