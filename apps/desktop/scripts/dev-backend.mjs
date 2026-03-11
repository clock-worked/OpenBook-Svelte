import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const desktopDir = path.resolve(__dirname, '..');
const repoRoot = path.resolve(desktopDir, '..', '..');
const pyServicesDir = path.join(repoRoot, 'py_services');
const pythonExe = path.join(repoRoot, '.venv', 'Scripts', 'python.exe');

const args = ['-m', 'uvicorn', 'api_server:app', '--reload', '--port', '8010'];

const child = spawn(pythonExe, args, {
  cwd: pyServicesDir,
  stdio: 'inherit',
  env: process.env,
});

child.on('error', (err) => {
  console.error(`Failed to start backend using ${pythonExe}`);
  console.error(err.message);
  process.exit(1);
});

child.on('exit', (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
    return;
  }
  process.exit(code ?? 0);
});
