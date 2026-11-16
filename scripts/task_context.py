#!/usr/bin/env python3
"""Print one card and its owned API operations instead of the entire spec."""

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('task_id')
parser.add_argument('--contracts', action='store_true', help='Include owned operations and used schemas')
args = parser.parse_args()
tasks = json.loads((ROOT / 'docs/tasks.json').read_text())
task = next((t for t in tasks if t['id'] == args.task_id.upper()), None)
if task is None:
    parser.error('Unknown task ID')
print(json.dumps(task, ensure_ascii=False, indent=2))
if args.contracts:
    spec = json.loads((ROOT / 'contracts/openapi.json').read_text())
    paths = {}
    names = {'schemas': set(), 'responses': set()}

    def references(value):
        if isinstance(value, dict):
            if '$ref' in value:
                category, name = value['$ref'].split('/')[-2:]
                if name not in names[category]:
                    names[category].add(name)
                    references(spec['components'][category][name])
            for child in value.values():
                references(child)
        elif isinstance(value, list):
            for child in value:
                references(child)

    for path, methods in spec['paths'].items():
        for method, operation in methods.items():
            if operation['x-task'] == task['id']:
                paths.setdefault(path, {})[method] = operation
                references(operation)
    components = {category: {name: spec['components'][category][name]
                            for name in sorted(selected)}
                  for category, selected in names.items() if selected}
    print(json.dumps({'paths': paths, 'components': components}, ensure_ascii=False, indent=2))
