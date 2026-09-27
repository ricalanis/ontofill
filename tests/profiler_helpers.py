"""Synthetic document fixtures for the R54 profiler tests (no third-party authoring deps)."""

from __future__ import annotations

import io
import zipfile

from openpyxl import Workbook


def _escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def make_pdf(pages: list[list[str]], *, font_size: int = 10) -> bytes:
    """Build a minimal uncompressed multi-page text PDF with monospaced (Courier) lines."""
    objects: list[bytes] = []
    page_ids = [3 + index * 2 for index in range(len(pages))]
    content_ids = [4 + index * 2 for index in range(len(pages))]
    font_id = 3 + len(pages) * 2

    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    for page_index, lines in enumerate(pages):
        content_id = content_ids[page_index]
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Contents {content_id} 0 R /Resources << /Font << /F1 {font_id} 0 R >> >> >>"
            ).encode()
        )
        body = ["BT", f"/F1 {font_size} Tf", "40 750 Td"]
        for line_index, line in enumerate(lines):
            if line_index:
                body.append("0 -14 Td")
            body.append(f"({_escape(line)}) Tj")
        body.append("ET")
        stream = "\n".join(body).encode()
        objects.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>")

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode()
    )
    return bytes(out)


def make_xlsx(sheets: list[tuple[str, list[list[object]]]]) -> bytes:
    workbook = Workbook()
    default = workbook.active
    workbook.remove(default)
    for name, rows in sheets:
        sheet = workbook.create_sheet(title=name[:31])
        for row in rows:
            sheet.append(list(row))
    buffer = io.BytesIO()
    workbook.save(buffer)
    workbook.close()
    return buffer.getvalue()


def make_zip(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    return buffer.getvalue()
