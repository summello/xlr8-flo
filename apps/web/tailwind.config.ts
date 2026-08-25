import type { Config } from "tailwindcss";

const config = {
  theme: {
    extend: {
      colors: {
        canvas: "var(--canvas)",
        sunken: "var(--sunken)",
        surface: "var(--surface)",
        raised: "var(--raised)",
        foreground: "var(--fg)",
        "foreground-secondary": "var(--fg-secondary)",
        "foreground-muted": "var(--fg-muted)",
        border: "var(--border)",
        "border-strong": "var(--border-strong)",
        ring: "var(--ring)",
        scrim: "var(--scrim)",
        phase: {
          foundation: "var(--phase-foundation)",
          plan: "var(--phase-plan)",
          demand: "var(--phase-demand)",
          commit: "var(--phase-commit)",
        },
        status: {
          neutral: {
            DEFAULT: "var(--status-neutral)",
            tint: "var(--status-neutral-tint)",
          },
          info: { DEFAULT: "var(--status-info)", tint: "var(--status-info-tint)" },
          success: {
            DEFAULT: "var(--status-success)",
            tint: "var(--status-success-tint)",
          },
          warning: {
            DEFAULT: "var(--status-warning)",
            tint: "var(--status-warning-tint)",
          },
          danger: {
            DEFAULT: "var(--status-danger)",
            tint: "var(--status-danger-tint)",
          },
        },
        tag: {
          slate: { DEFAULT: "var(--tag-slate)", tint: "var(--tag-slate-tint)" },
          blue: { DEFAULT: "var(--tag-blue)", tint: "var(--tag-blue-tint)" },
          azure: { DEFAULT: "var(--tag-azure)", tint: "var(--tag-azure-tint)" },
          cyan: { DEFAULT: "var(--tag-cyan)", tint: "var(--tag-cyan-tint)" },
          teal: { DEFAULT: "var(--tag-teal)", tint: "var(--tag-teal-tint)" },
          green: { DEFAULT: "var(--tag-green)", tint: "var(--tag-green-tint)" },
          lime: { DEFAULT: "var(--tag-lime)", tint: "var(--tag-lime-tint)" },
          gold: { DEFAULT: "var(--tag-gold)", tint: "var(--tag-gold-tint)" },
          amber: { DEFAULT: "var(--tag-amber)", tint: "var(--tag-amber-tint)" },
          orange: { DEFAULT: "var(--tag-orange)", tint: "var(--tag-orange-tint)" },
          rose: { DEFAULT: "var(--tag-rose)", tint: "var(--tag-rose-tint)" },
          magenta: {
            DEFAULT: "var(--tag-magenta)",
            tint: "var(--tag-magenta-tint)",
          },
          violet: { DEFAULT: "var(--tag-violet)", tint: "var(--tag-violet-tint)" },
          indigo: { DEFAULT: "var(--tag-indigo)", tint: "var(--tag-indigo-tint)" },
        },
        money: {
          inflow: "var(--money-inflow)",
          outflow: "var(--money-outflow)",
          zero: "var(--money-zero)",
        },
        chart: {
          1: "var(--chart-1)",
          2: "var(--chart-2)",
          3: "var(--chart-3)",
          4: "var(--chart-4)",
          5: "var(--chart-5)",
          6: "var(--chart-6)",
          7: "var(--chart-7)",
          8: "var(--chart-8)",
        },
      },
      spacing: {
        1: "var(--space-1)",
        2: "var(--space-2)",
        3: "var(--space-3)",
        4: "var(--space-4)",
        5: "var(--space-5)",
        6: "var(--space-6)",
        7: "var(--space-7)",
        8: "var(--space-8)",
      },
      borderRadius: {
        input: "var(--radius-input)",
        control: "var(--radius-control)",
        card: "var(--radius-card)",
        pill: "var(--radius-pill)",
      },
      boxShadow: {
        sm: "var(--shadow-sm)",
        md: "var(--shadow-md)",
        lg: "var(--shadow-lg)",
        xl: "var(--shadow-xl)",
        drag: "var(--shadow-drag)",
        "lit-edge": "var(--lit-edge)",
      },
      transitionDuration: {
        instant: "var(--dur-instant)",
        fast: "var(--dur-fast)",
        base: "var(--dur-base)",
        slow: "var(--dur-slow)",
        page: "var(--dur-page)",
      },
      transitionTimingFunction: {
        out: "var(--ease-out)",
        in: "var(--ease-in)",
        inout: "var(--ease-inout)",
        spring: "var(--ease-spring)",
      },
    },
  },
} satisfies Config;

export default config;
