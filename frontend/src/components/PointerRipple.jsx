import React, {useEffect, useRef} from 'react';

const SPACING = 7;
const RADIUS = 126;
const SIZE = 2 * RADIUS;

export default function PointerRipple() {
  const canvasRef = useRef(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const surface = canvas.closest('.app-main');
    if (!surface) return;
    const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
    const context = canvas.getContext('2d');
    if (!context) return;

    let pointer;
    let frame = 0;
    let strength = 0;
    let lastTime = 0;
    let lastMove = 0;
    const resolution = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = SIZE * resolution;
    canvas.height = SIZE * resolution;
    canvas.style.width = `${SIZE}px`;
    canvas.style.height = `${SIZE}px`;
    context.scale(resolution, resolution);
    const paper = getComputedStyle(surface).backgroundColor;

    function draw(time) {
      frame = 0;
      const elapsed = Math.min(time - lastTime, 32);
      lastTime = time;
      const age = time - lastMove;
      const target = pointer.active ? Math.exp(-Math.max(0, age - 100) / 400) : 0;
      const settlingTime = target > strength ? 85 : 240;
      strength += (target - strength) * (1 - Math.exp(-elapsed / settlingTime));
      if (target < .003 && strength < .003) {
        canvas.style.opacity = '0';
        return;
      }

      const left = Math.round((pointer.x - RADIUS) / SPACING) * SPACING;
      const top = Math.round((pointer.y - RADIUS) / SPACING) * SPACING;
      canvas.style.transform = `translate(${left}px, ${top}px)`;
      canvas.style.opacity = String(strength);
      context.fillStyle = paper;
      context.fillRect(0, 0, SIZE, SIZE);
      context.fillStyle = '#d0c5b5';
      context.beginPath();
      for (let y = SPACING / 2; y < SIZE; y += SPACING) {
        for (let x = SPACING / 2; x < SIZE; x += SPACING) {
          const dx = x + left - pointer.x;
          const dy = y + top - pointer.y;
          const distance = Math.hypot(dx, dy);
          const envelope = Math.max(0, 1 - distance / RADIUS);
          const wave = Math.sin(distance / 9 - time / 145);
          const offset = 3.2 * envelope * wave * strength;
          const scale = distance ? offset / distance : 0;
          const dotX = x + dx * scale;
          const dotY = y + dy * scale;
          const radius = .5 + .25 * envelope * (1 + wave) / 2 * strength;
          context.moveTo(dotX + radius, dotY);
          context.arc(dotX, dotY, radius, 0, 2 * Math.PI);
        }
      }
      context.fill();
      frame = requestAnimationFrame(draw);
    }

    function move(event) {
      if (motion.matches || event.pointerType !== 'mouse') return;
      const bounds = surface.getBoundingClientRect();
      pointer = {x: event.clientX - bounds.left, y: event.clientY - bounds.top, active: true};
      lastMove = performance.now();
      if (!frame) {
        lastTime = lastMove;
        frame = requestAnimationFrame(draw);
      }
    }

    function fade() {
      if (pointer) pointer.active = false;
    }

    function reset() {
      cancelAnimationFrame(frame);
      frame = 0;
      strength = 0;
      pointer = undefined;
      canvas.style.opacity = '0';
    }

    surface.addEventListener('pointermove', move, {passive: true});
    surface.addEventListener('pointerleave', fade);
    window.addEventListener('scroll', reset);
    window.addEventListener('blur', reset);
    motion.addEventListener('change', reset);
    return () => {
      reset();
      surface.removeEventListener('pointermove', move);
      surface.removeEventListener('pointerleave', fade);
      window.removeEventListener('scroll', reset);
      window.removeEventListener('blur', reset);
      motion.removeEventListener('change', reset);
    };
  }, []);

  return <div className="ripple-layer" aria-hidden="true">
    <canvas ref={canvasRef} />
  </div>;
}
