// 模擬攝影機畫面（NEXT_PUBLIC_MOCK=1 時取代 MJPEG）。純 SVG 動畫，1280×720 座標系。

function Grid() {
  return (
    <g opacity={0.12}>
      {Array.from({ length: 16 }, (_, i) => (
        <line key={`v${i}`} x1={i * 80} y1={0} x2={i * 80} y2={720} stroke="#94a3b8" strokeWidth={1} />
      ))}
      {Array.from({ length: 9 }, (_, i) => (
        <line key={`h${i}`} x1={0} y1={i * 80} x2={1280} y2={i * 80} stroke="#94a3b8" strokeWidth={1} />
      ))}
    </g>
  );
}

function Feeder({ jam }: { jam: boolean }) {
  return (
    <g>
      <rect x={0} y={560} width={1280} height={160} fill="#1a222c" />
      {/* 送料機本體 */}
      <rect x={380} y={300} width={180} height={300} rx={8} fill="#2b3644" stroke="#3d4b5c" strokeWidth={3} />
      <rect x={880} y={220} width={300} height={380} rx={6} fill="#26303c" stroke="#3d4b5c" strokeWidth={3} />
      <text x={900} y={260} fill="#64748b" fontSize={26} fontFamily="monospace">PRESS 250t</text>
      {/* 滾輪 */}
      {[430, 510].map((x) => (
        <g key={x}>
          <circle cx={x} cy={420} r={34} fill="#475569" stroke="#94a3b8" strokeWidth={3} />
          <circle cx={x} cy={500} r={34} fill="#475569" stroke="#94a3b8" strokeWidth={3} />
          {!jam && (
            <line x1={x} y1={420} x2={x + 30} y2={420} stroke="#cbd5e1" strokeWidth={4}>
              <animateTransform attributeName="transform" type="rotate" from={`0 ${x} 420`} to={`360 ${x} 420`} dur="1.2s" repeatCount="indefinite" />
            </line>
          )}
        </g>
      ))}
      {/* 板材 */}
      {jam ? (
        <path d="M 120 460 L 560 460 Q 620 400 660 470 Q 700 540 760 455 L 880 460" fill="none" stroke="#fbbf24" strokeWidth={14} strokeLinejoin="round" />
      ) : (
        <g>
          <rect x={120} y={453} width={760} height={14} fill="#cbd5e1" />
          {Array.from({ length: 10 }, (_, i) => (
            <rect key={i} x={120 + i * 80} y={453} width={6} height={14} fill="#64748b">
              <animate attributeName="x" from={120 + i * 80} to={200 + i * 80} dur="0.9s" repeatCount="indefinite" />
            </rect>
          ))}
        </g>
      )}
    </g>
  );
}

function Press({ idle }: { idle: boolean }) {
  return (
    <g>
      <rect x={0} y={600} width={1280} height={120} fill="#1a222c" />
      <rect x={340} y={80} width={60} height={540} fill="#2b3644" />
      <rect x={880} y={80} width={60} height={540} fill="#2b3644" />
      <rect x={340} y={60} width={600} height={70} rx={6} fill="#334155" />
      <g>
        <rect x={420} y={180} width={440} height={110} rx={6} fill="#475569" stroke="#94a3b8" strokeWidth={3} />
        {!idle && <animateTransform attributeName="transform" type="translate" values="0 0; 0 230; 0 0" dur="1.5s" repeatCount="indefinite" />}
      </g>
      <rect x={400} y={520} width={480} height={80} rx={4} fill="#3b4757" stroke="#64748b" strokeWidth={3} />
      <rect x={380} y={505} width={520} height={14} fill="#cbd5e1" />
    </g>
  );
}

