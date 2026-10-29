#!/usr/bin/env python3
"""Read-only, bounded log query installed on the 74 host by the operator."""

import json
import re
import subprocess
import sys


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in ('--trace', '--mobi'):
        raise SystemExit('usage: query-74-logs --trace|--mobi identifier')
    kind, value = sys.argv[1], sys.argv[2]
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value):
        raise SystemExit('invalid identifier')
    pattern = re.compile(re.escape(value)) if kind == '--trace' else re.compile(
        r'(?i)\bmobi(?:_id|_no)?["\s:=]+["\s]*' + re.escape(value) + r'(?![A-Za-z0-9_-])')
    for container in ('helix-cloud', 'helix-gateway', 'helix-mobilization', 'helix-authz'):
        result = subprocess.run(
            ['docker', 'logs', '--since', '24h', '--tail', '10000', container],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=12,
        )
        if result.returncode:
            raise SystemExit('log source unavailable: ' + container)
        matches = [line[:4000] for line in result.stdout.splitlines() if pattern.search(line)]
        if matches:
            print(json.dumps({'container': container, 'query': kind, 'value': value,
                              'window': '24h', 'lines': matches[-50:]}, ensure_ascii=False))


if __name__ == '__main__':
    main()
