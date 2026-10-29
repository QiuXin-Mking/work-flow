"""Trusted coordinator SSH adapter. Agent never receives SSH keys or raw shell input."""
import os
import re
import subprocess


def read_trace_logs(info: dict) -> dict | None:
    trace = str(info.get('traceId') or '').strip()
    mobi = str(info.get('mobi') or '').strip()
    if not trace and (info.get('reproduction') != 'always' or not mobi):
        return None
    kind, value = ('trace', trace) if trace else ('mobi', mobi)
    user = os.getenv('DIAG_SSH_USER','')
    key = os.getenv('DIAG_SSH_KEY_PATH','')
    known_hosts = os.getenv('DIAG_SSH_KNOWN_HOSTS', '')
    # Operator config is a fixed, trusted remote executable, not a model command.
    query = os.getenv('DIAG_LOG_QUERY_PROGRAM','')
    if not all((user,key,query,known_hosts)):
        return None
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',user) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',value):
        raise ValueError('invalid SSH identity or trace id')
    if not re.fullmatch(r'/[A-Za-z0-9_./-]+',query):
        raise ValueError('log query must be an operator-configured absolute executable')
    result = subprocess.run(['ssh','-n','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',
                             '-o','UserKnownHostsFile='+known_hosts,
                             '-o','ConnectTimeout=10','-o','IdentitiesOnly=yes',
                             '-i',key,user+'@192.168.2.74',query,'--'+kind,value],
                            capture_output=True,text=True,check=True,timeout=60)
    if not result.stdout.strip():
        return None
    output = re.sub(r'(?i)(password|token|secret|authorization)(\s*[:=]\s*)([^\s,;]+)',
                    r'\1\2[REDACTED]', result.stdout[-20000:])
    return {'host':'192.168.2.74','trace_id':trace or None,
            'query_kind':kind,'query_value':value,'output':output}
