const RECEIVER = "http://127.0.0.1:8765";
let working = false;

async function receiverPost(path, body) {
  const response = await fetch(`${RECEIVER}${path}`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  });
  const result = await response.json();
  if (!response.ok || !result.ok) throw new Error(result.error || `Receiver HTTP ${response.status}`);
  return result;
}

async function capture(tabId, type, expectedChat) {
  const response = await chrome.tabs.sendMessage(tabId, {
    type, expectedChat, maxSteps: 80
  });
  if (!response?.ok) throw new Error(response?.error || `${type} failed.`);
  return response;
}

async function runNextJob() {
  if (working) return;
  working = true;
  let job = null;
  try {
    const nextResponse = await fetch(`${RECEIVER}/automation/next`, { cache: "no-store" });
    if (!nextResponse.ok) return;
    job = (await nextResponse.json()).job;
    if (!job) return;
    const tabs = await chrome.tabs.query({ url: "https://web.whatsapp.com/*" });
    const targetTab = tabs.find((tab) => tab.active) || tabs[0];
    if (!targetTab?.id) throw new Error("WhatsApp Web is not open.");
    const tabId = targetTab.id;
    await chrome.tabs.update(tabId, { active: true });
    await capture(tabId, "OPEN_TARGET_CHAT", job.expected_chat);
    const media = await capture(tabId, "CAPTURE_LOADED_MEDIA", job.expected_chat);
    const mediaResult = await receiverPost("/media-manifest", media.media_manifest);
    const history = await capture(tabId, "CAPTURE_LOADED_HISTORY", job.expected_chat);
    const captureResult = await receiverPost("/capture", history.payload);
    await receiverPost("/automation/result", {
      job_id: job.id, ok: true,
      result: { media: mediaResult, capture: captureResult }
    });
  } catch (error) {
    if (job) {
      try {
        await receiverPost("/automation/result", { job_id: job.id, ok: false, error: error.message });
      } catch (_) {}
    }
  } finally {
    working = false;
  }
}

chrome.runtime.onInstalled.addListener(() => {
  chrome.alarms.create("order-capture-jobs", { periodInMinutes: 0.5 });
  runNextJob();
});
chrome.runtime.onStartup.addListener(() => {
  chrome.alarms.create("order-capture-jobs", { periodInMinutes: 0.5 });
  runNextJob();
});
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "order-capture-jobs") runNextJob();
});
runNextJob();
