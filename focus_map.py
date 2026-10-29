"""Map where an image is in focus: per-tile sharpness, overlaid as a heat map.

Raw Laplacian variance mostly measures contrast, so each tile is scored by
a contrast-free ratio instead: energy in a fine band (0.8-px denoised image
minus a 2-px blur) over energy in a mid band (2-px blur minus 6-px blur).
Defocus removes the fine band first, so the ratio falls as a tile goes
soft, whatever its contrast. Only tiles with strong edges (top 35% of
mid-band energy) are judged; the rest are left grey.

  python focus_map.py IMAGE OUT.jpg [--tile 96]
"""
import argparse

import cv2
import numpy as np


def sharpness_tiles(gray, tile):
    # pre-blur 0.8 px so pixel noise (strong in dim areas) isn't read as detail
    g = cv2.GaussianBlur(gray.astype(np.float32), (0, 0), 0.8)
    b1 = cv2.GaussianBlur(g, (0, 0), 2.0)
    b4 = cv2.GaussianBlur(g, (0, 0), 6.0)
    fine, mid = (g - b1) ** 2, (b1 - b4) ** 2
    ny, nx = g.shape[0] // tile, g.shape[1] // tile
    ratio = np.full((ny, nx), np.nan, np.float32)
    mid_e = np.zeros((ny, nx), np.float32)
    for j in range(ny):
        for i in range(nx):
            s = np.s_[j * tile:(j + 1) * tile, i * tile:(i + 1) * tile]
            m = mid[s].mean()
            mid_e[j, i] = m
            ratio[j, i] = fine[s].mean() / m if m > 0 else np.nan
    # only judge tiles with real edges: top 35% of mid-band energy
    ratio[mid_e < np.percentile(mid_e, 65)] = np.nan
    return ratio


def overlay(img, ratio, tile):
    ok = ~np.isnan(ratio)
    lo, hi = np.percentile(ratio[ok], (5, 95))
    norm = np.clip((ratio - lo) / max(hi - lo, 1e-6), 0, 1)
    heat = cv2.applyColorMap((np.nan_to_num(norm) * 255).astype(np.uint8), cv2.COLORMAP_TURBO)
    heat[~ok] = (90, 90, 90)
    heat = cv2.resize(heat, (ratio.shape[1] * tile, ratio.shape[0] * tile), interpolation=cv2.INTER_NEAREST)
    base = img[:heat.shape[0], :heat.shape[1]]
    out = cv2.addWeighted(base, 0.55, heat, 0.45, 0)
    cv2.putText(out, "focus map: red = sharpest, blue = softest, grey = no detail to judge",
                (20, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (255, 255, 255), 4)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("out")
    ap.add_argument("--tile", type=int, default=96)
    a = ap.parse_args()
    img = cv2.imread(a.image)
    ratio = sharpness_tiles(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), a.tile)
    cv2.imwrite(a.out, overlay(img, ratio, a.tile))
    np.save(a.out.rsplit(".", 1)[0] + ".npy", ratio)


if __name__ == "__main__":
    main()
