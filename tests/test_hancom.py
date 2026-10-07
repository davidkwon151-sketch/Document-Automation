"""Offline Hancom conversion contracts; no real COM or network calls."""
from io import BytesIO
import importlib.util
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import zlib

import pytest

ROOT=Path(__file__).resolve().parents[1]
from parsers import hancom as hwp

@pytest.fixture
def source(tmp_path,monkeypatch):
 path=tmp_path/'업무 양식.hwp';path.write_bytes(hwp.legacy.OLE_SIGNATURE+b'private synthetic fixture')
 state={'flags':0,'version':0x05000302,'missing_header':False,'names':[['FileHeader'],['BodyText','Section0']]}
 class Container:
  def __init__(self,*args):pass
  def __enter__(self):return self
  def __exit__(self,*args):pass
  def exists(self,name):return not state['missing_header']
  def openstream(self,name):
   return BytesIO(b'HWP Document File'.ljust(32,b'\0')+struct.pack('<II',state['version'],state['flags'])+bytes(216))
  def listdir(self):return state['names']
 monkeypatch.setattr(hwp.olefile,'isOleFile',lambda path:True)
 monkeypatch.setattr(hwp.olefile,'OleFileIO',Container)
 return path,state

def engine(copied,output):shutil.copyfile(ROOT/'samples/sample_company_form.hwpx',output)

def test_original_copy_preserved_hwpx_reopened_and_private_directory_removed(source,tmp_path):
 path,state=source;original=path.read_bytes();calls=[]
 def convert(copied,output):
  assert copied!=path and copied.name=='input.hwp' and copied.read_bytes()==original
  calls.append(copied.parent)
  engine(copied,output)
 output=hwp.convert_hwp(path,tmp_path/'outputs',engine=convert)
 assert output.name=='업무 양식.hwpx' and output.is_file()
 assert hwp.parse_hwpx(output)['본문']
 assert path.read_bytes()==original and not calls[0].exists()

@pytest.mark.parametrize('bit',[1,2,3,4,7,8,9,10,12,13,14,15,31])
def test_protected_signed_script_unknown_flags_rejected_before_engine(source,bit):
 path,state=source;state['flags']=1<<bit;called=[]
 with pytest.raises(ValueError,match='보안|서명|스크립트'):
  hwp.convert_hwp(path,engine=lambda *args:called.append(args))
 assert not called and not path.with_suffix('.hwpx').exists()

@pytest.mark.parametrize('mode',['old_version','missing_header','missing_body','script_storage','signature_storage'])
def test_bad_or_unsafe_container_rejected_before_engine(source,mode):
 path,state=source
 if mode=='old_version':state['version']=0x04000000
 elif mode=='missing_header':state['missing_header']=True
 elif mode=='missing_body':state['names']=[['FileHeader']]
 elif mode=='script_storage':state['names'].append(['Scripts','InjectedScript'])
 else:state['names'].append(['DigitalSignature','Signature'])
 called=[]
 with pytest.raises(ValueError):hwp.convert_hwp(path,engine=lambda *args:called.append(args))
 assert not called

@pytest.mark.parametrize('mode',['invalid_output','missing_output','failure','timeout','mutated_copy','existing_output'])
def test_failed_conversion_never_saves_partial_output_or_changes_original(source,tmp_path,mode):
 path,_=source;original=path.read_bytes();out=tmp_path/'out';out.mkdir()
 target=out/(path.stem+'.hwpx')
 if mode=='existing_output':target.write_bytes(b'existing')
 def convert(copied,candidate):
  if mode=='missing_output':return
  if mode=='failure':raise RuntimeError('converter failed')
  if mode=='timeout':raise TimeoutError('test bound')
  if mode=='mutated_copy':copied.write_bytes(b'changed');engine(copied,candidate)
  else:candidate.write_bytes(b'invalid archive')
 with pytest.raises((ValueError,RuntimeError,TimeoutError,FileExistsError,PermissionError)):
  hwp.convert_hwp(path,out,engine=convert)
 assert path.read_bytes()==original
 assert target.read_bytes()==b'existing' if mode=='existing_output' else not target.exists()

