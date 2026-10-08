"""Run the protected local trial backend with owner-only runtime credentials."""

import json
import os
from pathlib import Path

import uvicorn
from dotenv import dotenv_values

from app.web_api import create_app


ROOT = Path(__file__).resolve().parents[1]


def runtime_config(path=None):
    file = Path(path or Path(os.environ['LOCALAPPDATA']) /
                'DocumentStandardAI' / 'external-runtime.json')
    data = json.loads(file.read_text(encoding='utf-8'))
    if (set(data) != {'gateway_secret', 'hwp_token'}
            or any(not isinstance(data[key], str) or len(data[key]) < 32
                   for key in data)):
        raise ValueError('외부 시험 서버의 인증 설정이 올바르지 않음')
    return data


def serve(*, port=8603, runtime_file=None):
    config = runtime_config(runtime_file)
    # OAuth client credentials stay in the untracked operator file. The
    # gateway/HWP secrets continue to come from the existing runtime JSON.
    for key in ('GMAIL_CLIENT_ID', 'GMAIL_CLIENT_SECRET', 'GMAIL_REDIRECT_URI'):
        value = dotenv_values(ROOT / '.runtime' / 'external-server.env').get(key)
        if value:
            os.environ[key] = value
    app = create_app(root=ROOT / 'data' / 'external-users',
                     secret=config['gateway_secret'],
                     hwp_url='http://127.0.0.1:8602',
                     hwp_token=config['hwp_token'])
    uvicorn.run(app, host='127.0.0.1', port=port, log_level='warning')


if __name__ == '__main__':
    serve()
