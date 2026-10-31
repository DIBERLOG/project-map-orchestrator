from pathlib import Path
import subprocess
import tempfile
import unittest
from core import Store
from knowledge import retrieve,graph,archive


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.repo=self.root/'repo';self.repo.mkdir()
        subprocess.run(['git','init',str(self.repo)],capture_output=True,check=True)
        self.store=Store(self.root/'db.sqlite3')
        self.project=self.store.create_project('test',str(self.repo))['id']

    def tearDown(self):self.tmp.cleanup()

    def test_retrieval_reads_current_files_with_lines(self):
        p=self.repo/'notes.md';p.write_text('alpha\nneedle\nomega',encoding='utf-8')
        result=retrieve(self.store,self.project,'needle')
        self.assertEqual(result['results'][0]['path'],'notes.md')
        self.assertEqual(result['results'][0]['line'],1)
        p.write_text('new content',encoding='utf-8')
        self.assertEqual(retrieve(self.store,self.project,'needle')['results'],[])
        (self.repo/'.env').write_text('needle secret')
        self.assertEqual(retrieve(self.store,self.project,'secret')['results'],[])

    def test_python_graph_reports_unresolved_calls_and_parse_errors(self):
        (self.repo/'main.py').write_text('def hello():\n    save()\n',encoding='utf-8')
        (self.repo/'broken.py').write_text('def invalid(',encoding='utf-8')
        result=graph(self.store,self.project)
        self.assertEqual(result['symbols'][0]['name'],'hello')
        self.assertEqual(result['calls'][0]['target'],'save')
        self.assertFalse(result['calls'][0]['resolved'])
        self.assertIn('broken.py',result['skipped'])

    def test_archive_not_limited_to_snapshot_history(self):
        n=self.store.create_node(self.project,'goal','Keep history')
        for i in range(305):
            self.store.update_node(n['id'],i+1,body=str(i))
        self.assertEqual(len(self.store.snapshot(self.project)['events']),300)
        result=archive(self.store,self.project)
        self.assertEqual(len(result['events']),307)
        self.assertEqual(result['format_version'],1)
