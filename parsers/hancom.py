"""Convert unprotected HWP5 through an owned Hancom instance, preserving originals."""
from hashlib import file_digest
import json
import os
from pathlib import Path
import shutil
import struct
import stat
import subprocess
import tempfile
import zipfile
import zlib

import olefile
from . import legacy
from .extract import parse_hwpx,ParseError

HERE=Path(__file__).resolve().parent
MODULE_NAME='FilePathCheckerModuleExample'
REGISTRY_KEY=r'Software\HNC\HwpAutomation\Modules'
MAX_FILE_BYTES=legacy.MAX_FILE_BYTES
CONVERSION_TIMEOUT=60
HWP_MESSAGE='HWP 변환용 보안 모듈 등록·한글 자동화 소유권을 확인하지 못함. 한글에서 원본을 HWPX로 다른 이름으로 저장해 주세요.'
HWP_ALLOWED_FLAGS=(1<<0)|(1<<5)|(1<<6)|(1<<11)
DEFAULT_JS_SETUP='varDocuments=XHwpDocuments;varDocument=Documents.Active_XHwpDocument;'
DEFAULT_JS_EMPTY_NEW='functionOnDocument_New(){//todo:}'

def _plain_stream(package,name,flags):
 data=package.openstream(name).read(16385)
 if len(data)>16384:raise ValueError('HWP 스크립트 정보 용량이 너무 큼')
 if flags&1:
  decoder=zlib.decompressobj(-15)
  data=decoder.decompress(data,16385)
  if len(data)>16384 or decoder.unconsumed_tail or not decoder.eof:
   raise ValueError('HWP 스크립트 정보 압축이 잘못됨')
  if decoder.unused_data and decoder.unused_data!=struct.pack('<II',zlib.crc32(data),len(data)):
   raise ValueError('HWP 스크립트 정보 압축 체크섬이 잘못됨')
 return data

def _empty_script_metadata(package,names,flags):
 scripts=[name for name in names if name[0].lower()=='scripts']
 if not scripts:return
 if {tuple(name) for name in scripts}!={('Scripts','DefaultJScript'),('Scripts','JScriptVersion')}:
  raise ValueError('미확인 스크립트 저장소가 있는 HWP는 자동 변환하지 않음')
 if _plain_stream(package,'Scripts/JScriptVersion',flags)!=b'\x01\x00\x00\x00\x00\x00\x00\x00':
  raise ValueError('미확인 HWP 스크립트 버전임')
 data=_plain_stream(package,'Scripts/DefaultJScript',flags)
 texts=[];offset=0
 while offset+4<=len(data) and len(texts)<16:
  length=struct.unpack_from('<I',data,offset)[0];offset+=4
  if length==0xffffffff:
   if offset!=len(data):raise ValueError('HWP 기본 스크립트 끝 정보가 잘못됨')
   break
  if length>8192 or offset+length*2>len(data):raise ValueError('HWP 스크립트 문자 길이가 잘못됨')
  try:text=data[offset:offset+length*2].decode('utf-16le')
  except UnicodeError:raise ValueError('HWP 스크립트 문자가 손상됨') from None
  texts.append(''.join(text.split()));offset+=length*2
 else:raise ValueError('HWP 기본 스크립트 구조가 잘못됨')
 if offset!=len(data) or len(texts)!=4 or any(text not in {'',DEFAULT_JS_SETUP,DEFAULT_JS_EMPTY_NEW} for text in texts):
  raise ValueError('실행 내용 또는 미확인 스크립트가 있는 HWP는 자동 변환하지 않음')
 if texts[0] not in {'',DEFAULT_JS_SETUP} or texts[1] not in {'',DEFAULT_JS_EMPTY_NEW} or any(texts[2:]):
  raise ValueError('HWP 기본 스크립트 순서가 잘못됨')

def _hash(path):
 with Path(path).open('rb') as stream:return file_digest(stream,'sha256').hexdigest()

