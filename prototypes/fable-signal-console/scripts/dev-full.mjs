import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const prototypeRoot = path.resolve(scriptDir, "..");
const repositoryRoot = path.resolve(prototypeRoot, "../..");
const python = path.join(repositoryRoot, ".venv", "bin", "python");
const vite = path.join(prototypeRoot, "node_modules", ".bin", "vite");
const apiPort = process.env.FABLE_SIGNAL_CONSOLE_API_PORT || "8643";
const webPort = process.env.FABLE_SIGNAL_CONSOLE_WEB_PORT || "4173";

const sharedEnvironment = {
  ...process.env,
  OMP_NUM_THREADS: "1",
  MKL_NUM_THREADS: "1",
  PYTHONPATH: repositoryRoot,
  FABLE_SIGNAL_CONSOLE_API_BASE: `http://127.0.0.1:${apiPort}`,
};

const backend = spawn(
  python,
  ["-m", "uvicorn", "yoyo.console.server:app", "--host", "127.0.0.1", "--port", apiPort],
  { cwd: repositoryRoot, env: sharedEnvironment, stdio: "inherit" },
);
const frontend = spawn(
  vite,
  ["--host", "0.0.0.0", "--port", webPort, "--strictPort"],
  { cwd: prototypeRoot, env: sharedEnvironment, stdio: "inherit" },
);

let stopping = false;
function stop(code = 0) {
  if (stopping) return;
  stopping = true;
  backend.kill("SIGTERM");
  frontend.kill("SIGTERM");
  setTimeout(() => process.exit(code), 250);
}

for (const child of [backend, frontend]) {
  child.on("exit", (code, signal) => {
    if (!stopping) stop(code ?? (signal ? 1 : 0));
  });
}

process.on("SIGINT", () => stop(0));
process.on("SIGTERM", () => stop(0));
