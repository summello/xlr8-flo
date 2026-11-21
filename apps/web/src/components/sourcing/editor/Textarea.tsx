import { forwardRef, type TextareaHTMLAttributes } from "react";
import { useFieldContext } from "../../form/context";

/** The forms kit has no multi-line control yet; this one reads the same `Field` context as `Input`. */
const Textarea = forwardRef<HTMLTextAreaElement, Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, "aria-describedby" | "aria-invalid" | "id">>(function Textarea(props, ref) {
  const field = useFieldContext();
  return <textarea rows={3} {...props} aria-describedby={field.describedBy} aria-invalid={field.error === undefined ? undefined : true} aria-required={field.required || undefined} id={field.id} ref={ref} />;
});

export default Textarea;
