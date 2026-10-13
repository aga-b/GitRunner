import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'controller'))
from policy import accepts_job, eligible_repository, plan, trusted_run


class PolicyTests(unittest.TestCase):
    def test_global_cap_with_busy_and_idle(self):
        self.assertEqual(plan({'a':10,'b':10},{'a':1},{'a':2,'b':1},4,4),['a'])

    def test_idle_capacity_is_reserved(self):
        self.assertEqual(plan({'a':3},{'a':3},{'a':3},4,4),[])

    def test_many_jobs_allocate_only_limit(self):
        self.assertEqual(plan({'a':99},{},{},4,4),['a']*4)

    def test_fair_across_repos(self):
        self.assertEqual(plan({'a':10,'b':10,'c':10},{},{},4,4),['a','b','c','a'])

    def test_per_repo_limit(self):
        self.assertEqual(plan({'a':10,'b':10},{},{'a':1},4,2),['a','b','b'])

    def test_cap_reduction_does_not_allocate(self):
        self.assertEqual(plan({'a':10},{},{'a':4},2,4),[])

    def test_only_target_labels(self):
        labels={'self-hosted','linux','x64','ci-linux'}
        self.assertTrue(accepts_job({'status':'queued','labels':['self-hosted','Linux','X64','ci-linux']},labels,'ci-linux'))
        self.assertFalse(accepts_job({'status':'queued','labels':['ubuntu-latest']},labels,'ci-linux'))
        self.assertFalse(accepts_job({'status':'queued','labels':['ci-linux','gpu']},labels,'ci-linux'))
        self.assertFalse(accepts_job({'status':'in_progress','labels':['ci-linux']},labels,'ci-linux'))

    def test_repos_only_owner(self):
        repo={'full_name':'aga-b/demo','owner':{'login':'aga-b'}}
        self.assertTrue(eligible_repository(repo,'aga-b',set(),set()))
        self.assertFalse(eligible_repository(repo,'other',set(),set()))
        self.assertFalse(eligible_repository({**repo,'archived':True},'aga-b',set(),set()))
        self.assertFalse(eligible_repository(repo,'aga-b',set(),{'aga-b/demo'}))
        self.assertFalse(eligible_repository(repo,'aga-b',{'aga-b/another'},set()))

    def test_trusted_runs_and_forks(self):
        repo={'id':1}
        run={'actor':{'login':'aga-b'},'event':'push'}
        self.assertTrue(trusted_run(run,repo,{'aga-b'}))
        self.assertFalse(trusted_run({**run,'actor':{'login':'outsider'}},repo,{'aga-b'}))
        self.assertFalse(trusted_run({**run,'event':'pull_request_target'},repo,{'aga-b'}))
        pr={**run,'event':'pull_request','pull_requests':[{'head':{'repo':{'id':1}}}]}
        self.assertTrue(trusted_run(pr,repo,{'aga-b'}))
        self.assertFalse(trusted_run({**pr,'pull_requests':[{'head':{'repo':{'id':2}}}]},repo,{'aga-b'}))
        self.assertFalse(trusted_run({**pr,'pull_requests':[]},repo,{'aga-b'}))
        self.assertFalse(trusted_run({**pr,'pull_requests':[{'head':{'repo':None}}]},repo,{'aga-b'}))
        self.assertFalse(trusted_run({**pr,'pull_requests':[{'head':None}]},repo,{'aga-b'}))


if __name__=='__main__': unittest.main()
