"""Offline tests for the brief model, brief-lint, the assign lint gate and compose (context preamble)."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from conftest import load_xq

SCRIPT = Path(__file__).resolve().parents[1] / '.claude/skills/executor-queue/scripts/xq.py'

GOOD = '''# Parser task
Domain: backend
Result: The parser accepts empty input and returns an empty list.

1. Read src/parser.py.
2. Handle the empty string.

## Acceptance
Files:
- `src/parser.py`
- tests/test_parser.py
Checks: python -m pytest -q tests/test_parser.py
Notes: notes/parser.md
'''


@pytest.fixture
def b(tmp_path, monkeypatch, isolated_queue_environment):
    repo = tmp_path / 'repo'
    repo.mkdir()
    monkeypatch.chdir(repo)
    xq = load_xq()
    monkeypatch.setattr(xq.module_of('resource_readings'), 'resource_readings', lambda root: dict(load=0, cpus=2, repo_free_gb=10, temp_free_gb=10))
    monkeypatch.setattr(xq.module_of('live_process_counts'), 'live_process_counts', lambda samples=None: dict(codex=0, agy=0))
    subprocess.run(['git', 'init', '-q', '-b', 'main'], check=True, timeout=15)
    repo = repo.resolve()

    def cli(*args):
        monkeypatch.setattr(sys, 'argv', ['xq', *args])
        return xq.main()

    def brief(text, name='t1'):
        path = repo / '.agents/briefs' / (name + '.md')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return '.agents/briefs/' + name + '.md'

    def configure(**data):
        (repo / '.agents').mkdir(exist_ok=True)
        (repo / '.agents/config.json').write_text(json.dumps(data), encoding='utf-8')

    def lint(text, **settings):
        return xq.lint_brief(text, dict(xq.BRIEF_LINT_DEFAULTS, **settings))

    return SimpleNamespace(**locals())


def test_brief_model_parses_result_fields_steps_and_acceptance(b):
    text = GOOD.replace('Checks:', '<!-- Result: hidden -->\n```\nResult: in a fence\n## Other\n```\nChecks:')
    model = b.xq.BriefModel(text)
    assert [value for _, value in model.results] == ['The parser accepts empty input and returns an empty list.']
    assert model.fields['Domain'][1] == 'backend' and len(model.steps) == 2
    assert model.acceptance_last and len(model.acceptance) == 1
    assert model.files() == ['src/parser.py', 'tests/test_parser.py']
    assert b.xq.BriefModel(GOOD.replace('Files:\n- `src/parser.py`\n- tests/test_parser.py',
                                        'Files: src/a.py, `docs/*.md`')).files() == ['src/a.py', 'docs/*.md']
    assert b.lint(text) == ([], [])


@pytest.mark.parametrize('change, message', [
    (('Result: The parser', 'The parser'), 'exactly one "Result:" line, found 0'),
    (('Domain: backend', 'Result: other'), 'exactly one "Result:" line, found 2'),
    (('Result: The parser accepts empty input and returns an empty list.', 'Result: TBD'), 'Result is a placeholder'),
    (('## Acceptance', '## Done'), 'missing final "## Acceptance"'),
    (('Notes: notes/parser.md', 'Notes: notes/parser.md\n## Later'), 'must be the last section'),
    (('# Parser task', '## Acceptance\nFiles: a\n## Body'), '2 "## Acceptance" sections'),
    (('Checks: python -m pytest -q tests/test_parser.py\n', ''), 'no "Checks:" line'),
    (('Notes: notes/parser.md', 'Notes: n/a'), 'Notes: is empty or a placeholder'),
    (('- tests/test_parser.py', '- ../outside.py'), 'must be a repo-relative path'),
    (('- tests/test_parser.py', '- /etc/passwd'), 'must be a repo-relative path'),
])
def test_brief_lint_errors(b, change, message):
    errors, _ = b.lint(GOOD.replace(*change))
    assert any(message in error for error in errors), errors


@pytest.mark.parametrize('change, message', [
    (('returns an empty list.', 'returns an empty list; also logs.'), 'several outcomes'),
    (('returns an empty list.', 'returns an empty list and also logs.'), 'several outcomes'),
    (('returns an empty list.', 'returns' + ' word' * 50 + '.'), 'words (max 45)'),
    (('2. Handle', '2. a\n3. b\n4. c\n5. Handle'), '5 numbered steps (max 4)'),
])
def test_brief_lint_warnings_exit_zero_unless_enforced(b, capsys, change, message):
    path = b.brief(GOOD.replace(*change))
    assert b.cli('brief-lint', '--brief', path) == 0
    out = capsys.readouterr().out
    assert 'WARN' in out and message in out and 'ERROR' not in out
    assert b.cli('brief-lint', '--brief', path, '--enforce') == 1


def test_brief_lint_cli_json_exit_codes_and_usage(b, capsys):
    assert b.cli('brief-lint', '--brief', b.brief(GOOD)) == 0
    assert 'brief-lint OK: 0 error(s), 0 warning(s)' in capsys.readouterr().out
    assert b.cli('brief-lint', '--brief', b.brief('Result: x\n'), '--json') == 1
    data = json.loads(capsys.readouterr().out)
    assert data['version'] == 1 and data['ok'] is False and data['errors'] and data['warnings'] == []
    with pytest.raises(SystemExit) as error:
        b.cli('brief-lint')
    assert error.value.code == 2
    with pytest.raises(SystemExit, match='brief not found'):
        b.cli('brief-lint', '--brief', 'missing.md')


def test_restricted_words_need_handoff_marker_and_config_overrides(b):
    text = GOOD.replace('2. Handle', '2. Lead runs the network fetch.\n3. Network calls are forbidden.\n4. Handle')
    assert b.lint(text) == ([], [])  # Generic default: nothing is restricted.
    errors, _ = b.lint(text, restricted=['network'])
    assert errors == ['restricted "network" on line(s) 7 without a "lead runs" handoff on that line']
    errors, _ = b.lint(text, restricted=['network'], handoff_marker='Lead runs', max_steps=9)
    assert len(errors) == 1 and 'networks' not in errors[0]
    assert b.lint(text.replace('Network calls', 'Networking'), restricted=['network']) == ([], [])


def test_header_field_allow_list_and_notes_file_option(b):
    assert b.lint(GOOD, fields={'Domain': ['backend', 'ui']}) == ([], [])
    errors, _ = b.lint(GOOD, fields={'Domain': ['ui']})
    assert errors == ['line 2: Domain: "backend" is not one of: ui']
    errors, _ = b.lint(GOOD.replace('Domain: backend\n', ''), fields={'Domain': ['ui']})
    assert errors == ['missing header field "Domain:" (allowed: ui)']
    no_file = GOOD.replace('notes/parser.md', 'record findings in the task')
    assert b.lint(no_file) == ([], [])
    assert b.lint(no_file, notes_require_file=True)[0] == ['Acceptance Notes: names no .md/.txt notes file']
    assert b.lint(GOOD, notes_require_file=True) == ([], [])


@pytest.mark.parametrize('data', [
    {'brief_lint': {'mode': 'strict'}}, {'brief_lint': {'unknown': 1}}, {'brief_lint': []},
    {'brief_lint': {'restricted': 'network'}}, {'brief_lint': {'fields': {'Domain': []}}},
    {'brief_lint': {'max_steps': 0}}, {'brief_lint': {'result_max_words': True}},
    {'brief_lint': {'notes_require_file': 'yes'}}, {'context_files': 'AGENTS.md'}, {'context_files': ['']},
])
def test_invalid_brief_config_names_valid_shapes(b, data):
    b.configure(**data)
    with pytest.raises(SystemExit, match='brief_lint: .* mode off|warn|enforce.*context_files'):
        b.cli('brief-lint', '--brief', b.brief(GOOD))


def assign(b, path, *extra):
    return b.cli('assign', '--id', 't1', '--executor', 'agy', '--brief', path, *extra)


def test_assign_unconfigured_is_silent_and_warn_mode_never_refuses(b, capsys):
    path = b.brief('No shape at all.\n')
    assert assign(b, path) == 0
    captured = capsys.readouterr()
    assert captured.out == captured.err == ''
    b.configure(brief_lint={})
    assert b.cli('assign', '--id', 't2', '--executor', 'agy', '--brief', path) == 0
    err = capsys.readouterr().err
    assert 'brief-lint: WARN brief expected exactly one "Result:"' in err and 'ERROR' not in err
    assert b.xq.state(b.repo)['t2']['status'] == 'running'


def test_assign_enforce_refuses_until_forced_and_off_is_silent(b, capsys):
    path = b.brief('No shape at all.\n')
    b.configure(brief_lint={'mode': 'enforce'})
    with pytest.raises(SystemExit, match='brief-lint refused .*pass --force to bypass'):
        assign(b, path, '--queued')
    assert 'brief-lint: ERROR' in capsys.readouterr().err
    assert b.xq.events(b.repo) == []
    assert assign(b, path, '--force') == 0
    assert b.xq.events(b.repo)[-1]['force'] is True
    capsys.readouterr()
    b.configure(brief_lint={'mode': 'off'})
    assert b.cli('assign', '--id', 't3', '--executor', 'agy', '--brief', path) == 0
    assert capsys.readouterr().err == ''


def test_assign_enforce_accepts_clean_brief(b, capsys):
    b.configure(brief_lint={'mode': 'enforce'})
    assert assign(b, b.brief(GOOD), '--queued') == 0
    assert capsys.readouterr().err == ''


def test_compose_writes_context_then_brief_and_assign_records_prompt(b, capsys):
    (b.repo / 'AGENTS.md').write_text('Agent rules.\n', encoding='utf-8')
    (b.repo / 'docs').mkdir()
    (b.repo / 'docs/CONTEXT.md').write_text('Generated context.', encoding='utf-8')
    b.configure(context_files=['AGENTS.md', 'docs/CONTEXT.md'])
    path = b.brief(GOOD)
    assert b.cli('compose', '--brief', path) == 0
    out = Path(capsys.readouterr().out.strip())
    assert out == b.repo / '.agents/review/t1.prompt.md'
    text = out.read_text(encoding='utf-8')
    digest = hashlib.sha256(b'Agent rules.\n').hexdigest()[:12]
    assert text.startswith('===== BEGIN xq context AGENTS.md sha256:' + digest + ' =====\nAgent rules.\n'
                           '===== END xq context AGENTS.md =====\n')
    assert text.index('docs/CONTEXT.md') < text.index('BEGIN xq brief .agents/briefs/t1.md') < text.index('Result:')
    assert b.xq.events(b.repo) == []  # compose records nothing
    assert assign(b, path, '--prompt', str(out.relative_to(b.repo))) == 0
    event = b.xq.events(b.repo)[-1]
    assert event['prompt'] == str(out) and event['prompt_sha256'] == hashlib.sha256(out.read_bytes()).hexdigest()
    custom = b.repo / 'prompts/p.md'
    assert b.cli('compose', '--brief', path, '--out', 'prompts/p.md') == 0
    assert custom.read_text(encoding='utf-8') == text


def test_compose_without_context_files_and_missing_prompt(b, capsys):
    path = b.brief(GOOD)
    assert b.cli('compose', '--brief', path) == 0
    text = Path(capsys.readouterr().out.strip()).read_text(encoding='utf-8')
    assert text.startswith('===== BEGIN xq brief .agents/briefs/t1.md') and 'context' not in text
    with pytest.raises(SystemExit, match='prompt not found'):
        assign(b, path, '--prompt', 'nope.md')
    assert b.xq.events(b.repo) == []


@pytest.mark.parametrize('entry, message', [
    ('MISSING.md', 'context file not found: MISSING.md'),
    ('/etc/hosts', 'must be repo-relative'),
    ('../outside.md', 'must be repo-relative'),
    ('link.md', 'unsafe symlink'),
    ('folder', 'cannot read context file folder'),
])
def test_compose_refuses_unsafe_or_missing_context_files(b, entry, message):
    (b.repo / 'real.md').write_text('x', encoding='utf-8')
    (b.repo / 'folder').mkdir()
    if hasattr(os, 'symlink'):
        os.symlink(str(b.repo / 'real.md'), str(b.repo / 'link.md'))
    b.configure(context_files=[entry])
    path = b.brief(GOOD)
    with pytest.raises(SystemExit, match=message):
        b.cli('compose', '--brief', path)
    assert not (b.repo / '.agents/review/t1.prompt.md').exists()


@pytest.mark.parametrize('out, message', [
    ('.agents/ledger.jsonl', 'xq state or config'),
    ('.agents/ledger.lock', 'xq state or config'),
    ('.agents/config.json', 'xq state or config'),
    ('.AGENTS/Config.JSON', 'xq state or config'),
    ('.agents/config.json.tmp', 'xq state or config'),
    ('src/existing.py', 'exists and is not a \\*.prompt.md'),
])
def test_compose_out_never_overwrites_state_config_or_other_files(b, out, message):
    b.configure(context_files=[])
    path = b.brief(GOOD)
    assert assign(b, path, '--queued') == 0
    ledger = (b.repo / '.agents/ledger.jsonl').read_bytes()
    config = (b.repo / '.agents/config.json').read_bytes()
    (b.repo / 'src').mkdir()
    (b.repo / 'src/existing.py').write_text('keep\n', encoding='utf-8')
    with pytest.raises(SystemExit, match=message):
        b.cli('compose', '--brief', path, '--out', out)
    assert (b.repo / '.agents/ledger.jsonl').read_bytes() == ledger
    assert (b.repo / '.agents/config.json').read_bytes() == config
    assert (b.repo / 'src/existing.py').read_text(encoding='utf-8') == 'keep\n'


def test_compose_out_rewrites_existing_prompt_file(b, capsys):
    path = b.brief(GOOD)
    target = b.repo / 'prompts/t1.prompt.md'
    target.parent.mkdir()
    target.write_text('old', encoding='utf-8')
    assert b.cli('compose', '--brief', path, '--out', 'prompts/t1.prompt.md') == 0
    assert target.read_text(encoding='utf-8').startswith('===== BEGIN xq brief')
    assert b.cli('compose', '--brief', path) == 0  # the default review path can be recomposed
    assert b.cli('compose', '--brief', path) == 0


def test_longer_fence_keeps_inner_fence_and_acceptance(b):
    text = ('Result: x\n\n````\nResult: y\n```\nResult: z\n````\n'
            '## Acceptance\nFiles: a.py\nChecks: pytest\nNotes: n.md\n')
    assert b.lint(text) == ([], [])
    tilde = 'Result: x\n~~~~\n~~~ python\nResult: y\n~~~~~\n## Acceptance\nFiles: a.py\nChecks: pytest\nNotes: n.md\n'
    assert b.lint(tilde) == ([], [])
    assert b.lint(text.replace('\n````\n## Acc', '\n```` x\n## Acc'))[0]  # an info string never closes a fence


def test_bom_does_not_hide_first_line_field(b, capsys):
    path = b.repo / '.agents/briefs/bom.md'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'\xef\xbb\xbfResult: does a thing.\n\n## Acceptance\nFiles: a.py\nChecks: pytest\nNotes: n.md\n')
    assert b.cli('brief-lint', '--brief', str(path)) == 0
    b.configure(context_files=['ctx.md'])
    (b.repo / 'ctx.md').write_bytes(b'\xef\xbb\xbfContext.\n')
    assert b.cli('compose', '--brief', str(path)) == 0
    text = Path(capsys.readouterr().out.strip().splitlines()[-1]).read_text(encoding='utf-8')
    assert '﻿' not in text and '=====\nContext.\n' in text


def test_wildcard_files_and_brace_globs(b):
    errors, _ = b.lint('Result: x\n## Acceptance\nFiles: *\nChecks: pytest\nNotes: n.md\n')
    assert errors == ['Acceptance Files: "*" matches every file; name the paths or a narrower glob']
    errors, _ = b.lint('Result: x\n## Acceptance\nFiles: **/*\nChecks: c\nNotes: n\n')
    assert errors and 'matches every file' in errors[0]
    brief = b.xq.BriefModel('Result: x\n## Acceptance\nFiles: src/{a,b}.py, docs/*.md\nChecks: c\nNotes: n\n')
    assert brief.files() == ['src/{a,b}.py', 'docs/*.md']
