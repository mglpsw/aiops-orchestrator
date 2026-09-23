"""Bounded tests of this distribution, not upstream semantic qualification."""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
import copy
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(os.environ.get('MPACK_TEST_ROOT',Path(__file__).resolve().parents[1])).resolve()
sys.path.insert(0,str(ROOT/'tools'))
import mpack
import generate_views
from jsonschema import Draft202012Validator

class PackFixture(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name)
        self.pack=self.base/'pack'
        shutil.copytree(ROOT,self.pack)
    def manifest(self):
        return json.loads((self.pack/mpack.MANIFEST).read_text())
    def save_manifest(self,m):
        (self.pack/mpack.MANIFEST).write_text(json.dumps(m,ensure_ascii=False,indent=2)+'\n')
    def repo(self):
        p=self.base/'repository with spaces'; p.mkdir()
        subprocess.run(['git','init','--quiet',str(p)],check=True,capture_output=True)
        (p/'AGENTS.md').write_bytes(b'Existing project instructions: preserve me.\n')
        (p/'app.txt').write_bytes(b'Existing product: preserve me.\n')
        return p

class IntegrityTests(PackFixture):
    def test_valid_pack_positive(self):
        p=mpack.load_pack(self.pack)
        self.assertGreater(len(p.files),25)
        self.assertEqual(p.version,'0.1.0-preview.1')
    def test_tampered_member_refused(self):
        p=self.pack/'method/CORE.md'
        b=p.read_bytes(); p.write_bytes(b.replace(b'Core',b'C0re',1))
        self.assertEqual(len(b),len(p.read_bytes()))
        with self.assertRaisesRegex(mpack.PackError,'MEMBER_BYTES_MISMATCH'):
            mpack.load_pack(self.pack)
    def test_truncated_member_refused(self):
        p=self.pack/'method/CORE.md'; p.write_bytes(p.read_bytes()[:-1])
        with self.assertRaisesRegex(mpack.PackError,'MEMBER_BYTES_MISMATCH'):
            mpack.load_pack(self.pack)
    def test_unexpected_member_refused(self):
        (self.pack/'extra.txt').write_text('not declared')
        with self.assertRaisesRegex(mpack.PackError,'MEMBERSHIP_MISMATCH'):
            mpack.load_pack(self.pack)
    def test_missing_member_refused(self):
        (self.pack/'method/CORE.md').unlink()
        with self.assertRaisesRegex(mpack.PackError,'MEMBERSHIP_MISMATCH'):
            mpack.load_pack(self.pack)
    def test_duplicate_member_refused(self):
        m=self.manifest(); m['files'].append(m['files'][0]); self.save_manifest(m)
        with self.assertRaisesRegex(mpack.PackError,'DUPLICATE_OR_SELF'):
            mpack.load_pack(self.pack)
    def test_case_alias_refused(self):
        m=self.manifest(); row=copy.deepcopy(m['files'][0]); row['path']=row['path'].swapcase(); m['files'].append(row); self.save_manifest(m)
        with self.assertRaisesRegex(mpack.PackError,'DUPLICATE_OR_SELF'):
            mpack.load_pack(self.pack)
    def test_parent_traversal_refused(self):
        m=self.manifest(); m['files'][0]['path']='../escape'; self.save_manifest(m)
        with self.assertRaisesRegex(mpack.PackError,'UNSAFE_PATH'):
            mpack.load_pack(self.pack)
    def test_absolute_path_refused(self):
        m=self.manifest(); m['files'][0]['path']='/escape'; self.save_manifest(m)
        with self.assertRaisesRegex(mpack.PackError,'UNSAFE_PATH'):
            mpack.load_pack(self.pack)
    def test_windows_reserved_names_refused(self):
        for value in ['CON.txt','folder/NUL','COM1','LPT9.dat','C:/tmp/a','a\\b','a./b','a//b','./a']:
            with self.subTest(value=value),self.assertRaises(mpack.PackError):
                mpack.valid_relative_path(value)
    def test_regular_paths_positive(self):
        for value in ['method/CORE.md','templates/task.json','tools/mpack.py']:
            self.assertEqual(mpack.valid_relative_path(value),value)
    def test_manifest_self_reference_refused(self):
        m=self.manifest(); m['files'][0]['path']=mpack.MANIFEST; self.save_manifest(m)
        with self.assertRaisesRegex(mpack.PackError,'DUPLICATE_OR_SELF'):
            mpack.load_pack(self.pack)
    def test_boolean_size_refused(self):
        m=self.manifest(); m['files'][0]['size']=True; self.save_manifest(m)
        with self.assertRaisesRegex(mpack.PackError,'MANIFEST_SIZE'):
            mpack.load_pack(self.pack)
    def test_unknown_manifest_field_refused(self):
        m=self.manifest(); m['approved']=True; self.save_manifest(m)
        with self.assertRaisesRegex(mpack.PackError,'MANIFEST_SHAPE'):
            mpack.load_pack(self.pack)
    def test_noncanonical_order_refused(self):
        m=self.manifest(); m['files'].reverse(); self.save_manifest(m)
        with self.assertRaisesRegex(mpack.PackError,'NON_CANONICAL'):
            mpack.load_pack(self.pack)
    def test_wrong_external_anchor_refused(self):
        with self.assertRaisesRegex(mpack.PackError,'MANIFEST_ANCHOR_MISMATCH'):
            mpack.load_pack(self.pack,'0'*64)
    def test_correct_external_anchor_positive(self):
        anchor=mpack.sha256((self.pack/mpack.MANIFEST).read_bytes())
        self.assertEqual(mpack.load_pack(self.pack,anchor).manifest_digest,anchor)
    def test_rehash_does_not_authenticate_origin(self):
        anchor=mpack.sha256((self.pack/mpack.MANIFEST).read_bytes())
        p=self.pack/'method/CORE.md'; p.write_bytes(p.read_bytes()+b'\nChanged by hypothetical distributor.\n')
        m=self.manifest()
        for r in m['files']:
            if r['path']=='method/CORE.md': r.update(size=p.stat().st_size,sha256=mpack.sha256(p.read_bytes()))
        self.save_manifest(m)
        self.assertIsInstance(mpack.load_pack(self.pack),mpack.LoadedPack) # consistency, not authenticity
        with self.assertRaisesRegex(mpack.PackError,'MANIFEST_ANCHOR_MISMATCH'):
            mpack.load_pack(self.pack,anchor)
    def test_symlink_member_refused(self):
        p=self.pack/'method/CORE.md'; target=self.base/'actual'; p.rename(target); p.symlink_to(target)
        with self.assertRaisesRegex(mpack.PackError,'NON_REGULAR_FILE'):
            mpack.load_pack(self.pack)
    def test_symlink_directory_refused(self):
        (self.pack/'external').symlink_to(self.base,target_is_directory=True)
        with self.assertRaisesRegex(mpack.PackError,'NON_REGULAR_DIRECTORY'):
            mpack.load_pack(self.pack)
    def test_symlink_root_refused(self):
        alias=self.base/'alias'; alias.symlink_to(self.pack,target_is_directory=True)
        with self.assertRaisesRegex(mpack.PackError,'INVALID_PACK_ROOT'):
            mpack.load_pack(alias)
    def test_manifest_metadata_identity_refused(self):
        m=self.manifest(); m['version']='other-version'; self.save_manifest(m)
        with self.assertRaisesRegex(mpack.PackError,'IDENTITY_MISMATCH'):
            mpack.load_pack(self.pack)
    def test_duplicate_json_key_refused(self):
        with self.assertRaisesRegex(mpack.PackError,'DUPLICATE_JSON_KEY'):
            mpack.read_json_bytes(b'{"key":1,"key":2}')
    def test_nan_refused(self):
        with self.assertRaisesRegex(mpack.PackError,'NON_JSON_CONSTANT'):
            mpack.read_json_bytes(b'{"key":NaN}')
    def test_invalid_utf8_refused(self):
        with self.assertRaisesRegex(mpack.PackError,'INVALID_JSON'):
            mpack.read_json_bytes(b'\xff')
    def test_loaded_snapshot_immutable(self):
        p=mpack.load_pack(self.pack)
        with self.assertRaises(TypeError): p.files['new']=b'x'
    def test_snapshot_bytes_survive_source_change(self):
        p=mpack.load_pack(self.pack)
        before=p.files['method/CORE.md']
        (self.pack/'method/CORE.md').write_text('changed after read')
        self.assertEqual(before,p.files['method/CORE.md'])
    def test_empty_universe_refused(self):
        m=self.manifest(); m['files']=[]; self.save_manifest(m)
        with self.assertRaisesRegex(mpack.PackError,'MANIFEST_FILE_DOMAIN'):
            mpack.load_pack(self.pack)

