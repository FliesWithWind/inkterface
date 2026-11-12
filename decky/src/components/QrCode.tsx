import { API_KEY_QR, API_KEY_URL } from "./qrData";

const QUIET = 4; // modules of white around the code; scanners need it

/** The QR code of the Steam Web API key page, drawn as one SVG path (dark on white, whatever Steam's theme is). */
export function ApiKeyQr({ size = 176 }: { size?: number }) {
  const n = API_KEY_QR.length;
  let d = "";
  API_KEY_QR.forEach((row, y) => {
    for (let x = 0; x < row.length; x++) if (row[x] === "#") d += `M${x + QUIET} ${y + QUIET}h1v1h-1z`;
  });
  const side = n + 2 * QUIET;
  return (
    <svg role="img" aria-label={`QR code for ${API_KEY_URL.replace("https://", "")}`} width={size} height={size} viewBox={`0 0 ${side} ${side}`} shapeRendering="crispEdges">
      <rect width={side} height={side} fill="#fff" />
      <path d={d} fill="#000" />
    </svg>
  );
}
