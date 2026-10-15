import re
import unicodedata
from typing import List, Optional, Dict
from config.settings import settings
from src.core.logger import logger

try:
    from google import genai
    from google.genai import types
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False


def normalize_text(text: str) -> str:
    """Normaliza texto eliminando acentos, puntuación y convirtiendo a minúsculas."""
    text = text.lower()
    text = unicodedata.normalize("NFKD", text).encode("ASCII", "ignore").decode("utf-8")
    return re.sub(r"[^a-z0-9]", " ", text).strip()


class AIService:
    """Servicio inteligente con Gemini Flash para mapeo y clasificación semántica."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.GEMINI_API_KEY
        self.client = None
        if self.api_key and GENAI_AVAILABLE:
            try:
                self.client = genai.Client(api_key=self.api_key)
                logger.info("Cliente Gemini AI inicializado con éxito.")
            except Exception as e:
                logger.warning(f"No se pudo inicializar Gemini Client: {e}. Usando fallback heurístico.")

    def match_course(self, canvas_course_name: str, candidate_folder_names: List[str]) -> Optional[str]:
        """
        Determina a qué carpeta de Google Drive creada por el alumno corresponde la asignatura de Canvas.
        Si la asignatura de Canvas no ha sido creada aún por el alumno, retorna None (se omite).
        """
        if not candidate_folder_names:
            return None

        # 1. Si Gemini está disponible, usarlo para matching semántico de alta precisión (Català/Valencià <-> Castellano)
        if self.client:
            try:
                prompt = (
                    "Eres un asistente de organización académica universitaria.\n"
                    f"El alumno tiene estas carpetas existentes en su Google Drive:\n{candidate_folder_names}\n\n"
                    f"En Canvas LMS ha aparecido la asignatura:\n'{canvas_course_name}'\n\n"
                    "Determina si esta asignatura de Canvas corresponde a alguna de las carpetas existentes del alumno "
                    "(ten en cuenta que en Canvas los nombres están en castellano con códigos de matrícula y en el Drive pueden estar en catalán/valenciano o sin códigos).\n"
                    "Responde ÚNICAMENTE con el nombre EXACTO de la carpeta de Drive a la que corresponde. "
                    "Si no corresponde a NINGUNA de las carpetas de la lista (por ejemplo porque es de otro cuatrimestre o curso posterior), responde 'NONE'."
                )
                response = self.client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=prompt,
                    config=types.GenerateContentConfig(temperature=0.1)
                )
                chosen = response.text.strip().replace("'", "").replace('"', "")
                if chosen in candidate_folder_names:
                    logger.info(f"[IA Gemini] Asignatura Canvas '{canvas_course_name}' -> Carpeta Drive '{chosen}'")
                    return chosen
                if chosen.upper() == "NONE":
                    logger.info(f"[IA Gemini] Asignatura Canvas '{canvas_course_name}' no tiene carpeta en Drive (omitida).")
                    return None
            except Exception as ge:
                logger.warning(f"Fallo en llamada a Gemini (match_course): {ge}. Usando fallback heurístico.")

        # 2. Fallback heurístico si no hay API key o falló la llamada
        norm_canvas = normalize_text(canvas_course_name)

        # Diccionario de equivalencias semánticas frecuentes (Castellano -> Valencià/Català)
        keyword_pairs = [
            ("algebra", ["algebra"]),
            ("analisis matematico", ["analisi matematic", "analisis matematic", "matematic"]),
            ("bases de la informatica", ["bases de l informatica", "bases informatica", "bases"]),
            ("fundamentos de redes", ["fundaments de xarxes", "redes", "xarxes"]),
            ("fundamentos de programacion", ["fundaments de programacio", "programacio"]),
            ("programacion orientada a objetos", ["programacio orientada a objectes", "poo"]),
            ("estructura de computadores", ["estructura de computadors", "computadors"]),
        ]

        for es_term, cat_terms in keyword_pairs:
            if es_term in norm_canvas:
                for candidate in candidate_folder_names:
                    norm_cand = normalize_text(candidate)
                    if any(ct in norm_cand for ct in cat_terms) or es_term in norm_cand:
                        return candidate

        # Comprobación de palabras clave compartidas
        canvas_words = set(w for w in norm_canvas.split() if len(w) > 4)
        best_match = None
        max_overlap = 0

        for candidate in candidate_folder_names:
            cand_words = set(w for w in normalize_text(candidate).split() if len(w) > 4)
            overlap = len(canvas_words.intersection(cand_words))
            if overlap > max_overlap and overlap >= 2:
                max_overlap = overlap
                best_match = candidate

        return best_match

    def classify_file_to_subfolder(
        self,
        course_name: str,
        file_name: str,
        canvas_module_name: Optional[str],
        available_subfolders: List[str]
    ) -> Optional[str]:
        """
        Determina en cuál de las subcarpetas existentes debe ubicarse un archivo.
        Si no hay subcarpeta adecuada, retorna None (se ubica en la raíz del curso).
        """
        if not available_subfolders:
            return None

        # 1. Si Gemini está disponible
        if self.client:
            try:
                prompt = (
                    "Eres un clasificador de archivos de apuntes universitarios.\n"
                    f"Asignatura: {course_name}\n"
                    f"Archivo: '{file_name}'\n"
                    f"Módulo/Tema en Canvas: '{canvas_module_name or 'Sin módulo'}'\n\n"
                    f"Subcarpetas existentes en la carpeta de la asignatura:\n{available_subfolders}\n\n"
                    "Determina en cuál de esas subcarpetas existentes debe guardarse este archivo.\n"
                    "Por ejemplo: si es una pizarra debe ir a la subcarpeta de pizarras si existe; si es de un tema concreto como Tema 1 o T1, a la de ese tema.\n"
                    "Responde ÚNICAMENTE con el nombre EXACTO de la subcarpeta elegida de la lista, o 'NONE' si debe quedar en la carpeta general."
                )
                response = self.client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=prompt,
                    config=types.GenerateContentConfig(temperature=0.1)
                )
                chosen = response.text.strip().replace("'", "").replace('"', "")
                if chosen in available_subfolders:
                    return chosen
                if chosen.upper() == "NONE":
                    return None
            except Exception as ge:
                logger.warning(f"Fallo en llamada a Gemini (classify_file): {ge}")

        # 2. Fallback heurístico inteligente
        combined = f"{file_name} {canvas_module_name or ''}".lower()

        # Detección específica para Álgebra (PIZZARRES vs TEMARI vs EXERCICIS)
        for sub in available_subfolders:
            norm_sub = sub.lower()
            if "pizzarr" in norm_sub and "pizarra" in combined:
                return sub
            if "temari" in norm_sub and any(k in combined for k in ["clase", "presentacion", "teoria"]):
                return sub
            if "exercic" in norm_sub and any(k in combined for k in ["ejercicio", "entrega", "practica"]):
                return sub

        # Detección de tema (T1, T2, T3, Tema 1, Unidad 1, etc.)
        topic_match = re.search(r"\b(t\d+|tema\s*\d+|unidad\s*\d+|w\d+)\b", combined)
        if topic_match:
            raw_token = topic_match.group(1).replace(" ", "")
            # Extraer el número
            num_match = re.search(r"\d+", raw_token)
            if num_match:
                topic_num = num_match.group(0)
                # Buscar carpetas que coincidan con T<num> o Tema <num>
                for sub in available_subfolders:
                    norm_sub = sub.lower()
                    if re.search(rf"\b(t|tema\s*|unidad\s*){topic_num}\b", norm_sub):
                        return sub

        return None

    def summarize_assignment(self, assignment_name: str, description_html: str) -> Optional[str]:
        """Genera un resumen conciso de 1-2 líneas para la descripción de Google Calendar."""
        if not self.client or not description_html or len(description_html.strip()) < 30:
            return None

        try:
            # Limpiar etiquetas HTML básicas
            clean_desc = re.sub(r"<[^>]+>", " ", description_html)
            clean_desc = " ".join(clean_desc.split())[:1200]

            prompt = (
                f"Resume en 2 líneas muy concisas el objetivo de esta entrega universitaria y el formato requerido:\n"
                f"Tarea: {assignment_name}\n"
                f"Detalles: {clean_desc}\n\n"
                "Resumen en español:"
            )
            response = self.client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(max_output_tokens=100, temperature=0.2)
            )
            return response.text.strip()
        except Exception as ge:
            logger.debug(f"No se pudo generar resumen con Gemini: {ge}")
            return None
