"""Imported history (xq.py import): mapping source rows to read-only import events (CORE)."""

import hashlib
import json
import os

from . import paths
from .config import check_import_map, IMPORT_EVENTS, IMPORT_MAP_SHAPE, IMPORT_STATES
from .ledger import replay_events
from .state import parse_timestamp
from .util import display_name, fail, ID_RE, OPTION_RE, SHA_RE


def import_mapping(text, data):
    """The --map JSON, else config "import_map" (validated by config()); fails naming the valid shape."""
    if text is None:
        if 'import_map' not in data:
            fail('import needs --map JSON or "import_map" in .agents/config.json: ' + IMPORT_MAP_SHAPE)
        return data['import_map']
    try:
        mapping = json.loads(text)
        check_import_map(mapping)
    except (ValueError, RecursionError):
        fail('invalid --map; expected ' + IMPORT_MAP_SHAPE)
    return mapping


def printable(value):
    return len(value) <= 4096 and value.isprintable()


# Optional copied fields: (map key, import event key, validity check); an invalid value is dropped.
IMPORT_COPIED = (('executor', 'source_executor', OPTION_RE.fullmatch), ('brief', 'brief', printable),
                 ('by', 'by', lambda v: len(v) <= 200 and v.isprintable()), ('model', 'model', OPTION_RE.fullmatch),
                 ('effort', 'effort', OPTION_RE.fullmatch))
# Optional checked fields: (map key, value map, allowed values); an invalid value makes the line malformed.
IMPORT_CHECKED = (('status', 'status_values', IMPORT_STATES), ('event', 'event_values', IMPORT_EVENTS),
                  ('verdict', None, ('pass', 'fixes', 'reject')))


def import_event(row, mapping):
    """One parsed source row as an xq 'import' event (no provenance yet), or (None, reason) when malformed."""
    if not isinstance(row, dict):
        return None, 'not a JSON object'
    tid = row.get(mapping['id'])
    if not isinstance(tid, str) or not ID_RE.fullmatch(tid):
        return None, 'missing or invalid task id in "{}"'.format(mapping['id'])
    event = {'event': 'import', 'id': tid}
    for field, key, valid in IMPORT_COPIED:
        value = row.get(mapping.get(field))
        if isinstance(value, str) and value and valid(value):
            event[key] = value
    for field, values, allowed in IMPORT_CHECKED:
        if mapping.get(field) not in row:
            continue
        value = row[mapping[field]]
        mapped = mapping.get(values, {}).get(value, value) if isinstance(value, str) else None
        if mapped not in allowed:
            return None, '{} {} is not {}{}'.format(field, display_name(str(value))[:80], '|'.join(allowed),
                                                    '; map it in "{}"'.format(values) if values else '')
        event['import_event' if field == 'event' else field] = mapped
    if mapping.get('commit') in row:
        value = row[mapping['commit']]
        if not isinstance(value, str) or not SHA_RE.fullmatch(value):
            return None, 'commit in "{}" is not a 7-64 character hex SHA'.format(mapping['commit'])
        event['commit'] = value
    if mapping.get('ts') in row:
        try:
            parse_timestamp(row[mapping['ts']])
        except (ValueError, TypeError):
            return None, 'invalid zoned ISO timestamp in "{}"'.format(mapping['ts'])
        event['ts'] = row[mapping['ts']]
    return event, None


def import_plan(lines, source, mapping, rows):
    """(new import events, [(line, reason)], count already imported) for (line number, raw bytes) pairs.
    Provenance is {source, line, sha256 of the raw line}; a sha256 already in the ledger is skipped (idempotent)."""
    seen = {e['imported'].get('sha256') for e in rows
            if e.get('event') == 'import' and isinstance(e.get('imported'), dict)}
    owners = {tid.casefold(): (tid, bool(task.get('imported'))) for tid, task in replay_events(rows).items()}
    new, problems, skipped = [], [], 0
    for number, raw in lines:
        digest = hashlib.sha256(raw).hexdigest()
        if digest in seen:
            skipped += 1
            continue
        try:
            event, reason = import_event(json.loads(raw.decode('utf-8-sig')), mapping)  # tolerate a BOM
        except (ValueError, RecursionError):
            event, reason = None, 'not valid UTF-8 JSON'
        owner = owners.get(event['id'].casefold()) if event else None
        if owner and (not owner[1] or owner[0] != event['id']):
            event, reason = None, 'task id {} collides with an xq task {}'.format(event['id'], owner[0])
        if event is None:
            problems.append((number, reason))
            continue
        event['imported'] = {'source': source, 'line': number, 'sha256': digest}
        seen.add(digest)
        owners[event['id'].casefold()] = (event['id'], True)
        new.append(event)
    return new, problems, skipped


def jsonl_lines(path):
    """(line number, raw bytes without the line ending) for each non-blank line of a JSONL file."""
    try:
        with paths.safe_open(path, os.O_RDONLY, 'rb') as stream:
            return [(number, line.rstrip(b'\r\n')) for number, line in enumerate(stream, 1) if line.strip()]
    except OSError as exc:
        fail('cannot read {}: {}'.format(path, exc))
