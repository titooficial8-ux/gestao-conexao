import { createContext, useContext, useEffect, useRef, useState, useCallback } from 'react';
import { createPortal } from 'react-dom';

/* Kit de UI leve (sem Radix) para as páginas portadas do PCP Hub, com o
   visual do PCP Hub (Card/Dialog/Popover/Button) mas cores da Gestão Conexão
   (ver src/styles/gc-theme.css). */

export function Card({ className = '', children, ...rest }) {
  return <div className={`gc-card ${className}`} {...rest}>{children}</div>;
}
export function CardHeader({ className = '', children, ...rest }) {
  return <div className={`p-4 pb-2 ${className}`} {...rest}>{children}</div>;
}
export function CardTitle({ className = '', children, ...rest }) {
  return <h3 className={`font-heading font-semibold ${className}`} {...rest}>{children}</h3>;
}
export function CardContent({ className = '', children, ...rest }) {
  return <div className={`p-4 pt-2 ${className}`} {...rest}>{children}</div>;
}

const BTN_VARIANT = { default: 'gc-btn-default', outline: 'gc-btn-outline', ghost: 'gc-btn-ghost' };
export function Button({ className = '', variant = 'default', size, children, ...rest }) {
  const sizeClass = size === 'sm' ? 'gc-btn-sm' : '';
  return (
    <button className={`gc-btn ${BTN_VARIANT[variant] || BTN_VARIANT.default} ${sizeClass} ${className}`} {...rest}>
      {children}
    </button>
  );
}

export function Input({ className = '', ...rest }) {
  return <input className={`gc-input ${className}`} {...rest} />;
}

export function Dialog({ open, onOpenChange, children }) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e) => { if (e.key === 'Escape') onOpenChange?.(false); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, onOpenChange]);
  if (!open) return null;
  return createPortal(
    <div className="gc-dialog-overlay" onMouseDown={(e) => { if (e.target === e.currentTarget) onOpenChange?.(false); }}>
      {children}
    </div>,
    document.body,
  );
}
export function DialogContent({ className = '', children, ...rest }) {
  return <div className={`gc-dialog-content p-5 ${className}`} onMouseDown={(e) => e.stopPropagation()} {...rest}>{children}</div>;
}
export function DialogHeader({ className = '', children, ...rest }) {
  return <div className={`mb-3 ${className}`} {...rest}>{children}</div>;
}
export function DialogTitle({ className = '', children, ...rest }) {
  return <h2 className={`font-heading font-bold text-lg ${className}`} {...rest}>{children}</h2>;
}

const PopoverCtx = createContext(null);

export function Popover({ open, onOpenChange, children }) {
  const triggerRef = useRef(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e) => {
      if (triggerRef.current && !triggerRef.current.contains(e.target) && !e.target.closest('.gc-popover-content')) {
        onOpenChange?.(false);
      }
    };
    const onKey = (e) => { if (e.key === 'Escape') onOpenChange?.(false); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open, onOpenChange]);
  return (
    <PopoverCtx.Provider value={triggerRef}>
      <span ref={triggerRef} className="relative inline-block">{children}</span>
    </PopoverCtx.Provider>
  );
}
export function PopoverTrigger({ asChild, children, ...rest }) {
  return <span {...rest}>{children}</span>;
}
export function PopoverContent({ className = '', children, ...rest }) {
  const triggerRef = useContext(PopoverCtx);
  const [pos, setPos] = useState(null);
  useEffect(() => {
    if (!triggerRef?.current) return;
    const r = triggerRef.current.getBoundingClientRect();
    setPos({ top: r.bottom + 4, left: r.left });
  }, [triggerRef]);
  if (!pos) return null;
  return createPortal(
    <div className={`gc-popover-content ${className}`} style={{ position: 'fixed', top: pos.top, left: pos.left }} {...rest}>
      {children}
    </div>,
    document.body,
  );
}

/* Toast simples (substitui o hook useToast do shadcn/ui) */
const ToastCtx = createContext(() => {});
let idSeq = 0;
export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const toast = useCallback(({ title, description, variant }) => {
    const id = ++idSeq;
    setToasts((t) => [...t, { id, title, description, variant }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4500);
  }, []);
  return (
    <ToastCtx.Provider value={toast}>
      {children}
      {createPortal(
        <div className="gc-toast-viewport">
          {toasts.map((t) => (
            <div key={t.id} className={`gc-toast ${t.variant === 'destructive' ? 'gc-toast-destructive' : ''}`}>
              {t.title && <div className="gc-toast-title">{t.title}</div>}
              {t.description && <div className="gc-toast-description">{t.description}</div>}
            </div>
          ))}
        </div>,
        document.body,
      )}
    </ToastCtx.Provider>
  );
}
export function useToast() {
  const toast = useContext(ToastCtx);
  return { toast };
}
