#!/usr/bin/env node
/**
 * Kill whatever is listening on the given ports — including the WHOLE
 * process tree (watchdogs, reloaders, spawners), not just the socket owner.
 *
 * The npm `kill-port` package only kills the PID that owns the socket. A
 * supervisor higher up the tree (e.g. a concurrently/watchdog node from an
 * older, elevated dev session) immediately respawns a replacement, which
 * re-takes the port with stale code. This script walks the parent chain to
 * the highest killable ancestor and taskkills that tree.
 *
 * Usage: node scripts/kill-ports.mjs 5173 8012
 *
 * Windows-focused (this is a Windows dev stack): netstat -ano for
 * port→PID, Win32_Process for the parent chain, taskkill /F /T for the kill.
 * The walk stops below terminal hosts (conhost/cmd/powershell/VS Code) so we
 * never close your terminal window.
 */
import { execFileSync } from 'node:child_process';

const SLEEP = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function portPids(port) {
  const out = execFileSync('netstat', ['-ano'], { encoding: 'utf8' });
  const pids = new Set();
  const re = new RegExp(`^TCP\\s+\\S+:${port}\\s+\\S+\\s+LISTENING\\s+(\\d+)`, 'im');
  for (const line of out.split('\n')) {
    const m = line.trim().match(re);
    if (m) pids.add(Number(m[1]));
  }
  return pids;
}

function ps(query) {
  try {
    return execFileSync(
      'powershell',
      ['-NoProfile', '-Command', query],
      { encoding: 'utf8' }
    ).trim();
  } catch {
    return '';
  }
}

function parentPid(pid) {
  const out = ps(
    `(Get-CimInstance Win32_Process -Filter "ProcessId=${pid}" -ErrorAction SilentlyContinue).ParentProcessId`
  );
  const p = Number(out.split(/\s+/).find(Boolean) || NaN);
  return Number.isFinite(p) && p > 0 ? p : null;
}

function processName(pid) {
  const out = ps(`(Get-Process -Id ${pid} -ErrorAction SilentlyContinue).ProcessName`);
  return (out.split(/\s+/)[0] || '').toLowerCase();
}

// Never kill these: they are terminal/IDE hosts, not dev-stack supervisors.
const TERMINAL_HOSTS = new Set([
  'conhost', 'cmd', 'powershell', 'pwsh', 'windowsterminal',
  'openconsole', 'code', 'code-explorer', 'vscodetask', 'devenv',
]);

function highestKillableAncestor(pid) {
  let root = pid;
  for (let guard = 0; guard < 20; guard++) {
    const parent = parentPid(root);
    if (!parent || parent <= 4) break;
    const name = processName(parent);
    if (name && TERMINAL_HOSTS.has(name)) break;
    root = parent;
  }
  return root;
}

const ports = process.argv.slice(2).map((a) => Number.parseInt(a, 10)).filter(Number.isInteger);
if (!ports.length) {
  console.log('Usage: node scripts/kill-ports.mjs <port> [port ...]');
  process.exit(1);
}

for (const port of ports) {
  let done = false;
  for (let attempt = 0; attempt < 3 && !done; attempt++) {
    const owners = portPids(port);
    if (!owners.size) {
      console.log(`Port ${port} free`);
      done = true;
      break;
    }
    for (const pid of owners) {
      const root = highestKillableAncestor(pid);
      console.log(`Port ${port}: killing tree rooted at PID ${root} (socket owner ${pid})`);
      try {
        execFileSync('taskkill', ['/F', '/T', '/PID', String(root)], { stdio: 'ignore' });
      } catch {
        // Ancestor not killable from this context — fall back to the owner.
        try {
          execFileSync('taskkill', ['/F', '/PID', String(pid)], { stdio: 'ignore' });
        } catch {
          console.warn(`  (could not kill PID ${pid} — not visible from this session?)`);
        }
      }
    }
    await SLEEP(1500);
  }
  const remaining = portPids(port);
  if (remaining.size) {
    console.warn(
      `WARNING: port ${port} still held by PID(s) ${[...remaining].join(',')} ` +
      'after tree kill. Run this from an ELEVATED terminal so the full ' +
      'process tree is visible, then retry.'
    );
    process.exitCode = 1;
  }
}
