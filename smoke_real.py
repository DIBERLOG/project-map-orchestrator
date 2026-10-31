from pathlib import Path
import json,subprocess,sys,threading,shutil
from core import Store,ROOT
import jobs
root=ROOT/'outputs'/'codex-smoke'
root.mkdir(parents=True,exist_ok=True)
repo=root/'repo';repo.mkdir(exist_ok=True)
if not (repo/'.git').exists():
 subprocess.run(['git','init',str(repo)],check=True,capture_output=True)
 (repo/'result.txt').write_text('pending\n')
 subprocess.run(['git','-C',str(repo),'add','.'],check=True,capture_output=True)
 subprocess.run(['git','-C',str(repo),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-m','fixture'],check=True,capture_output=True)
s=Store(root/'smoke.sqlite3')
p=s.create_project('Codex real smoke',str(repo))['id']
n=s.create_node(p,'task','Replace pending with PMAP_OK in result.txt only',body='This is a disposable local smoke fixture. Make exactly that file change. No extra agents or external services.',criteria='result.txt contains exactly PMAP_OK followed by newline.',scope='result.txt')
w=s.worktree(n['id'])
cmd=[str(Path.home()/'AppData/Local/OpenAI/Codex/bin/08d187884f2330d1/codex.exe'),'exec','--json','--sandbox','workspace-write','-']
jobs.configure(s,p,cmd,1,90)
jobs.enqueue(s,n['id']);job=jobs.claim(s)
jobs.perform(s,job,threading.Event())
report=jobs.list_jobs(s,p)[0]
report['file_result']=(Path(w['path'])/'result.txt').read_text()
(root/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:report[k] for k in ('state','code','file_result')},ensure_ascii=False))
print(report['output'][-2200:])
