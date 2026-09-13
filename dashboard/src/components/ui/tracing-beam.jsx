// Adapted from Aceternity UI "Tracing Beam" (https://ui.aceternity.com/components/tracing-beam).
// Guides the eye down the story; carries no information of its own.
import {useEffect, useRef, useState} from 'react';
import {motion, useScroll, useSpring, useTransform} from 'motion/react';
import {cn} from '../../lib/utils';

export function TracingBeam({children, className}) {
  const ref = useRef(null);
  const contentRef = useRef(null);
  const [height, setHeight] = useState(0);
  const {scrollYProgress} = useScroll({target: ref, offset: ['start start', 'end end']});

  useEffect(() => {
    if (!contentRef.current) return undefined;
    const observer = new ResizeObserver(() => setHeight(contentRef.current.offsetHeight));
    observer.observe(contentRef.current);
    return () => observer.disconnect();
  }, []);

  const y1 = useSpring(useTransform(scrollYProgress, [0, 0.8], [50, height]), {stiffness: 500, damping: 90});
  const y2 = useSpring(useTransform(scrollYProgress, [0, 1], [50, height - 200]), {stiffness: 500, damping: 90});

  return (
    <motion.div ref={ref} className={cn('relative mx-auto w-full', className)}>
      <div aria-hidden="true" className="absolute top-3 -left-2 hidden xl:block">
        <motion.div
          transition={{duration: 0.2, delay: 0.5}}
          animate={{boxShadow: scrollYProgress.get() > 0 ? 'none' : 'rgba(0, 0, 0, 0.24) 0px 3px 8px'}}
          className="ml-[27px] flex h-4 w-4 items-center justify-center rounded-full border border-rule bg-night"
        >
          <div className="h-2 w-2 rounded-full border border-glacier bg-ice/60" />
        </motion.div>
        <svg viewBox={`0 0 20 ${height}`} width="20" height={height} className="ml-4 block">
          <motion.path d={`M 1 0V -36 l 18 24 V ${height * 0.8} l -18 24V ${height}`} fill="none" stroke="#1f3149" strokeOpacity="0.9" />
          <motion.path d={`M 1 0V -36 l 18 24 V ${height * 0.8} l -18 24V ${height}`} fill="none" stroke="url(#beam-gradient)" strokeWidth="1.5" />
          <defs>
            <motion.linearGradient id="beam-gradient" gradientUnits="userSpaceOnUse" x1="0" x2="0" y1={y1} y2={y2}>
              <stop stopColor="#8fd6ff" stopOpacity="0" />
              <stop stopColor="#8fd6ff" />
              <stop offset="0.325" stopColor="#3f8fc4" />
              <stop offset="1" stopColor="#f4a940" stopOpacity="0" />
            </motion.linearGradient>
          </defs>
        </svg>
      </div>
      <div ref={contentRef}>{children}</div>
    </motion.div>
  );
}
