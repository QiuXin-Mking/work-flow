"""Bounded execution errors without credential-bearing command lines."""
import os
import re
import subprocess


def safe_error(exc: Exception) -> str:
    if isinstance(exc, subprocess.TimeoutExpired):
        return f'TimeoutExpired: command exceeded {exc.timeout} seconds'
    if isinstance(exc, subprocess.CalledProcessError):
        return f'CalledProcessError: command exited {exc.returncode}'
    text = str(exc)
    for key,value in os.environ.items():
        if value and any(word in key.upper() for word in ('TOKEN','SECRET','PASSWORD','API_KEY')):
            text = text.replace(value,'[REDACTED]')
    text = re.sub(r'(?i)(authorization|cookie)([:=]\s*)[^\r\n]+',r'\1\2[REDACTED]',text)
    text = re.sub(r'https?://[^/\s:@]+:[^/\s@]+@','https://[REDACTED]@',text)
    return (type(exc).__name__+': '+text)[:1000]
