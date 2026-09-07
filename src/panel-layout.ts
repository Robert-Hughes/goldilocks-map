// Keep this aligned with the narrow-viewport media query in templates/goldilocks.html.
export const NARROW_PANEL_MEDIA_QUERY = "(max-width: 600px)";

export function syncNarrowPanelCorner(
  root: HTMLElement | null,
  expanded: boolean,
  media: MediaQueryList,
) {
  if (!root?.parentElement) return;
  root.parentElement.classList.toggle("goldilocks-narrow-panel-active", media.matches && expanded);
}
