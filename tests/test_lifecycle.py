import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'controller'))
from main import Scaler
from docker_host import PREFIX
from github_api import APIError


class FakeDocker:
    def __init__(self): self.containers=[]; self.removed=[]; self.calls=[]
    def objects(self,kind,pool): return self.containers if kind=='container' else []
    def call(self,*args,**kwargs): self.calls.append(args); return b'' if kwargs.get('raw') else ''
    def remove_slot(self,pool,slot):
        self.removed.append(slot)
        self.containers=[c for c in self.containers if c['Config']['Labels'][PREFIX+'.slot']!=slot]


class FakeAPI:
    def __init__(self): self.calls=[]; self.repos=[]; self.fail_delete=False
    def request(self,method,path,body=None):
        self.calls.append((method,path,body))
        if method=='DELETE' and self.fail_delete: raise APIError(403,path)
        return {'approval_policy':'first_time_contributors'}
    def repositories(self): return self.repos


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'STATE_DIR':self.tmp.name,'DIND_IMAGE':'docker:test',
                                        'POOL_NAME':'test-ci','MAX_RUNNERS':'4'},clear=True)
        self.env.start()
        self.api,self.docker=FakeAPI(),FakeDocker()
        self.scaler=Scaler(self.api,self.docker)
        self.scaler.memory_available_mb=lambda: 10**6

    def tearDown(self): self.env.stop(); self.tmp.cleanup()

    def add(self,slot='test-ci-1-aaaaaaaa',status='running',age=0):
        born=int(time.time()-age)
        self.scaler.record(slot,{'slot':slot,'repo':'aga-b/demo','born':born,'runner_id':42})
        self.docker.containers.append({'Id':slot,'Config':{'Labels':{
            PREFIX+'.slot':slot,PREFIX+'.repo':'aga-b/demo',PREFIX+'.born':str(born),
            PREFIX+'.pool':'test-ci',PREFIX+'.role':'runner',PREFIX+'.runner-id':'42'}},
            'State':{'Status':status}})
        return slot

    def test_controller_restart_adopts_running_jobs(self):
        slot=self.add(age=100)
        restarted=Scaler(self.api,self.docker)
        active,idle=restarted.reconcile({'aga-b/demo':{slot:{'busy':True,'id':42}}})
        self.assertEqual(active['aga-b/demo'],1)
        self.assertEqual(idle['aga-b/demo'],0)
        self.assertEqual(self.docker.removed,[])

    def test_restart_remembers_job_that_was_busy(self):
        slot=self.add(age=1000)
        self.scaler.reconcile({'aga-b/demo':{slot:{'busy':True,'id':42}}})
        restarted=Scaler(self.api,self.docker)
        active,idle=restarted.reconcile({'aga-b/demo':{}})
        self.assertEqual(active['aga-b/demo'],1)
        self.assertEqual(idle['aga-b/demo'],0)
        self.assertEqual(self.docker.removed,[])

    def test_discovery_outage_still_enforces_lifetime(self):
        slot=self.add(age=12000)
        def unavailable(): raise APIError(503,'/installation/repositories')
        self.api.repositories=unavailable
        self.api.pages=lambda *a,**k: iter([])
        with patch.object(self.scaler,'provision') as provision:
            self.scaler.tick()
            provision.assert_not_called()
        self.scaler.wait_retired()
        self.assertIn(slot,self.docker.removed)
        self.assertTrue(json.loads((self.scaler.state/'status.json').read_text())['errors'])

    def test_rate_limit_does_not_skip_local_reconciliation(self):
        slot=self.add(age=12000)
        self.scaler.next_refresh=time.time()+300
        def limited(*a,**k): raise APIError(429,'/actions/runners',retry_after=3600)
        self.api.pages=limited
        self.scaler.tick()
        self.scaler.wait_retired()
        self.assertIn(slot,self.docker.removed)
        self.assertGreater(self.scaler.cooldown['aga-b/demo'],time.time()+3500)

    def test_default_global_capacity_is_two(self):
        os.environ.pop('MAX_RUNNERS')
        self.assertEqual(Scaler(self.api,self.docker).maximum,2)

    def test_done_job_removes_registration_and_resources(self):
        slot=self.add(status='exited')
        active,idle=self.scaler.reconcile({'aga-b/demo':{}})
        self.assertEqual(sum(active.values()),0)
        self.assertIn(slot,self.docker.removed)
        self.assertIn(('DELETE','/repos/aga-b/demo/actions/runners/42',None),self.api.calls)
        self.assertFalse((self.scaler.state/'provisioning'/f'{slot}.json').exists())

    def test_registration_delay_reserves_slot(self):
        self.add(age=30)
        active,idle=self.scaler.reconcile({'aga-b/demo':{}})
        self.assertEqual((active['aga-b/demo'],idle['aga-b/demo']),(1,1))

    def test_busy_job_is_not_idle_timed_out(self):
        slot=self.add(age=1000)
        active,_=self.scaler.reconcile({'aga-b/demo':{slot:{'busy':True,'id':42}}})
        self.assertEqual(active['aga-b/demo'],1)
        self.assertEqual(self.docker.removed,[])

    def test_api_outage_does_not_infer_idle_timeout(self):
        self.add(age=1000)
        active,_=self.scaler.reconcile({})
        self.assertEqual(active['aga-b/demo'],1)
        self.assertEqual(self.docker.removed,[])

    def test_idle_runner_is_stopped_and_removed(self):
        slot=self.add(age=1000)
        active,_=self.scaler.reconcile({'aga-b/demo':{}})
        self.assertEqual(sum(active.values()),0)
        self.assertIn(('stop','--time','30',slot),self.docker.calls)

    def test_lifetime_limit_stops_even_busy_runner(self):
        slot=self.add(age=12000)
        active,_=self.scaler.reconcile({'aga-b/demo':{slot:{'busy':True,'id':42}}})
        self.assertEqual(sum(active.values()),0)
        stop_index=next(i for i,c in enumerate(self.docker.calls) if c[0]=='stop')
        log_index=next(i for i,c in enumerate(self.docker.calls) if c[0]=='logs')
        self.assertLess(stop_index,log_index)

    def test_cleanup_failure_still_reserves_capacity(self):
        self.add(status='exited')
        self.api.fail_delete=True
        active,_=self.scaler.reconcile({'aga-b/demo':{}})
        self.assertEqual(active['aga-b/demo'],1)
        self.assertEqual(self.docker.removed,[])

    def test_interrupted_provisioning_reclaimed(self):
        slot='test-ci-1-bbbbbbbb'
        self.scaler.record(slot,{'slot':slot,'repo':'aga-b/demo','born':int(time.time()-1000),'runner_id':None})
        active,_=self.scaler.reconcile({'aga-b/demo':{}})
        self.assertEqual(sum(active.values()),0)
        self.assertIn(slot,self.docker.removed)

    def test_public_repo_requires_external_approval(self):
        self.api.repos=[{'full_name':'aga-b/public','owner':{'login':'aga-b'},'private':False}]
        self.scaler.refresh_repositories()
        self.assertEqual(len(self.scaler.repos),1)
        self.assertIn(('PUT','/repos/aga-b/public/actions/permissions/fork-pr-contributor-approval',
                       {'approval_policy':'all_external_contributors'}),self.api.calls)

    def test_drain_does_not_spawn(self):
        self.scaler.repos=[{'full_name':'aga-b/demo','owner':{'login':'aga-b'},'id':1}]
        self.scaler.next_refresh=time.time()+300
        self.api.pages=lambda *a,**k: iter([])
        self.scaler.queue=lambda r: [{'id':1}]*20
        (self.scaler.state/'drain').touch()
        with patch.object(self.scaler,'provision') as provision:
            self.scaler.tick()
            provision.assert_not_called()
        self.assertTrue(json.loads((self.scaler.state/'status.json').read_text())['draining'])

    def test_failed_provision_cleans_slot_and_registration(self):
        def boom(repo,slot,now,data):
            data['runner_id']=77
            self.scaler.record(slot,data)
            raise RuntimeError('Per-job Docker daemon did not become ready')
        with patch.object(self.scaler,'_provision',side_effect=boom):
            with self.assertRaises(RuntimeError):
                self.scaler.provision('aga-b/demo')
        self.assertEqual(len(self.docker.removed),1)
        self.assertIn(('DELETE','/repos/aga-b/demo/actions/runners/77',None),self.api.calls)
        self.assertEqual(list((self.scaler.state/'provisioning').glob('*.json')),[])

    def test_failed_provision_does_not_block_other_repos(self):
        self.scaler.repos=[{'full_name':f'aga-b/r{i}','owner':{'login':'aga-b'},'id':i} for i in range(3)]
        self.scaler.next_refresh=time.time()+300
        self.api.pages=lambda *a,**k: iter([])
        self.scaler.queue=lambda r: [{'id':1,'created_at':'2026-01-01T00:00:00Z'}]
        calls=[]
        def fake(repo):
            calls.append(repo)
            if repo=='aga-b/r1': raise RuntimeError('boom')
            return 1.0
        with patch.object(self.scaler,'provision',side_effect=fake):
            self.scaler.tick()
        self.assertEqual(sorted(calls),['aga-b/r0','aga-b/r1','aga-b/r2'])
        self.assertGreater(self.scaler.cooldown['aga-b/r1'],time.time())
        self.assertLess(self.scaler.cooldown['aga-b/r1'],time.time()+31)
        status=json.loads((self.scaler.state/'status.json').read_text())
        self.assertEqual(status['active'].get('aga-b/r0'),1)
        self.assertEqual(status['metrics']['provisioned_this_tick'],3)
        self.assertGreater(status['metrics']['oldest_queued_seconds'],0)

    def test_provisioning_runs_in_parallel(self):
        self.scaler.repos=[{'full_name':f'aga-b/r{i}','owner':{'login':'aga-b'},'id':i} for i in range(4)]
        self.scaler.next_refresh=time.time()+300
        self.api.pages=lambda *a,**k: iter([])
        self.scaler.queue=lambda r: [{'id':1}]
        def slow(repo):
            time.sleep(0.3); return 0.3
        begun=time.time()
        with patch.object(self.scaler,'provision',side_effect=slow) as provision:
            self.scaler.tick()
        self.assertEqual(provision.call_count,4)
        self.assertLess(time.time()-begun,0.9)

    def test_memory_admission_limits_new_runners(self):
        self.scaler.repos=[{'full_name':'aga-b/demo','owner':{'login':'aga-b'},'id':1}]
        self.scaler.next_refresh=time.time()+300
        self.api.pages=lambda *a,**k: iter([])
        self.scaler.queue=lambda r: [{'id':1}]*4
        self.scaler.min_free_mb=1000; self.scaler.job_reserve_mb=1000
        with patch.object(self.scaler,'memory_available_mb',return_value=3500), \
             patch.object(self.scaler,'provision') as provision:
            self.scaler.tick()
        self.assertEqual(provision.call_count,2)

    def test_retire_is_deferred_not_blocking_tick(self):
        slot=self.add(status='exited')
        gate=__import__('threading').Event()
        original=self.scaler.retire
        def slow(*a,**k): gate.wait(2); return original(*a,**k)
        with patch.object(self.scaler,'retire',side_effect=slow):
            active,_=self.scaler.reconcile({'aga-b/demo':{}},background=True)
            self.assertEqual(sum(active.values()),0)
            self.assertEqual(self.docker.removed,[])
            gate.set(); self.scaler.wait_retired()
        self.assertIn(slot,self.docker.removed)

    def test_pending_workflow_run_with_queued_matching_job_is_admitted(self):
        repo={'full_name':'aga-b/demo','owner':{'login':'aga-b'},'id':1}
        run={'id':123,'actor':{'login':'aga-b'},'event':'push'}
        job={'id':456,'status':'queued','labels':['self-hosted','linux','x64','ci-linux']}
        queried=[]
        def pages(path,key=None,cached=False):
            queried.append(path)
            if path.endswith('/actions/runners'):
                return iter([])
            if path.endswith('/actions/runs?status=pending'):
                return iter([run])
            if path.endswith('/actions/runs/123/jobs?filter=latest'):
                return iter([job])
            return iter([])
        self.api.pages=pages
        self.scaler.repos=[repo]
        self.scaler.next_refresh=time.time()+300
        with patch.object(self.scaler,'provision') as provision:
            self.scaler.tick()
        provision.assert_called_once_with('aga-b/demo')
        self.assertIn('/repos/aga-b/demo/actions/runs?status=pending',queried)
        status=json.loads((self.scaler.state/'status.json').read_text())
        self.assertEqual(status['queued']['aga-b/demo'],1)


if __name__=='__main__': unittest.main()
