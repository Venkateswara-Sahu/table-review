import type { PageBox } from "../../api/models";

export interface Viewport {
  width: number;
  height: number;
  offsetLeft?: number;
  offsetTop?: number;
}
export interface PixelBox {
  left: number;
  top: number;
  width: number;
  height: number;
}

export function sourceBoxToPixels(box: PageBox, viewport: Viewport): PixelBox {
  const sx = viewport.width / box.page_width;
  const sy = viewport.height / box.page_height;
  return {
    left: box.left * sx - (viewport.offsetLeft ?? 0),
    top: box.top * sy - (viewport.offsetTop ?? 0),
    width: (box.right - box.left) * sx,
    height: (box.bottom - box.top) * sy,
  };
}