class InstallationTests(PackFixture):
    def test_forged_snapshot_extra_path_refused(self):
        repo=self.repo(); p=mpack.load_pack(self.pack)
        forged=dict(p.files); forged['../../escape']=b'not allowed'
        carrier=mpack.LoadedPack(p.version,p.manifest_bytes,p.manifest_digest,forged)
        with self.assertRaisesRegex(mpack.PackError,'INSTALL_SNAPSHOT_MEMBERSHIP'):
            mpack.install(carrier,repo,True)
        self.assertFalse((repo/'.aocm').exists())
    def test_forged_snapshot_changed_bytes_refused(self):
        repo=self.repo(); p=mpack.load_pack(self.pack)
        forged=dict(p.files); forged['method/CORE.md']=b'different bytes'
        carrier=mpack.LoadedPack(p.version,p.manifest_bytes,p.manifest_digest,forged)
        with self.assertRaisesRegex(mpack.PackError,'INSTALL_SNAPSHOT_BYTES'):
            mpack.install(carrier,repo,True)
        self.assertFalse((repo/'.aocm').exists())
    def test_dry_run_write_zero(self):
        repo=self.repo(); before=set(repo.rglob('*'))
        result=mpack.install(mpack.load_pack(self.pack),repo)
        self.assertEqual(set(repo.rglob('*')),before)
        self.assertFalse(result['apply'])
        self.assertFalse((repo/'.aocm').exists())
    def test_install_preserves_existing_product_and_instructions(self):
        repo=self.repo(); before={p.relative_to(repo):p.read_bytes() for p in repo.rglob('*') if p.is_file()}
        result=mpack.install(mpack.load_pack(self.pack),repo,True)
        self.assertTrue(result['apply'])
        for name,b in before.items(): self.assertEqual((repo/name).read_bytes(),b)
        added=[p.relative_to(repo).parts[0] for p in repo.rglob('*') if p.is_file() and p.relative_to(repo) not in before]
        self.assertTrue(added); self.assertEqual(set(added),{'.aocm'})
        prof=json.loads((repo/'.aocm/repository-profile.json').read_text())
        self.assertEqual(prof['lifecycle'],'UNADOPTED')
        self.assertFalse((repo/'.aocm/INSTALL_INCOMPLETE.json').exists())
    def test_existing_destination_refused_without_mutation(self):
        repo=self.repo(); target=repo/'.aocm'; target.mkdir(); sentinel=target/'keep'; sentinel.write_bytes(b'original')
        with self.assertRaisesRegex(mpack.PackError,'DESTINATION_EXISTS'):
            mpack.install(mpack.load_pack(self.pack),repo,True)
        self.assertEqual(list(target.iterdir()),[sentinel]); self.assertEqual(sentinel.read_bytes(),b'original')
    def test_second_install_refused(self):
        repo=self.repo(); p=mpack.load_pack(self.pack); mpack.install(p,repo,True)
        with self.assertRaisesRegex(mpack.PackError,'DESTINATION_EXISTS'):
            mpack.install(p,repo,True)
    def test_target_symlink_refused(self):
        repo=self.repo(); outside=self.base/'outside'; outside.mkdir(); (repo/'.aocm').symlink_to(outside,target_is_directory=True)
        with self.assertRaisesRegex(mpack.PackError,'DESTINATION_EXISTS'):
            mpack.install(mpack.load_pack(self.pack),repo,True)
        self.assertEqual(list(outside.iterdir()),[])
    def test_subdirectory_not_repo_root_refused(self):
        repo=self.repo(); sub=repo/'src'; sub.mkdir()
        with self.assertRaisesRegex(mpack.PackError,'EXACT_REPOSITORY_ROOT_REQUIRED'):
            mpack.install(mpack.load_pack(self.pack),sub,True)
    def test_non_git_refused(self):
        p=self.base/'notgit'; p.mkdir()
        with self.assertRaisesRegex(mpack.PackError,'GIT_REPOSITORY_UNAVAILABLE'):
            mpack.install(mpack.load_pack(self.pack),p,True)
    def test_doctor_before_and_after(self):
        repo=self.repo()
        self.assertEqual(mpack.doctor(repo)['installation'],'ABSENT')
        mpack.install(mpack.load_pack(self.pack),repo,True)
        d=mpack.doctor(repo)
        self.assertEqual(d['installation'],'BYTE_INTEGRITY_VALID')
        self.assertFalse(d['semantic_verdict_issued'])
    def test_failure_leaves_incomplete_marker(self):
        repo=self.repo(); p=mpack.load_pack(self.pack)
        real=mpack._exclusive_write
        def fail(path,data):
            if path.name=='CORE.md': raise OSError('controlled test fault')
            real(path,data)
        with patch.object(mpack,'_exclusive_write',side_effect=fail):
            with self.assertRaisesRegex(mpack.PackError,'INSTALL_INCOMPLETE'):
                mpack.install(p,repo,True)
        self.assertTrue((repo/'.aocm/INSTALL_INCOMPLETE.json').exists())
        self.assertFalse((repo/'.aocm/INSTALL_RECEIPT.json').exists())
        with self.assertRaisesRegex(mpack.PackError,'INSTALLATION_INCOMPLETE'):
            mpack.doctor(repo)
    def test_install_uses_captured_verified_bytes(self):
        repo=self.repo(); p=mpack.load_pack(self.pack); before=p.files['method/CORE.md']
        (self.pack/'method/CORE.md').write_text('mutated after acquisition')
        mpack.install(p,repo,True)
        self.assertEqual((repo/'.aocm/vendor/method/CORE.md').read_bytes(),before)

