const API_ROOT = `${import.meta.env.VITE_SIGNAL_MODEL_API || ""}/api/model`;

let statusPromise;
const inferencePromises = new Map();

async function requestJson(path, options = {}) {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 90_000);
  try {
    const response = await fetch(`${API_ROOT}${path}`, {
      ...options,
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      signal: controller.signal,
    });
    const payload = await response.json();
    if (!response.ok || !payload.ok) {
      const error = new Error(payload.detail || `模型接口返回 ${response.status}`);
      error.status = response.status;
      throw error;
    }
    return payload;
  } finally {
    window.clearTimeout(timeout);
  }
}

/** Load the server-controlled adapter identity without loading ML runtimes. */
export function getModelStatus() {
  statusPromise ||= requestJson("/status").catch((error) => {
    statusPromise = undefined;
    throw error;
  });
  return statusPromise;
}

/** Run one immutable pre-holdout replay; duplicate React effects share a promise. */
export function runModelInference(symbol, { force = false } = {}) {
  const key = `${symbol}:${force ? "force" : "cached"}`;
  if (!force && inferencePromises.has(key)) return inferencePromises.get(key);
  const requestWithBusyRetry = async () => {
    let lastError;
    for (let attempt = 0; attempt < 6; attempt += 1) {
      try {
        return await requestJson("/infer", {
          method: "POST",
          body: JSON.stringify({ symbol, force }),
        });
      } catch (error) {
        lastError = error;
        if (error.status !== 409) throw error;
        await new Promise((resolve) => window.setTimeout(resolve, 1_000 + attempt * 500));
      }
    }
    throw lastError;
  };
  const promise = requestWithBusyRetry().catch((error) => {
    inferencePromises.delete(key);
    throw error;
  }).finally(() => {
    if (force) inferencePromises.delete(key);
  });
  inferencePromises.set(key, promise);
  return promise;
}
