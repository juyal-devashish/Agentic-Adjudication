"""A redacted file, read-only: spreadsheets as tables, PDFs one page at a time. Shared by
Documents (the site's review) and Quality check (the Safety Specialist's decision)."""

from __future__ import annotations

import io

import pandas as pd
import pymupdf
import streamlit as st
from openpyxl import load_workbook


def show_sheets(content: bytes) -> None:
    wb = load_workbook(io.BytesIO(content), data_only=True)
    for ws in wb.worksheets:
        hidden = " (hidden in the original)" if ws.sheet_state != "visible" else ""
        st.caption(f"Sheet {ws.title}{hidden}")
        st.dataframe(pd.DataFrame(ws.values), hide_index=True, width="stretch")


def pdf_page(content: bytes, key: str | None = None) -> tuple[int, bytes]:
    """Let the reader pick a page; return its number and a PNG of it."""
    with pymupdf.open(stream=content, filetype="pdf") as doc:
        n = st.number_input("Page", 1, doc.page_count, 1, key=key) if doc.page_count > 1 else 1
        return n, doc[n - 1].get_pixmap(dpi=90).tobytes("png")


def show_file(content: bytes, path: str, name: str, key: str) -> None:
    if path.endswith(".xlsx"):
        show_sheets(content)
    else:
        n, png = pdf_page(content, key=key)
        st.image(png, caption=f"{name}, page {n}, as submitted", width="stretch")
