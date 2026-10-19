"""TODO F3：GitHub 回報的主要語言被存下來；沒有就留空，不猜。"""

from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.models import Base, GitHubRepoState
from integrations import github_client
from integrations.github_client import GitHubClient


class _Db:
    def __init__(self, factory):
        self._factory = factory

    @contextmanager
    def session_scope(self):
        session = self._factory()
        try:
            yield session
            session.commit()
        finally:
            session.close()


def _client(monkeypatch, repos, factory):
    monkeypatch.setattr(github_client, "get_db", lambda: _Db(factory))
    client = GitHubClient()
    monkeypatch.setattr(client, "test_connection", lambda: {"connected": True})
    monkeypatch.setattr(client, "fetch_all_repositories", lambda limit=50: repos)
    monkeypatch.setattr(client, "fetch_repo_prs", lambda *a, **k: [])
    monkeypatch.setattr(client, "fetch_repo_issues", lambda *a, **k: [], raising=False)
    return client


def _repo(name, language):
    return {
        "name": name, "full_name": f"dof/{name}", "html_url": f"https://github.com/dof/{name}",
        "language": language,
    }


def test_sync_all_stores_language_and_leaves_missing_as_none(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    repos = [_repo("alpha", "Python"), _repo("beta", None), _repo("gamma", "")]
    _client(monkeypatch, repos, factory).sync_all()

    with factory() as session:
        langs = {r.repo_name: r.language for r in session.query(GitHubRepoState).all()}
    assert langs == {"alpha": "Python", "beta": None, "gamma": None}

    repos[1]["language"] = "Go"  # 之後 GitHub 判出語言 → 更新
    _client(monkeypatch, repos, factory).sync_all()
    with factory() as session:
        assert session.query(GitHubRepoState).filter_by(repo_name="beta").one().language == "Go"


def test_migration_adds_nullable_language_column_to_existing_table(tmp_path):
    import sqlite3
    from core.migrations import upgrade_sqlite_database

    path = tmp_path / "m.db"
    upgrade_sqlite_database(path, backup_before=False)
    with sqlite3.connect(path) as conn:
        cols = {r[1]: r for r in conn.execute("PRAGMA table_info(github_repo_states)")}
    assert "language" in cols and cols["language"][3] == 0  # notnull=0