def _validate_hwp(source):
 if not source.is_file() or not 0<source.stat().st_size<=MAX_FILE_BYTES:
  raise ValueError('HWP 파일은 비어 있지 않은 50 MiB 이하 파일이어야 함')
 if not olefile.isOleFile(str(source)):
  raise ValueError('HWP5 복합 파일이 아님. HWPX 원본을 첨부해 주세요.')
 try:
  with olefile.OleFileIO(str(source)) as package:
   if not package.exists('FileHeader'):raise ValueError('HWP 인식 정보가 없음')
   header=package.openstream('FileHeader').read(256)
   if len(header)<40 or header[:32].rstrip(b'\x00')!=b'HWP Document File':raise ValueError('HWP 인식 정보가 잘못됨')
   version,flags=struct.unpack_from('<II',header,32)
   if version>>24!=5:raise ValueError('HWP5만 자동 변환함. HWPX 원본을 첨부해 주세요.')
   if flags & ~HWP_ALLOWED_FLAGS:raise ValueError('암호·DRM·배포·서명·스크립트·미확인 보안 HWP는 자동 변환하지 않음')
   names=package.listdir()
   if not any(len(name)==2 and name[0]=='BodyText' and name[1].startswith('Section') for name in names):
    raise ValueError('HWP 본문 구역이 없음')
   if any(any('signature' in part.lower() for part in name) for name in names):
    raise ValueError('서명 부품이 있는 HWP는 자동 변환하지 않음')
   _empty_script_metadata(package,names,flags)
 except ValueError:raise
 except Exception:raise ValueError('HWP 복합 파일이 손상됨. HWPX 원본을 첨부해 주세요.') from None

def registered_module_path():
 if os.name!='nt':return None
 import winreg
 try:
  with winreg.OpenKey(winreg.HKEY_CURRENT_USER,REGISTRY_KEY,0,winreg.KEY_READ) as key:
   value,kind=winreg.QueryValueEx(key,MODULE_NAME)
  if kind!=winreg.REG_SZ or not isinstance(value,str):return None
  path=Path(value)
  return path if path.is_absolute() and path.suffix.lower()=='.dll' and path.is_file() else None
 except (OSError,ValueError):return None

def _run_worker(command,owner,directory,timeout):
 options={'stdout':subprocess.DEVNULL,'stderr':subprocess.DEVNULL}
 if os.name=='nt':options['creationflags']=subprocess.CREATE_NO_WINDOW
 process=subprocess.Popen(command,**options)
 try:
  process.wait(timeout=timeout)
  if process.returncode:raise RuntimeError(HWP_MESSAGE)
 except subprocess.TimeoutExpired:
  process.kill();process.wait(timeout=5)
  raise TimeoutError('HWP 변환 제한시간을 초과함') from None
 finally:
  if owner.is_file():
   cleanup=directory/'hwp-cleanup.ps1'
   cleanup.write_bytes((HERE/'hwp_cleanup.ps1').read_bytes())
   subprocess.run([legacy._powershell(),'-NoProfile','-NonInteractive','-File',str(cleanup),'-OwnerFile',str(owner)],
    stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=5,
    creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)

def _hwp_worker(directory,source=None,target=None,timeout=60):
 worker,owner,result=(directory/name for name in ('hwp-worker.ps1','owner.json','result.json'))
 worker.write_text((HERE/'hwp_worker.ps1').read_text(encoding='utf-8'),encoding='utf-8-sig')
 command=[legacy._powershell(),'-NoProfile','-NonInteractive','-File',str(worker),'-OwnerFile',str(owner),'-ResultFile',str(result)]
 if source is not None:command+=['-InputPath',str(source),'-OutputPath',str(target)]
 _run_worker(command,owner,directory,timeout)
 if not result.is_file():raise RuntimeError(HWP_MESSAGE)
 try:data=json.loads(result.read_text(encoding='utf-8-sig'))
 except (ValueError,OSError):raise RuntimeError('HWP 변환 확인 기록이 손상됨') from None
 if (not isinstance(data,dict) or data.get('status') not in {'available','converted','unavailable'}
     or type(data.get('module_registered')) is not bool or type(data.get('ownership_verified')) is not bool
     or data.get('status') in {'available','converted'} and not (data['module_registered'] and data['ownership_verified'])):
  raise RuntimeError('HWP 자동화 확인 기록의 상태가 잘못됨')
 return data

