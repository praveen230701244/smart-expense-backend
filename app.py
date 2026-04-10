import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from flask import Flask, current_app, jsonify
from flask_cors import CORS

from routes.analysis import analysis_bp
from routes.chatbot import chatbot_bp
from routes.copilot import copilot_bp
from routes.upload import upload_bp
from services.auth_firebase import register_auth_context
from services.embedding_categorizer import MiniLMCategorizer
from services.gemini_service import GeminiService
from services.ml_model import AutoCategorizer
from services.storage import AzureBlobStorageAdapter, ExpenseRepository, LocalStorageAdapter


def _build_categorizer():
    # Default: MiniLM (all-MiniLM-L6-v2). Set USE_MINILM=false for lightweight Render tiers.
    use_minilm = os.getenv("USE_MINILM", "true").lower().strip() not in ("0", "false", "no")
    if use_minilm:
        try:
            return MiniLMCategorizer.default()
        except Exception:
            pass
    return AutoCategorizer.default()


def create_app() -> Flask:
    app = Flask(__name__)

    base_dir = Path(__file__).resolve().parent
    data_dir = Path(os.getenv("DATA_DIR", base_dir / "data"))
    db_path = data_dir / "expenses.db"
    upload_dir = data_dir / "uploads"

    app.config["PDF_PARSER"] = os.getenv("PDF_PARSER", "pymupdf").lower().strip()
    app.config["STORE_UPLOADS"] = os.getenv("STORE_UPLOADS", "true").lower().strip() == "true"
    app.config["ANOMALY_CONTAMINATION"] = float(os.getenv("ANOMALY_CONTAMINATION", "0.08"))
    app.config["MAX_UPLOAD_SIZE_BYTES"] = int(os.getenv("MAX_UPLOAD_SIZE_MB", "10")) * 1024 * 1024
    app.config["AZURE_DOC_INTELLIGENCE_ENDPOINT"] = os.getenv("AZURE_DOC_INTELLIGENCE_ENDPOINT", "").strip()
    app.config["AZURE_DOC_INTELLIGENCE_KEY"] = os.getenv("AZURE_DOC_INTELLIGENCE_KEY", "").strip()
    app.config["TESSERACT_CMD"] = os.getenv("TESSERACT_CMD", "/usr/bin/tesseract").strip()

    cors_origin = os.getenv("CORS_ORIGIN", "*")
    CORS(app, resources={r"/*": {"origins": cors_origin}})

    register_auth_context(app)

    repo = ExpenseRepository(db_path=db_path)
    categorizer = _build_categorizer()

    az_storage_conn = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "").strip()
    az_storage_container = os.getenv("AZURE_STORAGE_CONTAINER_NAME", "").strip()
    file_storage = None
    if az_storage_conn and az_storage_container:
        try:
            file_storage = AzureBlobStorageAdapter(
                connection_string=az_storage_conn,
                container_name=az_storage_container,
            )
        except Exception:
            file_storage = None
    if file_storage is None:
        file_storage = LocalStorageAdapter(base_dir=upload_dir)

    app.extensions["repo"] = repo
    app.extensions["categorizer"] = categorizer
    app.extensions["file_storage"] = file_storage
    app.extensions["openai_service"] = GeminiService()
    # Bounded caches for hot paths (cleared implicitly on process restart / deploy).
    app.extensions["forecast_cache"] = {}
    app.extensions["forecast_cache_order"] = []
    app.extensions["anomaly_cache"] = {}
    app.extensions["anomaly_cache_order"] = []

    app.register_blueprint(upload_bp, url_prefix="/upload")
    app.register_blueprint(analysis_bp)
    app.register_blueprint(chatbot_bp)
    app.register_blueprint(copilot_bp)

    @app.route("/health", methods=["GET"])
    def health():
        repo = current_app.extensions["repo"]
        return jsonify({"status": "ok", "expenseRows": repo.count_all()})

    @app.route("/reset", methods=["DELETE"])
    def reset():
        from flask import g

        repo = current_app.extensions["repo"]
        uid = getattr(g, "user_id", None)
        if not uid:
            return jsonify({"error": "Unauthorized"}), 401
        repo.clear_user_data(uid)
        categorizer = current_app.extensions.get("categorizer")
        try:
            if categorizer is not None:
                if hasattr(categorizer, "reset_model"):
                    categorizer.reset_model()
                else:
                    categorizer._vectorizer = None
                    categorizer._classifier = None
        except Exception:
            pass
        for key in ("forecast_cache", "anomaly_cache"):
            c = current_app.extensions.get(key)
            if isinstance(c, dict):
                c.clear()
        for key in ("forecast_cache_order", "anomaly_cache_order"):
            o = current_app.extensions.get(key)
            if isinstance(o, list):
                o.clear()
        return jsonify({"status": "cleared"})

    @app.errorhandler(404)
    def not_found(_e):
        return jsonify({"error": "Not found"}), 404

    return app


app = create_app()

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
