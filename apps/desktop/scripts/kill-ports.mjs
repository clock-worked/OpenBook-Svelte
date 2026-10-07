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
import net from 'node:net';

const SLEEP = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Ground truth for "is this port free": actually try to bind it.
 * netstat can keep stale LISTENING entries for already-dead PIDs (ghost
 * sockets), which would send the kill loop after a PID that no longer
 * exists and make this script abort a perfectly good dev start. A
 * successful bind proves the port is usable, no entry or not.
 *
 * The test binds 127.0.0.1, the address this dev stack actually uses
 * (vite on localhost:5173, uvicorn on 127.0.0.1:8012). Binding 0.0.0.0
 * here would be a FALSE check on Windows: unlike Linux, Windows lets a
 * wildcard (0.0.0.0) bind and a loopback (127.0.0.1) bind of the same
 * port COEXIST, so a 0.0.0.0 probe passes while a live 127.0.0.1
 * listener still blocks the real server (observed: orphaned vite on
 * 127.0.0.1:5173, probe said "free", new vite died with EADDRINUSE).
 */
function portIsBindable(port) {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (ok) => {
      if (!settled) {
        settled = true;
        resolve(ok);
      }
    };
    const srv = net.createServer();
    srv.once('error', () => finish(false)); // EADDRINUSE: genuinely held
    srv.listen({ port, host: '127.0.0.1' }, () => {
      srv.close();
      finish(true);
    });
    setTimeout(() => finish(true), 1500); // hang safety valve
  });
}

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
    if (await portIsBindable(port)) {
      console.log(`Port ${port} free (netstat entry is stale — bind test passed)`);
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
    if (await portIsBindable(port)) {
      console.log(`Port ${port} usable (netstat entry is stale — bind test passed)`);
    } else {
      console.warn(
        `WARNING: port ${port} still held by PID(s) ${[...remaining].join(',')} ` +
        'after tree kill. Run this from an ELEVATED terminal so the full ' +
        'process tree is visible, then retry.'
      );
      process.exitCode = 1;
    }
  }
}
