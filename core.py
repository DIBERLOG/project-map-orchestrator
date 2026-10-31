"""Local project ledger. All writes and their events share a transaction."""
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
KINDS = ('request', 'goal', 'question', 'decision', 'spec', 'instruction', 'task', 'fix', 'test', 'prompt')
CHAIN_LIMIT = 40
STATES = ('planned', 'active', 'blocked', 'review', 'done', 'deferred', 'cancelled')


def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def uid():
    return uuid.uuid4().hex[:16]


def required(value, name, limit=20000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f'{name}: требуется непустой текст, до {limit} символов')
    return value.strip()


class Conflict(ValueError):
    pass


class Store:
    def __init__(self, path=None):
        self.path = Path(path or os.environ.get('PROJECT_MAP_DB', ROOT / 'data' / 'map.sqlite3'))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,name TEXT NOT NULL,path TEXT NOT NULL,runner TEXT NOT NULL DEFAULT '[]',created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS nodes(id TEXT PRIMARY KEY,project TEXT NOT NULL REFERENCES projects(id),kind TEXT NOT NULL,title TEXT NOT NULL,body TEXT NOT NULL DEFAULT '',criteria TEXT NOT NULL DEFAULT '',evidence TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'planned',parent TEXT REFERENCES nodes(id),scope TEXT NOT NULL DEFAULT '',owner TEXT NOT NULL DEFAULT '',worktree TEXT NOT NULL DEFAULT '',version INTEGER NOT NULL DEFAULT 1,created TEXT NOT NULL,updated TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS edges(project TEXT NOT NULL,source TEXT NOT NULL REFERENCES nodes(id),target TEXT NOT NULL REFERENCES nodes(id),PRIMARY KEY(source,target));
            CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,project TEXT NOT NULL REFERENCES projects(id),node TEXT,kind TEXT NOT NULL,payload TEXT NOT NULL,created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,project TEXT NOT NULL REFERENCES projects(id),node TEXT NOT NULL REFERENCES nodes(id),command TEXT NOT NULL,cwd TEXT NOT NULL,status TEXT NOT NULL,code INTEGER,output TEXT NOT NULL DEFAULT '',fingerprint TEXT NOT NULL,created TEXT NOT NULL,finished TEXT);
            ''')
            db.execute('BEGIN IMMEDIATE')
            if 'contract' not in {r[1] for r in db.execute('PRAGMA table_info(runs)')}:
                db.execute("ALTER TABLE runs ADD COLUMN contract TEXT NOT NULL DEFAULT ''")
            if 'check_project' not in {r[1] for r in db.execute('PRAGMA table_info(nodes)')}:
                db.execute("ALTER TABLE nodes ADD COLUMN check_project TEXT NOT NULL DEFAULT ''")
            if 'owner_pid' not in {r[1] for r in db.execute('PRAGMA table_info(runs)')}:
                db.execute('ALTER TABLE runs ADD COLUMN owner_pid INTEGER')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def event(self, db, project, node, kind, payload):
        db.execute('INSERT INTO events(project,node,kind,payload,created) VALUES(?,?,?,?,?)',
                   (project, node, kind, json.dumps(payload, ensure_ascii=False), now()))

    def get(self, db, table, id):
        if table not in ('projects', 'nodes', 'runs'):
            raise ValueError('Unknown table')
        row = db.execute(f'SELECT * FROM {table} WHERE id=?', (id,)).fetchone()
        if not row:
            raise ValueError('Запись не найдена')
        return dict(row)

    def projects(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT id,name,path,created FROM projects ORDER BY created')]

    def create_project(self, name, path):
        name = required(name, 'Название', 160)
        folder = Path(required(path, 'Папка')).resolve()
        if not folder.is_dir():
            raise ValueError('Папка проекта не существует')
        id = uid()
        with self.connect() as db:
            db.execute('INSERT INTO projects(id,name,path,created) VALUES(?,?,?,?)', (id, name, str(folder), now()))
            self.event(db, id, None, 'project.created', {'name': name})
        return {'id': id}

    def snapshot(self, project):
        with self.connect() as db:
            p = self.get(db, 'projects', project)
            return {'project': p, 'nodes': [dict(r) for r in db.execute('SELECT * FROM nodes WHERE project=? ORDER BY created,id', (project,))],
                    'edges': [dict(r) for r in db.execute('SELECT * FROM edges WHERE project=?', (project,))],
                    'events': [dict(r) | {'payload': json.loads(r['payload'])} for r in db.execute('SELECT * FROM events WHERE project=? ORDER BY seq DESC LIMIT 300', (project,))],
                    'runs': [dict(r) for r in db.execute('SELECT * FROM runs WHERE project=? ORDER BY created DESC LIMIT 50', (project,))]}

    def create_node(self, project, kind, title, body='', parent=None, criteria='', scope='', owner='', evidence=''):
        if kind not in KINDS:
            raise ValueError('Неизвестный тип узла')
        title = required(title, 'Название', 200)
        for value in (body, criteria, scope, owner, evidence):
            if not isinstance(value, str) or len(value) > 20000:
                raise ValueError('Слишком длинный или некорректный текст')
        id, date = uid(), now()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.get(db, 'projects', project)
            if parent and self.get(db, 'nodes', parent)['project'] != project:
                raise ValueError('Родитель должен принадлежать этому проекту')
            db.execute('INSERT INTO nodes(id,project,kind,title,body,parent,criteria,scope,owner,evidence,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                       (id, project, kind, title, body, parent or None, criteria, scope, owner, evidence, date, date))
            self.event(db, project, id, f'{kind}.created', {'title': title, 'parent': parent})
            return self.get(db, 'nodes', id)

    def update_node(self, id, version, **changes):
        allowed = {'title', 'body', 'criteria', 'evidence', 'status', 'scope', 'owner', 'check_project'}
        if not changes or not set(changes) <= allowed:
            raise ValueError('Недопустимые поля изменения')
        for key, value in changes.items():
            if not isinstance(value, str) or len(value) > (200 if key == 'title' else 20000):
                raise ValueError('Некорректный текст')
        if 'title' in changes:
            changes['title'] = required(changes['title'], 'Название', 200)
        if 'status' in changes and changes['status'] not in STATES:
            raise ValueError('Неизвестный статус')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = self.get(db, 'nodes', id)
            if old['version'] != version:
                raise Conflict('Запись уже изменена. Скопируйте свой текст и откройте свежую версию.')
            new = old | changes
            if new['check_project']:
                self.get(db, 'projects', new['check_project'])
                if new['worktree'] and new['check_project'] != new['project']:
                    raise ValueError('Рабочая копия проверяется командой своего проекта; внешний проект проверки недоступен')
            if changes.get('status') in ('deferred','cancelled'):
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs'").fetchone():
                    db.execute("UPDATE jobs SET cancel=1,state=CASE WHEN state='queued' THEN 'cancelled' ELSE state END WHERE node=? AND state IN ('queued','running')",(id,))
            if new['status'] in ('active', 'done'):
                unmet = db.execute("SELECT n.title FROM edges e JOIN nodes n ON n.id=e.target WHERE e.source=? AND n.status!='done'", (id,)).fetchall()
                if unmet:
                    raise ValueError('Сначала завершите зависимости: ' + ', '.join(r[0] for r in unmet))
            if new['status'] == 'done' and old['kind'] in ('task', 'fix'):
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs'").fetchone():
                    if db.execute("SELECT 1 FROM jobs WHERE node=? AND state IN ('queued','running','orphaned')", (id,)).fetchone():
                        raise ValueError('Сначала завершите или восстановите запуск исполнителя')
                if not new['criteria'].strip() or not new['evidence'].strip():
                    raise ValueError('Нужны критерии приёмки и описание результата')
                run = db.execute('SELECT * FROM runs WHERE node=? ORDER BY created DESC LIMIT 1', (id,)).fetchone()
                project = self.get(db, 'projects', new['check_project'] or old['project'])
                if (not run or run['status'] != 'passed' or run['contract'] != contract(new)
                    or run['command'] != project['runner'] or run['cwd'] != (new['worktree'] or project['path'])
                    or fingerprint(Path(run['cwd'])) != run['fingerprint']):
                    raise ValueError('Нужен успешный прогон на актуальных исходниках')
            db.execute('UPDATE nodes SET ' + ','.join(f'{k}=?' for k in changes) + ',version=version+1,updated=? WHERE id=?',
                       (*changes.values(), now(), id))
            self.event(db, old['project'], id, 'node.updated', {'before': {k: old[k] for k in changes}, 'after': changes})
            if old['kind'] == 'prompt' and 'status' in changes:
                self.advance_chain(db, id)
            return self.get(db, 'nodes', id)

    def create_chain(self, project, prompts, title='', parent=None):
        """Store pasted prompts as one chain: root -> prompt 1 -> prompt 2 ... The first step is current."""
        steps = split_prompts(prompts)
        if not steps:
            raise ValueError('Вставьте хотя бы один промт')
        if len(steps) > CHAIN_LIMIT:
            raise ValueError(f'В одной цепочке до {CHAIN_LIMIT} промтов')
        if not isinstance(title, str) or len(title) > 200:
            raise ValueError('Некорректное название цепочки')
        date = now()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.get(db, 'projects', project)
            if parent and self.get(db, 'nodes', parent)['project'] != project:
                raise ValueError('Родитель должен принадлежать этому проекту')
            root = uid()
            name = title.strip() or f'Цепочка промтов · {len(steps)}'
            db.execute('INSERT INTO nodes(id,project,kind,title,body,parent,status,created,updated) VALUES(?,?,?,?,?,?,?,?,?)',
                       (root, project, 'request', name, f'{len(steps)} промтов выполняются по порядку.', parent or None, 'active', date, date))
            ids, previous = [], root
            for index, text in enumerate(steps):
                id = uid()
                line = ' '.join(text.split('\n', 1)[0].split())
                db.execute('INSERT INTO nodes(id,project,kind,title,body,parent,status,created,updated) VALUES(?,?,?,?,?,?,?,?,?)',
                           (id, project, 'prompt', f'{index + 1}. {line}'[:200], text, previous, 'active' if index == 0 else 'planned', date, date))
                ids.append(id)
                previous = id
            self.event(db, project, root, 'chain.created', {'title': name, 'steps': len(steps)})
            return {'root': root, 'steps': ids}

    def chain_steps(self, db, id):
        """Root (non-prompt head, may be None) and ordered prompt steps of the chain containing id."""
        node = self.get(db, 'nodes', id)
        guard = 0
        while node['kind'] == 'prompt' and node['parent'] and guard < CHAIN_LIMIT + 5:
            parent = self.get(db, 'nodes', node['parent'])
            if parent['kind'] != 'prompt':
                break
            node, guard = parent, guard + 1
        root = self.get(db, 'nodes', node['parent']) if node['kind'] == 'prompt' and node['parent'] else None
        steps = [node] if node['kind'] == 'prompt' else []
        while steps and len(steps) <= CHAIN_LIMIT:
            following = db.execute("SELECT * FROM nodes WHERE parent=? AND kind='prompt' ORDER BY created,id LIMIT 1", (steps[-1]['id'],)).fetchone()
            if not following:
                break
            steps.append(dict(following))
        return root, steps

    def advance_chain(self, db, id):
        """After a step is answered or accepted, the next unfinished step becomes current."""
        root, steps = self.chain_steps(db, id)
        finished = ('review', 'done', 'cancelled')
        current = next((s for s in steps if s['status'] not in finished + ('deferred',)), None)
        if current and current['status'] == 'planned' and all(s['status'] in finished for s in steps[:steps.index(current)]):
            db.execute("UPDATE nodes SET status='active',version=version+1,updated=? WHERE id=?", (now(), current['id']))
            self.event(db, current['project'], current['id'], 'chain.advanced', {'from': id})
        if root and root['status'] not in ('deferred', 'cancelled'):
            live = [s for s in steps if s['status'] != 'cancelled']
            target = ('done' if all(s['status'] == 'done' for s in live) else
                      'review' if all(s['status'] in ('review', 'done') for s in live) else 'active') if live else root['status']
            if target != root['status']:
                db.execute('UPDATE nodes SET status=?,version=version+1,updated=? WHERE id=?', (target, now(), root['id']))
                self.event(db, root['project'], root['id'], 'chain.status', {'status': target})

    def current_chain_step(self, db, project, text):
        """The current step whose prompt matches submitted text, or None. Never guesses on partial overlap."""
        wanted = normalize(text)
        if not wanted:
            return None
        for row in db.execute("SELECT * FROM nodes WHERE project=? AND kind='prompt' AND status IN ('active','planned') ORDER BY status='planned',created", (project,)).fetchall():
            step = normalize(row['body'])
            if step and (step == wanted or step in wanted):
                if row['status'] == 'planned':
                    previous = self.get(db, 'nodes', row['parent']) if row['parent'] else None
                    if previous and previous['kind'] == 'prompt' and previous['status'] not in ('review', 'done', 'cancelled'):
                        continue
                return dict(row)
        return None

    def set_step_status(self, db, id, status, source):
        node = self.get(db, 'nodes', id)
        if node['kind'] != 'prompt' or node['status'] == status:
            return
        db.execute('UPDATE nodes SET status=?,version=version+1,updated=? WHERE id=?', (status, now(), id))
        self.event(db, node['project'], id, 'node.updated', {'source': source, 'before': {'status': node['status']}, 'after': {'status': status}})
        self.advance_chain(db, id)

    def depend(self, source, target):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            a, b = self.get(db, 'nodes', source), self.get(db, 'nodes', target)
            if a['project'] != b['project'] or source == target:
                raise ValueError('Недопустимая зависимость')
            if a['status'] in ('active', 'done'):
                raise ValueError('Верните задачу в план перед изменением зависимостей')
            reachable = {target}
            edges = list(db.execute('SELECT source,target FROM edges WHERE project=?', (a['project'],)))
            while True:
                more = {r['target'] for r in edges if r['source'] in reachable}
                if source in more:
                    raise ValueError('Зависимость создаёт цикл')
                if more <= reachable:
                    break
                reachable |= more
            cursor = db.execute('INSERT OR IGNORE INTO edges(project,source,target) VALUES(?,?,?)', (a['project'], source, target))
            if cursor.rowcount:
                self.event(db, a['project'], source, 'dependency.created', {'target': target})
        return {'ok': True}

    def undepend(self, source, target):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            a = self.get(db, 'nodes', source)
            if not db.execute('DELETE FROM edges WHERE source=? AND target=?', (source, target)).rowcount:
                raise ValueError('Такой зависимости нет')
            self.event(db, a['project'], source, 'dependency.removed', {'target': target})
        return {'ok': True}

    def context(self, project, query='', limit=20):
        s = self.snapshot(project)
        query = query.casefold()
        nodes = [n for n in s['nodes'] if query in (n['title'] + ' ' + n['body']).casefold()]
        nodes.sort(key=lambda n: (n['status'] in ('done','deferred','cancelled'), n['created']))
        limit = max(1, min(int(limit), 50))
        return {'project': s['project']['name'], 'total_matches': len(nodes), 'nodes': [
            {k: n[k] for k in ('id', 'kind', 'title', 'status', 'version', 'parent', 'criteria', 'scope')}
            | {'body': n['body'][:1000]} for n in nodes[:limit]], 'dependencies': s['edges'],
            'coverage': 'Only events recorded through Project Map; not all Codex conversations.'}

    def set_runner(self, project, command):
        if not isinstance(command, list) or not command or not all(isinstance(x, str) and x for x in command):
            raise ValueError('Команда должна быть массивом аргументов')
        with self.connect() as db:
            self.get(db, 'projects', project)
            db.execute('UPDATE projects SET runner=? WHERE id=?', (json.dumps(command), project))
            self.event(db, project, None, 'runner.configured', {'command': command})

    def recover_runs(self, db=None):
        """Mark checks whose owning process is gone as interrupted, never as success."""
        if db is None:
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                return self.recover_runs(db)
        recovered = []
        for row in db.execute("SELECT * FROM runs WHERE status='running'").fetchall():
            if row['owner_pid']:
                lost = row['owner_pid'] != os.getpid() and process_state(row['owner_pid']) == 'gone'
            else:
                # Older records have no owner; the 60 second limit makes 10 minutes conclusive.
                try:
                    lost = time.time() - datetime.fromisoformat(row['created']).timestamp() > 600
                except ValueError:
                    lost = False
            if lost:
                db.execute("UPDATE runs SET status='interrupted',output=?,finished=? WHERE id=?",
                           ('Проверка прервана: процесс завершился до получения результата.', now(), row['id']))
                self.event(db, row['project'], row['node'], 'test.interrupted', {'run': row['id']})
                recovered.append(row['id'])
        return recovered

    def run_check(self, node):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.recover_runs(db)
            n = self.get(db, 'nodes', node)
            p = self.get(db, 'projects', n['check_project'] or n['project'])
            if n['worktree'] and p['id'] != n['project']:
                raise ValueError('Рабочая копия несовместима с внешним проектом проверки')
            command = json.loads(p['runner'])
            if not command:
                raise ValueError('Сначала настройте тестовую команду через CLI (см. README)')
            folder = Path(n['worktree'] or p['path'])
            if db.execute("SELECT 1 FROM runs WHERE (project=? OR cwd=?) AND status='running'", (n['project'], str(folder))).fetchone():
                raise ValueError('В проекте уже выполняется проверка')
            stamp, id = fingerprint(folder), uid()
            db.execute('INSERT INTO runs(id,project,node,command,cwd,status,fingerprint,created,contract,owner_pid) VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (id, n['project'], node, json.dumps(command), str(folder), 'running', stamp, now(), contract(n), os.getpid()))
            self.event(db, n['project'], node, 'test.started', {'run': id, 'check_project': p['id']})
        code, output, status = None, '', 'failed'
        try:
            code, output = execute(command, folder, 60)
            after = fingerprint(folder)
            status = 'passed' if code == 0 and stamp == after else ('stale' if stamp != after else 'failed')
        except Exception as exc:
            output = str(exc)
        with self.connect() as db:
            db.execute('UPDATE runs SET status=?,code=?,output=?,finished=? WHERE id=?', (status, code, output[-24000:], now(), id))
            self.event(db, n['project'], node, 'test.finished', {'run': id, 'status': status, 'code': code})
            return self.get(db, 'runs', id)

    def worktree(self, node):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            n = self.get(db, 'nodes', node)
            p = self.get(db, 'projects', n['project'])
            if n['check_project'] and n['check_project'] != n['project']:
                raise ValueError('Перед созданием рабочей копии верните проверку в текущий проект')
            if n['worktree']:
                raise ValueError('Рабочая копия для задачи уже создана')
            folder = Path(p['path'])
            result = subprocess.run(['git', '-C', str(folder), 'status', '--porcelain'], capture_output=True, text=True, timeout=10)
            if result.returncode:
                raise ValueError('Для ветки нужен Git-репозиторий с начальным коммитом')
            if result.stdout.strip():
                raise ValueError('В исходной папке есть изменения. Рабочая копия создаётся только из сохранённой версии.')
            target = self.path.parent / 'worktrees' / node
            target.parent.mkdir(parents=True, exist_ok=True)
            branch = 'codex/map-' + node
            result = subprocess.run(['git', '-C', str(folder), 'worktree', 'add', '-b', branch, str(target), 'HEAD'], capture_output=True, text=True, timeout=60)
            if result.returncode:
                raise ValueError(result.stderr[-2000:])
            db.execute('UPDATE nodes SET worktree=?,version=version+1,updated=? WHERE id=?', (str(target), now(), node))
            self.event(db, p['id'], node, 'worktree.created', {'branch': branch, 'path': str(target)})
            return {'branch': branch, 'path': str(target)}

    def remove_worktree(self, node, discard=False):
        """Remove a task worktree. Unsaved worker changes require explicit discard."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            n = self.get(db, 'nodes', node)
            p = self.get(db, 'projects', n['project'])
            if not n['worktree']:
                raise ValueError('У задачи нет рабочей копии')
            if n['status'] == 'active':
                raise ValueError('Задача в работе: сначала остановите исполнителя')
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs'").fetchone():
                if db.execute("SELECT 1 FROM jobs WHERE node=? AND state IN ('queued','running','orphaned')", (node,)).fetchone():
                    raise ValueError('Сначала завершите или восстановите запуск исполнителя')
            kept = remove_checkout(p['path'], n['worktree'], 'codex/map-' + node, discard)
            db.execute("UPDATE nodes SET worktree='',version=version+1,updated=? WHERE id=?", (now(), node))
            self.event(db, p['id'], node, 'worktree.removed', {'path': n['worktree'], 'discarded': bool(discard), 'branch_kept': kept})
            return {'removed': n['worktree'], 'branch_kept': kept}


def process_state(pid):
    """Return 'alive', 'gone' or 'unknown'. os.kill(pid, 0) is not a portable probe on Windows."""
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            error = ctypes.get_last_error()
            return 'gone' if error == 87 else ('alive' if error == 5 else 'unknown')
        code = wintypes.DWORD()
        try:
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
                return 'unknown'
            return 'alive' if code.value == 259 else 'gone'
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return 'gone'
    except PermissionError:
        return 'alive'
    except OSError:
        return 'unknown'
    return 'alive'


def remove_checkout(source, path, branch, discard):
    """Remove a pmap worktree and its branch when safe. Returns the branch name if it was kept."""
    folder = Path(path)
    def git(*args):
        return subprocess.run(['git', '-C', str(source), *args], capture_output=True, text=True, timeout=60)
    if folder.is_dir():
        status = subprocess.run(['git', '-C', str(folder), 'status', '--porcelain'], capture_output=True, text=True, timeout=15)
        if status.returncode:
            raise ValueError('Не удалось проверить рабочую копию: ' + status.stderr[-1000:])
        if status.stdout.strip() and not discard:
            raise ValueError('В рабочей копии есть изменения. Подтвердите удаление вместе с изменениями.')
        result = git('worktree', 'remove', *(['--force'] if discard else []), str(folder))
        if result.returncode:
            raise ValueError(result.stderr[-2000:])
    else:
        git('worktree', 'prune')
    # -d refuses to drop commits that exist only on this branch; such a branch is kept.
    if git('branch', '-d', branch).returncode == 0 or not git('rev-parse', '--verify', '--quiet', 'refs/heads/' + branch).stdout.strip():
        return ''
    return branch


def normalize(text):
    return ' '.join(str(text or '').split()).casefold()


def split_prompts(text):
    """Lines of '---' separate prompts; otherwise blank lines; otherwise every line is a prompt."""
    if not isinstance(text, str) or len(text) > 200000:
        raise ValueError('Текст цепочки должен быть строкой до 200000 символов')
    text = text.replace('\r\n', '\n').strip()
    if re.search(r'^\s*-{3,}\s*$', text, re.M):
        parts = re.split(r'^\s*-{3,}\s*$', text, flags=re.M)
    elif re.search(r'\n\s*\n', text):
        parts = re.split(r'\n\s*\n', text)
    else:
        parts = text.split('\n')
    result = []
    for part in parts:
        part = re.sub(r'^\s*(?:\d{1,3}[.)]|[-*•])\s+', '', part.strip(), count=1).strip()
        if part:
            result.append(part[:19000])
    return result


def contract(node):
    values = {k: node[k] for k in ('title', 'body', 'criteria', 'scope')}
    if node.get('check_project'):
        values['check_project'] = node['check_project']
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def fingerprint(folder):
    if not folder.is_dir():
        raise ValueError('Папка исходников недоступна')
    skip = {'.git', 'data', 'outputs', '__pycache__', 'node_modules', '.venv', '.worktrees'}
    digest = hashlib.sha256()
    count = 0
    for base, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if d not in skip and not (Path(base) / d).is_symlink())
        for name in sorted(files):
            file = Path(base) / name
            if file.is_symlink() or name.endswith(('.pyc', '.log')):
                continue
            count += 1
            if count > 50000:
                raise ValueError('Слишком большой проект для текущего механизма отпечатка')
            digest.update(str(file.relative_to(folder)).replace('\\', '/').encode())
            digest.update(b'\0')
            with file.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(chunk)
            digest.update(b'\0')
    return digest.hexdigest()


def execute(command, folder, timeout):
    # Use a file, not PIPE: large test output must not exhaust RAM or deadlock.
    import tempfile
    import signal
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(command, cwd=folder, stdout=output, stderr=subprocess.STDOUT,
                                   start_new_session=os.name != 'nt', creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True, timeout=10)
            else:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)
            raise ValueError('Проверка остановлена: превышен лимит 60 секунд')
        size = output.tell()
        output.seek(max(0, size - 24000))
        return process.returncode, output.read().decode('utf-8', errors='replace')
