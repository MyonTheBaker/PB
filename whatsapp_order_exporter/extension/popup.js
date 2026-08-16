const RECEIVER = "http://127.0.0.1:8765";
const EXPECTED_CHAT = "PB Advance Orders";
const button = document.getElementById("capture");
const historyButton = document.getElementById("history");
const mediaButton = document.getElementById("media");
const statusNode = document.getElementById("status");

function setStatus(message, kind = "") {
  statusNode.textContent = message;
  statusNode.className = kind;
}

async function receiverHealth() {
  try {
    const response = await fetch(`${RECEIVER}/health`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    setStatus("Local receiver is ready.", "ok");
  } catch (error) {
    setStatus("Local receiver is not running. Start it, then try again.", "error");
  }
}

async function runCapture(messageType) {
  button.disabled = true;
  historyButton.disabled = true;
  mediaButton.disabled = true;
  setStatus(messageType === "CAPTURE_LOADED_HISTORY"
    ? "Scrolling through loaded history… Keep this popup open."
    : "Reading the open WhatsApp chat…");
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id || !tab.url?.startsWith("https://web.whatsapp.com/")) {
      throw new Error("Open WhatsApp Web and select the order group first.");
    }
    const capture = await chrome.tabs.sendMessage(tab.id, {
      type: messageType,
      expectedChat: EXPECTED_CHAT,
      maxSteps: 80
    });
    if (!capture?.ok) throw new Error(capture?.error || "Capture failed.");
    const isMedia = messageType === "CAPTURE_LOADED_MEDIA";
    const body = isMedia ? capture.media_manifest : capture.payload;
    const response = await fetch(`${RECEIVER}/${isMedia ? "media-manifest" : "capture"}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
    const result = await response.json();
    if (!response.ok || !result.ok) throw new Error(result.error || `Receiver HTTP ${response.status}`);
    if (isMedia) {
      setStatus(`Saved ${result.downloaded} media files; ${result.failed} unavailable.`, result.failed ? "warning" : "ok");
    } else {
      const suffix = capture.payload.scan ? ` across ${capture.payload.scan.steps} scroll positions` : "";
      const expanded = capture.payload.scan?.expanded_read_more || 0;
      const remaining = capture.payload.scan?.remaining_read_more || 0;
      const expansion = capture.payload.scan ? ` Expanded ${expanded} long messages; ${remaining} markers remain.` : "";
      setStatus(`Saved ${result.message_count} messages (${result.new_messages} new)${suffix}.${expansion}`, remaining ? "warning" : "ok");
    }
  } catch (error) {
    setStatus(error.message, "error");
  } finally {
    button.disabled = false;
    historyButton.disabled = false;
    mediaButton.disabled = false;
  }
}

button.addEventListener("click", () => runCapture("CAPTURE_VISIBLE_MESSAGES"));
historyButton.addEventListener("click", () => runCapture("CAPTURE_LOADED_HISTORY"));
mediaButton.addEventListener("click", () => runCapture("CAPTURE_LOADED_MEDIA"));
receiverHealth();
