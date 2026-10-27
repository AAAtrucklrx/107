import { forwardRef, useId, type InputHTMLAttributes } from "react";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement> & { label?: string }>(function Input(
  { label, id, className = "", ...props }, ref,
) {
  const generatedId = useId();
  const inputId = id ?? generatedId;
  return <div className="ui-field">{label && <label htmlFor={inputId}>{label}</label>}
    <input {...props} id={inputId} ref={ref} className={`ui-input ${className}`} />
  </div>;
});
