import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import kill from "tree-kill";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const desktopDir = path.resolve(__dirname, "..");
const repoRoot = path.resolve(desktopDir, "..", "..");
const pyServicesDir = path.join(repoRoot, "py_services");
// OPENBOOK_PYTHON_EXE lets a worktree/secondary checkout borrow the main
// checkout's .venv instead of needing its own (venvs are not git-tracked).
const pythonExe =
  process.env.OPENBOOK_PYTHON_EXE ||
  path.join(repoRoot, ".venv", "Scripts", "python.exe");

// 8012: 8011 is squatted by a stale elevated/other-user backend process on
// this machine that cannot be killed from a normal session (it keeps
// respawning workers with pre-fix code). Revisit after a reboot.
const args = ["-m", "uvicorn", "api_server:app", "--port", "8012", "--reload"];

const backendEnv = {
  ...process.env,
  PYTHONIOENCODING: "utf-8",
  PYTHONUTF8: "1",
};
for (const variable of ["SSL_CERT_FILE", "SSL_CERT_DIR"]) {
  const certificatePath = backendEnv[variable];
  if (certificatePath && !existsSync(certificatePath)) {
    console.warn(`Ignoring ${variable}: certificate path does not exist (${certificatePath}). Using default TLS trust.`);
    delete backendEnv[variable];
  }
}

const child = spawn(pythonExe, args, {
  cwd: pyServicesDir,
  stdio: "inherit",
  env: backendEnv,
});

let shuttingDown = false;

function stopChild(signal = "SIGTERM") {
  return new Promise((resolve) => {
    if (!child.pid) {
      resolve();
      return;
    }

    kill(child.pid, signal, (err) => {
      if (err && err.code !== "ESRCH") {
        console.error(`Failed to stop backend process tree (${signal})`);
        console.error(err.message);
      }
      resolve();
    });
  });
}

function handleShutdown(signal) {
  if (shuttingDown) {
    return;
  }

  shuttingDown = true;
  stopChild(signal).finally(() => {
    process.exit(signal === "SIGINT" ? 130 : 143);
  });
}

process.on("SIGINT", () => {
  handleShutdown("SIGINT");
});

process.on("SIGTERM", () => {
  handleShutdown("SIGTERM");
});

child.on("error", (err) => {
  console.error(`Failed to start backend using ${pythonExe}`);
  console.error(err.message);
  process.exit(1);
});

child.on("exit", (code, signal) => {
  if (shuttingDown) {
    process.exit(code ?? 0);
    return;
  }

  if (signal) {
    process.exit(1);
    return;
  }

  process.exit(code ?? 0);
});
