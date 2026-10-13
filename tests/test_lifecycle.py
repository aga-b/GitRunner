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
        self.assertIn(slot,self.docker.removed)
        self.assertTrue(json.loads((self.scaler.state/'status.json').read_text())['errors'])

    def test_rate_limit_does_not_skip_local_reconciliation(self):
        slot=self.add(age=12000)
        self.scaler.next_refresh=time.time()+300
        def limited(*a,**k): raise APIError(429,'/actions/runners',retry_after=3600)
        self.api.pages=limited
        self.scaler.tick()
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


if __name__=='__main__': unittest.main()
