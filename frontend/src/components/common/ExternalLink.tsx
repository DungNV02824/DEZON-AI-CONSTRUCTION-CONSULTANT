import type { ReactNode } from 'react';

/** Link mở tab mới an toàn (rel noopener noreferrer). */
export function ExternalLink({
  href,
  children,
  className,
  onClick,
  ariaLabel,
}: {
  href: string;
  children: ReactNode;
  className?: string;
  onClick?: () => void;
  ariaLabel?: string;
}) {
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" className={className} onClick={onClick} aria-label={ariaLabel}>
      {children}
    </a>
  );
}
