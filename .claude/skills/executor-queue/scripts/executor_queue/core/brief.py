"""Brief model, brief lint and prompt composition (CORE)."""

import hashlib
import os
from pathlib import Path
import re

from . import paths, seams
from .paths import repo_relative
from .util import display_name, fail


BRIEF_LINT_MODES = ('off', 'warn', 'enforce')
BRIEF_LINT_DEFAULTS = dict(mode='warn', restricted=[], handoff_marker='lead runs', fields={}, result_max_words=45,
                           max_steps=4, notes_require_file=False)
ACCEPTANCE_LABELS = ('Files', 'Checks', 'Notes')
ACCEPTANCE_KEYS = {label.casefold(): label for label in ACCEPTANCE_LABELS}
BRIEF_MAX_BYTES = 1 << 20
BRIEF_FENCE_RE = re.compile(r'^\s{0,3}(`{3,}|~{3,})(.*)$')
BRIEF_COMMENT_RE = re.compile(r'<!--.*?-->', re.S)
BRIEF_HEADING_RE = re.compile(r'^\s{0,3}(#{1,2})\s+(.*?)\s*#*\s*$')
BRIEF_FIELD_RE = re.compile(r'^\s*(?:[-*]\s+)?\**([A-Za-z][A-Za-z0-9 _/-]{0,40}?)\**:\**\s*(.*?)\s*$')
BRIEF_BULLET_RE = re.compile(r'^\s*[-*+]\s+(.*?)\s*$')
BRIEF_STEP_RE = re.compile(r'^\s{0,3}\d{1,3}[.)]\s+\S')
WILDCARD_ALL_RE = re.compile(r'[*/\\.]*\*[*/\\.]*')
BRIEF_LIST_ITEM_RE = re.compile(r'(?:\{[^}]*\}|[^,{])+')  # comma list; a {a,b} brace group stays one item
PLACEHOLDER_RE = re.compile(r'[\s.…?*_`~-]*|(tbd|tba|todo|fixme|none|n/?a|xxx|placeholder|\.\.\.)[\s.!?]*', re.I)


class BriefModel:
    """A brief markdown parsed once (fenced code blocks and HTML comments removed, line numbers kept).

    results: [(line, value)] for every `Result:` line; fields: header `Key: value` lines before the first ## heading
    ({key: (line, value)}); steps: line numbers of numbered steps outside Acceptance; acceptance: line numbers of
    every `## Acceptance` heading; acceptance_last: the last such heading ends the brief; sections:
    {'Files'|'Checks'|'Notes': (line, inline value, [bullet items])} from the last Acceptance section;
    files(): the declared repo-relative paths/globs (stage 2 checks the diff against them); lines: the stripped text.
    """

    def __init__(self, text):
        self.lines = strip_brief_markup(text).split('\n')
        self.results, self.fields, self.steps, self.acceptance, self.sections = [], {}, [], [], {}
        self.acceptance_last = False
        heading_seen, in_acceptance, label = False, False, None
        for number, line in enumerate(self.lines, 1):
            heading = BRIEF_HEADING_RE.match(line)
            if heading:
                heading_seen, label = heading_seen or len(heading.group(1)) == 2, None
                in_acceptance = heading.group(2).strip('*: ').casefold() == 'acceptance'
                self.acceptance += [number] if in_acceptance else []
                self.acceptance_last = in_acceptance
                if in_acceptance:
                    self.sections = {}
                continue
            field = BRIEF_FIELD_RE.match(line)
            name = field.group(1).strip() if field else None
            name = ACCEPTANCE_KEYS.get(name.casefold(), name) if name else None
            if name and name.casefold() == 'result':
                self.results.append((number, field.group(2)))
            if in_acceptance:
                label = self.acceptance_line(number, line, name, field, label)
            elif field and not heading_seen:
                self.fields.setdefault(name, (number, field.group(2)))
            elif BRIEF_STEP_RE.match(line):
                self.steps.append(number)

    def acceptance_line(self, number, line, name, field, label):
        """Collect one Acceptance line; returns the label that following bullets belong to."""
        if name in ACCEPTANCE_LABELS:
            self.sections.setdefault(name, (number, field.group(2), []))
            return name
        bullet = BRIEF_BULLET_RE.match(line)
        if label and bullet:
            self.sections[label][2].append(bullet.group(1))
            return label
        return label if not line.strip() else None

    def section_items(self, label):
        _, value, bullets = self.sections.get(label, (0, '', []))
        items = BRIEF_LIST_ITEM_RE.findall(value) + bullets
        return [x.strip().strip('`"\'').strip() for x in items if x.strip().strip('`"\'').strip()]

    def files(self):
        return self.section_items('Files')