class DocumentContractTests(unittest.TestCase):
    def load(self,path): return json.loads((ROOT/path).read_text())
    def test_json_all_parse_strictly(self):
        files=list(ROOT.rglob('*.json')); self.assertTrue(files)
        for p in files: mpack.read_json_bytes(p.read_bytes())
    def test_schemas_are_valid(self):
        for p in (ROOT/'schemas').glob('*.schema.json'):
            Draft202012Validator.check_schema(json.loads(p.read_text()))
    def test_all_json_templates_conform(self):
        for stem in ('repository-profile','task','observation'):
            Draft202012Validator(self.load(f'schemas/{stem}.schema.json')).validate(self.load(f'templates/{stem}.json'))
    def test_adopted_profile_without_decision_refused(self):
        p=self.load('templates/repository-profile.json'); p['lifecycle']='ADOPTED_BY_MAINTAINER'
        self.assertTrue(list(Draft202012Validator(self.load('schemas/repository-profile.schema.json')).iter_errors(p)))
    def test_profile_with_declared_decision_shape_positive(self):
        p=self.load('templates/repository-profile.json'); p.update(lifecycle='ADOPTED_BY_MAINTAINER',decision_record='synthetic owner decision',authority_by_domain=[dict(domain='example',owner='example-owner',source='example-contract')])
        Draft202012Validator(self.load('schemas/repository-profile.schema.json')).validate(p)
        # Shape acceptance explicitly does not authenticate the decision.
    def test_task_cannot_contain_execution_result(self):
        p=self.load('templates/task.json'); p['run_passed']=True
        self.assertTrue(list(Draft202012Validator(self.load('schemas/task.schema.json')).iter_errors(p)))
    def test_unexecuted_observation_cannot_claim_readiness(self):
        p=self.load('templates/observation.json'); p['disposition']='READY_FOR_HUMAN_DECISION'
        self.assertTrue(list(Draft202012Validator(self.load('schemas/observation.schema.json')).iter_errors(p)))
    def test_unexecuted_observation_cannot_embed_evidence(self):
        p=self.load('templates/observation.json'); p['evidence']=[dict(locator='fake',consumed_subject='fake',result='fake',limitations=[])]
        self.assertTrue(list(Draft202012Validator(self.load('schemas/observation.schema.json')).iter_errors(p)))
    def test_knowledge_refs_resolve_to_declared_sources(self):
        sources=self.load('provenance/SOURCE_REGISTRY.json')['sources']
        ids={s['source_id'] for s in sources}; self.assertEqual(len(ids),len(sources))
        items=self.load('provenance/KNOWLEDGE_REGISTER.json')['items']
        self.assertEqual(len({i['knowledge_id'] for i in items}),len(items))
        for i in items:
            self.assertTrue(i['source_refs']); self.assertTrue(set(i['source_refs'])<=ids)
            self.assertTrue((ROOT/i['content_locator']).is_file())
            self.assertEqual(i['upstream_normative_effect'],'NONE')
    def test_candidate_maturity_is_not_silently_promoted(self):
        items={i['knowledge_id']:i for i in self.load('provenance/KNOWLEDGE_REGISTER.json')['items']}
        for kid in ('MP-K07','MP-K08','MP-K11'):
            self.assertEqual(items[kid]['pack_role'],'PROFILE_CANDIDATE')
            self.assertIn('UPSTREAM_',items[kid]['source_maturity'])
    def test_git_sources_have_exact_commit_locators(self):
        for s in self.load('provenance/SOURCE_REGISTRY.json')['sources']:
            if s['commit'] is not None:
                self.assertRegex(s['commit'],r'^[0-9a-f]{40}$')
                self.assertIn('/'+s['commit']+'/',s['locator'])
    def test_no_raw_bytes_hash_invented_for_connector_text(self):
        for s in self.load('provenance/SOURCE_REGISTRY.json')['sources']:
            if s['acquisition']=='CONNECTOR_TEXT_READ':
                self.assertIsNone(s['raw_bytes_sha256_recomputed'])
    def test_generated_chat_view_matches_owners(self):
        for v in self.load('provenance/GENERATED_VIEWS.json')['views']:
            self.assertEqual((ROOT/v['path']).read_text(),generate_views.render(ROOT,v))
    def test_all_named_source_ids_in_documents_exist(self):
        ids={s['source_id'] for s in self.load('provenance/SOURCE_REGISTRY.json')['sources']}
        for p in ROOT.rglob('*.md'):
            found=set(re.findall(r'\bS\d\d\b',p.read_text()))
            self.assertTrue(found<=ids,(p,found-ids))
    def test_all_named_knowledge_ids_exist(self):
        ids={i['knowledge_id'] for i in self.load('provenance/KNOWLEDGE_REGISTER.json')['items']}
        for p in ROOT.rglob('*.md'):
            self.assertTrue(set(re.findall(r'MP-K\d\d',p.read_text()))<=ids)
    def test_cli_does_not_claim_future_commands_implemented(self):
        self.assertEqual(self.load('PACK.json')['implemented_commands'],['verify','install','doctor'])
    def test_cli_verify_reports_no_semantic_verdict(self):
        p=subprocess.run([sys.executable,'-B',str(ROOT/'tools/mpack.py'),'verify','--root',str(ROOT)],capture_output=True,text=True,timeout=20)
        self.assertEqual(p.returncode,0,p.stderr)
        result=json.loads(p.stdout)
        self.assertEqual(result['disposition'],'PACKAGE_INTEGRITY_VALID')
        self.assertFalse(result['semantic_verdict_issued']); self.assertFalse(result['authority_granted'])

if __name__=='__main__': unittest.main()
