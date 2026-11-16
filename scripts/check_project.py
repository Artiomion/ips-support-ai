#!/usr/bin/env python3
"""Offline structural checks for the planning package, not application tests."""

import json
import re
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
errors = []


def require(condition, message):
    if not condition:
        errors.append(message)


def validate(value, schema, location, schemas):
    if '$ref' in schema:
        return validate(value, schemas[schema['$ref'].split('/')[-1]], location, schemas)
    if 'anyOf' in schema:
        # Our examples use only explicit null unions.
        if value is None:
            require(any(x.get('type') == 'null' for x in schema['anyOf']), location)
            return
        return validate(value, next(x for x in schema['anyOf'] if x.get('type') != 'null'), location, schemas)
    kind = schema.get('type')
    types = {'object': dict, 'array': list, 'string': str, 'integer': int,
             'number': (int, float), 'boolean': bool, 'null': type(None)}
    require(kind in types and isinstance(value, types.get(kind, object)), f'{location}: type {kind}')
    if kind not in types or not isinstance(value, types[kind]):
        return
    if 'enum' in schema:
        require(value in schema['enum'], f'{location}: enum')
    if kind == 'object':
        for key in schema.get('required', []):
            require(key in value, f'{location}: missing {key}')
        props = schema.get('properties', {})
        if schema.get('additionalProperties') is False:
            require(not (value.keys() - props.keys()), f'{location}: additional fields')
        for key in value.keys() & props.keys():
            validate(value[key], props[key], f'{location}.{key}', schemas)
    if kind == 'array':
        for i, item in enumerate(value):
            validate(item, schema['items'], f'{location}[{i}]', schemas)
    if kind == 'string':
        require(len(value) >= schema.get('minLength', 0), f'{location}: too short')
        require(len(value) <= schema.get('maxLength', len(value)), f'{location}: too long')
    if kind in ('integer', 'number'):
        require(not isinstance(value, bool), f'{location}: boolean is not a number')
        require(value >= schema.get('minimum', value), f'{location}: minimum')
        require(value <= schema.get('maximum', value), f'{location}: maximum')


def main():
    tasks = json.loads((ROOT / 'docs/tasks.json').read_text())
    require(len(tasks) == 30, 'Expected 30 task cards')
    require(len({x['id'] for x in tasks}) == len(tasks), 'Duplicate task IDs')
    for owner in 'ABC':
        rows = [x for x in tasks if x['owner'] == owner]
        require(len(rows) == 10, f'{owner}: expected 10 cards')
        require(sum(x['points'] for x in rows) == 40, f'{owner}: expected 40 points')
        require(sum(x['points'] for x in rows if x['phase'] != 'M5') == 32,
                f'{owner}: expected 32 MVP points')
        plan = (ROOT / f'docs/plans/track-{owner.lower()}.md').read_text()
        for task in rows:
            require(f"## {task['id']} —" in plan, f"Missing card: {task['id']}")
            require(task['acceptance'] in plan, f"Acceptance drift: {task['id']}")
    for phase in ['M0', 'M1', 'M2', 'M3', 'M4', 'M5']:
        weights = [sum(x['points'] for x in tasks if x['owner'] == owner and x['phase'] == phase)
                   for owner in 'ABC']
        require(len(set(weights)) == 1, f'Unequal phase load: {phase} {weights}')
    team = json.loads((ROOT / 'docs/team.json').read_text())
    require({x['role'] for x in team['members']} == set('ABC'), 'Invalid team roles')
    spec = json.loads((ROOT / 'contracts/openapi.json').read_text())
    schemas = spec['components']['schemas']
    refs = re.findall(r'"\$ref":\s*"([^"]+)"', json.dumps(spec))
    for reference in refs:
        value = spec
        try:
            if not reference.startswith('#/'):
                raise KeyError(reference)
            for part in reference[2:].split('/'):
                value = value[part.replace('~1', '/').replace('~0', '~')]
        except (KeyError, TypeError):
            require(False, f'Unresolved reference: {reference}')
    operations = []
    for route, methods in spec['paths'].items():
        for method, op in methods.items():
            operations.append(op['operationId'])
            require(op['x-owner'] in 'BC', f'{route}: invalid owner')
            matching = [task for task in tasks if task['id'] == op.get('x-task')]
            require(len(matching) == 1, f'{route}: operation has no task')
            if matching:
                require(matching[0]['owner'] == op['x-owner'], f'{route}: owner mismatch')
                require((matching[0]['phase'] == 'M5') == (op['x-phase'] == 'M5'),
                        f'{route}: task phase mismatch')
            actual = {p['name'] for p in op.get('parameters', []) if p['in'] == 'path'}
            require(actual == set(re.findall(r'\{([^}]+)\}', route)), f'{route}: path params')
            require(op['x-phase'] in ['M4', 'M5'], f'{route}: phase missing')
            if op.get('security') == [{'staffCookie': []}] and method != 'get':
                require(any(p['name'] == 'X-CSRF-Token' for p in op.get('parameters', [])),
                        f'{route}: CSRF header missing')
    require(len(operations) == len(set(operations)), 'Duplicate operationId')
    require('internal_note' not in schemas['PublicMessage']['properties']['kind']['enum'],
            'Internal message allowed in public DTO')
    mappings = {'conversation': 'Conversation', 'public-message': 'PublicMessage',
                'internal-note': 'StaffMessage', 'waiting-ticket': 'TicketSummary', 'error': 'Error'}
    for filename, schema in mappings.items():
        value = json.loads((ROOT / f'contracts/examples/{filename}.json').read_text())
        validate(value, schemas[schema], filename, schemas)
    manifest = json.loads((ROOT / '.ai/tooling.json').read_text())
    require(bool(re.fullmatch('[0-9a-f]{40}', manifest['source']['commit'])), 'Unpinned skills')
    for skill in manifest['skills']:
        require(bool(re.fullmatch('[0-9a-f]{40}', skill['skill_md_blob_sha'])), f"Unpinned {skill['name']}")
    # Verify local Markdown links; inline code paths may describe planned files.
    for file in ROOT.rglob('*.md'):
        if any(part in ['.git', '.agents', 'node_modules', '.venv'] for part in file.parts):
            continue
        text = file.read_text()
        for target in re.findall(r'(?<!!)\[[^\]]+\]\(([^)]+)\)', text):
            if '://' in target or target.startswith('#'):
                continue
            path = unquote(target.split('#')[0].strip('<>'))
            require((file.parent / path).exists(), f'{file.relative_to(ROOT)}: broken link {target}')
        require('/Users/artemsotnikov/' not in text, f'{file.relative_to(ROOT)}: personal path')
    require((ROOT / 'AGENTS.md').exists(), 'Missing root AGENTS')
    if errors:
        for error in errors:
            print(f'FAIL: {error}', file=sys.stderr)
        return 1
    print(f'PASS: {len(tasks)} cards; A/B/C 40 points each (32 MVP); all phases balanced')
    print(f'PASS: {len(operations)} operations, {len(schemas)} schemas, references and examples')
    print('PASS: local Markdown links, portable paths, pinned AI manifest')
    print('This checker verifies planning files only; it does not test the application.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
