# extractors/metadata_extractor.py
"""
Handles metadata extraction for supported file types (DOCX, PDF, etc.)
Keeps logic separate from discovery.py to maintain modularity.
"""

from typing import Dict

class MetadataExtractor:
    """Extracts metadata like author, created, modified."""

    @staticmethod
    def extract(file_path: str, ext: str) -> Dict:
        metadata = {}
        try:
            if ext == ".docx":
                from docx import Document
                doc = Document(file_path)
                core = doc.core_properties
                metadata = {
                    "author": core.author or "Unknown",
                    "created": str(core.created) if core.created else "N/A",
                    "modified": str(core.modified) if core.modified else "N/A"
                }

            elif ext == ".pdf":
                from PyPDF2 import PdfReader
                reader = PdfReader(file_path)
                if reader.metadata:
                    metadata = {
                        "author": reader.metadata.get("/Author", "Unknown"),
                        "created": reader.metadata.get("/CreationDate", "N/A"),
                        "modified": reader.metadata.get("/ModDate", "N/A")
                    }

        except Exception as e:
            metadata = {"error": str(e)}

        return metadata