def strip_brief_markup(text):
    """Blank HTML comments and fenced code blocks, keeping one output line per input line."""
    text = BRIEF_COMMENT_RE.sub(lambda m: '\n' * m.group(0).count('\n'), text.replace('\r\n', '\n'))
    lines, fence = [], None
    for line in text.split('\n'):
        marker = BRIEF_FENCE_RE.match(line)
        if fence is None and marker and not (marker.group(1)[0] == '`' and '`' in marker.group(2)):
            fence = marker.group(1)
        elif fence is not None and fence_closes(marker, fence):
            fence = None
        else:
            lines.append('' if fence is not None else line)
            continue
        lines.append('')
    return '\n'.join(lines)


def fence_closes(marker, fence):
    """CommonMark: a closing fence uses the opening character, is at least as long and carries no info string."""
    return bool(marker) and marker.group(1)[0] == fence[0] and len(marker.group(1)) >= len(fence) \
        and not marker.group(2).strip()


def read_brief_text(path):
    with open(str(path), 'rb') as stream:
        raw = stream.read(BRIEF_MAX_BYTES + 1)
    if len(raw) > BRIEF_MAX_BYTES:
        fail('brief is larger than {} bytes: {}'.format(BRIEF_MAX_BYTES, path))
    return raw.decode('utf-8-sig', errors='replace')  # an editor's BOM must not hide a line-1 field


def placeholder(value):
    return bool(PLACEHOLDER_RE.fullmatch(value.strip()))


def lint_result(brief, settings):
    errors, warnings = [], []
    if len(brief.results) != 1:
        errors.append('expected exactly one "Result:" line, found {}{}'.format(
            len(brief.results), ' (lines {})'.format(', '.join(str(n) for n, _ in brief.results))
            if brief.results else ''))
    for number, value in brief.results[:1]:
        if placeholder(value):
            errors.append('line {}: Result is a placeholder; state the one observable outcome'.format(number))
            continue
        words = len(value.split())
        if ';' in value or ' and also ' in ' {} '.format(value.casefold()):
            warnings.append('line {}: Result joins several outcomes (";" or "and also"); split the task'.format(number))
        if words > settings['result_max_words']:
            warnings.append('line {}: Result has {} words (max {}); split the task'.format(
                number, words, settings['result_max_words']))
    if len(brief.steps) > settings['max_steps']:
        warnings.append('{} numbered steps (max {}); split the task'.format(len(brief.steps), settings['max_steps']))
    return errors, warnings


def lint_acceptance(brief, settings):
    if not brief.acceptance:
        return ['missing final "## Acceptance" section with Files:, Checks: and Notes:']
    errors = []
    if len(brief.acceptance) > 1:
        errors.append('{} "## Acceptance" sections (lines {}); keep one'.format(
            len(brief.acceptance), ', '.join(map(str, brief.acceptance))))
    if not brief.acceptance_last:
        errors.append('"## Acceptance" (line {}) must be the last section'.format(brief.acceptance[-1]))
    for label in ACCEPTANCE_LABELS:
        if label not in brief.sections:
            errors.append('Acceptance has no "{}:" line'.format(label))
        elif all(placeholder(x) and not WILDCARD_ALL_RE.fullmatch(x) for x in brief.section_items(label)):
            errors.append('line {}: Acceptance {}: is empty or a placeholder'.format(brief.sections[label][0], label))
    for path in brief.files():
        if WILDCARD_ALL_RE.fullmatch(path):
            errors.append('Acceptance Files: "{}" matches every file; name the paths or a narrower glob'.format(path))
        elif path.startswith(('/', '\\', '~')) or re.match(r'^[A-Za-z]:', path) or '..' in re.split(r'[\\/]', path):
            errors.append('Acceptance Files: "{}" must be a repo-relative path or glob'.format(display_name(path)))
    notes = ' '.join(brief.section_items('Notes'))
    if settings['notes_require_file'] and 'Notes' in brief.sections and not re.search(r'\S\.(md|txt)\b', notes):
        errors.append('Acceptance Notes: names no .md/.txt notes file')
    return errors


