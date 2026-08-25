import type { HTMLAttributes } from "react";

export type SurfaceShadow = "sm" | "md" | "lg" | "xl" | "drag";

type SurfaceElement = "article" | "aside" | "div" | "section";

type SurfaceProps = HTMLAttributes<HTMLElement> & {
  as?: SurfaceElement;
  shadow?: SurfaceShadow;
};

export default function Surface({
  as: Component = "div",
  className,
  shadow = "md",
  ...props
}: SurfaceProps) {
  const classes = ["material-surface", "material-shadow", className].filter(Boolean).join(" ");

  return <Component {...props} className={classes} data-shadow={shadow} />;
}
