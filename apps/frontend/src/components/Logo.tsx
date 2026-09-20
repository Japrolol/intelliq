/** Display the supplied artwork without clipping the transparent asset bounds. */
export function Logo({ mark = false }: { mark?: boolean }) {
  return (
    <svg
      className={mark ? "block h-8 w-7" : "block h-auto w-28"}
      viewBox={mark ? "0 0 1254 1254" : "0 0 2172 724"}
      preserveAspectRatio="xMidYMid slice"
      role="img"
      aria-label="IntelliQ"
    >
      <image
        href={mark ? "/brand/intelliq-mark.png" : "/brand/intelliq-logo.png"}
        width={mark ? 1254 : 2172}
        height={mark ? 1254 : 724}
        preserveAspectRatio="xMidYMid slice"
      />
    </svg>
  );
}
