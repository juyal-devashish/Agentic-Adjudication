"""Drag a box on a page preview (plan step 11, P-30), with Streamlit's own box selection
on a Plotly chart: no third-party component to break on a Streamlit upgrade.

The page image fills the plot; x runs 0..1 across the page and y 0..height/width down it,
so the axes keep the page's proportions. A grid of invisible points makes box selection
available everywhere on the page. The result is the box as fractions of the page.
"""

from __future__ import annotations

import io

import plotly.graph_objects as go
import streamlit as st
from PIL import Image
from theme import VIOLET

GRID = 40  # invisible points across the page


def page_box(png: bytes, key: str, height_px: int = 820) -> list[float] | None:
    """Show the page; return the last dragged box as [x0, y0, x1, y1] fractions, or None."""
    img = Image.open(io.BytesIO(png))
    aspect = img.height / img.width
    rows = max(1, round(GRID * aspect))
    xs = [(i + 0.5) / GRID for i in range(GRID) for _ in range(rows)]
    ys = [(j + 0.5) / rows * aspect for _ in range(GRID) for j in range(rows)]
    fig = go.Figure(
        go.Scatter(
            x=xs, y=ys, mode="markers", marker={"opacity": 0, "size": 6},
            hoverinfo="skip", showlegend=False,
        )
    )  # fmt: skip
    fig.add_layout_image(
        source=img, xref="x", yref="y", x=0, y=0, sizex=1, sizey=aspect,
        sizing="stretch", layer="below",
    )  # fmt: skip
    fig.update_xaxes(visible=False, range=[0, 1], fixedrange=True)
    fig.update_yaxes(visible=False, range=[aspect, 0], fixedrange=True, scaleanchor="x")
    fig.update_layout(
        dragmode="select",
        height=height_px,
        margin={"l": 0, "r": 0, "t": 0, "b": 0},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        newselection={"line": {"color": VIOLET, "width": 2}},
        activeselection={"fillcolor": VIOLET, "opacity": 0.15},
    )
    event = st.plotly_chart(
        fig,
        key=key,
        on_select="rerun",
        selection_mode="box",
        config={"displayModeBar": False, "scrollZoom": False},
        width="stretch",
    )
    boxes = (event or {}).get("selection", {}).get("box") or []
    if not boxes:
        return None
    x0, x1 = sorted(boxes[-1]["x"])
    y0, y1 = sorted(boxes[-1]["y"])
    return [x0, y0 / aspect, x1, y1 / aspect]