def lint_restricted(brief, settings):
    """Each configured restricted word/phrase used on a line without the handoff marker."""
    marker = settings['handoff_marker'].casefold()
    errors = []
    for phrase in settings['restricted']:
        pattern = re.compile(r'(?<!\w)' + re.escape(phrase.strip()) + r'(?!\w)', re.I)
        hits = [n for n, line in enumerate(brief.lines, 1) if pattern.search(line) and marker not in line.casefold()]
        if hits:
            errors.append('restricted "{}" on line(s) {} without a "{}" handoff on that line'.format(
                phrase.strip(), ', '.join(map(str, hits[:10])), settings['handoff_marker']))
    return errors


def lint_fields(brief, settings):
    errors = []
    for name, allowed in sorted(settings['fields'].items()):
        number, value = brief.fields.get(name, (0, None))
        if value is None:
            errors.append('missing header field "{}:" (allowed: {})'.format(name, ', '.join(allowed)))
        elif value not in allowed:
            errors.append('line {}: {}: "{}" is not one of: {}'.format(
                number, name, display_name(value[:120]), ', '.join(allowed)))
    return errors


def lint_brief(text, settings):
    """(errors, warnings) for a brief text under brief_lint settings (see brief_lint_settings)."""
    brief = BriefModel(text)
    errors, warnings = lint_result(brief, settings)
    errors += lint_acceptance(brief, settings) + lint_restricted(brief, settings) + lint_fields(brief, settings)
    return errors, warnings


def context_file_path(root, value):
    """A configured context file: repo-relative, no traversal; safe_open refuses symlinks and non-regular files."""
    if not repo_relative(value):
        fail('context_files entry must be repo-relative without "..": ' + display_name(value))
    return root / value


def compose_prompt(root, brief_text, brief_name, context_files, extra_context=()):
    """The launch prompt: each context file between delimiters naming its path and sha256 prefix, then the brief.
    extra_context (compose --context) follows the configured context_files; a file already included is skipped."""
    blocks, included = [], set()
    sources = [(value, 'configured in .agents/config.json context_files') for value in context_files]
    sources += [(value, '--context') for value in extra_context]
    for value, origin in sources:
        path = context_file_path(root, value)
        key = os.path.normcase(os.path.normpath(value))
        if key in included:
            continue
        included.add(key)
        blocks.append(delimited('context', value, read_context_file(path, value, origin)))
    blocks.append(delimited('brief', brief_name, brief_text))
    return '\n'.join(blocks)


def read_context_file(path, value, origin):
    """One context file's text: a regular, non-symlinked file of at most BRIEF_MAX_BYTES."""
    try:
        with paths.safe_open(path, os.O_RDONLY, 'rb') as stream:
            raw = stream.read(BRIEF_MAX_BYTES + 1)
    except FileNotFoundError:
        fail('context file not found: {} ({})'.format(value, origin))
    except OSError as exc:
        fail('cannot read context file {}: {}'.format(value, exc))
    if len(raw) > BRIEF_MAX_BYTES:
        fail('context file is larger than {} bytes: {}'.format(BRIEF_MAX_BYTES, value))
    return raw.decode('utf-8-sig', errors='replace')


def compose_out_refusal(root, out):
    """Why compose must not write `out` (None when it may): never xq state or config, and an existing file only when
    it is a `*.prompt.md`, so a typo cannot truncate the ledger, the config or a tracked file."""
    try:
        relative = '/' + Path(os.path.normpath(str(out))).relative_to(Path(os.path.normpath(str(root)))).as_posix()
    except ValueError:
        relative = None
    review = '/.agents/review/'
    folded = (relative or '').casefold()
    if relative and (folded == '/.agents/config.json' or any(
            folded == p.casefold() or (p.endswith('/') and folded.startswith(p.casefold()))
            for p in seams.private_paths(root) if p != review)):
        return '--out {} is xq state or config; choose a *.prompt.md path'.format(relative.lstrip('/'))
    if os.path.lexists(str(out)) and not out.name.casefold().endswith('.prompt.md'):
        return '--out {} exists and is not a *.prompt.md file; refusing to overwrite it'.format(out)
    return None


def delimited(kind, name, text):
    digest = hashlib.sha256(text.encode('utf-8')).hexdigest()[:12]
    return '===== BEGIN xq {} {} sha256:{} =====\n{}{}===== END xq {} {} =====\n'.format(
        kind, name, digest, text, '' if text.endswith('\n') else '\n', kind, name)


def file_sha256(path):
    digest = hashlib.sha256()
    with open(str(path), 'rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 16), b''):
            digest.update(chunk)
    return digest.hexdigest()
