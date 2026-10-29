import subprocess

import pytest

from app.log_source import read_trace_logs


def configure(monkeypatch):
    monkeypatch.setenv('DIAG_SSH_USER', 'neptune')
    monkeypatch.setenv('DIAG_SSH_KEY_PATH', '/run/ssh74/id_ed25519')
    monkeypatch.setenv('DIAG_SSH_KNOWN_HOSTS', '/run/ssh74/known_hosts')
    monkeypatch.setenv('DIAG_LOG_QUERY_PROGRAM', '/home/neptune/.local/libexec/pi-bugfix-query-logs')


def test_repeatable_issue_can_query_by_mobi_without_inventing_trace(monkeypatch):
    configure(monkeypatch)
    calls = []

    def run(argv, **options):
        calls.append((argv, options))
        return subprocess.CompletedProcess(argv, 0, 'matched mobi:425 log\n', '')

    monkeypatch.setattr(subprocess, 'run', run)
    result = read_trace_logs({'mobi': '425', 'reproduction': 'always'})
    assert result['query_kind'] == 'mobi'
    assert result['query_value'] == '425'
    assert calls[0][0][-2:] == ['--mobi', '425']
    assert 'UserKnownHostsFile=/run/ssh74/known_hosts' in calls[0][0]


def test_trace_query_is_fixed_argv_and_rejects_shell_syntax(monkeypatch):
    configure(monkeypatch)
    with pytest.raises(ValueError):
        read_trace_logs({'traceId': 'trace; touch /tmp/unsafe'})


def test_unconfigured_ssh_blocks_instead_of_connecting(monkeypatch):
    monkeypatch.delenv('DIAG_SSH_USER', raising=False)
    assert read_trace_logs({'traceId': 'abc123'}) is None