def test_no_registration_never_launches_hancom_and_cannot_be_forced(source,monkeypatch):
 path,_=source
 monkeypatch.setattr(hwp.legacy,'_powershell',lambda:'powershell.exe')
 monkeypatch.setattr(hwp,'registered_module_path',lambda:None)
 called=[];monkeypatch.setattr(hwp,'_hwp_worker',lambda *args,**kwargs:called.append(args))
 assert hwp.hwp_available() is False
 with pytest.raises(RuntimeError,match='HWPX'):hwp.convert_hwp(path,engine='hwp_com')
 assert not called

def test_available_requires_actual_successful_module_probe_not_registry_presence(monkeypatch,tmp_path):
 monkeypatch.setattr(hwp.legacy,'_powershell',lambda:'powershell.exe')
 dll=tmp_path/'official.dll';dll.write_bytes(b'synthetic')
 monkeypatch.setattr(hwp,'registered_module_path',lambda:dll)
 called=[]
 def probe(directory,**kwargs):called.append(kwargs);return {'status':'available','module_registered':True,'ownership_verified':True}
 monkeypatch.setattr(hwp,'_hwp_worker',probe)
 assert hwp.hwp_available() and called==[{'timeout':20}]
 def failure(*args,**kwargs):raise RuntimeError('not registered')
 monkeypatch.setattr(hwp,'_hwp_worker',failure)
 assert hwp.hwp_available() is False

def test_worker_module_before_open_and_only_owned_blank_automation_quit():
 text=(hwp.HERE/'hwp_worker.ps1').read_text(encoding='utf-8')
 assert text.index("RegisterModule('FilePathCheckDLL','FilePathCheckerModuleExample')")<text.index('$app.Open(')
 for value in ('$ownerPid -in $priorIds',"$process.ProcessName -ne 'Hwp'",'$process.StartTime.ToUniversalTime()',
               '$created.CommandLine -notmatch',"$app.GetTextFile('TEXT','')",'$count -gt 1',".Open($InputPath,'HWP','suspendpassword:true;forceopen:true;setcurdir:false')",'$owned=$true'):
  assert value in text
 assert text.index('$owned=$true')<text.index('.Visible=$false')
 assert "if($owned){try{[void]$app.Clear(1);[void]$app.Quit()}catch{}}" in text
 cleanup=(hwp.HERE/'hwp_cleanup.ps1').read_text(encoding='utf-8')
 assert "$owner.process_name -ne 'Hwp'" in cleanup
 assert '$process.StartTime.ToUniversalTime().Ticks -eq [long]$owner.start_ticks' in cleanup
 assert 'taskkill' not in cleanup and '/IM' not in cleanup

def test_native_subprocess_timeout_cleanup_uses_owner_identity_and_no_named_kill(monkeypatch,tmp_path):
 calls=[]
 class Process:
  returncode=0
  def wait(self,timeout=None):
   if timeout==60:raise subprocess.TimeoutExpired('worker',timeout)
  def kill(self):calls.append('own-worker-killed')
 monkeypatch.setattr(hwp.subprocess,'Popen',lambda *args,**kwargs:Process())
 monkeypatch.setattr(hwp.subprocess,'run',lambda command,**kwargs:calls.append(command))
 monkeypatch.setattr(hwp.legacy,'_powershell',lambda:'powershell.exe')
 owner=tmp_path/'owner.json';owner.write_text('{"pid":123,"start_ticks":456,"process_name":"Hwp"}')
 with pytest.raises(TimeoutError,match='제한시간'):hwp._run_worker(['fake'],owner,tmp_path,60)
 assert 'own-worker-killed' in calls
 assert any(isinstance(item,list) and '-OwnerFile' in item for item in calls)
 assert not any(isinstance(item,list) and 'taskkill' in item for item in calls)

def test_private_command_does_not_interpolate_filename_into_script(monkeypatch,tmp_path):
 source=tmp_path/'quote`$test.hwp';target=tmp_path/'result.hwpx'
 calls=[]
 monkeypatch.setattr(hwp.legacy,'_powershell',lambda:'powershell.exe')
 def run(command,owner,directory,timeout):
  calls.append((command,timeout))
  (directory/'result.json').write_text('{"status":"converted","module_registered":true,"ownership_verified":true}')
 monkeypatch.setattr(hwp,'_run_worker',run)
 assert hwp._hwp_worker(tmp_path,source,target)['status']=='converted'
 command,timeout=calls[0]
 assert command[command.index('-InputPath')+1]==str(source)
 assert timeout==60 and str(source) not in (tmp_path/'hwp-worker.ps1').read_text(encoding='utf-8-sig')

