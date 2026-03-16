const platform = window.location.hostname.includes("doubao") ? "doubao" : "chatgpt";

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type === "captureConversation") {
    captureConversation().then(sendResponse).catch((error) => {
      sendResponse({ error: error.message });
    });
    return true;
  }
  return false;
});

async function captureConversation() {
  const messages = await extractMessages();
  return {
    platform,
    conversation: {
      external_id: getConversationId(),
      title: getConversationTitle(),
      source_url: window.location.href,
      messages,
    },
  };
}

function getConversationTitle() {
  return document.title.replace(/\s*-\s*(ChatGPT|豆包).*$/i, "").trim() || document.title;
}

function getConversationId() {
  const parts = window.location.pathname.split("/").filter(Boolean);
  return parts[parts.length - 1] || `page-${Date.now()}`;
}

async function extractMessages() {
  const selectors = platform === "doubao"
    ? ["main article", "main [class*='message']", "main section"]
    : ["article[data-testid^='conversation-turn-']", "main article", "[data-message-author-role]"];
  const nodes = [];
  for (const selector of selectors) {
    for (const node of document.querySelectorAll(selector)) {
      if (!nodes.includes(node)) nodes.push(node);
    }
  }
  const seen = new Set();
  const messages = [];
  let index = 1;
  for (const node of nodes) {
    const text = node.innerText?.trim();
    if (!text || text.length < 6) continue;
    const signature = text.slice(0, 200);
    if (seen.has(signature)) continue;
    seen.add(signature);
    const role = detectRole(node, index);
    const links = Array.from(node.querySelectorAll("a[href]")).slice(0, 8).map((link) => ({
      href: link.href,
      text: link.textContent?.trim() || link.href,
    }));
    const codeBlocks = Array.from(node.querySelectorAll("pre code")).slice(0, 5).map((codeNode) => ({
      language: codeNode.className?.replace("language-", "") || "text",
      code: codeNode.textContent || "",
    }));
    const assets = await extractAssets(node);
    messages.push({
      external_id: `${getConversationId()}-${index}`,
      seq_no: index,
      role,
      text_content: text,
      created_at: new Date().toISOString(),
      links,
      code_blocks: codeBlocks,
      assets,
    });
    index += 1;
  }
  return messages;
}

function detectRole(node, index) {
  const roleNode = node.querySelector("[data-message-author-role]");
  const explicitRole = roleNode?.getAttribute("data-message-author-role");
  if (explicitRole) return explicitRole;
  const aria = node.getAttribute("aria-label") || "";
  if (/user|你|我|you/i.test(aria)) return "user";
  return index % 2 === 1 ? "user" : "assistant";
}

async function extractAssets(node) {
  const assets = [];
  const images = Array.from(node.querySelectorAll("img[src]")).slice(0, 4);
  for (const image of images) {
    assets.push({
      kind: "image",
      mime_type: guessMimeType(image.src, "image/png"),
      file_name: image.alt || image.src.split("/").pop() || "image.png",
      source_url: image.src,
      content_base64: await tryFetchBase64(image.src),
    });
  }
  const files = Array.from(node.querySelectorAll("a[href]")).slice(0, 8);
  for (const link of files) {
    const href = link.href;
    if (!href) continue;
    if (href.endsWith(".pdf") || link.download) {
      assets.push({
        kind: href.endsWith(".pdf") ? "pdf" : "attachment",
        mime_type: guessMimeType(href, href.endsWith(".pdf") ? "application/pdf" : "application/octet-stream"),
        file_name: link.download || href.split("/").pop() || "attachment",
        source_url: href,
        content_base64: await tryFetchBase64(href),
      });
    }
  }
  return assets;
}

function guessMimeType(url, fallback) {
  if (url.endsWith(".pdf")) return "application/pdf";
  if (url.endsWith(".png")) return "image/png";
  if (url.endsWith(".jpg") || url.endsWith(".jpeg")) return "image/jpeg";
  if (url.startsWith("data:")) return url.slice(5).split(";")[0];
  return fallback;
}

async function tryFetchBase64(url) {
  try {
    if (url.startsWith("data:")) return url.split(",")[1];
    const response = await fetch(url);
    if (!response.ok) return null;
    const blob = await response.blob();
    return await blobToBase64(blob);
  } catch {
    return null;
  }
}

function blobToBase64(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = reader.result;
      if (typeof result !== "string") {
        reject(new Error("Unable to read asset."));
        return;
      }
      resolve(result.split(",")[1]);
    };
    reader.onerror = () => reject(reader.error || new Error("Unable to read asset."));
    reader.readAsDataURL(blob);
  });
}
