import os
import logging
import time
from typing import List, Dict, Any

try:
    from fpdf import FPDF
except ImportError:
    FPDF = None

logger = logging.getLogger(__name__)

class ReportService:
    """
    Servicio para la generación de reportes y exportación de evidencia (Feature 3.7).
    """

    def __init__(self, output_dir: str = "app/static/reports"):
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

    def generate_evidence_pdf(
        self, 
        query: str, 
        answer: str, 
        sources: List[Dict[str, Any]], 
        client_id: str
    ) -> str:
        """
        Genera un PDF con la respuesta y las fuentes utilizadas.
        Retorna la ruta al archivo generado.
        """
        if FPDF is None:
            logger.error("FPDF no está instalado. No se puede generar el reporte.")
            raise ImportError("fpdf es requerido para generar reportes PDF.")

        pdf = FPDF()
        pdf.add_page()
        
        # Título
        pdf.set_font("Arial", "B", 16)
        pdf.cell(0, 10, "System Semantic Engine - Reporte de Evidencia", ln=True, align="C")
        pdf.ln(5)

        # Metadata
        pdf.set_font("Arial", "", 10)
        pdf.cell(0, 5, f"Cliente: {client_id}", ln=True)
        pdf.cell(0, 5, f"Fecha: {time.strftime('%Y-%m-%d %H:%M:%S')}", ln=True)
        pdf.ln(10)

        # Consulta
        pdf.set_font("Arial", "B", 12)
        pdf.cell(0, 10, "Consulta del Usuario:", ln=True)
        pdf.set_font("Arial", "", 11)
        pdf.multi_cell(0, 7, query)
        pdf.ln(5)

        # Respuesta
        pdf.set_font("Arial", "B", 12)
        pdf.cell(0, 10, "Respuesta Sintetizada:", ln=True)
        pdf.set_font("Arial", "", 11)
        # Clean answer for PDF (replace characters that fpdf might not like)
        clean_answer = answer.replace('“', '"').replace('”', '"').replace('\u2018', "'").replace('\u2019', "'")
        pdf.multi_cell(0, 7, clean_answer)
        pdf.ln(10)

        # Fuentes
        pdf.set_font("Arial", "B", 12)
        pdf.cell(0, 10, "Fuentes y Evidencia:", ln=True)
        pdf.set_font("Arial", "", 9)

        for i, source in enumerate(sources, 1):
            meta = source.get("metadata", {})
            filename = meta.get("filename") or meta.get("file_name") or "Desconocido"
            score = source.get("score", 0)
            
            pdf.set_font("Arial", "B", 10)
            pdf.cell(0, 7, f"[{i}] {filename} (Relevancia: {score:.4f})", ln=True)
            
            pdf.set_font("Arial", "I", 9)
            snippet = source.get("text_snippet", "Sin fragmento disponible.")
            clean_snippet = snippet.replace('\
', ' ').replace('“', '"').replace('”', '"')[:500]
            pdf.multi_cell(0, 5, clean_snippet)
            pdf.ln(3)

        # Guardar
        timestamp = int(time.time())
        filename = f"report_{client_id}_{timestamp}.pdf"
        file_path = os.path.join(self.output_dir, filename)
        pdf.output(file_path)

        logger.info(f"📄 Reporte generado: {file_path}")
        return file_path

_report_service = None

def get_report_service() -> ReportService:
    global _report_service
    if _report_service is None:
        _report_service = ReportService()
    return _report_service
