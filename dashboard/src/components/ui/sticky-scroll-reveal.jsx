// Adapted from Aceternity UI "Sticky Scroll Reveal"
// (https://ui.aceternity.com/components/sticky-scroll-reveal). The original scrolls an
// inner container; this version follows page scroll so the reader is never trapped.
import {useRef, useState} from 'react';
import {motion, useMotionValueEvent, useScroll} from 'motion/react';
import {cn} from '../../lib/utils';

export function StickyScroll({content, className}) {
  const ref = useRef(null);
  const [active, setActive] = useState(0);
  const {scrollYProgress} = useScroll({target: ref, offset: ['start center', 'end center']});

  useMotionValueEvent(scrollYProgress, 'change', latest => {
    const index = Math.min(content.length - 1, Math.max(0, Math.floor(latest * content.length)));
    setActive(index);
  });

  return (
    <div ref={ref} className={cn('relative grid gap-10 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]', className)}>
      <div>
        {content.map((item, index) => (
          <motion.article
            key={item.title}
            animate={{opacity: active === index ? 1 : 0.35}}
            className="flex min-h-[70vh] flex-col justify-center py-10 lg:min-h-[85vh]"
          >
            <div className="font-mono text-xs tracking-widest text-sodium uppercase">{item.eyebrow}</div>
            <h3 className="mt-3 font-display text-4xl font-bold leading-none tracking-wide text-snow md:text-5xl">{item.title}</h3>
            <div className="mt-5 max-w-xl space-y-4 text-frost">{item.description}</div>
            {/* On small screens the visual sits under its own text instead of in a sticky pane. */}
            <div className="mt-8 lg:hidden">{item.visual}</div>
          </motion.article>
        ))}
      </div>
      <div className="hidden lg:block">
        <div className="sticky top-[12vh] flex h-[76vh] items-center">
          <motion.div
            key={active}
            initial={{opacity: 0, y: 12}}
            animate={{opacity: 1, y: 0}}
            transition={{duration: 0.35}}
            className="w-full rounded-2xl border border-rule bg-deep/80 p-6"
          >
            {content[active].visual}
          </motion.div>
        </div>
      </div>
    </div>
  );
}
