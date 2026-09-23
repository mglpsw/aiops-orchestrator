#!/usr/bin/env python3
"""Development-only regeneration. Does not reseal or requalify the pack."""
from pathlib import Path
import json
ROOT=Path(__file__).resolve().parents[1]
HEADER='# AOCM-MPACK — contexto de conversa\n\nGENERATED_VIEW. Owner: arquivos listados abaixo. Não editar isoladamente. Snapshot 2026-09-23.'
def render(root: Path, view: dict) -> str:
    parts=[HEADER]
    for path in view['sources']:
        parts.append(f'\n---\n\n## Origem local: `{path}`\n\n'+(root/path).read_text(encoding='utf-8'))
    return '\n'.join(parts).strip()+'\n'
if __name__=='__main__':
    for v in json.loads((ROOT/'provenance/GENERATED_VIEWS.json').read_text())['views']:
        (ROOT/v['path']).write_text(render(ROOT,v),encoding='utf-8',newline='\n')
    print('Views regenerated. Any material change invalidates the previous manifest/qualification; no authority was granted.')