function Assembly({ noHelmet }: { noHelmet: boolean }) {
  return (
    <g>
      <rect x={0} y={560} width={1280} height={160} fill="#1a222c" />
      <rect x={160} y={470} width={1000} height={40} fill="#3b4757" />
      <rect x={200} y={510} width={20} height={120} fill="#334155" />
      <rect x={1100} y={510} width={20} height={120} fill="#334155" />
      {[300, 460, 960].map((x) => (
        <rect key={x} x={x} y={420} width={90} height={50} rx={4} fill="#64748b" />
      ))}
      {/* 人員（人臉已模糊） */}
      <g>
        <animateTransform attributeName="transform" type="translate" values="0 0; 14 0; 0 0" dur="3s" repeatCount="indefinite" />
        <rect x={650} y={300} width={140} height={260} rx={40} fill="#1e3a5f" />
        <circle cx={720} cy={230} r={56} fill="#7c8a9c" />
        <circle cx={720} cy={235} r={44} fill="#94a3b8" opacity={0.55} filter="url(#blur)" />
        {!noHelmet && <path d="M 660 222 Q 720 140 780 222 Z" fill="#facc15" stroke="#ca8a04" strokeWidth={4} />}
        <rect x={600} y={380} width={60} height={22} rx={10} fill="#1e3a5f" />
      </g>
    </g>
  );
}

function Welding({ smoke }: { smoke: boolean }) {
  return (
    <g>
      <rect x={0} y={580} width={1280} height={140} fill="#1a222c" />
      <rect x={300} y={500} width={680} height={80} fill="#3b4757" />
      <g>
        <rect x={560} y={420} width={80} height={80} fill="#475569" />
        <path d="M 600 420 L 560 260 L 700 200 L 690 330" fill="none" stroke="#f97316" strokeWidth={34} strokeLinecap="round" strokeLinejoin="round" />
        <animateTransform attributeName="transform" type="rotate" values="0 600 460; 4 600 460; 0 600 460" dur="2.4s" repeatCount="indefinite" />
      </g>
      <circle cx={690} cy={340} r={12} fill="#fde68a">
        <animate attributeName="opacity" values="1;0.2;1;0.5;1" dur="0.4s" repeatCount="indefinite" />
      </circle>
      {smoke &&
        [0, 1, 2, 3, 4].map((i) => (
          <circle key={i} cx={640 + i * 30} cy={300} r={60 + i * 10} fill="#94a3b8" opacity={0.25}>
            <animate attributeName="cy" values={`330;${170 - i * 10};330`} dur={`${3 + i * 0.5}s`} repeatCount="indefinite" />
            <animate attributeName="opacity" values="0.35;0.1;0.35" dur={`${3 + i * 0.5}s`} repeatCount="indefinite" />
          </circle>
        ))}
    </g>
  );
}

export function MockFeed({ cameraId, source, frozen }: { cameraId: string; source?: string | null; frozen?: boolean }) {
  const src = source ?? "";
  let scene;
  if (cameraId.startsWith("CAM-STP-01")) scene = <Feeder jam={src.includes("feed_jam")} />;
  else if (cameraId.startsWith("CAM-STP")) scene = <Press idle={src.includes("idle")} />;
  else if (cameraId.startsWith("CAM-ASM")) scene = <Assembly noHelmet={src.includes("no_helmet")} />;
  else scene = <Welding smoke={src.includes("smoke")} />;
  return (
    <svg viewBox="0 0 1280 720" preserveAspectRatio="none" className="h-full w-full" role="img" aria-label={`${cameraId} 模擬畫面`}>
      <defs>
        <filter id="blur">
          <feGaussianBlur stdDeviation="10" />
        </filter>
        <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#111a24" />
          <stop offset="1" stopColor="#0b1118" />
        </linearGradient>
      </defs>
      <rect width={1280} height={720} fill="url(#bg)" />
      <Grid />
      <g style={frozen ? { animationPlayState: "paused" } : undefined}>{scene}</g>
      <text x={1260} y={700} textAnchor="end" fill="#a855f7" fontSize={22} fontFamily="monospace" opacity={0.8}>
        SIMULATED FEED
      </text>
    </svg>
  );
}
