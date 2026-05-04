"""Camera entity: accumulated cleaning path map rendered as a PNG."""
from __future__ import annotations

import io
import logging
from typing import Any

from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_DEVICE_NAME, DOMAIN
from .coordinator import EufyX8Coordinator

_LOGGER = logging.getLogger(__name__)

IMAGE_SIZE = 600       # pixels square
MARGIN = 20            # pixel margin inside image
DOT_RADIUS = 2         # path point dot size
DOCK_RADIUS = 6        # dock marker size

# Colours per session (cycles after 8 sessions)
SESSION_COLOURS = [
    (100, 200, 255),   # light blue
    (100, 255, 150),   # light green
    (255, 200, 100),   # orange
    (200, 100, 255),   # purple
    (255, 100, 150),   # pink
    (100, 255, 255),   # cyan
    (255, 255, 100),   # yellow
    (180, 180, 180),   # grey (oldest)
]
DOCK_COLOUR = (255, 80, 80)      # red
BG_COLOUR = (20, 20, 20)         # near-black background


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EufyX8Coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([CleaningMapCamera(coordinator, entry)])


class CleaningMapCamera(Camera):
    """Renders accumulated cleaning path data as a PNG image."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: EufyX8Coordinator, entry: ConfigEntry) -> None:
        super().__init__()
        self._coordinator = coordinator
        self._attr_name = "Cleaning Map"
        self._attr_unique_id = f"{entry.data['device_id']}_map"
        self._attr_is_streaming = False
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.data["device_id"])},
            name=entry.data[CONF_DEVICE_NAME],
            manufacturer="Eufy",
            model="X8 / X8 Pro",
        )
        self._cached_image: bytes | None = None
        self._cached_session_count: int = 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        sessions = self._coordinator.map_data.get("sessions", [])
        total_points = sum(len(s.get("points", [])) for s in sessions)
        return {
            "sessions_stored": len(sessions),
            "total_path_points": total_points,
            "latest_session": sessions[-1]["timestamp"] if sessions else None,
        }

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        sessions = self._coordinator.map_data.get("sessions", [])
        if not sessions:
            return _empty_image()

        # Only re-render if data changed
        if (self._cached_image is not None
                and len(sessions) == self._cached_session_count):
            return self._cached_image

        image = await self._coordinator.hass.async_add_executor_job(
            _render_map, sessions
        )
        self._cached_image = image
        self._cached_session_count = len(sessions)
        return image


def _empty_image() -> bytes:
    try:
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new("RGB", (IMAGE_SIZE, IMAGE_SIZE), BG_COLOUR)
        draw = ImageDraw.Draw(img)
        draw.text((IMAGE_SIZE // 2 - 80, IMAGE_SIZE // 2),
                  "No cleaning data yet", fill=(120, 120, 120))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except ImportError:
        return b""


def _render_map(sessions: list[dict]) -> bytes:
    """Render accumulated path sessions as a PNG. Runs in executor."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        _LOGGER.warning("Pillow not available — map rendering disabled")
        return b""

    # Collect all points to compute bounds
    all_points = [pt for s in sessions for pt in s.get("points", [])]
    if not all_points:
        return _empty_image()

    xs = [p["x"] for p in all_points]
    ys = [p["y"] for p in all_points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    span_x = max_x - min_x or 1
    span_y = max_y - min_y or 1

    draw_area = IMAGE_SIZE - 2 * MARGIN

    def to_px(x, y):
        px = int(MARGIN + (x - min_x) / span_x * draw_area)
        py = int(MARGIN + (y - min_y) / span_y * draw_area)
        return px, py

    img = Image.new("RGB", (IMAGE_SIZE, IMAGE_SIZE), BG_COLOUR)
    draw = ImageDraw.Draw(img)

    # Draw each session in a different colour, oldest = most faded
    n = len(sessions)
    for i, session in enumerate(sessions):
        colour = SESSION_COLOURS[min(n - 1 - i, len(SESSION_COLOURS) - 1)]
        points = session.get("points", [])
        # Draw lines between consecutive points for a path effect
        for j in range(len(points) - 1):
            p1 = to_px(points[j]["x"], points[j]["y"])
            p2 = to_px(points[j + 1]["x"], points[j + 1]["y"])
            draw.line([p1, p2], fill=colour, width=1)
        # Draw dots on top
        for pt in points:
            cx, cy = to_px(pt["x"], pt["y"])
            draw.ellipse(
                [cx - DOT_RADIUS, cy - DOT_RADIUS, cx + DOT_RADIUS, cy + DOT_RADIUS],
                fill=colour,
            )

    # Mark the first point of the most recent session as the dock (approximate)
    if sessions:
        first = sessions[-1]["points"][0] if sessions[-1].get("points") else None
        if first:
            dx, dy = to_px(first["x"], first["y"])
            draw.ellipse(
                [dx - DOCK_RADIUS, dy - DOCK_RADIUS, dx + DOCK_RADIUS, dy + DOCK_RADIUS],
                fill=DOCK_COLOUR, outline=(255, 255, 255), width=1,
            )

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
