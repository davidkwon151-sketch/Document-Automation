import fs from 'node:fs';
import path from 'node:path';

const source = path.resolve('dist');
const output = path.resolve('vercel_output');
const pages = ['index.html', 'styles.css', 'app.js', 'workspace.html',
  'workspace.css', 'workspace.js', 'sales.html', 'sales.css', 'sales.js',
  'common.html', 'common.css', 'common.js', 'access.html', 'access.css',
  'access.js', 'claude.html'];

fs.rmSync(output, { recursive: true, force: true });
fs.mkdirSync(output, { recursive: true });
for (const name of pages) fs.copyFileSync(path.join(source, name), path.join(output, name));
fs.cpSync(path.join(source, 'assets'), path.join(output, 'assets'), { recursive: true });
