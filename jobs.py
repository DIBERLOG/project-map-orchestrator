"""Durable work queue. One coordinator, isolated worktrees, bounded local workers."""
import json
import os
from pathlib import Path
import signal
import subprocess
import threading
import time
from core import now, uid, required, contract


def codex_command():
    """Prefer the desktop's compatible executable over an outdated npm shim."""
    import shutil
    candidates=list((Path.home()/'AppData/Local/OpenAI/Codex/bin').glob('*/codex.exe'))
    if candidates:
        binary=str(max(candidates,key=lambda p:p.stat().st_mtime))
    else:
        binary=shutil.which('codex')
        if not binary or Path(binary).suffix in ('.ps1','.cmd','.bat'):
            raise ValueError('Укажите полный путь к совместимому исполняемому файлу Codex')
    return [binary,'exec','--json','--sandbox','workspace-write','-']


def setup(store):
    with store.connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS worker_config(project TEXT PRIMARY KEY REFERENCES projects(id),command TEXT NOT NULL,parallel INTEGER NOT NULL,timeout INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,project TEXT NOT NULL REFERENCES projects(id),node TEXT NOT NULL REFERENCES nodes(id),state TEXT NOT NULL,prompt TEXT NOT NULL,command TEXT NOT NULL,cwd TEXT NOT NULL,scope TEXT NOT NULL,timeout INTEGER NOT NULL,pid INTEGER,code INTEGER,output TEXT NOT NULL DEFAULT '',created TEXT NOT NULL,finished TEXT,cancel INTEGER NOT NULL DEFAULT 0);
        ''')
        for name in ('contract', 'usage'):
            if name not in {r[1] for r in db.execute('PRAGMA table_info(jobs)')}:
                db.execute(f"ALTER TABLE jobs ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")


def configure(store, project, command, parallel=2, timeout=600):
    setup(store)
    if not isinstance(command, list) or not command or not all(isinstance(x, str) and x for x in command):
        raise ValueError('Команда исполнителя должна быть массивом аргументов')
    if not 1 <= parallel <= 4 or not 1 <= timeout <= 3600:
        raise ValueError('Допустимо 1–4 исполнителя, 1–3600 секунд')
    with store.connect() as db:
        store.get(db, 'projects', project)
        db.execute('INSERT OR REPLACE INTO worker_config VALUES(?,?,?,?)', (project, json.dumps(command), parallel, timeout))
        store.event(db, project, None, 'worker.configured', {'parallel': parallel, 'timeout': timeout})


def list_jobs(store, project):
    setup(store)
    with store.connect() as db:
        store.get(db, 'projects', project)
        return [dict(r) for r in db.execute('SELECT * FROM jobs WHERE project=? ORDER BY created DESC LIMIT 100', (project,))]


def enqueue(store, node):
    setup(store)
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        n = store.get(db, 'nodes', node)
        if n['kind'] not in ('task', 'fix') or n['status'] in ('done','deferred','cancelled'):
            raise ValueError('Исполнителю нужна незавершённая задача или исправление')
        required(n['criteria'], 'Критерии готовности')
        scope = [x.strip().replace('\\', '/') for x in n['scope'].split(',') if x.strip()]
        if not scope or any(Path(x).is_absolute() or '..' in x.split('/') or ':' in x or x.startswith('/') for x in scope):
            raise ValueError('Задайте область работы относительными путями через запятую')
        config = db.execute('SELECT * FROM worker_config WHERE project=?', (n['project'],)).fetchone()
        if not config:
            raise ValueError('Сначала настройте исполнителя через CLI: worker-config')
        if not n['worktree'] or not Path(n['worktree']).is_dir():
            raise ValueError('Сначала создайте отдельную рабочую копию задачи')
        if db.execute("SELECT 1 FROM jobs WHERE node=? AND state IN ('queued','running','orphaned')", (node,)).fetchone():
            raise ValueError('Для задачи уже есть незавершённый запуск')
        prompt = (f'Задача: {n["title"]}\nКонтекст: {n["body"][:6000]}\nКритерии: {n["criteria"]}\n'
                  f'Меняй только: {n["scope"]}. Работай в текущей отдельной копии. '
                  'Не делай commit, push, merge, публикацию или запуск других агентов. '
                  'Не помечай задачу принятой. В конце кратко опиши изменения, проверки и ограничения.')
        id = uid()
        db.execute('INSERT INTO jobs(id,project,node,state,prompt,command,cwd,scope,timeout,created) VALUES(?,?,?,?,?,?,?,?,?,?)',
                   (id, n['project'], node, 'queued', prompt, config['command'], n['worktree'], json.dumps(scope), config['timeout'], now()))
        db.execute('UPDATE jobs SET contract=? WHERE id=?', (contract(n), id))
        db.execute("UPDATE nodes SET status='planned',version=version+1,updated=? WHERE id=?", (now(), node))
        store.event(db, n['project'], node, 'job.queued', {'job': id})
        return {'id': id, 'state': 'queued'}


def cancel(store, job):
    setup(store)
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM jobs WHERE id=?', (job,)).fetchone()
        if not row:
            raise ValueError('Запуск не найден')
        if row['state'] not in ('queued', 'running'):
            raise ValueError('Этот запуск уже завершён или требует восстановления')
        state = 'cancelled' if row['state'] == 'queued' else 'running'
        db.execute('UPDATE jobs SET cancel=1,state=? WHERE id=?', (state, job))
        store.event(db, row['project'], row['node'], 'job.cancel_requested', {'job': job})
        return {'state': state, 'cancel_requested': True}


def claim(store):
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        for row in db.execute("SELECT j.*,c.parallel FROM jobs j JOIN worker_config c ON c.project=j.project WHERE j.state='queued' ORDER BY j.created").fetchall():
            n = store.get(db, 'nodes', row['node'])
            if n['status'] in ('deferred','cancelled'):
                db.execute("UPDATE jobs SET state='cancelled',cancel=1,finished=? WHERE id=?",(now(),row['id']))
                store.event(db,row['project'],row['node'],'job.cancelled',{'job':row['id'],'node_status':n['status']})
                continue
            if contract(n) != row['contract'] or n['status'] == 'done':
                db.execute("UPDATE jobs SET state='stale',finished=? WHERE id=?", (now(),row['id']))
                store.event(db,row['project'],row['node'],'job.stale',{'job':row['id']})
                continue
            active = db.execute("SELECT scope FROM jobs WHERE project=? AND state IN ('running','orphaned')", (row['project'],)).fetchall()
            if len(active) >= row['parallel']:
                continue
            scopes = json.loads(row['scope'])
            def overlaps(a, b):
                a, b = a.rstrip('/'), b.rstrip('/')
                return a == b or a.startswith(b + '/') or b.startswith(a + '/') or '.' in (a, b)
            if any(overlaps(a, b) for r in active for a in scopes for b in json.loads(r['scope'])):
                continue
            if db.execute("SELECT 1 FROM edges e JOIN nodes n ON e.target=n.id WHERE e.source=? AND n.status!='done'", (row['node'],)).fetchone():
                continue
            db.execute("UPDATE jobs SET state='running' WHERE id=?", (row['id'],))
            db.execute("UPDATE nodes SET status='active',version=version+1,updated=? WHERE id=?", (now(), row['node']))
            store.event(db, row['project'], row['node'], 'job.started', {'job': row['id']})
            return dict(row)


def kill(process):
    if process.poll() is not None:
        return
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True, timeout=15)
    else:
        os.killpg(process.pid, signal.SIGKILL)
    process.wait(timeout=15)


def perform(store, job, stopping):
    import tempfile
    state, code, text = 'failed', None, ''
    process = None
    try:
        with tempfile.TemporaryFile() as log, tempfile.TemporaryFile() as prompt:
            prompt.write(job['prompt'].encode('utf-8')); prompt.seek(0)
            process = subprocess.Popen(json.loads(job['command']), cwd=job['cwd'], stdin=prompt, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=os.name != 'nt', creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            with store.connect() as db:
                db.execute('UPDATE jobs SET pid=? WHERE id=?', (process.pid, job['id']))
            deadline = time.monotonic() + job['timeout']
            while process.poll() is None:
                with store.connect() as db:
                    cancelled = db.execute('SELECT cancel FROM jobs WHERE id=?', (job['id'],)).fetchone()[0]
                if cancelled or stopping.is_set() or time.monotonic() >= deadline:
                    state = 'cancelled' if cancelled else ('interrupted' if stopping.is_set() else 'timed_out')
                    kill(process)
                    break
                time.sleep(.1)
            else:
                state = 'review' if process.returncode == 0 else 'failed'
            code = process.returncode
            log.seek(0, 2); size = log.tell(); log.seek(max(0, size - 24000))
            text = log.read().decode('utf-8', errors='replace')
        # Audit scope after execution; prompts alone do not enforce file access.
        result = subprocess.run(['git', 'ls-files', '--modified', '--others', '--exclude-standard'], cwd=job['cwd'], capture_output=True, text=True, timeout=15)
        staged = subprocess.run(['git', 'diff', '--cached', '--name-only'], cwd=job['cwd'], capture_output=True, text=True, timeout=15)
        if result.returncode or staged.returncode:
            state = 'failed'; text += '\nНе удалось проверить изменённые файлы.'
        else:
            scope = json.loads(job['scope'])
            changed = set(result.stdout.splitlines() + staged.stdout.splitlines())
            outside = [p for p in changed if not any(s == '.' or p == s.rstrip('/') or p.startswith(s.rstrip('/') + '/') for s in scope)]
            if outside:
                state = 'scope_violation'; text += '\nИзменения вне области: ' + ', '.join(outside)
    except Exception as exc:
        text += '\n' + str(exc)
        if process and process.poll() is None:
            kill(process)
    usage = None
    for line in text.splitlines():
        try:
            item = json.loads(line)
            if item.get('type') == 'turn.completed' and isinstance(item.get('usage'), dict):
                usage = item['usage']
        except (ValueError, AttributeError):
            pass
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        n = store.get(db, 'nodes', job['node'])
        if state == 'review' and contract(n) != job['contract']:
            state = 'stale'
        db.execute('UPDATE jobs SET state=?,code=?,output=?,finished=?,usage=? WHERE id=?', (state, code, text[-24000:], now(), json.dumps(usage) if usage else '', job['id']))
        if n['status'] not in ('deferred','cancelled'):
            db.execute('UPDATE nodes SET status=?,version=version+1,updated=? WHERE id=?', ('review' if state == 'review' else 'blocked', now(), job['node']))
        store.event(db, job['project'], job['node'], 'job.finished', {'job': job['id'], 'state': state, 'code': code})


def coordinate(store, stopping=None):
    """Exclusive OS lock prevents two schedulers; crashed jobs require explicit recovery."""
    setup(store)
    stopping = stopping or threading.Event()
    lock = open(store.path.with_suffix('.coordinator.lock'), 'a+b')
    lock.seek(0); lock.write(b'0'); lock.flush(); lock.seek(0)
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with store.connect() as db:
            rows = db.execute("SELECT * FROM jobs WHERE state='running'").fetchall()
            for row in rows:
                db.execute("UPDATE jobs SET state='orphaned' WHERE id=?", (row['id'],))
                store.event(db, row['project'], row['node'], 'job.orphaned', {'job': row['id'], 'pid': row['pid']})
        workers = []
        try:
            while not stopping.is_set():
                job = claim(store)
                if job:
                    thread = threading.Thread(target=perform, args=(store, job, stopping))
                    workers.append(thread); thread.start()
                else:
                    stopping.wait(.2)
                workers = [t for t in workers if t.is_alive()]
        except KeyboardInterrupt:
            stopping.set()
        finally:
            stopping.set()
            for thread in workers:
                thread.join()
    finally:
        lock.close()


def recover(store, job):
    setup(store)
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM jobs WHERE id=?', (job,)).fetchone()
        if not row or row['state'] != 'orphaned':
            raise ValueError('Восстановление доступно только для потерянного запуска')
        if not row['pid']:
            raise ValueError('PID не был сохранён. Нужна ручная проверка старых процессов; автоматический повтор заблокирован.')
        if row['pid']:
            if os.name == 'nt':
                # os.kill(pid, 0) is not a portable existence probe on Windows.
                import ctypes
                from ctypes import wintypes
                kernel = ctypes.WinDLL('kernel32', use_last_error=True)
                kernel.OpenProcess.argtypes = [wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
                kernel.OpenProcess.restype = wintypes.HANDLE
                kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
                kernel.CloseHandle.argtypes = [wintypes.HANDLE]
                handle = kernel.OpenProcess(0x1000,False,row['pid'])
                if handle:
                    code=wintypes.DWORD()
                    try:
                        if not kernel.GetExitCodeProcess(handle,ctypes.byref(code)) or code.value==259:
                            raise ValueError('Старый PID ещё существует. Проверьте старое дерево процессов вручную.')
                    finally:
                        kernel.CloseHandle(handle)
                elif ctypes.get_last_error()!=87:
                    raise ValueError('Нельзя подтвердить остановку старого процесса')
            else:
                try:
                    os.kill(row['pid'], 0)
                except ProcessLookupError:
                    pass
                except OSError:
                    raise ValueError('Нельзя подтвердить остановку старого процесса')
                else:
                    raise ValueError('Старый PID ещё существует. Проверьте и остановите старое дерево процессов вручную.')
        db.execute("UPDATE jobs SET state='interrupted',finished=? WHERE id=?", (now(), job))
        store.event(db, row['project'], row['node'], 'job.recovered', {'job': job})
        return {'state': 'interrupted', 'message': 'Рабочая копия сохранена. Проверьте её перед повторной постановкой задачи.'}