@pytest.mark.parametrize('texts',[
 ['','','',''],
 ['var Documents = XHwpDocuments;\r\nvar Document = Documents.Active_XHwpDocument;\r\n','','',''],
 ['var Documents = XHwpDocuments;\r\nvar Document = Documents.Active_XHwpDocument;\r\n','function OnDocument_New()\r\n{\r\n\t//todo : \r\n}\r\n\r\n','',''],
])
def test_exact_default_empty_script_storage_is_not_active_macro(texts):
 data=b''.join(struct.pack('<I',len(t))+t.encode('utf-16le') for t in texts)+struct.pack('<I',0xffffffff)
 streams={'Scripts/DefaultJScript':data,'Scripts/JScriptVersion':b'\x01'+bytes(7)}
 class Package:
  def openstream(self,name):return BytesIO(streams[name])
 names=[['Scripts','DefaultJScript'],['Scripts','JScriptVersion']]
 hwp._empty_script_metadata(Package(),names,0)

@pytest.mark.parametrize('bad',['var Evil = 1;','function OnDocument_New(){ launch(); }','function OnDocument_New(){//todo:\nlaunch();}','var Documents = XHwpDocuments;var Document = Documents.Active_XHwpDocument;launch();'])
def test_added_code_cannot_be_mistaken_for_empty_default_script(bad):
 texts=[bad,'','','']
 data=b''.join(struct.pack('<I',len(t))+t.encode('utf-16le') for t in texts)+struct.pack('<I',0xffffffff)
 class Package:
  def openstream(self,name):return BytesIO(data if name.endswith('DefaultJScript') else b'\x01'+bytes(7))
 with pytest.raises(ValueError):hwp._empty_script_metadata(Package(),[['Scripts','DefaultJScript'],['Scripts','JScriptVersion']],0)

def test_existing_public_hwp_default_metadata_can_be_validated_without_com():
 for relative in ['nonghyup_bank_complaint.hwp','business/business_iris_honam_agreement_2026.hwp','business/business_kosmes_carbon_forms_2026.hwp']:
  path=ROOT/'data/public_templates'/relative
  if not path.is_file():pytest.skip('Separately acquired public HWP corpus missing')
  before=path.read_bytes()
  hwp._validate_hwp(path)
  assert path.read_bytes()==before

@pytest.mark.parametrize('trailer',['valid_crc','invalid_crc','extra_code','no_trailer'])
def test_compressed_script_crc_and_size_trailer_is_checked(trailer):
 data=b'\x01'+bytes(7)
 encoder=zlib.compressobj(wbits=-15)
 raw=encoder.compress(data)+encoder.flush()
 if trailer=='valid_crc':raw+=struct.pack('<II',zlib.crc32(data),len(data))
 elif trailer=='invalid_crc':raw+=struct.pack('<II',zlib.crc32(data)^1,len(data))
 elif trailer=='extra_code':raw+=b'added executable'
 class Package:
  def openstream(self,name):return BytesIO(raw)
 if trailer in {'valid_crc','no_trailer'}:assert hwp._plain_stream(Package(),'Scripts/JScriptVersion',1)==data
 else:
  with pytest.raises(ValueError,match='체크섬'):hwp._plain_stream(Package(),'Scripts/JScriptVersion',1)

def test_registration_present_but_module_activation_false_is_never_available(monkeypatch,tmp_path):
 dll=tmp_path/'official.dll';dll.write_bytes(b'candidate fixture')
 monkeypatch.setattr(hwp,'registered_module_path',lambda:dll)
 monkeypatch.setattr(hwp.legacy,'_powershell',lambda:'powershell.exe')
 monkeypatch.setattr(hwp,'_hwp_worker',lambda *args,**kwargs:{'status':'unavailable','module_registered':False,
  'ownership_verified':True,'reason':'module_activation_failed'})
 cap=hwp.hwp_capability()
 assert not cap['available'] and cap['module_registration_present']
 assert not cap['module_active'] and cap['ownership_verified']
 assert '등록은 있으나' in cap['reason'] and '활성화가 실패' in cap['reason']
 assert hwp.hwp_available() is False

