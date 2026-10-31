import concurrent.futures
from pathlib import Path
import tempfile
import unittest
from core import Store
from capture import capture


class CaptureTests(unittest.TestCase):
    def test_desktop_metadata_is_not_a_request_title(self):
        prompt='<in-app-browser-context source="ambient-ui-state">not user text</in-app-browser-context>\n## My request:\nСделай карту удобнее'
        capture(self.store,self.base|{'hook_event_name':'UserPromptSubmit','prompt':prompt})
        node=self.store.snapshot(self.project)['nodes'][0]
        self.assertEqual(node['title'],'Сделай карту удобнее')
        self.assertNotIn('ambient-ui-state',node['body'])
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'db.sqlite3')
        self.project = self.store.create_project('Hook fixture', str(self.root))['id']
        self.base = {'cwd': str(self.root), 'session_id': 's1', 'turn_id': 't1'}

    def tearDown(self):
        self.temp.cleanup()

    def test_prompt_and_patch_automatically_become_linked_nodes(self):
        prompt = self.base | {'hook_event_name': 'UserPromptSubmit', 'prompt': 'Исправь сохранение заметки'}
        response = capture(self.store, prompt)
        self.assertIn('additionalContext', response['hookSpecificOutput'])
        capture(self.store, self.base | {'hook_event_name': 'PostToolUse', 'tool_name': 'apply_patch', 'tool_use_id': 'tool1', 'tool_input': {'command': 'SECRET PATCH'}})
        snap = self.store.snapshot(self.project)
        self.assertEqual([n['kind'] for n in snap['nodes']], ['request', 'fix'])
        self.assertEqual(snap['nodes'][1]['parent'], snap['nodes'][0]['id'])
        self.assertNotIn('SECRET PATCH', str(snap))
        self.assertNotEqual(snap['nodes'][1]['status'], 'done')

    def test_repeated_concurrent_delivery_deduplicates(self):
        prompt = self.base | {'hook_event_name': 'UserPromptSubmit', 'prompt': 'One request'}
        with concurrent.futures.ThreadPoolExecutor(3) as pool:
            list(pool.map(lambda _: capture(self.store, prompt), range(6)))
        snap = self.store.snapshot(self.project)
        self.assertEqual(len(snap['nodes']), 1)
        self.assertEqual(len(snap['events']), 2)

    def test_unknown_folder_and_missing_ids_not_collected(self):
        capture(self.store, self.base | {'cwd': str(self.root.parent), 'hook_event_name': 'UserPromptSubmit', 'prompt': 'private'})
        capture(self.store, {'cwd': str(self.root), 'hook_event_name': 'UserPromptSubmit', 'prompt': 'no stable ID'})
        self.assertEqual(len(self.store.snapshot(self.project)['nodes']), 0)

    def test_stop_does_not_claim_completion(self):
        capture(self.store, self.base | {'hook_event_name': 'Stop'})
        snap = self.store.snapshot(self.project)
        self.assertEqual(snap['events'][0]['kind'], 'turn.finished')
        self.assertEqual(snap['nodes'], [])

    def test_other_tool_calls_are_not_collected(self):
        capture(self.store, self.base | {'hook_event_name': 'PostToolUse', 'tool_name': 'Bash', 'tool_use_id': 't2'})
        self.assertEqual(self.store.snapshot(self.project)['nodes'], [])

    def test_plan_tasks_deduplicated_and_completed_is_review(self):
        event=self.base|{'hook_event_name':'PostToolUse','tool_name':'update_plan','tool_input':{'plan':[{'step':'Implement a feature','status':'in_progress'}]}}
        capture(self.store,event);capture(self.store,event)
        self.assertEqual(len(self.store.snapshot(self.project)['nodes']),1)
        event['tool_input']['plan'][0]['status']='completed'
        capture(self.store,event)
        self.assertEqual(self.store.snapshot(self.project)['nodes'][0]['status'],'review')

    def test_plan_does_not_reactivate_cancelled_or_deferred_task(self):
        event=self.base|{'hook_event_name':'PostToolUse','tool_name':'update_plan','tool_input':{'plan':[{'step':'Stop this task','status':'in_progress'}]}}
        capture(self.store,event)
        for status in ('cancelled','deferred'):
            n=self.store.snapshot(self.project)['nodes'][0]
            self.store.update_node(n['id'],n['version'],status=status)
            capture(self.store,event)
            self.assertEqual(self.store.snapshot(self.project)['nodes'][0]['status'],status)

    def test_shell_changes_and_untracked_edits_detected(self):
        import subprocess
        subprocess.run(['git','init',str(self.root)],capture_output=True,check=True)
        (self.root/'.gitignore').write_text('db.sqlite3*\n')
        subprocess.run(['git','-C',str(self.root),'add','.gitignore'],capture_output=True,check=True)
        subprocess.run(['git','-C',str(self.root),'-c','user.name=Test','-c','user.email=t@example.invalid','commit','-m','fixture'],capture_output=True,check=True)
        event=self.base|{'hook_event_name':'PostToolUse','tool_name':'Bash','tool_use_id':'one'}
        (self.root/'new.txt').write_text('first')
        capture(self.store,event)
        event['tool_use_id']='two';capture(self.store,event)
        self.assertEqual(len(self.store.snapshot(self.project)['nodes']),1)
        (self.root/'new.txt').write_text('second')
        event['tool_use_id']='three';capture(self.store,event)
        self.assertEqual(len(self.store.snapshot(self.project)['nodes']),2)


if __name__ == '__main__':
    unittest.main()
