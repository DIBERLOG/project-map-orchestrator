import concurrent.futures
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from core import Store, Conflict, fingerprint, execute
from app import rpc, handler


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        (self.source / 'source.txt').write_text('first')
        self.db = self.root / 'map.sqlite3'
        self.store = Store(self.db)
        self.project = self.store.create_project('Тестовый проект', str(self.source))['id']

    def tearDown(self):
        self.temp.cleanup()

    def node(self, **kwargs):
        return self.store.create_node(self.project, kwargs.pop('kind', 'task'), kwargs.pop('title', 'Сохранить заметку'), **kwargs)

    def test_task_fix_and_events_survive_restart(self):
        task = self.node()
        fix = self.node(kind='fix', parent=task['id'], title='Исправить сохранение')
        snapshot = Store(self.db).snapshot(self.project)
        self.assertEqual(len(snapshot['nodes']), 2)
        self.assertEqual(snapshot['nodes'][1]['parent'], task['id'])
        self.assertEqual(snapshot['events'][0]['kind'], 'fix.created')
        self.assertEqual(snapshot['events'][0]['node'], fix['id'])

    def test_existing_database_migrates_once_under_concurrent_startup(self):
        n = self.node()
        with self.store.connect() as db:
            db.execute('ALTER TABLE nodes DROP COLUMN check_project')
        def reopen(_):
            return Store(self.db).snapshot(self.project)['nodes'][0]
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            rows = list(pool.map(reopen, range(4)))
        self.assertTrue(all(r['id'] == n['id'] and r['check_project'] == '' for r in rows))

    def test_stale_edit_cannot_overwrite_and_no_phantom_event(self):
        n = self.node()
        self.store.update_node(n['id'], 1, title='Новое название')
        with self.assertRaises(Conflict):
            self.store.update_node(n['id'], 1, title='Устаревшее название')
        s = self.store.snapshot(self.project)
        self.assertEqual(s['nodes'][0]['title'], 'Новое название')
        self.assertEqual(len(s['events']), 3)

    def test_mcp_can_defer_cancel_and_resume_without_losing_text(self):
        node=self.node(body='Keep this draft')
        for status in ('deferred','cancelled','planned'):
            result=rpc(self.store,{'id':1,'method':'tools/call','params':{'name':'map_update_node','arguments':{'id':node['id'],'version':node['version'],'status':status}}})
            self.assertNotIn('isError',result['result'])
            node=json.loads(result['result']['content'][0]['text'])
            self.assertEqual(node['status'],status)
            self.assertEqual(node['body'],'Keep this draft')
        reopened=Store(self.db).snapshot(self.project)
        self.assertEqual(reopened['nodes'][0]['status'],'planned')
        self.assertEqual(len(reopened['events']),5)

    def test_concurrent_writers_one_wins(self):
        n = self.node()
        def save(title):
            try:
                self.store.update_node(n['id'], 1, title=title)
                return True
            except Conflict:
                return False
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            results = list(pool.map(save, ['A', 'B']))
        self.assertEqual(results.count(True), 1)

    def test_cross_project_parent_rejected(self):
        n = self.node()
        other = self.store.create_project('Другой', str(self.source))['id']
        with self.assertRaises(ValueError):
            self.store.create_node(other, 'fix', 'Ошибка', parent=n['id'])
        self.assertEqual(len(self.store.snapshot(other)['nodes']), 0)

    def test_external_check_keeps_history_and_enforces_target_sources_runner_and_contract(self):
        folder = self.root / 'other'
        folder.mkdir()
        marker = folder / 'marker.txt'
        marker.write_text('target')
        other = self.store.create_project('Проверяемые исходники', str(folder))['id']
        command = [sys.executable, '-c', 'from pathlib import Path; assert Path("marker.txt").read_text()=="target"']
        self.store.set_runner(other, command)
        n = self.node(criteria='Проверены исходники другого проекта', evidence='test receipt')
        n = self.store.update_node(n['id'], n['version'], check_project=other)
        result = self.store.run_check(n['id'])
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['cwd'], str(folder))
        self.assertEqual(result['project'], self.project)
        self.assertEqual(self.store.snapshot(other)['runs'], [])
        self.assertEqual(self.store.snapshot(self.project)['events'][0]['kind'], 'test.finished')
        n = self.store.update_node(n['id'], n['version'], status='done')
        with self.assertRaisesRegex(ValueError, 'актуальных'):
            self.store.update_node(n['id'], n['version'], check_project='')
        self.store.set_runner(other, [sys.executable, '-c', 'print("different check")'])
        with self.assertRaisesRegex(ValueError, 'актуальных'):
            self.store.update_node(n['id'], n['version'], evidence='Changed runner')
        self.store.set_runner(other, command)
        marker.write_text('changed')
        with self.assertRaisesRegex(ValueError, 'актуальных'):
            self.store.update_node(n['id'], n['version'], evidence='Changed source')

    def test_external_check_rejects_unknown_project_and_incompatible_worktree(self):
        n = self.node()
        with self.assertRaisesRegex(ValueError, 'не найдена'):
            self.store.update_node(n['id'], n['version'], check_project='missing')
        other = self.store.create_project('Другой', str(self.source))['id']
        n = self.store.update_node(n['id'], n['version'], check_project=other)
        with self.assertRaisesRegex(ValueError, 'верните проверку'):
            self.store.worktree(n['id'])
        n = self.store.update_node(n['id'], n['version'], check_project='')
        with self.store.connect() as db:
            db.execute('UPDATE nodes SET worktree=? WHERE id=?', (str(self.source), n['id']))
        with self.assertRaisesRegex(ValueError, 'Рабочая копия'):
            self.store.update_node(n['id'], n['version'], check_project=other)

    def test_external_check_reserves_the_same_source_folder_across_maps(self):
        other = self.store.create_project('Другой', str(self.source))['id']
        self.store.set_runner(other, [sys.executable, '-c', 'print("ok")'])
        n = self.node()
        n = self.store.update_node(n['id'], n['version'], check_project=other)
        with self.store.connect() as db:
            db.execute('INSERT INTO runs(id,project,node,command,cwd,status,fingerprint,created) VALUES(?,?,?,?,?,?,?,?)',
                       ('busy', other, n['id'], '[]', str(self.source), 'running', '', 'now'))
        with self.assertRaisesRegex(ValueError, 'уже выполняется'):
            self.store.run_check(n['id'])

    def test_dependency_cycle_and_early_start_rejected(self):
        a, b, c = self.node(), self.node(), self.node()
        self.store.depend(a['id'], b['id'])
        self.store.depend(b['id'], c['id'])
        with self.assertRaisesRegex(ValueError, 'цикл'):
            self.store.depend(c['id'], a['id'])
        with self.assertRaisesRegex(ValueError, 'зависимости'):
            self.store.update_node(a['id'], 1, status='active')

    def test_done_requires_proof_and_fresh_sources(self):
        n = self.node(criteria='Программа завершается успешно')
        with self.assertRaises(ValueError):
            self.store.update_node(n['id'], 1, status='done', evidence='Агент сказал готово')
        self.store.set_runner(self.project, [sys.executable, '-c', 'print("checked")'])
        run = self.store.run_check(n['id'])
        self.assertEqual(run['status'], 'passed')
        self.assertIn('checked', run['output'])
        (self.source / 'source.txt').write_text('second')
        with self.assertRaisesRegex(ValueError, 'актуальных'):
            self.store.update_node(n['id'], 1, status='done', evidence='Проверено')
        self.store.run_check(n['id'])
        done = self.store.update_node(n['id'], 1, status='done', evidence='Локальная команда вернула код 0')
        self.assertEqual(done['status'], 'done')

    def test_failed_test_is_not_success(self):
        n = self.node(criteria='Успешный запуск')
        self.store.set_runner(self.project, [sys.executable, '-c', 'raise SystemExit(7)'])
        run = self.store.run_check(n['id'])
        self.assertEqual((run['status'], run['code']), ('failed', 7))
        with self.assertRaises(ValueError):
            self.store.update_node(n['id'], 1, status='done', evidence='ok')

    def test_modified_sources_during_run_are_stale(self):
        n = self.node()
        self.store.set_runner(self.project, [sys.executable, '-c', 'from pathlib import Path; Path("source.txt").write_text("changed")'])
        self.assertEqual(self.store.run_check(n['id'])['status'], 'stale')

    def test_large_output_and_timeout(self):
        code, text = execute([sys.executable, '-c', 'print("x"*100000)'], self.source, 5)
        self.assertEqual(code, 0)
        self.assertLessEqual(len(text), 24000)
        with self.assertRaisesRegex(ValueError, 'остановлена'):
            execute([sys.executable, '-c', 'import time; time.sleep(30)'], self.source, .1)

    def test_context_search_is_bounded(self):
        self.node(title='Исправить кнопку', body='Описание кнопки')
        self.node(title='Другая задача')
        result = self.store.context(self.project, 'кноп', 1)
        self.assertEqual(result['total_matches'], 1)
        self.assertEqual(len(result['nodes']), 1)

    def test_mcp_rpc_error_and_creation(self):
        init = rpc(self.store, {'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-03-26'}})
        self.assertEqual(init['result']['protocolVersion'], '2025-03-26')
        result = rpc(self.store, {'id': 2, 'method': 'tools/call', 'params': {'name': 'map_create_node', 'arguments': {'project': self.project, 'kind': 'fix', 'title': 'MCP исправление'}}})
        self.assertNotIn('isError', result['result'])
        self.assertEqual(self.store.snapshot(self.project)['nodes'][0]['title'], 'MCP исправление')
        result = rpc(self.store, {'id': 3, 'method': 'tools/call', 'params': {'name': 'map_update_node', 'arguments': {'id': 'missing', 'version': 1, 'status': 'done'}}})
        self.assertTrue(result['result']['isError'])

    def test_mcp_stdio_protocol(self):
        messages = [{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-03-26'}},
                    {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
                    {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}]
        result = subprocess.run([sys.executable, 'app.py', '--db', str(self.db), 'mcp'], input='\n'.join(json.dumps(m) for m in messages)+'\n', capture_output=True, text=True, encoding='utf-8', timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(lines), 2)
        self.assertIn('map_enqueue', [t['name'] for t in lines[1]['result']['tools']])

    def test_http_origin_and_input_validation(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler(self.store))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f'http://127.0.0.1:{server.server_port}'
        try:
            with urllib.request.urlopen(url+'/api/projects') as response:
                self.assertEqual(json.load(response)[0]['id'], self.project)
            for headers in ({'Origin': 'https://attacker.example'}, {'Host': 'attacker.example'}):
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(urllib.request.Request(url+'/api/projects', headers=headers))
                self.assertEqual(error.exception.code, 403)
                error.exception.close()
            body = json.dumps({'name': 'map_create_node', 'arguments': {'project': self.project, 'kind': 'task', 'title': 'HTTP'}}).encode()
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(urllib.request.Request(url+'/api/call', data=body))
            self.assertEqual(error.exception.code, 403)
            error.exception.close()
            with urllib.request.urlopen(urllib.request.Request(url+'/api/call', data=body, headers={'X-Project-Map': '1'})) as response:
                self.assertEqual(json.load(response)['title'], 'HTTP')
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_worktree_from_clean_commit(self):
        def git(*args):
            return subprocess.run(['git', '-C', str(self.source), *args], capture_output=True, text=True, timeout=10)
        self.assertEqual(git('init').returncode, 0)
        git('add', 'source.txt')
        self.assertEqual(git('-c', 'user.name=Map Test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'fixture').returncode, 0)
        n = self.node()
        result = self.store.worktree(n['id'])
        self.assertTrue(Path(result['path'], 'source.txt').exists())
        self.assertTrue(result['branch'].startswith('codex/'))
        self.assertEqual(git('status', '--porcelain').stdout.strip(), '')
        git('worktree', 'remove', result['path'])

    def test_changed_requirements_or_runner_invalidate_receipt(self):
        n = self.node(criteria='Первое условие')
        self.store.set_runner(self.project, [sys.executable, '-c', 'print("ok")'])
        self.store.run_check(n['id'])
        with self.assertRaises(ValueError):
            self.store.update_node(n['id'], 1, status='done', criteria='Другое условие', evidence='ok')
        self.store.set_runner(self.project, [sys.executable, '-c', 'print("different")'])
        with self.assertRaises(ValueError):
            self.store.update_node(n['id'], 1, status='done', evidence='ok')


if __name__ == '__main__':
    unittest.main()
