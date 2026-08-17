const RECEIVER = "http://127.0.0.1:8765";
const button = document.getElementById("capture");
const statusNode = document.getElementById("status");

function setStatus(message, kind = "") {
  statusNode.textContent = message;
  statusNode.className = kind;
}

async function receiverJson(path, body = null) {
  const response = await fetch(`${RECEIVER}${path}`, {
    method: body ? "POST" : "GET",
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
    cache: "no-store"
  });
  const result = await response.json();
  if (!response.ok || !result.ok) throw new Error(result.error || `Receiver HTTP ${response.status}`);
  return result;
}

async function receiverHealth() {
  try {
    await receiverJson("/health");
    setStatus("Local receiver is ready.", "ok");
  } catch (_error) {
    setStatus("Local receiver is not running. Start it from the Control Tower, then try again.", "error");
  }
}

async function waitForJob(jobId) {
  for (let attempt = 0; attempt < 240; attempt += 1) {
    const job = (await receiverJson(`/automation/status?id=${encodeURIComponent(jobId)}`)).job;
    if (job.status === "completed") return job;
    if (job.status === "failed") throw new Error(job.error || "Capture failed.");
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  throw new Error("Capture is still pending. It may continue in the background.");
}

async function runCapture() {
  button.disabled = true;
  setStatus("Queueing incremental capture…");
  try {
    const started = await receiverJson("/automation/start", {});
    setStatus("Capturing updates in the background…");
    const job = await waitForJob(started.job_id);
    const capture = job.result?.capture || {};
    const media = job.result?.media || {};
    const imported = job.result?.import || {};
    setStatus(
      `Saved and imported ${imported.messages ?? capture.message_count ?? 0} messages and ${imported.media ?? media.downloaded ?? 0} media files.`,
      media.failed ? "warning" : "ok"
    );
  } catch (error) {
    setStatus(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

button.addEventListener("click", runCapture);
receiverHealth();
