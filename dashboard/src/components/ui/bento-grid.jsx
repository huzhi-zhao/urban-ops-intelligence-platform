// Adapted from Aceternity UI "Bento Grid" (https://ui.aceternity.com/components/bento-grid).
import {cn} from '../../lib/utils';

export function BentoGrid({className, children}) {
  return <div className={cn('mx-auto grid grid-cols-1 gap-4 md:auto-rows-[15rem] md:grid-cols-3', className)}>{children}</div>;
}

export function BentoGridItem({className, title, description, header, eyebrow}) {
  return (
    <div className={cn('group/bento row-span-1 flex flex-col justify-between gap-4 rounded-2xl border border-rule bg-deep/70 p-5 transition duration-200 hover:border-glacier/60', className)}>
      {header}
      <div className="transition duration-200 group-hover/bento:translate-x-1">
        {eyebrow && <div className="mb-1 font-mono text-xs tracking-wide text-ice uppercase">{eyebrow}</div>}
        <div className="font-display text-2xl font-bold tracking-wide text-snow">{title}</div>
        <div className="mt-1 text-[15px] leading-snug text-frost">{description}</div>
      </div>
    </div>
  );
}