def hwp_capability():
 registered=registered_module_path() is not None
 result={'available':False,'formats':['.hwp'],'module_registration_present':registered,
         'module_active':False,'ownership_verified':False,'reason':HWP_MESSAGE}
 if not registered:
  result['reason']='HWP 자동 변환 보안 모듈의 사용자 등록 경로를 확인하지 못함. 원본을 HWPX로 저장해 주세요.'
  return result
 if not legacy._powershell():
  result['reason']='보안 모듈 등록은 있으나 Windows 한글 자동화 실행 환경을 확인하지 못함. HWPX 원본을 첨부해 주세요.'
  return result
 try:
  with tempfile.TemporaryDirectory(prefix='report-hwp-probe-') as name:
   response=_hwp_worker(Path(name),timeout=20)
   result.update(available=response['status']=='available',module_active=response['module_registered'],
                 ownership_verified=response['ownership_verified'])
   if result['available']:result['reason']=''
   elif response.get('reason')=='module_activation_failed':
    result['reason']='보안 모듈 등록은 있으나 한글의 RegisterModule 활성화가 실패함. 자동 변환은 보류하며 HWPX 원본을 첨부해 주세요.'
   else:result['reason']='보안 모듈 등록은 있으나 새 한글 자동화 인스턴스의 소유권·활성화를 확인하지 못함. HWPX 원본을 첨부해 주세요.'
 except (OSError,RuntimeError,TimeoutError,subprocess.SubprocessError):
  result['reason']='보안 모듈 등록은 있으나 한글 자동화 확인이 실패하거나 제한시간을 초과함. HWPX 원본을 첨부해 주세요.'
 return result

def hwp_available():return hwp_capability()['available']

def _validate_hwpx(path):
 if not path.is_file() or not 0<path.stat().st_size<=MAX_FILE_BYTES:raise ValueError('HWPX 변환 결과가 없거나 용량이 잘못됨')
 try:
  with zipfile.ZipFile(path) as package:
   legacy._check_package_limits(package)
   if package.testzip() is not None:raise ValueError('HWPX 패키지가 손상됨')
  parse_hwpx(path)
 except (ParseError,zipfile.BadZipFile,OSError):raise ValueError('변환 결과가 정상 HWPX가 아님') from None

def _publish(candidate, output, source, before):
 """Publish a complete file atomically without replacing an existing destination."""
 output.parent.mkdir(parents=True,exist_ok=True)
 descriptor,name=tempfile.mkstemp(prefix='.hwp-',suffix='.tmp',dir=output.parent)
 temporary=Path(name)
 try:
  with os.fdopen(descriptor,'wb') as stream,candidate.open('rb') as converted:
   shutil.copyfileobj(converted,stream)
   stream.flush();os.fsync(stream.fileno())
  if _hash(temporary)!=_hash(candidate) or _hash(source)!=before:
   raise ValueError('최종 저장 전에 변환 결과 또는 원본이 변경됨')
  # Same-directory hard link is atomic and fails if any destination already exists.
  os.link(temporary,output)
  if _hash(source)!=before:
   if output.samefile(temporary):output.unlink()
   raise ValueError('최종 저장 중 원본이 변경됨')
 finally:
  temporary.unlink(missing_ok=True)

def convert_hwp(path,output_dir=None,engine=None):
 source=Path(path).resolve()
 if source.suffix.lower()!='.hwp':raise ValueError('HWP 원본만 HWPX로 변환함')
 _validate_hwp(source)
 before=_hash(source)
 destination=Path(output_dir).resolve() if output_dir is not None else source.parent
 output=destination/(source.stem+'.hwpx')
 if output.exists():raise FileExistsError('HWPX 출력이 이미 있음. 다른 출력 폴더를 사용해 주세요.')
 if not callable(engine):
  if engine not in (None,'hwp_com'):raise RuntimeError(HWP_MESSAGE)
  capability=hwp_capability()
  if not capability['available']:raise RuntimeError(capability['reason'])
 with tempfile.TemporaryDirectory(prefix='report-hwp-convert-') as name:
  directory=Path(name);copied=directory/'input.hwp';candidate=directory/'converted.hwpx'
  shutil.copyfile(source,copied)
  os.chmod(copied,stat.S_IREAD)
  if _hash(copied)!=before or _hash(source)!=before:raise ValueError('변환 전에 원본 또는 사본이 변경됨')
  try:
   if callable(engine):engine(copied,candidate)
   else:
    response=_hwp_worker(directory,copied,candidate,timeout=CONVERSION_TIMEOUT)
    if response['status']!='converted' or not (response['module_registered'] and response['ownership_verified']):raise RuntimeError(HWP_MESSAGE)
   if _hash(copied)!=before or _hash(source)!=before:raise ValueError('변환 중 원본 또는 읽기용 사본이 변경됨')
   _validate_hwpx(candidate)
   _publish(candidate,output,source,before)
  finally:
   os.chmod(copied,stat.S_IWRITE)
   if _hash(source)!=before:raise ValueError('변환 중 원본이 변경됨')
 return output
