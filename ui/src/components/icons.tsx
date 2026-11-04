/**
 * One small icon set, drawn on a 24px grid with a 1.75 stroke in currentColor. Every icon is
 * decorative (aria-hidden): the text beside it, or the control's aria-label, carries the meaning.
 */
import type { ReactNode, SVGProps } from "react";

function Icon({ children, ...rest }: SVGProps<SVGSVGElement> & { children: ReactNode }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width="18"
      height="18"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...rest}
    >
      {children}
    </svg>
  );
}

export const icons = {
  cart: (
    <Icon>
      <path d="M3 4h2l2.4 11.2a1 1 0 0 0 1 .8h8.9a1 1 0 0 0 1-.8L20 8H6.2" />
      <circle cx="9" cy="20" r="1.2" />
      <circle cx="17" cy="20" r="1.2" />
    </Icon>
  ),
  forklift: (
    <Icon>
      <path d="M3 18V9h6l3 5v4" />
      <path d="M12 14h3" />
      <path d="M18 4v14h3" />
      <circle cx="6" cy="18.5" r="1.5" />
      <circle cx="12" cy="18.5" r="1.5" />
    </Icon>
  ),
  crowd: (
    <Icon>
      <circle cx="8" cy="8" r="2.5" />
      <circle cx="16" cy="8" r="2.5" />
      <path d="M3 19c.6-3 2.6-5 5-5s4.4 2 5 5" />
      <path d="M13.5 14.6c.8-.4 1.6-.6 2.5-.6 2.4 0 4.4 2 5 5" />
    </Icon>
  ),
  pen: (
    <Icon>
      <path d="M4 20h4L19 9a2.1 2.1 0 0 0-3-3L5 17v3Z" />
      <path d="M14 7l3 3" />
    </Icon>
  ),
  file: (
    <Icon>
      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8Z" />
      <path d="M14 3v5h5M9 13h6M9 17h6" />
    </Icon>
  ),
  check: (
    <Icon>
      <path d="M9 11l3 3 8-8" />
      <path d="M20 12v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h9" />
    </Icon>
  ),
  code: (
    <Icon>
      <path d="M16 18l6-6-6-6M8 6l-6 6 6 6" />
    </Icon>
  ),
  monitor: (
    <Icon>
      <rect x="3" y="4" width="18" height="12" rx="2" />
      <path d="M8 20h8M12 16v4" />
      <circle cx="12" cy="10" r="2.5" />
    </Icon>
  ),
  video: (
    <Icon>
      <rect x="2.5" y="6" width="13" height="12" rx="2" />
      <path d="M15.5 10.5 21 7v10l-5.5-3.5" />
    </Icon>
  ),
  bell: (
    <Icon>
      <path d="M6 8a6 6 0 0 1 12 0c0 7 3 8 3 8H3s3-1 3-8" />
      <path d="M10.3 20a1.9 1.9 0 0 0 3.4 0" />
    </Icon>
  ),
  chat: (
    <Icon>
      <path d="M21 15a2 2 0 0 1-2 2H8l-5 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2Z" />
    </Icon>
  ),
  bars: (
    <Icon>
      <path d="M18 20V10M12 20V4M6 20v-6" />
    </Icon>
  ),
  globe: (
    <Icon>
      <circle cx="12" cy="12" r="9" />
      <path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18" />
    </Icon>
  ),
  play: (
    <Icon>
      <path d="M7 4.5v15l12-7.5Z" />
    </Icon>
  ),
  shield: (
    <Icon>
      <path d="M12 21s8-4 8-10V5l-8-3-8 3v6c0 6 8 10 8 10Z" />
      <path d="M9 11.5l2 2 4-4" />
    </Icon>
  ),
  alert: (
    <Icon>
      <path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" />
      <path d="M12 9v4M12 17h.01" />
    </Icon>
  ),
  qr: (
    <Icon>
      <rect x="3" y="3" width="7" height="7" rx="1" />
      <rect x="14" y="3" width="7" height="7" rx="1" />
      <rect x="3" y="14" width="7" height="7" rx="1" />
      <path d="M14 14h3v3M21 14v7h-4M14 18v3" />
    </Icon>
  ),
  hash: (
    <Icon>
      <path d="M5 9h15M4 15h15M10 3 8 21M16 3l-2 18" />
    </Icon>
  ),
  cpu: (
    <Icon>
      <rect x="5" y="5" width="14" height="14" rx="2" />
      <rect x="9" y="9" width="6" height="6" rx="1" />
      <path d="M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3" />
    </Icon>
  ),
  menu: (
    <Icon>
      <path d="M4 7h16M4 12h16M4 17h16" />
    </Icon>
  ),
  sidebar: (
    <Icon>
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M9 4v16" />
    </Icon>
  ),
  close: (
    <Icon>
      <path d="M6 6l12 12M18 6 6 18" />
    </Icon>
  ),
  sun: (
    <Icon>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
    </Icon>
  ),
  moon: (
    <Icon>
      <path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5Z" />
    </Icon>
  ),
  glasses: (
    <Icon>
      <circle cx="6.5" cy="14" r="3.5" />
      <circle cx="17.5" cy="14" r="3.5" />
      <path d="M10 14h4M3 14 4.5 7.5A2 2 0 0 1 6.4 6H8M21 14l-1.5-6.5A2 2 0 0 0 17.6 6H16" />
    </Icon>
  ),
  plug: (
    <Icon>
      <path d="M9 2v5M15 2v5M6 7h12v4a6 6 0 0 1-12 0Z" />
      <path d="M12 17v5" />
    </Icon>
  ),
  phone: (
    <Icon>
      <rect x="6" y="2" width="12" height="20" rx="2.5" />
      <path d="M11 18h2" />
    </Icon>
  ),
  seal: (
    <Icon>
      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h4" />
      <path d="M14 3v5h5" />
      <circle cx="16.5" cy="15.5" r="3.5" />
      <path d="M14.5 18.5 14 22l2.5-1.2L19 22l-.5-3.5" />
    </Icon>
  ),
  plus: (
    <Icon>
      <path d="M12 5v14M5 12h14" />
    </Icon>
  ),
  arrowRight: (
    <Icon>
      <path d="M5 12h14M13 6l6 6-6 6" />
    </Icon>
  ),
  external: (
    <Icon>
      <path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" />
    </Icon>
  ),
} as const;

export type IconName = keyof typeof icons;

/**
 * The Rulecam mark: a camera lens inside a rule's square bracket. The lens takes the person
 * colour (amber); the bracket follows the ink.
 */
export function RulecamMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true" focusable="false">
      <path
        d="M8 3H4v18h4M16 3h4v18h-4"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle className="lens" cx="12" cy="12" r="4" />
    </svg>
  );
}
