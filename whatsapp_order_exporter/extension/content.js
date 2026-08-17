(() => {
  const CAPTURE_PROTOCOL_VERSION = "0.6.4";
  const clean = (value) => (value || "").replace(/[\u200e\u200f\u2060]/g, "").trim();
  const pause = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

  function currentChatTitle() {
    const panel = document.querySelector('[data-testid="conversation-panel-wrapper"]') || document.querySelector("#main");
    const header = panel?.querySelector("header");
    const primaryTitle = clean(header?.querySelector('span[dir="auto"]')?.textContent);
    if (primaryTitle) return primaryTitle;
    const candidates = header ? [...header.querySelectorAll("[title]")] : [];
    const titled = candidates.map((node) => clean(node.getAttribute("title"))).filter(Boolean);
    return titled[0] || clean(header?.querySelector("span")?.textContent);
  }

  async function waitForChatTitle(expectedChat, timeoutMs = 10000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const title = currentChatTitle();
      if (title === expectedChat) return title;
      await pause(250);
    }
    return currentChatTitle();
  }

  function captureMessages() {
    const panel = document.querySelector('[data-testid="conversation-panel-messages"]') || document.querySelector("#main");
    if (!panel) throw new Error("The conversation panel is not available yet.");
    const rows = [...panel.querySelectorAll('[role="row"]')];
    const seen = new Set();
    return rows.map((row, ordinal) => {
      const idNode = row.matches("[data-id]") ? row : row.querySelector("[data-id]");
      const messageId = clean(idNode?.getAttribute("data-id"));
      const testNode = row.querySelector('[data-testid^="conv-msg-"]');
      const rawText = clean(row.innerText);
      if (!messageId || !rawText || seen.has(messageId)) return null;
      seen.add(messageId);
      const images = [...row.querySelectorAll("img")].map((img) => ({
        alt: clean(img.alt),
        source_kind: img.src?.startsWith("blob:") ? "blob" : img.src?.startsWith("data:") ? "embedded" : "remote",
        width: img.naturalWidth || null,
        height: img.naturalHeight || null
      }));
      return {
        message_id: messageId,
        ordinal,
        raw_text: rawText,
        test_id: clean(testNode?.getAttribute("data-testid")),
        virtualized: row.getAttribute("data-virtualized"),
        pre_plain_text: clean(row.querySelector("[data-pre-plain-text]")?.getAttribute("data-pre-plain-text")),
        image_metadata: images
      };
    }).filter(Boolean);
  }

  function mediaCandidates(row, messageId) {
    const nodes = [...row.querySelectorAll("img, video, audio, source, a[href]")];
    const seen = new Set();
    return nodes.map((node, mediaIndex) => {
      const source = node.currentSrc || node.src || node.href || "";
      if (!source || seen.has(source) || !source.startsWith("blob:")) return null;
      seen.add(source);
      return {
        message_id: messageId,
        media_index: mediaIndex,
        source,
        source_kind: source.startsWith("blob:") ? "blob" : "remote",
        tag: node.tagName.toLowerCase(),
        width: node.naturalWidth || node.videoWidth || null,
        height: node.naturalHeight || node.videoHeight || null,
        alt: clean(node.alt),
      };
    }).filter(Boolean);
  }

  async function uploadCandidate(candidate) {
    try {
      const sourceResponse = await fetch(candidate.source);
      if (!sourceResponse.ok) throw new Error(`source HTTP ${sourceResponse.status}`);
      const blob = await sourceResponse.blob();
      if (!blob.size) throw new Error("empty media body");
      const upload = await fetch("http://127.0.0.1:8765/media", {
        method: "POST",
        headers: {
          "Content-Type": blob.type || "application/octet-stream",
          "X-Message-Id": candidate.message_id,
          "X-Media-Index": String(candidate.media_index),
          "X-Source-Kind": candidate.source_kind,
          "X-Media-Width": String(candidate.width || 0),
          "X-Media-Height": String(candidate.height || 0),
        },
        body: blob,
      });
      const result = await upload.json();
      if (!upload.ok || !result.ok) throw new Error(result.error || `receiver HTTP ${upload.status}`);
      return { ...candidate, source: undefined, status: "downloaded", byte_count: result.byte_count,
        sha256: result.sha256, asset_id: result.asset_id };
    } catch (error) {
      return { ...candidate, source: undefined, status: "unavailable", error: error.message };
    }
  }

  async function captureLoadedMedia(maxSteps) {
    const scrollContainer = findScrollContainer();
    if (!scrollContainer) throw new Error("Could not identify the message-history scroller.");
    // Always begin at the newest loaded message so a prior history scan that
    // stopped at the top cannot accidentally produce a one-screen media pass.
    scrollContainer.scrollTop = scrollContainer.scrollHeight;
    scrollContainer.dispatchEvent(new Event("scroll", { bubbles: true }));
    await pause(1000);
    const results = new Map();
    const messageIds = new Set();
    let stableSteps = 0;
    let previousOldest = "";
    let steps = 0;
    let expandedReadMore = 0;
    for (; steps < Math.min(Math.max(maxSteps || 40, 1), 120); steps += 1) {
      expandedReadMore += await expandVisibleReadMore();
      const panel = document.querySelector('[data-testid="conversation-panel-messages"]') || document.querySelector("#main");
      const rows = [...panel.querySelectorAll('[role="row"]')];
      for (const row of rows) {
        const idNode = row.matches("[data-id]") ? row : row.querySelector("[data-id]");
        const messageId = clean(idNode?.getAttribute("data-id"));
        if (!messageId) continue;
        messageIds.add(messageId);
        for (const candidate of mediaCandidates(row, messageId)) {
          const key = `${candidate.message_id}:${candidate.media_index}:${candidate.source}`;
          if (!results.has(key)) results.set(key, await uploadCandidate(candidate));
        }
      }
      const visible = captureMessages();
      const oldest = visible[0]?.message_id || "";
      stableSteps = oldest && oldest === previousOldest ? stableSteps + 1 : 0;
      previousOldest = oldest;
      const before = scrollContainer.scrollTop;
      scrollContainer.scrollTop = Math.max(0, before - Math.max(300, Math.floor(scrollContainer.clientHeight * 0.8)));
      scrollContainer.dispatchEvent(new Event("scroll", { bubbles: true }));
      await pause(800);
      if (scrollContainer.scrollTop === 0 && stableSteps >= 3) break;
    }
    return { results: [...results.values()], scannedMessages: messageIds.size, steps: steps + 1,
      reachedLoadedTop: scrollContainer.scrollTop === 0, expandedReadMore };
  }

  function findScrollContainer() {
    const panel = document.querySelector('[data-testid="conversation-panel-messages"]') || document.querySelector("#main");
    if (!panel) return null;
    const candidates = [panel, ...panel.querySelectorAll("div")];
    let ancestor = panel.parentElement;
    while (ancestor && ancestor !== document.body) {
      candidates.push(ancestor);
      if (ancestor.id === "main") break;
      ancestor = ancestor.parentElement;
    }
    return candidates
      .filter((node) => node.clientHeight > 200 && node.scrollHeight > node.clientHeight + 50)
      .sort((a, b) => (b.scrollHeight - b.clientHeight) - (a.scrollHeight - a.clientHeight))[0] || null;
  }

  async function expandVisibleReadMore() {
    const panel = document.querySelector('[data-testid="conversation-panel-messages"]') || document.querySelector("#main");
    if (!panel) return 0;
    const candidates = [...panel.querySelectorAll("button, [role='button'], span")]
      .filter((node) => clean(node.textContent).toLowerCase() === "read more");
    let expanded = 0;
    for (const node of candidates) {
      const clickable = node.closest("button, [role='button']") || node;
      try {
        clickable.click();
        expanded += 1;
        await pause(150);
      } catch (_error) {
        // Remaining markers are reported in the capture manifest.
      }
    }
    return expanded;
  }

  async function captureLoadedHistory(maxSteps) {
    const scrollContainer = findScrollContainer();
    if (!scrollContainer) throw new Error("Could not identify the message-history scroller.");
    const allMessages = new Map();
    let stableSteps = 0;
    let previousOldest = "";
    let steps = 0;
    let expandedReadMore = 0;
    for (; steps < Math.min(Math.max(maxSteps || 40, 1), 120); steps += 1) {
      expandedReadMore += await expandVisibleReadMore();
      const visible = captureMessages();
      for (const message of visible) allMessages.set(message.message_id, message);
      const oldest = visible[0]?.message_id || "";
      stableSteps = oldest && oldest === previousOldest ? stableSteps + 1 : 0;
      previousOldest = oldest;
      const before = scrollContainer.scrollTop;
      scrollContainer.scrollTop = Math.max(0, before - Math.max(300, Math.floor(scrollContainer.clientHeight * 0.8)));
      scrollContainer.dispatchEvent(new Event("scroll", { bubbles: true }));
      await pause(700);
      if (scrollContainer.scrollTop === 0 && stableSteps >= 3) break;
    }
    const messages = [...allMessages.values()];
    messages.forEach((message, ordinal) => { message.ordinal = ordinal; });
    return { messages, steps: steps + 1, reachedLoadedTop: scrollContainer.scrollTop === 0,
      expandedReadMore,
      remainingReadMore: messages.filter((message) => /Read more/i.test(message.raw_text)).length };
  }

  function messageTimestamp(message) {
    const match = (message.pre_plain_text || "").match(/^\[(.+?),\s*(\d{1,2})\/(\d{1,2})\/(\d{4})\]/);
    if (!match) return null;
    const time = match[1].trim().match(/^(\d{1,2}):(\d{2})(?::\d{2})?\s*([AP]M)$/i);
    if (!time) return null;
    let hour = Number(time[1]) % 12;
    if (time[3].toUpperCase() === "PM") hour += 12;
    return new Date(Number(match[4]), Number(match[2]) - 1, Number(match[3]), hour, Number(time[2])).getTime();
  }

  async function captureIncremental(cutoffIso, maxSteps) {
    const scrollContainer = findScrollContainer();
    if (!scrollContainer) throw new Error("Could not identify the message-history scroller.");
    scrollContainer.scrollTop = scrollContainer.scrollHeight;
    scrollContainer.dispatchEvent(new Event("scroll", { bubbles: true }));
    await pause(800);
    const cutoff = cutoffIso ? Date.parse(cutoffIso) : null;
    const allMessages = new Map();
    const mediaResults = new Map();
    let steps = 0;
    let expandedReadMore = 0;
    let reachedCutoff = false;
    for (; steps < Math.min(Math.max(maxSteps || 40, 1), 120); steps += 1) {
      expandedReadMore += await expandVisibleReadMore();
      const visible = captureMessages();
      const panel = document.querySelector('[data-testid="conversation-panel-messages"]') || document.querySelector("#main");
      const rows = [...panel.querySelectorAll('[role="row"]')];
      for (const message of visible) allMessages.set(message.message_id, message);
      for (const row of rows) {
        const idNode = row.matches("[data-id]") ? row : row.querySelector("[data-id]");
        const messageId = clean(idNode?.getAttribute("data-id"));
        const message = visible.find((item) => item.message_id === messageId);
        const stamp = message ? messageTimestamp(message) : null;
        if (!messageId || (cutoff !== null && stamp !== null && stamp < cutoff)) continue;
        for (const candidate of mediaCandidates(row, messageId)) {
          const key = `${candidate.message_id}:${candidate.media_index}:${candidate.source}`;
          if (!mediaResults.has(key)) mediaResults.set(key, await uploadCandidate(candidate));
        }
      }
      const stamps = visible.map(messageTimestamp).filter((value) => value !== null);
      reachedCutoff = cutoff !== null && stamps.length > 0 && Math.min(...stamps) <= cutoff;
      if (reachedCutoff) break;
      const before = scrollContainer.scrollTop;
      scrollContainer.scrollTop = Math.max(0, before - Math.max(300, Math.floor(scrollContainer.clientHeight * 0.8)));
      scrollContainer.dispatchEvent(new Event("scroll", { bubbles: true }));
      await pause(700);
      if (scrollContainer.scrollTop === 0) break;
    }
    const messages = [...allMessages.values()];
    messages.forEach((message, ordinal) => { message.ordinal = ordinal; });
    return { messages, mediaResults: [...mediaResults.values()], steps: steps + 1,
      reachedCutoff, reachedLoadedTop: scrollContainer.scrollTop === 0,
      expandedReadMore,
      remainingReadMore: messages.filter((message) => /Read more/i.test(message.raw_text)).length };
  }

  function payloadFor(title, messages, scan = null) {
    return {
      schema_version: 2,
      captured_at: new Date().toISOString(),
      chat_title: title,
      page_url: location.href,
      viewport: { width: innerWidth, height: innerHeight, device_pixel_ratio: devicePixelRatio },
      scan,
      messages
    };
  }

  chrome.runtime.onMessage.addListener((request, _sender, sendResponse) => {
    if (!["CAPTURE_VERSION", "OPEN_TARGET_CHAT", "CAPTURE_INCREMENTAL", "CAPTURE_VISIBLE_MESSAGES", "CAPTURE_LOADED_HISTORY", "CAPTURE_LOADED_MEDIA"].includes(request?.type)) return false;
    (async () => {
      try {
        if (request.type === "CAPTURE_VERSION") {
          sendResponse({ ok: true, version: CAPTURE_PROTOCOL_VERSION });
          return;
        }
        let title = currentChatTitle();
        if (request.type === "OPEN_TARGET_CHAT") {
          if (title !== request.expectedChat) {
            const target = [...document.querySelectorAll("span[title]")]
              .find((node) => node.getAttribute("title") === request.expectedChat);
            const row = target?.closest('[role="row"]');
            if (!row) throw new Error(`Chat “${request.expectedChat}” is not visible in the chat list.`);
            row.click();
            title = await waitForChatTitle(request.expectedChat);
          }
          if (title !== request.expectedChat) throw new Error(`Could not open “${request.expectedChat}”.`);
          sendResponse({ ok: true, chat_title: title });
          return;
        }
        if (title !== request.expectedChat) {
          throw new Error(`Refusing capture: open chat is “${title || "unknown"}”, not “${request.expectedChat}”.`);
        }
        let messages;
        let scan = null;
        if (request.type === "CAPTURE_INCREMENTAL") {
          const incremental = await captureIncremental(request.cutoffAt, request.maxSteps);
          sendResponse({ ok: true,
            media_manifest: { schema_version: 1, captured_at: new Date().toISOString(), chat_title: title,
              scanned_messages: incremental.messages.length, scan: { steps: incremental.steps,
                reached_cutoff: incremental.reachedCutoff, reached_loaded_top: incremental.reachedLoadedTop,
                expanded_read_more: incremental.expandedReadMore }, results: incremental.mediaResults },
            payload: payloadFor(title, incremental.messages, { steps: incremental.steps,
              cutoff_at: request.cutoffAt || null, reached_cutoff: incremental.reachedCutoff,
              reached_loaded_top: incremental.reachedLoadedTop,
              expanded_read_more: incremental.expandedReadMore,
              remaining_read_more: incremental.remainingReadMore })
          });
          return;
        } else if (request.type === "CAPTURE_LOADED_MEDIA") {
          const media = await captureLoadedMedia(request.maxSteps);
          sendResponse({ ok: true, media_manifest: {
            schema_version: 1, captured_at: new Date().toISOString(), chat_title: title,
            scanned_messages: media.scannedMessages, scan: { steps: media.steps,
              reached_loaded_top: media.reachedLoadedTop,
              expanded_read_more: media.expandedReadMore }, results: media.results,
          }});
          return;
        } else if (request.type === "CAPTURE_LOADED_HISTORY") {
          const history = await captureLoadedHistory(request.maxSteps);
          messages = history.messages;
          scan = { steps: history.steps, reached_loaded_top: history.reachedLoadedTop,
            expanded_read_more: history.expandedReadMore,
            remaining_read_more: history.remainingReadMore };
        } else {
          messages = captureMessages();
        }
        if (!messages.length) throw new Error("No loaded message rows were found. Open the chat and wait for messages to appear.");
        sendResponse({ ok: true, payload: payloadFor(title, messages, scan) });
      } catch (error) {
        sendResponse({ ok: false, error: error.message });
      }
    })();
    return true;
  });
})();
