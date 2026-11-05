const pad = (value) => String(value).padStart(2, "0");

// "22.05.22 в 22:30", as in the design mockup.
export function formatDate(iso) {
  const date = new Date(iso);
  const day = `${pad(date.getDate())}.${pad(date.getMonth() + 1)}.${pad(date.getFullYear() % 100)}`;
  return `${day} в ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function formatSize(bytes) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} КБ`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} МБ`;
}

export function plainText(html) {
  return new DOMParser().parseFromString(html, "text/html").body.textContent || "";
}

export function excerpt(html, length = 90) {
  const text = plainText(html).replace(/\s+/g, " ").trim();
  return text.length > length ? `${text.slice(0, length).trimEnd()}…` : text;
}

const AVATAR_COLORS = ["#6c8cff", "#b07cf0", "#f08c6c", "#3fb27f", "#f0b84c", "#4cb8f0", "#e46b9b"];

export function avatarColor(name) {
  let hash = 0;
  for (const char of name) hash = (hash * 31 + char.charCodeAt(0)) | 0;
  return AVATAR_COLORS[Math.abs(hash) % AVATAR_COLORS.length];
}
