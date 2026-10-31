import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from core import Store, process_state
from app import rpc, handler
import integration
import jobs
import knowledge


def git(folder, *args):
    return subprocess.run(['git', '-C', str(folder), *args], capture_output=True, text=True, check=True).stdout


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'repo'; self.repo.mkdir()
        (self.repo / 'a.txt').write_text('old')
        git(self.repo, 'init')
        git(self.repo, 'add', '.')
        git(self.repo, '-c', 'user.name=Test', '-c', 'user.email=t@example.invalid', 'commit', '-m', 'fixture')
        self.store = Store(self.root / 'map.sqlite3')
        self.project = self.store.create_project('test', str(self.repo))['id']
        self.store.set_runner(self.project, [sys.executable, '-c', 'print("ok")'])

    def tearDown(self):
        self.tmp.cleanup()

    def node(self, **kwargs):
        return self.store.create_node(self.project, kwargs.pop('kind', 'task'), kwargs.pop('title', 'Change'), criteria='Check', scope='a.txt', **kwargs)

    def running(self, id, node, pid, created):
        with self.store.connect() as db:
            db.execute('INSERT INTO runs(id,project,node,command,cwd,status,fingerprint,created,owner_pid) VALUES(?,?,?,?,?,?,?,?,?)',
                       (id, self.project, node, '[]', str(self.repo), 'running', '', created, pid))

    def test_checks_of_dead_processes_are_interrupted_not_passed(self):
        n = self.node()
        dead = subprocess.Popen([sys.executable, '-c', 'pass']); dead.wait()
        self.assertEqual(process_state(dead.pid), 'gone')
        self.running('dead', n['id'], dead.pid, '2026-01-01T00:00:00.000+00:00')
        self.assertEqual(self.store.run_check(n['id'])['status'], 'passed')
        runs = {r['id']: r for r in self.store.snapshot(self.project)['runs']}
        self.assertEqual(runs['dead']['status'], 'interrupted')
        self.assertIn('test.interrupted', [e['kind'] for e in self.store.snapshot(self.project)['events']])

    def test_live_and_recent_legacy_checks_still_block(self):
        n = self.node()
        self.running('mine', n['id'], os.getpid(), '2026-01-01T00:00:00.000+00:00')
        with self.assertRaisesRegex(ValueError, 'уже выполняется'):
            self.store.run_check(n['id'])
        with self.store.connect() as db:
            db.execute("UPDATE runs SET owner_pid=NULL,created=? WHERE id='mine'", ('2999-01-01T00:00:00.000+00:00',))
        self.assertEqual(self.store.recover_runs(), [])
        with self.store.connect() as db:
            db.execute("UPDATE runs SET created='2020-01-01T00:00:00.000+00:00' WHERE id='mine'")
        self.assertEqual(self.store.recover_runs(), ['mine'])

    def test_dependency_can_be_removed(self):
        a, b = self.node(), self.node()
        self.store.depend(a['id'], b['id'])
        with self.assertRaises(ValueError):
            self.store.update_node(a['id'], a['version'], status='active')
        self.store.undepend(a['id'], b['id'])
        self.assertEqual(self.store.snapshot(self.project)['edges'], [])
        self.store.update_node(a['id'], a['version'], status='active')
        with self.assertRaisesRegex(ValueError, 'нет'):
            self.store.undepend(a['id'], b['id'])

    def test_worktree_removal_protects_unsaved_changes(self):
        n = self.node()
        path = Path(self.store.worktree(n['id'])['path'])
        (path / 'a.txt').write_text('worker change')
        with self.assertRaisesRegex(ValueError, 'изменения'):
            self.store.remove_worktree(n['id'])
        self.assertTrue(path.is_dir())
        result = self.store.remove_worktree(n['id'], discard=True)
        self.assertEqual(result['branch_kept'], '')
        self.assertFalse(path.exists())
        self.assertNotIn('codex/map-' + n['id'], git(self.repo, 'branch'))
        self.assertEqual(self.store.snapshot(self.project)['nodes'][0]['worktree'], '')
        self.store.worktree(n['id'])  # can be recreated afterwards

    def test_worktree_removal_keeps_branch_with_own_commits(self):
        n = self.node()
        path = Path(self.store.worktree(n['id'])['path'])
        (path / 'a.txt').write_text('committed')
        git(path, '-c', 'user.name=T', '-c', 'user.email=t@example.invalid', 'commit', '-am', 'worker')
        result = self.store.remove_worktree(n['id'])
        self.assertEqual(result['branch_kept'], 'codex/map-' + n['id'])
        self.assertIn('codex/map-' + n['id'], git(self.repo, 'branch'))

    def test_worktree_removal_refused_while_job_is_pending(self):
        jobs.configure(self.store, self.project, [sys.executable, '-c', 'pass'])
        n = self.node()
        self.store.worktree(n['id'])
        jobs.enqueue(self.store, n['id'])
        with self.assertRaisesRegex(ValueError, 'исполнителя'):
            self.store.remove_worktree(n['id'])

    def test_integration_removal(self):
        n = self.node()
        path = Path(self.store.worktree(n['id'])['path'])
        (path / 'a.txt').write_text('new')
        with self.store.connect() as db:
            db.execute("UPDATE nodes SET status='review' WHERE id=?", (n['id'],))
        built = integration.prepare(self.store, self.project, n['id'])
        with self.assertRaisesRegex(ValueError, 'изменения'):
            integration.remove(self.store, built['id'])
        self.assertEqual(integration.remove(self.store, built['id'], discard=True)['state'], 'removed')
        self.assertFalse(Path(built['path']).exists())
        self.assertEqual(integration.listing(self.store, self.project)[0]['state'], 'removed')
        with self.assertRaises(ValueError):
            integration.remove(self.store, built['id'])

    def test_token_budget_blocks_new_work(self):
        jobs.configure(self.store, self.project, [sys.executable, '-c', 'print(\'{"type":"turn.completed","usage":{"input_tokens":70,"cached_input_tokens":50,"output_tokens":40}}\')'])
        jobs.set_budget(self.store, self.project, 100)
        a, b = self.node(), self.node()
        for n in (a, b):
            self.store.worktree(n['id'])
            jobs.enqueue(self.store, n['id'])
        jobs.perform(self.store, jobs.claim(self.store), threading.Event())
        status = jobs.status(self.store, self.project)
        self.assertEqual((status['spent'], status['budget'], status['remaining']), (110, 100, 0))
        self.assertIsNone(jobs.claim(self.store))
        states = {j['node']: j['state'] for j in jobs.list_jobs(self.store, self.project)}
        self.assertEqual(states[b['id']], 'budget_exceeded')
        with self.assertRaisesRegex(ValueError, 'Бюджет'):
            jobs.enqueue(self.store, b['id'])
        jobs.set_budget(self.store, self.project, 0)
        jobs.enqueue(self.store, b['id'])
        # Reconfiguring the command keeps the budget unless a new one is given.
        jobs.set_budget(self.store, self.project, 500)
        jobs.configure(self.store, self.project, [sys.executable, '-c', 'pass'])
        self.assertEqual(jobs.status(self.store, self.project)['budget'], 500)

    def test_archive_round_trip_creates_new_project_without_commands(self):
        jobs.configure(self.store, self.project, [sys.executable, '-c', 'pass'])
        goal = self.node(kind='goal', title='Цель')
        task = self.node(parent=goal['id'], title='Задача')
        other = self.node(title='Сначала')
        self.store.depend(task['id'], other['id'])
        self.store.run_check(task['id'])
        self.store.worktree(task['id'])
        jobs.enqueue(self.store, task['id'])
        archive = json.loads(json.dumps(knowledge.archive(self.store, self.project)))
        result = knowledge.restore(self.store, archive, name='Копия')
        snap = self.store.snapshot(result['id'])
        self.assertEqual(snap['project']['name'], 'Копия')
        self.assertEqual(snap['project']['runner'], '[]')
        self.assertEqual(len(snap['nodes']), 3)
        self.assertTrue(all(n['worktree'] == '' for n in snap['nodes']))
        self.assertFalse({n['id'] for n in snap['nodes']} & {goal['id'], task['id'], other['id']})
        by_title = {n['title']: n for n in snap['nodes']}
        self.assertEqual(by_title['Задача']['parent'], by_title['Цель']['id'])
        self.assertEqual(snap['edges'][0]['target'], by_title['Сначала']['id'])
        self.assertEqual(snap['runs'][0]['status'], 'passed')
        self.assertEqual([j['state'] for j in jobs.list_jobs(self.store, result['id'])], ['interrupted'])
        self.assertEqual(snap['events'][0]['kind'], 'project.imported')
        self.assertGreater(len(snap['events']), 5)
        self.assertEqual(len(self.store.snapshot(self.project)['nodes']), 3)

    def test_broken_archive_leaves_no_project(self):
        before = len(self.store.projects())
        archive = knowledge.archive(self.store, self.project)
        archive['nodes'] = [{'id': 'x', 'kind': 'nonsense', 'title': 'bad'}]
        with self.assertRaises(ValueError):
            knowledge.restore(self.store, archive)
        with self.assertRaisesRegex(ValueError, 'формата'):
            knowledge.restore(self.store, {'nodes': []})
        with self.assertRaisesRegex(ValueError, 'Папка'):
            knowledge.restore(self.store, knowledge.archive(self.store, self.project), path=str(self.root / 'missing'))
        self.assertEqual(len(self.store.projects()), before)

    def test_new_tools_over_mcp_and_http_import(self):
        names = {t['name'] for t in rpc(self.store, {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})['result']['tools']}
        self.assertTrue({'map_undepend', 'map_remove_worktree', 'map_remove_integration', 'map_worker_status'} <= names)
        status = rpc(self.store, {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {'name': 'map_worker_status', 'arguments': {'project': self.project}}})
        self.assertEqual(json.loads(status['result']['content'][0]['text']), {'configured': False, 'spent': 0})
        self.node()
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler(self.store))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            port = server.server_port
            body = json.dumps({'archive': knowledge.archive(self.store, self.project), 'name': 'HTTP'}).encode()
            def post(headers):
                request = urllib.request.Request(f'http://127.0.0.1:{port}/api/import', body, headers | {'Content-Type': 'application/json'})
                return json.loads(urllib.request.urlopen(request).read())
            with self.assertRaises(urllib.error.HTTPError) as denied:
                post({})
            self.assertEqual(denied.exception.code, 403)
            denied.exception.close()
            result = post({'X-Project-Map': '1'})
            self.assertEqual(result['nodes'], 1)
        finally:
            server.shutdown(); server.server_close()


if __name__ == '__main__':
    unittest.main()
