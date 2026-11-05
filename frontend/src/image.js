// Checks an attached image in the browser and shrinks JPEG/PNG to the attachment size.
// The server repeats every check and the resize, so whenever the browser can't do
// something reliably the original file is sent and the server handles it.

export const MAX_IMAGE_WIDTH = 320;
export const MAX_IMAGE_HEIGHT = 240;
// Same limit as on the server: a small file can still decode into a huge bitmap.
const MAX_SOURCE_PIXELS = 20_000_000;
const JPEG_QUALITY = 0.9;

const SIGNATURES = [
  { type: "image/jpeg", bytes: [0xff, 0xd8, 0xff] },
  { type: "image/png", bytes: [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a] },
  { type: "image/gif", bytes: [0x47, 0x49, 0x46, 0x38, 0x37, 0x61] }, // GIF87a
  { type: "image/gif", bytes: [0x47, 0x49, 0x46, 0x38, 0x39, 0x61] }, // GIF89a
];

export class ImageCheckError extends Error {}

// The real format, from the first bytes of the file rather than its extension.
async function sniffType(file) {
  const head = new Uint8Array(await file.slice(0, 8).arrayBuffer());
  return SIGNATURES.find(({ bytes }) => bytes.every((byte, index) => head[index] === byte))?.type ?? null;
}

function loadImage(file) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const image = new Image();
    image.onload = () => {
      URL.revokeObjectURL(url);
      resolve(image);
    };
    image.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new ImageCheckError("Файл повреждён или не является изображением."));
    };
    image.src = url;
  });
}

function draw(source, width, height) {
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d");
  if (!context) throw new Error("Canvas is unavailable");
  context.imageSmoothingEnabled = true;
  context.imageSmoothingQuality = "high";
  context.drawImage(source, 0, 0, width, height);
  return canvas;
}

let readbackWorks;

// Anti-fingerprinting modes (Firefox resistFingerprinting, Brave, Tor) return noise
// or a blank picture when a canvas is read back; then the browser must not resize.
function canvasReadbackWorks() {
  if (readbackWorks !== undefined) return readbackWorks;
  try {
    const context = document.createElement("canvas").getContext("2d", { willReadFrequently: true });
    const colors = [[255, 0, 0], [0, 255, 0], [0, 0, 255], [255, 255, 255]];
    colors.forEach(([r, g, b], index) => {
      context.fillStyle = `rgb(${r}, ${g}, ${b})`;
      context.fillRect(index, 0, 1, 1);
    });
    const data = context.getImageData(0, 0, colors.length, 1).data;
    readbackWorks = colors.every((color, index) =>
      [...color, 255].every((value, channel) => data[index * 4 + channel] === value),
    );
  } catch {
    readbackWorks = false;
  }
  return readbackWorks;
}

// Proportional fit into 320x240; never enlarges, like Pillow's thumbnail() on the server.
export function fitSize(width, height) {
  const scale = Math.min(1, MAX_IMAGE_WIDTH / width, MAX_IMAGE_HEIGHT / height);
  return {
    width: Math.max(1, Math.round(width * scale)),
    height: Math.max(1, Math.round(height * scale)),
  };
}

function resize(image, type, target) {
  // One big drawImage step skips most source pixels and gives jagged edges, so the
  // picture is halved while that still stays above the target size.
  let source = image;
  let width = image.naturalWidth;
  let height = image.naturalHeight;
  while (width / 2 >= target.width && height / 2 >= target.height) {
    width = Math.round(width / 2);
    height = Math.round(height / 2);
    source = draw(source, width, height);
  }
  const canvas = draw(source, target.width, target.height);
  return new Promise((resolve) => canvas.toBlob(resolve, type, JPEG_QUALITY));
}

// Resolves to { file, width, height, resized, shrinksOnServer }: the file to upload
// and the size it will have once stored. Rejects with ImageCheckError only for files
// that are definitely not acceptable images.
export async function prepareImage(file) {
  const type = await sniffType(file);
  if (!type) throw new ImageCheckError("Файл не является изображением JPG, GIF или PNG.");

  const image = await loadImage(file);
  // Modern browsers already apply the EXIF orientation here, as the server does.
  const { naturalWidth, naturalHeight } = image;
  if (naturalWidth * naturalHeight > MAX_SOURCE_PIXELS) {
    throw new ImageCheckError("Разрешение изображения слишком большое.");
  }

  const target = fitSize(naturalWidth, naturalHeight);
  const fits = target.width === naturalWidth && target.height === naturalHeight;
  const original = { file, ...target, resized: false, shrinksOnServer: !fits };
  // A canvas keeps only the first frame of a GIF, so animations are left to the server.
  if (fits || type === "image/gif" || !canvasReadbackWorks()) return original;

  try {
    const blob = await resize(image, type, target);
    if (!blob || blob.type !== type || blob.size >= file.size) return original;
    return { file: new File([blob], file.name, { type }), ...target, resized: true, shrinksOnServer: false };
  } catch {
    // Out of memory on a weak device, canvas size limits and the like.
    return original;
  }
}
