"""Register global lifecycle capture; Codex still requires its normal hook trust."""
import json
from pathlib import Path
import sys
from core import ROOT


def install(home=None):
    home=Path(home or Path.home())
    file=home/'.codex/hooks.json'
    file.parent.mkdir(parents=True,exist_ok=True)
    data=json.loads(file.read_text(encoding='utf-8')) if file.exists() else {'hooks':{}}
    command=f'python "{ROOT / "capture.py"}"'
    hooks=data.setdefault('hooks',{})
    for event in ('UserPromptSubmit','PostToolUse','Stop'):
        groups=hooks.setdefault(event,[])
        found=False
        for g in groups:
            for h in g.get('hooks',[]):
                if str(ROOT/'capture.py').replace('\\','/') in h.get('command','').replace('\\','/'):
                    h['command']=command;found=True
        if found:continue
        groups.append({'hooks':[{'type':'command','command':command,'timeout':10}]})
    if file.exists() and not file.with_suffix('.json.before-pmap').exists():
        file.with_suffix('.json.before-pmap').write_bytes(file.read_bytes())
    file.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    return file


if __name__=='__main__':print(install())
