"""Derive the missing tag-swatch quads for design-system/MASTER.md 2.4.

Input: the 14 published light-text hexes (hue + chroma are taken from them, so the
family stays recognisably the same colour). Output: light tint, dark text, dark tint,
each searched for the lowest-contrast value that still clears 2.4's floors
(>=5.9:1 light, >=7.5:1 dark) while staying inside the sRGB gamut.
"""
import math

SWATCHES = [
    ("slate", "#0D53AF"), ("blue", "#2151AF"), ("azure", "#076082"), ("cyan", "#076566"),
    ("teal", "#076758"), ("green", "#076A2F"), ("lime", "#456405"), ("gold", "#685704"),
    ("amber", "#745004"), ("orange", "#903A04"), ("rose", "#9C1F43"), ("magenta", "#8E2873"),
    ("violet", "#673BA2"), ("indigo", "#3C4BAF"),
]

def srgb_to_lin(c): return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
def lin_to_srgb(c): return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055

M1 = [[0.4122214708, 0.5363325363, 0.0514459929],
      [0.2119034982, 0.6806995451, 0.1073969566],
      [0.0883024619, 0.2817188376, 0.6299787005]]
M2 = [[0.2104542553, 0.7936177850, -0.0040720468],
      [1.9779984951, -2.4285922050, 0.4505937099],
      [0.0259040371, 0.7827717662, -0.8086757660]]

def lin_rgb_to_oklab(r, g, b):
    l, m, s = (sum(M1[i][j] * v for j, v in enumerate((r, g, b))) for i in range(3))
    l, m, s = (math.copysign(abs(x) ** (1 / 3), x) for x in (l, m, s))
    return tuple(sum(M2[i][j] * v for j, v in enumerate((l, m, s))) for i in range(3))

def oklab_to_lin_rgb(L, a, b):
    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b
    l, m, s = l_ ** 3, m_ ** 3, s_ ** 3
    return (+4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
            -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
            -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s)

def hex_to_oklch(h):
    r, g, b = (srgb_to_lin(int(h[i:i + 2], 16) / 255) for i in (1, 3, 5))
    L, a, bb = lin_rgb_to_oklab(r, g, b)
    return L, math.hypot(a, bb), math.degrees(math.atan2(bb, a)) % 360

def oklch_to_rgb(L, C, H):
    a, b = C * math.cos(math.radians(H)), C * math.sin(math.radians(H))
    return oklab_to_lin_rgb(L, a, b)

def in_gamut(L, C, H, eps=1e-4):
    return all(-eps <= c <= 1 + eps for c in oklch_to_rgb(L, C, H))

def clamp_chroma(L, C, H):
    """Largest chroma <= C that stays in sRGB. Bisection is plenty at this precision."""
    if in_gamut(L, C, H):
        return C
    lo, hi = 0.0, C
    for _ in range(40):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if in_gamut(L, mid, H) else (lo, mid)
    return lo

def to_hex(L, C, H):
    return "#" + "".join(f"{round(max(0, min(1, lin_to_srgb(c))) * 255):02X}"
                         for c in oklch_to_rgb(L, C, H))

def relative_luminance(hexstr):
    r, g, b = (srgb_to_lin(int(hexstr[i:i + 2], 16) / 255) for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b

def contrast(h1, h2):
    a, b = sorted((relative_luminance(h1), relative_luminance(h2)))
    return (b + 0.05) / (a + 0.05)

def search(fixed_hex, H, C, floor, lightness_range):
    """Walk lightness from the least-contrasty end until the pair clears `floor`."""
    for step in lightness_range:
        c = clamp_chroma(step, C, H)
        cand = to_hex(step, c, H)
        if contrast(fixed_hex, cand) >= floor:
            return step, c, cand
    raise SystemExit(f"no lightness satisfies {floor}:1 against {fixed_hex}")

rows = []
for name, light_text in SWATCHES:
    Lt, Ct, H = hex_to_oklch(light_text)
    # Light tint: pale wash of the same hue, chroma held low so it reads as a tint.
    lt_L, lt_C, light_tint = search(light_text, H, 0.030, 5.9,
                                    [x / 1000 for x in range(900, 1001, 2)])
    # Dark tint first: deep, near-canvas ground for the dark theme.
    dt_L, dt_C = 0.245, clamp_chroma(0.245, 0.028, H)
    dark_tint = to_hex(dt_L, dt_C, H)
    # Dark text: lightened family member that clears 7.5:1 on that ground.
    dx_L, dx_C, dark_text = search(dark_tint, H, min(Ct, 0.130), 7.5,
                                   [x / 1000 for x in range(700, 1001, 2)])
    rows.append((name, light_text, light_tint, dark_text, dark_tint,
                 contrast(light_text, light_tint), contrast(dark_text, dark_tint),
                 (lt_L, lt_C), (dx_L, dx_C), (dt_L, dt_C), H))

print("| Swatch | Light text / tint | Dark text / tint | Light | Dark |")
print("|---|---|---|---|---|")
for n, a, b, c, d, cl, cd, *_ in rows:
    print(f"| `{n}` | `{a}` / `{b}` | `{c}` / `{d}` | {cl:.1f}:1 | {cd:.1f}:1 |")
print()
print("min light", min(r[5] for r in rows), "min dark", min(r[6] for r in rows))
