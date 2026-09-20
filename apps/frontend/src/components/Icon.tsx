import type { SVGProps } from "react";

export type IconName =
  | "arrow-left"
  | "arrow-right"
  | "arrow-up-right"
  | "calendar"
  | "check"
  | "chevron-down"
  | "chevron-right"
  | "clipboard"
  | "clock"
  | "close"
  | "file-text"
  | "layers"
  | "lock"
  | "logout"
  | "menu"
  | "refresh"
  | "search"
  | "shield"
  | "spark"
  | "triangle"
  | "users"
  | "x";

const paths: Record<IconName, string[]> = {
  "arrow-left": ["M19 12H5", "M12 19l-7-7 7-7"],
  "arrow-right": ["M5 12h14", "M12 5l7 7-7 7"],
  "arrow-up-right": ["M7 17 17 7", "M7 7h10v10"],
  calendar: [
    "M7 3v4",
    "M17 3v4",
    "M4 9h16",
    "M5 5h14a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1Z",
  ],
  check: ["m5 12 4 4L19 6"],
  "chevron-down": ["m6 9 6 6 6-6"],
  "chevron-right": ["m9 18 6-6-6-6"],
  clipboard: [
    "M9 5h6",
    "M9 3h6v4H9z",
    "M6 5H5a1 1 0 0 0-1 1v14a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1V6a1 1 0 0 0-1-1h-1",
    "M8 12h8",
    "M8 16h5",
  ],
  clock: ["M12 7v5l3 2", "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z"],
  close: ["M6 6l12 12", "M18 6 6 18"],
  "file-text": [
    "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z",
    "M14 2v6h6",
    "M8 13h8",
    "M8 17h6",
  ],
  layers: ["m12 2 9 5-9 5-9-5 9-5Z", "m3 12 9 5 9-5", "m3 17 9 5 9-5"],
  lock: ["M7 10V7a5 5 0 0 1 10 0v3", "M5 10h14v11H5z"],
  logout: ["M10 17l5-5-5-5", "M15 12H3", "M21 19V5a2 2 0 0 0-2-2h-6"],
  menu: ["M4 6h16", "M4 12h16", "M4 18h16"],
  refresh: [
    "M20 11a8.1 8.1 0 0 0-15.5-2M4 5v4h4",
    "M4 13a8.1 8.1 0 0 0 15.5 2M20 19v-4h-4",
  ],
  search: ["m21 21-4.3-4.3", "M11 19a8 8 0 1 1 0-16 8 8 0 0 1 0 16Z"],
  shield: ["M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z", "m9 12 2 2 4-4"],
  spark: [
    "m12 3-1.5 5.5L5 10l5.5 1.5L12 17l1.5-5.5L19 10l-5.5-1.5L12 3Z",
    "m19 16-.7 2.3L16 19l2.3.7L19 22l.7-2.3L22 19l-2.3-.7L19 16Z",
  ],
  triangle: ["m12 3 9 18H3L12 3Z", "M12 9v4", "M12 17h.01"],
  users: [
    "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2",
    "M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z",
    "M22 21v-2a4 4 0 0 0-3-3.87",
    "M16 3.13a4 4 0 0 1 0 7.75",
  ],
  x: ["M6 6l12 12", "M18 6 6 18"],
};

export function Icon({
  name,
  size = 18,
  strokeWidth = 1.8,
  ...props
}: SVGProps<SVGSVGElement> & {
  name: IconName;
  size?: number;
  strokeWidth?: number;
}) {
  return (
    <svg
      aria-hidden="true"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      {...props}
    >
      {paths[name].map((path) => (
        <path key={path} d={path} />
      ))}
    </svg>
  );
}