def test_native_worker_requires_os_readonly_copy_and_documents_supported_options():
 text=(hwp.HERE/'hwp_worker.ps1').read_text(encoding='utf-8')
 assert '[System.IO.FileAttributes]::ReadOnly' in text
 assert text.index('[System.IO.FileAttributes]::ReadOnly')<text.index('$app.Open(')
 assert 'readonly:true' not in text

def test_native_success_contract_reopens_hwpx_without_touching_original(source,tmp_path,monkeypatch):
 path,_=source;before=path.read_bytes();calls=[]
 monkeypatch.setattr(hwp,'hwp_capability',lambda:{'available':True})
 def worker(directory,copied,candidate,**kwargs):
  calls.append(kwargs)
  assert copied.read_bytes()==before and copied!=path
  engine(copied,candidate)
  return {'status':'converted','module_registered':True,'ownership_verified':True}
 monkeypatch.setattr(hwp,'_hwp_worker',worker)
 output=hwp.legacy.convert_legacy(path,tmp_path/'result',engine='hwp_com')
 assert output.suffix=='.hwpx' and hwp.parse_hwpx(output)['본문']
 assert calls==[{'timeout':60}] and path.read_bytes()==before

@pytest.mark.parametrize('proof',[
 {'status':'converted','module_registered':False,'ownership_verified':True},
 {'status':'converted','module_registered':True,'ownership_verified':False},
 {'status':'unavailable','module_registered':True,'ownership_verified':True},
])
def test_native_result_cannot_bypass_activation_or_ownership(source,tmp_path,monkeypatch,proof):
 path,_=source
 monkeypatch.setattr(hwp,'hwp_capability',lambda:{'available':True})
 def worker(directory,copied,candidate,**kwargs):
  engine(copied,candidate);return proof
 monkeypatch.setattr(hwp,'_hwp_worker',worker)
 with pytest.raises(RuntimeError):hwp.convert_hwp(path,tmp_path/'out')
 assert not (tmp_path/'out').exists()

@pytest.mark.parametrize('proof',[
 {'status':'available','module_registered':False,'ownership_verified':True},
 {'status':'converted','module_registered':True,'ownership_verified':False},
 {'status':'invented','module_registered':True,'ownership_verified':True},
 {'status':'available','module_registered':1,'ownership_verified':True},
])
def test_worker_receipt_invalid_states_are_blocked(monkeypatch,tmp_path,proof):
 import json
 monkeypatch.setattr(hwp.legacy,'_powershell',lambda:'powershell.exe')
 def run(command,owner,directory,timeout):
  (directory/'result.json').write_text(json.dumps(proof),encoding='utf-8')
 monkeypatch.setattr(hwp,'_run_worker',run)
 with pytest.raises(RuntimeError,match='상태'):hwp._hwp_worker(tmp_path)

def test_atomic_publication_preserves_competing_destination(source,tmp_path):
 path,_=source;out=tmp_path/'out';out.mkdir();target=out/(path.stem+'.hwpx')
 def convert(copied,candidate):
  engine(copied,candidate);target.write_bytes(b'concurrent destination')
 with pytest.raises(FileExistsError):hwp.convert_hwp(path,out,convert)
 assert target.read_bytes()==b'concurrent destination'
 assert not list(out.glob('.hwp-*.tmp'))

def test_atomic_copy_failure_leaves_no_partial_final_or_staging(source,tmp_path,monkeypatch):
 path,_=source;out=tmp_path/'out';original=shutil.copyfileobj
 def fail(source_stream,target_stream,*args,**kwargs):
  target_stream.write(b'partial');raise OSError('disk full fixture')
 monkeypatch.setattr(hwp.shutil,'copyfileobj',fail)
 with pytest.raises(OSError):hwp.convert_hwp(path,out,engine)
 assert not list(out.iterdir())

@pytest.mark.parametrize('enabled',[False,True])
def test_catalog_requires_actual_hancom_activation(monkeypatch,enabled):
 proof={'available':enabled,'formats':['.hwp'],'module_registration_present':True,
        'module_active':enabled,'ownership_verified':True,'reason':'' if enabled else '등록은 있으나 활성화 실패'}
 monkeypatch.setattr(hwp,'hwp_capability',lambda:dict(proof))
 monkeypatch.setattr(hwp.legacy,'_office_available',lambda kind:False)
 assert hwp.legacy.available_converters()['hwp']==proof
 assert '.hwp' not in hwp.legacy.TARGETS
