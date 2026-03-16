const backendInput = document.getElementById("backendUrl");
const statusBox = document.getElementById("statusBox");
const SUPPORTED_URL_PATTERNS = [
  /^https:\/\/chatgpt\.com\//i,
  /^https:\/\/chat\.openai\.com\//i,
  /^https:\/\/www\.doubao\.com\//i,
];

chrome.storage.local.get(["backendUrl"], (result) => {
  backendInput.value = result.backendUrl || backendInput.value;
});

backendInput.addEventListener("change", () => {
  chrome.storage.local.set({ backendUrl: backendInput.value });
});

document.getElementById("openDashboardButton").addEventListener("click", () => {
  window.open(`${backendInput.value}/`, "_blank");
});

document.getElementById("captureButton").addEventListener("click", async () => {
  try {
    statusBox.textContent = "Capturing current conversation...";
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id) throw new Error("No active tab found.");
    if (!isSupportedUrl(tab.url)) {
      throw new Error("Open a ChatGPT or Doubao conversation page first.");
    }

    const payload = await captureFromTab(tab.id);
    if (!payload?.conversation?.messages?.length) {
      throw new Error("No supported conversation content found on this page.");
    }

    const response = await fetch(`${backendInput.value}/api/ingest/page-session`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `Import failed: ${response.status}`);

    statusBox.textContent = `Imported conversation.\nBatch: ${result.import_batch_id}\nConversation: ${result.conversation_id}`;
  } catch (error) {
    statusBox.textContent = formatError(error);
  }
});

async function captureFromTab(tabId) {
  try {
    return await chrome.tabs.sendMessage(tabId, { type: "captureConversation" });
  } catch (error) {
    const message = String(error?.message || error || "");
    if (!message.includes("Receiving end does not exist")) {
      throw error;
    }

    await chrome.scripting.executeScript({
      target: { tabId },
      files: ["content.js"],
    });

    return chrome.tabs.sendMessage(tabId, { type: "captureConversation" });
  }
}

function isSupportedUrl(url) {
  return SUPPORTED_URL_PATTERNS.some((pattern) => pattern.test(url || ""));
}

function formatError(error) {
  const message = String(error?.message || error || "Unknown error.");
  if (message.includes("Receiving end does not exist")) {
    return "The page capture script is not attached. Refresh the conversation page once, then try again.";
  }
  return message;
}
