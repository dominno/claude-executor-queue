import os

import pytest


@pytest.fixture
def isolated_queue_environment(tmp_path, monkeypatch):
    for key in tuple(os.environ):
        if key.startswith(('GIT_', 'XQ_')) or key == 'CLAUDE_CONFIG_DIR':
            monkeypatch.delenv(key)

    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('USERPROFILE', str(home))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(home / '.config'))
    monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', '1')
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', os.devnull)
    monkeypatch.setenv('GIT_ALLOW_PROTOCOL', 'file')
    monkeypatch.setenv('GIT_TERMINAL_PROMPT', '0')
    return home
