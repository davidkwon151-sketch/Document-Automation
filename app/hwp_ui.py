"""API-free HWP conversion tool; unavailable native engines never yield downloads."""

from hashlib import sha256
from pathlib import Path
import tempfile

import streamlit as st

from parsers.hancom import convert_hwp, _validate_hwpx


def convert_uploaded_hwp(filename, data):
    if not isinstance(data, bytes) or Path(filename).suffix.lower() != '.hwp':
        raise ValueError('HWP 파일을 첨부해 주세요.')
    with tempfile.TemporaryDirectory(prefix='ra-hwp-upload-') as directory:
        source = Path(directory) / 'uploaded.hwp'
        source.write_bytes(data)
        output = convert_hwp(source, output_dir=Path(directory) / 'converted')
        if sha256(source.read_bytes()).hexdigest() != sha256(data).hexdigest():
            raise ValueError('HWP 변환 중 입력 파일이 변경됨')
        _validate_hwpx(output)
        result = output.read_bytes()
    return {'filename': Path(filename).stem + '.hwpx', 'data': result,
            'source_sha256': sha256(data).hexdigest(), 'output_sha256': sha256(result).hexdigest()}


def render_hwp_converter():
    with st.expander('HWP → HWPX 자동 변환'):
        st.caption('설치된 한글과 활성화된 공식 보안 모듈로 변환합니다. API 잔액은 필요하지 않습니다. 원본은 보존하며 암호·DRM·서명 보호를 해제하지 않습니다.')
        upload = st.file_uploader('변환할 HWP 파일', type=['hwp'], key='hwp_conversion_upload')
        fingerprint = (upload.name, sha256(upload.getvalue()).hexdigest()) if upload else None
        if st.session_state.get('hwp_conversion_input') != fingerprint:
            st.session_state.pop('hwp_conversion_result', None)
            st.session_state['hwp_conversion_input'] = fingerprint
        if st.button('HWPX로 변환', key='hwp_conversion_run', disabled=upload is None):
            st.session_state.pop('hwp_conversion_result', None)
            try:
                with st.spinner('원본을 보존하며 한글 변환과 결과 재열기를 확인하고 있습니다.'):
                    st.session_state['hwp_conversion_result'] = convert_uploaded_hwp(upload.name, upload.getvalue())
            except (ValueError, RuntimeError, OSError, TimeoutError) as exc:
                st.error(str(exc))
        result = st.session_state.get('hwp_conversion_result')
        if result is not None:
            st.success('HWPX 구조와 재열기를 확인했습니다. 원본과의 전체 배치는 한글에서 확인해 주세요.')
            st.download_button('변환한 HWPX 다운로드', result['data'], result['filename'],
                               mime='application/vnd.hancom.hwpx', key='hwp_conversion_download')
